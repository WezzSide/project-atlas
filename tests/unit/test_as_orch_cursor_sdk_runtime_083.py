"""D-083 Cursor SDK supervisor: fake follow-up, idempotency, recovery, CI observer."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from project_atlas.cli import EXIT_OK, main
from project_atlas.orchestration.autonomy.continuation_broker import (
    BACKEND,
    PRIMARY_CONTINUATION_BACKEND,
    BrokerPhase,
    SuccessorKind,
    enqueue_successor,
    recover_broker,
    supersede_legacy_hook_successor,
)
from project_atlas.orchestration.autonomy.models import CANONICAL_REPOSITORY_IDENTITY
from project_atlas.orchestration.sdk.backend import FakeCursorSDKBackend
from project_atlas.orchestration.sdk.ci_observer import CiObservation, persist_observation
from project_atlas.orchestration.sdk.cost_guard import CostGuard
from project_atlas.orchestration.sdk.idempotency import build_idempotency_key
from project_atlas.orchestration.sdk.models import AgentRole, RunStatus, ScheduleRequest
from project_atlas.orchestration.sdk.recovery import recover_runtime
from project_atlas.orchestration.sdk.registries import CloudAgentRegistry, RunRegistry
from project_atlas.orchestration.sdk.role_pool import AgentRolePool
from project_atlas.orchestration.sdk.scheduler import DagToAgentScheduler, ReadyWorkItem
from project_atlas.orchestration.sdk.supervisor import DurableAtlasSupervisor

PIN = "7e797468a2eca37c959920912b1fa264df4be638"
TREE = "3cb40645c343edf8f8ab95f6ddf3a819e2110ef2"


def _stack(tmp_path: Path) -> tuple[FakeCursorSDKBackend, DagToAgentScheduler]:
    agents = CloudAgentRegistry(tmp_path)
    runs = RunRegistry(tmp_path)
    pool = AgentRolePool(agents)
    backend = FakeCursorSDKBackend(agents_reg=agents, runs_reg=runs, pool=pool)
    sched = DagToAgentScheduler(
        backend=backend, agents=agents, runs=runs, pool=pool, cost=CostGuard(runs)
    )
    return backend, sched


def test_followup_run_without_human(tmp_path: Path) -> None:
    backend, sched = _stack(tmp_path)
    first = ReadyWorkItem(
        role=AgentRole.LOCAL_AUTHENTIC_WORKER,
        package_id="AS-ORCH-CONTINUATION-BROKER-001",
        node_id="SDK-SMOKE-A",
        cycle_id="CYCLE-SDK-A",
        dag_generation=83,
        base_main=PIN,
        prompt="bounded read-only smoke A",
    )
    started = asyncio.run(sched.assign_and_start([first]))
    assert len(started.started) == 1
    run_a = started.started[0]
    assert run_a.status == RunStatus.FINISHED
    follow = ReadyWorkItem(
        role=AgentRole.LOCAL_AUTHENTIC_WORKER,
        package_id="AS-ORCH-CONTINUATION-BROKER-001",
        node_id="SDK-SMOKE-B",
        cycle_id="CYCLE-SDK-B",
        dag_generation=83,
        base_main=PIN,
        prompt="bounded follow-up B",
        prefer_followup=True,
        existing_agent_id=run_a.agent_id,
        attempt=2,
    )
    second = asyncio.run(sched.assign_and_start([follow]))
    assert len(second.started) == 1
    run_b = second.started[0]
    assert run_b.agent_id == run_a.agent_id
    assert run_b.run_id != run_a.run_id
    assert backend.context_len(run_a.agent_id) == 2


def test_idempotent_retry_does_not_duplicate(tmp_path: Path) -> None:
    backend, _sched = _stack(tmp_path)
    req = ScheduleRequest(
        role=AgentRole.IMPLEMENTER,
        package_id="AS-ORCH-CONTINUATION-BROKER-001",
        node_id="NODE-1",
        cycle_id="CYCLE-1",
        dag_generation=1,
        attempt=1,
        base_main=PIN,
        prompt="do work",
    )
    first = asyncio.run(backend.create_and_send(req))
    again = asyncio.run(backend.create_and_send(req))
    assert first.run_id == again.run_id
    key = build_idempotency_key(
        dag_generation=1, node_id="NODE-1", role=AgentRole.IMPLEMENTER, attempt=1
    )
    assert first.idempotency_key == key


def test_supervisor_restart_recovers_without_duplicate(tmp_path: Path) -> None:
    backend, _sched = _stack(tmp_path)
    backend.auto_finish = False
    req = ScheduleRequest(
        role=AgentRole.REMEDIATOR,
        package_id="AS-ORCH-CONTINUATION-BROKER-001",
        node_id="NODE-R",
        cycle_id="CYCLE-R",
        dag_generation=2,
        attempt=1,
        base_main=PIN,
        prompt="remediate",
    )
    run = asyncio.run(backend.create_and_send(req))
    assert run.status == RunStatus.RUNNING
    report = asyncio.run(
        recover_runtime(backend=backend, agents=backend.agents_reg, runs=backend.runs_reg)
    )
    assert run.agent_id in report.resumed_agents
    recovered = backend.runs_reg.get(run.run_id)
    assert recovered is not None
    assert recovered.run_id == run.run_id


def test_legacy_d081_successor_is_superseded_not_replayed(tmp_path: Path) -> None:
    enqueue_successor(
        tmp_path,
        cycle_id="D081-CI-0",
        kind=SuccessorKind.CI_PENDING_WITH_OBSERVER,
        trusted_main=PIN,
        trusted_tree=TREE,
        repository_identity=CANONICAL_REPOSITORY_IDENTITY,
        next_action_class="MONITOR_EXACT_HEAD_CI",
    )
    updated = supersede_legacy_hook_successor(tmp_path, cycle_id="D081-CI-0")
    assert updated.phase == BrokerPhase.AWAITING_RESULT
    assert updated.external_wait_identity == "SUPERSEDED_BY_SDK_SUPERVISOR"
    assert recover_broker(tmp_path) is not None
    assert BACKEND == "CURSOR_STOP_HOOK_FOLLOWUP"
    assert PRIMARY_CONTINUATION_BACKEND == "CURSOR_SDK_DURABLE_AGENT_RUNTIME"


def test_ci_observer_persists_without_prompt(tmp_path: Path) -> None:
    obs = CiObservation(head_sha=PIN, run_id="32397435014", status="PENDING")
    path = persist_observation(tmp_path, obs)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["observer"] == "SDK_SUPERVISOR"
    assert payload["status"] == "PENDING"
    assert "prompt" not in payload


def test_independence_roles_cannot_reuse_implementer(tmp_path: Path) -> None:
    agents = CloudAgentRegistry(tmp_path)
    pool = AgentRolePool(agents)
    with pytest.raises(Exception, match="independence") as exc:
        pool.require_new_agent(AgentRole.INDEPENDENT_VERIFIER, reason="followup_from_implementer")
    assert exc.value.code == "INDEPENDENCE_REQUIRED"


def test_cli_sdk_auth_status(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["orchestrator", "sdk-auth-status", "--root", str(tmp_path)]) == EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["cursor_api_key_available"] in {"YES", "NO"}
    assert out["merge_authorized"] is False


def test_supervisor_create_observe_only_without_key(tmp_path: Path) -> None:
    supervisor = DurableAtlasSupervisor.create(tmp_path, use_fake=True, max_cycles=1)
    assert supervisor.status.merge_authorized is False
    assert supervisor.status.next_machine_action_executing_or_scheduled is True


def test_cancelled_ci_is_not_classified_as_fail() -> None:
    from project_atlas.orchestration.sdk.ci_observer import (
        CiObservation,
        classify_against_live_head,
        classify_exact_head_status,
    )

    assert classify_exact_head_status(raw_status="completed", conclusion="cancelled") == (
        "CANCELLED"
    )
    assert classify_exact_head_status(raw_status="completed", conclusion="failure") == "FAIL"
    old = CiObservation(
        head_sha="b3b0e3048a605703eb90fc893545f937e794ef6f",
        run_id="32399649516",
        status="CANCELLED",
        conclusion="cancelled",
    )
    live = "8a02af94b0c41df1bc62940f24015c6930561a4b"
    classified = classify_against_live_head(exact=old, live_head=live)
    assert classified.status == "STALE_SUPERSEDED"


def test_live_dag_adopts_new_head_and_new_ci(tmp_path: Path) -> None:
    from project_atlas.orchestration.sdk.ci_observer import (
        CiObservation,
        PrHeadRef,
        persist_observation,
    )
    from project_atlas.orchestration.sdk.event_log import read_events
    from project_atlas.orchestration.sdk.live_dag import LiveDagController

    persist_observation(
        tmp_path,
        CiObservation(
            head_sha="b3b0e3048a605703eb90fc893545f937e794ef6f",
            run_id="32399649516",
            status="CANCELLED",
            conclusion="cancelled",
        ),
    )
    live = PrHeadRef(
        pr_number=429,
        head_sha="8a02af94b0c41df1bc62940f24015c6930561a4b",
        tree_sha="a897d79f3a03bb9cd3933b59ca895b6bc44191dd",
    )
    observations = {
        "b3b0e3048a605703eb90fc893545f937e794ef6f": CiObservation(
            head_sha="b3b0e3048a605703eb90fc893545f937e794ef6f",
            run_id="32399649516",
            status="CANCELLED",
            conclusion="cancelled",
        ),
        "8a02af94b0c41df1bc62940f24015c6930561a4b": CiObservation(
            head_sha="8a02af94b0c41df1bc62940f24015c6930561a4b",
            run_id="32399733297",
            status="PENDING",
            conclusion=None,
        ),
    }
    controller = LiveDagController(
        tmp_path,
        refresh=lambda: live,
        observe=lambda sha: observations[sha],
        real_sdk_backend=True,
    )
    state, items = controller.tick()
    assert state.target_move_detected is True
    assert state.new_head_adopted is True
    assert state.new_ci_adopted is True
    assert state.bound_head == "8a02af94b0c41df1bc62940f24015c6930561a4b"
    assert state.ci_run_id == "32399733297"
    assert state.previous_ci_classification == "STALE_SUPERSEDED"
    assert state.material_transitions >= 2
    names = [event.event for event in read_events(tmp_path)]
    assert "OLD_CI_CANCELLED" in names
    assert "OLD_CI_SUPERSEDED" in names
    assert "NEW_HEAD_ADOPTED" in names
    assert "NEW_CI_ADOPTED" in names
    assert state.ci_status == "PENDING"
    assert all(item.role != AgentRole.INDEPENDENT_VERIFIER for item in items)
    assert all(item.role != AgentRole.SECURITY_REVIEWER for item in items)
    assert all(item.role == AgentRole.CLOUD_RUNTIME_AUDITOR for item in items)


def test_live_dag_dispatches_iv_and_adv_on_pass(tmp_path: Path) -> None:
    from project_atlas.orchestration.sdk.audit_provenance import (
        mint_cloud_audit_assignment,
    )
    from project_atlas.orchestration.sdk.ci_observer import CiObservation, PrHeadRef
    from project_atlas.orchestration.sdk.live_dag import (
        LiveDagController,
        LiveDagState,
        persist_live_dag,
    )
    from project_atlas.orchestration.sdk.models import PACKAGE_ID, AgentRole
    from project_atlas.orchestration.sdk.result_plane import ResultEnvelope, append_result
    from project_atlas.orchestration.sdk.security_gates import (
        BoundWorkerResult,
        WorkerBackend,
    )

    head = "8a02af94b0c41df1bc62940f24015c6930561a4b"
    tree = "a897d79f3a03bb9cd3933b59ca895b6bc44191dd"
    auditor = "cli-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    mint_cloud_audit_assignment(
        tmp_path,
        assignment_id="assign-cloud-audit-90",
        dag_generation=90,
        candidate_head=head,
        candidate_tree=tree,
        worker_id=auditor,
        run_id="run-cloud-audit-90",
        implementer_worker_id="cli-writer-00000000-0000-0000-0000-000000000001",
    )
    append_result(
        tmp_path,
        ResultEnvelope(
            source="CLOUD_RUNTIME_AUDITOR",
            binding=BoundWorkerResult(
                worker_backend=WorkerBackend.CURSOR_AGENT_CLI,
                session_or_agent_id=auditor,
                run_id="run-cloud-audit-90",
                package_id=PACKAGE_ID,
                dag_node="CLOUD-AUDIT-LIVE",
                dag_generation=90,
                role=AgentRole.CLOUD_RUNTIME_AUDITOR,
                lease_id="lease-cloud-audit-90",
                attempt=1,
                result_digest="b" * 64,
                candidate_head=head,
                candidate_tree=tree,
            ),
            payload={
                "ASSIGNMENT_ID": "assign-cloud-audit-90",
                "SIX_P1_RUNTIME_OPEN_COUNT": 0,
                "WIRING_VERIFIED": {"RESULT_BINDING": "PASS"},
                "AUDIT_RESULT": "PASS",
            },
        ),
    )
    persist_live_dag(
        tmp_path,
        LiveDagState(
            bound_head=head,
            bound_tree=tree,
            ci_run_id="32399733297",
            ci_status="PENDING",
            dag_generation=90,
        ),
    )
    live = PrHeadRef(
        pr_number=429,
        head_sha="8a02af94b0c41df1bc62940f24015c6930561a4b",
        tree_sha="a897d79f3a03bb9cd3933b59ca895b6bc44191dd",
    )
    controller = LiveDagController(
        tmp_path,
        refresh=lambda: live,
        observe=lambda _sha: CiObservation(
            head_sha="8a02af94b0c41df1bc62940f24015c6930561a4b",
            run_id="32399733297",
            status="PASS",
            conclusion="success",
        ),
        real_sdk_backend=True,
    )
    _state, items = controller.tick()
    roles = {item.role for item in items}
    assert roles == {AgentRole.INDEPENDENT_VERIFIER, AgentRole.SECURITY_REVIEWER}
    iv = next(item for item in items if item.role == AgentRole.INDEPENDENT_VERIFIER)
    adv = next(item for item in items if item.role == AgentRole.SECURITY_REVIEWER)
    assert iv.candidate_head == adv.candidate_head
    assert iv.cycle_id != adv.cycle_id
