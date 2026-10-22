"""CLI entrypoints for the durable SDK supervisor."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path

from project_atlas.orchestration.sdk.auth import discover_auth, record_auth_prerequisite
from project_atlas.orchestration.sdk.backend import CursorSDKExecutionBackend
from project_atlas.orchestration.sdk.ci_observer import CANONICAL_PR
from project_atlas.orchestration.sdk.cli_execution_port import CursorAgentCliExecutionPort
from project_atlas.orchestration.sdk.live_dag import LiveDagController
from project_atlas.orchestration.sdk.local_proof import run_local_sdk_proof
from project_atlas.orchestration.sdk.models import (
    PRIMARY_BACKEND,
    STOP_HOOK_BACKEND,
    SdkRuntimeError,
)
from project_atlas.orchestration.sdk.supervisor import (
    DurableAtlasSupervisor,
    status_dict,
)

EXIT_OK = 0
EXIT_ERROR = 1


def run_auth_status(*, root: Path) -> tuple[dict[str, object], int]:
    discovery = discover_auth()
    newly = record_auth_prerequisite(root, discovery)
    payload = discovery.model_dump(mode="json")
    payload["primary_continuation_backend"] = PRIMARY_BACKEND
    payload["stop_hook_backend"] = STOP_HOOK_BACKEND
    payload["prerequisite_recorded"] = newly
    payload["owner_action_required_now"] = False
    payload["merge_authorized"] = False
    payload["execution_authorized"] = False
    return payload, EXIT_OK


def run_governor_service(
    *,
    root: Path,
    max_cycles: int | None = None,
    poll_interval_sec: float = 2.0,
    use_fake: bool = False,
    candidate_head: str | None = None,
    pr_number: int = CANONICAL_PR,
) -> tuple[dict[str, object], int]:
    """Persistent host process. Returns only after stop/max_cycles."""
    from project_atlas.orchestration.autonomy.continuation_broker import (
        BrokerError,
        supersede_legacy_hook_successor,
    )
    from project_atlas.orchestration.sdk.host import (
        assert_single_supervisor_or_raise,
        clear_supervisor_stop,
        release_supervisor_lock,
        write_host_identity,
    )
    from project_atlas.orchestration.sdk.windows_bridge import (
        apply_windows_discovery_patch,
    )

    apply_windows_discovery_patch()

    try:
        assert_single_supervisor_or_raise(root)
    except SdkRuntimeError as exc:
        return (
            {
                "ok": False,
                "code": exc.code,
                "detail": str(exc),
                "merge_authorized": False,
                "execution_authorized": False,
            },
            EXIT_ERROR,
        )
    clear_supervisor_stop(root)

    with contextlib.suppress(BrokerError):
        supersede_legacy_hook_successor(root, cycle_id="D081-CI-0")

    try:
        supervisor = DurableAtlasSupervisor.create(
            root,
            use_fake=use_fake,
            poll_interval_sec=poll_interval_sec,
            max_cycles=max_cycles if max_cycles is not None else None,
        )
        cli_backend = isinstance(supervisor.backend, CursorAgentCliExecutionPort)
        real_sdk = isinstance(supervisor.backend, CursorSDKExecutionBackend) and not use_fake
        dispatch_enabled = (cli_backend or real_sdk) and not use_fake
        controller = LiveDagController(
            root,
            pr_number=pr_number,
            supervisor_pid=os.getpid(),
            real_sdk_backend=real_sdk,
            worker_dispatch_enabled=dispatch_enabled,
        )
        if candidate_head and controller.state.bound_head is None:
            controller.state.bound_head = candidate_head

        async def _ready() -> list[object]:
            _state, items = controller.tick()
            if cli_backend and isinstance(supervisor.backend, CursorAgentCliExecutionPort):
                for lease in controller._leases.values():
                    supervisor.backend.register_lease(lease)
            return list(items)

        backend_label = (
            "CURSOR_AGENT_CLI"
            if cli_backend
            else "LOCAL_SDK"
            if real_sdk
            else "FAKE_TEST_ONLY"
            if use_fake
            else "OBSERVER"
        )
        write_host_identity(
            root,
            pid=os.getpid(),
            backend=backend_label,
            package_head=controller.state.bound_head or candidate_head or "unknown",
            worktree=str(root),
        )
        controller.tick()
        if real_sdk:
            proof = run_local_sdk_proof(root)
            agent_id = str(proof.get("real_local_agent_id") or "")
            if not agent_id.startswith("agent-") and not cli_backend:
                controller.worker_dispatch_enabled = False
                controller.real_sdk_backend = False
        original_cycle = supervisor.schedule_cycle

        async def _cycle_and_mark() -> object:
            result = await original_cycle()
            for run in result.started:
                if run.node_id == "CLOUD-AUDIT-LIVE" and run.agent_id and run.run_id:
                    from project_atlas.orchestration.sdk.audit_provenance import (
                        rebind_cloud_audit_assignment,
                    )

                    rebind_cloud_audit_assignment(
                        root, worker_id=run.agent_id, run_id=run.run_id
                    )
                if run.node_id:
                    controller.mark_dispatched(run.node_id)
            if controller.state.ci_status == "PASS":
                if (
                    controller.state.cloud_runtime_audit_pass
                    and controller.state.cloud_audit_consume_id
                    and controller.state.iv_dispatched
                    and controller.state.adv_dispatched
                ):
                    supervisor.status.next_machine_action = "INGEST_IV_ADV_RESULTS"
                elif (
                    controller.state.cloud_runtime_audit_pass
                    and controller.state.cloud_audit_consume_id
                    and dispatch_enabled
                ):
                    supervisor.status.next_machine_action = "DISPATCH_IV_ADV_PARALLEL"
                elif dispatch_enabled and not controller.state.cloud_audit_dispatched:
                    supervisor.status.next_machine_action = "DISPATCH_CLOUD_RUNTIME_AUDIT"
                elif dispatch_enabled:
                    supervisor.status.next_machine_action = "AWAIT_CLOUD_AUDIT_RESULT"
                else:
                    supervisor.status.next_machine_action = "MONITOR_EXACT_HEAD_CI"
            elif controller.state.ci_status == "FAIL":
                supervisor.status.next_machine_action = "DISPATCH_REMEDIATION"
            else:
                supervisor.status.next_machine_action = "MONITOR_EXACT_HEAD_CI"
            return result

        supervisor.schedule_cycle = _cycle_and_mark  # type: ignore[method-assign,assignment]
        supervisor.ready_provider = _ready  # type: ignore[assignment]
        supervisor.status.next_machine_action = "MONITOR_EXACT_HEAD_CI"
        status = asyncio.run(supervisor.run_forever())
        payload = status_dict(status)
        payload["bound_head"] = controller.state.bound_head
        payload["bound_tree"] = controller.state.bound_tree
        payload["ci_run_id"] = controller.state.ci_run_id
        payload["ci_status"] = controller.state.ci_status
        payload["material_transitions"] = controller.state.material_transitions
        payload["target_move_detected"] = controller.state.target_move_detected
        payload["iv_dispatched"] = controller.state.iv_dispatched
        payload["adv_dispatched"] = controller.state.adv_dispatched
        return payload, EXIT_OK
    except SdkRuntimeError as exc:
        return (
            {
                "ok": False,
                "code": exc.code,
                "detail": str(exc),
                "merge_authorized": False,
                "execution_authorized": False,
            },
            EXIT_ERROR,
        )
    finally:
        release_supervisor_lock(root)


def run_governor_service_once(
    *, root: Path, use_fake: bool = True
) -> tuple[dict[str, object], int]:
    """Single schedule cycle — useful for tests and dry-run."""
    supervisor = DurableAtlasSupervisor.create(root, use_fake=use_fake, max_cycles=1)

    async def _once() -> dict[str, object]:
        await supervisor.startup_recovery()
        await supervisor.schedule_cycle()
        return status_dict(supervisor.status)

    return asyncio.run(_once()), EXIT_OK


def run_supervisor_stop(*, root: Path) -> tuple[dict[str, object], int]:
    from project_atlas.orchestration.sdk.host import request_supervisor_stop

    try:
        request_supervisor_stop(root)
    except SdkRuntimeError as exc:
        return (
            {
                "ok": False,
                "code": exc.code,
                "detail": str(exc),
                "stop_requested": False,
                "merge_authorized": False,
                "execution_authorized": False,
            },
            EXIT_ERROR,
        )
    return (
        {
            "ok": True,
            "stop_requested": True,
            "merge_authorized": False,
            "execution_authorized": False,
        },
        EXIT_OK,
    )


def print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))
