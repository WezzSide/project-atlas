"""The supervisor loop: one approved program, executed to program completion.

The product property this exists for: the owner approves a work program once,
and the supervisor keeps going -- across task boundaries, across worker
sessions ending, across its own restarts -- asking for input only when a
decision is genuinely the owner's.

  TASK_COMPLETE != PROGRAM_COMPLETE. A worker finishing its session is a task
  event. The program continues.

What this module does NOT do, ever: merge, grant an owner gate, widen a
program, expand a profile, or treat anything a worker wrote as authority. A
worker's plan, its result message, and any repository text it produced are
data. The approved program file and the profiles resolved from it are the only
things that decide what may run.

Scheduling reuses the existing control plane rather than restating it:
``autonomy.continuation.select_next`` chooses eligible work, ``autonomy.dag``
polices every transition, ``autonomy.leases`` grants ownership,
``autonomy.lease_projection`` makes that ownership survive a crash,
``autonomy.overlap`` refuses unsafe parallel mutation, and
``autonomy.owner_gates`` fails closed on anything an owner must decide.

One worker at a time. Concurrent scheduling is deliberately out of this slice;
the overlap gate is still consulted so adding it later cannot silently skip it.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from project_atlas.orchestration.autonomy.continuation import select_next
from project_atlas.orchestration.autonomy.dag import IllegalTransitionError, assert_transition
from project_atlas.orchestration.autonomy.evidence import hash_payload
from project_atlas.orchestration.autonomy.lease_projection import (
    ProjectionError,
    active_rows,
    load_projection,
    project_grant,
    project_release,
)
from project_atlas.orchestration.autonomy.leases import grant_lease, release_lease
from project_atlas.orchestration.autonomy.models import (
    AgentCapability,
    AgentLease,
    NodeState,
    StopReason,
    WorkNode,
)
from project_atlas.orchestration.autonomy.owner_gates import evaluate_owner_action
from project_atlas.orchestration.program.acceptance import (
    AcceptanceResult,
    evaluate_task,
    progress_fingerprint,
)
from project_atlas.orchestration.program.adapters.base import (
    AdapterOutcome,
    AdapterRequest,
    AdapterUnavailableError,
    RuntimeAdapter,
)
from project_atlas.orchestration.program.adapters.claude_code import (
    ClaudeCodeAdapter,
    new_session_id,
)
from project_atlas.orchestration.program.adapters.codex import CodexAdapter
from project_atlas.orchestration.program.adapters.local_command import LocalCommandAdapter
from project_atlas.orchestration.program.enrollment import (
    AgentRegistry,
    AgentStatus,
    EnrolledAgent,
    bind,
    load_registry,
)
from project_atlas.orchestration.program.loader import LoadedProgram, profile_digest
from project_atlas.orchestration.program.models import (
    PERMANENT_FAILURES,
    AttemptPhase,
    ExecutionConfidence,
    FailureClass,
    ProgramError,
    ProgramStopReason,
    ProgramTask,
)
from project_atlas.orchestration.program.profiles import (
    AdapterKind,
    AgentProfile,
    ProfileLimits,
)
from project_atlas.orchestration.program.recovery import RecoveryAction, classify_attempt
from project_atlas.orchestration.program.store import (
    AttemptRecord,
    HandoffRecord,
    ProgramStateRecord,
    TaskRecord,
    append_event,
    evidence_dir,
    load_state,
    persist_state,
    state_dir,
    write_evidence,
)
from project_atlas.orchestration.program.waiting import (
    WaitOutcome,
    cancel_observers,
    ensure_observer,
    poll_precondition,
)
from project_atlas.orchestration.sdk.host import (
    acquire_supervisor_lock,
    clear_supervisor_stop,
    new_supervisor_instance_id,
    release_supervisor_lock,
    request_supervisor_stop,
    stop_requested,
)

#: States in which a task is being worked on right now and its lease is held.
IN_FLIGHT_STATES: frozenset[NodeState] = frozenset(
    {NodeState.LEASED, NodeState.ACTIVE, NodeState.REMEDIATING, NodeState.VERIFYING}
)
#: States that count as this program's own definition of "done with it".
DONE_STATES: frozenset[NodeState] = frozenset({NodeState.CERTIFIED, NodeState.CLOSED})

#: Stop reasons that describe "nothing to start right now", which is a
#: different statement from "the program is finished". While a worker is still
#: running, any of these is provisional and is re-derived on the next cycle;
#: reporting one as the program's verdict would end a program that is simply
#: busy.
_NOT_TERMINAL_WHILE_RUNNING: frozenset[ProgramStopReason] = frozenset(
    {
        ProgramStopReason.NO_ELIGIBLE_WORK,
        ProgramStopReason.OWNER_DECISION_REQUIRED,
        ProgramStopReason.AWAITING_INDEPENDENT_VERIFICATION,
        ProgramStopReason.HARD_BLOCKER,
    }
)


class SupervisorError(ProgramError):
    code = "SUPERVISOR_ERROR"


class DispatchMode(StrEnum):
    """Why the supervisor is about to launch a worker."""

    NEW = "NEW"
    RETRY = "RETRY"
    RESUME = "RESUME"
    VERIFY = "VERIFY"


@dataclass(frozen=True)
class DispatchChoice:
    task_id: str
    mode: DispatchMode
    reason: str
    resume_session_id: str | None = None


@dataclass(frozen=True)
class RunningWork:
    """One launched worker the supervisor is waiting on.

    Deliberately holds no mutable program state. A worker thread only ever
    sees ``adapter`` and ``request``; everything it could change lives on the
    supervisor's thread, which is why concurrency here needs no lock.
    """

    attempt_id: str
    task_id: str
    mode: DispatchMode
    verifying: bool
    profile: AgentProfile
    adapter: RuntimeAdapter
    request: AdapterRequest
    started_at: float


@dataclass
class CycleResult:
    """One pass of the loop. Never authority."""

    cycle: int
    dispatched_task_id: str | None = None
    dispatch_mode: DispatchMode | None = None
    #: Every task this cycle launched. ``dispatched_task_id`` is the first of
    #: them, kept because sequential programs read more clearly with it.
    dispatched: list[tuple[str, DispatchMode]] = field(default_factory=list)
    settled: list[str] = field(default_factory=list)
    stop_reason: ProgramStopReason | None = None
    progressed: bool = False
    notes: list[str] = field(default_factory=list)
    notifications: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SupervisorReport:
    program_id: str
    cycles: list[CycleResult]
    stop_reason: ProgramStopReason
    complete: bool
    #: Cumulative launches for the whole program, across every invocation.
    #: This is the number the program's launch limit is enforced against.
    launches: int
    #: Launches this invocation started. Distinct from ``launches`` on
    #: purpose: a resumed program that dispatches nothing new still carries a
    #: non-zero cumulative total, and conflating the two makes "did this run
    #: start anything" unanswerable from the report.
    launches_this_run: int
    estimated_cost_usd: float
    notifications: list[dict[str, Any]]
    truth_boundary: str
    #: The most workers actually in flight at once. Reported because a program
    #: that permits four and never ran more than one has not demonstrated
    #: concurrency, and saying "max_concurrent_workers: 4" would imply it did.
    max_concurrent_observed: int = 1

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "program_id": self.program_id,
            "stop_reason": self.stop_reason.value,
            "program_complete": self.complete,
            "cycles_run": len(self.cycles),
            "tasks_dispatched": [
                {"cycle": cycle.cycle, "task_id": task_id, "mode": mode.value}
                for cycle in self.cycles
                for task_id, mode in cycle.dispatched
            ],
            "max_concurrent_workers_observed": self.max_concurrent_observed,
            "total_launches": self.launches,
            "launches_this_run": self.launches_this_run,
            "estimated_cost_usd": self.estimated_cost_usd,
            "estimated_cost_note": (
                "client-side estimate reported by the runtime; not billed spend"
            ),
            "notifications": self.notifications,
            "merge_authorized": False,
            "execution_authorized": False,
            "truth_boundary": self.truth_boundary,
        }


def build_default_adapters(loaded: LoadedProgram) -> dict[str, RuntimeAdapter]:
    """One adapter instance per profile, built from what the profile declares."""
    adapters: dict[str, RuntimeAdapter] = {}
    for profile in loaded.profiles.profiles.values():
        adapters[profile.profile_id] = _build_adapter(profile)
    return adapters


def _build_adapter(profile: AgentProfile) -> RuntimeAdapter:
    if profile.adapter is AdapterKind.CLAUDE_CODE:
        return ClaudeCodeAdapter()
    if profile.adapter is AdapterKind.CODEX:
        return CodexAdapter()
    if profile.adapter is AdapterKind.LOCAL_COMMAND:
        return LocalCommandAdapter(profile.command_argv())
    raise SupervisorError(  # pragma: no cover - the enum is closed
        f"no adapter implementation for {profile.adapter}", code="UNKNOWN_ADAPTER"
    )


def idempotency_key(
    *,
    program_id: str,
    task_id: str,
    attempt_number: int,
    adapter_id: str,
    profile_sha: str,
    base_pin: str,
) -> str:
    """A key that distinguishes work that is genuinely different.

    The adapter id and the effective-profile digest are both in the key on
    purpose. A key over (repository, generation, node, role, attempt) alone
    collides across two different commands at the same commit, so the second
    one silently returns the first one's result -- an observed defect class in
    this codebase, not a hypothetical. Two runs are the same run only when
    every input that decides what actually executes is the same.
    """
    return hash_payload(
        {
            "program_id": program_id,
            "task_id": task_id,
            "attempt": attempt_number,
            "adapter": adapter_id,
            "profile_sha256": profile_sha,
            "base_pin": base_pin,
        }
    )


class ProgramSupervisor:
    """Executes one approved program. Single instance per program state root."""

    def __init__(
        self,
        loaded: LoadedProgram,
        *,
        state_root: Path | None = None,
        adapters: Mapping[str, RuntimeAdapter] | None = None,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
        enrolled_agents: Sequence[EnrolledAgent] = (),
        registry_root: Path | None = None,
    ) -> None:
        if enrolled_agents:
            loaded = _apply_enrollments(loaded, enrolled_agents)
        self.enrolled_agents = tuple(enrolled_agents)
        #: Where the enrolment roster lives, so authority can be re-read
        #: immediately before a dispatch rather than trusted from start-up.
        #: An agent suspended, retired or un-granted while a long program runs
        #: must not get one more task because the supervisor cached its
        #: permissions at launch.
        self.registry_root = registry_root
        self.loaded = loaded
        self.program = loaded.program
        self.workspace = loaded.workspace
        # Program state lives beside the workspace, not inside it, so a
        # worker's own diff can never contain the supervisor's checkpoints and
        # a task cannot accidentally (or deliberately) rewrite its own record.
        self.root = (state_root or loaded.source_path.parent).resolve()
        self.adapters: dict[str, RuntimeAdapter] = dict(
            adapters if adapters is not None else build_default_adapters(loaded)
        )
        self._clock = clock
        self._sleep = sleeper
        self._instance_id: str | None = None
        self._sequence = 0
        self._leases: dict[str, AgentLease] = {}
        self._notifications: list[dict[str, Any]] = []
        self._launches_this_run = 0
        self._running: dict[str, tuple[RunningWork, Future[AdapterOutcome]]] = {}
        self._executor: ThreadPoolExecutor | None = None
        self._max_concurrent_observed = 0

    # ------------------------------------------------------------- ownership

    @property
    def lock_root(self) -> Path:
        """Root the singleton lock is taken under.

        The program's own state directory, not the repository root: the SDK
        lane's durable supervisor takes its lock at the repository root, and
        two unrelated supervisors excluding each other would be a liveness
        bug, not a safety property. Two supervisors for the *same program*
        still exclude each other, which is the property that matters.
        """
        return state_dir(self.root)

    def _acquire(self) -> str:
        token = new_supervisor_instance_id()
        if not acquire_supervisor_lock(self.lock_root, instance_id=token):
            raise SupervisorError(
                "another live supervisor already owns this program",
                code="SUPERVISOR_DOUBLE_START",
            )
        # Read-back re-verification. `acquire_supervisor_lock` has a known
        # reclaim race (open PR #780, unmerged at time of writing): two
        # contenders can each judge a stale lock reclaimable and both end up
        # believing they own it. Immediately re-asserting ownership turns that
        # into a detectable loss for whichever contender was overwritten,
        # instead of two supervisors dispatching in parallel.
        if not acquire_supervisor_lock(self.lock_root, instance_id=token):
            release_supervisor_lock(self.lock_root, instance_id=token)
            raise SupervisorError(
                "lost the supervisor lock immediately after acquiring it",
                code="SUPERVISOR_LOCK_CONTENDED",
            )
        self._instance_id = token
        return token

    def _assert_still_supervisor(self) -> None:
        """Re-assert ownership immediately before anything consequential.

        An eligibility snapshot taken at the top of a cycle is not a licence
        to dispatch at the bottom of it. Ownership can have changed in
        between, and a dispatch by a supervisor that no longer owns the
        program is exactly the duplicate-dispatch case this check exists to
        prevent.
        """
        if self._instance_id is None:
            raise SupervisorError(
                "supervisor lock was never acquired", code="SUPERVISOR_NOT_OWNED"
            )
        if not acquire_supervisor_lock(self.lock_root, instance_id=self._instance_id):
            raise SupervisorError(
                "this supervisor no longer owns the program lock",
                code="SUPERVISOR_LOCK_LOST",
            )

    def _release(self) -> None:
        if self._instance_id is not None:
            release_supervisor_lock(self.lock_root, instance_id=self._instance_id)
            self._instance_id = None

    # ----------------------------------------------------------------- state

    def _fresh_state(self) -> ProgramStateRecord:
        return ProgramStateRecord(
            program_id=self.program.program_id,
            program_digest=self.loaded.digest,
            base_pin=self.program.base_pin,
            tasks={
                task.task_id: TaskRecord(task_id=task.task_id)
                for task in self.program.tasks
            },
        )

    def load_or_init_state(self) -> ProgramStateRecord:
        state = load_state(self.root)
        if state is None:
            state = self._fresh_state()
            persist_state(self.root, state)
            append_event(
                self.root,
                "PROGRAM_INITIALISED",
                {
                    "program_id": state.program_id,
                    "program_digest": state.program_digest,
                    "tasks": sorted(state.tasks),
                },
            )
            return state
        if state.program_id != self.program.program_id:
            raise SupervisorError(
                f"state root holds program {state.program_id}, not "
                f"{self.program.program_id}",
                code="PROGRAM_MISMATCH",
            )
        if state.program_digest != self.loaded.digest:
            # The approved program file changed underneath a program that has
            # already run. Continuing would execute under an approval that no
            # longer describes the work. That is an owner decision, not
            # something to reconcile automatically.
            raise SupervisorError(
                "the program file has changed since this program started "
                f"(approved digest {state.program_digest[:12]}, current "
                f"{self.loaded.digest[:12]}); start a new program or restore "
                "the approved file",
                code="PROGRAM_DIGEST_DRIFT",
            )
        # A task added to state that the program no longer has, or vice versa,
        # is impossible while the digest matches -- but the record is rebuilt
        # for any task the state has never seen so an older state file remains
        # loadable.
        for task in self.program.tasks:
            state.tasks.setdefault(task.task_id, TaskRecord(task_id=task.task_id))
        return state

    def _nodes(self, state: ProgramStateRecord) -> tuple[WorkNode, ...]:
        """Project current state onto the governance node type each cycle.

        Rebuilt from the persisted record every cycle rather than carried in
        memory: the persisted record is the authoritative picture, and a node
        list that drifted from it would make every downstream decision --
        eligibility, leasing, overlap -- correct about the wrong world.
        """
        nodes: list[WorkNode] = []
        for task in self.program.tasks:
            record = state.tasks[task.task_id]
            node = task.to_work_node(base_pin=self.program.base_pin)
            nodes.append(node.model_copy(update={"state": record.state}))
        return tuple(nodes)

    def _transition(
        self,
        state: ProgramStateRecord,
        task_id: str,
        to_state: NodeState,
        *,
        reason: str,
    ) -> None:
        record = state.tasks[task_id]
        try:
            assert_transition(record.state, to_state)
        except IllegalTransitionError as exc:
            raise SupervisorError(str(exc), code="ILLEGAL_DAG_TRANSITION") from exc
        append_event(
            self.root,
            "TASK_TRANSITION",
            {
                "task_id": task_id,
                "from": record.state.value,
                "to": to_state.value,
                "reason": reason[:256],
            },
        )
        record.state = to_state
        record.reason = reason[:512]

    # ------------------------------------------------------------ public API

    def start(self) -> SupervisorReport:
        """Run cycles until a stop reason. Continues across task completion."""
        self._acquire()
        clear_supervisor_stop(self.lock_root)
        cycles: list[CycleResult] = []
        self._notifications = []
        self._launches_this_run = 0
        self._running = {}
        started_at = self._clock()
        try:
            state = self.load_or_init_state()
            state.supervisor_instance_id = self._instance_id
            state.supervisor_pid = _self_pid()
            persist_state(self.root, state)

            stop = self._reconcile_on_start(state)
            if stop is not None:
                persist_state(self.root, state)
                return self._report(state, cycles, stop)

            limit_cycles = self.program.limits.max_cycles
            while True:
                if len(cycles) >= limit_cycles:
                    stop = ProgramStopReason.CYCLE_BUDGET_REACHED
                    break
                if self._clock() - started_at >= self.program.limits.max_program_seconds:
                    stop = ProgramStopReason.LIMIT_REACHED
                    self._notify(
                        "PROGRAM_TIME_LIMIT",
                        "the program's wall-clock limit was reached",
                        {"max_program_seconds": self.program.limits.max_program_seconds},
                    )
                    break

                result = self._cycle(state, len(cycles) + 1)
                cycles.append(result)
                state.total_cycles += 1
                state.idle_cycles = 0 if result.progressed else state.idle_cycles + 1
                persist_state(self.root, state)

                if result.stop_reason is not None:
                    stop = result.stop_reason
                    break
                if state.idle_cycles >= self.program.limits.max_idle_cycles:
                    stop = ProgramStopReason.NO_ELIGIBLE_WORK
                    self._notify(
                        "NO_PROGRESS",
                        (
                            f"{state.idle_cycles} consecutive cycles changed no task "
                            "state, produced no new evidence and resolved no external "
                            "event"
                        ),
                        {"idle_cycles": state.idle_cycles},
                    )
                    break
                if not result.dispatched_task_id and not self._running:
                    # Nothing dispatched, nothing running, but not stopped: an
                    # external wait is outstanding. Sleep briefly rather than
                    # spinning. With workers in flight the cycle already
                    # blocked on one of them finishing, so sleeping again here
                    # would add latency for no reason.
                    self._sleep(self.program.limits.idle_sleep_seconds)

            # Never exit with a worker unaccounted for. An outcome that was
            # produced but never applied would leave an attempt stuck at
            # ADAPTER_INVOKED, which the next start correctly refuses to
            # redispatch -- turning a clean finish into a reconciliation.
            if self._running:
                final = CycleResult(cycle=len(cycles) + 1)
                self._drain(state, final)
                if final.settled:
                    cycles.append(final)
                if state.complete is False and all(
                    record.state in DONE_STATES for record in state.tasks.values()
                ):
                    state.complete = True
                    stop = ProgramStopReason.PROGRAM_COMPLETE
                    self._notify(
                        "PROGRAM_COMPLETE",
                        "every task in the approved program reached an accepted state",
                        {"tasks": sorted(state.tasks)},
                    )

            state.last_stop_reason = stop
            persist_state(self.root, state)
            return self._report(state, cycles, stop)
        finally:
            if self._executor is not None:
                self._executor.shutdown(wait=True)
                self._executor = None
            self._release()

    def status(self) -> dict[str, Any]:
        """Compact, read-only inspection. Acquires no lock and dispatches nothing."""
        state = load_state(self.root)
        if state is None:
            return {
                "program_id": self.program.program_id,
                "started": False,
                "detail": "this program has never been started",
                "merge_authorized": False,
            }
        ready: list[str] = []
        blocked: list[str] = []
        running: list[str] = []
        owner_required: list[dict[str, str]] = []
        waiting: list[dict[str, str]] = []
        awaiting_iv: list[str] = []
        needs_reconcile: list[dict[str, str]] = []
        done: list[str] = []

        for task in self.program.tasks:
            record = state.tasks.get(task.task_id)
            if record is None:
                continue
            if record.state in DONE_STATES:
                done.append(task.task_id)
            elif record.awaiting_independent_verification:
                awaiting_iv.append(task.task_id)
            elif record.state in IN_FLIGHT_STATES:
                running.append(task.task_id)
            elif record.state is NodeState.BLOCKED:
                blocked.append(task.task_id)
            elif record.pending_observer_id:
                waiting.append(
                    {"task_id": task.task_id, "observer_id": record.pending_observer_id}
                )
            elif record.state is NodeState.READY:
                ready.append(task.task_id)
            if task.owner_gate is not None and record.state not in DONE_STATES:
                owner_required.append(
                    {"task_id": task.task_id, "gate": task.owner_gate.value}
                )

        for attempt in state.attempts.values():
            if attempt.confidence is ExecutionConfidence.UNCERTAIN and (
                attempt.phase is not AttemptPhase.TERMINAL
            ):
                needs_reconcile.append(
                    {"task_id": attempt.task_id, "attempt_id": attempt.attempt_id}
                )

        last_progress = max(
            (
                attempt.ended_at or attempt.started_at
                for attempt in state.attempts.values()
            ),
            default=None,
        )
        limits = self.program.limits
        return {
            "program_id": state.program_id,
            "started": True,
            "program_complete": state.complete,
            "current_task": running[0] if running else None,
            "last_verified_progress_at": last_progress,
            "last_stop_reason": (
                state.last_stop_reason.value if state.last_stop_reason else None
            ),
            "ready": sorted(ready),
            "running": sorted(running),
            "blocked": sorted(blocked),
            "done": sorted(done),
            "waiting_on_external_event": waiting,
            "awaiting_independent_verification": sorted(awaiting_iv),
            "owner_decision_required": owner_required,
            "needs_reconciliation": needs_reconcile,
            "remaining_limits": {
                "launches": max(0, limits.max_task_launches - state.total_launches),
                "max_task_launches": limits.max_task_launches,
                "max_program_seconds": limits.max_program_seconds,
                "max_cycles": limits.max_cycles,
                "idle_cycles_used": state.idle_cycles,
                "max_idle_cycles": limits.max_idle_cycles,
            },
            "estimated_cost_usd": state.estimated_cost_usd,
            "estimated_cost_note": (
                "client-side estimate reported by the runtime; not billed spend"
            ),
            "cancel_requested": state.cancel_requested
            or stop_requested(self.lock_root),
            "supervisor_pid": state.supervisor_pid,
            "supervisor_alive": _supervisor_alive(state),
            "merge_authorized": False,
            "execution_authorized": False,
        }

    def request_cancel(self) -> dict[str, Any]:
        """Ask a running supervisor to stop, and mark the durable record.

        Two mechanisms because there are two cases: a live supervisor notices
        the stop file between polls of the child it is running, and a
        supervisor that is not running yet reads the state flag when it
        starts. Neither kills anything by force from here.
        """
        request_supervisor_stop(self.lock_root)
        state = load_state(self.root)
        if state is not None:
            state.cancel_requested = True
            persist_state(self.root, state)
        append_event(self.root, "CANCEL_REQUESTED", {"program_id": self.program.program_id})
        return {
            "program_id": self.program.program_id,
            "cancel_requested": True,
            "note": (
                "a running supervisor stops before its next launch and "
                "terminates any worker it is currently running; a worker's "
                "external effects up to that point are recorded as UNCERTAIN"
            ),
        }

    def enroll_session(
        self,
        *,
        task_id: str,
        session_id: str,
        enrolled_by: str,
        note: str = "",
    ) -> dict[str, Any]:
        """Hand an existing stored session to this program, explicitly.

        This is the controlled alternative to attaching to a live session,
        which neither supported runtime offers and which this package would
        refuse anyway: a supervisor that adopts processes it did not start
        cannot say what those processes were authorized to do.

        What actually happens is narrow and stated: the next dispatch for
        ``task_id`` continues ``session_id`` in a NEW supervised run, under
        this program's profile, limits, acceptance and ownership. The prior
        session's own permissions do not carry over -- the profile decides,
        as it does for any other dispatch.
        """
        state = load_state(self.root)
        if state is None:
            state = self.load_or_init_state()
        task = self.program.task(task_id)
        profile = self.loaded.effective_profile(task_id)
        adapter = self._adapter_for(profile)
        capabilities = adapter.capabilities

        if not capabilities.supports_resume:
            raise SupervisorError(
                f"the {capabilities.adapter_id} adapter cannot continue a "
                "stored session, so there is nothing to hand off to",
                code="RESUME_UNSUPPORTED",
            )
        existing = state.handoffs.get(task_id)
        if existing is not None and existing.consumed_by_attempt_id is None:
            raise SupervisorError(
                f"task {task_id} already has a pending handoff to session "
                f"{existing.session_id}",
                code="HANDOFF_ALREADY_PENDING",
            )
        record = state.tasks[task_id]
        if record.state in DONE_STATES:
            raise SupervisorError(
                f"task {task_id} is already {record.state.value}",
                code="TASK_ALREADY_DONE",
            )

        # Corroborate the session id where the adapter can, and record what it
        # said either way. An operator naming an id the runtime has never seen
        # should be able to see that nothing backed it up, rather than finding
        # out when the resume fails.
        probe_attempt = AttemptRecord(
            attempt_id=f"{self.program.program_id}.{task_id}.handoff-probe",
            task_id=task_id,
            attempt_number=1,
            idempotency_key="handoff-probe",
            profile_id=profile.profile_id,
            agent_id=profile.agent_id,
            adapter=capabilities.adapter_id,
            profile_digest=profile_digest(profile),
            base_pin=self.program.base_pin,
            runtime_session_id=session_id,
        )
        observed = adapter.probe_run_started(
            self._build_request(
                task=task,
                profile=profile,
                attempt=probe_attempt,
                resume_session_id=None,
                cancel_check=None,
            )
        )

        handoff = HandoffRecord(
            task_id=task_id,
            adapter=capabilities.adapter_id,
            session_id=session_id,
            enrolled_by=enrolled_by,
            note=note,
            session_observed=observed,
        )
        state.handoffs[task_id] = handoff
        persist_state(self.root, state)
        append_event(
            self.root,
            "SESSION_ENROLLED",
            {
                "task_id": task_id,
                "adapter": capabilities.adapter_id,
                "session_id": session_id,
                "enrolled_by": enrolled_by,
                "session_observed": observed,
            },
        )
        return {
            "program_id": self.program.program_id,
            "task_id": task_id,
            "adapter": capabilities.adapter_id,
            "session_id": session_id,
            "session_observed": observed,
            "session_observed_note": (
                "the adapter found evidence of this session"
                if observed
                else "the adapter could not corroborate this session id; the "
                "enrolment is recorded on your assertion alone"
            ),
            "effect": (
                "the next dispatch for this task continues that session in a "
                "NEW supervised run under this program's profile, limits, "
                "acceptance and ownership; no live process is adopted and the "
                "prior session's permissions do not carry over"
            ),
            "merge_authorized": False,
        }

    def reconcile(self, *, resolve_uncertain: str | None = None) -> dict[str, Any]:
        """Inspect interrupted attempts, and optionally settle one explicitly.

        With no argument this reports what an interrupted attempt looks like
        and what the recovery contract permits for it. With
        ``resolve_uncertain=<attempt_id>`` the operator asserts that they have
        looked and that the attempt's effect did not land; the attempt is
        sealed as terminal and its task returns to being schedulable. This is
        the operator's judgement being recorded, not the supervisor deciding.
        """
        state = load_state(self.root)
        if state is None:
            raise SupervisorError("this program has never been started", code="NO_STATE")

        findings: list[dict[str, Any]] = []
        for attempt in sorted(state.attempts.values(), key=lambda item: item.started_at):
            if attempt.phase is AttemptPhase.TERMINAL:
                continue
            task = self.program.task(attempt.task_id)
            profile = self.loaded.effective_profile(task.task_id)
            adapter = self._adapter_for(profile)
            request = self._build_request(
                task=task,
                profile=profile,
                attempt=attempt,
                resume_session_id=None,
                cancel_check=None,
            )
            verdict = classify_attempt(
                attempt,
                task=task,
                adapter=adapter,
                capabilities=adapter.capabilities,
                request=request,
            )
            findings.append(
                {
                    "attempt_id": attempt.attempt_id,
                    "task_id": attempt.task_id,
                    "phase": attempt.phase.value,
                    "confidence": (
                        attempt.confidence.value if attempt.confidence else None
                    ),
                    "recovery_action": verdict.action.value,
                    "reason": verdict.reason,
                    "runtime_session_id": attempt.runtime_session_id,
                }
            )

        resolved: dict[str, Any] | None = None
        if resolve_uncertain is not None:
            settling = state.attempts.get(resolve_uncertain)
            if settling is None:
                raise SupervisorError(
                    f"unknown attempt {resolve_uncertain}", code="UNKNOWN_ATTEMPT"
                )
            if settling.phase is AttemptPhase.TERMINAL:
                raise SupervisorError(
                    f"attempt {resolve_uncertain} is already terminal",
                    code="ATTEMPT_ALREADY_TERMINAL",
                )
            settling.phase = AttemptPhase.TERMINAL
            settling.confidence = ExecutionConfidence.FAILED
            settling.failure_class = FailureClass.UNCERTAIN_OUTCOME
            settling.notes = (
                *settling.notes,
                "settled by an explicit operator reconciliation; the "
                "supervisor did not determine this outcome itself",
            )
            record = state.tasks[settling.task_id]
            self._release_lease(state, settling.task_id)
            # LEASED means the worker was never confirmed to have started, so
            # the task returns to READY and may be scheduled again. Anything
            # further along (ACTIVE, VERIFYING, REMEDIATING) means something
            # ran and its effect is unknown, which is a BLOCKED task needing a
            # human look -- not something to hand back to the scheduler.
            if record.state in IN_FLIGHT_STATES:
                target = (
                    NodeState.READY
                    if record.state is NodeState.LEASED
                    else NodeState.BLOCKED
                )
                self._transition(
                    state,
                    settling.task_id,
                    target,
                    reason="operator reconciled an uncertain attempt",
                )
            persist_state(self.root, state)
            append_event(
                self.root,
                "ATTEMPT_RECONCILED",
                {"attempt_id": resolve_uncertain, "task_id": settling.task_id},
            )
            resolved = {
                "attempt_id": resolve_uncertain,
                "task_id": settling.task_id,
                "task_state": state.tasks[settling.task_id].state.value,
            }

        return {
            "program_id": self.program.program_id,
            "interrupted_attempts": findings,
            "resolved": resolved,
            "merge_authorized": False,
        }

    # ------------------------------------------------------------ the cycle

    def _cycle(self, state: ProgramStateRecord, cycle: int) -> CycleResult:
        result = CycleResult(cycle=cycle)

        # 1. Ownership, before anything else this cycle decides.
        self._assert_still_supervisor()

        # 2. Collect anything that finished since the last cycle. Settling
        #    comes before deciding, so a task that just completed can unblock
        #    its dependants in this same cycle rather than the next one.
        if self._collect_finished(state, result, block=False):
            result.progressed = True

        # 3. Cancellation and program limits.
        if state.cancel_requested or stop_requested(self.lock_root):
            state.cancel_requested = True
            cancelled = cancel_observers(self.root, program_id=self.program.program_id)
            # Workers already running are asked to stop through the cancel
            # check they poll; whatever they had already done is recorded as
            # UNCERTAIN, never assumed either way.
            self._collect_finished(state, result, block=True)
            result.stop_reason = ProgramStopReason.CANCELLED
            result.notes.append(f"cancelled; {cancelled} pending observer(s) stood down")
            self._notify(
                "CANCELLED", "the program was cancelled", {}
            )
            return result
        if state.paused:
            # A pause lets running workers finish. Interrupting them would
            # convert a reversible operator decision into a set of uncertain
            # outcomes needing reconciliation, which is not what "pause" means
            # to anybody.
            if self._running:
                self._drain(state, result)
                result.progressed = True
            result.stop_reason = ProgramStopReason.PAUSED
            result.notes.append(
                f"paused by {state.paused_by or 'an operator'}; running workers "
                "were allowed to finish"
            )
            return result
        if state.total_launches >= self.program.limits.max_task_launches:
            result.stop_reason = ProgramStopReason.LIMIT_REACHED
            self._notify(
                "LAUNCH_LIMIT",
                "the program's launch limit was reached; no further worker may start",
                {
                    "total_launches": state.total_launches,
                    "max_task_launches": self.program.limits.max_task_launches,
                },
            )
            return result
        cost_cap = self.program.limits.max_estimated_cost_usd
        if cost_cap is not None and state.estimated_cost_usd >= cost_cap:
            result.stop_reason = ProgramStopReason.LIMIT_REACHED
            self._notify(
                "COST_LIMIT",
                (
                    "the program's estimated-cost ceiling was reached. This is a "
                    "client-side estimate reported by the runtime, not an "
                    "account spending limit"
                ),
                {
                    "estimated_cost_usd": state.estimated_cost_usd,
                    "max_estimated_cost_usd": cost_cap,
                },
            )
            return result

        # 3. Eligibility. A task with no external precondition is eligible as
        #    soon as it exists; one with a precondition waits in DISCOVERED
        #    until its event lands. Dependencies are NOT checked here --
        #    `select_next` owns that, and duplicating the rule would let the
        #    two disagree.
        if self._promote_unconditioned(state):
            result.progressed = True

        # 4. External events. Polled every cycle, blocking nothing.
        if self._poll_waits(state, result):
            result.progressed = True

        # 6. Program completion, checked against evidence-backed states only,
        #    and never while a worker is still running: a task whose worker has
        #    not reported cannot be in a done state, so this is belt and
        #    braces, but declaring a program finished with work in flight is
        #    the kind of claim that must be impossible rather than unlikely.
        if not self._running and all(
            record.state in DONE_STATES for record in state.tasks.values()
        ):
            state.complete = True
            result.stop_reason = ProgramStopReason.PROGRAM_COMPLETE
            self._notify(
                "PROGRAM_COMPLETE",
                "every task in the approved program reached an accepted state",
                {"tasks": sorted(state.tasks)},
            )
            return result

        # 7. Fill the available worker slots. Each iteration re-derives
        #    eligibility from the state the previous dispatch just changed, so
        #    the surface-overlap gate and the dependency rule are applied
        #    against what is actually in flight -- never against a snapshot
        #    taken before this cycle started dispatching.
        limit = self.program.limits.max_concurrent_workers
        while len(self._running) < limit:
            if state.total_launches >= self.program.limits.max_task_launches:
                if not result.dispatched:
                    result.stop_reason = ProgramStopReason.LIMIT_REACHED
                    self._notify(
                        "LAUNCH_LIMIT",
                        "the program's launch limit was reached",
                        {"total_launches": state.total_launches},
                    )
                break

            choice = self._choose(state, result)
            if choice is None:
                break

            # Recheck immediately before the consequential action. An
            # eligibility decision taken a moment ago is not a licence to
            # dispatch now: ownership can have changed, and a dispatch by a
            # supervisor that no longer owns the program is exactly the
            # duplicate-dispatch case this guards.
            self._assert_still_supervisor()
            if state.cancel_requested or stop_requested(self.lock_root):
                result.stop_reason = ProgramStopReason.CANCELLED
                break

            running = self._begin_dispatch(state, choice, result)
            if running is None:
                break
            self._submit(running)
            result.dispatched.append((choice.task_id, choice.mode))
            if result.dispatched_task_id is None:
                result.dispatched_task_id = choice.task_id
                result.dispatch_mode = choice.mode
            result.progressed = True
            # A stop reason set while filling slots (an owner gate, an
            # exhausted queue) is not a reason to abandon workers already
            # launched this cycle; it just ends the filling.
            if result.stop_reason is not None:
                break

        # 8. With workers still in flight, "nothing more to start" is a
        #    statement about this instant, not about the program. Selection
        #    reached it while a dependency was mid-flight, a surface was held,
        #    or an agent was busy -- all of which the next settle can change.
        #    Reporting it as the program's verdict would stop a program that
        #    is simply busy, so it is cleared and re-derived next cycle.
        if self._running:
            if result.stop_reason in _NOT_TERMINAL_WHILE_RUNNING:
                result.stop_reason = None
            # Wait for the FIRST worker to finish, never for all of them: a
            # supervisor that waited on the slowest before noticing the
            # fastest had unblocked three dependants would be serialising the
            # very thing concurrency is for. Every slot that could be filled
            # was filled above, before this wait. A stop reason that survived
            # the clearing above is real (cancelled, a limit) and skips it.
            if result.stop_reason is None and self._collect_finished(
                state, result, block=True
            ):
                result.progressed = True
        return result

    # ------------------------------------------------------- worker slots

    def _submit(self, running: RunningWork) -> None:
        """Hand one launched worker to a thread. Nothing shared goes with it."""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self.program.limits.max_concurrent_workers,
                thread_name_prefix=f"atlas-program-{self.program.program_id}",
            )
        future = self._executor.submit(running.adapter.run, running.request)
        self._running[running.attempt_id] = (running, future)
        self._max_concurrent_observed = max(
            self._max_concurrent_observed, len(self._running)
        )

    def _collect_finished(
        self, state: ProgramStateRecord, result: CycleResult, *, block: bool
    ) -> bool:
        """Settle every worker that has finished. Optionally wait for one.

        ``block=True`` waits for the FIRST worker to finish, not for all of
        them: a supervisor that waited for the slowest worker before noticing
        the fastest one had unblocked three dependants would be serialising
        the very thing concurrency is for.
        """
        if not self._running:
            return False
        if block:
            futures = [future for _running, future in self._running.values()]
            wait(futures, return_when=FIRST_COMPLETED)

        settled = False
        for attempt_id, (running, future) in list(self._running.items()):
            if not future.done():
                continue
            del self._running[attempt_id]
            try:
                outcome: AdapterOutcome | BaseException = future.result()
            except BaseException as exc:
                outcome = exc
            self._settle_running(state, running, outcome, result)
            result.settled.append(running.task_id)
            settled = True
        if settled:
            persist_state(self.root, state)
        return settled

    def _drain(self, state: ProgramStateRecord, result: CycleResult) -> None:
        """Settle every outstanding worker before the supervisor exits.

        A worker whose outcome was never applied is an attempt stuck at
        ADAPTER_INVOKED, which the next start correctly refuses to redispatch.
        Draining converts that into a real recorded outcome wherever the work
        actually finished, which is the difference between a program that
        resumes cleanly and one that needs a human to reconcile it.
        """
        while self._running:
            self._collect_finished(state, result, block=True)

    def _promote_unconditioned(self, state: ProgramStateRecord) -> bool:
        """DISCOVERED -> READY for every task with no external precondition.

        READY here means "this task exists and nothing external gates it",
        not "this task may run now": dependency order, owner gates and surface
        overlap are all still `select_next`'s decision, made freshly every
        cycle against the current node list. Pre-filtering them here as well
        would put the same rule in two places, and two copies of a scheduling
        rule drift.
        """
        changed = False
        for task in self.program.tasks:
            record = state.tasks[task.task_id]
            if record.state is not NodeState.DISCOVERED:
                continue
            if task.external_precondition is not None:
                continue
            self._transition(
                state,
                task.task_id,
                NodeState.READY,
                reason="no external precondition gates this task",
            )
            changed = True
        return changed

    def _poll_waits(self, state: ProgramStateRecord, result: CycleResult) -> bool:
        """Poll every registered external precondition once. Never blocks."""
        changed = False
        for task in self.program.tasks:
            precondition = task.external_precondition
            if precondition is None:
                continue
            record = state.tasks[task.task_id]
            if record.state is not NodeState.DISCOVERED:
                continue
            profile = self.loaded.effective_profile(task.task_id)
            ensure_observer(
                self.root,
                program_id=self.program.program_id,
                task_id=task.task_id,
                precondition=precondition,
                now=self._clock(),
            )
            record.pending_observer_id = None
            wait = poll_precondition(
                self.root,
                program_id=self.program.program_id,
                task_id=task.task_id,
                precondition=precondition,
                workspace=self.workspace,
                profile=profile,
                now=self._clock(),
            )
            if wait.outcome is WaitOutcome.PASSED:
                if wait.newly_terminal:
                    # Once-only consumption: a repeated terminal event returns
                    # newly_terminal=False and this promotion does not run
                    # twice, which is what keeps a re-delivered event from
                    # producing a second dispatch.
                    self._transition(
                        state,
                        task.task_id,
                        NodeState.READY,
                        reason=f"external precondition {precondition.precondition_id} passed",
                    )
                    changed = True
                    result.notes.append(
                        f"{task.task_id}: external event resolved, now eligible"
                    )
                else:
                    result.notes.append(
                        f"{task.task_id}: external event already consumed once; "
                        "not promoted again"
                    )
            elif wait.outcome in {WaitOutcome.FAILED, WaitOutcome.TIMED_OUT}:
                if wait.newly_terminal:
                    self._transition(
                        state,
                        task.task_id,
                        NodeState.BLOCKED,
                        reason=(
                            f"external precondition {precondition.precondition_id} "
                            f"{wait.outcome.value.lower()}: {wait.detail[:160]}"
                        ),
                    )
                    changed = True
                    self._notify(
                        "EXTERNAL_EVENT_FAILED",
                        f"task {task.task_id} will not become eligible",
                        {
                            "task_id": task.task_id,
                            "precondition": precondition.precondition_id,
                            "detail": wait.detail[:512],
                        },
                    )
            else:
                record.pending_observer_id = wait.observer_id
                result.notes.append(
                    f"{task.task_id}: waiting on {wait.observer_id} ({wait.detail[:120]})"
                )
        return changed

    def _choose(
        self, state: ProgramStateRecord, result: CycleResult
    ) -> DispatchChoice | None:
        """Pick one action. In-flight work first, then newly eligible work."""
        busy = self._busy_agents()

        # Independent verification of work that already passed acceptance.
        for task in self.program.tasks:
            record = state.tasks[task.task_id]
            if not record.awaiting_independent_verification:
                continue
            verifier = self.loaded.verifiers.get(task.task_id)
            if verifier is not None and verifier.agent_id in busy:
                continue
            if task.verifier_profile_ref is None:
                result.stop_reason = None
                result.notes.append(
                    f"{task.task_id}: passed acceptance and awaits independent "
                    "verification by a person; unrelated work continues"
                )
                continue
            return DispatchChoice(
                task_id=task.task_id,
                mode=DispatchMode.VERIFY,
                reason="acceptance passed; a distinct agent must now certify",
            )

        # Retry of a task whose acceptance failed and which has attempts left.
        for task in self.program.tasks:
            record = state.tasks[task.task_id]
            if record.state is not NodeState.REMEDIATING:
                continue
            profile = self.loaded.effective_profile(task.task_id)
            if profile.agent_id in busy:
                continue
            budget = min(
                self.program.limits.max_attempts_per_task, profile.limits.max_attempts
            )
            if record.attempts >= budget:
                self._transition(
                    state,
                    task.task_id,
                    NodeState.BLOCKED,
                    reason=f"attempt budget of {budget} exhausted",
                )
                self._notify(
                    "TASK_BLOCKED",
                    f"task {task.task_id} exhausted its attempt budget",
                    {"task_id": task.task_id, "attempts": record.attempts},
                )
                continue
            return DispatchChoice(
                task_id=task.task_id,
                mode=DispatchMode.RETRY,
                reason="previous attempt did not satisfy acceptance",
            )

        # A task that already holds ownership but has no live attempt was
        # interrupted before (or exactly at) its launch and has since been
        # reconciled as safe to start. Its lease is still valid, so it is
        # dispatched under the ownership it already has rather than being
        # granted a second one.
        for task in self.program.tasks:
            record = state.tasks[task.task_id]
            if record.state is not NodeState.LEASED:
                continue
            if self._has_open_attempt(state, task.task_id):
                continue
            if self.loaded.effective_profile(task.task_id).agent_id in busy:
                continue
            return DispatchChoice(
                task_id=task.task_id,
                mode=DispatchMode.NEW,
                reason="ownership already held; dispatching under the existing lease",
            )

        # Anything genuinely stuck in flight is a reconciliation matter. "In
        # flight" means an attempt that never reached a terminal phase -- a
        # task sitting in an in-flight DAG state with every attempt settled is
        # recoverable, and calling it stuck would stop a program that has
        # nothing wrong with it.
        live = {running.task_id for running, _future in self._running.values()}
        stuck = [
            task_id
            for task_id, record in state.tasks.items()
            if record.state in {NodeState.LEASED, NodeState.ACTIVE, NodeState.VERIFYING}
            and not record.awaiting_independent_verification
            and self._has_open_attempt(state, task_id)
            # A worker this supervisor is currently waiting on is not stuck.
            # Its attempt is open precisely because it is still running, and
            # calling that a reconciliation matter would stop a healthy
            # program the moment concurrency was switched on.
            and task_id not in live
        ]
        if stuck:
            result.stop_reason = ProgramStopReason.RECONCILE_REQUIRED
            self._notify(
                "RECONCILE_REQUIRED",
                (
                    "task(s) are in flight with no recorded outcome; run "
                    "`reconcile` before continuing"
                ),
                {"tasks": sorted(stuck)},
            )
            return None

        # Newly eligible work, chosen by the existing continuation policy.
        nodes = self._nodes_for_selection(state)
        decision = select_next(nodes)
        if decision.next_package_id is not None:
            handoff = state.handoffs.get(decision.next_package_id)
            if handoff is not None and handoff.consumed_by_attempt_id is None:
                return DispatchChoice(
                    task_id=decision.next_package_id,
                    mode=DispatchMode.RESUME,
                    reason=(
                        f"continuing session {handoff.session_id} enrolled by "
                        f"{handoff.enrolled_by}"
                    ),
                    resume_session_id=handoff.session_id,
                )
            return DispatchChoice(
                task_id=decision.next_package_id,
                mode=DispatchMode.NEW,
                reason="selected by the existing continuation policy",
            )

        result.stop_reason = self._map_stop_reason(state, decision.stop_reason, result)
        return None

    def _busy_agents(self) -> frozenset[str]:
        """Agents that already have a worker in flight.

        An enrolled agent is ONE worker, not a pool. Concurrency in this
        package comes from different agents working different surfaces, which
        is also what the durable lease projection independently enforces --
        it refuses a second active lease for one agent_id. Filtering here as
        well means that refusal is never reached in the ordinary case, so a
        busy agent produces "not eligible right now" rather than an exception.
        """
        return frozenset(
            running.profile.agent_id for running, _future in self._running.values()
        )

    def _nodes_for_selection(self, state: ProgramStateRecord) -> tuple[WorkNode, ...]:
        """The node list `select_next` sees, with busy agents' work held back.

        A READY task whose agent is already working is projected as
        DISCOVERED for this selection only. Nothing is persisted: the task is
        genuinely READY, it simply cannot start this instant, and saying so by
        reusing the existing "not yet eligible" state keeps one scheduler
        rather than adding a second filter `select_next` knows nothing about.
        """
        busy = self._busy_agents()
        if not busy:
            return self._nodes(state)
        nodes: list[WorkNode] = []
        for task in self.program.tasks:
            record = state.tasks[task.task_id]
            node_state = record.state
            if (
                node_state is NodeState.READY
                and self.loaded.effective_profile(task.task_id).agent_id in busy
            ):
                node_state = NodeState.DISCOVERED
            node = task.to_work_node(base_pin=self.program.base_pin)
            nodes.append(node.model_copy(update={"state": node_state}))
        return tuple(nodes)

    def _authority_revoked(self, task_id: str) -> str | None:
        """Has the authority behind this dispatch changed since start-up?

        Re-read from the durable roster, not from the in-memory binding, and
        called immediately before the launch rather than once at start-up. A
        long program can outlive the decision that authorized it: an agent gets
        suspended, retired, re-enrolled, or has its runtime-substitution grant
        withdrawn, and none of that should be discovered one task too late.

        Returns a reason string when the dispatch must not proceed, else None.
        """
        if not self.enrolled_agents or self.registry_root is None:
            return None
        try:
            registry = load_registry(self.registry_root)
        except ProgramError as exc:
            return f"the agent roster could not be read: {exc}"

        for agent_id, kind in self._agents_this_dispatch_uses(task_id):
            reason = self._agent_authority_revoked(registry, agent_id, kind)
            if reason is not None:
                return reason
        return None

    def _agents_this_dispatch_uses(self, task_id: str) -> list[tuple[str, str]]:
        """Every enrolled identity a dispatch of this task would rely on.

        The verifier is included deliberately. Checking only the implementer
        let an impeccable implementer start work whose verifier had been
        suspended, retired or un-enrolled -- the independence the task asked
        for was gone, and nothing said so until far later.
        """
        used: list[tuple[str, str]] = []
        seen: set[str] = set()
        profile = self.loaded.effective.get(task_id)
        if profile is not None and profile.agent_id not in seen:
            used.append((profile.agent_id, "implementer"))
            seen.add(profile.agent_id)
        verifier = self.loaded.verifiers.get(task_id)
        if verifier is not None and verifier.agent_id not in seen:
            used.append((verifier.agent_id, "verifier"))
            seen.add(verifier.agent_id)
        return used

    def _agent_authority_revoked(
        self, registry: AgentRegistry, agent_id: str, kind: str
    ) -> str | None:
        """Re-read one agent's standing. Returns a reason, or None."""
        launched_as = next(
            (a for a in self.enrolled_agents if a.agent_id == agent_id), None
        )
        if launched_as is None:
            # Not an enrolled identity at all: the program's own placeholder.
            # Nothing was bound to it, so there is no enrollment to withdraw.
            return None
        current = registry.agents.get(agent_id)
        if current is None:
            return (
                f"{kind} {agent_id} is no longer enrolled; its record was "
                "removed after this program started"
            )
        if current.status is not AgentStatus.ACTIVE:
            return (
                f"{kind} {agent_id} is {current.status.value}; dispatch is "
                "withheld until it is active again"
            )
        if current.role != launched_as.role:
            return (
                f"{kind} {agent_id} was re-enrolled into role {current.role!r}, "
                f"not {launched_as.role!r}, since this program started"
            )
        # An assignment is not a one-time gate. Re-reading it here is what stops
        # a binding recorded before the first task from carrying a later task
        # after the operator pointed that agent at a different program.
        expected = str(self.loaded.source_path.expanduser().resolve())
        assigned = current.assigned_program
        if assigned is None:
            return (
                f"{kind} {agent_id} has no assigned program; the assignment "
                "recorded when this program started has since been cleared"
            )
        if str(Path(assigned).expanduser().resolve()) != expected:
            return (
                f"{kind} {agent_id} is now assigned a different program "
                f"({assigned}); the binding this dispatch would use is stale"
            )
        # A substitution grant is per agent record and is cleared by
        # re-enrolment. If the effective profile is only valid because of one,
        # its withdrawal must stop the next dispatch, not merely the next
        # assignment.
        program_profile = self.loaded.profiles.profiles.get(launched_as.role)
        substituting = (
            program_profile is not None and program_profile.adapter is not current.adapter
        )
        if substituting and not current.runtime_substitution_authorized:
            return (
                f"{kind} {agent_id} runs role {launched_as.role!r} on a "
                "substituted runtime and that authorization has been withdrawn"
            )
        return None

    def _has_open_attempt(self, state: ProgramStateRecord, task_id: str) -> bool:
        """Does this task have an attempt that never reached a terminal phase?"""
        return any(
            attempt.task_id == task_id and attempt.phase is not AttemptPhase.TERMINAL
            for attempt in state.attempts.values()
        )

    def _map_stop_reason(
        self,
        state: ProgramStateRecord,
        reason: StopReason | None,
        result: CycleResult,
    ) -> ProgramStopReason | None:
        """Translate the shared continuation vocabulary into this package's.

        Explicit rather than conflated: ``StopReason`` has no member for
        program completion, an outstanding external wait, or a pending
        independent verification, and reporting NO_ELIGIBLE_WORK for any of
        those three would tell the operator the wrong thing.
        """
        waiting = [
            task_id
            for task_id, record in state.tasks.items()
            if record.pending_observer_id and record.state is NodeState.DISCOVERED
        ]
        awaiting_iv = [
            task_id
            for task_id, record in state.tasks.items()
            if record.awaiting_independent_verification
        ]

        if reason is StopReason.OWNER_GATE:
            gates = []
            for task in self.program.tasks:
                record = state.tasks[task.task_id]
                if task.owner_gate is None or record.state in DONE_STATES:
                    continue
                decision = evaluate_owner_action(task.owner_gate, owner_grant=False)
                gates.append(
                    {
                        "task_id": task.task_id,
                        "gate": task.owner_gate.value,
                        "allowed": decision.allowed,
                        "reason": decision.reason,
                    }
                )
            self._notify_unless_busy(
                "OWNER_DECISION_REQUIRED",
                "remaining work is owner-gated and cannot be started autonomously",
                {"gates": gates},
            )
            return ProgramStopReason.OWNER_DECISION_REQUIRED

        if waiting:
            result.notes.append(
                "no eligible work right now; "
                f"{len(waiting)} task(s) waiting on an external event"
            )
            # Deliberately not a stop: the loop sleeps and polls again, which
            # is what lets independent work start the moment the event lands.
            return None

        if awaiting_iv:
            self._notify_unless_busy(
                "AWAITING_INDEPENDENT_VERIFICATION",
                (
                    "every remaining task has passed acceptance and needs an "
                    "independent verifier this program does not configure"
                ),
                {"tasks": sorted(awaiting_iv)},
            )
            return ProgramStopReason.AWAITING_INDEPENDENT_VERIFICATION

        if reason is StopReason.HARD_BLOCKER:
            blocked = sorted(
                task_id
                for task_id, record in state.tasks.items()
                if record.state is NodeState.BLOCKED
            )
            self._notify_unless_busy(
                "HARD_BLOCKER",
                "no eligible work remains and blocked work needs attention",
                {"blocked": blocked},
            )
            return ProgramStopReason.HARD_BLOCKER

        if reason is StopReason.RESOURCE_BOUNDARY:
            return ProgramStopReason.LIMIT_REACHED

        self._notify_unless_busy(
            "NO_ELIGIBLE_WORK",
            "no task is eligible and nothing is pending",
            {
                "states": {
                    task_id: record.state.value
                    for task_id, record in sorted(state.tasks.items())
                }
            },
        )
        return ProgramStopReason.NO_ELIGIBLE_WORK

    # --------------------------------------------------------------- dispatch

    def _adapter_for(self, profile: AgentProfile) -> RuntimeAdapter:
        adapter = self.adapters.get(profile.profile_id)
        if adapter is None:
            adapter = _build_adapter(profile)
            self.adapters[profile.profile_id] = adapter
        return adapter

    def _next_sequence(self, state: ProgramStateRecord) -> int:
        self._sequence = max(self._sequence + 1, state.total_launches + 1)
        return self._sequence

    def _ensure_lease(
        self, state: ProgramStateRecord, task: ProgramTask, profile: AgentProfile
    ) -> AgentLease:
        """Grant and durably project ownership of the task's mutation surface.

        The DAG state transition to LEASED and the durable projection row are
        both written before any worker starts. A crash after this point leaves
        an ACTIVE lease row on disk, which is exactly how a restarting
        supervisor knows the surface was claimed.
        """
        existing = self._leases.get(task.task_id)
        if existing is not None and existing.active:
            return existing
        # A restart loses the in-memory lease but not the durable row. Ownership
        # of a surface is exactly the fact that must survive a crash, so the
        # projection is consulted before concluding this task is unclaimed --
        # otherwise a retry after a restart would either be refused (the task
        # is no longer READY) or, worse, granted a second lease over a surface
        # the projection already says is held.
        rehydrated = self._rehydrate_lease(task)
        if rehydrated is not None:
            self._leases[task.task_id] = rehydrated
            return rehydrated
        record = state.tasks[task.task_id]
        if record.state is not NodeState.READY:
            raise SupervisorError(
                f"task {task.task_id} is {record.state.value}, not READY",
                code="NODE_NOT_READY",
            )
        node = task.to_work_node(base_pin=self.program.base_pin).model_copy(
            update={"state": NodeState.READY}
        )
        sequence = self._next_sequence(state)
        lease = grant_lease(
            lease_id=f"{self.program.program_id}-{task.task_id}-{sequence}",
            agent=profile.to_agent_record(),
            node=node,
            branch=_current_branch(self.workspace),
            worktree=str(self.workspace),
            sequence=sequence,
        )
        try:
            project_grant(
                state_dir(self.root), lease, live_main=self.program.base_pin
            )
        except ProjectionError as exc:
            raise SupervisorError(
                f"could not durably record ownership of {task.task_id}: {exc}",
                code=str(getattr(exc, "code", "LEASE_PROJECTION_ERROR")),
            ) from exc
        self._leases[task.task_id] = lease
        state.tasks[task.task_id].last_attempt_id = None
        self._transition(
            state, task.task_id, NodeState.LEASED, reason="ownership granted"
        )
        append_event(
            self.root,
            "LEASE_GRANTED",
            {
                "task_id": task.task_id,
                "lease_id": lease.lease_id,
                "agent_id": lease.agent_id,
                "authorized_paths": list(lease.authorized_paths),
            },
        )
        return lease

    def _rehydrate_lease(self, task: ProgramTask) -> AgentLease | None:
        """Reconstruct this task's lease from the durable projection, if any.

        Returns ``None`` when no ACTIVE row names this task. A row whose
        ``base_pin`` differs from the program's approved pin is deliberately
        NOT rehydrated: it belongs to a different revision of this program and
        adopting it would silently continue work under an approval that is not
        the current one.
        """
        try:
            projection = load_projection(state_dir(self.root))
        except ProjectionError:
            return None
        for row in active_rows(projection):
            if row.package_id != task.task_id:
                continue
            if row.base_pin != self.program.base_pin:
                append_event(
                    self.root,
                    "LEASE_REHYDRATION_REFUSED",
                    {
                        "task_id": task.task_id,
                        "lease_id": row.lease_id,
                        "row_base_pin": row.base_pin,
                        "program_base_pin": self.program.base_pin,
                        "reason": "the row belongs to a different program revision",
                    },
                )
                return None
            try:
                capabilities = tuple(
                    AgentCapability(item) for item in row.capabilities
                )
            except ValueError:
                return None
            lease = AgentLease(
                lease_id=row.lease_id,
                agent_id=row.agent_id,
                package_id=row.package_id,
                branch=row.branch,
                worktree=row.worktree,
                base_pin=row.base_pin,
                authorized_paths=row.authorized_paths,
                forbidden_paths=row.forbidden_paths,
                capabilities=capabilities,
                start_state=row.start_state,
                expected_output="EVIDENCE_BUNDLE",
                expiry_or_terminal_condition="UNTIL_NODE_TERMINAL",
                active=True,
                sequence=row.created_sequence,
            )
            append_event(
                self.root,
                "LEASE_REHYDRATED",
                {"task_id": task.task_id, "lease_id": row.lease_id},
            )
            return lease
        return None

    def _release_lease(self, state: ProgramStateRecord, task_id: str) -> None:
        lease = self._leases.pop(task_id, None)
        if lease is None:
            return
        try:
            project_release(
                state_dir(self.root),
                lease,
                live_main=self.program.base_pin,
            )
        except ProjectionError as exc:
            # A projection that cannot be released is recorded, never
            # swallowed: the row stays ACTIVE on disk and a later run will
            # see it, which is preferable to a supervisor believing it let go
            # of a surface it still owns.
            append_event(
                self.root,
                "LEASE_RELEASE_FAILED",
                {"task_id": task_id, "lease_id": lease.lease_id, "error": str(exc)},
            )
            return
        release_lease(lease)
        append_event(
            self.root, "LEASE_RELEASED", {"task_id": task_id, "lease_id": lease.lease_id}
        )
        _ = state

    def _build_request(
        self,
        *,
        task: ProgramTask,
        profile: AgentProfile,
        attempt: AttemptRecord,
        resume_session_id: str | None,
        cancel_check: Callable[[], bool] | None,
        instruction: str | None = None,
    ) -> AdapterRequest:
        workspace = self.workspace
        if profile.workspace.working_subdir != ".":
            workspace = (workspace / profile.workspace.working_subdir).resolve()
            if not workspace.is_relative_to(self.workspace.resolve()):
                raise SupervisorError(
                    "profile working_subdir escapes the workspace",
                    code="WORKSPACE_ESCAPE",
                )
        timeout = min(
            self.program.limits.max_task_seconds, profile.limits.max_seconds
        )
        return AdapterRequest(
            program_id=self.program.program_id,
            task_id=task.task_id,
            attempt_id=attempt.attempt_id,
            attempt_number=attempt.attempt_number,
            idempotency_key=attempt.idempotency_key,
            instruction=instruction if instruction is not None else task.instruction,
            workspace=workspace,
            profile=profile,
            session_id=attempt.runtime_session_id,
            resume_session_id=resume_session_id,
            timeout_seconds=timeout,
            evidence_dir=evidence_dir(self.root),
            cancel_requested=cancel_check,
        )

    def _begin_dispatch(
        self,
        state: ProgramStateRecord,
        choice: DispatchChoice,
        result: CycleResult,
    ) -> RunningWork | None:
        """Take one task all the way to a launched worker, then return.

        Everything up to and including the launch happens on the supervisor's
        own thread: the lease, the durable dispatch intent, the DAG
        transitions. Only ``adapter.run`` is handed to a worker thread. That
        split is what makes concurrency safe without a single lock -- program
        state is never mutated from more than one thread, because the worker
        threads never touch it. They receive an immutable request and return
        an outcome; the supervisor settles it.
        """
        task = self.program.task(choice.task_id)
        record = state.tasks[task.task_id]
        verifying = choice.mode is DispatchMode.VERIFY
        profile = (
            self.loaded.verifiers[task.task_id]
            if verifying
            else self.loaded.effective_profile(task.task_id)
        )
        adapter = self._adapter_for(profile)

        revoked = self._authority_revoked(task.task_id)
        if revoked is not None:
            append_event(
                self.root,
                "AUTHORITY_REVOKED",
                {"task_id": task.task_id, "agent_id": profile.agent_id, "reason": revoked},
            )
            self._transition(
                state,
                task.task_id,
                NodeState.OWNER_HELD,
                reason=f"authority revoked: {revoked}"[:512],
            )
            result.stop_reason = ProgramStopReason.OWNER_DECISION_REQUIRED
            self._notify(
                "AUTHORITY_REVOKED",
                (
                    f"task {task.task_id} was not dispatched: {revoked}. "
                    "Nothing was launched"
                ),
                {"task_id": task.task_id, "agent_id": profile.agent_id},
            )
            return None

        try:
            adapter.preflight(profile)
        except AdapterUnavailableError as exc:
            # BLOCKED is reachable from every state a task can be in when a
            # dispatch is attempted (DISCOVERED, READY, LEASED, ACTIVE,
            # VERIFYING, REMEDIATING all have the edge), so no branch is needed
            # here -- an earlier version had one whose arms were identical.
            self._transition(
                state,
                task.task_id,
                NodeState.BLOCKED,
                reason=f"runtime unavailable: {exc}",
            )
            record.last_failure_class = FailureClass.QUOTA_OR_CREDENTIAL
            result.stop_reason = ProgramStopReason.HARD_BLOCKER
            self._notify(
                "RUNTIME_UNAVAILABLE",
                f"the runtime for profile {profile.profile_id} cannot run: {exc}",
                {"task_id": task.task_id, "code": getattr(exc, "code", "ADAPTER_UNAVAILABLE")},
            )
            return None

        if not verifying:
            try:
                self._ensure_lease(state, task, profile)
            except SupervisorError as exc:
                if getattr(exc, "code", "") not in {
                    "FOREIGN_WORKER",
                    "DUPLICATE_ACTIVE_LEASE",
                }:
                    raise
                # The durable projection refused because this agent, or this
                # task, is already owned. That is the ownership rule doing its
                # job, not a failure: the task waits and is offered again next
                # cycle. Nothing is transitioned and nothing is launched.
                append_event(
                    self.root,
                    "DISPATCH_DEFERRED",
                    {
                        "task_id": task.task_id,
                        "agent_id": profile.agent_id,
                        "code": getattr(exc, "code", ""),
                    },
                )
                result.notes.append(
                    f"{task.task_id}: deferred, ownership already held "
                    f"({getattr(exc, 'code', '')})"
                )
                return None

        capabilities = adapter.capabilities
        attempt_number = record.attempts + 1
        attempt_id = (
            f"{self.program.program_id}.{task.task_id}."
            f"{'verify' if verifying else 'run'}.{attempt_number}."
            f"{uuid.uuid4().hex[:8]}"
        )
        sha = profile_digest(profile)
        attempt = AttemptRecord(
            attempt_id=attempt_id,
            task_id=task.task_id,
            attempt_number=attempt_number,
            idempotency_key=idempotency_key(
                program_id=self.program.program_id,
                task_id=task.task_id,
                attempt_number=attempt_number,
                adapter_id=capabilities.adapter_id,
                profile_sha=sha,
                base_pin=self.program.base_pin,
            ),
            profile_id=profile.profile_id,
            agent_id=profile.agent_id,
            adapter=capabilities.adapter_id,
            profile_digest=sha,
            base_pin=self.program.base_pin,
            lease_id=_lease_id_for(self._leases, task.task_id, verifying=verifying),
            # A session id is pre-assigned only for a runtime that will accept
            # the one we hand it. Codex mints its own and announces it on its
            # event stream, so writing a made-up id into the checkpoint here
            # would record an identity that names nothing -- worse than
            # recording none, because a recovery probe would then look for it.
            runtime_session_id=(
                choice.resume_session_id
                or (new_session_id() if capabilities.accepts_assigned_session else None)
            ),
        )
        if choice.resume_session_id:
            attempt.runtime_session_id = choice.resume_session_id

        if choice.mode is DispatchMode.RESUME:
            handoff = state.handoffs.get(task.task_id)
            if handoff is not None and handoff.consumed_by_attempt_id is None:
                # Spent at the moment of dispatch, not after the run returns:
                # a handoff still marked pending when the supervisor dies would
                # resume the same session again on the next start, which is a
                # duplicate dispatch under another name.
                handoff.consumed_by_attempt_id = attempt_id

        # DISPATCH INTENT, persisted BEFORE the launch. A crash between here
        # and the adapter returning leaves exactly this record on disk, and
        # `recovery.classify_attempt` is what reads it.
        state.attempts[attempt_id] = attempt
        record.last_attempt_id = attempt_id
        record.attempts = attempt_number
        persist_state(self.root, state)
        append_event(
            self.root,
            "DISPATCH_INTENT",
            {
                "attempt_id": attempt_id,
                "task_id": task.task_id,
                "mode": choice.mode.value,
                "profile_id": profile.profile_id,
                "agent_id": profile.agent_id,
                "adapter": capabilities.adapter_id,
                "idempotency_key": attempt.idempotency_key,
                "runtime_session_id": attempt.runtime_session_id,
            },
        )

        if not verifying and record.state is NodeState.LEASED:
            self._transition(state, task.task_id, NodeState.ACTIVE, reason="worker starting")
        elif not verifying and record.state is NodeState.REMEDIATING:
            self._transition(
                state, task.task_id, NodeState.ACTIVE, reason="retrying after acceptance failure"
            )
        persist_state(self.root, state)

        instruction = (
            _verification_instruction(task) if verifying else task.instruction
        )
        request = self._build_request(
            task=task,
            profile=profile,
            attempt=attempt,
            resume_session_id=choice.resume_session_id,
            cancel_check=lambda: state.cancel_requested or stop_requested(self.lock_root),
            instruction=instruction,
        )

        attempt.phase = AttemptPhase.ADAPTER_INVOKED
        persist_state(self.root, state)
        state.total_launches += 1
        record.launches += 1
        self._launches_this_run += 1

        return RunningWork(
            attempt_id=attempt_id,
            task_id=task.task_id,
            mode=choice.mode,
            verifying=verifying,
            profile=profile,
            adapter=adapter,
            request=request,
            started_at=self._clock(),
        )

    def _settle_running(
        self,
        state: ProgramStateRecord,
        running: RunningWork,
        outcome: AdapterOutcome | BaseException,
        result: CycleResult,
    ) -> None:
        """Apply one finished worker's outcome. Always on the supervisor's thread."""
        task = self.program.task(running.task_id)
        attempt = state.attempts[running.attempt_id]
        attempt_id = running.attempt_id
        verifying = running.verifying
        profile = running.profile

        if isinstance(outcome, BaseException):
            attempt.phase = AttemptPhase.ADAPTER_RETURNED
            attempt.confidence = ExecutionConfidence.UNCERTAIN
            attempt.failure_class = FailureClass.UNCERTAIN_OUTCOME
            attempt.notes = (*attempt.notes, f"adapter raised: {outcome}"[:512])
            attempt.ended_at = _now_iso()
            persist_state(self.root, state)
            append_event(
                self.root,
                "ADAPTER_RAISED",
                {
                    "attempt_id": attempt_id,
                    "task_id": task.task_id,
                    "error": str(outcome)[:512],
                },
            )
            result.stop_reason = ProgramStopReason.RECONCILE_REQUIRED
            self._notify(
                "UNCERTAIN_OUTCOME",
                f"the adapter for {task.task_id} raised; its effect is unknown",
                {"task_id": task.task_id, "attempt_id": attempt_id},
            )
            return

        attempt.phase = AttemptPhase.ADAPTER_RETURNED
        attempt.exit_status = outcome.exit_status
        attempt.confidence = outcome.confidence
        attempt.failure_class = outcome.failure_class
        attempt.worker_reported = outcome.reported
        attempt.process_pid = outcome.pid
        attempt.process_start_identity = outcome.process_start_identity
        attempt.runtime_session_id = outcome.session_id or attempt.runtime_session_id
        attempt.evidence_paths = outcome.evidence
        attempt.estimated_cost_usd = outcome.estimated_cost_usd
        attempt.policy_denials = outcome.policy_denials
        attempt.usage = dict(outcome.usage)
        attempt.notes = (*attempt.notes, *outcome.notes)
        attempt.ended_at = _now_iso()
        if outcome.estimated_cost_usd:
            state.estimated_cost_usd += float(outcome.estimated_cost_usd)
        persist_state(self.root, state)
        append_event(
            self.root,
            "ADAPTER_RETURNED",
            {
                "attempt_id": attempt_id,
                "task_id": task.task_id,
                "terminal_state": outcome.terminal_state,
                "exit_status": outcome.exit_status,
                "confidence": outcome.confidence.value,
                "failure_class": (
                    outcome.failure_class.value if outcome.failure_class else None
                ),
            },
        )

        if outcome.confidence is ExecutionConfidence.UNCERTAIN:
            self._handle_uncertain(state, task, attempt, outcome.terminal_state, result)
            return

        self._evaluate_and_settle(
            state,
            task=task,
            profile=self.loaded.effective_profile(task.task_id),
            attempt=attempt,
            verifying=verifying,
            verifier_agent_id=profile.agent_id if verifying else None,
            result=result,
        )

    def _handle_uncertain(
        self,
        state: ProgramStateRecord,
        task: ProgramTask,
        attempt: AttemptRecord,
        terminal_state: str,
        result: CycleResult,
    ) -> None:
        """An uncertain outcome stops the program and is never retried here.

        The lease is deliberately NOT released. The worker may have left the
        workspace half-changed, and handing the surface to anything else while
        that is unknown would be worse than stopping. `reconcile` is the way
        out, and it records an operator's judgement rather than inventing one.
        """
        attempt.notes = (
            *attempt.notes,
            f"terminal_state={terminal_state}; external effect is UNKNOWN and "
            "was NOT assumed either way",
        )
        persist_state(self.root, state)
        self._notify(
            "UNCERTAIN_OUTCOME",
            (
                f"task {task.task_id} ended in an uncertain state "
                f"({terminal_state}); its external effects are unknown"
            ),
            {
                "task_id": task.task_id,
                "attempt_id": attempt.attempt_id,
                "runtime_session_id": attempt.runtime_session_id,
                "next": "run `reconcile` to inspect and settle it",
            },
        )
        result.stop_reason = (
            ProgramStopReason.CANCELLED
            if terminal_state in {"cancelled"}
            else ProgramStopReason.RECONCILE_REQUIRED
        )

    def _evaluate_and_settle(
        self,
        state: ProgramStateRecord,
        *,
        task: ProgramTask,
        profile: AgentProfile,
        attempt: AttemptRecord,
        verifying: bool,
        verifier_agent_id: str | None,
        result: CycleResult,
    ) -> None:
        """Acceptance, then the task's next DAG state. Evidence decides both."""
        record = state.tasks[task.task_id]

        if attempt.confidence is ExecutionConfidence.FAILED:
            failure = attempt.failure_class or FailureClass.TRANSIENT_INFRASTRUCTURE
            record.last_failure_class = failure
            attempt.phase = AttemptPhase.TERMINAL
            if record.state is NodeState.ACTIVE:
                if failure in PERMANENT_FAILURES:
                    self._transition(
                        state,
                        task.task_id,
                        NodeState.BLOCKED,
                        reason=f"worker failed permanently: {failure.value}",
                    )
                    self._release_lease(state, task.task_id)
                    self._notify(
                        "TASK_BLOCKED",
                        (
                            f"task {task.task_id} failed with {failure.value}, "
                            "which is never retried"
                        ),
                        {"task_id": task.task_id, "failure_class": failure.value},
                    )
                    if failure is FailureClass.QUOTA_OR_CREDENTIAL:
                        result.stop_reason = ProgramStopReason.HARD_BLOCKER
                else:
                    self._transition(
                        state,
                        task.task_id,
                        NodeState.REMEDIATING,
                        reason=f"worker failed: {failure.value}",
                    )
            persist_state(self.root, state)
            return

        acceptance = evaluate_task(task, workspace=self.workspace, profile=profile)
        attempt.acceptance_passed = acceptance.passed
        attempt.acceptance_detail = tuple(
            check.to_public_dict() for check in acceptance.checks
        )
        attempt.phase = AttemptPhase.ACCEPTANCE_EVALUATED
        fingerprint = progress_fingerprint(self.workspace, profile)
        persist_state(self.root, state)

        evidence_name = f"{attempt.attempt_id}.acceptance.json"
        write_evidence(
            self.root,
            evidence_name,
            {
                "program_id": self.program.program_id,
                "task_id": task.task_id,
                "attempt_id": attempt.attempt_id,
                "adapter": attempt.adapter,
                "agent_id": attempt.agent_id,
                "worker_reported": attempt.worker_reported,
                "worker_report_is_not_acceptance": True,
                "acceptance": acceptance.to_public_dict(),
                "progress_fingerprint": fingerprint,
            },
        )
        attempt.evidence_paths = (*attempt.evidence_paths, evidence_name)
        append_event(
            self.root,
            "ACCEPTANCE_EVALUATED",
            {
                "attempt_id": attempt.attempt_id,
                "task_id": task.task_id,
                "passed": acceptance.passed,
                "failed_checks": [
                    check.check_id for check in acceptance.checks if not check.passed
                ],
            },
        )

        if verifying:
            self._settle_verification(
                state,
                task=task,
                attempt=attempt,
                acceptance=acceptance,
                verifier_agent_id=verifier_agent_id,
                result=result,
            )
            return

        no_progress = (
            fingerprint != "UNMEASURABLE"
            and record.progress_fingerprint == fingerprint
            and not acceptance.passed
        )
        record.progress_fingerprint = fingerprint

        if record.state is NodeState.ACTIVE:
            self._transition(
                state,
                task.task_id,
                NodeState.VERIFYING,
                reason="worker finished; acceptance evaluated by the supervisor",
            )

        if acceptance.passed:
            self._settle_accepted(state, task=task, attempt=attempt, result=result)
            return

        # A run that was denied what it asked for and then failed acceptance
        # did not fail for a reason another attempt would fix. Classifying it
        # as a retryable acceptance failure would burn the whole attempt budget
        # re-running a worker that will be denied the same thing every time.
        if attempt.policy_denials:
            record.last_failure_class = FailureClass.POLICY_REFUSAL
        elif no_progress:
            record.last_failure_class = FailureClass.NO_PROGRESS
        else:
            record.last_failure_class = FailureClass.ACCEPTANCE_FAILED
        attempt.phase = AttemptPhase.TERMINAL
        failed = [check.check_id for check in acceptance.checks if not check.passed]
        if attempt.policy_denials:
            self._transition(
                state,
                task.task_id,
                NodeState.BLOCKED,
                reason=(
                    f"the runtime denied {attempt.policy_denials} request(s) "
                    "and acceptance did not pass: the worker did not have what "
                    "the task needs"
                ),
            )
            self._release_lease(state, task.task_id)
            self._notify(
                "POLICY_REFUSAL",
                (
                    f"task {task.task_id}: the runtime denied "
                    f"{attempt.policy_denials} request(s) and acceptance failed. "
                    "Retrying would be denied the same thing; widen the "
                    "profile's permissions or narrow the task"
                ),
                {
                    "task_id": task.task_id,
                    "policy_denials": attempt.policy_denials,
                    "failed_checks": failed,
                },
            )
        elif no_progress:
            self._transition(
                state,
                task.task_id,
                NodeState.BLOCKED,
                reason=(
                    "acceptance failed and the workspace is byte-identical to "
                    "the previous attempt: no observable progress"
                ),
            )
            self._release_lease(state, task.task_id)
            self._notify(
                "NO_PROGRESS",
                (
                    f"task {task.task_id} ran again and changed nothing "
                    "observable; it will not be retried"
                ),
                {"task_id": task.task_id, "failed_checks": failed},
            )
        else:
            self._transition(
                state,
                task.task_id,
                NodeState.REMEDIATING,
                reason=(
                    "the worker reported completion but acceptance did not "
                    f"pass: {', '.join(failed[:8])}"
                ),
            )
            self._notify(
                "ACCEPTANCE_FAILED",
                (
                    f"task {task.task_id}: the worker finished cleanly and the "
                    "supervisor's own acceptance checks did not pass"
                ),
                {"task_id": task.task_id, "failed_checks": failed},
            )
        persist_state(self.root, state)

    def _settle_accepted(
        self,
        state: ProgramStateRecord,
        *,
        task: ProgramTask,
        attempt: AttemptRecord,
        result: CycleResult,
    ) -> None:
        record = state.tasks[task.task_id]
        attempt.phase = AttemptPhase.TERMINAL
        record.last_failure_class = None
        if task.requires_independent_verification:
            record.awaiting_independent_verification = True
            self._release_lease(state, task.task_id)
            persist_state(self.root, state)
            append_event(
                self.root,
                "AWAITING_INDEPENDENT_VERIFICATION",
                {"task_id": task.task_id, "implementer_agent_id": attempt.agent_id},
            )
            result.notes.append(
                f"{task.task_id}: acceptance passed; its own worker cannot "
                "satisfy the verification gate, so unrelated work continues"
            )
            return
        self._transition(
            state,
            task.task_id,
            NodeState.CERTIFIED,
            reason="every acceptance condition observed to hold",
        )
        self._release_lease(state, task.task_id)
        persist_state(self.root, state)
        append_event(
            self.root,
            "TASK_ACCEPTED",
            {"task_id": task.task_id, "attempt_id": attempt.attempt_id},
        )
        result.notes.append(
            f"{task.task_id}: accepted. TASK_COMPLETE != PROGRAM_COMPLETE; "
            "the supervisor selects the next eligible task without a prompt"
        )

    def _settle_verification(
        self,
        state: ProgramStateRecord,
        *,
        task: ProgramTask,
        attempt: AttemptRecord,
        acceptance: AcceptanceResult,
        verifier_agent_id: str | None,
        result: CycleResult,
    ) -> None:
        record = state.tasks[task.task_id]
        attempt.phase = AttemptPhase.TERMINAL
        implementer = self.loaded.effective_profile(task.task_id).agent_id
        if verifier_agent_id is None or verifier_agent_id == implementer:
            # Defence in depth: the loader already refuses this at validation
            # time. If it ever reached here it would mean an agent certified
            # its own work, which is the one thing this gate exists to stop.
            raise SupervisorError(
                f"task {task.task_id} would be certified by its own implementer",
                code="IMPLEMENTER_CANNOT_VERIFY",
            )
        if not acceptance.passed:
            record.awaiting_independent_verification = False
            self._transition(
                state,
                task.task_id,
                NodeState.BLOCKED,
                reason="independent verification did not confirm acceptance",
            )
            persist_state(self.root, state)
            self._notify(
                "VERIFICATION_FAILED",
                f"an independent verifier could not confirm task {task.task_id}",
                {"task_id": task.task_id, "verifier_agent_id": verifier_agent_id},
            )
            return
        record.awaiting_independent_verification = False
        record.verified_by_agent_id = verifier_agent_id
        self._transition(
            state,
            task.task_id,
            NodeState.CERTIFIED,
            reason=f"independently verified by {verifier_agent_id}",
        )
        persist_state(self.root, state)
        append_event(
            self.root,
            "TASK_VERIFIED",
            {
                "task_id": task.task_id,
                "verifier_agent_id": verifier_agent_id,
                "implementer_agent_id": implementer,
            },
        )
        result.notes.append(f"{task.task_id}: independently verified")

    # -------------------------------------------------------------- recovery

    def _reconcile_on_start(self, state: ProgramStateRecord) -> ProgramStopReason | None:
        """Reconcile before selecting anything. Never redispatch on a guess."""
        blockers: list[dict[str, Any]] = []
        for attempt in sorted(state.attempts.values(), key=lambda item: item.started_at):
            if attempt.phase is AttemptPhase.TERMINAL:
                continue
            task = self.program.task(attempt.task_id)
            profile = self.loaded.effective_profile(task.task_id)
            adapter = self._adapter_for(profile)
            request = self._build_request(
                task=task,
                profile=profile,
                attempt=attempt,
                resume_session_id=None,
                cancel_check=None,
            )
            verdict = classify_attempt(
                attempt,
                task=task,
                adapter=adapter,
                capabilities=adapter.capabilities,
                request=request,
            )
            append_event(
                self.root,
                "RESTART_RECONCILIATION",
                {
                    "attempt_id": attempt.attempt_id,
                    "task_id": attempt.task_id,
                    "phase": attempt.phase.value,
                    "action": verdict.action.value,
                    "reason": verdict.reason,
                },
            )

            if verdict.action is RecoveryAction.WORKER_STILL_RUNNING:
                blockers.append(
                    {
                        "task_id": attempt.task_id,
                        "attempt_id": attempt.attempt_id,
                        "reason": verdict.reason,
                    }
                )
                continue

            if verdict.action is RecoveryAction.EVALUATE_ACCEPTANCE:
                self._evaluate_and_settle(
                    state,
                    task=task,
                    profile=profile,
                    attempt=attempt,
                    verifying=False,
                    verifier_agent_id=None,
                    result=CycleResult(cycle=0),
                )
                continue

            if verdict.action is RecoveryAction.SAFE_TO_LAUNCH:
                # No evidence the worker ever started AND the task declares its
                # effect repeatable. The attempt is discarded so the next cycle
                # dispatches a fresh one rather than reusing a half-built record.
                attempt.phase = AttemptPhase.TERMINAL
                attempt.confidence = ExecutionConfidence.FAILED
                attempt.failure_class = FailureClass.TRANSIENT_INFRASTRUCTURE
                attempt.notes = (*attempt.notes, "no launch evidence; safe to repeat")
                record = state.tasks[attempt.task_id]
                record.attempts = max(0, record.attempts - 1)
                # A crash in this window leaves the task LEASED: ownership is
                # granted before the intent is written, and the ACTIVE
                # transition happens after it. LEASED is already dispatchable
                # under the existing lease, so it is left alone. ACTIVE would
                # mean the crash landed later than the phase suggests, and
                # REMEDIATING routes it back through the ordinary retry path.
                if record.state is NodeState.ACTIVE:
                    self._transition(
                        state,
                        attempt.task_id,
                        NodeState.REMEDIATING,
                        reason="interrupted before launch; task declares repeatable effect",
                    )
                continue

            if verdict.action is RecoveryAction.RESUME_SESSION:
                blockers.append(
                    {
                        "task_id": attempt.task_id,
                        "attempt_id": attempt.attempt_id,
                        "reason": verdict.reason,
                        "resume_session_id": verdict.resume_session_id,
                        "note": (
                            "the recorded session can be continued; this is a "
                            "resume, not a second dispatch"
                        ),
                    }
                )
                continue

            blockers.append(
                {
                    "task_id": attempt.task_id,
                    "attempt_id": attempt.attempt_id,
                    "reason": verdict.reason,
                }
            )

        if blockers:
            self._notify(
                "RECONCILE_REQUIRED",
                (
                    "the previous supervisor left work whose outcome cannot be "
                    "determined automatically; nothing was redispatched"
                ),
                {"attempts": blockers},
            )
            return ProgramStopReason.RECONCILE_REQUIRED
        return None

    # ----------------------------------------------------------------- misc

    def _notify_unless_busy(
        self, kind: str, message: str, detail: dict[str, Any]
    ) -> None:
        """Raise a notification only if it is still true with workers in flight.

        Found by the systemwide acceptance run, not by reasoning: selection
        reached "no task is eligible and nothing is pending" while a Codex
        worker was mid-task, and said so out loud. The stop reason itself was
        already cleared as provisional a few lines later and the program went
        on to complete, so nothing behaved wrongly -- but the operator was told
        something false, and a notification that can be false is worse than no
        notification at all.
        """
        if self._running:
            return
        self._notify(kind, message, detail)

    def _notify(self, kind: str, message: str, detail: dict[str, Any]) -> None:
        """Record an operator-actionable notification.

        Notifications are separate from checkpoints on purpose. A checkpoint
        is written after every meaningful transition, including the entirely
        routine ones. A notification is raised only for something the operator
        would act on: a decision that is theirs, a material failure, safe work
        being exhausted, or the program finishing. Routine task completion
        produces an event and the next task, never a request for a fresh goal.
        """
        row = {"kind": kind, "message": message, "detail": detail}
        self._notifications.append(row)
        append_event(self.root, f"NOTIFY_{kind}", {"message": message, **detail})

    def _report(
        self,
        state: ProgramStateRecord,
        cycles: list[CycleResult],
        stop: ProgramStopReason,
    ) -> SupervisorReport:
        from project_atlas.orchestration.program.models import TRUTH_BOUNDARY

        return SupervisorReport(
            program_id=self.program.program_id,
            cycles=cycles,
            stop_reason=stop,
            complete=state.complete,
            launches=state.total_launches,
            launches_this_run=self._launches_this_run,
            estimated_cost_usd=state.estimated_cost_usd,
            max_concurrent_observed=self._max_concurrent_observed,
            notifications=list(self._notifications),
            truth_boundary=TRUTH_BOUNDARY,
        )


def _lease_id_for(
    leases: Mapping[str, AgentLease], task_id: str, *, verifying: bool
) -> str | None:
    """The lease id to stamp on an attempt.

    A verification run holds no lease: it does not mutate the surface, and
    recording the implementer's lease on it would misattribute ownership of a
    change the verifier never made.
    """
    if verifying:
        return None
    lease = leases.get(task_id)
    return lease.lease_id if lease is not None else None


def _apply_enrollments(
    loaded: LoadedProgram, agents: Sequence[EnrolledAgent]
) -> LoadedProgram:
    """Substitute enrolled agents' bound profiles for the roles they fill.

    What changes is the principal and any narrowing the enrollment carries.
    What does not change is where permissions come from: the approved
    program's profile for that role, narrowed. An enrollment cannot widen it,
    and `bind` refuses the attempt.

    The implementer-cannot-verify check is re-run afterwards, on the
    substituted `agent_id`s. Two profiles that looked independent while they
    were placeholders can resolve to one enrolled agent, and that is precisely
    the case a check performed only at load time would miss.
    """
    by_role: dict[str, EnrolledAgent] = {}
    for agent in agents:
        previous = by_role.get(agent.role)
        if previous is not None and previous.agent_id != agent.agent_id:
            raise SupervisorError(
                f"role {agent.role!r} is claimed by two enrolled agents "
                f"({previous.agent_id}, {agent.agent_id})",
                code="ROLE_CONTENTION",
            )
        by_role[agent.role] = agent

    effective = dict(loaded.effective)
    verifiers = dict(loaded.verifiers)
    for task in loaded.program.tasks:
        # Deliberately a new name: reusing the loop variable from the
        # role-contention loop above shadows a non-optional binding with an
        # optional one, which mypy catches and a reader would not.
        task_agent = by_role.get(task.profile_ref)
        if task_agent is not None:
            # The authorization is read from the agent's durable record, not
            # assumed. Passing True here unconditionally -- which an earlier
            # version did -- would have let an agent run a role written for a
            # different runtime at launch time even though `assign` refuses to
            # record that assignment without an explicit grant.
            bound = bind(
                task_agent,
                loaded,
                allow_runtime_substitution=task_agent.runtime_substitution_authorized,
            )
            # The task's own override is applied on top of the program profile
            # by the loader; re-applying the enrollment's narrowing over that
            # result would lose the task override, so both are layered here.
            base = loaded.effective[task.task_id]
            effective[task.task_id] = base.model_copy(
                update={
                    "agent_id": bound.agent_id,
                    "adapter": bound.adapter,
                    "permission_mode": _tighter(
                        base.permission_mode, bound.permission_mode
                    ),
                    "allowed_tools": tuple(
                        sorted(set(base.allowed_tools) & set(bound.allowed_tools))
                    )
                    if base.allowed_tools and bound.allowed_tools
                    else (bound.allowed_tools or base.allowed_tools),
                    "limits": _tighter_limits(base.limits, bound.limits),
                    "adapter_options": bound.adapter_options or base.adapter_options,
                }
            )
        verifier_agent: EnrolledAgent | None = (
            by_role.get(task.verifier_profile_ref)
            if task.verifier_profile_ref
            else None
        )
        if verifier_agent is not None and task.task_id in verifiers:
            # The verifier gets the SAME treatment as the implementer above.
            # Replacing only `agent_id` -- which an earlier version did -- left
            # a verifier running the program's permission mode, tools and
            # limits even where its enrollment had narrowed them, and let a
            # verifier profile name a runtime the agent held no substitution
            # grant for. Task configuration must not widen a registered
            # authority, and that rule is not implementer-only.
            bound_verifier = bind(
                verifier_agent,
                loaded,
                allow_runtime_substitution=verifier_agent.runtime_substitution_authorized,
            )
            base_verifier = verifiers[task.task_id]
            verifiers[task.task_id] = base_verifier.model_copy(
                update={
                    "agent_id": bound_verifier.agent_id,
                    "adapter": bound_verifier.adapter,
                    "permission_mode": _tighter(
                        base_verifier.permission_mode, bound_verifier.permission_mode
                    ),
                    "allowed_tools": tuple(
                        sorted(
                            set(base_verifier.allowed_tools)
                            & set(bound_verifier.allowed_tools)
                        )
                    )
                    if base_verifier.allowed_tools and bound_verifier.allowed_tools
                    else (bound_verifier.allowed_tools or base_verifier.allowed_tools),
                    "limits": _tighter_limits(base_verifier.limits, bound_verifier.limits),
                    "adapter_options": bound_verifier.adapter_options
                    or base_verifier.adapter_options,
                }
            )

    for task in loaded.program.tasks:
        verifier = verifiers.get(task.task_id)
        if verifier is None:
            continue
        if verifier.agent_id == effective[task.task_id].agent_id:
            raise SupervisorError(
                f"task {task.task_id}: after enrollment its implementer and "
                f"verifier are the same agent ({verifier.agent_id}); an agent "
                "cannot independently verify its own work",
                code="IMPLEMENTER_CANNOT_VERIFY",
            )

    return replace(loaded, effective=effective, verifiers=verifiers)


def _tighter(left: str, right: str) -> str:
    """The more restrictive of two permission modes."""
    from project_atlas.orchestration.program.profiles import PERMISSION_RANK

    return left if PERMISSION_RANK[left] <= PERMISSION_RANK[right] else right


def _tighter_limits(left: ProfileLimits, right: ProfileLimits) -> ProfileLimits:
    """The tighter of two limit sets, field by field.

    Taking the minimum rather than one side wholesale: an enrollment that says
    "this agent never runs longer than 60s" and a program that says "this task
    never runs longer than 300s" both mean it, and honouring only one of them
    would silently discard a bound somebody set on purpose.
    """
    costs = [
        value
        for value in (left.max_estimated_cost_usd, right.max_estimated_cost_usd)
        if value is not None
    ]
    return ProfileLimits(
        max_seconds=min(left.max_seconds, right.max_seconds),
        max_attempts=min(left.max_attempts, right.max_attempts),
        max_estimated_cost_usd=min(costs) if costs else None,
    )


def _verification_instruction(task: ProgramTask) -> str:
    """The prompt an independent verifier receives.

    Deliberately does not include the implementer's own account of what it
    did. A verifier that starts from the implementer's story is checking the
    story, not the work.
    """
    conditions = "\n".join(
        f"  - [{check.kind.value}] {check.description}" for check in task.acceptance
    )
    return (
        "You are an independent verifier. Another agent claims to have "
        f"completed this task:\n\n  {task.title}\n\n"
        "Check the working tree against these acceptance conditions and "
        "report what you actually observe. Do not fix anything, do not "
        "modify the repository, and do not take the previous agent's word "
        f"for any of it.\n\nAcceptance conditions:\n{conditions}\n"
    )


def _now_iso() -> str:
    from project_atlas.orchestration.program.store import _utc_now

    return _utc_now()


def _self_pid() -> int:
    import os

    return os.getpid()


def _supervisor_alive(state: ProgramStateRecord) -> bool:
    from project_atlas.orchestration.sdk.host import pid_is_alive

    pid = state.supervisor_pid
    return bool(pid and pid > 0 and pid_is_alive(pid))


def _current_branch(workspace: Path) -> str:
    """Best-effort branch name for the lease record.

    A lease needs a non-empty branch string. When the workspace is not a git
    checkout, or is detached, the answer is recorded as such rather than
    fabricated -- a lease that claims a branch that does not exist is worse
    than one that says it could not tell.
    """
    import subprocess

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"
    name = (completed.stdout or "").strip()
    if completed.returncode != 0 or not name:
        return "UNKNOWN"
    return name[:256]
