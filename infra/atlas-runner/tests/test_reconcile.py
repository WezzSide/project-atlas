"""Reconcile / crash-recovery tests (AS-RUNNER-FABRIC-001).

RECONCILED != VERIFIED: reconciliation restores conservative state; it never
upgrades failures into successes.
"""

from __future__ import annotations

from conftest import drive_to_state

from controller import lifecycle
from controller.dockerctl import worker_container_name
from controller.reconcile import Reconciler


def _reconciler(config, store, fake_docker, fake_clock):
    return Reconciler(config=config, store=store, docker=fake_docker, clock=fake_clock)


def _running_execution(store, fake_clock):
    store.submit_task("t-rec", {"task_id": "t-rec"})
    execution_id = store.create_execution(task_id="t-rec", definition_hash="h")
    drive_to_state(store, execution_id, lifecycle.RUNNING, clock=fake_clock)
    worker = worker_container_name(execution_id)
    store.set_execution_fields(execution_id, worker_name=worker)
    return execution_id, worker


def test_worker_lost_fails_closed(config, store, fake_docker, fake_clock):
    """RUNNING in sqlite but container gone -> FAILED(worker_lost)."""
    execution_id, worker = _running_execution(store, fake_clock)
    assert fake_docker.container_exists(worker) is False
    summary = _reconciler(config, store, fake_docker, fake_clock).reconcile()
    assert summary["worker_lost"] == 1
    row = store.get_execution(execution_id)
    assert row["status"] == lifecycle.FAILED
    assert row["failure_reason"] == "worker_lost"


def test_running_container_not_touched(config, store, fake_docker, fake_clock):
    """RUNNING with a live, fresh container: no action."""
    execution_id, worker = _running_execution(store, fake_clock)
    fake_docker.containers[worker] = {"running": True, "exit_code": 0}
    summary = _reconciler(config, store, fake_docker, fake_clock).reconcile()
    assert summary["worker_lost"] == 0
    assert summary["stale_destroyed"] == 0
    assert store.get_execution(execution_id)["status"] == lifecycle.RUNNING


def test_stale_running_worker_destroyed(config, store, fake_docker, fake_clock):
    execution_id, worker = _running_execution(store, fake_clock)
    fake_docker.containers[worker] = {"running": True, "exit_code": 0}
    fake_clock.advance(config.worker.timeout_seconds + 10)
    summary = _reconciler(config, store, fake_docker, fake_clock).reconcile()
    assert summary["stale_destroyed"] == 1
    row = store.get_execution(execution_id)
    assert row["status"] == lifecycle.TIMED_OUT
    assert row["cleanup_status"] == "ok"
    assert worker not in fake_docker.containers


def test_foreign_container_not_touched(config, store, fake_docker, fake_clock):
    """A container we cannot prove ownership of: BLOCKED anomaly, untouched."""
    fake_docker.containers["atlas-worker-foreign"] = {"running": True, "exit_code": 0}
    reconciler = _reconciler(config, store, fake_docker, fake_clock)
    anomalies = reconciler.foreign_worker_anomalies()
    assert anomalies == ["atlas-worker-foreign"]
    assert "atlas-worker-foreign" in fake_docker.containers
    # and reconcile() itself must not destroy it
    reconciler.reconcile()
    assert "atlas-worker-foreign" in fake_docker.containers


def test_orphaned_owned_container_removed(config, store, fake_docker, fake_clock):
    """atlas.runner=owned but no state row and not running -> removed."""
    from controller.dockerctl import ContainerInfo

    fake_docker.containers["atlas-worker-orphan"] = {"running": False, "exit_code": 0}

    class _PsDocker:
        def __init__(self, inner):
            self.inner = inner

        def ps_all(self):
            return [
                ContainerInfo(
                    name="atlas-worker-orphan",
                    status="exited (0)",
                    labels={"atlas.runner": "owned"},
                )
            ]

        def rm(self, name):
            self.inner.containers.pop(name, None)

    reconciler = Reconciler(
        config=config, store=store, docker=_PsDocker(fake_docker), clock=fake_clock
    )
    anomalies = reconciler.foreign_worker_anomalies()
    assert anomalies == []
    assert "atlas-worker-orphan" not in fake_docker.containers


def test_stale_workspace_pruned(config, store, fake_docker, fake_clock, workspace):
    jobs_root = workspace / "jobs"
    jobs_root.mkdir(parents=True, exist_ok=True)
    fresh = jobs_root / "ex-fresh"
    fresh.mkdir()
    stale = jobs_root / "ex-stale"
    stale.mkdir()
    import os

    old = fake_clock() - (config.retention.workspace_hours + 1) * 3600
    os.utime(stale, (old, old))
    summary = _reconciler(config, store, fake_docker, fake_clock).reconcile()
    assert summary["workspaces_pruned"] == 1
    assert not stale.exists()
    assert fresh.exists()


def test_docker_ps_failure_fails_quietly(config, store, fake_clock):
    """If docker is unreachable, reconcile must not corrupt state."""

    class _BrokenDocker:
        def ps_all(self):
            from controller.dockerctl import DockerError

            raise DockerError("daemon down")

        def rm(self, name):
            from controller.dockerctl import DockerError

            raise DockerError("daemon down")

    execution_id, _worker = _running_execution(store, fake_clock)
    reconciler = Reconciler(config=config, store=store, docker=_BrokenDocker(), clock=fake_clock)
    summary = reconciler.reconcile()
    # cannot see containers -> must NOT declare the worker lost
    assert summary["worker_lost"] == 0
    assert store.get_execution(execution_id)["status"] == lifecycle.RUNNING
