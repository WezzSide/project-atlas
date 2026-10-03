"""AS-ORCH-PROGRAM-SUPERVISOR-001 M3 — enrollment and the four identities.

The property under test is separation. Agent identity, runtime/session
identity, task ownership and supervisor process lifecycle have four different
lifetimes, and every test here fails if two of them are conflated.

`ENROLLMENT != AUTHORIZATION`. `NEVER SILENTLY ADOPT`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from project_atlas.orchestration.autonomy.models import NodeState
from project_atlas.orchestration.program.enrollment import (
    AgentStatus,
    EnrollmentError,
    assign,
    bind,
    enroll,
    identity_view,
    load_registry,
    set_status,
)
from project_atlas.orchestration.program.loader import load_program
from project_atlas.orchestration.program.models import ProgramStopReason
from project_atlas.orchestration.program.profiles import AdapterKind
from project_atlas.orchestration.program.store import load_state
from project_atlas.orchestration.program.supervisor import (
    ProgramSupervisor,
    SupervisorError,
)

FIXTURE_WORKER = Path(__file__).with_name("_program_fixture_worker.py")


def _fixture_profile(agent_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "agent_id": agent_id,
        "adapter": "local-command",
        "credential": "NOT_APPLICABLE",
        "capabilities": ["IMPLEMENT"],
        "permission_mode": "acceptEdits",
        "allowed_tools": ["Read", "Edit", "Bash"],
        "limits": {"max_seconds": 120, "max_attempts": 3},
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


def _write_program(
    tmp_path: Path,
    workspace: Path,
    *,
    tasks: list[dict[str, Any]] | None = None,
    profiles: dict[str, dict[str, Any]] | None = None,
    name: str = "program.json",
) -> Path:
    payload = {
        "schema_version": 1,
        "program": {
            "program_id": "enrollment-program",
            "objective": "enrollment coverage",
            "approved_by": "test",
            "approval_reference": "test",
            "workspace_root": str(workspace),
            "base_pin": "0" * 40,
            "limits": {"max_cycles": 10, "idle_sleep_seconds": 0.0},
            "tasks": tasks
            or [
                {
                    "task_id": "only",
                    "title": "only task",
                    "instruction": "do it",
                    "profile_ref": "implementer",
                    "mutation_paths": ["only.txt"],
                    "surface_id": "only",
                    "surface_semantic": "ONLY",
                    "capabilities_required": ["IMPLEMENT"],
                    "acceptance": [
                        {
                            "check_id": "out",
                            "kind": "FILE_EXISTS",
                            "description": "only.txt exists",
                            "path": "only.txt",
                        }
                    ],
                }
            ],
        },
        "profiles": profiles or {"implementer": _fixture_profile("program-placeholder")},
    }
    path = tmp_path / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _fixture_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_FIXTURE_MODE", "write")
    monkeypatch.delenv("ATLAS_FIXTURE_TARGET", raising=False)


# ------------------------------------------------------------- registration


def test_enrolling_records_identity_and_grants_nothing(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    agent = enroll(
        tmp_path / "registry",
        agent_id="claude-impl",
        role="implementer",
        adapter=AdapterKind.CLAUDE_CODE,
        workspace_root=workspace,
        enrolled_by="wesley",
        description="the Claude worker",
    )
    assert agent.status is AgentStatus.ACTIVE
    assert agent.assigned_program is None
    assert agent.merge_authorized is False

    registry = load_registry(tmp_path / "registry")
    assert list(registry.agents) == ["claude-impl"]
    assert registry.merge_authorized is False


def test_re_enrolling_without_replace_is_refused(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    kwargs: dict[str, Any] = {
        "agent_id": "a",
        "role": "implementer",
        "adapter": AdapterKind.LOCAL_COMMAND,
        "workspace_root": workspace,
        "enrolled_by": "wesley",
    }
    enroll(tmp_path / "registry", **kwargs)
    with pytest.raises(EnrollmentError) as excinfo:
        enroll(tmp_path / "registry", **kwargs)
    assert excinfo.value.code == "AGENT_ALREADY_ENROLLED"


def test_replacing_an_enrollment_keeps_its_assignment(tmp_path: Path) -> None:
    """Correcting a description must not silently unassign an agent's work."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _write_program(tmp_path, workspace)
    enroll(
        tmp_path / "registry",
        agent_id="a",
        role="implementer",
        adapter=AdapterKind.LOCAL_COMMAND,
        workspace_root=workspace,
        enrolled_by="wesley",
    )
    assign(
        tmp_path / "registry",
        agent_id="a",
        program_path=program,
        assigned_by="wesley",
    )
    replaced = enroll(
        tmp_path / "registry",
        agent_id="a",
        role="implementer",
        adapter=AdapterKind.LOCAL_COMMAND,
        workspace_root=workspace,
        enrolled_by="wesley",
        description="a better description",
        replace=True,
    )
    assert replaced.assigned_program is not None
    assert replaced.description == "a better description"


def test_a_suspended_agent_cannot_be_assigned_work(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _write_program(tmp_path, workspace)
    enroll(
        tmp_path / "registry",
        agent_id="a",
        role="implementer",
        adapter=AdapterKind.LOCAL_COMMAND,
        workspace_root=workspace,
        enrolled_by="wesley",
    )
    set_status(tmp_path / "registry", agent_id="a", status=AgentStatus.SUSPENDED)
    with pytest.raises(EnrollmentError) as excinfo:
        assign(
            tmp_path / "registry",
            agent_id="a",
            program_path=program,
            assigned_by="wesley",
        )
    assert excinfo.value.code == "AGENT_NOT_ACTIVE"


# ------------------------------------------------------------------ binding


def test_an_enrollment_may_narrow_and_may_never_widen(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _write_program(tmp_path, workspace)
    loaded = load_program(program)

    narrowed = enroll(
        tmp_path / "registry",
        agent_id="tight",
        role="implementer",
        adapter=AdapterKind.LOCAL_COMMAND,
        workspace_root=workspace,
        enrolled_by="wesley",
        profile_narrowing={
            "allowed_tools": ["Read"],
            "permission_mode": "dontAsk",
            "limits": {"max_seconds": 30},
        },
    )
    bound = bind(narrowed, loaded)
    assert bound.allowed_tools == ("Read",)
    assert bound.permission_mode == "dontAsk"
    assert bound.limits.max_seconds == 30
    # The principal is the agent's, the permissions are the program's, narrowed.
    assert bound.agent_id == "tight"

    wide = enroll(
        tmp_path / "registry",
        agent_id="wide",
        role="implementer",
        adapter=AdapterKind.LOCAL_COMMAND,
        workspace_root=workspace,
        enrolled_by="wesley",
        profile_narrowing={"permission_mode": "bypassPermissions"},
    )
    with pytest.raises(EnrollmentError) as excinfo:
        bind(wide, loaded)
    assert excinfo.value.code == "PERMISSION_MODE_ESCALATION"


def test_a_role_the_program_does_not_declare_is_refused(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    loaded = load_program(_write_program(tmp_path, workspace))
    agent = enroll(
        tmp_path / "registry",
        agent_id="a",
        role="reviewer",
        adapter=AdapterKind.LOCAL_COMMAND,
        workspace_root=workspace,
        enrolled_by="wesley",
    )
    with pytest.raises(EnrollmentError) as excinfo:
        bind(agent, loaded)
    assert excinfo.value.code == "ROLE_NOT_IN_PROGRAM"


def test_running_a_role_on_a_different_runtime_needs_an_explicit_decision(
    tmp_path: Path,
) -> None:
    """Switching runtimes is a decision, never a default."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    loaded = load_program(_write_program(tmp_path, workspace))
    agent = enroll(
        tmp_path / "registry",
        agent_id="codex-agent",
        role="implementer",
        adapter=AdapterKind.CODEX,
        workspace_root=workspace,
        enrolled_by="wesley",
    )
    with pytest.raises(EnrollmentError) as excinfo:
        bind(agent, loaded)
    assert excinfo.value.code == "RUNTIME_SUBSTITUTION_NOT_AUTHORIZED"

    substituted = bind(agent, loaded, allow_runtime_substitution=True)
    assert substituted.adapter is AdapterKind.CODEX


def test_assignment_refuses_a_binding_that_could_not_run(tmp_path: Path) -> None:
    """Refused now, not when a worker would have started."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _write_program(tmp_path, workspace)
    enroll(
        tmp_path / "registry",
        agent_id="a",
        role="nonexistent-role",
        adapter=AdapterKind.LOCAL_COMMAND,
        workspace_root=workspace,
        enrolled_by="wesley",
    )
    with pytest.raises(EnrollmentError) as excinfo:
        assign(
            tmp_path / "registry",
            agent_id="a",
            program_path=program,
            assigned_by="wesley",
        )
    assert excinfo.value.code == "ROLE_NOT_IN_PROGRAM"
    # Nothing was recorded.
    assert load_registry(tmp_path / "registry").agents["a"].assigned_program is None


# ---------------------------------------------------- the four identities


def test_the_four_identities_are_reported_separately(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _write_program(tmp_path, workspace)
    enroll(
        tmp_path / "registry",
        agent_id="a",
        role="implementer",
        adapter=AdapterKind.LOCAL_COMMAND,
        workspace_root=workspace,
        enrolled_by="wesley",
    )
    agent, loaded = assign(
        tmp_path / "registry",
        agent_id="a",
        program_path=program,
        assigned_by="wesley",
    )
    view = identity_view(agent, loaded)
    assert set(view) >= {
        "agent_identity",
        "runtime_session_identity",
        "task_ownership",
        "supervisor_process",
    }
    lifetimes = {
        key: view[key]["lifetime"]
        for key in (
            "agent_identity",
            "runtime_session_identity",
            "task_ownership",
            "supervisor_process",
        )
    }
    # Four distinct lifetimes, stated. If two ever collapse to the same string
    # somebody has conflated them.
    assert len(set(lifetimes.values())) == 4, lifetimes
    assert view["agent_identity"]["agent_id"] == "a"
    assert view["merge_authorized"] is False


def test_the_enrolled_agent_is_the_principal_the_lease_records(
    tmp_path: Path,
) -> None:
    """Task ownership names the enrolled agent, not the program's placeholder."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _write_program(tmp_path, workspace)
    agent = enroll(
        tmp_path / "registry",
        agent_id="the-real-agent",
        role="implementer",
        adapter=AdapterKind.LOCAL_COMMAND,
        workspace_root=workspace,
        enrolled_by="wesley",
    )
    loaded = load_program(program)
    supervisor = ProgramSupervisor(
        loaded,
        state_root=tmp_path / "state",
        enrolled_agents=(agent,),
        sleeper=lambda _s: None,
    )
    report = supervisor.start()
    assert report.stop_reason is ProgramStopReason.PROGRAM_COMPLETE

    state = load_state(tmp_path / "state")
    assert state is not None
    attempt = next(iter(state.attempts.values()))
    assert attempt.agent_id == "the-real-agent"
    assert attempt.profile_id == "implementer"

    from project_atlas.orchestration.autonomy.lease_projection import load_projection
    from project_atlas.orchestration.program.store import state_dir

    projection = load_projection(state_dir(tmp_path / "state"))
    assert [row.agent_id for row in projection.leases] == ["the-real-agent"]


def test_a_dead_supervisor_does_not_release_task_ownership(tmp_path: Path) -> None:
    """Process lifetime and ownership lifetime are different facts.

    A supervisor exiting with a task still in flight must leave the durable
    lease row ACTIVE. If it did not, the next process would believe the
    surface was free while a worker's effects on it were still unknown.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _write_program(tmp_path, workspace)
    loaded = load_program(program)
    supervisor = ProgramSupervisor(
        loaded, state_root=tmp_path / "state", sleeper=lambda _s: None
    )
    state = supervisor.load_or_init_state()
    supervisor._acquire()
    try:
        task = loaded.program.task("only")
        supervisor._transition(
            state, "only", NodeState.READY, reason="test setup"
        )
        supervisor._ensure_lease(state, task, loaded.effective_profile("only"))
    finally:
        supervisor._release()

    from project_atlas.orchestration.autonomy.lease_projection import (
        active_rows,
        load_projection,
    )
    from project_atlas.orchestration.program.store import state_dir

    rows = active_rows(load_projection(state_dir(tmp_path / "state")))
    assert [row.package_id for row in rows] == ["only"]

    # And a brand-new supervisor process finds and reuses that ownership
    # rather than granting a second lease over the same surface.
    successor = ProgramSupervisor(
        loaded, state_root=tmp_path / "state", sleeper=lambda _s: None
    )
    rehydrated = successor._rehydrate_lease(loaded.program.task("only"))
    assert rehydrated is not None
    assert rehydrated.lease_id == rows[0].lease_id


def test_enrollment_after_the_fact_cannot_make_one_agent_verify_itself(
    tmp_path: Path,
) -> None:
    """Two placeholder profiles can resolve to one enrolled agent.

    The loader's check runs on the program's own profiles, which look
    independent. Only after substitution do they collapse -- so the check is
    re-run there too.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    tasks = [
        {
            "task_id": "verified",
            "title": "needs verification",
            "instruction": "do it",
            "profile_ref": "implementer",
            "mutation_paths": ["verified.txt"],
            "surface_id": "verified",
            "surface_semantic": "VERIFIED",
            "capabilities_required": ["IMPLEMENT"],
            "requires_independent_verification": True,
            "verifier_profile_ref": "verifier",
            "acceptance": [
                {
                    "check_id": "out",
                    "kind": "FILE_EXISTS",
                    "description": "verified.txt exists",
                    "path": "verified.txt",
                }
            ],
        }
    ]
    profiles = {
        "implementer": _fixture_profile("placeholder-impl"),
        "verifier": _fixture_profile(
            "placeholder-verifier", capabilities=["IMPLEMENT", "VERIFY"]
        ),
    }
    program = _write_program(tmp_path, workspace, tasks=tasks, profiles=profiles)
    loaded = load_program(program)  # loads fine: the placeholders differ

    one_agent_two_roles = [
        enroll(
            tmp_path / "registry",
            agent_id="only-agent",
            role=role,
            adapter=AdapterKind.LOCAL_COMMAND,
            workspace_root=workspace,
            enrolled_by="wesley",
            replace=True,
        )
        for role in ("implementer", "verifier")
    ]
    with pytest.raises(SupervisorError) as excinfo:
        ProgramSupervisor(
            loaded,
            state_root=tmp_path / "state",
            enrolled_agents=one_agent_two_roles,
        )
    assert excinfo.value.code == "IMPLEMENTER_CANNOT_VERIFY"


def test_two_agents_cannot_claim_the_same_role(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    loaded = load_program(_write_program(tmp_path, workspace))
    agents = [
        enroll(
            tmp_path / "registry",
            agent_id=name,
            role="implementer",
            adapter=AdapterKind.LOCAL_COMMAND,
            workspace_root=workspace,
            enrolled_by="wesley",
        )
        for name in ("first", "second")
    ]
    with pytest.raises(SupervisorError) as excinfo:
        ProgramSupervisor(
            loaded, state_root=tmp_path / "state", enrolled_agents=agents
        )
    assert excinfo.value.code == "ROLE_CONTENTION"


def test_the_tighter_of_program_and_enrollment_limits_wins(tmp_path: Path) -> None:
    """Both bounds were set on purpose; honouring only one discards the other."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _write_program(tmp_path, workspace)
    loaded = load_program(program)
    agent = enroll(
        tmp_path / "registry",
        agent_id="a",
        role="implementer",
        adapter=AdapterKind.LOCAL_COMMAND,
        workspace_root=workspace,
        enrolled_by="wesley",
        profile_narrowing={"limits": {"max_seconds": 15, "max_attempts": 1}},
    )
    supervisor = ProgramSupervisor(
        loaded, state_root=tmp_path / "state", enrolled_agents=(agent,)
    )
    effective = supervisor.loaded.effective_profile("only")
    assert effective.limits.max_seconds == 15
    assert effective.limits.max_attempts == 1
    assert effective.agent_id == "a"


# ----------------------------------------------------------------- the CLI


def test_cli_enroll_assign_and_launch(tmp_path: Path, capsys: Any) -> None:
    from project_atlas.orchestration.program.cli import main

    workspace = tmp_path / "ws"
    workspace.mkdir()
    program = _write_program(tmp_path, workspace)
    registry = str(tmp_path / "registry")

    assert (
        main(
            [
                "agent", "enroll",
                "--registry", registry,
                "--agent-id", "cli-agent",
                "--role", "implementer",
                "--adapter", "local-command",
                "--workspace", str(workspace),
                "--enrolled-by", "wesley",
                "--narrow", json.dumps({"limits": {"max_seconds": 60}}),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert "ENROLLMENT != AUTHORIZATION" in payload["note"]

    assert (
        main(
            [
                "agent", "assign",
                "--registry", registry,
                "--agent-id", "cli-agent",
                "--program", str(program),
                "--assigned-by", "wesley",
            ]
        )
        == 0
    )
    assigned = json.loads(capsys.readouterr().out)
    assert assigned["program_id"] == "enrollment-program"
    assert assigned["merge_authorized"] is False

    assert (
        main(
            [
                "agent", "launch",
                "--registry", registry,
                "--agent-id", "cli-agent",
                "--state-root", str(tmp_path / "state"),
            ]
        )
        == 0
    )
    launched = json.loads(capsys.readouterr().out)
    assert launched["launched_as_agent"] == "cli-agent"
    assert launched["program_complete"] is True

    state = load_state(tmp_path / "state")
    assert state is not None
    assert next(iter(state.attempts.values())).agent_id == "cli-agent"


def test_cli_refuses_to_launch_an_unassigned_agent(tmp_path: Path) -> None:
    from project_atlas.orchestration.program.cli import main

    workspace = tmp_path / "ws"
    workspace.mkdir()
    registry = str(tmp_path / "registry")
    main(
        [
            "agent", "enroll",
            "--registry", registry,
            "--agent-id", "idle",
            "--role", "implementer",
            "--adapter", "local-command",
            "--workspace", str(workspace),
            "--enrolled-by", "wesley",
        ]
    )
    code = main(
        ["agent", "launch", "--registry", registry, "--agent-id", "idle"]
    )
    assert code == 1
