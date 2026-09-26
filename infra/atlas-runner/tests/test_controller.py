"""Controller admission/lifecycle tests with fakes (AS-RUNNER-FABRIC-001).

POLLING != AUTHORITY: queued GitHub jobs are requests; the controller admits
by label subset + capacity only.
"""

from __future__ import annotations

from conftest import make_config
from controller import lifecycle
from controller.controller import Capacity, Controller


def _queued_run(run_id, attempt=1, labels=("self-hosted", "linux", "x64", "atlas", "executor")):
    return {
        "id": run_id,
        "run_attempt": attempt,
        "jobs": [
            {
                "id": run_id * 1000 + 1,
                "name": f"job-{run_id}",
                "status": "queued",
                "labels": list(labels),
            }
        ],
    }


def _make_controller(config, store, fake_docker, fake_github, fake_worker_manager):
    return Controller(
        config=config,
        store=store,
        docker=fake_docker,
        github=fake_github,
        worker_manager=fake_worker_manager,
        sleeper=lambda *_: None,
    )


def test_admission_by_label_subset(config, store, fake_docker, fake_github, fake_worker_manager):
    fake_github.queued = [
        _queued_run(1, labels=("self-hosted", "linux")),
        _queued_run(2, labels=("self-hosted", "linux", "x64", "atlas", "executor", "gpu")),
    ]
    controller = _make_controller(config, store, fake_docker, fake_github, fake_worker_manager)
    admitted = controller.admit_queued_jobs()
    assert len(admitted) == 1
    task = store.get_task(admitted[0])
    assert task is not None
    # the gpu-labelled job must never even get a task row
    assert store.get_task("gh-2-1-2001") is None


def test_duplicate_suppression_by_run_attempt(
    config, store, fake_docker, fake_github, fake_worker_manager
):
    fake_github.queued = [_queued_run(1), _queued_run(1, attempt=1)]
    controller = _make_controller(config, store, fake_docker, fake_github, fake_worker_manager)
    admitted = controller.admit_queued_jobs()
    assert len(admitted) == 1
    assert len(fake_worker_manager.ran) == 1


def test_capacity_admission_defers(config, store, fake_docker, fake_github, fake_worker_manager):
    controller = _make_controller(config, store, fake_docker, fake_github, fake_worker_manager)
    reason = controller.admission_reason(
        Capacity(
            free_memory_mb=10_000, free_disk_mb=10_000,
            active_workers=config.max_concurrent_jobs,
        )
    )
    assert reason == "capacity:max_concurrent_jobs"
    reason = controller.admission_reason(
        Capacity(free_memory_mb=0, free_disk_mb=10_000, active_workers=0)
    )
    assert reason == "capacity:min_free_memory_mb"
    reason = controller.admission_reason(
        Capacity(free_memory_mb=10_000, free_disk_mb=0, active_workers=0)
    )
    assert reason == "capacity:min_free_disk_mb"
    assert controller.admission_reason(
        Capacity(free_memory_mb=10_000, free_disk_mb=10_000, active_workers=0)
    ) is None


def test_deferred_job_stays_requested(config, store, fake_docker, fake_github, fake_worker_manager):
    """Capacity-blocked jobs stay REQUESTED and are re-evaluated next poll."""

    class _ActiveStub:
        def run_execution(self, execution, *, definition):
            execution_id = execution["execution_id"]
            fake_worker_manager.ran.append(execution_id)
            row = store.get_execution(execution_id)
            if row["status"] == lifecycle.REQUESTED:
                store.transition(execution_id, lifecycle.ADMITTED)
            store.transition(execution_id, lifecycle.PROVISIONING)
            store.transition(execution_id, lifecycle.REGISTERING)
            store.transition(execution_id, lifecycle.READY)
            store.transition(execution_id, lifecycle.ASSIGNED)
            store.transition(execution_id, lifecycle.RUNNING)
            return store.get_execution(execution_id)

    limited = make_config(
        store.db_path.parent.parent,
        max_concurrent_jobs=1,
    )
    fake_github.queued = [_queued_run(1), _queued_run(2)]
    controller = _make_controller(limited, store, fake_docker, fake_github, _ActiveStub())
    controller.admit_queued_jobs()
    task1 = store.get_task("gh-1-1-1001")
    task2 = store.get_task("gh-2-1-2001")
    assert task1["status"] == lifecycle.RUNNING  # worker active
    assert task2["status"] == lifecycle.REQUESTED  # deferred by capacity
    assert store.count_active_workers() == 1


def test_poll_once_heartbeats_and_admits(
    config, store, fake_docker, fake_github, fake_worker_manager
):
    fake_github.queued = [_queued_run(7)]
    controller = _make_controller(config, store, fake_docker, fake_github, fake_worker_manager)
    controller.poll_once()
    assert store.last_heartbeat() is not None
    assert len(fake_worker_manager.ran) == 1


def test_github_failure_fails_closed(config, store, fake_docker, fake_github, fake_worker_manager):
    fake_github.fail_auth = True
    controller = _make_controller(config, store, fake_docker, fake_github, fake_worker_manager)
    assert controller.admit_queued_jobs() == []
    assert fake_worker_manager.ran == []


def test_submit_direct_task(config, store, fake_docker, fake_github, fake_worker_manager):
    controller = _make_controller(config, store, fake_docker, fake_github, fake_worker_manager)
    outcome, detail = controller.submit_task(
        {"schema_version": 1, "task_id": "manual-1", "env": {"CI": "true"}}
    )
    assert outcome == "admitted"
    assert store.get_execution(detail)["status"] == lifecycle.COMPLETE


def test_submit_rejects_bad_env(config, store, fake_docker, fake_github, fake_worker_manager):
    controller = _make_controller(config, store, fake_docker, fake_github, fake_worker_manager)
    import pytest
    from controller.config import ConfigError

    with pytest.raises(ConfigError):
        controller.submit_task({"schema_version": 1, "task_id": "bad", "env": {"EVIL_TOKEN": "x"}})


def test_submit_idempotent_existing(config, store, fake_docker, fake_github, fake_worker_manager):
    controller = _make_controller(config, store, fake_docker, fake_github, fake_worker_manager)
    definition = {"schema_version": 1, "task_id": "dup-1"}
    outcome1, _ = controller.submit_task(definition)
    outcome2, _detail2 = controller.submit_task(definition)
    assert outcome1 == "admitted"
    assert outcome2 == "existing_terminal"
    assert len(fake_worker_manager.ran) == 1


def test_submit_conflict_rejected(config, store, fake_docker, fake_github, fake_worker_manager):
    import pytest
    from controller.state import TaskConflictError

    controller = _make_controller(config, store, fake_docker, fake_github, fake_worker_manager)
    controller.submit_task({"schema_version": 1, "task_id": "c-1", "job_name": "a"})
    with pytest.raises(TaskConflictError):
        controller.submit_task({"schema_version": 1, "task_id": "c-1", "job_name": "b"})


def test_health_report(tmp_path, store, fake_docker, fake_github, config):
    from controller.health import run_health

    store.heartbeat(1)
    report, code = run_health(
        config=config, store=store, docker=fake_docker, github=fake_github,
        now=store.last_heartbeat(),
    )
    assert report["status"] == "healthy"
    assert code == 0
    assert set(report["checks"]) >= {
        "state_db", "docker", "state_dir_writable", "config",
        "free_memory_mb", "free_disk_mb", "workers", "github_api",
    }


def test_health_degraded_when_github_unreachable(tmp_path, store, fake_docker, fake_github, config):
    from controller.health import run_health

    fake_github.fail_auth = True
    store.heartbeat(1)
    report, code = run_health(
        config=config, store=store, docker=fake_docker, github=fake_github,
        now=store.last_heartbeat(),
    )
    assert report["checks"]["github_api"]["ok"] is False
    assert report["status"] == "degraded"
    assert code == 1


def test_health_blocked_on_bad_config(tmp_path, store, fake_docker, fake_github, config):
    from controller.health import run_health

    store.heartbeat(1)
    report, code = run_health(
        config=config, store=store, docker=fake_docker, github=fake_github,
        config_text="[worker]\nnetwork = \"host\"\n", now=store.last_heartbeat(),
    )
    assert report["checks"]["config"]["ok"] is False
    assert report["status"] == "blocked"
    assert code == 2


def test_health_never_executes_work(tmp_path, store, fake_docker, fake_github, config):
    """The health lens must not run any worker lifecycle or docker mutation."""
    from controller.health import run_health

    store.heartbeat(1)
    run_health(config=config, store=store, docker=fake_docker, github=fake_github)
    mutations = {"run_worker", "stop", "kill", "rm"}
    assert mutations.isdisjoint(fake_docker.call_names())
    assert "generate_jitconfig" not in fake_github.calls
