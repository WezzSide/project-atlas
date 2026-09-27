"""Failure matrix A-L (AS-RUNNER-FABRIC-001). Every scenario must fail closed:
no false success, no silent COMPLETE, secrets never leak.
"""

from __future__ import annotations

import json

from conftest import drive_to_state

from controller import lifecycle
from controller.controller import Controller
from controller.dockerctl import worker_container_name
from controller.reconcile import Reconciler
from controller.worker import WorkerManager


def _manager(config, store, fake_docker, fake_github, fake_clock):
    return WorkerManager(
        config=config, store=store, docker=fake_docker, github=fake_github,
        token_provider=fake_github.token_provider, source_revision="f" * 40, clock=fake_clock,
    )


def _definition(**overrides):
    base = {
        "schema_version": 1,
        "task_id": "task-1",
        "github_run_id": 555,
        "github_run_attempt": 1,
        "runner_name": "atlas-task-1",
    }
    base.update(overrides)
    return base


def _submit(config, store, fake_docker, fake_github, fake_clock, definition):
    store.submit_task(definition["task_id"], definition)
    execution_id = store.create_execution(
        task_id=definition["task_id"],
        definition_hash="h",
        github_run_id=definition.get("github_run_id"),
        github_run_attempt=definition.get("github_run_attempt", 1),
    )
    store.transition(execution_id, lifecycle.ADMITTED)
    manager = _manager(config, store, fake_docker, fake_github, fake_clock)
    execution = store.get_execution(execution_id)
    manager.run_execution(execution, definition=definition)
    return execution_id


def test_a_provisioning_failure_fails_closed(config, store, fake_docker, fake_github, fake_clock):
    fake_docker.fail_run = "docker daemon error"
    execution_id = _submit(config, store, fake_docker, fake_github, fake_clock, _definition())
    row = store.get_execution(execution_id)
    assert row["status"] == lifecycle.FAILED
    assert row["failure_reason"].startswith("provision_failed")
    # cleanup attempted: rm probed for the container
    assert "container_exists" in fake_docker.call_names()


def test_b_malformed_task_rejected_no_state_mutation(store):
    from controller.schemas import validate_worker_task

    errors = validate_worker_task({"schema_version": 1})  # missing task_id
    assert errors
    errors = validate_worker_task({"schema_version": 1, "task_id": "ok", "unexpected": 1})
    assert errors
    # no task row was ever created
    assert store.get_task("ok") is None


def test_c_concurrent_duplicate_single_worker(config, store, fake_docker, fake_github, fake_clock):
    import threading

    outcomes = []

    def worker():
        definition = _definition(task_id="dup-task")
        try:
            outcome, _status = store.submit_task("dup-task", definition)
            outcomes.append(outcome)
        except Exception as exc:  # pragma: no cover - defensive
            outcomes.append(f"error:{exc}")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["admitted", "existing_active"]
    assert store.count_active_workers() == 0


def test_d_registration_auth_failure_no_secret_leak(
    config, store, fake_docker, fake_github, fake_clock, workspace
):
    fake_github.fail_auth = True
    execution_id = _submit(config, store, fake_docker, fake_github, fake_clock, _definition())
    row = store.get_execution(execution_id)
    assert row["status"] == lifecycle.FAILED
    assert row["failure_reason"].startswith("auth:")
    # no worker left, no container was ever started
    assert fake_docker.containers == {}
    assert "run_worker" not in fake_docker.call_names()
    # token never appears in any written file
    token = fake_github.token_provider.token
    for path in workspace.rglob("*"):
        if path.is_file():
            assert token not in path.read_text(encoding="utf-8", errors="ignore")


def test_e_nonzero_workload_fails_with_evidence(
    config, store, fake_docker, fake_github, fake_clock
):
    definition = _definition(task_id="task-e2")
    store.submit_task(definition["task_id"], definition)
    execution_id = store.create_execution(
        task_id=definition["task_id"], definition_hash="h",
        github_run_id=555, github_run_attempt=1,
    )
    store.transition(execution_id, lifecycle.ADMITTED)
    # make the container exit 1 as soon as it is inspected
    worker = worker_container_name(execution_id)
    fake_docker.inspect_seq[worker] = [{"Running": False, "ExitCode": 1}]
    manager = _manager(config, store, fake_docker, fake_github, fake_clock)
    manager.run_execution(store.get_execution(execution_id), definition=definition)
    row = store.get_execution(execution_id)
    assert row["status"] == lifecycle.FAILED
    assert row["failure_reason"] == "nonzero_exit:1"
    assert row["cleanup_status"] == "ok"
    with open(row["evidence_path"]) as handle:
        doc = json.load(handle)
    assert doc["terminal_status"] == "failed"
    assert doc["exit_code"] == 1


def test_f_timeout_kills_container(config, store, fake_docker, fake_github, fake_clock):
    definition = _definition(task_id="task-f")
    store.submit_task(definition["task_id"], definition)
    execution_id = store.create_execution(
        task_id=definition["task_id"],
        definition_hash="h",
        github_run_id=555,
        github_run_attempt=1,
    )
    store.transition(execution_id, lifecycle.ADMITTED)
    worker = worker_container_name(execution_id)
    # container stays Running forever -> controller must enforce timeout
    fake_docker.inspect_seq[worker] = [{"Running": True}] * 10_000
    manager = _manager(config, store, fake_docker, fake_github, fake_clock)
    manager.run_execution(store.get_execution(execution_id), definition=definition)
    row = store.get_execution(execution_id)
    assert row["status"] == lifecycle.TIMED_OUT
    assert row["terminal_status"] == "timed_out"
    assert "stop" in fake_docker.call_names()
    assert "kill" in fake_docker.call_names()
    assert worker not in fake_docker.containers
    with open(row["evidence_path"]) as handle:
        doc = json.load(handle)
    assert doc["terminal_status"] == "timed_out"


def test_g_worker_lost_mid_run(config, store, fake_docker, fake_github, fake_clock):
    definition = _definition(task_id="task-g")
    store.submit_task(definition["task_id"], definition)
    execution_id = store.create_execution(
        task_id=definition["task_id"], definition_hash="h", github_run_id=555, github_run_attempt=1
    )
    store.transition(execution_id, lifecycle.ADMITTED)
    worker_container_name(execution_id)
    # container vanishes after the first inspect: worker lost mid-run
    real_inspect = fake_docker.inspect
    seen = {"n": 0}

    def vanishing_inspect(name):
        seen["n"] += 1
        if seen["n"] >= 2:
            fake_docker.containers.pop(name, None)
        return real_inspect(name)

    fake_docker.inspect = vanishing_inspect  # type: ignore[method-assign]
    manager = _manager(config, store, fake_docker, fake_github, fake_clock)
    manager.run_execution(store.get_execution(execution_id), definition=definition)
    row = store.get_execution(execution_id)
    assert row["status"] == lifecycle.FAILED
    assert row["failure_reason"] == "worker_lost"


def test_h_controller_restart_recovers(
    config, store, fake_docker, fake_github, fake_clock, workspace
):
    """Mid-lifecycle restart: state reopens, reconcile resolves, no duplicate."""
    task_id = "gh-555-1-9"
    definition = {
        "task_id": task_id,
        "github_run_id": 555,
        "github_run_attempt": 1,
        "github_job_id": 9,
        "job_name": "",
        "labels": ["self-hosted"],
    }
    store.submit_task(task_id, definition)
    execution_id = store.create_execution(
        task_id=task_id,
        definition_hash="h",
        github_run_id=555,
        github_run_attempt=1,
        github_job_id=9,
    )
    store.transition(execution_id, lifecycle.ADMITTED)
    store.transition(execution_id, lifecycle.PROVISIONING)
    drive_to_state(store, execution_id, lifecycle.RUNNING)
    worker = worker_container_name(execution_id)
    store.set_execution_fields(execution_id, worker_name=worker)
    db_path = store.db_path
    store.close()

    reopened = type(store)(db_path)
    # container died while controller was down
    Reconciler(config=config, store=reopened, docker=fake_docker, clock=fake_clock).reconcile()
    row = reopened.get_execution(execution_id)
    assert row["status"] == lifecycle.FAILED
    assert row["failure_reason"] == "worker_lost"
    # a later duplicate poll for the same run must not respawn a worker
    from conftest import FakeGitHub

    github2 = FakeGitHub()
    github2.queued = [
        {
            "id": 555,
            "run_attempt": 1,
            "jobs": [{"id": 9, "status": "queued", "labels": ["self-hosted"]}],
        }
    ]
    controller = Controller(
        config=config, store=reopened, docker=fake_docker, github=github2,
        sleeper=lambda *_: None,
    )
    controller.admit_queued_jobs()
    # same (run, attempt, job) after restart -> duplicate suppressed, no respawn
    assert reopened.find_execution_by_job(555, 1, 9)["execution_id"] == execution_id
    assert "run_worker" not in fake_docker.call_names()
    reopened.close()


def test_i_stale_worker_cleanup(config, store, fake_docker, fake_clock):
    definition = _definition(task_id="task-i")
    store.submit_task(definition["task_id"], definition)
    execution_id = store.create_execution(
        task_id=definition["task_id"], definition_hash="h", github_run_id=555, github_run_attempt=1
    )
    store.transition(execution_id, lifecycle.ADMITTED)
    store.transition(execution_id, lifecycle.PROVISIONING)
    drive_to_state(store, execution_id, lifecycle.RUNNING, clock=fake_clock)
    worker = worker_container_name(execution_id)
    store.set_execution_fields(execution_id, worker_name=worker)
    fake_docker.containers[worker] = {"running": True, "exit_code": 0}
    fake_clock.advance(config.worker.timeout_seconds + 5)
    summary = Reconciler(
        config=config, store=store, docker=fake_docker, clock=fake_clock
    ).reconcile()
    assert summary["stale_destroyed"] == 1
    row = store.get_execution(execution_id)
    assert row["status"] == lifecycle.TIMED_OUT
    assert worker not in fake_docker.containers


def test_j_workspace_escape_rejected(
    config, store, fake_docker, fake_github, fake_clock, workspace
):
    jobs_root = (workspace / "jobs").resolve()
    for evil in ("../outside", "..", "/abs/path"):
        with __import__("pytest").raises(ValueError):
            from atlas_contracts.identity import resolve_under_root

            resolve_under_root(jobs_root, evil, label="workspace")
    # and no writes happened outside the jobs root
    assert not (workspace / "outside").exists()


def test_k_evidence_validation_failure_never_complete(
    config, store, fake_docker, fake_github, fake_clock
):
    """A workspace whose evidence fragment produces an invalid doc -> FAILED."""

    def broken_build_evidence(**kwargs):
        doc = real_build(**kwargs)
        del doc["cleanup_status"]  # simulate evidence generation bug
        return doc

    from controller import evidence as evidence_mod

    real_build = evidence_mod.build_evidence
    evidence_mod.build_evidence = broken_build_evidence  # type: ignore[assignment]
    try:
        definition = _definition(task_id="task-k")
        store.submit_task(definition["task_id"], definition)
        execution_id = store.create_execution(
            task_id=definition["task_id"],
            definition_hash="h",
            github_run_id=555,
            github_run_attempt=1,
        )
        store.transition(execution_id, lifecycle.ADMITTED)
        manager = _manager(config, store, fake_docker, fake_github, fake_clock)
        manager.run_execution(store.get_execution(execution_id), definition=definition)
    finally:
        evidence_mod.build_evidence = real_build  # type: ignore[assignment]
    row = store.get_execution(execution_id)
    assert row["status"] == lifecycle.FAILED
    assert row["failure_reason"] == "evidence_validation"
    assert row["status"] != lifecycle.COMPLETE


def test_l_cleanup_failure_is_terminal_cleanup_required(
    config, store, fake_docker, fake_github, fake_clock
):
    definition = _definition(task_id="task-l")
    store.submit_task(definition["task_id"], definition)
    execution_id = store.create_execution(
        task_id=definition["task_id"], definition_hash="h", github_run_id=555, github_run_attempt=1
    )
    store.transition(execution_id, lifecycle.ADMITTED)
    worker = worker_container_name(execution_id)
    fake_docker.inspect_seq[worker] = [{"Running": False, "ExitCode": 0}]
    fake_docker.fail_rm.add(worker)
    manager = _manager(config, store, fake_docker, fake_github, fake_clock)
    manager.run_execution(store.get_execution(execution_id), definition=definition)
    row = store.get_execution(execution_id)
    assert row["status"] == lifecycle.CLEANUP_REQUIRED
    assert row["cleanup_status"] == "failed"
