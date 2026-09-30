"""D-098 durable writer-lease execution gate (L/G matrices at scheduler boundary)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from project_atlas.orchestration.sdk.backend import FakeCursorSDKBackend
from project_atlas.orchestration.sdk.cost_guard import CostGuard
from project_atlas.orchestration.sdk.lease_registry import (
    mint_governor_writer_lease,
    require_scheduler_lease,
    resolve_durable_lease,
)
from project_atlas.orchestration.sdk.models import PACKAGE_ID, AgentRole, SdkRuntimeError
from project_atlas.orchestration.sdk.package_registry import update_package_route_on_head_move
from project_atlas.orchestration.sdk.registries import CloudAgentRegistry, RunRegistry
from project_atlas.orchestration.sdk.role_pool import AgentRolePool
from project_atlas.orchestration.sdk.scheduler import DagToAgentScheduler, ReadyWorkItem

PIN = "7e797468a2eca37c959920912b1fa264df4be638"
HEAD = "cf314cc2e8ccb419815d1bbdf2f03472bac8c1ed"
TREE = "a92f886725c4d855464df750ea0cbb4bc100500e"
GEN = 93
LEASE_ID = "lease-d098-writer-93"


class SpyBackend(FakeCursorSDKBackend):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.create_calls = 0
        self.followup_calls = 0

    async def create_and_send(self, request):  # type: ignore[no-untyped-def]
        self.create_calls += 1
        return await super().create_and_send(request)

    async def send_followup(self, agent_id: str, request):  # type: ignore[no-untyped-def]
        self.followup_calls += 1
        return await super().send_followup(agent_id, request)


def _sched(tmp_path: Path) -> tuple[SpyBackend, DagToAgentScheduler]:
    agents = CloudAgentRegistry(tmp_path)
    runs = RunRegistry(tmp_path)
    pool = AgentRolePool(agents)
    backend = SpyBackend(agents_reg=agents, runs_reg=runs, pool=pool)
    sched = DagToAgentScheduler(
        backend=backend,
        agents=agents,
        runs=runs,
        pool=pool,
        cost=CostGuard(runs),
        root=tmp_path,
    )
    return backend, sched


def _item(**kwargs: object) -> ReadyWorkItem:
    base: dict[str, object] = dict(
        role=AgentRole.REMEDIATOR,
        package_id=PACKAGE_ID,
        node_id="REMEDIATE-D098",
        cycle_id="C-D098",
        dag_generation=GEN,
        base_main=PIN,
        prompt="close d097 runtime gaps",
        branch="feat/as-orch-continuation-broker-001",
        candidate_head=HEAD,
        candidate_tree=TREE,
    )
    base.update(kwargs)
    return ReadyWorkItem(**base)  # type: ignore[arg-type]


def _plant_route(tmp_path: Path, *, head: str = HEAD, generation: int = GEN) -> None:
    update_package_route_on_head_move(tmp_path, head=head, tree=TREE, dag_generation=generation)


def test_l_a_mutating_without_lease_never_calls_backend(tmp_path: Path) -> None:
    backend, sched = _sched(tmp_path)
    result = asyncio.run(sched.assign_and_start([_item(lease_id=None)]))
    assert backend.create_calls == 0
    assert backend.followup_calls == 0
    assert result.started == []
    assert result.mutating_no_lease_backend_calls == 0
    assert any("LEASE_REQUIRED" in row for row in result.lease_rejections)


def test_l_b_scheduler_reloads_durable_lease_before_backend(tmp_path: Path) -> None:
    _plant_route(tmp_path)
    lease = mint_governor_writer_lease(
        tmp_path,
        lease_id=LEASE_ID,
        role=AgentRole.REMEDIATOR,
        dag_generation=GEN,
        candidate_head=HEAD,
        candidate_tree=TREE,
        worktree=str(tmp_path),
    )
    loaded = resolve_durable_lease(tmp_path, LEASE_ID)
    assert loaded is not None
    assert loaded.mutation_authorized is True
    assert loaded.canonical_pr == 429
    backend, sched = _sched(tmp_path)
    result = asyncio.run(sched.assign_and_start([_item(lease_id=lease.lease_id)]))
    assert backend.create_calls == 1
    assert result.mutating_no_lease_backend_calls == 0
    assert len(result.started) == 1
    assert result.started[0].lease_id == LEASE_ID


def test_l_c_followup_revalidates_durable_lease(tmp_path: Path) -> None:
    _plant_route(tmp_path)
    lease = mint_governor_writer_lease(
        tmp_path,
        lease_id=LEASE_ID,
        role=AgentRole.REMEDIATOR,
        dag_generation=GEN,
        candidate_head=HEAD,
        candidate_tree=TREE,
        worktree=str(tmp_path),
    )
    backend, sched = _sched(tmp_path)
    first = asyncio.run(sched.assign_and_start([_item(lease_id=lease.lease_id)]))
    agent_id = first.started[0].agent_id
    follow = _item(
        lease_id=lease.lease_id,
        node_id="REMEDIATE-D098-FOLLOW",
        cycle_id="C-D098-F",
        prefer_followup=True,
        existing_agent_id=agent_id,
        attempt=2,
    )
    second = asyncio.run(sched.assign_and_start([follow]))
    assert backend.followup_calls == 1
    assert len(second.started) == 1
    assert second.started[0].agent_id == agent_id


def test_l_d_missing_registry_row_rejects_even_with_lease_id(tmp_path: Path) -> None:
    _plant_route(tmp_path)
    backend, sched = _sched(tmp_path)
    result = asyncio.run(sched.assign_and_start([_item(lease_id="lease-ghost")]))
    assert backend.create_calls == 0
    assert any("LEASE_REQUIRED" in row for row in result.lease_rejections)


def test_g_a_pr428_split_brain_lease_rejected(tmp_path: Path) -> None:
    _plant_route(tmp_path)
    lease = mint_governor_writer_lease(
        tmp_path,
        lease_id=LEASE_ID,
        role=AgentRole.REMEDIATOR,
        dag_generation=GEN,
        candidate_head=HEAD,
        candidate_tree=TREE,
        worktree=str(tmp_path),
    )
    backend, sched = _sched(tmp_path)
    result = asyncio.run(
        sched.assign_and_start(
            [
                _item(
                    lease_id=lease.lease_id,
                    branch="feat/as-orch-428",
                )
            ]
        )
    )
    assert backend.create_calls == 0
    assert result.started == []
    assert any("STALE_LINEAGE" in row for row in result.lease_rejections)


def test_g_b_stale_head_at_invocation_rejected(tmp_path: Path) -> None:
    _plant_route(tmp_path)
    lease = mint_governor_writer_lease(
        tmp_path,
        lease_id=LEASE_ID,
        role=AgentRole.REMEDIATOR,
        dag_generation=GEN,
        candidate_head=HEAD,
        candidate_tree=TREE,
        worktree=str(tmp_path),
    )
    with pytest.raises(SdkRuntimeError) as exc:
        require_scheduler_lease(
            tmp_path,
            _item(
                lease_id=lease.lease_id,
                candidate_head="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
            ),
            invocation=True,
        )
    assert exc.value.code in {"STALE_LINEAGE", "STALE_DIRECTIVE"}


def test_g_c_read_only_may_omit_lease(tmp_path: Path) -> None:
    backend, sched = _sched(tmp_path)
    item = ReadyWorkItem(
        role=AgentRole.READ_ONLY_ANALYST,
        package_id=PACKAGE_ID,
        node_id="RO-D098",
        cycle_id="C-RO",
        dag_generation=GEN,
        base_main=PIN,
        prompt="observe",
    )
    result = asyncio.run(sched.assign_and_start([item]))
    assert backend.create_calls == 1
    assert len(result.started) == 1


def test_l_e_head_move_expires_writer_lease(tmp_path: Path) -> None:
    from project_atlas.orchestration.sdk.lease_registry import expire_stale_leases

    _plant_route(tmp_path)
    lease = mint_governor_writer_lease(
        tmp_path,
        lease_id=LEASE_ID,
        role=AgentRole.REMEDIATOR,
        dag_generation=GEN,
        candidate_head=HEAD,
        candidate_tree=TREE,
        worktree=str(tmp_path),
    )
    expired = expire_stale_leases(tmp_path, live_generation=GEN + 1, live_head="ab" * 20)
    assert expired[lease.lease_id].expired is True
    backend, sched = _sched(tmp_path)
    result = asyncio.run(sched.assign_and_start([_item(lease_id=LEASE_ID)]))
    assert backend.create_calls == 0
    assert result.started == []
