"""ATLAS-SUPERVISOR-OPERATIONAL-INTEGRATION-002 — lifecycle recovery.

The seven properties an operator has to be able to rely on, each asserted
against the real mechanisms rather than a mock of them: the existing DAG, the
durable lease projection, the singleton host lock, and the enrolment roster.

Workers are labelled FIXTURES throughout. FIXTURE_RUN != REAL_RUNTIME_COMPATIBILITY.
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
from project_atlas.orchestration.autonomy.models import NodeState
from project_atlas.orchestration.program import control
from project_atlas.orchestration.program.adapters.base import (
    AdapterCapabilities,
    AdapterOutcome,
    AdapterRequest,
)
from project_atlas.orchestration.program.enrollment import (
    AgentStatus,
    assign,
    enroll,
    set_status,
)
from project_atlas.orchestration.program.loader import load_program
from project_atlas.orchestration.program.models import (
    AttemptPhase,
    ExecutionConfidence,
    FailureClass,
    ProgramStopReason,
)
from project_atlas.orchestration.program.profiles import AdapterKind, AgentProfile
from project_atlas.orchestration.program.store import (
    AttemptRecord,
    load_state,
    persist_state,
    read_events,
    state_dir,
)
from project_atlas.orchestration.program.supervisor import (
    ProgramSupervisor,
    SupervisorError,
)

FIXTURE_WORKER = Path(__file__).with_name("_program_fixture_worker.py")


class _SlowAdapter:
    """A worker that holds its slot long enough for overlap to be observable."""

    def __init__(self, *, hold: float = 0.3) -> None:
        self._hold = hold
        self._lock = threading.Lock()
        self.in_flight = 0
        self.peak = 0
        self.started: list[str] = []
        self.gate: threading.Event | None = None

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            adapter_id="slow-fixture",
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
        try:
            if self.gate is not None:
                self.gate.wait(timeout=30)
            else:
                time.sleep(self._hold)
            (request.workspace / f"{request.task_id}.txt").write_text(
                "done\n", encoding="utf-8"
            )
            return AdapterOutcome(
                attempt_id=request.attempt_id,
                task_id=request.task_id,
                launched=True,
                confidence=ExecutionConfidence.CONFIRMED,
                terminal_state="completed",
                exit_status=0,
                session_id=request.session_id,
                pid=None,
                process_start_identity=None,
                reported="fixture done",
                structured=None,
                usage={},
                estimated_cost_usd=None,
                evidence=(),
                failure_class=None,
                duration_seconds=0.0,
                notes=("FIXTURE",),
            )
        finally:
            with self._lock:
                self.in_flight -= 1


def _profile(agent_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "agent_id": agent_id,
        "adapter": "local-command",
        "credential": "NOT_APPLICABLE",
        "capabilities": ["IMPLEMENT"],
        "env_allowlist": [
            "ATLAS_FIXTURE_MODE",
            "ATLAS_PROGRAM_ATTEMPT",
            "ATLAS_PROGRAM_TASK",
        ],
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
) -> dict[str, Any]:
    return {
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


def _program(
    tmp_path: Path,
    workspace: Path,
    *,
    tasks: list[dict[str, Any]],
    profiles: dict[str, dict[str, Any]] | None = None,
    concurrency: int = 2,
    name: str = "program.json",
) -> Path:
    payload = {
        "schema_version": 1,
        "program": {
            "program_id": "lifecycle-program",
            "objective": "lifecycle recovery coverage",
            "approved_by": "wesley",
            "approval_reference": "docs/orchestration/program/OPERATOR-JOURNEY.md",
            "workspace_root": str(workspace),
            "base_pin": "0" * 40,
            "limits": {
                "max_cycles": 20,
                "idle_sleep_seconds": 0.0,
                "max_concurrent_workers": concurrency,
            },
            "tasks": tasks,
        },
        "profiles": profiles or {"impl": _profile("agent-one")},
    }
    path = tmp_path / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _fixture_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_FIXTURE_MODE", "write")
    monkeypatch.delenv("ATLAS_FIXTURE_TARGET", raising=False)


# (1) tasks continue without operator prompts between successful checkpoints


def test_a_chain_of_tasks_needs_no_prompt_between_checkpoints(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(
        tmp_path,
        workspace,
        tasks=[
            _task("one"),
            _task("two", depends_on=("one",)),
            _task("three", depends_on=("two",)),
        ],
        concurrency=1,
    )
    report = ProgramSupervisor(
        load_program(program), state_root=tmp_path / "state", sleeper=lambda _s: None
    ).start()

    assert report.stop_reason is ProgramStopReason.PROGRAM_COMPLETE
    dispatched = [
        row["task_id"] for row in report.to_public_dict()["tasks_dispatched"]
    ]
    assert dispatched == ["one", "two", "three"]

    # A checkpoint notification must not be a request for permission. The only
    # notification a clean run produces is the completion one.
    kinds = [row["kind"] for row in report.notifications]
    assert kinds == ["PROGRAM_COMPLETE"], kinds


# (2) conflicting work surfaces cannot execute concurrently


def test_conflicting_surfaces_never_execute_concurrently(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(
        tmp_path,
        workspace,
        tasks=[
            _task("alpha", profile_ref="a", paths=("alpha.txt", "contested.txt")),
            _task("beta", profile_ref="b", paths=("beta.txt", "contested.txt")),
        ],
        profiles={"a": _profile("agent-a"), "b": _profile("agent-b")},
        concurrency=2,
    )
    adapter = _SlowAdapter()
    report = ProgramSupervisor(
        load_program(program),
        state_root=tmp_path / "state",
        adapters={"a": adapter, "b": adapter},
        sleeper=lambda _s: None,
    ).start()

    assert report.stop_reason is ProgramStopReason.PROGRAM_COMPLETE
    assert adapter.peak == 1, "a shared mutation path must serialise the two"
    # And at most one lease was ever active over that surface.
    projection = load_projection(state_dir(tmp_path / "state"))
    assert active_rows(projection) == ()


# (3) pause prevents new dispatch and clearly reports already-running work


def test_pause_withholds_dispatch_and_names_what_is_still_running(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(
        tmp_path,
        workspace,
        tasks=[_task("running"), _task("queued", profile_ref="b")],
        profiles={"impl": _profile("agent-a"), "b": _profile("agent-b")},
        concurrency=1,
    )
    loaded = load_program(program)
    root = tmp_path / "state"
    supervisor = ProgramSupervisor(loaded, state_root=root, sleeper=lambda _s: None)
    state = supervisor.load_or_init_state()

    # Put a task in flight the way a real interruption would leave it.
    state.tasks["running"].state = NodeState.ACTIVE
    state.tasks["running"].last_attempt_id = "att-1"
    state.attempts["att-1"] = AttemptRecord(
        attempt_id="att-1",
        task_id="running",
        attempt_number=1,
        idempotency_key="k",
        profile_id="impl",
        agent_id="agent-a",
        adapter="local-command",
        profile_digest="a" * 64,
        base_pin=state.base_pin,
        phase=AttemptPhase.ADAPTER_INVOKED,
    )
    persist_state(root, state)

    paused = control.pause(root, requested_by="wesley")
    assert paused["paused"] is True
    assert [row["task_id"] for row in paused["still_running"]] == ["running"]
    assert paused["still_running"][0]["agent_id"] == "agent-a"
    assert "are NOT interrupted" in paused["still_running_note"]

    # Starting now reports RECONCILE_REQUIRED, not PAUSED -- and that ordering
    # is correct rather than a gap: this synthetic setup left a worker whose
    # outcome is unknown, and an unknown effect outranks a pause. Both facts
    # are true; the supervisor surfaces the one that needs a decision. What
    # matters for the pause claim is that nothing was launched.
    report = ProgramSupervisor(loaded, state_root=root, sleeper=lambda _s: None).start()
    assert report.stop_reason is ProgramStopReason.RECONCILE_REQUIRED
    assert report.launches_this_run == 0


def test_pause_withholds_dispatch_on_an_otherwise_healthy_program(
    tmp_path: Path,
) -> None:
    """The pause claim on its own, with nothing else competing to be reported."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(
        tmp_path, workspace, tasks=[_task("one"), _task("two")], concurrency=1
    )
    loaded = load_program(program)
    root = tmp_path / "state"
    ProgramSupervisor(loaded, state_root=root).load_or_init_state()

    paused = control.pause(root, requested_by="wesley")
    assert paused["still_running"] == [], "nothing was in flight"
    assert paused["reversible"] is True

    report = ProgramSupervisor(loaded, state_root=root, sleeper=lambda _s: None).start()
    assert report.stop_reason is ProgramStopReason.PAUSED
    assert report.launches_this_run == 0
    assert load_state(root).attempts == {}  # type: ignore[union-attr]

    control.resume(root, requested_by="wesley")
    resumed = ProgramSupervisor(
        loaded, state_root=root, sleeper=lambda _s: None
    ).start()
    assert resumed.stop_reason is ProgramStopReason.PROGRAM_COMPLETE
    assert resumed.launches_this_run == 2


# (4) restart reconciles existing workers and leases before dispatch


def test_restart_reconciles_leases_before_it_dispatches_anything(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(
        tmp_path, workspace, tasks=[_task("one"), _task("two")], concurrency=1
    )
    loaded = load_program(program)
    root = tmp_path / "state"

    # Leave a real lease and an in-flight attempt behind, as a crash would.
    first = ProgramSupervisor(loaded, state_root=root, sleeper=lambda _s: None)
    state = first.load_or_init_state()
    first._acquire()
    try:
        first._transition(state, "one", NodeState.READY, reason="setup")
        lease = first._ensure_lease(
            state, loaded.program.task("one"), loaded.effective_profile("one")
        )
        state.tasks["one"].last_attempt_id = "att-1"
        state.attempts["att-1"] = AttemptRecord(
            attempt_id="att-1",
            task_id="one",
            attempt_number=1,
            idempotency_key="k",
            profile_id="impl",
            agent_id="agent-one",
            adapter="local-command",
            profile_digest="a" * 64,
            base_pin=state.base_pin,
            lease_id=lease.lease_id,
            phase=AttemptPhase.ADAPTER_INVOKED,
        )
        persist_state(root, state)
    finally:
        first._release()

    successor = ProgramSupervisor(loaded, state_root=root, sleeper=lambda _s: None)
    report = successor.start()

    # Reconciliation happened, and it happened BEFORE anything was dispatched.
    events = [str(row.get("event")) for row in read_events(root)]
    assert "RESTART_RECONCILIATION" in events
    if "DISPATCH_INTENT" in events:
        assert events.index("RESTART_RECONCILIATION") < events.index("DISPATCH_INTENT")
    assert report.launches_this_run == 0
    assert report.stop_reason is ProgramStopReason.RECONCILE_REQUIRED

    # The lease survived the restart rather than being silently re-granted.
    rows = active_rows(load_projection(state_dir(root)))
    assert [row.package_id for row in rows] == ["one"]
    assert rows[0].lease_id == lease.lease_id


# (5) a second supervisor cannot take over an active program


def test_a_second_supervisor_cannot_take_over(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace, tasks=[_task("one")], concurrency=1)
    loaded = load_program(program)
    root = tmp_path / "state"

    holder = ProgramSupervisor(loaded, state_root=root, sleeper=lambda _s: None)
    holder._acquire()
    try:
        intruder = ProgramSupervisor(loaded, state_root=root, sleeper=lambda _s: None)
        with pytest.raises(SupervisorError) as excinfo:
            intruder.start()
        assert excinfo.value.code == "SUPERVISOR_DOUBLE_START"
        # Nothing was dispatched by the intruder.
        state = load_state(root)
        assert state is None or not state.attempts
    finally:
        holder._release()

    # And once released, a successor may proceed.
    after = ProgramSupervisor(loaded, state_root=root, sleeper=lambda _s: None).start()
    assert after.stop_reason is ProgramStopReason.PROGRAM_COMPLETE


# (6) ambiguous execution remains blocked from automatic replay


def test_an_ambiguous_outcome_is_never_replayed_automatically(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace, tasks=[_task("one")], concurrency=1)
    loaded = load_program(program)
    root = tmp_path / "state"

    supervisor = ProgramSupervisor(loaded, state_root=root, sleeper=lambda _s: None)
    state = supervisor.load_or_init_state()
    state.tasks["one"].state = NodeState.ACTIVE
    state.tasks["one"].attempts = 1
    state.tasks["one"].last_attempt_id = "att-1"
    state.attempts["att-1"] = AttemptRecord(
        attempt_id="att-1",
        task_id="one",
        attempt_number=1,
        idempotency_key="k",
        profile_id="impl",
        agent_id="agent-one",
        adapter="local-command",
        profile_digest="a" * 64,
        base_pin=state.base_pin,
        phase=AttemptPhase.ADAPTER_RETURNED,
        confidence=ExecutionConfidence.UNCERTAIN,
        failure_class=FailureClass.UNCERTAIN_OUTCOME,
    )
    persist_state(root, state)

    for _ in range(3):
        report = ProgramSupervisor(
            loaded, state_root=root, sleeper=lambda _s: None
        ).start()
        assert report.stop_reason is ProgramStopReason.RECONCILE_REQUIRED
        assert report.launches_this_run == 0

    # Still exactly one attempt: nothing was replayed on any of the three starts.
    state = load_state(root)
    assert state is not None
    assert len(state.attempts) == 1
    assert state.total_launches == 0


# (7) revoked or expired authority is checked before subsequent dispatch


def _enrolled_program(tmp_path: Path, workspace: Path) -> tuple[Path, Path, Any]:
    program = _program(
        tmp_path,
        workspace,
        tasks=[_task("one"), _task("two", depends_on=("one",))],
        profiles={"impl": _profile("placeholder")},
        concurrency=1,
    )
    registry = tmp_path / "registry"
    enroll(
        registry,
        agent_id="worker-a",
        role="impl",
        adapter=AdapterKind.LOCAL_COMMAND,
        workspace_root=workspace,
        enrolled_by="wesley",
    )
    agent, _loaded = assign(
        registry, agent_id="worker-a", program_path=program, assigned_by="wesley"
    )
    return program, registry, agent


def test_suspending_an_agent_stops_the_next_dispatch(tmp_path: Path) -> None:
    """Authority is re-read before the launch, not cached at start-up."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program, registry, agent = _enrolled_program(tmp_path, workspace)
    loaded = load_program(program)
    root = tmp_path / "state"

    supervisor = ProgramSupervisor(
        loaded,
        state_root=root,
        enrolled_agents=(agent,),
        registry_root=registry,
        sleeper=lambda _s: None,
    )
    original = supervisor._begin_dispatch
    suspended_after: list[str] = []

    def suspend_after_first(state: Any, choice: Any, result: Any) -> Any:
        running = original(state, choice, result)
        if running is not None and not suspended_after:
            suspended_after.append(running.task_id)
            set_status(registry, agent_id="worker-a", status=AgentStatus.SUSPENDED)
        return running

    supervisor._begin_dispatch = suspend_after_first  # type: ignore[method-assign]
    report = supervisor.start()

    assert suspended_after == ["one"]
    assert report.stop_reason is ProgramStopReason.OWNER_DECISION_REQUIRED
    assert report.launches_this_run == 1, "the second task must not have launched"
    kinds = {row["kind"] for row in report.notifications}
    assert "AUTHORITY_REVOKED" in kinds

    state = load_state(root)
    assert state is not None
    assert state.tasks["one"].state is NodeState.CERTIFIED
    assert state.tasks["two"].state is NodeState.OWNER_HELD
    events = [str(row.get("event")) for row in read_events(root)]
    assert "AUTHORITY_REVOKED" in events


def test_un_enrolling_an_agent_mid_program_stops_dispatch(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program, registry, agent = _enrolled_program(tmp_path, workspace)
    loaded = load_program(program)
    root = tmp_path / "state"

    supervisor = ProgramSupervisor(
        loaded,
        state_root=root,
        enrolled_agents=(agent,),
        registry_root=registry,
        sleeper=lambda _s: None,
    )
    # Remove the record entirely, as a roster edit would.
    from project_atlas.orchestration.program.enrollment import (
        load_registry,
        persist_registry,
    )

    roster = load_registry(registry)
    del roster.agents["worker-a"]
    persist_registry(registry, roster)

    report = supervisor.start()
    assert report.launches_this_run == 0
    assert report.stop_reason is ProgramStopReason.OWNER_DECISION_REQUIRED
    detail = next(
        row for row in report.notifications if row["kind"] == "AUTHORITY_REVOKED"
    )
    assert "no longer enrolled" in detail["message"]


def test_withdrawing_a_runtime_substitution_grant_stops_dispatch(
    tmp_path: Path,
) -> None:
    """A grant is per agent record and its withdrawal must bite before launch."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(
        tmp_path,
        workspace,
        tasks=[_task("one")],
        profiles={"impl": _profile("placeholder", adapter="claude-code",
                                   credential="SUBSCRIPTION_OAUTH",
                                   adapter_options={})},
        concurrency=1,
    )
    registry = tmp_path / "registry"
    enroll(
        registry,
        agent_id="codex-worker",
        role="impl",
        adapter=AdapterKind.CODEX,
        workspace_root=workspace,
        enrolled_by="wesley",
    )
    agent, _loaded = assign(
        registry,
        agent_id="codex-worker",
        program_path=program,
        assigned_by="wesley",
        allow_runtime_substitution=True,
    )
    assert agent.runtime_substitution_authorized is True

    # Withdraw it by re-enrolling, which clears the grant by design.
    revoked = enroll(
        registry,
        agent_id="codex-worker",
        role="impl",
        adapter=AdapterKind.CODEX,
        workspace_root=workspace,
        enrolled_by="wesley",
        replace=True,
    )
    assert revoked.runtime_substitution_authorized is False

    supervisor = ProgramSupervisor(
        load_program(program),
        state_root=tmp_path / "state",
        enrolled_agents=(agent,),
        registry_root=registry,
        sleeper=lambda _s: None,
    )
    report = supervisor.start()
    assert report.launches_this_run == 0
    detail = next(
        row for row in report.notifications if row["kind"] == "AUTHORITY_REVOKED"
    )
    assert "withdrawn" in detail["message"]


def test_an_active_agent_is_not_obstructed_by_the_authority_check(
    tmp_path: Path,
) -> None:
    """The negative control: the check must not block legitimate work."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program, registry, agent = _enrolled_program(tmp_path, workspace)
    report = ProgramSupervisor(
        load_program(program),
        state_root=tmp_path / "state",
        enrolled_agents=(agent,),
        registry_root=registry,
        sleeper=lambda _s: None,
    ).start()
    assert report.stop_reason is ProgramStopReason.PROGRAM_COMPLETE
    assert report.launches_this_run == 2
    assert not any(
        row["kind"] == "AUTHORITY_REVOKED" for row in report.notifications
    )
