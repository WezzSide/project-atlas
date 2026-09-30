"""D-164 fresh #428 successor — SDK supervisor stop/double-start ops."""

from __future__ import annotations

import asyncio
import os
import threading
from unittest.mock import patch

import pytest

from project_atlas.orchestration.sdk.cli import run_governor_service, run_supervisor_stop
from project_atlas.orchestration.sdk.host import (
    SUPERVISOR_LOCK_NAME,
    acquire_supervisor_lock,
    assert_single_supervisor_or_raise,
    clear_supervisor_stop,
    host_state_dir,
    new_supervisor_instance_id,
    read_supervisor_lock_pid,
    release_supervisor_lock,
    request_supervisor_stop,
    stop_requested,
)
from project_atlas.orchestration.sdk.models import SdkRuntimeError
from project_atlas.orchestration.sdk.supervisor import DurableAtlasSupervisor


def test_request_supervisor_stop_writes_file(tmp_path) -> None:
    request_supervisor_stop(tmp_path)
    assert stop_requested(tmp_path)
    clear_supervisor_stop(tmp_path)
    assert not stop_requested(tmp_path)


def test_double_start_rejected_when_live_holder(tmp_path) -> None:
    acquire_supervisor_lock(tmp_path)
    with (
        patch("project_atlas.orchestration.sdk.host.pid_is_alive", return_value=True),
        patch("project_atlas.orchestration.sdk.host.os.getpid", return_value=99999),
        pytest.raises(SdkRuntimeError, match="another live supervisor") as exc_info,
    ):
        assert_single_supervisor_or_raise(tmp_path)
    assert exc_info.value.code == "SERVICE_DOUBLE_START"
    release_supervisor_lock(tmp_path)


def test_run_supervisor_stop_json(tmp_path) -> None:
    payload, code = run_supervisor_stop(root=tmp_path)
    assert code == 0
    assert payload["stop_requested"] is True
    assert stop_requested(tmp_path)


def test_governor_service_double_start_returns_error(tmp_path) -> None:
    acquire_supervisor_lock(tmp_path)
    with (
        patch("project_atlas.orchestration.sdk.host.pid_is_alive", return_value=True),
        patch("project_atlas.orchestration.sdk.host.os.getpid", return_value=88888),
        patch(
            "project_atlas.orchestration.sdk.windows_bridge.apply_windows_discovery_patch",
            return_value=None,
        ),
    ):
        payload, code = run_governor_service(root=tmp_path, use_fake=True, max_cycles=0)
    release_supervisor_lock(tmp_path)
    assert code == 1
    assert payload["code"] == "SERVICE_DOUBLE_START"


def test_run_forever_honors_stop_file(tmp_path) -> None:
    acquire_supervisor_lock(tmp_path)
    clear_supervisor_stop(tmp_path)
    supervisor = DurableAtlasSupervisor.create(tmp_path, use_fake=True, poll_interval_sec=0.01)
    request_supervisor_stop(tmp_path)
    status = asyncio.run(supervisor.run_forever())
    release_supervisor_lock(tmp_path)
    assert status.running is False
    assert read_supervisor_lock_pid(tmp_path) == 0


def test_concurrent_acquire_only_one_holder(tmp_path) -> None:
    """PR476-IV-P1-001 — two independent instances → exactly one lock owner."""
    results: list[bool] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        barrier.wait()
        # Distinct auto-minted instance tokens (OWNERSHIP_IDENTITY != PID_ONLY).
        results.append(acquire_supervisor_lock(tmp_path))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [False, True]
    release_supervisor_lock(tmp_path)


def test_n_thread_concurrent_acquire_only_one_holder(tmp_path) -> None:
    n = 8
    results: list[bool] = []
    barrier = threading.Barrier(n)
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        ok = acquire_supervisor_lock(tmp_path)
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count(True) == 1
    assert results.count(False) == n - 1
    release_supervisor_lock(tmp_path)


def test_same_pid_different_instance_token_blocked(tmp_path) -> None:
    a = new_supervisor_instance_id()
    b = new_supervisor_instance_id()
    assert acquire_supervisor_lock(tmp_path, instance_id=a) is True
    assert acquire_supervisor_lock(tmp_path, instance_id=b) is False
    release_supervisor_lock(tmp_path, instance_id=a)


def test_same_owner_reentry_idempotent(tmp_path) -> None:
    token = new_supervisor_instance_id()
    assert acquire_supervisor_lock(tmp_path, instance_id=token) is True
    assert acquire_supervisor_lock(tmp_path, instance_id=token) is True
    release_supervisor_lock(tmp_path, instance_id=token)
    assert acquire_supervisor_lock(tmp_path, instance_id=token) is True
    release_supervisor_lock(tmp_path, instance_id=token)


def test_live_foreign_pid_blocked(tmp_path) -> None:
    lock_path = host_state_dir(tmp_path) / SUPERVISOR_LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        '{"pid": 424242, "instance_id": "foreign-token", "process_start_identity": "linux:1"}\n',
        encoding="utf-8",
    )
    with patch("project_atlas.orchestration.sdk.host.pid_is_alive", return_value=True):
        assert acquire_supervisor_lock(tmp_path) is False


def test_dead_owner_reclaimed(tmp_path) -> None:
    lock_path = host_state_dir(tmp_path) / SUPERVISOR_LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        '{"pid": 424242, "instance_id": "dead-token", "process_start_identity": "linux:1"}\n',
        encoding="utf-8",
    )
    with patch("project_atlas.orchestration.sdk.host.pid_is_alive", return_value=False):
        assert acquire_supervisor_lock(tmp_path) is True
    release_supervisor_lock(tmp_path)


def test_pid_reuse_does_not_inherit_ownership(tmp_path) -> None:
    """Live PID with mismatched start identity is treated as stale, not inherited."""
    lock_path = host_state_dir(tmp_path) / SUPERVISOR_LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    me = os.getpid()
    lock_path.write_text(
        f'{{"pid": {me}, "instance_id": "old-generation", '
        f'"process_start_identity": "linux:999999999"}}\n',
        encoding="utf-8",
    )
    with (
        patch("project_atlas.orchestration.sdk.host.pid_is_alive", return_value=True),
        patch(
            "project_atlas.orchestration.sdk.host.process_start_identity",
            return_value="linux:1",
        ),
    ):
        assert acquire_supervisor_lock(tmp_path) is True
    release_supervisor_lock(tmp_path)


def test_corrupt_lock_fails_closed(tmp_path) -> None:
    lock_path = host_state_dir(tmp_path) / SUPERVISOR_LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("{not-json", encoding="utf-8")
    assert acquire_supervisor_lock(tmp_path) is False


def test_empty_lock_fails_closed(tmp_path) -> None:
    lock_path = host_state_dir(tmp_path) / SUPERVISOR_LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("", encoding="utf-8")
    assert acquire_supervisor_lock(tmp_path) is False


def test_truncated_lock_fails_closed(tmp_path) -> None:
    lock_path = host_state_dir(tmp_path) / SUPERVISOR_LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text('{"pid": 1, "instance_id":', encoding="utf-8")
    assert acquire_supervisor_lock(tmp_path) is False


def test_legacy_pid_only_live_lock_fails_closed(tmp_path) -> None:
    lock_path = host_state_dir(tmp_path) / SUPERVISOR_LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text('{"pid": 424242}\n', encoding="utf-8")
    with patch("project_atlas.orchestration.sdk.host.pid_is_alive", return_value=True):
        assert acquire_supervisor_lock(tmp_path) is False


def test_stale_lock_reclaimed_when_holder_dead(tmp_path) -> None:
    lock_path = host_state_dir(tmp_path) / SUPERVISOR_LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text('{"pid": 424242}\n', encoding="utf-8")
    with patch("project_atlas.orchestration.sdk.host.pid_is_alive", return_value=False):
        assert acquire_supervisor_lock(tmp_path) is True
    release_supervisor_lock(tmp_path)


def test_foreign_owner_release_denied(tmp_path) -> None:
    owner = new_supervisor_instance_id()
    assert acquire_supervisor_lock(tmp_path, instance_id=owner) is True
    release_supervisor_lock(tmp_path, instance_id="not-the-owner")
    lock_path = host_state_dir(tmp_path) / SUPERVISOR_LOCK_NAME
    assert lock_path.is_file()
    release_supervisor_lock(tmp_path, instance_id=owner)
    assert not lock_path.is_file()


def test_true_owner_release_succeeds(tmp_path) -> None:
    owner = new_supervisor_instance_id()
    assert acquire_supervisor_lock(tmp_path, instance_id=owner) is True
    release_supervisor_lock(tmp_path, instance_id=owner)
    assert not (host_state_dir(tmp_path) / SUPERVISOR_LOCK_NAME).is_file()


def test_crash_between_create_and_payload_fails_closed(tmp_path) -> None:
    """O_EXCL file with empty body must not be treated as free or owned."""
    lock_path = host_state_dir(tmp_path) / SUPERVISOR_LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(fd)
    assert lock_path.stat().st_size == 0
    assert acquire_supervisor_lock(tmp_path) is False


def test_repeated_acquire_release_deterministic(tmp_path) -> None:
    for _ in range(5):
        token = new_supervisor_instance_id()
        assert acquire_supervisor_lock(tmp_path, instance_id=token) is True
        release_supervisor_lock(tmp_path, instance_id=token)
    assert not (host_state_dir(tmp_path) / SUPERVISOR_LOCK_NAME).is_file()
