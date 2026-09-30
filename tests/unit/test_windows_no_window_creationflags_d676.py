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
from unittest import mock
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
        with (
            patch.object(os, "name", "nt"),
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
        with (
            patch.object(os, "name", "nt"),
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


class TestReturnCodeAndErrorPropagationUnchanged:
    """RETURN_CODE_PROPAGATION / TIMEOUT_PROPAGATION = UNCHANGED."""

    def test_pid_is_alive_returncode_nonzero_still_false_when_pid_absent(self) -> None:
        with (
            patch.object(os, "name", "nt"),
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


class TestWindowsProbesAreNonInteractiveAndBounded:
    """The two real child-process probes must not touch the console or hang.

    `pid_is_alive` and `process_start_identity` shell out to `tasklist` and
    `powershell` from a resident driver's polling loop. Two properties matter
    beyond hiding the window:

    * **stdin is detached.** A child sharing the parent's console stdin can
      interact with that console. Windows delivers console control events to
      every process attached to the console, so a child that provokes one
      raises it in the parent too -- which is how a suite where no test fails
      still ends in `KeyboardInterrupt` and a non-zero exit.
    * **the call is bounded.** These answer a question about a PID; they
      respond quickly or something is wrong. Unbounded, a wedged child hangs
      the polling loop with no diagnostic.

    Asserted against the real call sites rather than the helper, because the
    helper cannot enforce how its callers invoke `subprocess.run`.
    """

    def test_pid_is_alive_detaches_stdin_and_bounds_the_call(self) -> None:
        captured: dict[str, object] = {}

        def _fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            captured.update(kwargs)
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with mock.patch.object(host.os, "name", "nt"), mock.patch.object(
            host.subprocess, "run", _fake_run
        ):
            host.pid_is_alive(1234)

        assert captured["stdin"] is subprocess.DEVNULL
        assert captured["timeout"] == host._WINDOWS_PROBE_TIMEOUT_SECONDS

    def test_process_start_identity_detaches_stdin_and_bounds_the_call(self) -> None:
        captured: dict[str, object] = {}

        def _fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            captured.update(kwargs)
            return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")

        with mock.patch.object(host.os, "name", "nt"), mock.patch.object(
            host.subprocess, "run", _fake_run
        ):
            host.process_start_identity(1234)

        assert captured["stdin"] is subprocess.DEVNULL
        assert captured["timeout"] == host._WINDOWS_PROBE_TIMEOUT_SECONDS

    @pytest.mark.parametrize(
        "probe", [lambda: host.pid_is_alive(1234), lambda: host.process_start_identity(1234)]
    )
    def test_a_wedged_probe_is_survivable(self, probe: object) -> None:
        """A timeout must degrade, not propagate: callers poll, they do not crash."""

        def _timeout(*args: object, **kwargs: object) -> None:
            raise subprocess.TimeoutExpired(cmd="probe", timeout=1)

        with mock.patch.object(host.os, "name", "nt"), mock.patch.object(
            host.subprocess, "run", _timeout
        ):
            assert probe() in (False, "unknown")  # type: ignore[operator]


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
