"""AS-AUTHORITY-004 -- authority must cover the verifier and stay current.

Three properties, each of which the supervisor asserted for the IMPLEMENTER
and silently skipped for the VERIFIER or for a binding that changed after the
program started:

  1. a verifier whose enrollment was withdrawn must not be dispatched, even
     when the implementer is impeccable -- and the reverse must also hold;
  2. enrollment narrowing and the runtime-substitution grant apply to the
     effective VERIFIER profile, not only the implementer's;
  3. `assigned_program` is re-read before every dispatch, so an assignment
     changed after the first task cannot keep working from a stored binding.

Zero model calls: every worker is the local fixture command.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from project_atlas.orchestration.program.enrollment import (
    AgentStatus,
    assign,
    enroll,
    load_registry,
    set_status,
)
from project_atlas.orchestration.program.loader import load_program
from project_atlas.orchestration.program.profiles import AdapterKind
from project_atlas.orchestration.program.supervisor import ProgramSupervisor

FIXTURE_WORKER = Path(__file__).with_name("_program_fixture_worker.py")


def _profile(agent_id: str, **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "agent_id": agent_id,
        "adapter": "local-command",
        "credential": "NOT_APPLICABLE",
        "capabilities": ["IMPLEMENT"],
        "permission_mode": "acceptEdits",
        "allowed_tools": ["Read", "Edit", "Bash"],
        "limits": {"max_seconds": 120, "max_attempts": 2},
        "env_allowlist": ["ATLAS_FIXTURE_MODE", "ATLAS_FIXTURE_TARGET",
                          "ATLAS_PROGRAM_ATTEMPT", "ATLAS_PROGRAM_TASK"],
        "adapter_options": {"argv": [sys.executable, str(FIXTURE_WORKER)]},
    }
    body.update(over)
    return body


def _verified_task(task_id: str) -> dict[str, Any]:
    return {
        "task_id": task_id, "title": task_id, "instruction": "do it",
        "profile_ref": "implementer", "mutation_paths": [f"{task_id}.txt"],
        "surface_id": task_id, "surface_semantic": task_id.upper(),
        "capabilities_required": ["IMPLEMENT"],
        "requires_independent_verification": True,
        "verifier_profile_ref": "verifier",
        "acceptance": [{"check_id": "out", "kind": "FILE_EXISTS",
                        "description": "written", "path": f"{task_id}.txt"}],
    }


def _write_program(tmp_path: Path, workspace: Path, *, tasks: list[dict[str, Any]],
                   profiles: dict[str, dict[str, Any]], name: str = "program.json",
                   program_id: str = "authority-program") -> Path:
    payload = {
        "schema_version": 1,
        "program": {
            "program_id": program_id, "objective": "authority coverage",
            "approved_by": "test", "approval_reference": "test",
            "workspace_root": str(workspace), "base_pin": "0" * 40,
            "limits": {"max_cycles": 10, "idle_sleep_seconds": 0.0},
            "tasks": tasks,
        },
        "profiles": profiles,
    }
    path = tmp_path / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _fixture_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_FIXTURE_MODE", "write")
    monkeypatch.delenv("ATLAS_FIXTURE_TARGET", raising=False)


def _enrol_pair(registry: Path, workspace: Path, program: Path):
    for agent_id, role in (("impl-a", "implementer"), ("verif-b", "verifier")):
        enroll(registry, agent_id=agent_id, role=role, adapter=AdapterKind.LOCAL_COMMAND,
               workspace_root=workspace, enrolled_by="test")
        assign(registry, agent_id=agent_id, program_path=program, assigned_by="test")


def _supervisor(tmp_path: Path, program: Path, registry: Path) -> ProgramSupervisor:
    loaded = load_program(program)
    agents = list(load_registry(registry).agents.values())
    return ProgramSupervisor(
        loaded, state_root=tmp_path / "state",
        enrolled_agents=agents, registry_root=registry,
    )


def _launch_count(workspace: Path) -> int:
    return len(list(workspace.glob("*.txt")))


# ------------------------------------------- 1. verifier authority is checked

def test_a_withdrawn_verifier_stops_dispatch_though_the_implementer_is_valid(
    tmp_path: Path,
) -> None:
    """The implementer is impeccable; only the verifier lost its enrollment."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    registry = tmp_path / "registry"
    program = _write_program(
        tmp_path, workspace, tasks=[_verified_task("alpha")],
        profiles={"implementer": _profile("impl-placeholder"),
                  "verifier": _profile("verif-placeholder", capabilities=["VERIFY"])})
    _enrol_pair(registry, workspace, program)
    set_status(registry, agent_id="verif-b", status=AgentStatus.SUSPENDED)

    sup = _supervisor(tmp_path, program, registry)
    reason = sup._authority_revoked("alpha")
    assert reason is not None, "a suspended verifier did not stop dispatch"
    assert "verif-b" in reason
    assert _launch_count(workspace) == 0, "a worker ran despite withdrawn authority"


def test_a_withdrawn_implementer_still_stops_dispatch(tmp_path: Path) -> None:
    """The reverse case, so the new check cannot have replaced the old one."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    registry = tmp_path / "registry"
    program = _write_program(
        tmp_path, workspace, tasks=[_verified_task("alpha")],
        profiles={"implementer": _profile("impl-placeholder"),
                  "verifier": _profile("verif-placeholder", capabilities=["VERIFY"])})
    _enrol_pair(registry, workspace, program)
    set_status(registry, agent_id="impl-a", status=AgentStatus.SUSPENDED)

    sup = _supervisor(tmp_path, program, registry)
    reason = sup._authority_revoked("alpha")
    assert reason is not None and "impl-a" in reason
    assert _launch_count(workspace) == 0


def test_both_enrolled_and_active_permits_dispatch(tmp_path: Path) -> None:
    """The positive control: legitimate work must still start."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    registry = tmp_path / "registry"
    program = _write_program(
        tmp_path, workspace, tasks=[_verified_task("alpha")],
        profiles={"implementer": _profile("impl-placeholder"),
                  "verifier": _profile("verif-placeholder", capabilities=["VERIFY"])})
    _enrol_pair(registry, workspace, program)
    sup = _supervisor(tmp_path, program, registry)
    assert sup._authority_revoked("alpha") is None


# ---------------------------- 2. narrowing applies to the verifier profile too

def test_enrollment_narrowing_reaches_the_effective_verifier_profile(
    tmp_path: Path,
) -> None:
    """A verifier profile may not keep authority its enrollment narrowed away."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    registry = tmp_path / "registry"
    program = _write_program(
        tmp_path, workspace, tasks=[_verified_task("alpha")],
        profiles={"implementer": _profile("impl-placeholder"),
                  "verifier": _profile("verif-placeholder", capabilities=["VERIFY"],
                                       permission_mode="acceptEdits",
                                       allowed_tools=["Read", "Edit", "Bash"])})
    for agent_id, role, narrow in (
        ("impl-a", "implementer", None),
        ("verif-b", "verifier", {"permission_mode": "plan", "allowed_tools": ["Read"]}),
    ):
        enroll(registry, agent_id=agent_id, role=role, adapter=AdapterKind.LOCAL_COMMAND,
               workspace_root=workspace, enrolled_by="test", profile_narrowing=narrow)
        assign(registry, agent_id=agent_id, program_path=program, assigned_by="test")

    sup = _supervisor(tmp_path, program, registry)
    verifier = sup.loaded.verifiers["alpha"]
    assert verifier.agent_id == "verif-b"
    assert verifier.permission_mode == "plan", (
        "the verifier kept acceptEdits although its enrollment narrowed to plan"
    )
    assert set(verifier.allowed_tools) <= {"Read"}, (
        f"verifier tools were not narrowed: {verifier.allowed_tools}"
    )


def test_a_verifier_runtime_substitution_needs_its_own_grant(tmp_path: Path) -> None:
    """A verifier enrolled on another runtime without a grant must not bind."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    registry = tmp_path / "registry"
    program = _write_program(
        tmp_path, workspace, tasks=[_verified_task("alpha")],
        profiles={"implementer": _profile("impl-placeholder"),
                  "verifier": _profile("verif-placeholder", capabilities=["VERIFY"],
                                       adapter="claude-code")})
    enroll(registry, agent_id="impl-a", role="implementer",
           adapter=AdapterKind.LOCAL_COMMAND, workspace_root=workspace,
           enrolled_by="test")
    assign(registry, agent_id="impl-a", program_path=program, assigned_by="test")
    # enrolled on local-command, but the program's verifier profile says claude-code
    enroll(registry, agent_id="verif-b", role="verifier",
           adapter=AdapterKind.LOCAL_COMMAND, workspace_root=workspace,
           enrolled_by="test")
    try:
        assign(registry, agent_id="verif-b", program_path=program, assigned_by="test")
    except Exception:
        pytest.skip("assign already refuses this; the dispatch path is not reachable")

    sup = _supervisor(tmp_path, program, registry)
    verifier = sup.loaded.verifiers["alpha"]
    assert verifier.adapter is not AdapterKind.CLAUDE_CODE, (
        "the verifier bound to a substituted runtime with no authorization"
    )


# ------------------------------ 3. assignment is re-read before every dispatch

def test_an_assignment_changed_after_the_first_task_stops_the_next_dispatch(
    tmp_path: Path,
) -> None:
    """A stored binding must not outlive the assignment it came from."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    registry = tmp_path / "registry"
    program = _write_program(
        tmp_path, workspace,
        tasks=[_verified_task("alpha"), _verified_task("beta")],
        profiles={"implementer": _profile("impl-placeholder"),
                  "verifier": _profile("verif-placeholder", capabilities=["VERIFY"])})
    other = _write_program(
        tmp_path, workspace, tasks=[_verified_task("alpha")],
        profiles={"implementer": _profile("impl-placeholder"),
                  "verifier": _profile("verif-placeholder", capabilities=["VERIFY"])},
        name="other.json", program_id="a-different-program")
    _enrol_pair(registry, workspace, program)

    sup = _supervisor(tmp_path, program, registry)
    assert sup._authority_revoked("alpha") is None, "baseline should permit dispatch"

    # the operator re-assigns the implementer to a different program mid-flight
    assign(registry, agent_id="impl-a", program_path=other, assigned_by="test")

    reason = sup._authority_revoked("beta")
    assert reason is not None, (
        "dispatch continued under a stored binding after the assignment changed"
    )
    assert "assign" in reason.lower() or "program" in reason.lower()
    assert _launch_count(workspace) == 0
