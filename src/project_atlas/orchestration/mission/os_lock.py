"""OS-backed exclusive file lock tied to an open file handle's lifetime.

DUPLICATION NOTICE: this is a byte-for-byte copy of
`project_atlas.orchestration.sdk.os_lock`, authored in this same working
session on the (at time of writing) unmerged, open PR #780
(`fix/primary-governor-atomic-lock-773`, adversarially hardened and
exact-head-CI-green as of this package's own commit). It is duplicated
here, not imported, because that PR is not merged: UNMERGED != CANONICAL
DEPENDENCY. Once #780 lands, this module should be deleted and its
importers pointed at the canonical one. Do not diverge the two copies
independently in the meantime -- fix the same defect in both, or note the
divergence explicitly if only one applies.

Cross-platform, non-blocking mutual exclusion: ``msvcrt.locking()`` on
Windows, ``fcntl.flock()`` on POSIX. Both are kernel-arbitrated -- two
processes racing `try_acquire_exclusive()` on the same path can never both
win, with no time-of-check-to-time-of-use window, because the decision is
made by a single atomic OS call rather than by this process reading then
writing a file.

The lock lives only as long as the returned file descriptor stays open.
Process exit -- including a crash, `SIGKILL`, or `TerminateProcess` --
closes every fd the OS still holds for that process, which releases the
lock automatically. This is what makes crash recovery immediate rather
than something a caller has to detect and reclaim: a new acquisition
attempt against a dead holder's lock simply succeeds, with no stale-lock
protocol and no risk of two racing "reclaimers" both believing they won.

This module is a pure mutual-exclusion primitive: it does not interpret
whatever bytes live in the locked file. Callers decide what (if anything)
to read or write once they hold the lock.
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path


def try_acquire_exclusive(path: Path) -> int | None:
    """Attempt to atomically, non-blockingly acquire an exclusive OS lock
    on `path`, creating it if it does not exist yet.

    Returns an open fd (int) on success -- the caller owns it and must
    eventually pass it to `release()`, or simply let the process exit,
    which also releases it -- or `None` if another live process already
    holds the lock. Never blocks; never raises for the "someone else
    holds it" case (that is the expected, common outcome of contention).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        if sys.platform == "win32":
            import msvcrt

            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                os.close(fd)
                return None
        else:
            import fcntl

            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                return None
        return fd
    except BaseException:
        os.close(fd)
        raise


def release(fd: int) -> None:
    """Release a lock acquired via `try_acquire_exclusive()` and close its
    fd. Safe to call at most once per fd. Best-effort: the OS releases the
    lock regardless (even on process crash) once every fd referencing it
    is gone, so a failure here is not a correctness problem, only a
    slightly delayed release within this still-alive process."""
    try:
        if sys.platform == "win32":
            import msvcrt

            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl

            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)


def probe_is_locked(path: Path) -> bool:
    """Non-destructively determine whether `path` is currently held by a
    live process's exclusive lock, without disturbing an existing holder
    and without leaving a lock held afterward.

    Returns `False` (not locked) if the path does not exist yet -- there
    is nothing to hold a lock on. This is the authoritative liveness
    check: unlike reading file content or checking mere existence, a
    failed non-blocking lock attempt is proof of a live holder, immune to
    stale files left behind by a crash (the OS would have already
    released a crashed holder's lock, so the probe would succeed and
    correctly report "not locked").
    """
    if not path.is_file():
        return False
    fd = try_acquire_exclusive(path)
    if fd is None:
        return True
    release(fd)
    return False
