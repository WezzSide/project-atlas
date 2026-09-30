"""D-104 runtime remediations: actual delta, resume lineage, dual rollback, audit IDs."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from tests.unit.test_as_orch_d095_audit_provenance import (
    ASSIGN,
    HEAD,
    TREE,
    _assignment,
    _binding,
    _pass_payload,
)

from project_atlas.orchestration.sdk.audit_provenance import (
    apply_cloud_audit_from_plane,
    evaluate_cloud_audit,
    persist_cloud_audit_assignment,
)
from project_atlas.orchestration.sdk.cli_execution_port import CursorAgentCliExecutionPort
from project_atlas.orchestration.sdk.event_log import append_event
from project_atlas.orchestration.sdk.live_dag import LiveDagState, persist_live_dag
from project_atlas.orchestration.sdk.models import (
    CANONICAL_REPO_URL,
    PACKAGE_ID,
    AgentRecord,
    AgentRole,
    AgentRuntime,
    AgentState,
    SdkRuntimeError,
    _utc_now,
)
from project_atlas.orchestration.sdk.package_registry import (
    PackageRouteRecord,
    persist_package_route,
)
from project_atlas.orchestration.sdk.recovery import _validate_loaded_against_high_water
from project_atlas.orchestration.sdk.registries import CloudAgentRegistry, RunRegistry
from project_atlas.orchestration.sdk.result_plane import ResultEnvelope, append_result
from project_atlas.orchestration.sdk.role_pool import AgentRolePool
from project_atlas.orchestration.sdk.security_gates import (
    HostHighWater,
    WorkerBackend,
    collect_actual_changed_paths,
    persist_high_water,
)

PIN = "7e797468a2eca37c959920912b1fa264df4be638"
BRANCH = "feat/as-orch-continuation-broker-001"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=True,
    )


def test_actual_delta_includes_committed_escape(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "d104@invalid.local")
    _git(tmp_path, "config", "user.name", "D104")
    allowed = tmp_path / "src" / "project_atlas" / "orchestration" / "sdk"
    allowed.mkdir(parents=True)
    (allowed / "ok.py").write_text("ok\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "seed")
    pre_head = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    escape = tmp_path / "docs" / "secrets.txt"
    escape.parent.mkdir(parents=True)
    escape.write_text("leaked\n", encoding="utf-8")
    _git(tmp_path, "add", "docs/secrets.txt")
    _git(tmp_path, "commit", "-m", "hide via commit")
    changed = collect_actual_changed_paths(tmp_path, pre_head=pre_head)
    assert changed is not None
    assert "docs/secrets.txt" in changed


def test_resume_agent_rejects_cross_worktree(tmp_path: Path) -> None:
    agents = CloudAgentRegistry(tmp_path)
    runs = RunRegistry(tmp_path)
    pool = AgentRolePool(agents)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    agents.upsert(
        AgentRecord(
            agent_id="cli-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            runtime=AgentRuntime.LOCAL,
            role=AgentRole.REMEDIATOR,
            package_id=PACKAGE_ID,
            base_main=PIN,
            branch=BRANCH,
            created_at=_utc_now(),
            state=AgentState.IDLE,
            worker_backend=WorkerBackend.CURSOR_AGENT_CLI.value,
            workspace=str(foreign.resolve()),
            repository=CANONICAL_REPO_URL,
            creation_generation=96,
            creation_sequence=1,
        )
    )
    port = CursorAgentCliExecutionPort(root=tmp_path, agents_reg=agents, runs_reg=runs, pool=pool)
    with pytest.raises(SdkRuntimeError, match=r"cross-worktree|workspace root mismatch"):
        asyncio.run(port.resume_agent("cli-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"))


def test_dual_live_and_high_water_rollback_rejected_by_event_log(tmp_path: Path) -> None:
    persist_live_dag(
        tmp_path,
        LiveDagState(dag_generation=95, material_transitions=20, bound_head=HEAD),
    )
    persist_high_water(
        tmp_path,
        HostHighWater(dag_generation=95, event_sequence=20, registry_revision=5),
    )
    persist_package_route(
        tmp_path,
        PackageRouteRecord(
            dag_generation=95,
            registry_revision=5,
            canonical_head=HEAD,
            canonical_tree=TREE,
        ),
    )
    append_event(
        tmp_path,
        "NEW_HEAD_ADOPTED",
        dag_generation=96,
        head=HEAD,
        tree=TREE,
    )
    with pytest.raises(SdkRuntimeError) as exc:
        _validate_loaded_against_high_water(
            root=tmp_path,
            high_water=HostHighWater(dag_generation=95, event_sequence=20, registry_revision=5),
        )
    assert exc.value.code == "HOST_ROLLBACK_REJECTED"


def test_synthetic_cli_audit_identity_rejected() -> None:
    decision = evaluate_cloud_audit(
        assignment=_assignment(worker_id="cli-audit-96-ef3e6c6fa370"),
        binding=_binding(session_or_agent_id="cli-audit-96-ef3e6c6fa370"),
        payload=_pass_payload(),
        live_head=HEAD,
        live_tree=TREE,
        live_generation=92,
        already_consumed=frozenset(),
        source="CLOUD_RUNTIME_AUDITOR",
    )
    assert decision.accepted is False
    assert decision.reason == "REJECT_INDEPENDENCE"


def test_pending_rebind_assignment_cannot_arm_gate() -> None:
    decision = evaluate_cloud_audit(
        assignment=_assignment(worker_id="pending-audit-g96"),
        binding=_binding(),
        payload=_pass_payload(),
        live_head=HEAD,
        live_tree=TREE,
        live_generation=92,
        already_consumed=frozenset(),
        source="CLOUD_RUNTIME_AUDITOR",
    )
    assert decision.accepted is False
    assert decision.reason == "PENDING_REBIND"


def test_poison_first_envelope_does_not_block_authentic_fail(tmp_path: Path) -> None:
    persist_cloud_audit_assignment(tmp_path, _assignment())
    append_result(
        tmp_path,
        ResultEnvelope(
            source="CLOUD_RUNTIME_AUDITOR",
            binding=_binding(session_or_agent_id="cli-audit-96-ef3e6c6fa370"),
            payload={
                "ASSIGNMENT_ID": ASSIGN,
                "AUDIT_RESULT": "PASS",
                "SIX_P1_RUNTIME_OPEN_COUNT": 0,
            },
        ),
    )
    append_result(
        tmp_path,
        ResultEnvelope(
            source="CLOUD_RUNTIME_AUDITOR",
            binding=_binding(),
            payload={
                "ASSIGNMENT_ID": ASSIGN,
                "AUDIT_RESULT": "FAIL",
                "SIX_P1_RUNTIME_OPEN_COUNT": 3,
            },
        ),
    )
    decision = apply_cloud_audit_from_plane(
        tmp_path, live_head=HEAD, live_tree=TREE, live_generation=92
    )
    assert decision.accepted is True
    assert decision.gate == "FAIL"
    assert decision.reason == "AUDIT_FAIL"
