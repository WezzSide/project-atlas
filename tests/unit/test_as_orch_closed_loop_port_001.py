"""Closed-loop port — PR435 self-contained extension point."""

from __future__ import annotations

from pathlib import Path

from project_atlas.orchestration.sdk.closed_loop_port import (
    clear_closed_loop_hook,
    ensure_closed_loop_binding,
    get_closed_loop_hook,
    register_closed_loop_hook,
)
from project_atlas.orchestration.sdk.models import AgentRole
from project_atlas.orchestration.sdk.resident_driver import resident_tick
from project_atlas.orchestration.sdk.resident_mission import persist_mission
from project_atlas.orchestration.sdk.resident_status import load_status
from project_atlas.orchestration.sdk.scheduler import ReadyWorkItem


class _StubHook:
    def reconcile(self, root: Path, *, now: float | None = None) -> dict[str, object]:
        return {"ok": True}

    def ready_work(self, root: Path, *, capacity: int = 2) -> list[ReadyWorkItem]:
        return [
            ReadyWorkItem(
                role=AgentRole.READ_ONLY_ANALYST,
                package_id="STUB",
                node_id="STUB-1",
                cycle_id="t",
                dag_generation=1,
                base_main="bd8faa8f97df454943181d19f1e14ee826900a20",
                prompt="stub",
                critical_path_score=10,
            )
        ]

    def active_worker_count(self, root: Path) -> int:
        return 0

    def progress_state(self, root: Path) -> dict[str, object]:
        return {"MISSION_GENERATION": 1, "PROGRESS_SEQUENCE": 1}

    def closed_loop_tick(self, root: Path, *, now: float | None = None) -> dict[str, object]:
        return {"REAL_WORKER_DISPATCH_COUNT": 1, "worker_id": "stub-w"}


def test_resident_self_contained_without_hook(tmp_path: Path) -> None:
    clear_closed_loop_hook()
    persist_mission(tmp_path)
    mode = ensure_closed_loop_binding()
    # Without PR436 on path, mode is scheduler-only (or degraded only if provider imports)
    assert get_closed_loop_hook() is None
    assert mode in {
        "RESIDENT_SCHEDULER_ONLY",
        "DEGRADED_MISSION_RECONCILER_UNAVAILABLE",
    }
    r = resident_tick(tmp_path, now=2000.0, ready=[], capacity=1)
    assert r.ready_count == 0
    status = load_status(tmp_path)
    assert status.ACTIVE_WORKER_COUNT == 0
    assert status.heartbeat_sequence >= 1


def test_registered_hook_drives_ready(tmp_path: Path) -> None:
    clear_closed_loop_hook()
    register_closed_loop_hook(_StubHook())
    persist_mission(tmp_path)
    assert ensure_closed_loop_binding() == "CLOSED_LOOP_MANDATORY"
    r = resident_tick(tmp_path, now=3000.0, capacity=2)
    status = load_status(tmp_path)
    assert status.ACTIVE_WORKER_COUNT == 0
    assert r.tick_at == 3000.0
    clear_closed_loop_hook()
