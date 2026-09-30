"""D-082 Cursor SDK durable runtime — unit and adversarial matrix."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from project_atlas.cli import EXIT_OK, main
from project_atlas.orchestration.sdk.auth import discover_auth, record_auth_prerequisite
from project_atlas.orchestration.sdk.backend import FakeCursorSDKBackend
from project_atlas.orchestration.sdk.cost_guard import CostGuard
from project_atlas.orchestration.sdk.idempotency import build_idempotency_key
from project_atlas.orchestration.sdk.lease_registry import mint_governor_writer_lease
from project_atlas.orchestration.sdk.models import (
    PACKAGE_ID,
    PRIMARY_BACKEND,
    STOP_HOOK_BACKEND,
    AgentRole,
    AgentRuntime,
    RunStatus,
    ScheduleRequest,
    SdkRuntimeError,
)
from project_atlas.orchestration.sdk.package_registry import update_package_route_on_head_move
from project_atlas.orchestration.sdk.registries import CloudAgentRegistry, RunRegistry
from project_atlas.orchestration.sdk.result_adapter import adapt_run_result
from project_atlas.orchestration.sdk.role_pool import AgentRolePool
from project_atlas.orchestration.sdk.scheduler import DagToAgentScheduler, ReadyWorkItem
from project_atlas.orchestration.sdk.supervisor import DurableAtlasSupervisor

PIN = "7e797468a2eca37c959920912b1fa264df4be638"
HEAD = "cf314cc2e8ccb419815d1bbdf2f03472bac8c1ed"
TREE = "a92f886725c4d855464df750ea0cbb4bc100500e"


def _backend(
    root: Path,
) -> tuple[FakeCursorSDKBackend, CloudAgentRegistry, RunRegistry, AgentRolePool]:
    agents = CloudAgentRegistry(root)
    runs = RunRegistry(root)
    pool = AgentRolePool(agents)
    backend = FakeCursorSDKBackend(agents_reg=agents, runs_reg=runs, pool=pool)
    return backend, agents, runs, pool


def test_auth_discovery_never_exposes_key(tmp_path: Path) -> None:
    discovery = discover_auth(environ={"CURSOR_API_KEY": "cursor_secret_should_not_leak"})
    dumped = discovery.model_dump_json()
    assert "cursor_secret" not in dumped
    assert discovery.cursor_api_key_available == "YES"
    # Cloud enables only when the official package is importable. CI does not
    # install the optional `orch` extra, so DISABLED is the honest result.
    if discovery.local_sdk_available == "YES":
        assert discovery.cloud_sdk_runtime == "ENABLED"
    else:
        assert discovery.cloud_sdk_runtime == "DISABLED"


def test_auth_prerequisite_deduped(tmp_path: Path) -> None:
    discovery = discover_auth(environ={})
    # Force prerequisite path when cloud disabled and we claim local unusable.
    discovery = discovery.model_copy(
        update={
            "cursor_api_key_available": "NO",
            "local_sdk_available": "NO",
            "cloud_sdk_runtime": "DISABLED",
            "prerequisite": "CURSOR_SDK_AUTH_REQUIRED",
        }
    )
    assert record_auth_prerequisite(tmp_path, discovery) is True
    assert record_auth_prerequisite(tmp_path, discovery) is False


def test_idempotency_stable_and_foreign_repo_rejected() -> None:
    a = build_idempotency_key(
        dag_generation=1, node_id="NODE-A", role=AgentRole.IMPLEMENTER, attempt=1
    )
    b = build_idempotency_key(
        dag_generation=1, node_id="NODE-A", role=AgentRole.IMPLEMENTER, attempt=1
    )
    assert a == b
    with pytest.raises(SdkRuntimeError, match="foreign"):
        build_idempotency_key(
            repository_identity="github.com/evil/other",
            dag_generation=1,
            node_id="NODE-A",
            role=AgentRole.IMPLEMENTER,
            attempt=1,
        )


def test_create_followup_retains_context(tmp_path: Path) -> None:
    backend, agents, _runs, _pool = _backend(tmp_path)

    async def _run() -> None:
        req1 = ScheduleRequest(
            role=AgentRole.IMPLEMENTER,
            package_id="PKG-A",
            node_id="NODE-1",
            cycle_id="CYCLE-1",
            dag_generation=1,
            base_main=PIN,
            prompt="implement change",
        )
        run1 = await backend.create_and_send(req1)
        assert run1.status == RunStatus.FINISHED
        assert run1.agent_id.startswith("bc-")
        req2 = ScheduleRequest(
            role=AgentRole.IMPLEMENTER,
            package_id="PKG-A",
            node_id="NODE-2",
            cycle_id="CYCLE-2",
            dag_generation=1,
            attempt=1,
            base_main=PIN,
            prompt="follow-up fix",
            prefer_followup=True,
            existing_agent_id=run1.agent_id,
        )
        run2 = await backend.send_followup(run1.agent_id, req2)
        assert run2.agent_id == run1.agent_id
        assert backend.context_len(run1.agent_id) == 2
        # Same idempotency key returns same run (no duplicate).
        again = await backend.send_followup(run1.agent_id, req2)
        assert again.run_id == run2.run_id

    asyncio.run(_run())
    assert agents.list_active(role=AgentRole.IMPLEMENTER)


def test_supervisor_restart_recovery_no_duplicate(tmp_path: Path) -> None:
    backend, agents, runs, pool = _backend(tmp_path)
    backend.auto_finish = False

    async def _run() -> None:
        req = ScheduleRequest(
            role=AgentRole.READ_ONLY_ANALYST,
            package_id="PKG-B",
            node_id="NODE-R",
            cycle_id="CYCLE-R",
            dag_generation=2,
            base_main=PIN,
            prompt="analyze",
            runtime=AgentRuntime.CLOUD,
        )
        run = await backend.create_and_send(req)
        assert run.status == RunStatus.RUNNING
        # Simulate process restart: new backend, same registries + cloud result store.
        backend2 = FakeCursorSDKBackend(agents_reg=agents, runs_reg=runs, pool=pool)
        backend2._run_results = dict(backend._run_results)
        from project_atlas.orchestration.sdk.recovery import recover_runtime

        report = await recover_runtime(backend=backend2, agents=agents, runs=runs)
        assert run.agent_id in report.resumed_agents
        assert run.run_id in report.ingested_runs
        assert runs.get(run.run_id) is not None
        assert runs.get(run.run_id).is_terminal  # type: ignore[union-attr]

    asyncio.run(_run())


def test_agent_busy_reconciles_without_duplicate(tmp_path: Path) -> None:
    backend, _agents, runs, _pool = _backend(tmp_path)
    backend.auto_finish = False

    async def _run() -> None:
        req = ScheduleRequest(
            role=AgentRole.IMPLEMENTER,
            package_id="PKG-C",
            node_id="NODE-C",
            cycle_id="CYCLE-C",
            dag_generation=3,
            base_main=PIN,
            prompt="work",
        )
        run = await backend.create_and_send(req)
        backend.raise_busy_once.add(run.agent_id)
        follow = ScheduleRequest(
            role=AgentRole.IMPLEMENTER,
            package_id="PKG-C",
            node_id="NODE-C2",
            cycle_id="CYCLE-C2",
            dag_generation=3,
            base_main=PIN,
            prompt="more",
        )
        with pytest.raises(SdkRuntimeError) as exc:
            await backend.send_followup(run.agent_id, follow)
        assert exc.value.code == "AGENT_BUSY"
        # Still a single nonterminal run for the agent.
        active = [r for r in runs.nonterminal() if r.agent_id == run.agent_id]
        assert len(active) == 1

    asyncio.run(_run())


def test_parallel_roles_get_separate_agents(tmp_path: Path) -> None:
    backend, agents, runs, pool = _backend(tmp_path)
    cost = CostGuard(runs)
    scheduler = DagToAgentScheduler(
        backend=backend,
        agents=agents,
        runs=runs,
        pool=pool,
        cost=cost,
        root=tmp_path,
    )
    update_package_route_on_head_move(tmp_path, head=HEAD, tree=TREE, dag_generation=4)
    writer = mint_governor_writer_lease(
        tmp_path,
        lease_id="lease-082-impl-4",
        role=AgentRole.IMPLEMENTER,
        dag_generation=4,
        candidate_head=HEAD,
        candidate_tree=TREE,
        worktree=str(tmp_path),
    )

    async def _run() -> None:
        items = [
            ReadyWorkItem(
                role=AgentRole.IMPLEMENTER,
                package_id=PACKAGE_ID,
                node_id="N-IMPL",
                cycle_id="C-P1",
                dag_generation=4,
                base_main=PIN,
                lease_id=writer.lease_id,
                branch="feat/as-orch-continuation-broker-001",
                candidate_head=HEAD,
                candidate_tree=TREE,
                prompt="implement",
                critical_path_score=10,
            ),
            ReadyWorkItem(
                role=AgentRole.READ_ONLY_ANALYST,
                package_id="PKG-P",
                node_id="N-RO",
                cycle_id="C-P2",
                dag_generation=4,
                base_main=PIN,
                prompt="analyze",
                critical_path_score=5,
            ),
            ReadyWorkItem(
                role=AgentRole.LOOKAHEAD,
                package_id="PKG-P",
                node_id="N-LOOK",
                cycle_id="C-P3",
                dag_generation=4,
                base_main=PIN,
                prompt="lookahead",
                critical_path_score=1,
                optional=True,
            ),
        ]
        result = await scheduler.assign_and_start(items)
        assert len(result.started) == 3
        assert len({r.agent_id for r in result.started}) == 3

    asyncio.run(_run())


def test_role_lineage_collision_fail_closed(tmp_path: Path) -> None:
    backend, agents, _runs, _pool = _backend(tmp_path)

    async def _run() -> None:
        req = ScheduleRequest(
            role=AgentRole.IMPLEMENTER,
            package_id="PKG-X",
            node_id="N-X",
            cycle_id="C-X",
            dag_generation=5,
            base_main=PIN,
            prompt="impl",
        )
        run = await backend.create_and_send(req)
        stored = agents.get(run.agent_id)
        assert stored is not None
        with pytest.raises(SdkRuntimeError) as exc:
            agents.upsert(stored.model_copy(update={"role": AgentRole.INDEPENDENT_VERIFIER}))
        assert exc.value.code == "ROLE_LINEAGE_COLLISION"

    asyncio.run(_run())


def test_result_authority_injection_rejected() -> None:
    with pytest.raises(SdkRuntimeError) as exc:
        adapt_run_result(
            run_id="run-1",
            agent_id="bc-abc",
            status="finished",
            claimed_merge_authorized=True,
        )
    assert exc.value.code == "AUTHORITY_INJECTION"


def test_forged_agent_id_rejected(tmp_path: Path) -> None:
    from pydantic import ValidationError

    agents = CloudAgentRegistry(tmp_path)
    from project_atlas.orchestration.sdk.models import AgentRecord, AgentState, _utc_now

    with pytest.raises(ValidationError):
        agents.upsert(
            AgentRecord(
                agent_id="forged-not-valid",
                runtime=AgentRuntime.CLOUD,
                role=AgentRole.IMPLEMENTER,
                package_id="PKG",
                base_main=PIN,
                created_at=_utc_now(),
                state=AgentState.IDLE,
            )
        )


def test_result_replay_conflict_fail_closed(tmp_path: Path) -> None:
    backend, _agents, runs, _pool = _backend(tmp_path)

    async def _run() -> None:
        req = ScheduleRequest(
            role=AgentRole.IMPLEMENTER,
            package_id="PKG-Y",
            node_id="N-Y",
            cycle_id="C-Y",
            dag_generation=6,
            base_main=PIN,
            prompt="impl",
        )
        run = await backend.create_and_send(req)
        with pytest.raises(SdkRuntimeError) as exc:
            runs.mark_terminal(
                run.run_id,
                status=RunStatus.FINISHED,
                result_digest="a" * 64,
            )
        assert exc.value.code == "RESULT_REPLAY"

    asyncio.run(_run())


def test_supervisor_auto_continues_without_human(tmp_path: Path) -> None:
    started: list[str] = []

    def ready() -> list[ReadyWorkItem]:
        if started:
            return []
        started.append("once")
        return [
            ReadyWorkItem(
                role=AgentRole.IMPLEMENTER,
                package_id="PKG-S",
                node_id="N-S",
                cycle_id="C-S",
                dag_generation=7,
                base_main=PIN,
                prompt="auto",
            )
        ]

    supervisor = DurableAtlasSupervisor.create(
        tmp_path, use_fake=True, ready_provider=ready, max_cycles=2, poll_interval_sec=0.01
    )

    async def _run() -> None:
        status = await supervisor.run_forever()
        assert status.human_scheduler_events == 0
        assert status.cycles == 2
        assert status.merge_authorized is False

    asyncio.run(_run())


def test_cli_sdk_auth_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    code = main(["orchestrator", "sdk-auth-status", "--root", str(tmp_path)])
    assert code == EXIT_OK


def test_primary_backend_constants() -> None:
    assert PRIMARY_BACKEND == "CURSOR_SDK_DURABLE_AGENT_RUNTIME"
    assert STOP_HOOK_BACKEND == "CURSOR_STOP_HOOK_FOLLOWUP"
