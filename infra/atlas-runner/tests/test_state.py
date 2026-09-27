"""State store tests: idempotency, conflicts, leases, transitions, recovery.

RUNNING_ROW != WORKER ALIVE: state truth is a model, reconciled elsewhere.
"""

from __future__ import annotations

import pytest
from conftest import drive_to_state

from controller import lifecycle
from controller.state import (
    StateStore,
    TaskConflictError,
    TransitionError,
    definition_hash,
)


def test_submit_is_idempotent(store):
    definition = {"task_id": "t1", "x": 1}
    outcome, status = store.submit_task("t1", definition)
    assert (outcome, status) == ("admitted", lifecycle.REQUESTED)
    outcome2, status2 = store.submit_task("t1", definition)
    assert outcome2 == "existing_active"
    assert status2 == lifecycle.REQUESTED
    # exactly one task row
    assert store.get_task("t1") is not None


def test_conflicting_definition_rejected(store):
    store.submit_task("t1", {"task_id": "t1", "x": 1})
    with pytest.raises(TaskConflictError):
        store.submit_task("t1", {"task_id": "t1", "x": 2})


def test_terminal_task_reports_existing_terminal(store):
    store.submit_task("t1", {"task_id": "t1"})
    execution_id = store.create_execution(task_id="t1", definition_hash="aa")
    drive_to_state(store, execution_id, lifecycle.RUNNING)
    store.transition(execution_id, lifecycle.COLLECTING_EVIDENCE)
    store.transition(execution_id, lifecycle.DEREGISTERING)
    store.transition(execution_id, lifecycle.DESTROYING)
    store.transition(execution_id, lifecycle.COMPLETE, terminal_status="complete")
    store.update_task_status("t1", lifecycle.COMPLETE)
    outcome, _ = store.submit_task("t1", {"task_id": "t1"})
    assert outcome == "existing_terminal"


def test_definition_hash_is_canonical():
    a = definition_hash({"b": 2, "a": 1})
    b = definition_hash({"a": 1, "b": 2})
    assert a == b
    assert len(a) == 64


def test_valid_transitions(store):
    execution_id = store.create_execution(task_id="t", definition_hash="h")
    store.transition(execution_id, lifecycle.ADMITTED)
    store.transition(execution_id, lifecycle.PROVISIONING)
    store.transition(execution_id, lifecycle.REGISTERING)
    store.transition(execution_id, lifecycle.READY)
    store.transition(execution_id, lifecycle.ASSIGNED)
    store.transition(execution_id, lifecycle.RUNNING)
    store.transition(execution_id, lifecycle.COLLECTING_EVIDENCE)
    store.transition(execution_id, lifecycle.DEREGISTERING)
    store.transition(execution_id, lifecycle.DESTROYING)
    store.transition(execution_id, lifecycle.COMPLETE, terminal_status="complete")
    row = store.get_execution(execution_id)
    assert row["status"] == lifecycle.COMPLETE
    assert row["terminal_status"] == "complete"
    assert row["finished_at"] is not None


def test_invalid_transition_rejected(store):
    execution_id = store.create_execution(task_id="t", definition_hash="h")
    with pytest.raises(TransitionError):
        store.transition(execution_id, lifecycle.RUNNING)  # skips ahead
    store.transition(execution_id, lifecycle.ADMITTED)
    with pytest.raises(TransitionError):
        store.transition(execution_id, lifecycle.COMPLETE)  # not reachable from ADMITTED


def test_terminal_states_are_absorbing(store):
    execution_id = store.create_execution(task_id="t", definition_hash="h")
    store.transition(execution_id, lifecycle.ADMITTED)
    store.transition(execution_id, lifecycle.PROVISIONING)
    store.transition(execution_id, lifecycle.DESTROYING)
    store.transition(execution_id, lifecycle.FAILED, terminal_status="failed")
    with pytest.raises(TransitionError):
        store.transition(execution_id, lifecycle.COMPLETE)


def test_crash_recovery_reopens_state(workspace):
    """A new StateStore over the same file sees prior rows (durability)."""
    store = StateStore(workspace / "state" / "crash.db")
    store.submit_task("t1", {"task_id": "t1"})
    execution_id = store.create_execution(task_id="t1", definition_hash="h")
    drive_to_state(store, execution_id, lifecycle.RUNNING)
    store.heartbeat(1234)
    store.close()

    reopened = StateStore(workspace / "state" / "crash.db")
    row = reopened.get_execution(execution_id)
    assert row["status"] == lifecycle.RUNNING
    assert reopened.last_heartbeat() is not None
    reopened.transition(execution_id, lifecycle.FAILED, terminal_status="failed")
    reopened.close()


def test_lease_exclusive(store):
    execution_id = store.create_execution(task_id="t", definition_hash="h")
    owner_a = store.acquire_lease(execution_id, ttl_seconds=60, owner="a")
    assert owner_a == "a"
    with pytest.raises(Exception, match="leased"):
        store.acquire_lease(execution_id, ttl_seconds=60, owner="b")
    store.release_lease(execution_id, "a")
    store.acquire_lease(execution_id, ttl_seconds=60, owner="b")  # ok after release


def test_lease_reacquirable_after_expiry(store, fake_clock):
    execution_id = store.create_execution(task_id="t", definition_hash="h")
    store.acquire_lease(execution_id, ttl_seconds=10, owner="a")
    # simulate expiry by backdating the lease
    store._conn.execute(
        "UPDATE executions SET lease_expires = lease_expires - 100 WHERE execution_id = ?",
        (execution_id,),
    )
    store.acquire_lease(execution_id, ttl_seconds=10, owner="b")  # must not raise


def test_find_execution_by_job_and_duplicates(store):
    e1 = store.create_execution(
        task_id="t", definition_hash="h", github_run_id=42, github_run_attempt=1, github_job_id=7
    )
    assert store.find_execution_by_job(42, 1, 7)["execution_id"] == e1
    assert store.find_execution_by_job(42, 1, 8) is None
    assert store.find_execution_by_job(42, 2, 7) is None


def test_count_active_workers(store):
    e1 = store.create_execution(task_id="t1", definition_hash="h")
    store.create_execution(task_id="t2", definition_hash="h")
    assert store.count_active_workers() == 0  # REQUESTED is not an active worker
    drive_to_state(store, e1, lifecycle.PROVISIONING)
    assert store.count_active_workers() == 1
