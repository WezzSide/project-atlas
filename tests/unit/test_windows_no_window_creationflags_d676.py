"""AS-WIN-676: Windows-native console-window suppression contract.

Real, user-reported symptom: a detached resident/governor process (no
console of its own -- see ``resident_windows.py``'s ``DETACHED_PROCESS``)
shells out to console-subsystem executables (``tasklist``, ``powershell``,
``gh``) via plain ``subprocess.run()``. Windows' default behavior for a
console-subsystem child whose parent has no console is to allocate a
brand-new, VISIBLE console window for it.

``host.no_window_creationflags()`` closes that: ``subprocess.CREATE_NO_WINDOW``
on Windows, ``0``/no-op everywhere else. This file proves the direct helper
contract, that every affected call site actually wires it in, and that
command behavior (args, capture, timeout, return code) is unchanged.

Windows-only real-process smoke: ``test_pid_is_alive_real_tasklist_call_is_hidden``
runs the real ``tasklist`` child (no mocks) to prove the helper does not
break the underlying command; the *visual* absence of a window cannot be
asserted by an automated test (no desktop introspection), so it is not
claimed here -- only that the correct flag reaches ``CreateProcess`` and the
command still functions.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from project_atlas.orchestration.sdk import ci_observer, host, resident_driver

# ``subprocess.CREATE_NO_WINDOW`` is a Windows-only attribute of the stdlib
# ``subprocess`` module -- it does not exist at all on Linux/macOS, even
# with ``os.name`` patched to "nt" (CPython only defines it when the module
# itself is built/imported on real Windows). ``host.no_window_creationflags()``
# already accounts for this via ``getattr(subprocess, "CREATE_NO_WINDOW", 0)``;
# this test file must compute its expectation the exact same way, or a bare
# ``subprocess.CREATE_NO_WINDOW`` reference raises AttributeError on every
# non-Windows CI runner.
_EXPECTED_NO_WINDOW_FLAG = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _fake_completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class TestNoWindowCreationflagsHelper:
    """Direct helper contract (T10: DIRECT HELPER CONTRACT)."""

    def test_windows_returns_create_no_window(self) -> None:
        with patch.object(os, "name", "nt"):
            assert host.no_window_creationflags() == _EXPECTED_NO_WINDOW_FLAG

    def test_non_windows_returns_zero_noop(self) -> None:
        with patch.object(os, "name", "posix"):
            assert host.no_window_creationflags() == 0

    def test_return_type_is_int(self) -> None:
        assert isinstance(host.no_window_creationflags(), int)


class TestHostCallSitesWireTheFlag:
    """REAL CALLER CONTRACT: host.py's two subprocess.run() sites."""

    def test_pid_is_alive_passes_creationflags_on_windows(self) -> None:
        """Forces the fast path off (see the equivalent comment on
        ``test_process_start_identity_passes_creationflags_on_windows``) so
        this deterministically tests the ``tasklist`` fallback's
        creationflags wiring rather than depending on whether pid 123
        happens to be a live process on whatever host runs this suite.
        """
        with (
            patch.object(os, "name", "nt"),
            patch.object(host, "_win_pid_is_alive_fast", return_value=None),
            patch.object(subprocess, "run", return_value=_fake_completed("123")) as mock_run,
        ):
            assert host.pid_is_alive(123) is True
        _args, kwargs = mock_run.call_args
        assert kwargs["creationflags"] == _EXPECTED_NO_WINDOW_FLAG
        # Command behavior untouched.
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["check"] is False

    def test_process_start_identity_passes_creationflags_on_windows(self) -> None:
        """This exercises the PowerShell fallback specifically, not whichever
        path `process_start_identity` happens to take. AS-WIN-PSI-FASTPATH
        gave it an in-process ``ctypes`` fast path that is tried first and,
        for pid 123, would usually fail anyway (no live process at that pid)
        -- but "usually" is not this test's business: forcing the fast path
        to report no answer, rather than relying on pid 123 being unclaimed
        on whatever host runs this suite, is what actually keeps this a test
        of the fallback's creationflags wiring rather than a coin flip.
        """
        with (
            patch.object(os, "name", "nt"),
            patch.object(host, "_win_process_start_ticks_fast", return_value=None),
            patch.object(subprocess, "run", return_value=_fake_completed("42")) as mock_run,
        ):
            identity = host.process_start_identity(123)
        _args, kwargs = mock_run.call_args
        assert kwargs["creationflags"] == _EXPECTED_NO_WINDOW_FLAG
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert identity == "win:42"

    def test_pid_is_alive_non_windows_path_never_calls_subprocess(self) -> None:
        """Non-Windows uses os.kill(); no creationflags kwarg exists to check.

        `os.kill` is patched, and the pid is a literal rather than this
        process's own. Both matter on the Windows runner, where this test
        forces the POSIX branch on a real Windows host: `signal.CTRL_C_EVENT`
        is `0`, so the liveness idiom `os.kill(pid, 0)` does not probe there --
        it delivers Ctrl+C to the console process group. Aimed at
        `os.getpid()`, that is pytest's own group, which took the interrupt at
        the very end of the run and turned a fully green Windows suite into
        `KeyboardInterrupt` and exit 1.

        Patching `os.kill` also makes the test say more than it did: that the
        POSIX branch probes with `os.kill(pid, 0)`, not merely that it avoids
        `subprocess.run`.
        """
        with (
            patch.object(os, "name", "posix"),
            patch.object(os, "kill") as mock_kill,
            patch.object(subprocess, "run") as mock_run,
        ):
            assert host.pid_is_alive(4321) is True
        mock_run.assert_not_called()
        mock_kill.assert_called_once_with(4321, 0)


class TestCiObserverCallSitesWireTheFlag:
    """REAL CALLER CONTRACT: all four gh-invoking subprocess.run() sites in ci_observer.py."""

    def test_fetch_jobs_passes_creationflags(self) -> None:
        with (
            patch.object(os, "name", "nt"),
            patch.object(subprocess, "run", return_value=_fake_completed("{}")) as mock_run,
        ):
            ci_observer._fetch_jobs(run_id="1", repo="o/r", gh_bin="gh")
        _args, kwargs = mock_run.call_args
        assert kwargs["creationflags"] == _EXPECTED_NO_WINDOW_FLAG
        assert kwargs["timeout"] == 45
        assert kwargs["capture_output"] is True

    def test_observe_exact_head_ci_passes_creationflags(self) -> None:
        sha = "a" * 40
        with (
            patch.object(os, "name", "nt"),
            patch.object(subprocess, "run", return_value=_fake_completed("[]")) as mock_run,
        ):
            ci_observer.observe_exact_head_ci(head_sha=sha, repo="o/r")
        _args, kwargs = mock_run.call_args
        assert kwargs["creationflags"] == _EXPECTED_NO_WINDOW_FLAG
        assert kwargs["timeout"] == 30

    def test_refresh_pr_head_passes_creationflags_on_both_calls(self) -> None:
        pr_view = _fake_completed('{"headRefOid":"' + "b" * 40 + '"}')
        api_tree = _fake_completed("c" * 40)
        with (
            patch.object(os, "name", "nt"),
            patch.object(subprocess, "run", side_effect=[pr_view, api_tree]) as mock_run,
        ):
            ci_observer.refresh_pr_head(pr_number=1, repo="o/r")
        assert mock_run.call_count == 2
        for call in mock_run.call_args_list:
            _args, kwargs = call
            assert kwargs["creationflags"] == _EXPECTED_NO_WINDOW_FLAG


class TestResidentDriverCallSiteWiresTheFlag:
    """REAL CALLER CONTRACT: the exact call site matching the original user report."""

    def test_poll_github_ci_passes_creationflags(self) -> None:
        with (
            patch.object(os, "name", "nt"),
            patch.object(
                subprocess,
                "run",
                return_value=_fake_completed('{"status":"completed","conclusion":"success"}'),
            ) as mock_run,
        ):
            status, conclusion, _head = resident_driver.poll_github_ci("123")
        _args, kwargs = mock_run.call_args
        assert kwargs["creationflags"] == _EXPECTED_NO_WINDOW_FLAG
        assert kwargs["timeout"] == 60
        assert status == "completed"
        assert conclusion == "success"

    def test_poll_github_ci_pending_run_id_never_calls_subprocess(self) -> None:
        with patch.object(subprocess, "run") as mock_run:
            status, conclusion, head = resident_driver.poll_github_ci("PENDING")
        mock_run.assert_not_called()
        assert (status, conclusion, head) == ("in_progress", None, None)


class TestResidentWindowsLogonTaskWiresTheFlag:
    """REAL CALLER CONTRACT: `register_windows_logon_task`'s `schtasks` call.

    Found during AS-WIN-PSI-FASTPATH follow-up: this call site is in the
    exact same "resident process with no console of its own" module this
    whole file exists to protect (see the module docstring), spawns the
    same kind of console-subsystem child (`schtasks.exe`) as `tasklist`/
    `powershell`/`gh` elsewhere in this family, and had neither
    `no_window_creationflags()` nor a bounded `timeout` -- unlike every
    other call site this file already covers. It has no other test
    coverage anywhere in the suite.
    """

    def test_register_windows_logon_task_passes_creationflags_and_timeout(
        self, tmp_path: Path
    ) -> None:
        from project_atlas.orchestration.sdk import resident_windows

        with (
            patch.object(os, "name", "nt"),
            patch.object(
                subprocess, "run", return_value=_fake_completed("SUCCESS")
            ) as mock_run,
        ):
            receipt = resident_windows.register_windows_logon_task(
                root=tmp_path, package_src=tmp_path / "pkg"
            )
        _args, kwargs = mock_run.call_args
        assert kwargs["creationflags"] == _EXPECTED_NO_WINDOW_FLAG
        assert kwargs["timeout"] == 30
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["check"] is False
        assert receipt["registered"] is True

    def test_register_windows_logon_task_timeout_propagates_unchanged(
        self, tmp_path: Path
    ) -> None:
        """Same discipline as `test_poll_github_ci_timeout_propagates_unchanged`:
        the timeout must surface as a real, catchable failure, never be
        silently swallowed into a false "not registered"."""
        from project_atlas.orchestration.sdk import resident_windows

        with (
            patch.object(os, "name", "nt"),
            patch.object(
                subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(cmd="schtasks", timeout=30),
            ),
            pytest.raises(subprocess.TimeoutExpired),
        ):
            resident_windows.register_windows_logon_task(
                root=tmp_path, package_src=tmp_path / "pkg"
            )

    def test_register_windows_logon_task_non_windows_never_calls_subprocess(
        self, tmp_path: Path
    ) -> None:
        from project_atlas.orchestration.sdk import resident_windows

        with (
            patch.object(os, "name", "posix"),
            patch.object(subprocess, "run") as mock_run,
        ):
            receipt = resident_windows.register_windows_logon_task(
                root=tmp_path, package_src=tmp_path / "pkg"
            )
        mock_run.assert_not_called()
        assert receipt == {"registered": False, "reason": "not_windows"}


class TestReturnCodeAndErrorPropagationUnchanged:
    """RETURN_CODE_PROPAGATION / TIMEOUT_PROPAGATION = UNCHANGED."""

    def test_pid_is_alive_returncode_nonzero_still_false_when_pid_absent(self) -> None:
        with (
            patch.object(os, "name", "nt"),
            patch.object(host, "_win_pid_is_alive_fast", return_value=None),
            patch.object(subprocess, "run", return_value=_fake_completed("", returncode=1)),
        ):
            assert host.pid_is_alive(999999) is False

    def test_poll_github_ci_timeout_propagates_unchanged(self) -> None:
        """poll_github_ci does not itself catch TimeoutExpired (pre-existing
        behavior, unchanged by this fix) -- callers are responsible. This
        locks in that the flag change did not add new exception handling
        that would silently mask a real timeout."""
        with (
            patch.object(
                subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=60)
            ),
            pytest.raises(subprocess.TimeoutExpired),
        ):
            resident_driver.poll_github_ci("123")


@pytest.mark.skipif(sys.platform != "win32", reason="authentic Windows process-path smoke")
class TestAuthenticWindowsSmoke:
    """AUTHENTIC WINDOWS SMOKE (T10): a real, unmocked child-process call.

    This proves the helper does not corrupt command behavior on a genuine
    Windows host. It cannot assert the *visual* absence of a console window
    (no desktop/window-manager introspection available to a test process);
    that boundary is disclosed, not hidden.
    """

    def test_pid_is_alive_real_tasklist_call_is_hidden(self) -> None:
        assert host.pid_is_alive(os.getpid()) is True
        assert host.pid_is_alive(999999999) is False

    def test_process_start_identity_real_powershell_call(self) -> None:
        identity = host.process_start_identity(os.getpid())
        assert identity.startswith("win:") or identity == "unknown"


# ------------------------- AS-WIN-PSI-FASTPATH: in-process fast path -------


class TestWinProcessStartTicksFastPathPortable:
    """Behavior that must hold on every platform, not just Windows."""

    def test_non_windows_or_no_windll_returns_none(self) -> None:
        """`ctypes.windll` genuinely does not exist off Windows -- this must
        not raise, it must decline. Runs for real (unmocked) on Linux/macOS
        CI; on a real Windows host it is skipped in favor of the smoke class
        below, which exercises the actual success path instead."""
        if sys.platform == "win32":
            pytest.skip("covered by the real success-path smoke below")
        assert host._win_process_start_ticks_fast(os.getpid()) is None

    def test_filetime_to_dotnet_ticks_offset_matches_known_reference_point(self) -> None:
        """1970-01-01T00:00:00Z is a fixed point both epochs agree exists.
        Win32 FILETIME ticks for it and .NET `DateTime(1970,1,1).Ticks` are
        both well-known published constants; the offset this module uses
        must carry one into the other exactly, or a value handed off
        between the fast path and the PowerShell fallback would silently
        disagree about what a running process's own start time was."""
        filetime_ticks_at_unix_epoch = 116_444_736_000_000_000
        dotnet_ticks_at_unix_epoch = 621_355_968_000_000_000
        assert (
            filetime_ticks_at_unix_epoch + host._FILETIME_TO_DOTNET_TICKS_OFFSET
            == dotnet_ticks_at_unix_epoch
        )

    def test_process_start_identity_prefers_fast_path_when_it_resolves(self) -> None:
        """With the fast path stubbed to succeed, the PowerShell fallback
        must never run at all -- not "run and be ignored"."""
        with (
            patch.object(os, "name", "nt"),
            patch.object(host, "_win_process_start_ticks_fast", return_value=123456789),
            patch.object(subprocess, "run") as mock_run,
        ):
            identity = host.process_start_identity(1)
        assert identity == "win:123456789"
        mock_run.assert_not_called()

    def test_process_start_identity_falls_back_when_fast_path_declines(self) -> None:
        with (
            patch.object(os, "name", "nt"),
            patch.object(host, "_win_process_start_ticks_fast", return_value=None),
            patch.object(subprocess, "run", return_value=_fake_completed("999")) as mock_run,
        ):
            identity = host.process_start_identity(1)
        assert identity == "win:999"
        mock_run.assert_called_once()


@pytest.mark.skipif(sys.platform != "win32", reason="authentic Windows process-path smoke")
class TestWinProcessStartTicksFastPathSmoke:
    """Real, unmocked Win32 calls -- the actual success path, and its exact
    agreement with the PowerShell fallback for the same live process."""

    def test_self_pid_resolves_without_any_subprocess(self) -> None:
        with patch.object(subprocess, "run") as mock_run:
            ticks = host._win_process_start_ticks_fast(os.getpid())
        assert ticks is not None
        assert ticks > 0
        mock_run.assert_not_called()

    def test_matches_the_powershell_fallback_value_exactly(self) -> None:
        """The fast path and the PowerShell path must agree, not merely both
        succeed -- a lock record written by one and re-read through the
        other has to compare equal."""
        pid = os.getpid()
        fast = host._win_process_start_ticks_fast(pid)
        assert fast is not None

        ps_cmd = f"(Get-Process -Id {pid} -ErrorAction Stop).StartTime.ToUniversalTime().Ticks"
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            check=False,
            creationflags=host.no_window_creationflags(),
        )
        assert proc.returncode == 0
        assert str(fast) == proc.stdout.strip()

    def test_invalid_pid_declines_rather_than_guessing(self) -> None:
        assert host._win_process_start_ticks_fast(999999999) is None

    def test_negative_control_powershell_only_path_costs_roughly_a_second_or_more(
        self,
    ) -> None:
        """THE ORIGINAL PROBLEM, reproduced on demand: with the fast path
        forced off (simulating the pre-fix code), a single
        ``acquire_supervisor_lock`` call -- which computes
        ``process_start_identity`` for this process as part of its payload
        -- costs whatever a cold ``powershell.exe`` launch costs on this
        host. This was measured at 1.7-1.9s during the PR #797 Windows
        investigation; the bound here is deliberately loose (must exceed
        0.5s) so the test is a reliable smoke/regression signal across
        hosts of very different speed, not a tight benchmark assertion.
        """
        from project_atlas.orchestration.sdk.host import (
            acquire_supervisor_lock,
            release_supervisor_lock,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(host, "_win_process_start_ticks_fast", return_value=None):
                t0 = time.perf_counter()
                token = "negative-control"
                acquired = acquire_supervisor_lock(root, instance_id=token)
                elapsed = time.perf_counter() - t0
            assert acquired is True
            release_supervisor_lock(root, instance_id=token)
        assert elapsed > 0.5, (
            f"expected the forced-PowerShell-only path to be slow (it was "
            f"1.7-1.9s during the original investigation); measured "
            f"{elapsed:.3f}s -- either this host is unrepresentative or the "
            f"fallback stopped calling powershell.exe at all"
        )

    def test_after_fast_path_the_same_operation_is_orders_of_magnitude_faster(
        self,
    ) -> None:
        """THE FIX, on the same operation, same host, fast path enabled
        (the actual default -- nothing patched here)."""
        from project_atlas.orchestration.sdk.host import (
            acquire_supervisor_lock,
            release_supervisor_lock,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            t0 = time.perf_counter()
            token = "positive-control"
            acquired = acquire_supervisor_lock(root, instance_id=token)
            elapsed = time.perf_counter() - t0
            assert acquired is True
            release_supervisor_lock(root, instance_id=token)
        assert elapsed < 0.5, (
            f"expected the fast path to keep lock acquisition well under a "
            f"second; measured {elapsed:.3f}s"
        )


# ------------------------- AS-WIN-PSI-FASTPATH: pid_is_alive fast path -----


class TestWinPidIsAliveFastPathPortable:
    """Behavior that must hold on every platform, not just Windows."""

    def test_non_windows_or_no_windll_returns_none(self) -> None:
        if sys.platform == "win32":
            pytest.skip("covered by the real success-path smoke below")
        assert host._win_pid_is_alive_fast(os.getpid()) is None

    def test_pid_is_alive_prefers_fast_path_when_it_resolves(self) -> None:
        with (
            patch.object(os, "name", "nt"),
            patch.object(host, "_win_pid_is_alive_fast", return_value=True),
            patch.object(subprocess, "run") as mock_run,
        ):
            assert host.pid_is_alive(1) is True
        mock_run.assert_not_called()

    def test_pid_is_alive_fast_path_can_positively_say_not_alive(self) -> None:
        """A resolved "no" (WAIT_OBJECT_0: the process handle signaled, i.e.
        it exited) must be trusted as-is, not treated as a decline."""
        with (
            patch.object(os, "name", "nt"),
            patch.object(host, "_win_pid_is_alive_fast", return_value=False),
            patch.object(subprocess, "run") as mock_run,
        ):
            assert host.pid_is_alive(1) is False
        mock_run.assert_not_called()

    def test_pid_is_alive_falls_back_when_fast_path_declines(self) -> None:
        with (
            patch.object(os, "name", "nt"),
            patch.object(host, "_win_pid_is_alive_fast", return_value=None),
            patch.object(subprocess, "run", return_value=_fake_completed("77")) as mock_run,
        ):
            assert host.pid_is_alive(77) is True
        mock_run.assert_called_once()


@pytest.mark.skipif(sys.platform != "win32", reason="authentic Windows process-path smoke")
class TestWinPidIsAliveFastPathSmoke:
    """Real, unmocked Win32 calls against real process lifecycles."""

    def test_self_pid_alive_without_any_subprocess(self) -> None:
        with patch.object(subprocess, "run") as mock_run:
            result = host._win_pid_is_alive_fast(os.getpid())
        assert result is True
        mock_run.assert_not_called()

    def test_invalid_pid_declines_rather_than_asserting_dead(self) -> None:
        """No such pid: OpenProcess itself fails, so this must defer
        (``None``), not assert ``False`` -- `pid_is_alive`'s existing
        `tasklist`-based answer for "never existed" stays authoritative."""
        assert host._win_pid_is_alive_fast(999999999) is None

    def test_real_child_process_lifecycle_start_to_exit(self) -> None:
        """The actual property this exists for: alive while running, not
        alive once it has genuinely exited -- checked against a real,
        unmocked child process, not a simulated pid."""
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(2)"],
            creationflags=host.no_window_creationflags(),
        )
        try:
            assert host._win_pid_is_alive_fast(child.pid) is True
        finally:
            child.wait(timeout=10)
        # Give the OS a moment to finish tearing the process down.
        deadline = time.perf_counter() + 5.0
        result = host._win_pid_is_alive_fast(child.pid)
        while result is True and time.perf_counter() < deadline:
            time.sleep(0.05)
            result = host._win_pid_is_alive_fast(child.pid)
        assert result is False

    def test_negative_control_tasklist_only_path_measurably_slower(self) -> None:
        """THE ORIGINAL PROBLEM, reproduced on demand: with the fast path
        forced off (simulating the pre-fix code), `pid_is_alive` spawns a
        real `tasklist.exe` child. Measured at ~0.13s per call on the
        validation host -- far smaller than `process_start_identity`'s
        PowerShell cost, but still ~1000x the in-process check, and the
        same class of problem in a function that liveness-polling loops
        call repeatedly.
        """
        with patch.object(host, "_win_pid_is_alive_fast", return_value=None):
            t0 = time.perf_counter()
            result = host.pid_is_alive(os.getpid())
            elapsed = time.perf_counter() - t0
        assert result is True
        assert elapsed > 0.02, (
            f"expected the forced-tasklist-only path to be clearly slower "
            f"than an in-process check (measured ~0.13s during the original "
            f"investigation); measured {elapsed:.4f}s -- either this host is "
            f"unrepresentative or the fallback stopped calling tasklist.exe"
        )

    def test_after_fast_path_the_same_operation_is_orders_of_magnitude_faster(
        self,
    ) -> None:
        t0 = time.perf_counter()
        result = host.pid_is_alive(os.getpid())
        elapsed = time.perf_counter() - t0
        assert result is True
        assert elapsed < 0.02, (
            f"expected the fast path to keep this well under the "
            f"tasklist-based cost; measured {elapsed:.4f}s"
        )
