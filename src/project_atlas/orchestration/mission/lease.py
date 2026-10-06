"""AS-MISSION-VERTICAL-SLICE-001, DEVELOPMENT/RECOVERY -- general-purpose,
single-holder ownership for one mission run's isolated workspace.

Built on `os_lock` (kernel lock, tied to an open file descriptor's
lifetime): a mission run acquires exclusive ownership of its workspace the
same way `resident_driver.acquire_primary_lock()` enforces
`ACTIVE_PRIMARY_GOVERNOR_COUNT <= 1` -- because it is the identical
primitive. This is deliberately NOT `orchestration.sdk.lease_registry`,
which is scoped to one specific package's own governed mutation route
(`PACKAGE_ID`, `CANONICAL_PR`, `CANONICAL_BRANCH` baked in) -- a
general-purpose "own this workspace while I work in it" lease has no
business sharing that narrow contract.

Ownership here answers exactly one question: is this workspace currently
claimed by a live process? It says nothing about *what* that process is
doing -- see `execution.py` for the durable checkpoint that answers that,
independently, the same way `resident_driver` keeps LOCK_ATOMICITY and
RECEIPT_ATOMICITY separate.

The receipt lives in a SEPARATE file from the lock target, not the same
file at a different offset -- learned the hard way in #780's own history
(see that PR's commits): `msvcrt.locking()` on Windows is a *mandatory*,
not advisory, byte-range lock. A plain read through a DIFFERENT handle
(exactly what a later `read_mission_lease_state()` call does) is denied
while the byte range is locked, even same-process, so writing the receipt
into the locked file itself made it silently unreadable the moment the
lock was held -- reproduced directly while writing this module, before
separating the files fixed it.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from project_atlas.orchestration.mission import MISSION_STATE_DIR_NAME, os_lock

LEASE_FILE_NAME = "mission.lease"
RECEIPT_FILE_NAME = "mission-lease-receipt.json"

# Guards `_HELD_LEASES` against concurrent threads/nested calls in this
# SAME process -- the OS lock (`os_lock`) already arbitrates correctly
# ACROSS processes on its own; this lock exists purely to make the
# check-then-act sequence below (is it already held? by whom? acquire if
# not) atomic within one process, closing a real race between the
# "already held" check and the write that records it.
_STATE_LOCK = threading.Lock()
# key -> (fd, run_id) -- run_id is what makes idempotency and release
# OWNERSHIP-SPECIFIC: a second run_id reentering the SAME workspace in the
# SAME process is a different logical owner and must be rejected, not
# silently granted by a same-process short-circuit.
_HELD_LEASES: dict[str, tuple[int, str]] = {}


def _lease_path(workspace: Path) -> Path:
    return workspace / MISSION_STATE_DIR_NAME / LEASE_FILE_NAME


def _receipt_path(workspace: Path) -> Path:
    return workspace / MISSION_STATE_DIR_NAME / RECEIPT_FILE_NAME


def _key(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


@dataclass(frozen=True)
class MissionLeaseState:
    held: bool
    holder_pid: int | None


def acquire_mission_lease(workspace: Path, *, run_id: str) -> bool:
    """Claim exclusive ownership of `workspace` for `run_id`. Returns True
    if `run_id` now holds it (freshly, or already did -- idempotent for
    the SAME run_id, no re-lock). False if a different live process OR a
    DIFFERENT run_id in THIS process holds it: reentrancy from a
    different logical owner (two threads, or nested calls, each running a
    different mission run against the same workspace) is rejected, not
    silently granted by the same-process short-circuit -- a prior defect
    here let two different run_ids both "win" and overwrite the same
    checkpoint. Never blocks. Crash recovery is automatic: see `os_lock`.
    """
    path = _lease_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = _key(path)
    with _STATE_LOCK:
        existing = _HELD_LEASES.get(key)
        if existing is not None:
            _, held_run_id = existing
            return held_run_id == run_id
        fd = os_lock.try_acquire_exclusive(path)
        if fd is None:
            return False
        _HELD_LEASES[key] = (fd, run_id)
    _publish_lease_receipt(workspace, pid=os.getpid(), run_id=run_id)
    return True


def _publish_lease_receipt(workspace: Path, *, pid: int, run_id: str) -> None:
    """Safe publication: unlink-then-write-temp-then-replace, same
    discipline as `resident_driver._publish_receipt()` -- a failed
    publish leaves the receipt ABSENT, never a stale prior holder's
    content, and this is purely informational either way (ownership is
    the OS lock, not this file). Unlike `execution._persist_checkpoint()`
    (which retries the same class of transient Windows sharing-violation
    `PermissionError` -- see that function's docstring for the real,
    reproduced hazard), this function deliberately does NOT retry: a
    checkpoint write failure would be load-bearing (crashing the caller
    with no recorded state), while a receipt publish failure here simply
    leaves `read_mission_lease_state()`'s `holder_pid` as `None` for this
    one read -- annoying, never incorrect, and consistent with this
    receipt's already-established best-effort contract."""
    receipt_path = _receipt_path(workspace)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.unlink(missing_ok=True)
    content = json.dumps({"pid": pid, "run_id": run_id, "at": time.time()})
    tmp_path = receipt_path.with_name(f".{receipt_path.name}.{pid}.tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, receipt_path)
    except OSError:
        tmp_path.unlink(missing_ok=True)


def release_mission_lease(workspace: Path, *, run_id: str) -> None:
    """Release ownership of `workspace` IF this process holds it under
    `run_id` -- ownership-specific: a caller cannot release a lease held
    by a DIFFERENT run_id in the same process (that would leave the
    actual owner's fd released out from under it while it still believes
    itself protected). Idempotent, safe even if never held or already
    released."""
    path = _lease_path(workspace)
    key = _key(path)
    with _STATE_LOCK:
        existing = _HELD_LEASES.get(key)
        if existing is None:
            return
        fd, held_run_id = existing
        if held_run_id != run_id:
            return
        del _HELD_LEASES[key]
    os_lock.release(fd)


def read_mission_lease_state(workspace: Path) -> MissionLeaseState:
    """Real, non-blocking answer to "is someone alive holding this
    workspace right now" -- never from file existence alone. `holder_pid`
    is informational only (parsed from the lease's own receipt bytes,
    read while NOT holding the lock -- see `os_lock`'s module docstring
    for why a plain read of the locked byte range would be denied on
    Windows if attempted through the SAME fd instead)."""
    path = _lease_path(workspace)
    held = os_lock.probe_is_locked(path)
    if not held:
        return MissionLeaseState(held=False, holder_pid=None)
    try:
        data = json.loads(_receipt_path(workspace).read_text(encoding="utf-8"))
        pid = int(data.get("pid", 0))
    except (OSError, json.JSONDecodeError, TypeError, ValueError, AttributeError):
        pid = 0
    return MissionLeaseState(held=True, holder_pid=pid if pid > 0 else None)
