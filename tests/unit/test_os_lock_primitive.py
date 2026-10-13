"""Hardening mission
(D-CODEX-ATLAS-WINDOWS-PRIMARY-LOCK-HARDENING-AND-INTEGRATION-READINESS-002),
Workstream F: audits `project_atlas.orchestration.sdk.os_lock` directly and
independently of its `resident_driver` callers -- nonblocking acquisition,
same-process exclusion, release, descriptor lifetime, crash release, and
probe semantics, on whichever platform this test runs on (the primitive is
cross-platform; CI/dev runs cover Windows and Linux/WSL separately).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from project_atlas.orchestration.sdk import os_lock
from project_atlas.orchestration.sdk.host import no_window_creationflags

_HOLDER = str(Path(__file__).with_name("_os_lock_holder.py"))
_TIMEOUT = 15.0


def test_nonblocking_acquire_and_release(tmp_path):
    """Basic acquire/release round trip: a fresh path is acquirable, the
    fd is a real positive descriptor, release() does not raise, and the
    file is created (not left absent) on first acquisition."""
    path = tmp_path / "some.lock"
    fd = os_lock.try_acquire_exclusive(path)
    assert fd is not None
    assert fd > 0
    assert path.is_file()
    os_lock.release(fd)


def test_zero_length_file_lockable(tmp_path):
    """Locking must not require the file to have any content -- byte 0 of
    an empty (0-byte) file is a valid, lockable region on both platforms."""
    path = tmp_path / "empty.lock"
    fd = os_lock.try_acquire_exclusive(path)
    assert fd is not None
    assert path.stat().st_size == 0
    os_lock.release(fd)


def test_second_open_by_a_different_process_is_excluded(tmp_path):
    """MULTI_PROCESS_EXCLUSION: a real, separate OS process attempting to
    lock the same path while this process holds it must fail (not block,
    not silently succeed)."""
    path = tmp_path / "excl.lock"
    fd = os_lock.try_acquire_exclusive(path)
    assert fd is not None
    try:
        result = subprocess.run(
            [sys.executable, "-c", (
                "import sys; sys.path.insert(0, sys.argv[1]); "
                "from project_atlas.orchestration.sdk import os_lock; "
                "from pathlib import Path; "
                "fd = os_lock.try_acquire_exclusive(Path(sys.argv[2])); "
                "print('LOCKED' if fd is None else 'ACQUIRED')"
            ), str(Path(__file__).resolve().parents[2] / "src"), str(path)],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            creationflags=no_window_creationflags(),
        )
        assert result.stdout.strip() == "LOCKED", (
            f"a live external holder must exclude a second process; "
            f"stdout={result.stdout!r} stderr={result.stderr[-500:]!r}"
        )
    finally:
        os_lock.release(fd)


def test_descriptor_close_releases_the_lock(tmp_path):
    """DESCRIPTOR_CLOSE_RELEASE: explicitly releasing (closing) the fd
    must free the lock for a subsequent acquirer -- same-process, no
    process exit involved."""
    path = tmp_path / "reopen.lock"
    fd1 = os_lock.try_acquire_exclusive(path)
    assert fd1 is not None
    os_lock.release(fd1)

    fd2 = os_lock.try_acquire_exclusive(path)
    assert fd2 is not None, "release() must actually free the lock for reacquisition"
    os_lock.release(fd2)


def test_probe_is_locked_non_destructive_against_a_real_holder(tmp_path):
    """PROBE_SEMANTICS: probing a lock a live, separate process holds must
    report True without disturbing that holder -- the holder must still
    be able to release cleanly afterward, proving the probe never
    actually took the lock away."""
    path = tmp_path / "probe.lock"
    go_file = tmp_path / "go"
    out_file = tmp_path / "out.json"
    holder = subprocess.Popen(
        [sys.executable, _HOLDER, str(path), str(go_file), str(out_file), "2.0"],
        creationflags=no_window_creationflags(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        go_file.write_text("go", encoding="utf-8")
        deadline = time.time() + _TIMEOUT
        while not out_file.is_file() and time.time() < deadline:
            time.sleep(0.02)
        assert out_file.is_file(), "holder never reported acquiring"
        assert json.loads(out_file.read_text(encoding="utf-8"))["acquired"] is True

        # Multiple probes while the real holder is still alive and holding.
        for _ in range(5):
            assert os_lock.probe_is_locked(path) is True

        holder.wait(timeout=_TIMEOUT)  # holder releases and exits on its own
        assert os_lock.probe_is_locked(path) is False, (
            "the real holder's own release must have worked -- if a probe had "
            "disturbed its lock, this could spuriously read either way"
        )
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=5)


def test_probe_is_locked_false_when_path_does_not_exist(tmp_path):
    """A probe on a path nothing has ever locked reports False, without
    creating the file as a side effect (non-destructive read-only check)."""
    path = tmp_path / "never-created.lock"
    assert os_lock.probe_is_locked(path) is False
    assert not path.exists()


def test_crash_releases_the_lock(tmp_path):
    """CRASH_RELEASE: a real, separate process holding the lock via
    `os_lock` directly, force-killed without any graceful release call,
    must not leave the lock permanently held -- the OS releases it when
    the process's descriptors close, even on SIGKILL/TerminateProcess."""
    path = tmp_path / "crash.lock"
    go_file = tmp_path / "go"
    out_file = tmp_path / "out.json"
    holder = subprocess.Popen(
        [sys.executable, _HOLDER, str(path), str(go_file), str(out_file), "30.0"],
        creationflags=no_window_creationflags(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        go_file.write_text("go", encoding="utf-8")
        deadline = time.time() + _TIMEOUT
        while not out_file.is_file() and time.time() < deadline:
            time.sleep(0.02)
        assert out_file.is_file()
        assert os_lock.probe_is_locked(path) is True

        holder.kill()  # SIGKILL / TerminateProcess -- no release() call
        holder.wait(timeout=_TIMEOUT)

        recovered = False
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if not os_lock.probe_is_locked(path):
                recovered = True
                break
            time.sleep(0.05)
        assert recovered, "a crashed holder must not permanently hold the lock"
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=5)


def test_same_process_second_open_is_excluded_not_reentrant(tmp_path):
    """os_lock itself is deliberately NOT reentrant: a second
    `try_acquire_exclusive()` call on an already-held path from the SAME
    process (a genuinely different fd/open) is excluded exactly like a
    different process would be. Same-process idempotency is a
    `resident_driver`-level concern (`_HELD_LOCK_FDS`), not something
    `os_lock` itself provides -- this pins that boundary explicitly, since
    POSIX `flock()` and Windows `msvcrt.locking()` differ in exactly this
    corner and callers must not assume either behaves like a recursive
    mutex at this layer."""
    path = tmp_path / "reentrant.lock"
    fd1 = os_lock.try_acquire_exclusive(path)
    assert fd1 is not None
    try:
        fd2 = os_lock.try_acquire_exclusive(path)
        assert fd2 is None, (
            "os_lock provides NO same-process reentrancy -- resident_driver's "
            "own registry is what makes acquire_primary_lock() idempotent"
        )
    finally:
        os_lock.release(fd1)
