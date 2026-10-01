"""D146-LOCK-RECEIPT-SHAPE-HARDENING (#767).

``read_primary_lock_pid`` / ``acquire_primary_lock`` / ``release_primary_lock``
must treat valid JSON that is not an object as a malformed receipt, not as
an uncaught ``AttributeError``.

WRONG SHAPE != EXCEPTION ESCAPE
WRONG SHAPE → NOT AUTHORITATIVE → return 0 / current no-confirmed-PID semantic
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from project_atlas.orchestration.sdk.resident_driver import (
    LOCK_NAME,
    _runtime,
    acquire_primary_lock,
    poll_github_ci,
    read_primary_lock_pid,
    release_primary_lock,
)

_WRONG_SHAPES: tuple[object, ...] = ([1, 2, 3], 1, "pid", None)


def _write_lock(root: Path, payload: object) -> Path:
    path = _runtime(root) / LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.parametrize("payload", _WRONG_SHAPES)
def test_read_primary_lock_pid_wrong_shape_returns_zero(
    tmp_path: Path, payload: object
) -> None:
    _write_lock(tmp_path, payload)
    assert read_primary_lock_pid(tmp_path) == 0


@pytest.mark.parametrize("payload", _WRONG_SHAPES)
def test_acquire_primary_lock_wrong_shape_does_not_raise(
    tmp_path: Path, payload: object
) -> None:
    _write_lock(tmp_path, payload)
    assert acquire_primary_lock(tmp_path) is True
    assert read_primary_lock_pid(tmp_path) > 0


@pytest.mark.parametrize("payload", _WRONG_SHAPES)
def test_release_primary_lock_wrong_shape_does_not_raise(
    tmp_path: Path, payload: object
) -> None:
    path = _write_lock(tmp_path, payload)
    release_primary_lock(tmp_path)
    assert path.is_file()


def test_malformed_json_still_returns_zero(tmp_path: Path) -> None:
    path = _runtime(tmp_path) / LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    assert read_primary_lock_pid(tmp_path) == 0


def test_object_receipt_with_live_self_pid_still_reads(tmp_path: Path) -> None:
    assert acquire_primary_lock(tmp_path) is True
    import os

    assert read_primary_lock_pid(tmp_path) == os.getpid()
    release_primary_lock(tmp_path)
    assert read_primary_lock_pid(tmp_path) == 0


class _StubClosedLoopHook:
    def reconcile(self, root: Path, *, now: float | None = None) -> dict[str, object]:
        return {"ok": True}

    def ready_work(self, root: Path, *, capacity: int = 2) -> list[object]:
        return []

    def active_worker_count(self, root: Path) -> int:
        return 0

    def progress_state(self, root: Path) -> dict[str, object]:
        return {
            "MISSION_GENERATION": 1,
            "PROGRESS_SEQUENCE": 1,
            "EMPTY_READY_QUEUE_RECONCILIATION_COUNT": 0,
        }

    def closed_loop_tick(
        self, root: Path, *, now: float | None = None
    ) -> dict[str, object]:
        return {"REAL_WORKER_DISPATCH_COUNT": 0}


@pytest.mark.parametrize("payload", _WRONG_SHAPES)
def test_closed_loop_marker_wrong_shape_does_not_raise(
    tmp_path: Path, payload: object
) -> None:
    from project_atlas.orchestration.sdk.closed_loop_port import (
        clear_closed_loop_hook,
        register_closed_loop_hook,
    )
    from project_atlas.orchestration.sdk.resident_driver import _try_closed_loop

    clear_closed_loop_hook()
    register_closed_loop_hook(_StubClosedLoopHook())
    marker = _runtime(tmp_path) / "d134-last-closed-loop.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(payload), encoding="utf-8")
    try:
        result = _try_closed_loop(tmp_path, now=4000.0)
    finally:
        clear_closed_loop_hook()
    assert result is not None
    assert result.get("paced") is not True


@pytest.mark.parametrize("stdout", ("[1, 2, 3]", "1", '"pid"', "null"))
def test_poll_github_ci_wrong_shape_is_in_progress(stdout: str) -> None:
    fake = SimpleNamespace(returncode=0, stdout=stdout)
    with patch.object(subprocess, "run", return_value=fake):
        status, conclusion, head = poll_github_ci("123")
    assert (status, conclusion, head) == ("in_progress", None, None)
