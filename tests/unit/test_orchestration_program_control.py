"""AS-ORCH-PROGRAM-SUPERVISOR-001 M6 — the Studio control contract.

Studio observes; it does not drive.

  UI != CANONICAL TRUTH
  OBSERVATION != CONTROL
  REQUEST != AUTHORIZATION

The contract is versioned so it can be depended on, narrow so a viewer cannot
reach anything that changes what a program may do, and read-only except for
four governed requests.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from project_atlas.orchestration.autonomy.models import NodeState, OwnerGateKind
from project_atlas.orchestration.program import control, service
from project_atlas.orchestration.program.loader import load_program
from project_atlas.orchestration.program.models import ProgramStopReason
from project_atlas.orchestration.program.store import (
    load_state,
    persist_state,
    state_dir,
)
from project_atlas.orchestration.program.supervisor import ProgramSupervisor

FIXTURE_WORKER = Path(__file__).with_name("_program_fixture_worker.py")


def _program(
    tmp_path: Path,
    workspace: Path,
    *,
    tasks: list[str] | None = None,
    owner_gated: str | None = None,
) -> Path:
    names = tasks or ["alpha", "beta"]
    task_bodies = []
    for name in names:
        body: dict[str, Any] = {
            "task_id": name,
            "title": f"task {name}",
            "instruction": "do it",
            "profile_ref": "impl",
            "mutation_paths": [f"{name}.txt"],
            "surface_id": name,
            "surface_semantic": name.upper(),
            "capabilities_required": ["IMPLEMENT"],
            "acceptance": [
                {
                    "check_id": f"{name}-out",
                    "kind": "FILE_EXISTS",
                    "description": f"{name}.txt exists",
                    "path": f"{name}.txt",
                }
            ],
        }
        if owner_gated == name:
            body["owner_gate"] = OwnerGateKind.B_ACCEPTANCE_WAIVER.value
        task_bodies.append(body)
    payload = {
        "schema_version": 1,
        "program": {
            "program_id": "control-program",
            "objective": "control contract coverage",
            "approved_by": "wesley",
            "approval_reference": "docs/orchestration/program/CONTROL.md",
            "workspace_root": str(workspace),
            "base_pin": "0" * 40,
            "limits": {"max_cycles": 10, "idle_sleep_seconds": 0.0},
            "tasks": task_bodies,
        },
        "profiles": {
            "impl": {
                "agent_id": "control-agent",
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
        },
    }
    path = tmp_path / "program.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _fixture_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_FIXTURE_MODE", "write")
    monkeypatch.delenv("ATLAS_FIXTURE_TARGET", raising=False)


def _run(tmp_path: Path, program: Path) -> Any:
    supervisor = ProgramSupervisor(
        load_program(program), state_root=tmp_path / "state", sleeper=lambda _s: None
    )
    return supervisor.start()


# ---------------------------------------------------------------- the view


def test_the_view_is_versioned_and_declares_itself_read_only(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    _run(tmp_path, program)
    view = control.control_view(tmp_path / "state", load_program(program))

    assert view["contract_id"] == "atlas.program.control"
    assert view["contract_version"] == control.CONTRACT_VERSION
    assert control.CONTRACT_VERSION >= 2
    assert view["read_only"] is True
    assert view["merge_authorized"] is False
    assert view["execution_authorized"] is False
    assert "TASK_COMPLETE != PROGRAM_COMPLETE" in view["truth_boundary"]


def test_the_view_carries_everything_an_operator_needs(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    _run(tmp_path, program)
    view = control.control_view(tmp_path / "state", load_program(program))

    assert {row["task_id"] for row in view["tasks"]} == {"alpha", "beta"}
    for row in view["tasks"]:
        assert row["is_done"] is True
        assert row["agent_id"] == "control-agent"
        assert row["last_attempt"]["acceptance_passed"] is True
        assert row["acceptance"], "a viewer must be able to show what was checked"

    assert view["limits"]["launches_used"] == 2
    assert view["limits"]["launches_remaining"] >= 0
    assert "not billed spend" in view["limits"]["estimated_cost_note"]
    assert view["program"]["complete"] is True
    assert view["recent_events"], "the view carries recent history"
    assert view["ownership"] == [], "finished work holds no lease"


def test_reading_the_view_never_mutates_state(tmp_path: Path) -> None:
    """Closing and reopening a viewer changes nothing about the run."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    _run(tmp_path, program)
    root = tmp_path / "state"
    before_state = (state_dir(root) / "state.json").read_bytes()
    before_events = (state_dir(root) / "events.jsonl").read_bytes()

    for _ in range(5):
        control.control_view(root, load_program(program))

    assert (state_dir(root) / "state.json").read_bytes() == before_state
    assert (state_dir(root) / "events.jsonl").read_bytes() == before_events


def test_the_contract_names_what_it_cannot_do(tmp_path: Path) -> None:
    """A UI should render honestly, not imply capabilities nobody has."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    _run(tmp_path, program)
    view = control.control_view(tmp_path / "state", load_program(program))

    unsupported = view["actions"]["unsupported"]
    for forbidden in (
        "grant_owner_gate",
        "raise_limits",
        "merge",
        "adopt_running_process",
        "edit_task",
    ):
        assert forbidden in unsupported
        assert unsupported[forbidden], "each refusal must say why"
    assert set(view["actions"]["supported"]) == {
        "pause",
        "resume",
        "cancel",
        "reconcile",
    }


def test_an_unsupported_action_is_refused_with_its_reason(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    _run(tmp_path, program)
    with pytest.raises(control.ControlError) as excinfo:
        control.request_action(
            tmp_path / "state",
            load_program(program),
            action="grant_owner_gate",
            requested_by="studio",
        )
    assert excinfo.value.code == "ACTION_NOT_SUPPORTED"
    assert "owner gate" in str(excinfo.value)


def test_owner_gates_appear_in_the_view_and_stay_ungrantable(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace, owner_gated="beta")
    _run(tmp_path, program)
    view = control.control_view(tmp_path / "state", load_program(program))

    gated = next(row for row in view["tasks"] if row["task_id"] == "beta")
    assert gated["owner_gate"] == "B_ACCEPTANCE_WAIVER"
    assert gated["is_done"] is False
    assert view["status"]["owner_decision_required"]


# ------------------------------------------------------------ pause/resume


def test_pause_stops_new_work_and_is_reversible(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace, tasks=["alpha", "beta", "gamma"])
    loaded = load_program(program)
    root = tmp_path / "state"

    supervisor = ProgramSupervisor(loaded, state_root=root, sleeper=lambda _s: None)
    supervisor.load_or_init_state()

    paused = control.pause(root, requested_by="wesley")
    assert paused["paused"] is True
    assert paused["reversible"] is True

    report = ProgramSupervisor(loaded, state_root=root, sleeper=lambda _s: None).start()
    assert report.stop_reason is ProgramStopReason.PAUSED
    assert report.launches_this_run == 0

    resumed = control.resume(root, requested_by="wesley")
    assert resumed["paused"] is False

    report2 = ProgramSupervisor(loaded, state_root=root, sleeper=lambda _s: None).start()
    assert report2.stop_reason is ProgramStopReason.PROGRAM_COMPLETE
    assert report2.launches_this_run == 3


def test_pause_is_not_cancel(tmp_path: Path) -> None:
    """Distinct states, distinct effects, and one is reversible."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    loaded = load_program(program)
    root = tmp_path / "state"
    ProgramSupervisor(loaded, state_root=root).load_or_init_state()

    control.request_action(
        root, loaded, action="cancel", requested_by="wesley"
    )
    with pytest.raises(control.ControlError) as excinfo:
        control.resume(root, requested_by="wesley")
    assert excinfo.value.code == "CANNOT_RESUME_CANCELLED"

    state = load_state(root)
    assert state is not None
    assert state.cancel_requested is True
    assert state.paused is False


def test_a_paused_program_lets_running_workers_finish(tmp_path: Path) -> None:
    """Pause must not manufacture uncertain outcomes.

    Interrupting a running worker turns a reversible operator decision into
    something that needs reconciling, which is what cancel is for.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace, tasks=["alpha", "beta"])
    loaded = load_program(program)
    root = tmp_path / "state"
    supervisor = ProgramSupervisor(loaded, state_root=root, sleeper=lambda _s: None)

    original = supervisor._begin_dispatch

    def pause_after_launch(state: Any, choice: Any, result: Any) -> Any:
        running = original(state, choice, result)
        if running is not None:
            state.paused = True
            state.paused_by = "wesley"
        return running

    supervisor._begin_dispatch = pause_after_launch  # type: ignore[method-assign]
    report = supervisor.start()

    assert report.stop_reason is ProgramStopReason.PAUSED
    state = load_state(root)
    assert state is not None
    # The worker that was already running finished properly.
    assert len(state.attempts) == 1
    attempt = next(iter(state.attempts.values()))
    assert attempt.acceptance_passed is True
    assert attempt.confidence is not None and attempt.confidence.value == "CONFIRMED"
    assert state.tasks["alpha"].state is NodeState.CERTIFIED


def test_resume_before_start_needs_state(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    with pytest.raises(control.ControlError) as excinfo:
        control.pause(tmp_path / "state", requested_by="wesley")
    assert excinfo.value.code == "NO_STATE"
    _ = program


# ----------------------------------------------------------- studio closing


def test_closing_a_viewer_does_not_touch_the_supervisor(tmp_path: Path) -> None:
    """The supervisor is a separate process; a viewer cannot reach it.

    Asserted rather than assumed: the read path never writes the stop file,
    never clears one, and never touches the service identity.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    loaded = load_program(program)
    root = tmp_path / "state"
    _run(tmp_path, program)
    identity = service.write_own_identity(root, loaded)

    stop_file = state_dir(root) / "supervisor.stop"
    for _ in range(3):
        view = control.control_view(root, loaded)
        assert view["supervisor"]["service_alive"] is True
    assert not stop_file.is_file(), "reading must never request a stop"
    assert service.read_identity(root) == identity


def test_cancel_through_the_contract_is_the_same_governed_request(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    loaded = load_program(program)
    root = tmp_path / "state"
    ProgramSupervisor(loaded, state_root=root).load_or_init_state()

    report = control.request_action(
        root, loaded, action="cancel", requested_by="studio-user"
    )
    assert report["cancel_requested"] is True
    assert report["requested_by"] == "studio-user"

    state = load_state(root)
    assert state is not None
    assert state.cancel_requested is True

    result = ProgramSupervisor(loaded, state_root=root, sleeper=lambda _s: None).start()
    assert result.stop_reason is ProgramStopReason.CANCELLED
    assert result.launches_this_run == 0


def test_reconcile_through_the_contract_reports_interrupted_attempts(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    loaded = load_program(program)
    root = tmp_path / "state"
    supervisor = ProgramSupervisor(loaded, state_root=root, sleeper=lambda _s: None)
    state = supervisor.load_or_init_state()
    from project_atlas.orchestration.program.models import (
        AttemptPhase,
        ExecutionConfidence,
    )
    from project_atlas.orchestration.program.store import AttemptRecord

    state.attempts["stranded"] = AttemptRecord(
        attempt_id="stranded",
        task_id="alpha",
        attempt_number=1,
        idempotency_key="k",
        profile_id="impl",
        agent_id="control-agent",
        adapter="local-command",
        profile_digest="a" * 64,
        base_pin=state.base_pin,
        phase=AttemptPhase.ADAPTER_INVOKED,
        confidence=ExecutionConfidence.UNCERTAIN,
    )
    persist_state(root, state)

    report = control.request_action(
        root, loaded, action="reconcile", requested_by="studio-user"
    )
    assert report["interrupted_attempts"]
    assert report["interrupted_attempts"][0]["attempt_id"] == "stranded"
    assert report["resolved"] is None, "inspecting must not settle anything"

    view = control.control_view(root, loaded)
    assert view["needs_reconciliation"][0]["attempt_id"] == "stranded"


# ------------------------------------------------------------------- CLI


def test_cli_control_prints_the_contract(tmp_path: Path, capsys: Any) -> None:
    from project_atlas.orchestration.program.cli import main

    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    _run(tmp_path, program)

    code = main(
        [
            "program", "control",
            "--program", str(program),
            "--state-root", str(tmp_path / "state"),
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["contract_version"] == control.CONTRACT_VERSION
    assert payload["read_only"] is True


def test_cli_control_pause_and_resume(tmp_path: Path, capsys: Any) -> None:
    from project_atlas.orchestration.program.cli import main

    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    _run(tmp_path, program)
    args = [
        "program", "control",
        "--program", str(program),
        "--state-root", str(tmp_path / "state"),
        "--requested-by", "studio",
    ]
    assert main([*args, "--action", "pause"]) == 0
    assert json.loads(capsys.readouterr().out)["paused"] is True
    assert main([*args, "--action", "resume"]) == 0
    assert json.loads(capsys.readouterr().out)["paused"] is False


def test_the_view_answers_the_operator_questions(tmp_path: Path) -> None:
    """Progress, waiting, retry eligibility and required actions, per task."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace, owner_gated="beta")
    _run(tmp_path, program)
    view = control.control_view(tmp_path / "state", load_program(program))

    done = next(row for row in view["tasks"] if row["task_id"] == "alpha")
    assert done["last_meaningful_progress"]["acceptance_passed"] is True
    assert done["last_meaningful_progress"]["workspace_fingerprint"]
    assert "not progress" in done["last_meaningful_progress"]["note"]
    assert done["retry"]["attempt_budget"] >= 1
    assert done["retry"]["last_failure_class"] is None

    gated = next(row for row in view["tasks"] if row["task_id"] == "beta")
    assert gated["last_meaningful_progress"] is None, "it never ran"
    assert gated["waiting_condition"] is None

    actions = view["required_operator_actions"]
    assert any(
        row["action"] == "owner_decision" and row["task_id"] == "beta"
        for row in actions
    )


def test_required_actions_stay_quiet_on_a_clean_run(tmp_path: Path) -> None:
    """An alert that fires on routine progress trains its reader to ignore it."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    _run(tmp_path, program)
    view = control.control_view(tmp_path / "state", load_program(program))
    assert view["required_operator_actions"] == []
    assert view["program"]["complete"] is True


def test_a_paused_program_asks_to_be_resumed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    root = tmp_path / "state"
    ProgramSupervisor(load_program(program), state_root=root).load_or_init_state()
    control.pause(root, requested_by="wesley")
    view = control.control_view(root, load_program(program))
    resume_action = next(
        row for row in view["required_operator_actions"] if row["action"] == "resume"
    )
    assert "wesley" in resume_action["why"]
    assert "--action resume" in resume_action["command"]


def test_retry_eligibility_distinguishes_permanent_from_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace, tasks=["alpha"])
    monkeypatch.setenv("ATLAS_FIXTURE_MODE", "exit:22")  # POLICY_REFUSAL
    _run(tmp_path, program)
    view = control.control_view(tmp_path / "state", load_program(program))
    row = next(r for r in view["tasks"] if r["task_id"] == "alpha")
    assert row["retry"]["eligible"] is False
    assert row["retry"]["last_failure_class"] == "POLICY_REFUSAL"
    assert "never retried" in row["retry"]["reason"]
    assert any(
        action["action"] == "investigate_blocked_task"
        for action in view["required_operator_actions"]
    )


def test_pausing_an_unstarted_program_creates_no_dispatch(tmp_path: Path) -> None:
    """Regression from the operator journey.

    An earlier draft of the journey ran a throwaway `start` to create the
    durable record before pausing -- which executed the whole program and made
    the next step's narrative false. Pausing an unstarted program is
    legitimate, is the safest moment to pause one, and must initialise the
    record without dispatching anything.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    loaded = load_program(program)
    root = tmp_path / "state"

    assert load_state(root) is None
    paused = control.pause(root, requested_by="wesley", loaded=loaded)
    assert paused["paused"] is True
    assert paused["still_running"] == []

    state = load_state(root)
    assert state is not None
    assert state.attempts == {}, "pausing must not have launched anything"
    assert state.total_launches == 0

    report = ProgramSupervisor(loaded, state_root=root, sleeper=lambda _s: None).start()
    assert report.stop_reason is ProgramStopReason.PAUSED
    assert report.launches_this_run == 0

    control.resume(root, requested_by="wesley", loaded=loaded)
    resumed = ProgramSupervisor(
        loaded, state_root=root, sleeper=lambda _s: None
    ).start()
    assert resumed.stop_reason is ProgramStopReason.PROGRAM_COMPLETE
    assert resumed.launches_this_run == 2


def test_pause_without_a_program_still_refuses_when_it_cannot_initialise(
    tmp_path: Path,
) -> None:
    with pytest.raises(control.ControlError) as excinfo:
        control.pause(tmp_path / "nowhere", requested_by="wesley")
    assert excinfo.value.code == "NO_STATE"


def test_no_notification_asks_permission_to_continue_authorised_work() -> None:
    """Checkpoint notifications inform; they do not ask to carry on.

    The program was approved once. A notification that reads as "may I
    continue?" turns an approved program back into a prompt loop, which is the
    exact failure this supervisor exists to remove. Parsed from the source so a
    future notification cannot quietly reintroduce it.
    """
    import re
    from pathlib import Path as _Path

    source = (
        _Path(__file__).resolve().parents[2]
        / "src"
        / "project_atlas"
        / "orchestration"
        / "program"
        / "supervisor.py"
    ).read_text(encoding="utf-8")
    sites = re.findall(
        r'self\._notify(?:_unless_busy)?\(\s*"([A-Z_]+)",\s*\(?\s*((?:"[^"]*"\s*)+)',
        source,
    )
    assert len(sites) >= 10, "the audit found no notification sites; the regex drifted"

    asking = (
        "should i",
        "may i",
        "can i",
        "shall i",
        "permission to continue",
        "would you like",
        "do you want",
        "proceed?",
        "ok to continue",
        "let me know if",
    )
    offenders = []
    for kind, literal in sites:
        message = " ".join(re.findall(r'"([^"]*)"', literal)).lower()
        if any(phrase in message for phrase in asking):
            offenders.append((kind, message))
    assert offenders == [], (
        "a checkpoint notification reads as a request for permission to "
        f"continue already-authorised work: {offenders}"
    )


def test_the_notifications_that_do_exist_name_a_real_decision() -> None:
    """The ones that stop the program point at something only a person can do."""
    from project_atlas.orchestration.program.models import ProgramStopReason
    from project_atlas.orchestration.program.supervisor import (
        _NOT_TERMINAL_WHILE_RUNNING,
    )

    # Every provisional reason is one the supervisor re-derives itself rather
    # than asking about, which is why it may be cleared while workers run.
    assert ProgramStopReason.NO_ELIGIBLE_WORK in _NOT_TERMINAL_WHILE_RUNNING
    assert ProgramStopReason.PROGRAM_COMPLETE not in _NOT_TERMINAL_WHILE_RUNNING
    assert ProgramStopReason.CANCELLED not in _NOT_TERMINAL_WHILE_RUNNING
    assert ProgramStopReason.RECONCILE_REQUIRED not in _NOT_TERMINAL_WHILE_RUNNING
