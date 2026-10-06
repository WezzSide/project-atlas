"""AS-ORCH-PROGRAM-SUPERVISOR-001 M5 — durable execution.

The supervisor as a service: explicit installation, explicit activation,
recovery that is not replay, and failure states that are named rather than
lumped together.

`SERVICE_INSTALLED != SERVICE_RUNNING != PROGRAM_AUTHORIZED`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from project_atlas.orchestration.autonomy.models import NodeState
from project_atlas.orchestration.program import service
from project_atlas.orchestration.program.adapters.base import AdapterOutcome
from project_atlas.orchestration.program.enrollment import (
    AgentStatus,
    EnrollmentError,
    assign,
    bind,
    enroll,
    load_registry,
)
from project_atlas.orchestration.program.loader import load_program
from project_atlas.orchestration.program.models import (
    ExecutionConfidence,
    FailureClass,
    ProgramStopReason,
)
from project_atlas.orchestration.program.profiles import AdapterKind
from project_atlas.orchestration.program.store import load_state, state_dir
from project_atlas.orchestration.program.supervisor import ProgramSupervisor

FIXTURE_WORKER = Path(__file__).with_name("_program_fixture_worker.py")
REPO_SRC = Path(__file__).resolve().parents[2] / "src"


def _profile(agent_id: str = "svc-agent", **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "agent_id": agent_id,
        "adapter": "local-command",
        "credential": "NOT_APPLICABLE",
        "capabilities": ["IMPLEMENT"],
        "env_allowlist": [
            "ATLAS_FIXTURE_MODE",
            "ATLAS_FIXTURE_TARGET",
            "ATLAS_PROGRAM_ATTEMPT",
            "ATLAS_PROGRAM_TASK",
        ],
        "adapter_options": {"argv": [sys.executable, str(FIXTURE_WORKER)]},
    }
    body.update(overrides)
    return body


def _program(
    tmp_path: Path,
    workspace: Path,
    *,
    tasks: list[str] | None = None,
    profiles: dict[str, dict[str, Any]] | None = None,
    limits: dict[str, Any] | None = None,
) -> Path:
    names = tasks or ["only"]
    base_limits: dict[str, Any] = {"max_cycles": 10, "idle_sleep_seconds": 0.0}
    base_limits.update(limits or {})
    payload = {
        "schema_version": 1,
        "program": {
            "program_id": "service-program",
            "objective": "service coverage",
            "approved_by": "test",
            "approval_reference": "test",
            "workspace_root": str(workspace),
            "base_pin": "0" * 40,
            "limits": base_limits,
            "tasks": [
                {
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
                for name in names
            ],
        },
        "profiles": profiles or {"impl": _profile()},
    }
    path = tmp_path / "program.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _fixture_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_FIXTURE_MODE", "write")
    monkeypatch.delenv("ATLAS_FIXTURE_TARGET", raising=False)


# ------------------------------------------------------ install / activate


def test_install_writes_a_launcher_and_activates_nothing(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    report = service.install(tmp_path / "state", program)

    assert report["installed"] is True
    assert report["activated"] is False
    launcher = Path(report["launcher"])
    assert launcher.is_file()
    assert os.access(launcher, os.X_OK)
    text = launcher.read_text(encoding="utf-8")
    assert "program service run" in text
    assert "does not merge" in text.lower()

    unit = Path(report["systemd_unit_file"])
    assert unit.is_file()
    assert "ExecStart=" in unit.read_text(encoding="utf-8")
    # The activation instructions are printed, never executed.
    assert "systemctl --user enable" in report["activation_note"]
    assert "SERVICE_INSTALLED != SERVICE_RUNNING" in report["truth_boundary"]


def test_install_persists_registry_binding_in_launcher(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    registry = tmp_path / "registry"
    report = service.install(tmp_path / "state", program, registry_root=registry)

    launcher = Path(report["launcher"]).read_text(encoding="utf-8")
    assert f"--registry {str(registry.resolve())!r}" in launcher


def test_status_before_anything_started(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    report = service.status(tmp_path / "state", program)
    assert report["service"] is None
    assert report["service_alive"] is False
    assert report["program_status"]["started"] is False


# ------------------------------------------------------------ the service


def test_service_run_drives_the_program_to_completion(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace, tasks=["alpha", "beta"])
    report = service.run(
        tmp_path / "state", program, poll_seconds=0.0, max_rounds=4, sleeper=lambda _s: None
    )
    assert report["program_complete"] is True
    assert report["final_stop_reason"] == "PROGRAM_COMPLETE"
    assert report["merge_authorized"] is False
    # The service recorded its own identity, from inside its own process.
    identity = service.read_identity(tmp_path / "state")
    assert identity is not None
    assert identity.pid == os.getpid()


def test_the_service_records_its_own_identity_not_a_launchers(
    tmp_path: Path,
) -> None:
    """A launcher's child is not necessarily the supervisor.

    Recording the pid a spawn returned, rather than the pid the service writes
    from inside itself, is a defect class this repository has hit before
    (PR #766): the recorded identity names a shim that has already exited, and
    "is the supervisor running?" becomes unanswerable.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_SRC) + os.pathsep + env.get("PYTHONPATH", "")
    env["ATLAS_FIXTURE_MODE"] = "write"
    env.pop("ATLAS_FIXTURE_TARGET", None)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "project_atlas.orchestration.program.cli",
            "program",
            "service",
            "run",
            "--program",
            str(program),
            "--state-root",
            str(tmp_path / "state"),
            "--max-rounds",
            "2",
                "--poll-seconds",
                "0",
                "--allow-unregistered",
            ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        env=env,
        cwd=str(workspace),
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    payload = json.loads(completed.stdout)
    identity = service.read_identity(tmp_path / "state")
    assert identity is not None
    # The identity belongs to the process that ran the service, not to this one.
    assert identity.pid == payload["service"]["pid"]
    assert identity.pid != os.getpid()
    assert payload["program_complete"] is True


def test_stop_is_honoured_before_the_next_round(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace, tasks=["alpha", "beta"])
    service.stop(tmp_path / "state")
    report = service.run(
        tmp_path / "state", program, poll_seconds=0.0, max_rounds=3, sleeper=lambda _s: None
    )
    assert report["final_stop_reason"] == "STOP_REQUESTED"
    assert report["program_complete"] is False
    state = load_state(tmp_path / "state")
    assert state is None or not state.attempts


def test_a_terminal_stop_reason_does_not_loop(tmp_path: Path) -> None:
    """A program waiting on a person is not retried every poll interval."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(
        tmp_path,
        workspace,
        tasks=["alpha"],
        limits={"max_task_launches": 1},
    )
    payload = json.loads(program.read_text(encoding="utf-8"))
    payload["program"]["tasks"][0]["owner_gate"] = "C_CERTIFIED_OBJECT_MUTATION"
    program.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    sleeps: list[float] = []
    report = service.run(
        tmp_path / "state",
        program,
        poll_seconds=99.0,
        max_rounds=5,
        sleeper=sleeps.append,
    )
    assert report["final_stop_reason"] == "OWNER_DECISION_REQUIRED"
    assert len(report["rounds"]) == 1, "it must not keep re-running an owner gate"
    assert sleeps == []


def test_service_start_refuses_a_second_live_service(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    # Claim the identity as this very process, which is trivially alive.
    loaded = load_program(program)
    service.write_own_identity(tmp_path / "state", loaded)
    with pytest.raises(service.ServiceError) as excinfo:
        service.start(tmp_path / "state", program)
    assert excinfo.value.code == "SERVICE_ALREADY_RUNNING"


def test_detached_start_requires_explicit_registry_binding(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)

    with pytest.raises(service.ServiceError) as excinfo:
        service.start(tmp_path / "state", program)

    assert excinfo.value.code == "REGISTRY_REQUIRED"


def test_detached_start_refuses_missing_assignment_before_spawning(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    registry = tmp_path / "registry"

    with pytest.raises(service.ServiceError) as excinfo:
        service.start(tmp_path / "state", program, registry_root=registry)

    assert excinfo.value.code == "REGISTRY_BINDING_MISSING"
    assert not (tmp_path / "state" / "service" / "service.json").exists()


def test_service_run_binds_assigned_registry_agent(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    registry = tmp_path / "registry"
    agent = enroll(
        registry,
        agent_id="svc-agent",
        role="impl",
        adapter=AdapterKind.LOCAL_COMMAND,
        workspace_root=workspace,
        enrolled_by="test",
    )
    assert agent.assigned_program is None
    assign(
        registry,
        agent_id="svc-agent",
        program_path=program,
        assigned_by="test",
    )

    report = service.run(
        tmp_path / "state",
        program,
        registry_root=registry,
        poll_seconds=0.0,
        max_rounds=1,
        sleeper=lambda _s: None,
    )

    assert report["program_complete"] is True
    assert report["rounds"][0]["launches_this_run"] == 1


def test_service_run_can_refuse_missing_registry_binding(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)

    with pytest.raises(service.ServiceError) as excinfo:
        service.run(
            tmp_path / "state",
            program,
            allow_unregistered=False,
            poll_seconds=0.0,
            max_rounds=1,
            sleeper=lambda _s: None,
        )

    assert excinfo.value.code == "REGISTRY_REQUIRED"


@pytest.mark.parametrize("status", [AgentStatus.SUSPENDED, AgentStatus.RETIRED])
def test_service_run_refuses_non_active_assignment_without_launch(
    tmp_path: Path, status: AgentStatus
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    registry = tmp_path / "registry"
    enroll(
        registry,
        agent_id="svc-agent",
        role="impl",
        adapter=AdapterKind.LOCAL_COMMAND,
        workspace_root=workspace,
        enrolled_by="test",
    )
    assign(
        registry,
        agent_id="svc-agent",
        program_path=program,
        assigned_by="test",
    )
    from project_atlas.orchestration.program.enrollment import set_status

    set_status(registry, agent_id="svc-agent", status=status)

    with pytest.raises(service.ServiceError) as excinfo:
        service.run(
            tmp_path / "state",
            program,
            registry_root=registry,
            poll_seconds=0.0,
            max_rounds=1,
            sleeper=lambda _s: None,
        )

    assert excinfo.value.code == "REGISTRY_BINDING_MISSING"
    assert not list(workspace.glob("*.txt"))


def test_service_run_refuses_assignment_to_a_different_program(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    other_root = tmp_path / "other"
    other_root.mkdir()
    other = _program(other_root, workspace)
    registry = tmp_path / "registry"
    enroll(
        registry,
        agent_id="svc-agent",
        role="impl",
        adapter=AdapterKind.LOCAL_COMMAND,
        workspace_root=workspace,
        enrolled_by="test",
    )
    assign(
        registry,
        agent_id="svc-agent",
        program_path=other,
        assigned_by="test",
    )

    with pytest.raises(service.ServiceError) as excinfo:
        service.run(
            tmp_path / "state",
            program,
            registry_root=registry,
            poll_seconds=0.0,
            max_rounds=1,
            sleeper=lambda _s: None,
        )

    assert excinfo.value.code == "REGISTRY_BINDING_MISSING"
    assert not list(workspace.glob("*.txt"))


def test_a_stale_identity_does_not_look_alive(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    loaded = load_program(_program(tmp_path, workspace))
    identity = service.write_own_identity(tmp_path / "state", loaded)
    assert service.service_is_alive(identity) is True

    stale = service.ServiceIdentity(
        pid=identity.pid,
        process_start_identity="a-start-identity-from-a-different-process",
        program_id=identity.program_id,
        program_path=identity.program_path,
        started_at=identity.started_at,
    )
    assert service.service_is_alive(stale) is False, "PID reuse must not look alive"

    unknown = service.ServiceIdentity(
        pid=identity.pid,
        process_start_identity="unknown",
        program_id=identity.program_id,
        program_path=identity.program_path,
        started_at=identity.started_at,
    )
    assert service.service_is_alive(unknown) is False


def test_a_reader_closing_does_not_affect_the_run(tmp_path: Path) -> None:
    """UI closure is not a supervisor event.

    Status is a read of durable state by a separate process. Nothing about
    reading it, or stopping reading it, touches ownership, attempts or the
    service.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace, tasks=["alpha", "beta"])
    service.run(
        tmp_path / "state", program, poll_seconds=0.0, max_rounds=4, sleeper=lambda _s: None
    )
    before = (state_dir(tmp_path / "state") / "state.json").read_bytes()
    for _ in range(3):
        report = service.status(tmp_path / "state", program)
        assert report["program_status"]["program_complete"] is True
    after = (state_dir(tmp_path / "state") / "state.json").read_bytes()
    assert before == after, "reading status must not mutate program state"


# -------------------------------------------------------- explicit states


def test_a_denied_run_that_fails_acceptance_is_a_policy_refusal(
    tmp_path: Path,
) -> None:
    """Retrying would be denied the same thing, so it is not retried."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace, limits={"max_attempts_per_task": 4})
    loaded = load_program(program)

    class _DeniedAdapter:
        @property
        def capabilities(self) -> Any:
            from project_atlas.orchestration.program.adapters.base import (
                AdapterCapabilities,
            )

            return AdapterCapabilities(
                adapter_id="denied",
                supports_resume=False,
                supports_session_probe=True,
                accepts_assigned_session=True,
                supports_cost_limit=False,
                reports_cost=False,
                supports_result_schema=False,
                version="FIXTURE",
            )

        def preflight(self, profile: Any) -> None:
            _ = profile

        def probe_run_started(self, request: Any) -> bool | None:
            _ = request
            return None

        def run(self, request: Any) -> AdapterOutcome:
            # A clean exit, a confident report, and two denied tool requests.
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
                reported="I could not write the file.",
                structured=None,
                usage={},
                estimated_cost_usd=None,
                evidence=(),
                failure_class=None,
                duration_seconds=0.0,
                policy_denials=2,
            )

    supervisor = ProgramSupervisor(
        loaded,
        state_root=tmp_path / "state",
        adapters={"impl": _DeniedAdapter()},
        sleeper=lambda _s: None,
    )
    report = supervisor.start()
    state = load_state(tmp_path / "state")
    assert state is not None
    assert state.tasks["only"].last_failure_class is FailureClass.POLICY_REFUSAL
    assert state.tasks["only"].state is NodeState.BLOCKED
    assert report.launches == 1, "a denied worker must not be retried"
    assert any(row["kind"] == "POLICY_REFUSAL" for row in report.notifications)
    assert next(iter(state.attempts.values())).policy_denials == 2


def test_an_unavailable_runtime_is_a_named_state_not_a_crash(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(
        tmp_path,
        workspace,
        profiles={
            "impl": {
                **_profile(),
                "adapter": "claude-code",
                "credential": "SUBSCRIPTION_OAUTH",
                "adapter_min_version": "999.0.0",
                "adapter_options": {},
            }
        },
    )
    supervisor = ProgramSupervisor(
        load_program(program), state_root=tmp_path / "state", sleeper=lambda _s: None
    )
    report = supervisor.start()
    assert report.stop_reason is ProgramStopReason.HARD_BLOCKER
    kinds = {row["kind"] for row in report.notifications}
    assert "RUNTIME_UNAVAILABLE" in kinds
    state = load_state(tmp_path / "state")
    assert state is not None
    assert state.tasks["only"].state is NodeState.BLOCKED
    assert report.launches == 0


# ---------------------------------------------------- runtime substitution


def test_runtime_substitution_authorization_is_durable_not_a_launch_flag(
    tmp_path: Path,
) -> None:
    """The grant must survive as a record, and must gate the launch path too.

    An earlier version passed allow_runtime_substitution=True unconditionally
    when binding enrolled agents at launch, which meant `assign` could refuse
    to record an assignment while the supervisor happily ran it anyway.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    loaded = load_program(program)
    registry = tmp_path / "registry"

    agent = enroll(
        registry,
        agent_id="codex-agent",
        role="impl",
        adapter=AdapterKind.CODEX,
        workspace_root=workspace,
        enrolled_by="wesley",
    )
    assert agent.runtime_substitution_authorized is False
    with pytest.raises(EnrollmentError):
        bind(agent, loaded, allow_runtime_substitution=False)

    # Without the grant, the supervisor refuses at launch too.
    with pytest.raises(Exception) as excinfo:
        ProgramSupervisor(
            loaded, state_root=tmp_path / "state", enrolled_agents=(agent,)
        )
    assert "RUNTIME_SUBSTITUTION_NOT_AUTHORIZED" in str(
        getattr(excinfo.value, "code", "")
    )

    granted, _loaded = assign(
        registry,
        agent_id="codex-agent",
        program_path=program,
        assigned_by="wesley",
        allow_runtime_substitution=True,
    )
    assert granted.runtime_substitution_authorized is True
    assert granted.runtime_substitution_authorized_by == "wesley"
    supervisor = ProgramSupervisor(
        loaded, state_root=tmp_path / "state", enrolled_agents=(granted,)
    )
    assert supervisor.loaded.effective_profile("only").adapter is AdapterKind.CODEX


def test_re_enrolling_does_not_inherit_a_substitution_grant(
    tmp_path: Path,
) -> None:
    """A grant given for one agent record must not outlive that record."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    registry = tmp_path / "registry"
    enroll(
        registry,
        agent_id="a",
        role="impl",
        adapter=AdapterKind.CODEX,
        workspace_root=workspace,
        enrolled_by="wesley",
    )
    granted, _loaded = assign(
        registry,
        agent_id="a",
        program_path=program,
        assigned_by="wesley",
        allow_runtime_substitution=True,
    )
    assert granted.runtime_substitution_authorized is True

    replaced = enroll(
        registry,
        agent_id="a",
        role="impl",
        adapter=AdapterKind.CODEX,
        workspace_root=workspace,
        enrolled_by="wesley",
        replace=True,
    )
    assert replaced.runtime_substitution_authorized is False
    assert replaced.assigned_program is not None, "the assignment itself survives"
    assert load_registry(registry).agents["a"].runtime_substitution_authorized is False


def test_a_suspended_agent_is_still_enrolled(tmp_path: Path) -> None:
    """Suspension withholds dispatch; it is not a deletion."""
    from project_atlas.orchestration.program.enrollment import set_status

    workspace = tmp_path / "ws"
    workspace.mkdir()
    registry = tmp_path / "registry"
    enroll(
        registry,
        agent_id="a",
        role="impl",
        adapter=AdapterKind.LOCAL_COMMAND,
        workspace_root=workspace,
        enrolled_by="wesley",
    )
    set_status(registry, agent_id="a", status=AgentStatus.SUSPENDED)
    agent = load_registry(registry).agents["a"]
    assert agent.status is AgentStatus.SUSPENDED
    assert agent.enrolled_by == "wesley"
    assert agent.enrolled_at


def test_recovery_after_a_service_round_is_not_replay(tmp_path: Path) -> None:
    """A second round re-reconciles; it never re-runs a completed task."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace, tasks=["alpha", "beta"])
    first = service.run(
        tmp_path / "state",
        program,
        poll_seconds=0.0,
        max_rounds=1,
        sleeper=lambda _s: None,
    )
    launched_first = first["rounds"][0]["launches_this_run"]
    assert launched_first == 2

    second = service.run(
        tmp_path / "state",
        program,
        poll_seconds=0.0,
        max_rounds=1,
        sleeper=lambda _s: None,
    )
    assert second["rounds"][0]["launches_this_run"] == 0, (
        "a completed program must not be replayed"
    )
    assert second["program_complete"] is True

    state = load_state(tmp_path / "state")
    assert state is not None
    assert len(state.attempts) == 2
    assert state.total_launches == 2


def test_an_invalid_program_still_prints_one_json_object(
    tmp_path: Path, capsys: Any
) -> None:
    """The CLI contract is one JSON object per command, errors included.

    Found while installing a real service: a hyphen in `surface_semantic`
    produced a raw pydantic traceback on stdout, which breaks the contract for
    exactly the reader most likely to hit it -- someone writing their first
    program file.
    """
    from project_atlas.orchestration.program.cli import main

    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    payload = json.loads(program.read_text(encoding="utf-8"))
    payload["program"]["tasks"][0]["surface_semantic"] = "not-uppercase"
    program.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    code = main(["program", "validate", "--program", str(program)])
    assert code == 1
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["code"] == "PROGRAM_INVALID"
    assert "surface_semantic" in parsed["error"]
    # The rejected value itself is not echoed: a program file can carry
    # arbitrary content and an error message is not the place to reprint it.
    assert "not-uppercase" not in parsed["error"]
    assert parsed["merge_authorized"] is False


def test_an_invalid_profile_is_reported_the_same_way(
    tmp_path: Path, capsys: Any
) -> None:
    from project_atlas.orchestration.program.cli import main

    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _program(tmp_path, workspace)
    payload = json.loads(program.read_text(encoding="utf-8"))
    payload["profiles"]["impl"]["capabilities"] = ["NOT_A_CAPABILITY"]
    program.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    code = main(["program", "validate", "--program", str(program)])
    assert code == 1
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["code"] == "PROFILE_INVALID"
