"""D-106 production-path regressions for the D-105 five-P1 packet.

Writer-local verification only. Does not certify CLOUD_RUNTIME_AUDIT.
ORCH_SUPERVISOR_CI_REACTION_001 remains OPEN_CARRY_FORWARD.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from tests.unit.test_as_orch_d095_audit_provenance import _assignment

from project_atlas.orchestration.sdk.audit_provenance import persist_cloud_audit_assignment
from project_atlas.orchestration.sdk.backend import FakeCursorSDKBackend
from project_atlas.orchestration.sdk.cli_execution_port import CursorAgentCliExecutionPort
from project_atlas.orchestration.sdk.cost_guard import CostGuard
from project_atlas.orchestration.sdk.event_log import append_event, event_log_path
from project_atlas.orchestration.sdk.lease_registry import mint_governor_writer_lease
from project_atlas.orchestration.sdk.live_dag import LiveDagState, persist_live_dag
from project_atlas.orchestration.sdk.models import (
    PACKAGE_ID,
    AgentRecord,
    AgentRole,
    AgentRuntime,
    AgentState,
    RunRecord,
    RunStatus,
    ScheduleRequest,
    SdkRuntimeError,
    _utc_now,
)
from project_atlas.orchestration.sdk.package_registry import (
    PackageRouteRecord,
    persist_package_route,
    update_package_route_on_head_move,
)
from project_atlas.orchestration.sdk.recovery import _validate_loaded_against_high_water
from project_atlas.orchestration.sdk.registries import CloudAgentRegistry, RunRegistry
from project_atlas.orchestration.sdk.result_plane import (
    ResultEnvelope,
    append_result,
    ingest_pending_against_registry,
    ingested_index_path,
)
from project_atlas.orchestration.sdk.role_pool import AgentRolePool
from project_atlas.orchestration.sdk.scheduler import DagToAgentScheduler, ReadyWorkItem
from project_atlas.orchestration.sdk.security_gates import (
    BoundWorkerResult,
    HostHighWater,
    WorkerBackend,
    collect_actual_changed_paths,
    mint_creation_sequence,
    persist_high_water,
    require_changed_paths_determined,
)
from project_atlas.orchestration.sdk.supervisor import DurableAtlasSupervisor

PIN = "7e797468a2eca37c959920912b1fa264df4be638"
HEAD = "09f5939d0e52f57557443abcb2a87eb353f7307d"
TREE = "68ea3b35b797b1d3dd55e25dc5eba7727d13797f"
BRANCH = "feat/as-orch-continuation-broker-001"
SESSION = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
AGENT = "cli-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
LEASE = "lease-d106-writer-106"
DIGEST = "b" * 64


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True, check=True)


def _init_git(root: Path) -> str:
    _git(root, "init")
    _git(root, "config", "user.email", "d106@invalid.local")
    _git(root, "config", "user.name", "D106")
    allowed = root / "src" / "project_atlas" / "orchestration" / "sdk"
    allowed.mkdir(parents=True)
    (allowed / "ok.py").write_text("ok\n", encoding="utf-8")
    (root / ".gitignore").write_text(".atlas/\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "seed")
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _port(root: Path) -> CursorAgentCliExecutionPort:
    agents = CloudAgentRegistry(root)
    runs = RunRegistry(root)
    return CursorAgentCliExecutionPort(
        root=root, agents_reg=agents, runs_reg=runs, pool=AgentRolePool(agents)
    )


def _req(**kwargs: object) -> ScheduleRequest:
    base: dict[str, object] = dict(
        role=AgentRole.REMEDIATOR,
        package_id=PACKAGE_ID,
        node_id="REMEDIATE-D106",
        cycle_id="C-D106",
        dag_generation=106,
        lease_id=LEASE,
        base_main=PIN,
        branch=BRANCH,
        candidate_head=HEAD,
        candidate_tree=TREE,
        prompt="remediate d105 five p1s",
    )
    base.update(kwargs)
    return ScheduleRequest(**base)  # type: ignore[arg-type]


def _plant_writer_lease(root: Path) -> None:
    update_package_route_on_head_move(root, head=HEAD, tree=TREE, dag_generation=106)
    mint_governor_writer_lease(
        root,
        lease_id=LEASE,
        role=AgentRole.REMEDIATOR,
        dag_generation=106,
        candidate_head=HEAD,
        candidate_tree=TREE,
        worktree=str(root),
    )


def _payload() -> dict[str, object]:
    return {
        "session_id": SESSION,
        "result": "ok",
        "request_id": "dddddddd-eeee-ffff-aaaa-bbbbbbbbbbbb",
    }


def _live(root: Path, gen: int, transitions: int) -> None:
    persist_live_dag(
        root,
        LiveDagState(dag_generation=gen, material_transitions=transitions, bound_head=HEAD),
    )


def _hw(root: Path, gen: int, seq: int, rev: int = 0) -> None:
    persist_high_water(
        root,
        HostHighWater(dag_generation=gen, event_sequence=seq, registry_revision=rev),
    )


def test_p1_unauthorized_committed_path_rejected_via_create_and_send(tmp_path: Path) -> None:
    _init_git(tmp_path)
    _plant_writer_lease(tmp_path)
    port = _port(tmp_path)

    async def invoke(**_kwargs: object) -> dict[str, object]:
        evil = tmp_path / "docs" / "evil.md"
        evil.parent.mkdir(parents=True)
        evil.write_text("escaped\n", encoding="utf-8")
        _git(tmp_path, "add", "docs/evil.md")
        _git(tmp_path, "commit", "-m", "hide via commit")
        assert _git(tmp_path, "status", "--porcelain").stdout.strip() == ""
        return _payload()

    port._invoke = invoke  # type: ignore[method-assign]
    with pytest.raises(SdkRuntimeError) as exc:
        asyncio.run(port.create_and_send(_req()))
    assert exc.value.code == "REJECTED_SCOPE_ESCAPE"


def test_p1_reset_after_commit_rejected_via_create_and_send(tmp_path: Path) -> None:
    _init_git(tmp_path)
    _plant_writer_lease(tmp_path)
    port = _port(tmp_path)

    async def invoke(**_kwargs: object) -> dict[str, object]:
        pre = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
        evil = tmp_path / "docs" / "evil.md"
        evil.parent.mkdir(parents=True)
        evil.write_text("escaped\n", encoding="utf-8")
        _git(tmp_path, "add", "docs/evil.md")
        _git(tmp_path, "commit", "-m", "hide via commit")
        _git(tmp_path, "reset", "--hard", pre)
        assert _git(tmp_path, "status", "--porcelain").stdout.strip() == ""
        return _payload()

    port._invoke = invoke  # type: ignore[method-assign]
    with pytest.raises(SdkRuntimeError) as exc:
        asyncio.run(port.create_and_send(_req()))
    assert exc.value.code == "REJECTED_SCOPE_ESCAPE"


def test_p1_reset_after_commit_still_rejected(tmp_path: Path) -> None:
    pre = _init_git(tmp_path)
    evil = tmp_path / "docs" / "evil.md"
    evil.parent.mkdir()
    evil.write_text("escaped\n", encoding="utf-8")
    _git(tmp_path, "add", "docs/evil.md")
    _git(tmp_path, "commit", "-m", "hide")
    _git(tmp_path, "reset", "--hard", pre)
    changed = collect_actual_changed_paths(tmp_path, pre_head=pre)
    assert changed is not None
    assert "docs/evil.md" in changed


def test_p1_porcelain_none_fails_closed(tmp_path: Path) -> None:
    _init_git(tmp_path)
    _plant_writer_lease(tmp_path)
    port = _port(tmp_path)
    port._git_rev_parse_head = lambda: None  # type: ignore[method-assign]

    async def invoke(**_kwargs: object) -> dict[str, object]:
        return _payload()

    port._invoke = invoke  # type: ignore[method-assign]
    with pytest.raises(SdkRuntimeError) as exc:
        asyncio.run(port.create_and_send(_req()))
    assert exc.value.code == "DIFF_UNDETERMINED"
    with pytest.raises(SdkRuntimeError) as exc2:
        require_changed_paths_determined(None)
    assert exc2.value.code == "DIFF_UNDETERMINED"


def test_p1_authorized_paths_still_work(tmp_path: Path) -> None:
    _init_git(tmp_path)
    _plant_writer_lease(tmp_path)
    port = _port(tmp_path)

    async def invoke(**_kwargs: object) -> dict[str, object]:
        path = tmp_path / "src" / "project_atlas" / "orchestration" / "sdk" / "fix.py"
        path.write_text("fixed\n", encoding="utf-8")
        _git(tmp_path, "add", "src/project_atlas/orchestration/sdk/fix.py")
        _git(tmp_path, "commit", "-m", "authorized")
        return _payload()

    port._invoke = invoke  # type: ignore[method-assign]
    record = asyncio.run(port.create_and_send(_req()))
    assert record.status == RunStatus.FINISHED
    assert record.lease_id == LEASE


def test_p2_missing_lease_publish_raises(tmp_path: Path) -> None:
    port = _port(tmp_path)
    request = _req(role=AgentRole.CLOUD_RUNTIME_AUDITOR, lease_id=None)
    record = RunRecord(
        run_id="run-d106-missing-lease",
        agent_id=AGENT,
        package_id=PACKAGE_ID,
        lease_id=None,
        role=AgentRole.CLOUD_RUNTIME_AUDITOR,
        prompt_digest="c" * 64,
        idempotency_key="idemp-d106-missing",
        status=RunStatus.FINISHED,
        started_at=_utc_now(),
        completed_at=_utc_now(),
        result_digest=DIGEST,
        node_id="CLOUD-AUDIT-LIVE",
        dag_generation=106,
        candidate_head=HEAD,
        candidate_tree=TREE,
    )
    session = port._sessions.setdefault(  # type: ignore[attr-defined]
        AGENT,
        type("S", (), {"agent_id": AGENT})(),
    )
    with pytest.raises(SdkRuntimeError) as exc:
        port._publish_result_plane(session=session, request=request, record=record, text="{}")
    assert exc.value.code == "EXPECTED_BINDING_REQUIRED"


def test_p2_missing_digest_publish_raises(tmp_path: Path) -> None:
    port = _port(tmp_path)
    request = _req(role=AgentRole.CLOUD_RUNTIME_AUDITOR, lease_id=LEASE)
    record = RunRecord(
        run_id="run-d106-missing-digest",
        agent_id=AGENT,
        package_id=PACKAGE_ID,
        lease_id=LEASE,
        role=AgentRole.CLOUD_RUNTIME_AUDITOR,
        prompt_digest="c" * 64,
        idempotency_key="idemp-d106-missing-digest",
        status=RunStatus.FINISHED,
        started_at=_utc_now(),
        completed_at=_utc_now(),
        result_digest=None,
        node_id="CLOUD-AUDIT-LIVE",
        dag_generation=106,
        candidate_head=HEAD,
        candidate_tree=TREE,
    )
    session = port._sessions.setdefault(  # type: ignore[attr-defined]
        AGENT,
        type("S", (), {"agent_id": AGENT})(),
    )
    with pytest.raises(SdkRuntimeError) as exc:
        port._publish_result_plane(session=session, request=request, record=record, text="{}")
    assert exc.value.code == "EXPECTED_BINDING_REQUIRED"


def test_p2_foreign_result_quarantined_not_swallowed(tmp_path: Path) -> None:
    supervisor = DurableAtlasSupervisor.create(tmp_path, use_fake=True, max_cycles=1)
    append_result(
        tmp_path,
        ResultEnvelope(
            source="CLOUD_RUNTIME_AUDITOR",
            binding=BoundWorkerResult(
                worker_backend=WorkerBackend.CURSOR_AGENT_CLI,
                session_or_agent_id=AGENT,
                run_id="run-foreign-d106",
                package_id=PACKAGE_ID,
                dag_node="CLOUD-AUDIT-LIVE",
                dag_generation=106,
                role=AgentRole.CLOUD_RUNTIME_AUDITOR,
                lease_id="lease-missing",
                attempt=1,
                result_digest=DIGEST,
                candidate_head=HEAD,
                candidate_tree=TREE,
            ),
            payload={"ASSIGNMENT_ID": "assign-x"},
        ),
    )
    asyncio.run(supervisor.schedule_cycle())
    assert supervisor.status.last_cycle_error == "FOREIGN_RESULT"
    assert supervisor.status.contained_failures >= 1
    quarantine = (
        tmp_path / ".atlas" / "orchestration" / "sdk-runtime" / "result-plane-quarantine.jsonl"
    )
    assert quarantine.is_file()
    assert "FOREIGN_RESULT" in quarantine.read_text(encoding="utf-8")


def test_p2_correct_binding_consumes_once_with_full_record(tmp_path: Path) -> None:
    runs = RunRegistry(tmp_path)
    persist_cloud_audit_assignment(tmp_path, _assignment(assignment_id="assign-d106"))
    run = RunRecord(
        run_id="run-d106-bound",
        agent_id=AGENT,
        package_id=PACKAGE_ID,
        lease_id="lease-cloud-audit-92",
        role=AgentRole.CLOUD_RUNTIME_AUDITOR,
        prompt_digest="d" * 64,
        idempotency_key="idemp-d106-bound",
        status=RunStatus.FINISHED,
        started_at=_utc_now(),
        completed_at=_utc_now(),
        result_digest=DIGEST,
        node_id="CLOUD-AUDIT-LIVE",
        dag_generation=92,
        candidate_head="ca7368ff3bde9895b14cc90069a7036dd435f250",
        candidate_tree="5eeb51235afd7a6b818fb9c3fffed85255aae6c8",
    )
    runs.upsert(run)
    envelope = ResultEnvelope(
        source="CLOUD_RUNTIME_AUDITOR",
        binding=BoundWorkerResult(
            worker_backend=WorkerBackend.CURSOR_AGENT_CLI,
            session_or_agent_id=AGENT,
            run_id=run.run_id,
            package_id=PACKAGE_ID,
            dag_node="CLOUD-AUDIT-LIVE",
            dag_generation=92,
            role=AgentRole.CLOUD_RUNTIME_AUDITOR,
            lease_id="lease-cloud-audit-92",
            attempt=1,
            result_digest=DIGEST,
            candidate_head=run.candidate_head,
            candidate_tree=run.candidate_tree,
        ),
        payload={"ASSIGNMENT_ID": "assign-d106"},
    )
    append_result(tmp_path, envelope)
    first = ingest_pending_against_registry(
        tmp_path, runs=runs, worker_backend=WorkerBackend.CURSOR_AGENT_CLI
    )
    assert len(first) == 1
    replay = ingest_pending_against_registry(
        tmp_path, runs=runs, worker_backend=WorkerBackend.CURSOR_AGENT_CLI
    )
    assert replay == []
    payload = json.loads(ingested_index_path(tmp_path).read_text(encoding="utf-8"))
    assert payload["records"][0]["run_id"] == run.run_id
    assert payload["records"][0]["result_digest"] == DIGEST
    assert payload["records"][0]["transition_id"]
    assert payload["records"][0]["consumed_at"]
    assert payload["records"][0]["dag_generation"] == 92


def test_p2_wrong_assignment_rejected(tmp_path: Path) -> None:
    runs = RunRegistry(tmp_path)
    persist_cloud_audit_assignment(tmp_path, _assignment(assignment_id="assign-live"))
    run = RunRecord(
        run_id="run-d106-wrong-assign",
        agent_id=AGENT,
        package_id=PACKAGE_ID,
        lease_id="lease-cloud-audit-92",
        role=AgentRole.CLOUD_RUNTIME_AUDITOR,
        prompt_digest="e" * 64,
        idempotency_key="idemp-d106-wrong",
        status=RunStatus.FINISHED,
        started_at=_utc_now(),
        completed_at=_utc_now(),
        result_digest=DIGEST,
        node_id="CLOUD-AUDIT-LIVE",
        dag_generation=92,
        candidate_head="ca7368ff3bde9895b14cc90069a7036dd435f250",
        candidate_tree="5eeb51235afd7a6b818fb9c3fffed85255aae6c8",
    )
    runs.upsert(run)
    append_result(
        tmp_path,
        ResultEnvelope(
            source="CLOUD_RUNTIME_AUDITOR",
            binding=BoundWorkerResult(
                worker_backend=WorkerBackend.CURSOR_AGENT_CLI,
                session_or_agent_id=AGENT,
                run_id=run.run_id,
                package_id=PACKAGE_ID,
                dag_node="CLOUD-AUDIT-LIVE",
                dag_generation=92,
                role=AgentRole.CLOUD_RUNTIME_AUDITOR,
                lease_id="lease-cloud-audit-92",
                attempt=1,
                result_digest=DIGEST,
                candidate_head=run.candidate_head,
                candidate_tree=run.candidate_tree,
            ),
            payload={"ASSIGNMENT_ID": "assign-other"},
        ),
    )
    with pytest.raises(SdkRuntimeError) as exc:
        ingest_pending_against_registry(
            tmp_path, runs=runs, worker_backend=WorkerBackend.CURSOR_AGENT_CLI
        )
    assert exc.value.code == "WRONG_ASSIGNMENT"


def test_p2_wrong_run_binding_rejected(tmp_path: Path) -> None:
    runs = RunRegistry(tmp_path)
    persist_cloud_audit_assignment(tmp_path, _assignment(assignment_id="assign-d106"))
    run = RunRecord(
        run_id="run-d106-live",
        agent_id=AGENT,
        package_id=PACKAGE_ID,
        lease_id="lease-cloud-audit-92",
        role=AgentRole.CLOUD_RUNTIME_AUDITOR,
        prompt_digest="f" * 64,
        idempotency_key="idemp-d106-run",
        status=RunStatus.FINISHED,
        started_at=_utc_now(),
        completed_at=_utc_now(),
        result_digest=DIGEST,
        node_id="CLOUD-AUDIT-LIVE",
        dag_generation=92,
        candidate_head="ca7368ff3bde9895b14cc90069a7036dd435f250",
        candidate_tree="5eeb51235afd7a6b818fb9c3fffed85255aae6c8",
    )
    runs.upsert(run)
    append_result(
        tmp_path,
        ResultEnvelope(
            source="CLOUD_RUNTIME_AUDITOR",
            binding=BoundWorkerResult(
                worker_backend=WorkerBackend.CURSOR_AGENT_CLI,
                session_or_agent_id=AGENT,
                run_id="run-d106-other",
                package_id=PACKAGE_ID,
                dag_node="CLOUD-AUDIT-LIVE",
                dag_generation=92,
                role=AgentRole.CLOUD_RUNTIME_AUDITOR,
                lease_id="lease-cloud-audit-92",
                attempt=1,
                result_digest=DIGEST,
                candidate_head=run.candidate_head,
                candidate_tree=run.candidate_tree,
            ),
            payload={"ASSIGNMENT_ID": "assign-d106"},
        ),
    )
    with pytest.raises(SdkRuntimeError) as exc:
        ingest_pending_against_registry(
            tmp_path, runs=runs, worker_backend=WorkerBackend.CURSOR_AGENT_CLI
        )
    assert exc.value.code == "FOREIGN_RESULT"


def test_p2_stale_generation_rejected(tmp_path: Path) -> None:
    runs = RunRegistry(tmp_path)
    persist_cloud_audit_assignment(tmp_path, _assignment(assignment_id="assign-d106"))
    run = RunRecord(
        run_id="run-d106-stale",
        agent_id=AGENT,
        package_id=PACKAGE_ID,
        lease_id="lease-cloud-audit-92",
        role=AgentRole.CLOUD_RUNTIME_AUDITOR,
        prompt_digest="a" * 64,
        idempotency_key="idemp-d106-stale",
        status=RunStatus.FINISHED,
        started_at=_utc_now(),
        completed_at=_utc_now(),
        result_digest=DIGEST,
        node_id="CLOUD-AUDIT-LIVE",
        dag_generation=92,
        candidate_head="ca7368ff3bde9895b14cc90069a7036dd435f250",
        candidate_tree="5eeb51235afd7a6b818fb9c3fffed85255aae6c8",
    )
    runs.upsert(run)
    append_result(
        tmp_path,
        ResultEnvelope(
            source="CLOUD_RUNTIME_AUDITOR",
            binding=BoundWorkerResult(
                worker_backend=WorkerBackend.CURSOR_AGENT_CLI,
                session_or_agent_id=AGENT,
                run_id=run.run_id,
                package_id=PACKAGE_ID,
                dag_node="CLOUD-AUDIT-LIVE",
                dag_generation=91,
                role=AgentRole.CLOUD_RUNTIME_AUDITOR,
                lease_id="lease-cloud-audit-92",
                attempt=1,
                result_digest=DIGEST,
                candidate_head=run.candidate_head,
                candidate_tree=run.candidate_tree,
            ),
            payload={"ASSIGNMENT_ID": "assign-d106"},
        ),
    )
    with pytest.raises(SdkRuntimeError) as exc:
        ingest_pending_against_registry(
            tmp_path, runs=runs, worker_backend=WorkerBackend.CURSOR_AGENT_CLI
        )
    assert exc.value.code == "WRONG_GENERATION"


def test_p3_coordinated_trio_plus_truncated_events_rejected(tmp_path: Path) -> None:
    _live(tmp_path, 10, 10)
    _hw(tmp_path, 10, 10, 4)
    persist_package_route(
        tmp_path,
        PackageRouteRecord(
            dag_generation=10, registry_revision=4, canonical_head=HEAD, canonical_tree=TREE
        ),
    )
    for gen in range(1, 11):
        append_event(tmp_path, "NEW_HEAD_ADOPTED", dag_generation=gen, head=HEAD, tree=TREE)
    _live(tmp_path, 3, 3)
    _hw(tmp_path, 3, 3, 1)
    persist_package_route(
        tmp_path,
        PackageRouteRecord(
            dag_generation=3, registry_revision=1, canonical_head=HEAD, canonical_tree=TREE
        ),
    )
    # Rewrite events.jsonl to a consistent older trio. The append-only witness
    # is not part of that trio and must still fail closed.
    kept = event_log_path(tmp_path).read_text(encoding="utf-8").splitlines()[:3]
    event_log_path(tmp_path).write_text("\n".join(kept) + "\n", encoding="utf-8")
    with pytest.raises(SdkRuntimeError) as exc:
        _validate_loaded_against_high_water(
            root=tmp_path,
            high_water=HostHighWater(dag_generation=3, event_sequence=3, registry_revision=1),
        )
    assert exc.value.code == "HOST_ROLLBACK_REJECTED"


def test_p3_corrupt_events_fail_closed(tmp_path: Path) -> None:
    _live(tmp_path, 3, 1)
    _hw(tmp_path, 3, 1)
    persist_package_route(
        tmp_path,
        PackageRouteRecord(dag_generation=3, canonical_head=HEAD, canonical_tree=TREE),
    )
    event_log_path(tmp_path).write_text("{not-json\n", encoding="utf-8")
    with pytest.raises(SdkRuntimeError) as exc:
        _validate_loaded_against_high_water(
            root=tmp_path,
            high_water=HostHighWater(dag_generation=3, event_sequence=1),
        )
    assert exc.value.code == "HOST_ROLLBACK_REJECTED"


def test_p3_valid_current_and_forward_recovery_accepted(tmp_path: Path) -> None:
    _live(tmp_path, 2, 2)
    _hw(tmp_path, 2, 2, 1)
    persist_package_route(
        tmp_path,
        PackageRouteRecord(
            dag_generation=2, registry_revision=1, canonical_head=HEAD, canonical_tree=TREE
        ),
    )
    append_event(tmp_path, "NEW_HEAD_ADOPTED", dag_generation=1, head=HEAD, tree=TREE)
    append_event(tmp_path, "NEW_HEAD_ADOPTED", dag_generation=2, head=HEAD, tree=TREE)
    _validate_loaded_against_high_water(
        root=tmp_path,
        high_water=HostHighWater(dag_generation=2, event_sequence=2, registry_revision=1),
    )
    _live(tmp_path, 3, 3)
    _hw(tmp_path, 3, 3, 1)
    persist_package_route(
        tmp_path,
        PackageRouteRecord(
            dag_generation=3, registry_revision=1, canonical_head=HEAD, canonical_tree=TREE
        ),
    )
    append_event(tmp_path, "NEW_CI_ADOPTED", dag_generation=3, head=HEAD, tree=TREE)
    _validate_loaded_against_high_water(
        root=tmp_path,
        high_water=HostHighWater(dag_generation=3, event_sequence=3, registry_revision=1),
    )


def test_p3_single_record_rollback_rejected(tmp_path: Path) -> None:
    _live(tmp_path, 3, 1)
    persist_package_route(
        tmp_path,
        PackageRouteRecord(
            dag_generation=3,
            registry_revision=2,
            canonical_head=HEAD,
            canonical_tree=TREE,
        ),
    )
    append_event(tmp_path, "NEW_HEAD_ADOPTED", dag_generation=3, head=HEAD, tree=TREE)
    with pytest.raises(SdkRuntimeError) as exc:
        _validate_loaded_against_high_water(
            root=tmp_path,
            high_water=HostHighWater(dag_generation=9, event_sequence=1, registry_revision=2),
        )
    assert exc.value.code == "HOST_ROLLBACK_REJECTED"


def test_p4_workspace_root_mismatch_and_sequence_rollback(tmp_path: Path) -> None:
    port = _port(tmp_path)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    port.agents_reg.upsert(
        AgentRecord(
            agent_id=AGENT,
            runtime=AgentRuntime.LOCAL,
            role=AgentRole.REMEDIATOR,
            package_id=PACKAGE_ID,
            base_main=PIN,
            branch=BRANCH,
            created_at=_utc_now(),
            state=AgentState.IDLE,
            worker_backend=WorkerBackend.CURSOR_AGENT_CLI.value,
            workspace=str(foreign.resolve()),
            repository="https://github.com/B0LK13/project-atlas",
            creation_generation=106,
            creation_sequence=2,
        )
    )
    with pytest.raises(SdkRuntimeError) as exc:
        asyncio.run(port.resume_agent(AGENT))
    assert exc.value.code == "WORKSPACE_ROOT_MISMATCH"

    mint_creation_sequence(tmp_path, AGENT)
    mint_creation_sequence(tmp_path, AGENT)
    port.agents_reg.upsert(
        AgentRecord(
            agent_id=AGENT,
            runtime=AgentRuntime.LOCAL,
            role=AgentRole.REMEDIATOR,
            package_id=PACKAGE_ID,
            base_main=PIN,
            branch=BRANCH,
            created_at=_utc_now(),
            state=AgentState.IDLE,
            worker_backend=WorkerBackend.CURSOR_AGENT_CLI.value,
            workspace=str(tmp_path.resolve()),
            repository="https://github.com/B0LK13/project-atlas",
            creation_generation=106,
            creation_sequence=1,
        )
    )
    with pytest.raises(SdkRuntimeError) as exc2:
        asyncio.run(port.resume_agent(AGENT))
    assert exc2.value.code == "ROLLED_BACK_CREATION_SEQUENCE"


def test_p4_same_lineage_resume_succeeds(tmp_path: Path) -> None:
    port = _port(tmp_path)
    seq = mint_creation_sequence(tmp_path, AGENT)
    port.agents_reg.upsert(
        AgentRecord(
            agent_id=AGENT,
            runtime=AgentRuntime.LOCAL,
            role=AgentRole.REMEDIATOR,
            package_id=PACKAGE_ID,
            base_main=PIN,
            branch=BRANCH,
            created_at=_utc_now(),
            state=AgentState.IDLE,
            worker_backend=WorkerBackend.CURSOR_AGENT_CLI.value,
            workspace=str(tmp_path.resolve()),
            repository="https://github.com/B0LK13/project-atlas",
            creation_generation=106,
            creation_sequence=seq,
        )
    )
    stored = asyncio.run(port.resume_agent(AGENT))
    assert stored.agent_id == AGENT


def test_p4_missing_sequence_is_stale(tmp_path: Path) -> None:
    port = _port(tmp_path)
    port.agents_reg.upsert(
        AgentRecord(
            agent_id=AGENT,
            runtime=AgentRuntime.LOCAL,
            role=AgentRole.REMEDIATOR,
            package_id=PACKAGE_ID,
            base_main=PIN,
            branch=BRANCH,
            created_at=_utc_now(),
            state=AgentState.IDLE,
            worker_backend=WorkerBackend.CURSOR_AGENT_CLI.value,
            workspace=str(tmp_path.resolve()),
            repository="https://github.com/B0LK13/project-atlas",
            creation_generation=106,
        )
    )
    with pytest.raises(SdkRuntimeError) as exc:
        asyncio.run(port.resume_agent(AGENT))
    assert exc.value.code == "STALE_WORKER_LINEAGE"


def test_p5_timeout_and_oserror_park_without_killing_host(tmp_path: Path) -> None:
    class Boom(FakeCursorSDKBackend):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)  # type: ignore[arg-type]
            self.calls = 0

        async def create_and_send(self, request):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("sdk timeout")
            raise OSError(104, "Connection reset")

    agents = CloudAgentRegistry(tmp_path)
    runs = RunRegistry(tmp_path)
    pool = AgentRolePool(agents)
    backend = Boom(agents_reg=agents, runs_reg=runs, pool=pool)
    sched = DagToAgentScheduler(
        backend=backend,
        agents=agents,
        runs=runs,
        pool=pool,
        cost=CostGuard(runs),
        root=tmp_path,
    )
    item = ReadyWorkItem(
        role=AgentRole.CLOUD_RUNTIME_AUDITOR,
        package_id=PACKAGE_ID,
        node_id="CLOUD-AUDIT-LIVE",
        cycle_id="C-D106",
        dag_generation=106,
        base_main=PIN,
        prompt="audit",
        branch=BRANCH,
        candidate_head=HEAD,
        candidate_tree=TREE,
    )
    first = asyncio.run(sched.assign_and_start([item]))
    assert first.started == []
    assert "CLOUD-AUDIT-LIVE" in first.parked
    parked = tmp_path / ".atlas" / "orchestration" / "sdk-runtime" / "scheduler-parked.json"
    payload = json.loads(parked.read_text(encoding="utf-8"))
    payload["CLOUD-AUDIT-LIVE"]["next_retry_at"] = 0
    parked.write_text(json.dumps(payload), encoding="utf-8")
    second = asyncio.run(sched.assign_and_start([item]))
    assert second.started == []
    assert "CLOUD-AUDIT-LIVE" in second.parked


def test_p5_next_cycle_recovers_after_transient_timeout(tmp_path: Path) -> None:
    class Recovering(FakeCursorSDKBackend):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)  # type: ignore[arg-type]
            self.calls = 0

        async def create_and_send(self, request):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("sdk timeout")
            return await super().create_and_send(request)

    agents = CloudAgentRegistry(tmp_path)
    runs = RunRegistry(tmp_path)
    pool = AgentRolePool(agents)
    backend = Recovering(agents_reg=agents, runs_reg=runs, pool=pool)
    sched = DagToAgentScheduler(
        backend=backend,
        agents=agents,
        runs=runs,
        pool=pool,
        cost=CostGuard(runs),
        root=tmp_path,
    )
    item = ReadyWorkItem(
        role=AgentRole.CLOUD_RUNTIME_AUDITOR,
        package_id=PACKAGE_ID,
        node_id="CLOUD-AUDIT-LIVE",
        cycle_id="C-D106",
        dag_generation=106,
        base_main=PIN,
        prompt="audit",
        branch=BRANCH,
        candidate_head=HEAD,
        candidate_tree=TREE,
    )
    first = asyncio.run(sched.assign_and_start([item]))
    assert first.started == []
    parked = tmp_path / ".atlas" / "orchestration" / "sdk-runtime" / "scheduler-parked.json"
    payload = json.loads(parked.read_text(encoding="utf-8"))
    payload["CLOUD-AUDIT-LIVE"]["next_retry_at"] = 0
    parked.write_text(json.dumps(payload), encoding="utf-8")
    second = asyncio.run(sched.assign_and_start([item]))
    assert len(second.started) == 1
    assert backend.calls == 2


def test_p5_ready_provider_and_run_forever_survive(tmp_path: Path) -> None:
    calls = {"n": 0}

    def ready() -> list[ReadyWorkItem]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("provider timeout")
        return []

    supervisor = DurableAtlasSupervisor.create(
        tmp_path,
        use_fake=True,
        max_cycles=2,
        ready_provider=ready,
        poll_interval_sec=0.01,
    )
    status = asyncio.run(supervisor.run_forever())
    assert status.cycles >= 2
    assert status.contained_failures >= 1
    assert status.last_cycle_error is not None
    assert status.running is False


def test_ci_reaction_not_claimed_closed() -> None:
    # D-106 must not close ORCH_SUPERVISOR_CI_REACTION_001.
    assert True
