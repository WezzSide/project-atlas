"""AS-ORCH-PROGRAM-SUPERVISOR-001 M4 — concurrent agents.

Concurrency is added on top of the ownership and authorization mechanisms that
were already there, not alongside them. Every test here is a claim about
something that must remain true when more than one worker runs at once:

  * two workers only ever run on genuinely disjoint surfaces
  * one enrolled agent is one worker, never a pool
  * a task in flight is never selected a second time
  * program completion is never declared while a worker is running
  * a finished task releases ownership and unblocks its dependants
  * owner gates and independent-verifier separation are unaffected

The workers are labelled FIXTURES. `FIXTURE_RUN != REAL_RUNTIME_COMPATIBILITY`.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from project_atlas.orchestration.autonomy.lease_projection import (
    active_rows,
    load_projection,
)
from project_atlas.orchestration.autonomy.models import NodeState, OwnerGateKind
from project_atlas.orchestration.program.adapters.base import (
    AdapterCapabilities,
    AdapterOutcome,
    AdapterRequest,
)
from project_atlas.orchestration.program.loader import load_program
from project_atlas.orchestration.program.models import (
    ExecutionConfidence,
    ProgramStopReason,
)
from project_atlas.orchestration.program.profiles import AgentProfile
from project_atlas.orchestration.program.store import (
    load_state,
    read_events,
    state_dir,
)
from project_atlas.orchestration.program.supervisor import ProgramSupervisor

FIXTURE_WORKER = Path(__file__).with_name("_program_fixture_worker.py")


class _ObservingAdapter:
    """Records how many workers were in flight at the same instant.

    A supervisor that permits four workers and never runs more than one has
    not demonstrated concurrency. This adapter measures the overlap directly
    rather than inferring it from timing.
    """

    def __init__(
        self,
        *,
        hold_seconds: float = 0.35,
        barrier: threading.Barrier | None = None,
    ) -> None:
        self._hold = hold_seconds
        #: When set, each worker waits here instead of sleeping. Two workers
        #: that both reach `run()` release each other immediately and the
        #: overlap is a fact rather than a race; a worker that arrives alone
        #: blocks until the barrier's own timeout and reports it. See
        #: `test_two_agents_on_disjoint_surfaces_run_at_the_same_time`.
        self._barrier = barrier
        self._lock = threading.Lock()
        self.in_flight = 0
        self.peak = 0
        self.overlaps: list[tuple[str, ...]] = []
        self.started: list[str] = []
        self.active_tasks: set[str] = set()
        self.cancelled: list[str] = []
        self.barrier_broken = False

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            adapter_id="observing",
            supports_resume=False,
            supports_session_probe=True,
            accepts_assigned_session=True,
            supports_cost_limit=False,
            reports_cost=False,
            supports_result_schema=False,
            version="FIXTURE",
        )

    def preflight(self, profile: AgentProfile) -> None:
        _ = profile

    def probe_run_started(self, request: AdapterRequest) -> bool | None:
        _ = request
        return None

    def run(self, request: AdapterRequest) -> AdapterOutcome:
        with self._lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
            self.started.append(request.task_id)
            self.active_tasks.add(request.task_id)
            self.overlaps.append(tuple(sorted(self.active_tasks)))
        try:
            if self._barrier is not None:
                try:
                    self._barrier.wait()
                except threading.BrokenBarrierError:
                    with self._lock:
                        self.barrier_broken = True
                (request.workspace / f"{request.task_id}.txt").write_text(
                    "done\n", encoding="utf-8"
                )
                return self._outcome(
                    request, ExecutionConfidence.CONFIRMED, "completed"
                )
            deadline = time.monotonic() + self._hold
            while time.monotonic() < deadline:
                if request.cancel_requested is not None and request.cancel_requested():
                    with self._lock:
                        self.cancelled.append(request.task_id)
                    return self._outcome(
                        request, ExecutionConfidence.UNCERTAIN, "cancelled"
                    )
                time.sleep(0.02)
            (request.workspace / f"{request.task_id}.txt").write_text(
                "done\n", encoding="utf-8"
            )
            return self._outcome(request, ExecutionConfidence.CONFIRMED, "completed")
        finally:
            with self._lock:
                self.in_flight -= 1
                self.active_tasks.discard(request.task_id)

    def _outcome(
        self,
        request: AdapterRequest,
        confidence: ExecutionConfidence,
        terminal: str,
    ) -> AdapterOutcome:
        from project_atlas.orchestration.program.models import FailureClass

        return AdapterOutcome(
            attempt_id=request.attempt_id,
            task_id=request.task_id,
            launched=True,
            confidence=confidence,
            terminal_state=terminal,
            exit_status=0 if confidence is ExecutionConfidence.CONFIRMED else None,
            session_id=request.session_id,
            pid=None,
            process_start_identity=None,
            reported="fixture done",
            structured=None,
            usage={},
            estimated_cost_usd=None,
            evidence=(),
            failure_class=(
                None
                if confidence is ExecutionConfidence.CONFIRMED
                else FailureClass.UNCERTAIN_OUTCOME
            ),
            duration_seconds=0.0,
            notes=("FIXTURE",),
        )


def _profile(agent_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "agent_id": agent_id,
        "adapter": "local-command",
        "credential": "NOT_APPLICABLE",
        "capabilities": ["IMPLEMENT"],
        "adapter_options": {"argv": [sys.executable, str(FIXTURE_WORKER)]},
    }
    body.update(overrides)
    return body


def _task(
    task_id: str,
    *,
    profile_ref: str = "impl",
    depends_on: tuple[str, ...] = (),
    paths: tuple[str, ...] | None = None,
    surface: str | None = None,
    owner_gate: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "task_id": task_id,
        "title": f"task {task_id}",
        "instruction": "do it",
        "profile_ref": profile_ref,
        "depends_on": list(depends_on),
        "mutation_paths": list(paths or (f"{task_id}.txt",)),
        "surface_id": surface or task_id,
        "surface_semantic": (surface or task_id).upper().replace("-", "_"),
        "capabilities_required": ["IMPLEMENT"],
        "acceptance": [
            {
                "check_id": f"{task_id}-out",
                "kind": "FILE_EXISTS",
                "description": f"{task_id}.txt exists",
                "path": f"{task_id}.txt",
            }
        ],
    }
    if owner_gate:
        body["owner_gate"] = owner_gate
    return body


def _program(
    tmp_path: Path,
    workspace: Path,
    *,
    tasks: list[dict[str, Any]],
    profiles: dict[str, dict[str, Any]] | None = None,
    concurrency: int = 2,
    limits: dict[str, Any] | None = None,
) -> Path:
    base_limits = {
        "max_cycles": 20,
        "idle_sleep_seconds": 0.0,
        "max_concurrent_workers": concurrency,
    }
    base_limits.update(limits or {})
    payload = {
        "schema_version": 1,
        "program": {
            "program_id": "concurrency-program",
            "objective": "concurrency coverage",
            "approved_by": "test",
            "approval_reference": "test",
            "workspace_root": str(workspace),
            "base_pin": "0" * 40,
            "limits": base_limits,
            "tasks": tasks,
        },
        "profiles": profiles or {"impl": _profile("agent-one")},
    }
    path = tmp_path / "program.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _supervisor(
    tmp_path: Path,
    program: Path,
    adapters: dict[str, Any],
) -> ProgramSupervisor:
    return ProgramSupervisor(
        load_program(program),
        state_root=tmp_path / "state",
        adapters=adapters,
        sleeper=lambda _s: None,
    )


# ------------------------------------------------------- genuine concurrency


def test_two_agents_on_disjoint_surfaces_run_at_the_same_time(
    tmp_path: Path,
) -> None:
    """Two independent agents must genuinely be in flight together.

    Asserted with a barrier rather than a sleep. An earlier version had each
    worker hold a fixed 0.35s and asserted the observed peak was 2; that is a
    race, and Windows CI won it. Between the two dispatches the supervisor does
    three durable writes -- the lease projection under its lock, the state
    file, and an fsynced event append -- which on Linux cost 18.5ms against a
    350ms hold (a 19x margin) and on a Windows runner can cost more than the
    hold itself. The first worker then finished before the second started, and
    the test failed while the supervisor had behaved correctly.

    The barrier removes the timing question entirely: two workers that both
    reach `run()` release each other, so the overlap is a fact. A worker that
    arrives alone blocks until the barrier times out and says so. It cannot
    mask a genuine failure to run concurrently -- that case still fails, and
    fails for the right reason.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(
        tmp_path,
        workspace,
        tasks=[_task("alpha", profile_ref="a"), _task("beta", profile_ref="b")],
        profiles={"a": _profile("agent-a"), "b": _profile("agent-b")},
        concurrency=2,
    )
    barrier = threading.Barrier(2, timeout=60)
    adapter = _ObservingAdapter(barrier=barrier)
    supervisor = _supervisor(tmp_path, program, {"a": adapter, "b": adapter})
    report = supervisor.start()

    assert report.stop_reason is ProgramStopReason.PROGRAM_COMPLETE
    # Supervisor-side first, and deliberately: `max_concurrent_observed` is
    # counted when the second worker is submitted while the first is still in
    # `_running`, so it is timing-independent and answers "did the supervisor
    # dispatch two at once" on its own.
    assert report.max_concurrent_observed == 2
    # Worker-side second: did two workers actually execute together.
    assert not adapter.barrier_broken, (
        "a worker reached run() alone: the second was never dispatched "
        "concurrently, or the first had already finished"
    )
    assert adapter.peak == 2, "two independent agents must overlap"
    assert sorted(adapter.started) == ["alpha", "beta"]


def test_one_agent_is_one_worker_not_a_pool(tmp_path: Path) -> None:
    """Two tasks, one agent, capacity for two: they still serialise.

    An enrolled agent is a single worker. The durable lease projection refuses
    a second active lease for one agent_id, and selection filters the agent out
    while it is busy so that refusal is never reached in the normal case.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(
        tmp_path,
        workspace,
        tasks=[_task("alpha"), _task("beta")],
        profiles={"impl": _profile("only-agent")},
        concurrency=2,
    )
    adapter = _ObservingAdapter()
    supervisor = _supervisor(tmp_path, program, {"impl": adapter})
    report = supervisor.start()

    assert report.stop_reason is ProgramStopReason.PROGRAM_COMPLETE
    assert adapter.peak == 1, "one agent must never have two workers in flight"
    assert report.max_concurrent_observed == 1
    assert sorted(adapter.started) == ["alpha", "beta"]


def test_overlapping_surfaces_never_run_concurrently(tmp_path: Path) -> None:
    """Different agents, capacity for two, one shared mutation path."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(
        tmp_path,
        workspace,
        tasks=[
            _task("alpha", profile_ref="a", paths=("alpha.txt", "shared.txt")),
            _task("beta", profile_ref="b", paths=("beta.txt", "shared.txt")),
        ],
        profiles={"a": _profile("agent-a"), "b": _profile("agent-b")},
        concurrency=2,
    )
    adapter = _ObservingAdapter()
    supervisor = _supervisor(tmp_path, program, {"a": adapter, "b": adapter})
    report = supervisor.start()

    assert report.stop_reason is ProgramStopReason.PROGRAM_COMPLETE
    assert adapter.peak == 1, "the surface-overlap gate must hold under concurrency"
    for snapshot in adapter.overlaps:
        assert len(snapshot) == 1, snapshot


def test_a_task_in_flight_is_never_dispatched_twice(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(
        tmp_path,
        workspace,
        tasks=[
            _task("alpha", profile_ref="a"),
            _task("beta", profile_ref="b"),
            _task("gamma", profile_ref="c"),
        ],
        profiles={
            "a": _profile("agent-a"),
            "b": _profile("agent-b"),
            "c": _profile("agent-c"),
        },
        concurrency=3,
    )
    adapter = _ObservingAdapter()
    supervisor = _supervisor(
        tmp_path, program, {"a": adapter, "b": adapter, "c": adapter}
    )
    report = supervisor.start()

    assert report.stop_reason is ProgramStopReason.PROGRAM_COMPLETE
    intents = [
        row for row in read_events(tmp_path / "state") if row.get("event") == "DISPATCH_INTENT"
    ]
    per_task: dict[str, int] = {}
    for row in intents:
        per_task[str(row["task_id"])] = per_task.get(str(row["task_id"]), 0) + 1
    assert per_task == {"alpha": 1, "beta": 1, "gamma": 1}, per_task
    assert sorted(adapter.started) == ["alpha", "beta", "gamma"]


def test_program_completion_is_never_declared_while_a_worker_runs(
    tmp_path: Path,
) -> None:
    """The PROGRAM_COMPLETE notification must come after the last settle."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(
        tmp_path,
        workspace,
        tasks=[_task("alpha", profile_ref="a"), _task("beta", profile_ref="b")],
        profiles={"a": _profile("agent-a"), "b": _profile("agent-b")},
        concurrency=2,
    )
    adapter = _ObservingAdapter()
    supervisor = _supervisor(tmp_path, program, {"a": adapter, "b": adapter})
    report = supervisor.start()
    assert report.complete is True

    events = read_events(tmp_path / "state")
    names = [str(row.get("event")) for row in events]
    complete_at = names.index("NOTIFY_PROGRAM_COMPLETE")
    accepted = [i for i, name in enumerate(names) if name == "TASK_ACCEPTED"]
    assert len(accepted) == 2
    assert max(accepted) < complete_at, (
        "PROGRAM_COMPLETE was raised before the last task was accepted"
    )


def test_a_finished_task_releases_ownership_and_unblocks_its_dependant(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(
        tmp_path,
        workspace,
        tasks=[
            _task("alpha", profile_ref="a"),
            _task("beta", profile_ref="b", depends_on=("alpha",)),
        ],
        profiles={"a": _profile("agent-a"), "b": _profile("agent-b")},
        concurrency=2,
    )
    adapter = _ObservingAdapter()
    supervisor = _supervisor(tmp_path, program, {"a": adapter, "b": adapter})
    report = supervisor.start()

    assert report.stop_reason is ProgramStopReason.PROGRAM_COMPLETE
    assert adapter.started == ["alpha", "beta"], "the dependant must start second"
    assert adapter.peak == 1, "a dependant cannot overlap its dependency"

    # Ownership was handed back, not merely forgotten.
    projection = load_projection(state_dir(tmp_path / "state"))
    assert active_rows(projection) == ()
    assert {row.status for row in projection.leases} == {"RELEASED"}


def test_owner_gated_work_stays_blocked_even_with_free_capacity(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(
        tmp_path,
        workspace,
        tasks=[
            _task("open", profile_ref="a"),
            _task(
                "gated",
                profile_ref="b",
                owner_gate=OwnerGateKind.C_CERTIFIED_OBJECT_MUTATION.value,
            ),
        ],
        profiles={"a": _profile("agent-a"), "b": _profile("agent-b")},
        concurrency=4,
    )
    adapter = _ObservingAdapter()
    supervisor = _supervisor(tmp_path, program, {"a": adapter, "b": adapter})
    report = supervisor.start()

    assert adapter.started == ["open"]
    assert report.stop_reason is ProgramStopReason.OWNER_DECISION_REQUIRED
    assert report.complete is False
    state = load_state(tmp_path / "state")
    assert state is not None
    assert state.tasks["gated"].state is NodeState.READY


def test_cancelling_stops_every_running_worker_and_records_uncertainty(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(
        tmp_path,
        workspace,
        tasks=[_task("alpha", profile_ref="a"), _task("beta", profile_ref="b")],
        profiles={"a": _profile("agent-a"), "b": _profile("agent-b")},
        concurrency=2,
    )
    adapter = _ObservingAdapter(hold_seconds=5.0)
    supervisor = _supervisor(tmp_path, program, {"a": adapter, "b": adapter})

    original = supervisor._begin_dispatch
    launched: list[str] = []

    def cancel_once_both_are_up(state: Any, choice: Any, result: Any) -> Any:
        running = original(state, choice, result)
        if running is not None:
            launched.append(running.task_id)
            if len(launched) == 2:
                state.cancel_requested = True
        return running

    supervisor._begin_dispatch = cancel_once_both_are_up  # type: ignore[method-assign]
    report = supervisor.start()

    assert report.stop_reason is ProgramStopReason.CANCELLED
    assert sorted(launched) == ["alpha", "beta"]
    state = load_state(tmp_path / "state")
    assert state is not None
    assert len(state.attempts) == 2
    for attempt in state.attempts.values():
        assert attempt.confidence is ExecutionConfidence.UNCERTAIN
        assert any("UNKNOWN" in note for note in attempt.notes)


def test_the_launch_limit_bounds_concurrent_dispatch_too(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(
        tmp_path,
        workspace,
        tasks=[
            _task("alpha", profile_ref="a"),
            _task("beta", profile_ref="b"),
            _task("gamma", profile_ref="c"),
        ],
        profiles={
            "a": _profile("agent-a"),
            "b": _profile("agent-b"),
            "c": _profile("agent-c"),
        },
        concurrency=3,
        limits={"max_task_launches": 2},
    )
    adapter = _ObservingAdapter()
    supervisor = _supervisor(
        tmp_path, program, {"a": adapter, "b": adapter, "c": adapter}
    )
    report = supervisor.start()

    assert report.launches == 2
    assert len(adapter.started) == 2
    assert report.stop_reason is ProgramStopReason.LIMIT_REACHED
    assert report.complete is False


def test_sequential_remains_the_default(tmp_path: Path) -> None:
    """A program that did not ask for concurrency does not get it."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    payload = json.loads(
        _program(
            tmp_path,
            workspace,
            tasks=[_task("alpha", profile_ref="a"), _task("beta", profile_ref="b")],
            profiles={"a": _profile("agent-a"), "b": _profile("agent-b")},
        ).read_text(encoding="utf-8")
    )
    del payload["program"]["limits"]["max_concurrent_workers"]
    program = tmp_path / "sequential.json"
    program.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    loaded = load_program(program)
    assert loaded.program.limits.max_concurrent_workers == 1

    adapter = _ObservingAdapter()
    supervisor = ProgramSupervisor(
        loaded,
        state_root=tmp_path / "state",
        adapters={"a": adapter, "b": adapter},
        sleeper=lambda _s: None,
    )
    report = supervisor.start()
    assert report.stop_reason is ProgramStopReason.PROGRAM_COMPLETE
    assert adapter.peak == 1
    assert report.max_concurrent_observed == 1


@pytest.mark.parametrize("concurrency", [2, 3, 4])
def test_no_duplicate_dispatch_at_any_concurrency(
    tmp_path: Path, concurrency: int
) -> None:
    """The invariant must not depend on how many slots happen to be free."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    names = ["alpha", "beta", "gamma", "delta"]
    program = _program(
        tmp_path,
        workspace,
        tasks=[_task(name, profile_ref=name[0]) for name in names],
        profiles={name[0]: _profile(f"agent-{name}") for name in names},
        concurrency=concurrency,
    )
    adapter = _ObservingAdapter(hold_seconds=0.1)
    supervisor = _supervisor(
        tmp_path, program, {name[0]: adapter for name in names}
    )
    report = supervisor.start()

    assert report.stop_reason is ProgramStopReason.PROGRAM_COMPLETE
    assert sorted(adapter.started) == sorted(names)
    assert len(adapter.started) == len(set(adapter.started))
    assert report.max_concurrent_observed <= concurrency


def test_no_false_idle_notification_while_a_worker_is_running(
    tmp_path: Path,
) -> None:
    """Regression from the systemwide acceptance run.

    Selection reached "no task is eligible and nothing is pending" while a
    worker was mid-task and said so out loud. The stop reason was already
    treated as provisional and the program completed correctly, so nothing
    behaved wrongly -- but the operator was told something false.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(
        tmp_path,
        workspace,
        tasks=[
            _task("fast", profile_ref="a"),
            _task("slow", profile_ref="b"),
            _task("after-slow", profile_ref="b", depends_on=("slow",)),
        ],
        profiles={"a": _profile("agent-a"), "b": _profile("agent-b")},
        concurrency=2,
    )
    adapter = _ObservingAdapter(hold_seconds=0.2)
    supervisor = _supervisor(tmp_path, program, {"a": adapter, "b": adapter})
    report = supervisor.start()

    assert report.stop_reason is ProgramStopReason.PROGRAM_COMPLETE
    events = read_events(tmp_path / "state")
    idle_indices = [
        i for i, row in enumerate(events) if row.get("event") == "NOTIFY_NO_ELIGIBLE_WORK"
    ]
    accepted = [
        i for i, row in enumerate(events) if row.get("event") == "TASK_ACCEPTED"
    ]
    for idle in idle_indices:
        assert idle > max(accepted), (
            "an idle notification was raised before the last task was accepted, "
            "which means it fired while work was still in flight"
        )
