"""Crash recovery / stale-worker reconciliation (AS-RUNNER-FABRIC-001).

Contract: on startup and periodically, reconcile sqlite state against
`docker ps -a` reality. Rules (fail closed):
- state RUNNING-ish but container gone -> FAILED(worker_lost), cleanup attempted;
- container with no state row and atlas-worker-* name that is NOT ours
  (missing/foreign atlas.runner=owned label) -> do NOT touch; log BLOCKED anomaly;
- ours and stale (older than timeout) -> destroyed, state reconciled;
- stale workspaces under jobs_dir older than retention -> removed.
RECONCILED != VERIFIED: reconciliation restores a conservative state, it
never upgrades a failure into success.
"""

from __future__ import annotations

import contextlib
import shutil
import time
from pathlib import Path

from controller import lifecycle
from controller.config import ControllerConfig
from controller.dockerctl import WORKER_LABEL, WORKER_LABEL_VALUE, ContainerInfo, DockerCtl
from controller.state import StateStore


def _is_active_state(status: str) -> bool:
    return status in {
        lifecycle.PROVISIONING,
        lifecycle.REGISTERING,
        lifecycle.READY,
        lifecycle.ASSIGNED,
        lifecycle.RUNNING,
        lifecycle.COLLECTING_EVIDENCE,
        lifecycle.DEREGISTERING,
        lifecycle.DESTROYING,
    }


class Reconciler:
    def __init__(
        self,
        *,
        config: ControllerConfig,
        store: StateStore,
        docker: DockerCtl,
        clock=time.time,
    ):
        self.config = config
        self.store = store
        self.docker = docker
        self.clock = clock

    def reconcile(self) -> dict:
        """Run all reconciliation rules; returns a summary dict."""
        summary = {
            "worker_lost": 0,
            "stale_destroyed": 0,
            "foreign_blocked": 0,
            "workspaces_pruned": 0,
        }
        try:
            containers = self.docker.ps_all()
        except Exception:
            # Fail quiet and closed: without container truth we cannot tell
            # "gone" from "daemon down", so we touch nothing.
            self.store.audit("reconcile_docker_unavailable")
            summary["workspaces_pruned"] = self.prune_stale_workspaces()
            return summary
        by_name = {c.name: c for c in containers}
        for execution in self.store.active_executions():
            if not _is_active_state(execution["status"]):
                continue
            worker_name = execution.get("worker_name")
            if not worker_name:
                continue
            container = by_name.get(worker_name)
            if container is None:
                if execution["status"] in {lifecycle.RUNNING, lifecycle.ASSIGNED, lifecycle.READY}:
                    self._fail_worker_lost(execution)
                    summary["worker_lost"] += 1
                continue
            if self._is_stale(container, execution):
                self._destroy_stale(execution, container)
                summary["stale_destroyed"] += 1
        summary["workspaces_pruned"] = self.prune_stale_workspaces()
        return summary

    def _safe_ps(self) -> list[ContainerInfo]:
        try:
            return self.docker.ps_all()
        except Exception:
            return []

    def _is_stale(self, container: ContainerInfo, execution: dict) -> bool:
        if not container.running:
            return False
        base = execution.get("started_at") or execution.get("created_at") or self.clock()
        age = self.clock() - float(base)
        return age > self.config.worker.timeout_seconds

    def _fail_worker_lost(self, execution: dict) -> None:
        execution_id = execution["execution_id"]
        row = self.store.get_execution(execution_id)
        if row is None or lifecycle.is_terminal(row["status"]):
            return
        # Conservative: RUNNING_ROW without a container is a lost worker.
        from_state = row["status"]
        with contextlib.suppress(Exception):
            self.store.transition(execution_id, lifecycle.COLLECTING_EVIDENCE)
        with contextlib.suppress(Exception):
            self.store.transition(execution_id, lifecycle.DESTROYING)
        if not lifecycle.is_terminal(self.store.get_execution(execution_id)["status"]):
            self.store.transition(
                execution_id, lifecycle.FAILED,
                terminal_status="failed", failure_reason="worker_lost",
                cleanup_status="unknown",
            )
        self.store.audit(
            "reconcile_worker_lost",
            task_id=execution["task_id"],
            execution_id=execution_id,
            detail={"from_state": from_state},
        )

    def _destroy_stale(self, execution: dict, container: ContainerInfo) -> None:
        execution_id = execution["execution_id"]
        try:
            self.docker.rm(container.name)
            cleanup = "ok"
        except Exception:
            cleanup = "failed"
        row = self.store.get_execution(execution_id)
        if row and not lifecycle.is_terminal(row["status"]):
            final = (
                lifecycle.CLEANUP_REQUIRED if cleanup != "ok" else lifecycle.TIMED_OUT
            )
            self.store.transition(
                execution_id, final,
                terminal_status="timed_out" if cleanup == "ok" else "cleanup_required",
                failure_reason="stale_worker",
                cleanup_status=cleanup,
            )
        self.store.audit(
            "reconcile_stale_destroyed" if cleanup == "ok" else "reconcile_stale_cleanup_failed",
            task_id=execution["task_id"],
            execution_id=execution_id,
        )

    def prune_stale_workspaces(self) -> int:
        """Remove job workspaces older than retention.workspace_hours."""
        jobs_root = Path(self.config.paths.jobs_dir)
        try:
            jobs_root.mkdir(parents=True, exist_ok=True)
        except OSError:
            return 0
        cutoff = self.clock() - self.config.retention.workspace_hours * 3600
        pruned = 0
        for child in jobs_root.iterdir():
            if not child.is_dir():
                continue
            try:
                if child.stat().st_mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
                    pruned += 1
            except OSError:
                continue
        return pruned

    def foreign_worker_anomalies(self) -> list[str]:
        """atlas-worker-* containers we cannot prove ownership of: BLOCKED, untouched."""
        anomalies: list[str] = []
        ours = {
            e["worker_name"]
            for e in self.store.active_executions()
            if e.get("worker_name")
        }
        for container in self._safe_ps():
            if not container.name.startswith("atlas-worker-"):
                continue
            if container.name in ours:
                continue
            if container.labels.get(WORKER_LABEL) == WORKER_LABEL_VALUE:
                # Labeled ours but no state row: orphaned by a crashed controller.
                # Not running -> safe to remove; running -> BLOCKED anomaly, untouched.
                if container.running:
                    anomalies.append(container.name)
                    self.store.audit(
                        "reconcile_foreign_blocked",
                        detail={"container": container.name},
                    )
                else:
                    try:
                        self.docker.rm(container.name)
                    except Exception:
                        anomalies.append(container.name)
                        self.store.audit(
                            "reconcile_foreign_blocked",
                            detail={"container": container.name},
                        )
                continue
            anomalies.append(container.name)
            self.store.audit("reconcile_foreign_blocked", detail={"container": container.name})
        return anomalies
