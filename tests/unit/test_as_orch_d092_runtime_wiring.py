"""D-092 runtime wiring matrices: job CI, result plane, paths, transient, lineage."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from project_atlas.orchestration.sdk.backend import FakeCursorSDKBackend
from project_atlas.orchestration.sdk.ci_observer import (
    CiJobObservation,
    CiObservation,
    classify_failure,
    classify_with_jobs,
    failure_identity,
)
from project_atlas.orchestration.sdk.cost_guard import CostGuard
from project_atlas.orchestration.sdk.models import (
    PACKAGE_ID,
    AgentRole,
    RunRecord,
    RunStatus,
    SdkRuntimeError,
    _utc_now,
)
from project_atlas.orchestration.sdk.package_registry import (
    require_mutating_route,
    update_package_route_on_head_move,
)
from project_atlas.orchestration.sdk.registries import CloudAgentRegistry, RunRegistry
from project_atlas.orchestration.sdk.result_plane import (
    ResultEnvelope,
    append_result,
    ingest_pending,
    ingest_pending_against_registry,
    transport_state,
)
from project_atlas.orchestration.sdk.role_pool import AgentRolePool
from project_atlas.orchestration.sdk.scheduler import (
    DagToAgentScheduler,
    ReadyWorkItem,
)
from project_atlas.orchestration.sdk.security_gates import (
    BoundWorkerResult,
    SixP1RuntimeProofs,
    WorkerBackend,
    enforce_allowed_paths,
    normalize_rel_path,
    six_p1_open_count,
    six_p1_runtime_open_count,
)

PIN = "7e797468a2eca37c959920912b1fa264df4be638"
HEAD = "aeef781fddce9b92d06fe202b027d7036fd01121"
TREE = "f4a685b4668920b2db0f035fee0f3584fc436742"


def test_six_p1_defaults_partial_without_proofs() -> None:
    assert six_p1_open_count() == 6
    closed = SixP1RuntimeProofs(
        result_binding_runtime=True,
        lease_gating_runtime=True,
        allowed_paths_post_run=True,
        host_high_water_recovery=True,
        worker_lineage_persisted=True,
        transient_failure_parked=True,
    )
    assert six_p1_runtime_open_count(closed) == 0


def test_job_level_required_fail_while_run_in_progress() -> None:
    jobs = (
        CiJobObservation(
            job_id="1",
            job_name="control-plane",
            job_status="completed",
            job_conclusion="success",
            required=True,
        ),
        CiJobObservation(
            job_id="2",
            job_name="quality (ubuntu-latest, 3.12, full)",
            job_status="completed",
            job_conclusion="failure",
            required=True,
        ),
        CiJobObservation(
            job_id="3",
            job_name="quality (ubuntu-latest, 3.13, compat)",
            job_status="in_progress",
            job_conclusion=None,
            required=True,
        ),
    )
    status, failed, digest = classify_with_jobs(
        raw_status="in_progress", conclusion=None, jobs=jobs
    )
    assert status == "FAIL"
    assert failed == "2"
    assert digest is not None


def test_job_fail_head_move_stale() -> None:
    obs = CiObservation(
        head_sha=HEAD,
        run_id="100",
        status="FAIL",
        failed_required_job_id="2",
        failure_digest="a" * 64,
    )
    classified = classify_failure(observation=obs, live_head="b" * 40, current_generation=90)
    assert classified.failure_class == "STALE_SUPERSEDED"


def test_failure_identity_stable() -> None:
    a = failure_identity(head=HEAD, run_id="1", job_id="2", failure_digest="d" * 64)
    b = failure_identity(head=HEAD, run_id="1", job_id="2", failure_digest="d" * 64)
    assert a == b


def test_result_plane_expected_binding_mandatory(tmp_path: Path) -> None:
    with pytest.raises(SdkRuntimeError, match=r"expected binding"):
        ingest_pending(tmp_path, expected=None)


def test_result_plane_consume_once_via_registry(tmp_path: Path) -> None:
    agents = CloudAgentRegistry(tmp_path)
    runs = RunRegistry(tmp_path)
    run = RunRecord(
        run_id="run-iv-1",
        agent_id="cli-eb663bca-c0ad-4a65-bc20-b417cbffa287",
        package_id=PACKAGE_ID,
        lease_id="lease-iv",
        role=AgentRole.INDEPENDENT_VERIFIER,
        prompt_digest="c" * 64,
        idempotency_key="idem-iv-1",
        status=RunStatus.FINISHED,
        started_at=_utc_now(),
        completed_at=_utc_now(),
        result_digest="b" * 64,
        candidate_head=HEAD,
        candidate_tree=TREE,
        node_id="IV-LIVE",
        dag_generation=90,
        attempt=1,
    )
    runs.upsert(run)
    binding = BoundWorkerResult(
        worker_backend=WorkerBackend.CURSOR_AGENT_CLI,
        session_or_agent_id=run.agent_id,
        run_id=run.run_id,
        package_id=PACKAGE_ID,
        dag_node="IV-LIVE",
        dag_generation=90,
        role=AgentRole.INDEPENDENT_VERIFIER,
        lease_id="lease-iv",
        attempt=1,
        result_digest="b" * 64,
        candidate_head=HEAD,
        candidate_tree=TREE,
    )
    append_result(tmp_path, ResultEnvelope(source="IV", binding=binding))
    assert transport_state(tmp_path) == "OPEN"
    first = ingest_pending_against_registry(
        tmp_path, runs=runs, worker_backend=WorkerBackend.CURSOR_AGENT_CLI
    )
    assert len(first) == 1
    second = ingest_pending_against_registry(
        tmp_path, runs=runs, worker_backend=WorkerBackend.CURSOR_AGENT_CLI
    )
    assert second == []
    assert transport_state(tmp_path) == "CLOSED"
    del agents


def test_windows_path_normalization_and_escapes() -> None:
    assert normalize_rel_path(r"src\project_atlas\orchestration\sdk\a.py") == (
        "src/project_atlas/orchestration/sdk/a.py"
    )
    with pytest.raises(SdkRuntimeError):
        normalize_rel_path(r"C:\Windows\system32")
    with pytest.raises(SdkRuntimeError):
        normalize_rel_path("../escape")
    enforce_allowed_paths(
        changed_paths=[r"src\project_atlas\orchestration\sdk\security_gates.py"],
        allowed_paths=("src/project_atlas/orchestration/sdk",),
    )
    with pytest.raises(SdkRuntimeError, match=r"unauthorized"):
        enforce_allowed_paths(
            changed_paths=["SRC/PROJECT_ATLAS/OTHER/x.py"],
            allowed_paths=("src/project_atlas/orchestration/sdk",),
        )


def test_scheduler_parks_transient_without_raising(tmp_path: Path) -> None:
    agents = CloudAgentRegistry(tmp_path)
    runs = RunRegistry(tmp_path)
    pool = AgentRolePool(agents)

    class Boom(FakeCursorSDKBackend):
        async def create_and_send(self, request):  # type: ignore[no-untyped-def]
            raise SdkRuntimeError("connection reset", code="TRANSIENT_CLI_BRIDGE")

    boom = Boom(agents_reg=agents, runs_reg=runs, pool=pool)
    sched = DagToAgentScheduler(
        backend=boom, agents=agents, runs=runs, pool=pool, cost=CostGuard(runs), root=tmp_path
    )
    item = ReadyWorkItem(
        role=AgentRole.READ_ONLY_ANALYST,
        package_id=PACKAGE_ID,
        node_id="NODE-T",
        cycle_id="C1",
        dag_generation=1,
        base_main=PIN,
        prompt="x",
    )
    result = asyncio.run(sched.assign_and_start([item]))
    assert "NODE-T" in result.parked
    assert result.started == []


def test_package_registry_rejects_428_and_stale_gen(tmp_path: Path) -> None:
    update_package_route_on_head_move(tmp_path, head=HEAD, tree=TREE, dag_generation=90)
    with pytest.raises(SdkRuntimeError, match=r"STALE_LINEAGE"):
        require_mutating_route(
            tmp_path,
            target_pr=428,
            branch="feat/as-orch-continuation-broker-001",
            head=HEAD,
            dag_generation=90,
        )
    with pytest.raises(SdkRuntimeError, match=r"evidence-only"):
        require_mutating_route(
            tmp_path,
            target_pr=429,
            branch="feat/as-orch-continuation-broker-001",
            head=HEAD,
            dag_generation=89,
        )
