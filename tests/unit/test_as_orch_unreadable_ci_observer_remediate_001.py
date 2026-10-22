"""AS-ORCH-SDK-UNREADABLE-CI-OBSERVER-REMEDIATE-001.

Unreadable ci-observer.json must not default to CANDIDATE_DEFECT and mint a
mutation-authorized remediator. Only a readable CANDIDATE_DEFECT remediates.
"""

from __future__ import annotations

from pathlib import Path

from project_atlas.orchestration.sdk.ci_observer import (
    CiJobObservation,
    CiObservation,
    FailureClass,
    persist_observation,
)
from project_atlas.orchestration.sdk.lease_registry import (
    load_durable_leases,
    require_scheduler_lease,
)
from project_atlas.orchestration.sdk.live_dag import (
    LiveDagController,
    LiveDagState,
    persist_live_dag,
)
from project_atlas.orchestration.sdk.models import STATE_DIR_RELATIVE, AgentRole
from project_atlas.orchestration.sdk.package_registry import (
    PackageRouteRecord,
    persist_package_route,
)
from project_atlas.orchestration.sdk.scheduler import ReadyWorkItem

HEAD = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
TREE = "cccccccccccccccccccccccccccccccccccccccc"


def _bind_failing_dag(root: Path) -> None:
    persist_package_route(
        root,
        PackageRouteRecord(
            dag_generation=0,
            canonical_head=HEAD,
            canonical_tree=TREE,
        ),
    )
    persist_live_dag(
        root,
        LiveDagState(
            dag_generation=0,
            bound_head=HEAD,
            bound_tree=TREE,
            ci_status="FAIL",
            ci_run_id="run-1",
            remediation_dispatched=False,
        ),
    )


def _observation(*, failure_class: FailureClass) -> CiObservation:
    return CiObservation(
        head_sha=HEAD,
        run_id="1",
        status="FAIL",
        conclusion="failure",
        run_status="completed",
        run_conclusion="failure",
        jobs=(
            CiJobObservation(
                job_id="1",
                job_name="control-plane",
                job_status="completed",
                job_conclusion="failure",
                required=True,
            ),
        ),
        failed_required_job_id="1",
        failure_digest="x",
        failure_class=failure_class,
    )


def _remediators(root: Path) -> list[ReadyWorkItem]:
    controller = LiveDagController(root, worker_dispatch_enabled=True)
    return [
        item
        for item in controller._ready_items()
        if item.role == AgentRole.REMEDIATOR
    ]


def test_unreadable_ci_observer_does_not_mint_mutating_remediator(
    tmp_path: Path,
) -> None:
    _bind_failing_dag(tmp_path)
    (tmp_path / STATE_DIR_RELATIVE / "ci-observer.json").write_text(
        "{not-json",
        encoding="utf-8",
    )
    items = _remediators(tmp_path)
    assert items == []
    assert load_durable_leases(tmp_path) == {}


def test_infra_transient_does_not_mint_remediator(tmp_path: Path) -> None:
    _bind_failing_dag(tmp_path)
    persist_observation(tmp_path, _observation(failure_class="INFRA_TRANSIENT"))
    assert _remediators(tmp_path) == []
    assert load_durable_leases(tmp_path) == {}


def test_readable_candidate_defect_still_mints_mutating_remediator(
    tmp_path: Path,
) -> None:
    _bind_failing_dag(tmp_path)
    persist_observation(tmp_path, _observation(failure_class="CANDIDATE_DEFECT"))
    items = _remediators(tmp_path)
    assert len(items) == 1
    item = items[0]
    assert item.node_id == "REMEDIATE-LIVE"
    lease = require_scheduler_lease(tmp_path, item, invocation=True)
    assert lease is not None
    assert lease.mutation_authorized is True
    assert lease.role == AgentRole.REMEDIATOR
