"""AS-WIN-RESIDENT-LOCK-PID-REUSE: the resident-driver "primary lock"
(`resident-primary.lock`) gains the same process-start-identity protection
`host.py`'s supervisor lock already has, closing a gap the two mechanisms
had drifted apart on.

Before this fix, `acquire_primary_lock`/`read_primary_lock_pid` decided
liveness with `pid_is_alive(pid)` alone. If the process that wrote the lock
died and, before the lock file was ever cleaned up, its pid happened to be
reused by any other live process (Windows generally avoids fast reuse but
does not guarantee it), the lock would be reported as still held by the
*new* process -- indefinitely, since this mechanism has no staleness/
heartbeat check at all. `acquire_primary_lock` would refuse forever, and
the resident-driver watchdog (`read_primary_lock_pid`) would never restart
a genuinely-dead governor.

Mirrors `host._live_foreign_owner`'s exact contract: a lock record with no
recorded identity (backward compat with existing/older lock files), or a
process this call cannot resolve an identity for, falls back to the
pre-existing pid-alive-only check unchanged. Only a recorded identity that
*positively disagrees* with a freshly-read live identity is treated as
reuse -- absence of evidence is never treated as evidence of reuse.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from project_atlas.orchestration.sdk import resident_driver as rd
from project_atlas.orchestration.sdk.host import no_window_creationflags
from project_atlas.orchestration.sdk.resident_status import ResidentStatus, status_claims_live

LOCK_RELATIVE = Path(".atlas") / "orchestration" / "sdk-runtime" / "resident-primary.lock"


def _lock_path(root: Path) -> Path:
    path = root / LOCK_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_lock(root: Path, **fields: object) -> None:
    _lock_path(root).write_text(json.dumps(fields), encoding="utf-8")


class TestBackwardCompatUnchangedBehavior:
    """Every case the pre-existing pid-only check already handled must
    behave identically -- this fix only ever ADDS a way to positively
    disprove liveness; it must never invent a new way to assert it."""

    def test_no_lock_file_acquires_and_writes_identity(self, tmp_path: Path) -> None:
        assert rd.read_primary_lock_pid(tmp_path) == 0
        assert rd.acquire_primary_lock(tmp_path) is True
        data = json.loads(_lock_path(tmp_path).read_text(encoding="utf-8"))
        assert data["pid"] == os.getpid()
        assert isinstance(data["process_start_identity"], str)

    def test_old_format_lock_naming_a_genuinely_alive_other_pid_still_refuses(
        self, tmp_path: Path
    ) -> None:
        """A lock file from before this fix shipped (no `process_start_identity`
        key at all) naming a real, currently-alive, different process must
        still be honoured exactly as before -- refuse to acquire, report
        that pid as the holder."""
        with patch.object(rd, "pid_is_alive", return_value=True):
            _write_lock(tmp_path, pid=999999, at=time.time())
            assert rd.acquire_primary_lock(tmp_path) is False
            assert rd.read_primary_lock_pid(tmp_path) == 999999

    def test_dead_other_pid_is_reclaimed_same_as_before(self, tmp_path: Path) -> None:
        with patch.object(rd, "pid_is_alive", return_value=False):
            _write_lock(
                tmp_path,
                pid=999999,
                at=time.time(),
                process_start_identity="win:whatever",
            )
            assert rd.read_primary_lock_pid(tmp_path) == 0
            assert rd.acquire_primary_lock(tmp_path) is True

    def test_self_reentry_still_permitted(self, tmp_path: Path) -> None:
        assert rd.acquire_primary_lock(tmp_path) is True
        assert rd.acquire_primary_lock(tmp_path) is True

    def test_release_then_reacquire_round_trip(self, tmp_path: Path) -> None:
        assert rd.acquire_primary_lock(tmp_path) is True
        assert rd.read_primary_lock_pid(tmp_path) == os.getpid()
        rd.release_primary_lock(tmp_path)
        assert rd.read_primary_lock_pid(tmp_path) == 0
        assert rd.acquire_primary_lock(tmp_path) is True

    def test_unreadable_lock_file_is_not_stolen(self, tmp_path: Path) -> None:
        """An empty or corrupt lock file is the in-progress O_EXCL→write
        window (or leftover garbage). Treating parse failure as stale and
        unlinking it is what produced two concurrent ACQUIRED winners.
        Fail closed: do not acquire."""
        _lock_path(tmp_path).write_text("{not valid json at all", encoding="utf-8")
        assert rd.acquire_primary_lock(tmp_path) is False
        assert rd.read_primary_lock_pid(tmp_path) == 0

    def test_empty_lock_file_is_not_stolen(self, tmp_path: Path) -> None:
        _lock_path(tmp_path).write_bytes(b"")
        assert rd.acquire_primary_lock(tmp_path) is False
        assert rd.read_primary_lock_pid(tmp_path) == 0


class TestAcquireIsNowAtomicNotCheckThenWrite:
    """AS-WIN-RESIDENT-LOCK-PID-REUSE, part 2: the version this replaced
    read the file, decided in Python, then wrote -- a plain check-then-write
    with no OS-level exclusivity at all. Two independent processes racing to
    become the primary governor could both observe "no valid holder" and
    both succeed, defeating "Ensure ACTIVE_PRIMARY_GOVERNOR_COUNT <= 1"
    entirely. The window is a handful of bytecode instructions -- far
    smaller than real process-start jitter -- so this is analytically real
    but not reliably reproducible by racing real OS processes and hoping;
    these tests instead verify the actual atomicity primitive directly,
    the same way its correctness is guaranteed rather than observed.
    """

    def test_lock_file_creation_uses_the_atomic_o_excl_primitive(
        self, tmp_path: Path
    ) -> None:
        """Once acquired, a second `O_CREAT | O_EXCL` against the exact same
        path must fail -- proving the filesystem itself, not Python-level
        timing, is what makes a second acquirer's create impossible to win
        once the first has happened. This is the actual guarantee; every
        other test in this class exercises what happens around it, not the
        guarantee itself."""
        assert rd.acquire_primary_lock(tmp_path) is True
        with pytest.raises(FileExistsError):
            fd = os.open(str(_lock_path(tmp_path)), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)  # pragma: no cover - only reached if the assertion above is wrong

    def test_many_concurrent_processes_at_most_one_ever_wins(self, tmp_path: Path) -> None:
        """Best-effort empirical corroboration, not the proof (see class
        docstring): real, separate OS processes -- not threads, which would
        all share this test's own pid and trivially "win" via the
        self-reentry path -- racing to acquire the same lock. Whether or
        not any two of them actually land in the same instant, the
        atomicity primitive above guarantees at most one can ever succeed;
        this asserts exactly that invariant against real processes rather
        than assuming it.
        """
        import subprocess

        worker = Path(__file__).with_name("_primary_lock_race_worker.py")
        n = 12
        procs = [
            subprocess.Popen(
                [sys.executable, str(worker), str(tmp_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
            )
            for _ in range(n)
        ]
        # Read every first line while winners still hold the lock. Closing
        # stdin (or waiting for exit) before this would re-introduce the
        # sequential-reclaim flake the hold-until-stdin contract exists to
        # close.
        outcomes = [p.stdout.readline().strip() for p in procs]
        try:
            assert outcomes.count("ACQUIRED") == 1, (
                f"expected exactly one of {n} independent processes to acquire "
                f"the lock concurrently; outcomes={outcomes}"
            )
            assert outcomes.count("REFUSED") == n - 1
        finally:
            for p in procs:
                if p.stdin is not None:
                    p.stdin.close()
            for p in procs:
                p.wait(timeout=30)


class TestPidReuseIsNowCaught:
    """THE ACTUAL GAP: a recorded identity that positively disagrees with
    the live process's real identity must be treated as reuse -- not as
    "the original holder is still here"."""

    def test_acquire_reclaims_when_recorded_identity_disagrees_with_live_one(
        self, tmp_path: Path
    ) -> None:
        with (
            patch.object(rd, "pid_is_alive", return_value=True),
            patch.object(rd, "process_start_identity", return_value="win:live-B"),
        ):
            _write_lock(
                tmp_path,
                pid=999999,
                at=time.time(),
                process_start_identity="win:dead-A",
            )
            assert rd.acquire_primary_lock(tmp_path) is True

    def test_read_primary_lock_pid_reports_zero_when_identity_disagrees(
        self, tmp_path: Path
    ) -> None:
        with (
            patch.object(rd, "pid_is_alive", return_value=True),
            patch.object(rd, "process_start_identity", return_value="win:live-B"),
        ):
            _write_lock(
                tmp_path,
                pid=999999,
                at=time.time(),
                process_start_identity="win:dead-A",
            )
            assert rd.read_primary_lock_pid(tmp_path) == 0

    def test_matching_identity_is_still_a_live_foreign_owner(self, tmp_path: Path) -> None:
        """The positive control: when the recorded and live identities
        genuinely agree, this is the ORIGINAL still-running holder, and
        must still block acquisition exactly as before."""
        with (
            patch.object(rd, "pid_is_alive", return_value=True),
            patch.object(rd, "process_start_identity", return_value="win:same"),
        ):
            _write_lock(
                tmp_path,
                pid=999999,
                at=time.time(),
                process_start_identity="win:same",
            )
            assert rd.acquire_primary_lock(tmp_path) is False
            assert rd.read_primary_lock_pid(tmp_path) == 999999

    def test_unknown_live_identity_does_not_assert_reuse(self, tmp_path: Path) -> None:
        """If this call cannot resolve a live identity for the pid right
        now (`process_start_identity` returning "unknown" -- e.g. a
        transient permission hiccup), that is not proof of reuse either;
        falls back to the pre-existing pid-alive-only behavior."""
        with (
            patch.object(rd, "pid_is_alive", return_value=True),
            patch.object(rd, "process_start_identity", return_value="unknown"),
        ):
            _write_lock(
                tmp_path,
                pid=999999,
                at=time.time(),
                process_start_identity="win:dead-A",
            )
            assert rd.acquire_primary_lock(tmp_path) is False


@pytest.mark.skipif(sys.platform != "win32", reason="authentic Windows process identity")
class TestAuthenticEndToEnd:
    """Real, unmocked identity calls: a genuinely different live process
    (a real spawned child, not a simulated pid) does not get mistaken for
    the process named in an old-format lock record, and a lock this
    process itself wrote round-trips through a real identity value."""

    def test_self_round_trip_with_real_identity(self, tmp_path: Path) -> None:
        assert rd.acquire_primary_lock(tmp_path) is True
        data = json.loads(_lock_path(tmp_path).read_text(encoding="utf-8"))
        assert data["process_start_identity"].startswith("win:")
        assert rd.read_primary_lock_pid(tmp_path) == os.getpid()

    def test_real_different_live_process_is_still_a_foreign_holder(
        self, tmp_path: Path
    ) -> None:
        import subprocess

        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            creationflags=no_window_creationflags(),
        )
        try:
            _write_lock(tmp_path, pid=child.pid, at=time.time())
            assert rd.acquire_primary_lock(tmp_path) is False
            assert rd.read_primary_lock_pid(tmp_path) == child.pid
        finally:
            child.terminate()
            child.wait(timeout=10)


# --------------------------------------------------------------------------
# The sister mechanism: resident_status.status_claims_live() gates the same
# watchdog restart decision (resident_windows.ensure_resident_alive checks
# it FIRST, before ever consulting read_primary_lock_pid), and had the
# identical pid-only gap -- but with no staleness bound to self-heal within
# at all (the primary lock never expires), vs. status_claims_live's 30s
# heartbeat window. Closing only the lock and not this would have left a
# 30s false-"still live" window on every restart decision regardless.


class TestStatusClaimsLiveBackwardCompat:
    def test_no_recorded_identity_falls_back_to_pid_alive_and_heartbeat(self) -> None:
        status = ResidentStatus(
            GOVERNOR_PID=os.getpid(),
            status_written_at=time.time(),
        )
        assert status.GOVERNOR_PROCESS_START_IDENTITY == ""
        assert status_claims_live(status) is True

    def test_dead_pid_still_not_live(self) -> None:
        status = ResidentStatus(GOVERNOR_PID=999999, status_written_at=time.time())
        with patch(
            "project_atlas.orchestration.sdk.resident_status.pid_is_alive",
            return_value=False,
        ):
            assert status_claims_live(status) is False

    def test_stale_heartbeat_still_not_live(self) -> None:
        status = ResidentStatus(GOVERNOR_PID=os.getpid(), status_written_at=time.time() - 999)
        assert status_claims_live(status) is False


class TestStatusClaimsLivePidReuse:
    def test_mismatched_recorded_identity_is_not_live(self) -> None:
        status = ResidentStatus(
            GOVERNOR_PID=999999,
            status_written_at=time.time(),
            GOVERNOR_PROCESS_START_IDENTITY="win:dead-A",
        )
        with (
            patch(
                "project_atlas.orchestration.sdk.resident_status.pid_is_alive",
                return_value=True,
            ),
            patch(
                "project_atlas.orchestration.sdk.resident_status.process_start_identity",
                return_value="win:live-B",
            ),
        ):
            assert status_claims_live(status) is False

    def test_matching_recorded_identity_is_still_live(self) -> None:
        status = ResidentStatus(
            GOVERNOR_PID=999999,
            status_written_at=time.time(),
            GOVERNOR_PROCESS_START_IDENTITY="win:same",
        )
        with (
            patch(
                "project_atlas.orchestration.sdk.resident_status.pid_is_alive",
                return_value=True,
            ),
            patch(
                "project_atlas.orchestration.sdk.resident_status.process_start_identity",
                return_value="win:same",
            ),
        ):
            assert status_claims_live(status) is True

    def test_unresolvable_live_identity_does_not_assert_reuse(self) -> None:
        status = ResidentStatus(
            GOVERNOR_PID=999999,
            status_written_at=time.time(),
            GOVERNOR_PROCESS_START_IDENTITY="win:dead-A",
        )
        with (
            patch(
                "project_atlas.orchestration.sdk.resident_status.pid_is_alive",
                return_value=True,
            ),
            patch(
                "project_atlas.orchestration.sdk.resident_status.process_start_identity",
                return_value="unknown",
            ),
        ):
            assert status_claims_live(status) is True


@pytest.mark.skipif(sys.platform != "win32", reason="authentic Windows process identity")
def test_authentic_self_identity_round_trips_through_persist_and_load(tmp_path: Path) -> None:
    """persist_status/load_status round-trip a real GOVERNOR_PROCESS_START_IDENTITY
    (not a simulated one), and status_claims_live accepts it as live."""
    from project_atlas.orchestration.sdk.host import process_start_identity as real_psi
    from project_atlas.orchestration.sdk.resident_status import load_status, persist_status

    status = ResidentStatus(
        GOVERNOR_PID=os.getpid(),
        GOVERNOR_PROCESS_START_IDENTITY=real_psi(os.getpid()),
        status_written_at=time.time(),
    )
    persist_status(tmp_path, status)
    reloaded = load_status(tmp_path)
    assert reloaded.GOVERNOR_PROCESS_START_IDENTITY.startswith("win:")
    assert status_claims_live(reloaded) is True
