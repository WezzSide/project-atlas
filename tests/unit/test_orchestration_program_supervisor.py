"""AS-ORCH-PROGRAM-SUPERVISOR-001 acceptance program.

Every scenario the founding directive names, plus the regressions the
implementation itself turned up. All workers here are LABELLED FIXTURES: these
tests establish supervisor behaviour and prove nothing about compatibility
with a real agent runtime. FIXTURE_RUN != REAL_RUNTIME_COMPATIBILITY.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from project_atlas.orchestration.autonomy.models import NodeState, OwnerGateKind
from project_atlas.orchestration.program.adapters.base import version_at_least
from project_atlas.orchestration.program.loader import ProgramLoadError, load_program
from project_atlas.orchestration.program.models import (
    AttemptPhase,
    ExecutionConfidence,
    FailureClass,
    ProgramStopReason,
)
from project_atlas.orchestration.program.profiles import (
    AuthorityExpansionError,
    build_profile_set,
    resolve_effective_profile,
)
from project_atlas.orchestration.program.store import load_state, read_events
from project_atlas.orchestration.program.supervisor import (
    DispatchMode,
    ProgramSupervisor,
    SupervisorError,
    idempotency_key,
)

FIXTURE_WORKER = Path(__file__).with_name("_program_fixture_worker.py")
ZERO_PIN = "0" * 40


# --------------------------------------------------------------------- helpers


def _git(workspace: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(workspace),
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _git(workspace, "init", "--quiet")
    _git(workspace, "config", "user.email", "fixture@example.invalid")
    _git(workspace, "config", "user.name", "Fixture")
    (workspace / "README.md").write_text("fixture workspace\n", encoding="utf-8")
    _git(workspace, "add", "README.md")
    _git(workspace, "commit", "--quiet", "-m", "seed")
    return workspace


def _head(workspace: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(workspace),
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return completed.stdout.strip()


def _profile(
    *,
    profile_id: str = "implementer",
    agent_id: str = "fixture-implementer",
    mode: str = "write",
    target: str = "out.txt",
    max_attempts: int = 3,
    max_seconds: int = 60,
    capabilities: tuple[str, ...] = ("IMPLEMENT",),
) -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "adapter": "local-command",
        "credential": "NOT_APPLICABLE",
        "capabilities": list(capabilities),
        "limits": {"max_seconds": max_seconds, "max_attempts": max_attempts},
        "env_allowlist": [
            "ATLAS_FIXTURE_MODE",
            "ATLAS_FIXTURE_TARGET",
            "ATLAS_PROGRAM_ATTEMPT",
            "ATLAS_PROGRAM_TASK",
        ],
        "adapter_options": {
            "argv": [sys.executable, str(FIXTURE_WORKER)],
        },
        "description": f"FIXTURE {profile_id} in {mode} mode writing {target}",
    }


def _task(
    task_id: str,
    *,
    output: str,
    depends_on: tuple[str, ...] = (),
    profile_ref: str = "implementer",
    owner_gate: str | None = None,
    precondition: dict[str, Any] | None = None,
    iv: bool = False,
    verifier: str | None = None,
    surface: str | None = None,
    retry_safe: bool = False,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "task_id": task_id,
        "title": f"fixture task {task_id}",
        "instruction": f"write {output}",
        "profile_ref": profile_ref,
        "depends_on": list(depends_on),
        "mutation_paths": [output],
        "surface_id": surface or f"surface-{task_id}",
        "surface_semantic": (surface or task_id).upper().replace("-", "_"),
        "capabilities_required": ["IMPLEMENT"],
        "acceptance": [
            {
                "check_id": f"{task_id}-output",
                "kind": "FILE_EXISTS",
                "description": f"{output} exists",
                "path": output,
            }
        ],
        "retry_safe_when_no_launch_evidence": retry_safe,
    }
    if owner_gate:
        body["owner_gate"] = owner_gate
    if precondition:
        body["external_precondition"] = precondition
    if iv:
        body["requires_independent_verification"] = True
    if verifier:
        body["verifier_profile_ref"] = verifier
    return body


def _write_program(
    tmp_path: Path,
    workspace: Path,
    *,
    tasks: list[dict[str, Any]],
    profiles: dict[str, dict[str, Any]] | None = None,
    limits: dict[str, Any] | None = None,
    program_id: str = "fixture-program",
) -> Path:
    payload = {
        "schema_version": 1,
        "program": {
            "program_id": program_id,
            "objective": "fixture program for supervisor acceptance",
            "approved_by": "fixture-owner",
            "approval_reference": "docs/orchestration/program/ACCEPTANCE.md",
            "workspace_root": str(workspace),
            "base_pin": _head(workspace),
            "tasks": tasks,
            "limits": limits or {"max_cycles": 20, "idle_sleep_seconds": 0.0},
        },
        "profile_defaults": {
            "adapter": "local-command",
            "credential": "NOT_APPLICABLE",
        },
        "profiles": profiles or {"implementer": _profile()},
    }
    path = tmp_path / "program.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _supervisor(program_path: Path, tmp_path: Path) -> ProgramSupervisor:
    loaded = load_program(program_path)
    return ProgramSupervisor(
        loaded, state_root=tmp_path / "state", sleeper=lambda _seconds: None
    )



def _strand_at_intent(
    supervisor: ProgramSupervisor, state_root: Path, task_id: str
) -> str:
    """Reproduce a crash in the exact window between intent and launch.

    Faithful to the real ordering rather than convenient: the supervisor
    grants and durably projects ownership FIRST, writes the dispatch intent
    SECOND, and only then transitions the task out of LEASED. A crash at
    INTENT_RECORDED therefore leaves a LEASED task, a durable ACTIVE lease
    row, and an attempt with no outcome -- and a test that synthesised any
    other combination would be exercising a state the system cannot reach.
    """
    from project_atlas.orchestration.autonomy.lease_projection import project_grant
    from project_atlas.orchestration.program.store import (
        AttemptRecord,
        persist_state,
        state_dir,
    )

    state = supervisor.load_or_init_state()
    task = supervisor.program.task(task_id)
    profile = supervisor.loaded.effective_profile(task_id)
    node = task.to_work_node(base_pin=state.base_pin).model_copy(
        update={"state": NodeState.READY}
    )
    from project_atlas.orchestration.autonomy.leases import grant_lease

    lease = grant_lease(
        lease_id=f"stranded-{task_id}",
        agent=profile.to_agent_record(),
        node=node,
        branch="fixture",
        worktree=str(supervisor.workspace),
        sequence=1,
    )
    project_grant(state_dir(state_root), lease, live_main=state.base_pin)
    state.tasks[task_id].state = NodeState.LEASED
    state.tasks[task_id].attempts = 1
    state.attempts["stranded"] = AttemptRecord(
        attempt_id="stranded",
        task_id=task_id,
        attempt_number=1,
        idempotency_key="k",
        profile_id=profile.profile_id,
        agent_id=profile.agent_id,
        adapter="local-command",
        profile_digest="a" * 64,
        base_pin=state.base_pin,
        lease_id=lease.lease_id,
        phase=AttemptPhase.INTENT_RECORDED,
        runtime_session_id="session-1",
    )
    persist_state(state_root, state)
    return lease.lease_id


@pytest.fixture(autouse=True)
def _fixture_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_FIXTURE_MODE", "write")
    monkeypatch.setenv("ATLAS_FIXTURE_TARGET", "out.txt")


# ------------------------------------------------- more than one task, no prompt


def test_two_tasks_run_in_dependency_order_without_a_second_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completes; B becomes eligible and starts with no user intervention."""
    workspace = _make_workspace(tmp_path)
    monkeypatch.setenv("ATLAS_FIXTURE_TARGET", "shared.txt")
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[
            _task("task-a", output="shared.txt"),
            _task("task-b", output="shared.txt", depends_on=("task-a",)),
        ],
    )
    report = _supervisor(program, tmp_path).start()

    dispatched = [
        row["task_id"] for row in report.to_public_dict()["tasks_dispatched"]
    ]
    assert dispatched == ["task-a", "task-b"], dispatched
    assert report.stop_reason is ProgramStopReason.PROGRAM_COMPLETE
    assert report.complete is True
    assert report.launches == 2

    state = load_state(tmp_path / "state")
    assert state is not None
    assert state.tasks["task-a"].state is NodeState.CERTIFIED
    assert state.tasks["task-b"].state is NodeState.CERTIFIED


def test_task_completion_is_not_program_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first task finishing must not end the program."""
    workspace = _make_workspace(tmp_path)
    monkeypatch.setenv("ATLAS_FIXTURE_TARGET", "shared.txt")
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[
            _task("task-a", output="shared.txt"),
            _task("task-b", output="shared.txt", depends_on=("task-a",)),
        ],
    )
    supervisor = _supervisor(program, tmp_path)
    report = supervisor.start()

    events = read_events(tmp_path / "state")
    accepted = [row for row in events if row.get("event") == "TASK_ACCEPTED"]
    assert [row["task_id"] for row in accepted] == ["task-a", "task-b"]
    # PROGRAM_COMPLETE is raised exactly once, after the last task.
    completions = [
        row for row in events if row.get("event") == "NOTIFY_PROGRAM_COMPLETE"
    ]
    assert len(completions) == 1
    assert report.complete is True


# ------------------------------------------------------------ external waiting


def test_independent_work_proceeds_while_another_task_waits_on_an_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A controlled external event gates one task; unrelated work still runs."""
    workspace = _make_workspace(tmp_path)
    monkeypatch.setenv("ATLAS_FIXTURE_TARGET", "shared.txt")
    signal_file = tmp_path / "ci-signal"
    precondition = {
        "precondition_id": "ci-green",
        "observer_type": "GITHUB_CI",
        "external_id": "run-1",
        "probe_argv": [sys.executable, "-c", _probe_source(signal_file)],
        "pass_exit_code": 0,
        "poll_interval_seconds": 0.0,
        "max_wait_seconds": 3600.0,
    }
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[
            _task("gated", output="shared.txt", precondition=precondition),
            _task("independent", output="shared.txt", surface="independent"),
        ],
        limits={"max_cycles": 6, "idle_sleep_seconds": 0.0, "max_idle_cycles": 2},
    )

    supervisor = _supervisor(program, tmp_path)
    report = supervisor.start()

    dispatched = [
        row["task_id"] for row in report.to_public_dict()["tasks_dispatched"]
    ]
    assert "independent" in dispatched, "unrelated ready work must not be blocked"
    assert "gated" not in dispatched, "the gated task must not run before its event"

    status = supervisor.status()
    assert status["waiting_on_external_event"], status
    assert status["done"] == ["independent"]

    # Now let the event happen; the gated task becomes eligible on its own.
    signal_file.write_text("green\n", encoding="utf-8")
    report2 = _supervisor(program, tmp_path).start()
    dispatched2 = [
        row["task_id"] for row in report2.to_public_dict()["tasks_dispatched"]
    ]
    assert dispatched2 == ["gated"], dispatched2
    assert report2.stop_reason is ProgramStopReason.PROGRAM_COMPLETE


def _probe_source(signal_file: Path) -> str:
    return (
        "import pathlib,sys;"
        f"sys.exit(0 if pathlib.Path({str(signal_file)!r}).exists() else 7)"
    )


def test_repeated_terminal_events_do_not_cause_a_second_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once-only consumption: the same passed event promotes a task once."""
    workspace = _make_workspace(tmp_path)
    monkeypatch.setenv("ATLAS_FIXTURE_TARGET", "shared.txt")
    signal_file = tmp_path / "ci-signal"
    signal_file.write_text("green\n", encoding="utf-8")
    precondition = {
        "precondition_id": "ci-green",
        "observer_type": "GITHUB_CI",
        "external_id": "run-1",
        "probe_argv": [sys.executable, "-c", _probe_source(signal_file)],
        "poll_interval_seconds": 0.0,
    }
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[_task("gated", output="shared.txt", precondition=precondition)],
    )
    report = _supervisor(program, tmp_path).start()
    assert report.launches_this_run == 1

    # Start again against the same durable state. The event is still terminal
    # and still "passed", but it has already been consumed once.
    report2 = _supervisor(program, tmp_path).start()
    assert report2.launches_this_run == 0
    assert report2.stop_reason is ProgramStopReason.PROGRAM_COMPLETE

    events = read_events(tmp_path / "state")
    intents = [row for row in events if row.get("event") == "DISPATCH_INTENT"]
    assert len(intents) == 1, "a re-delivered event produced a second dispatch"


# ----------------------------------------------------------------- owner gates


def test_owner_gated_task_never_dispatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _make_workspace(tmp_path)
    monkeypatch.setenv("ATLAS_FIXTURE_TARGET", "shared.txt")
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[
            _task("open-work", output="shared.txt"),
            _task(
                "gated-work",
                output="shared.txt",
                surface="gated",
                owner_gate=OwnerGateKind.D_SECURITY_GOVERNANCE_POLICY.value,
            ),
        ],
    )
    supervisor = _supervisor(program, tmp_path)
    report = supervisor.start()

    dispatched = [
        row["task_id"] for row in report.to_public_dict()["tasks_dispatched"]
    ]
    assert dispatched == ["open-work"]
    assert report.stop_reason is ProgramStopReason.OWNER_DECISION_REQUIRED
    assert report.complete is False

    kinds = {row["kind"] for row in report.notifications}
    assert "OWNER_DECISION_REQUIRED" in kinds

    status = supervisor.status()
    assert status["owner_decision_required"] == [
        {"task_id": "gated-work", "gate": "D_SECURITY_GOVERNANCE_POLICY"}
    ]
    assert status["merge_authorized"] is False


# ------------------------------------------------- acceptance vs worker claims


def test_worker_claiming_completion_without_doing_the_work_fails_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 0 and a confident 'Task complete' is not evidence."""
    workspace = _make_workspace(tmp_path)
    monkeypatch.setenv("ATLAS_FIXTURE_MODE", "claim-only")
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[_task("claims", output="never-written.txt")],
        profiles={"implementer": _profile(max_attempts=2)},
        limits={"max_cycles": 8, "idle_sleep_seconds": 0.0, "max_attempts_per_task": 2},
    )
    supervisor = _supervisor(program, tmp_path)
    report = supervisor.start()

    state = load_state(tmp_path / "state")
    assert state is not None
    record = state.tasks["claims"]
    assert record.state is NodeState.BLOCKED
    assert report.complete is False

    attempts = list(state.attempts.values())
    assert attempts, "an attempt should have been recorded"
    first = attempts[0]
    assert first.exit_status == 0
    assert first.confidence is ExecutionConfidence.CONFIRMED
    assert first.acceptance_passed is False
    assert "Task complete" in (first.worker_reported or "")

    kinds = [row["kind"] for row in report.notifications]
    assert "ACCEPTANCE_FAILED" in kinds or "NO_PROGRESS" in kinds


def test_no_progress_between_identical_attempts_stops_retrying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """More attempts are not progress. A byte-identical workspace stops it."""
    workspace = _make_workspace(tmp_path)
    monkeypatch.setenv("ATLAS_FIXTURE_MODE", "claim-only")
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[_task("claims", output="never-written.txt")],
        profiles={"implementer": _profile(max_attempts=5)},
        limits={"max_cycles": 12, "idle_sleep_seconds": 0.0, "max_attempts_per_task": 5},
    )
    report = _supervisor(program, tmp_path).start()
    state = load_state(tmp_path / "state")
    assert state is not None
    assert state.tasks["claims"].last_failure_class is FailureClass.NO_PROGRESS
    # It stopped well before exhausting five attempts.
    assert state.tasks["claims"].attempts == 2, state.tasks["claims"].attempts
    assert report.launches == 2


def test_transient_failure_is_retried_and_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _make_workspace(tmp_path)
    monkeypatch.setenv("ATLAS_FIXTURE_MODE", "fail-once")
    monkeypatch.setenv("ATLAS_FIXTURE_TARGET", "retried.txt")
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[_task("flaky", output="retried.txt")],
    )
    report = _supervisor(program, tmp_path).start()
    assert report.stop_reason is ProgramStopReason.PROGRAM_COMPLETE
    assert report.launches == 2
    modes = [cycle.dispatch_mode for cycle in report.cycles if cycle.dispatch_mode]
    assert modes == [DispatchMode.NEW, DispatchMode.RETRY]


@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [
        (21, FailureClass.INVALID_TASK_INPUT),
        (22, FailureClass.POLICY_REFUSAL),
        (23, FailureClass.QUOTA_OR_CREDENTIAL),
    ],
)
def test_permanent_failure_classes_are_never_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int,
    expected: FailureClass,
) -> None:
    workspace = _make_workspace(tmp_path)
    monkeypatch.setenv("ATLAS_FIXTURE_MODE", f"exit:{exit_code}")
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[_task("doomed", output="never.txt")],
        profiles={"implementer": _profile(max_attempts=5)},
        limits={"max_cycles": 10, "idle_sleep_seconds": 0.0, "max_attempts_per_task": 5},
    )
    report = _supervisor(program, tmp_path).start()
    state = load_state(tmp_path / "state")
    assert state is not None
    assert state.tasks["doomed"].last_failure_class is expected
    assert state.tasks["doomed"].state is NodeState.BLOCKED
    assert report.launches == 1, "a permanent failure must not be retried"


# --------------------------------------------------- independent verification


def test_implementer_cannot_verify_its_own_work(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[
            _task("needs-iv", output="out.txt", iv=True, verifier="shadow"),
        ],
        profiles={
            "implementer": _profile(agent_id="same-agent"),
            "shadow": {
                **_profile(agent_id="same-agent", capabilities=("IMPLEMENT", "VERIFY")),
            },
        },
    )
    with pytest.raises(ProgramLoadError) as excinfo:
        load_program(program)
    assert excinfo.value.code == "IMPLEMENTER_CANNOT_VERIFY"


def test_task_awaiting_verification_does_not_block_unrelated_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A task at its IV gate parks; the supervisor continues elsewhere."""
    workspace = _make_workspace(tmp_path)
    # Distinct outputs on purpose: these two tasks must be genuinely
    # independent. Sharing a mutation path would make the existing surface
    # overlap gate refuse to run them alongside each other -- correctly -- and
    # the test would then be measuring the overlap gate, not the IV gate.
    monkeypatch.delenv("ATLAS_FIXTURE_TARGET", raising=False)
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[
            _task("needs-iv", output="needs-iv.txt", iv=True),
            _task("unrelated", output="unrelated.txt", surface="unrelated"),
        ],
        limits={"max_cycles": 8, "idle_sleep_seconds": 0.0, "max_idle_cycles": 2},
    )
    supervisor = _supervisor(program, tmp_path)
    report = supervisor.start()

    dispatched = [
        row["task_id"] for row in report.to_public_dict()["tasks_dispatched"]
    ]
    assert dispatched == ["needs-iv", "unrelated"], dispatched
    assert report.stop_reason is ProgramStopReason.AWAITING_INDEPENDENT_VERIFICATION
    assert report.complete is False

    status = supervisor.status()
    assert status["awaiting_independent_verification"] == ["needs-iv"]
    assert status["done"] == ["unrelated"]


def test_a_distinct_verifier_agent_certifies_the_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _make_workspace(tmp_path)
    monkeypatch.setenv("ATLAS_FIXTURE_TARGET", "verified.txt")
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[_task("needs-iv", output="verified.txt", iv=True, verifier="verifier")],
        profiles={
            "implementer": _profile(agent_id="fixture-implementer"),
            "verifier": _profile(
                profile_id="verifier",
                agent_id="fixture-verifier",
                capabilities=("VERIFY",),
            ),
        },
    )
    report = _supervisor(program, tmp_path).start()
    assert report.stop_reason is ProgramStopReason.PROGRAM_COMPLETE

    state = load_state(tmp_path / "state")
    assert state is not None
    record = state.tasks["needs-iv"]
    assert record.state is NodeState.CERTIFIED
    assert record.verified_by_agent_id == "fixture-verifier"
    assert record.awaiting_independent_verification is False

    events = read_events(tmp_path / "state")
    verified = [row for row in events if row.get("event") == "TASK_VERIFIED"]
    assert verified[0]["implementer_agent_id"] != verified[0]["verifier_agent_id"]


# ------------------------------------------------------- limits, cancellation


def test_launch_limit_prevents_further_launches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _make_workspace(tmp_path)
    monkeypatch.setenv("ATLAS_FIXTURE_TARGET", "shared.txt")
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[
            _task("one", output="shared.txt"),
            _task("two", output="shared.txt", depends_on=("one",)),
            _task("three", output="shared.txt", depends_on=("two",)),
        ],
        limits={
            "max_cycles": 20,
            "idle_sleep_seconds": 0.0,
            "max_task_launches": 2,
        },
    )
    report = _supervisor(program, tmp_path).start()
    assert report.launches == 2
    assert report.stop_reason is ProgramStopReason.LIMIT_REACHED
    assert report.complete is False
    assert any(row["kind"] == "LAUNCH_LIMIT" for row in report.notifications)


def test_cancellation_prevents_further_launches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _make_workspace(tmp_path)
    monkeypatch.setenv("ATLAS_FIXTURE_TARGET", "shared.txt")
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[
            _task("one", output="shared.txt"),
            _task("two", output="shared.txt", depends_on=("one",)),
        ],
    )
    supervisor = _supervisor(program, tmp_path)
    supervisor.load_or_init_state()
    supervisor.request_cancel()

    report = supervisor.start()
    assert report.launches == 0
    assert report.stop_reason is ProgramStopReason.CANCELLED


def test_cancelling_a_running_worker_records_an_uncertain_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The local process is stopped; its external effect is NOT assumed."""
    workspace = _make_workspace(tmp_path)
    monkeypatch.setenv("ATLAS_FIXTURE_MODE", "hang")
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[_task("hangs", output="never.txt")],
        profiles={"implementer": _profile(max_seconds=3600)},
    )
    supervisor = _supervisor(program, tmp_path)

    # Ask for cancellation the instant the worker is launched, which is the
    # real shape of an operator pressing stop while something is running: the
    # child is already alive and the cancel check it polls is what stops it.
    original = supervisor._begin_dispatch

    def dispatch_then_cancel(state: Any, choice: Any, result: Any) -> Any:
        running = original(state, choice, result)
        state.cancel_requested = True
        return running

    supervisor._begin_dispatch = dispatch_then_cancel  # type: ignore[method-assign]
    report = supervisor.start()

    assert report.stop_reason is ProgramStopReason.CANCELLED
    state = load_state(tmp_path / "state")
    assert state is not None
    attempt = next(iter(state.attempts.values()))
    assert attempt.confidence is ExecutionConfidence.UNCERTAIN
    assert attempt.failure_class is FailureClass.UNCERTAIN_OUTCOME
    assert any("UNKNOWN" in note for note in attempt.notes)
    # The lease is deliberately still held: the surface is not handed on while
    # what happened to it is unknown.
    assert state.tasks["hangs"].state is NodeState.ACTIVE


def test_task_timeout_is_uncertain_not_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _make_workspace(tmp_path)
    monkeypatch.setenv("ATLAS_FIXTURE_MODE", "hang")
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[_task("hangs", output="never.txt")],
        profiles={"implementer": _profile(max_seconds=1)},
        limits={"max_cycles": 3, "idle_sleep_seconds": 0.0, "max_task_seconds": 1},
    )
    report = _supervisor(program, tmp_path).start()
    assert report.stop_reason is ProgramStopReason.RECONCILE_REQUIRED
    state = load_state(tmp_path / "state")
    assert state is not None
    attempt = next(iter(state.attempts.values()))
    assert attempt.confidence is ExecutionConfidence.UNCERTAIN


# ----------------------------------------------------- restart reconciliation


def test_restart_during_execution_reconciles_without_redispatching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash mid-run leaves an interrupted attempt; the next start stops."""
    workspace = _make_workspace(tmp_path)
    monkeypatch.setenv("ATLAS_FIXTURE_MODE", "hang")
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[_task("interrupted", output="never.txt")],
        profiles={"implementer": _profile(max_seconds=1)},
        limits={"max_cycles": 2, "idle_sleep_seconds": 0.0, "max_task_seconds": 1},
    )
    first = _supervisor(program, tmp_path).start()
    assert first.stop_reason is ProgramStopReason.RECONCILE_REQUIRED
    launches_before = first.launches

    second = _supervisor(program, tmp_path).start()
    assert second.stop_reason is ProgramStopReason.RECONCILE_REQUIRED
    assert second.launches_this_run == 0, "a restart must not redispatch an uncertain attempt"

    state = load_state(tmp_path / "state")
    assert state is not None
    assert len(state.attempts) == launches_before

    events = read_events(tmp_path / "state")
    reconciliations = [
        row for row in events if row.get("event") == "RESTART_RECONCILIATION"
    ]
    assert reconciliations, "the restart must record what it decided and why"
    assert reconciliations[-1]["action"] == "NEEDS_RECONCILIATION"


def test_operator_reconciliation_settles_an_uncertain_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _make_workspace(tmp_path)
    monkeypatch.setenv("ATLAS_FIXTURE_MODE", "hang")
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[_task("interrupted", output="never.txt")],
        profiles={"implementer": _profile(max_seconds=1)},
        limits={"max_cycles": 2, "idle_sleep_seconds": 0.0, "max_task_seconds": 1},
    )
    _supervisor(program, tmp_path).start()

    supervisor = _supervisor(program, tmp_path)
    findings = supervisor.reconcile()
    assert findings["interrupted_attempts"], findings
    attempt_id = findings["interrupted_attempts"][0]["attempt_id"]

    settled = supervisor.reconcile(resolve_uncertain=attempt_id)
    assert settled["resolved"]["attempt_id"] == attempt_id

    state = load_state(tmp_path / "state")
    assert state is not None
    assert state.attempts[attempt_id].phase is AttemptPhase.TERMINAL
    assert any(
        "operator" in note for note in state.attempts[attempt_id].notes
    ), state.attempts[attempt_id].notes


def test_intent_recorded_without_launch_evidence_is_not_relaunched(
    tmp_path: Path,
) -> None:
    """The crash window between writing intent and spawning the worker."""
    workspace = _make_workspace(tmp_path)
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[_task("half-dispatched", output="out.txt")],
    )
    supervisor = _supervisor(program, tmp_path)
    _strand_at_intent(supervisor, tmp_path / "state", "half-dispatched")

    report = _supervisor(program, tmp_path).start()
    assert report.stop_reason is ProgramStopReason.RECONCILE_REQUIRED
    assert report.launches == 0


def test_declared_repeatable_task_may_relaunch_after_no_launch_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a task that says its effect is repeatable gets relaunched."""
    workspace = _make_workspace(tmp_path)
    monkeypatch.setenv("ATLAS_FIXTURE_TARGET", "repeatable.txt")
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[_task("idempotent", output="repeatable.txt", retry_safe=True)],
    )
    supervisor = _supervisor(program, tmp_path)
    _strand_at_intent(supervisor, tmp_path / "state", "idempotent")

    report = _supervisor(program, tmp_path).start()
    assert report.launches_this_run == 1
    assert report.stop_reason is ProgramStopReason.PROGRAM_COMPLETE


def test_pid_reuse_does_not_look_like_a_live_worker() -> None:
    """A live PID with a different start identity is somebody else's process."""
    from project_atlas.orchestration.program.recovery import worker_still_alive
    from project_atlas.orchestration.program.store import AttemptRecord

    attempt = AttemptRecord(
        attempt_id="a",
        task_id="t",
        attempt_number=1,
        idempotency_key="k",
        profile_id="p",
        agent_id="g",
        adapter="local-command",
        profile_digest="a" * 64,
        base_pin=ZERO_PIN,
        process_pid=os.getpid(),
        process_start_identity="a-start-identity-that-is-not-ours",
    )
    assert worker_still_alive(attempt) is False

    unknown = attempt.model_copy(update={"process_start_identity": None})
    assert worker_still_alive(unknown) is False


# ---------------------------------------------------- single-supervisor safety


def test_two_supervisors_cannot_own_the_same_program(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    program = _write_program(
        tmp_path, workspace, tasks=[_task("only", output="out.txt")]
    )
    first = _supervisor(program, tmp_path)
    first._acquire()
    try:
        second = _supervisor(program, tmp_path)
        with pytest.raises(SupervisorError) as excinfo:
            second._acquire()
        assert excinfo.value.code == "SUPERVISOR_DOUBLE_START"
    finally:
        first._release()


def test_losing_the_lock_mid_cycle_stops_before_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An earlier eligibility snapshot is not a licence to dispatch later."""
    workspace = _make_workspace(tmp_path)
    program = _write_program(
        tmp_path, workspace, tasks=[_task("only", output="out.txt")]
    )
    supervisor = _supervisor(program, tmp_path)

    calls = {"n": 0}
    real_check = supervisor._assert_still_supervisor

    def flaky_check() -> None:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise SupervisorError("lock lost", code="SUPERVISOR_LOCK_LOST")
        real_check()

    supervisor._assert_still_supervisor = flaky_check  # type: ignore[method-assign]
    with pytest.raises(SupervisorError) as excinfo:
        supervisor.start()
    assert excinfo.value.code == "SUPERVISOR_LOCK_LOST"

    state = load_state(tmp_path / "state")
    assert state is None or not state.attempts, "nothing may be dispatched after the loss"


# ------------------------------------------------- profiles and authority

def test_task_override_may_narrow_but_never_widen() -> None:
    profiles = build_profile_set(
        defaults={"adapter": "local-command", "credential": "NOT_APPLICABLE"},
        raw_profiles={
            "impl": {
                "agent_id": "impl",
                "capabilities": ["IMPLEMENT", "VERIFY"],
                "allowed_tools": ["Read", "Edit", "Bash"],
                "permission_mode": "acceptEdits",
                "limits": {"max_seconds": 600, "max_attempts": 4},
                "allowed_mutation_prefixes": ["src"],
                "adapter_options": {"argv": ["/bin/true"]},
            }
        },
    )
    narrowed = resolve_effective_profile(
        profiles,
        profile_ref="impl",
        override={
            "allowed_tools": ["Read"],
            "permission_mode": "dontAsk",
            "limits": {"max_seconds": 60},
            "capabilities": ["IMPLEMENT"],
            "allowed_mutation_prefixes": ["src/pkg"],
        },
    )
    assert narrowed.allowed_tools == ("Read",)
    assert narrowed.permission_mode == "dontAsk"
    assert narrowed.limits.max_seconds == 60
    assert narrowed.limits.max_attempts == 4

    for override, code in (
        ({"allowed_tools": ["Read", "WebFetch"]}, "TOOL_ALLOWLIST_EXPANSION"),
        ({"permission_mode": "bypassPermissions"}, "PERMISSION_MODE_ESCALATION"),
        ({"limits": {"max_seconds": 6000}}, "LIMIT_EXPANSION"),
        ({"limits": {"max_attempts": 40}}, "LIMIT_EXPANSION"),
        ({"capabilities": ["ADVERSARIAL_REVIEW"]}, "CAPABILITY_EXPANSION"),
        ({"allowed_mutation_prefixes": ["tests"]}, "MUTATION_SURFACE_EXPANSION"),
        ({"adapter": "claude-code"}, "OVERRIDE_FIELD_FORBIDDEN"),
        ({"agent_id": "someone-else"}, "OVERRIDE_FIELD_FORBIDDEN"),
        ({"adapter_options": {"argv": ["/bin/sh"]}}, "OVERRIDE_FIELD_FORBIDDEN"),
    ):
        with pytest.raises(AuthorityExpansionError) as excinfo:
            resolve_effective_profile(profiles, profile_ref="impl", override=override)
        assert excinfo.value.code == code, override


def test_shared_defaults_never_overwrite_a_stated_role_permission() -> None:
    profiles = build_profile_set(
        defaults={
            "adapter": "local-command",
            "credential": "NOT_APPLICABLE",
            "permission_mode": "auto",
            "allowed_tools": ["Read", "Edit", "Bash"],
        },
        raw_profiles={
            "tight": {
                "agent_id": "tight",
                "capabilities": ["VERIFY"],
                "permission_mode": "plan",
                "allowed_tools": ["Read"],
                "adapter_options": {"argv": ["/bin/true"]},
            },
            "loose": {
                "agent_id": "loose",
                "capabilities": ["IMPLEMENT"],
                "adapter_options": {"argv": ["/bin/true"]},
            },
        },
    )
    assert profiles.resolve("tight").permission_mode == "plan"
    assert profiles.resolve("tight").allowed_tools == ("Read",)
    assert profiles.resolve("loose").permission_mode == "auto"


def test_task_exceeding_its_profile_is_rejected_at_validation(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    task = _task("wide", output="src/thing.txt")
    task["mutation_paths"] = ["docs/elsewhere.txt"]
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[task],
        profiles={
            "implementer": {
                **_profile(),
                "allowed_mutation_prefixes": ["src"],
            }
        },
    )
    with pytest.raises(ProgramLoadError) as excinfo:
        load_program(program)
    assert excinfo.value.code == "MUTATION_SURFACE_EXPANSION"


def test_profile_required_evidence_must_be_present(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[_task("thin", output="out.txt")],
        profiles={
            "implementer": {
                **_profile(),
                "required_evidence": ["COMMAND"],
            }
        },
    )
    with pytest.raises(ProgramLoadError) as excinfo:
        load_program(program)
    assert excinfo.value.code == "EVIDENCE_REQUIREMENT_UNMET"


def test_dependency_cycle_is_rejected_at_load(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[
            _task("a", output="a.txt", depends_on=("b",)),
            _task("b", output="b.txt", depends_on=("a",)),
        ],
    )
    with pytest.raises(Exception) as excinfo:
        load_program(program)
    assert "cycle" in str(excinfo.value)


def test_program_file_edited_mid_flight_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An approval that no longer describes the work is an owner matter."""
    workspace = _make_workspace(tmp_path)
    monkeypatch.setenv("ATLAS_FIXTURE_TARGET", "shared.txt")
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[
            _task("one", output="shared.txt"),
            _task("two", output="shared.txt", depends_on=("one",)),
        ],
        limits={"max_cycles": 1, "idle_sleep_seconds": 0.0},
    )
    _supervisor(program, tmp_path).start()

    payload = json.loads(program.read_text(encoding="utf-8"))
    payload["program"]["objective"] = "something the owner never approved"
    program.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    with pytest.raises(SupervisorError) as excinfo:
        _supervisor(program, tmp_path).start()
    assert excinfo.value.code == "PROGRAM_DIGEST_DRIFT"


# ------------------------------------------------------------- idempotency key


def test_idempotency_key_distinguishes_adapter_and_profile() -> None:
    """Two different commands at one commit must not collide."""
    common = {
        "program_id": "p",
        "task_id": "t",
        "attempt_number": 1,
        "base_pin": ZERO_PIN,
    }
    a = idempotency_key(adapter_id="claude-code", profile_sha="a" * 64, **common)
    b = idempotency_key(adapter_id="local-command", profile_sha="a" * 64, **common)
    c = idempotency_key(adapter_id="claude-code", profile_sha="b" * 64, **common)
    assert len({a, b, c}) == 3
    assert idempotency_key(adapter_id="claude-code", profile_sha="a" * 64, **common) == a


# ------------------------------------------------------------ adapter contract


def test_version_comparison_treats_unreadable_as_unsatisfied() -> None:
    assert version_at_least("2.1.267", "2.1.259") is True
    assert version_at_least("2.1.200", "2.1.259") is False
    assert version_at_least(None, "2.1.259") is False
    assert version_at_least("not a version", "2.1.259") is False
    assert version_at_least(None, None) is True


def test_claude_adapter_does_not_trust_subtype_success() -> None:
    """The observed real failure: subtype 'success' with is_error true."""
    from project_atlas.orchestration.program.adapters.claude_code import _classify

    observed = {
        "subtype": "success",
        "is_error": True,
        "terminal_reason": "api_error",
        "api_error_status": 400,
        "result": "Credit balance is too low",
        "session_id": "s",
    }
    confidence, failure, reason = _classify(
        terminal="completed", exit_status=1, parsed=observed, stderr=""
    )
    assert confidence is ExecutionConfidence.FAILED
    assert failure is FailureClass.QUOTA_OR_CREDENTIAL
    assert reason == "api_error"


def test_claude_adapter_sigterm_is_uncertain_not_failed() -> None:
    from project_atlas.orchestration.program.adapters.claude_code import (
        SIGTERM_EXIT_STATUS,
        _classify,
    )

    confidence, failure, _reason = _classify(
        terminal="completed", exit_status=SIGTERM_EXIT_STATUS, parsed=None, stderr=""
    )
    assert confidence is ExecutionConfidence.UNCERTAIN
    assert failure is FailureClass.UNCERTAIN_OUTCOME


def test_claude_adapter_keeps_the_prompt_off_the_command_line(tmp_path: Path) -> None:
    from project_atlas.orchestration.program.adapters.base import AdapterRequest
    from project_atlas.orchestration.program.adapters.claude_code import ClaudeCodeAdapter
    from project_atlas.orchestration.program.profiles import AgentProfile

    profile = AgentProfile.model_validate(
        {
            "profile_id": "impl",
            "agent_id": "impl",
            "adapter": "claude-code",
            "capabilities": ["IMPLEMENT"],
            "permission_mode": "dontAsk",
            "allowed_tools": ["Read", "Edit"],
            "model": "sonnet",
        }
    )
    request = AdapterRequest(
        program_id="p",
        task_id="t",
        attempt_id="a",
        attempt_number=1,
        idempotency_key="k",
        instruction="rm -rf / # this must never reach argv",
        workspace=tmp_path,
        profile=profile,
        session_id="11111111-2222-4333-8444-555555555555",
        resume_session_id=None,
        timeout_seconds=60,
        evidence_dir=tmp_path,
    )
    # sys.executable stands in for an installed runtime: this test is about
    # argv shape, not about `claude` being on this machine's PATH.
    argv = ClaudeCodeAdapter(sys.executable).build_argv(request)
    assert "rm -rf /" not in " ".join(argv)
    assert "--permission-prompts" in argv and "none" in argv
    assert "--session-id" in argv
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"


def test_child_environment_is_built_not_inherited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An inherited API key must not silently redirect which account is billed."""
    from project_atlas.orchestration.program.adapters.base import build_child_env
    from project_atlas.orchestration.program.profiles import AgentProfile

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-forwarded")
    monkeypatch.setenv("SOME_UNRELATED_VAR", "leak")
    profile = AgentProfile.model_validate(
        {
            "profile_id": "impl",
            "agent_id": "impl",
            "adapter": "claude-code",
            "capabilities": ["IMPLEMENT"],
            "credential": "SUBSCRIPTION_OAUTH",
        }
    )
    env = build_child_env(profile)
    assert "ANTHROPIC_API_KEY" not in env
    assert "SOME_UNRELATED_VAR" not in env
    assert "PATH" in env


def test_subscription_profile_may_not_allowlist_an_api_key(tmp_path: Path) -> None:
    from project_atlas.orchestration.program.adapters.base import AdapterRequest
    from project_atlas.orchestration.program.adapters.claude_code import ClaudeCodeAdapter
    from project_atlas.orchestration.program.profiles import AgentProfile

    profile = AgentProfile.model_validate(
        {
            "profile_id": "impl",
            "agent_id": "impl",
            "adapter": "claude-code",
            "capabilities": ["IMPLEMENT"],
            "credential": "SUBSCRIPTION_OAUTH",
            "env_allowlist": ["ANTHROPIC_API_KEY"],
        }
    )
    request = AdapterRequest(
        program_id="p",
        task_id="t",
        attempt_id="a",
        attempt_number=1,
        idempotency_key="k",
        instruction="hello",
        workspace=tmp_path,
        profile=profile,
        session_id=None,
        resume_session_id=None,
        timeout_seconds=5,
        evidence_dir=tmp_path,
    )
    # No executable is supplied on purpose: a profile whose declared
    # credential mechanism contradicts its own allow-list is broken wherever
    # it runs, and must be reported as THAT rather than as a missing runtime.
    with pytest.raises(Exception) as excinfo:
        ClaudeCodeAdapter("a-runtime-that-is-not-installed").run(request)
    assert "CREDENTIAL_MECHANISM_CONFLICT" in str(getattr(excinfo.value, "code", ""))


# ---------------------------------------------------------------------- CLI


def test_cli_validate_reports_the_enforcement_picture(tmp_path: Path) -> None:
    from project_atlas.orchestration.program.cli import main

    workspace = _make_workspace(tmp_path)
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[_task("only", output="out.txt", iv=True)],
    )
    code = main(["program", "validate", "--program", str(program)])
    assert code == 0


def test_cli_status_before_start_says_so(tmp_path: Path, capsys: Any) -> None:
    from project_atlas.orchestration.program.cli import main

    workspace = _make_workspace(tmp_path)
    program = _write_program(
        tmp_path, workspace, tasks=[_task("only", output="out.txt")]
    )
    code = main(
        [
            "program",
            "status",
            "--program",
            str(program),
            "--state-root",
            str(tmp_path / "state"),
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["started"] is False


def test_overlapping_mutation_surfaces_are_not_run_alongside_each_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The existing surface-overlap gate still applies in this package.

    One worker at a time makes this trivially satisfied today, which is
    exactly why it is asserted now: the check must already be in the path
    before concurrency is added, not bolted on alongside it.
    """
    from project_atlas.orchestration.autonomy.overlap import would_overlap

    workspace = _make_workspace(tmp_path)
    monkeypatch.delenv("ATLAS_FIXTURE_TARGET", raising=False)
    program = _write_program(
        tmp_path,
        workspace,
        tasks=[
            _task("first", output="shared-target.txt"),
            _task("second", output="shared-target.txt", surface="second"),
        ],
    )
    loaded = load_program(program)
    nodes = tuple(
        task.to_work_node(base_pin=loaded.program.base_pin)
        for task in loaded.program.tasks
    )
    active = (nodes[0].model_copy(update={"state": NodeState.ACTIVE}),)
    assert would_overlap(active, nodes[1]) is True

    distinct = _write_program(
        tmp_path / "other",
        workspace,
        tasks=[
            _task("first", output="a.txt"),
            _task("second", output="b.txt", surface="second"),
        ],
    ) if (tmp_path / "other").mkdir(exist_ok=True) is None else None
    assert distinct is not None
    loaded2 = load_program(distinct)
    nodes2 = tuple(
        task.to_work_node(base_pin=loaded2.program.base_pin)
        for task in loaded2.program.tasks
    )
    active2 = (nodes2[0].model_copy(update={"state": NodeState.ACTIVE}),)
    assert would_overlap(active2, nodes2[1]) is False


def test_child_runner_delivers_stdin_and_still_collects_output(tmp_path: Path) -> None:
    """Regression: the first real Claude Code launch died in this exact path.

    ``run_child_to_completion`` closes the child's stdin so the runtime sees
    EOF on its prompt, then calls ``communicate()``. Leaving the handle
    attached made ``communicate()`` close it a second time and raise
    "I/O operation on closed file", which the supervisor correctly recorded as
    an UNCERTAIN outcome -- for every single launch. Fixture tests never
    reached it because the fixture adapter passes no stdin.
    """
    from project_atlas.orchestration.program.adapters.base import run_child_to_completion

    exit_status, stdout, stderr, pid, identity, terminal = run_child_to_completion(
        [sys.executable, "-c", "import sys; print('GOT:' + sys.stdin.read().strip())"],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", "")},
        stdin_text="the prompt travels here",
        timeout_seconds=60,
        cancel_requested=None,
    )
    assert terminal == "completed"
    assert exit_status == 0
    assert "GOT:the prompt travels here" in stdout
    assert stderr == ""
    assert pid and pid > 0
    assert identity


def test_child_runner_survives_a_child_that_never_reads_stdin(tmp_path: Path) -> None:
    """A child exiting before reading its prompt is not an adapter failure."""
    from project_atlas.orchestration.program.adapters.base import run_child_to_completion

    exit_status, stdout, _stderr, _pid, _identity, terminal = run_child_to_completion(
        [sys.executable, "-c", "print('done without reading stdin')"],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", "")},
        stdin_text="x" * 200_000,
        timeout_seconds=60,
        cancel_requested=None,
    )
    assert terminal == "completed"
    assert exit_status == 0
    assert "done without reading stdin" in stdout
