"""D-146 adversarial verification for remediation behaviors."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

from project_atlas.cli import EXIT_OK, main
from project_atlas.orchestration.autonomy.return_gate import (
    AutonomyReturnState,
    may_emit_final_return,
)
from project_atlas.orchestration.sdk import resident_windows
from project_atlas.orchestration.sdk.ci_observer import CiObservation, classify_watch_session
from project_atlas.orchestration.sdk.host import no_window_creationflags
from project_atlas.orchestration.sdk.resident_driver import (
    LOCK_NAME,
    acquire_primary_lock,
    read_primary_lock_pid,
)
from project_atlas.orchestration.sdk.resident_status import load_status
from project_atlas.orchestration.sdk.resident_windows import (
    detach_resident_driver,
    ensure_resident_alive,
)

# `signal.SIGKILL` does not exist as an attribute on Windows at all (not
# merely unsupported at the OS level) -- this module is developed/tested on
# a Windows host but its POSIX cleanup branch below must still be
# reference-safe there, so resolve it once, defensively, rather than at
# every call site.
_POSIX_KILL_SIGNAL: int = getattr(signal, "SIGKILL", signal.SIGTERM)


def _terminate_resident_tree(pid: int) -> None:
    """Best-effort, platform-aware test cleanup for a
    `detach_resident_driver()`-spawned tree.

    D146 (native-Windows evidence, found while validating this successor):
    `os.kill(pid, signal.SIGTERM)` against these DETACHED_PROCESS /
    CREATE_NEW_PROCESS_GROUP children reproducibly raised
    ``OSError: [WinError 87] The parameter is incorrect`` on this class of
    host and never actually terminated the process -- a real, silent
    cleanup failure previously masked by `contextlib.suppress(OSError,
    ProcessLookupError)`. On Windows, ``taskkill /F /T /PID`` reliably
    terminates both the target and any live descendants (verified: it also
    caught a child of the resident that `os.kill` alone would have missed
    entirely) -- ``creationflags=no_window_creationflags()`` matches this
    repo's own established convention (``host.py``) for never popping a
    console window from a subprocess call made on behalf of a detached
    process. ``os.kill(pid, SIGTERM)`` has no such Windows-specific failure
    on POSIX -- `detach_resident_driver()` also spawns with
    ``start_new_session=True`` there, so a first attempt via
    ``os.killpg`` reaches the whole session/process group, not just the
    one PID, with a per-PID fallback if the group lookup itself fails.
    An unconditionally-Windows tool must never become the only cleanup
    path: this repo's CI runs this exact test file on `ubuntu-latest` too.
    Best-effort only -- this is cleanup, not an assertion.
    """
    if pid <= 0:
        return
    if os.name == "nt":
        with contextlib.suppress(OSError):
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                check=False,
                creationflags=no_window_creationflags(),
            )
        return
    with contextlib.suppress(OSError, ProcessLookupError):
        os.killpg(os.getpgid(pid), _POSIX_KILL_SIGNAL)
        return
    with contextlib.suppress(OSError, ProcessLookupError):
        os.kill(pid, _POSIX_KILL_SIGNAL)


def test_watchdog_waits_for_lock_holder_no_second_spawn(tmp_path: Path) -> None:
    """D146 successor (current-main, native-Windows-reproduced): current main's
    `detach_resident_driver()` returns bare `Popen.pid` unconditionally, which
    on a Windows venv whose `Scripts\\python.exe` is a launcher stub (confirmed
    on-host via binary size/hash against the base interpreter, and via
    `Win32_Process` ancestry: the real interpreter's `ParentProcessId` equals
    the launcher's PID) is the *launcher's* PID, not the resident's. Reproduced
    natively, repeatedly, with distinct PID pairs every time -- never PID 0,
    always a wrong-but-live PID. The successor makes `detach_resident_driver()`
    itself confirm the lock holder before returning (see its docstring), so by
    the time this function returns, `holder == returned_pid` is already the
    function's own contract -- this test's own poll loop below is now a
    redundant (harmless) safety net, not what makes the assertion pass.
    """
    package_src = Path(__file__).resolve().parents[2] / "src"
    root = tmp_path / "runtime"
    (root / ".atlas" / "orchestration" / "sdk-runtime").mkdir(parents=True)
    spawned_pid = detach_resident_driver(root=root, package_src=package_src)
    try:
        # Poll budget: 80 * 0.25s = 20s -- matches the internal budget
        # `detach_resident_driver()` already exhausted before returning;
        # this loop exists only so a slow-to-persist `resident-status.json`
        # (Finding B: status persistence lags lock acquisition, not
        # synchronized with it) doesn't make the assertions below flaky.
        for _ in range(80):
            if read_primary_lock_pid(root) > 0:
                break
            time.sleep(0.25)
        holder = read_primary_lock_pid(root)
        assert holder > 0
        assert holder == spawned_pid
        result = ensure_resident_alive(root=root, package_src=package_src)
        assert result["action"] == "noop"
        status = load_status(root)
        assert status.DUPLICATE_DISPATCH_COUNT == 0
    finally:
        # `detach_resident_driver()` spawns a genuinely detached OS
        # process (by design -- that's what "resident" means in
        # production) with no test-visible handle to wait/join on, so
        # nothing else in this process tree ever stops it. Left
        # unterminated, it idles indefinitely: on Windows specifically,
        # its watchdog loop periodically shells out to `gh run view`
        # against whatever `origin` remote happens to resolve at
        # process-start time in a bare `pytest-<n>/...` tmp_path
        # (nothing about this repo), which fails and pops a visible
        # console window per attempt -- a real, user-facing leak, not
        # just a lingering background process. `0` is a legitimate,
        # expected value here now (D146 successor: an unconfirmed
        # identity is honestly 0, not a guessed PID) -- there is nothing
        # confirmed to target in that case, so `_terminate_resident_tree`
        # (best-effort, PID-scoped, never broader) is a no-op on 0.
        _terminate_resident_tree(spawned_pid)


def test_detach_resident_driver_returns_authoritative_lock_holder_pid(
    tmp_path: Path,
) -> None:
    """D146 successor, isolated from the no-second-spawn scenario above: the
    PID `detach_resident_driver()` returns must itself already be the
    confirmed primary-lock holder -- not merely a live PID that happens to
    coincide with it. Cross-checked against `read_primary_lock_pid()` (the
    lock file the resident writes) directly; `load_status().GOVERNOR_PID` is
    checked with its own short bounded wait rather than asserted immediately
    (Finding B: `run_resident_loop()` writes the primary lock before it
    persists status, so the two are not synchronized).

    Negative control: on unpatched current main, `detach_resident_driver()`
    returns `Popen.pid` unconditionally -- reproducibly wrong on this class
    of Windows host (verified natively, repeatedly: launcher PID != lock
    holder PID, never PID 0).
    """
    package_src = Path(__file__).resolve().parents[2] / "src"
    root = tmp_path / "runtime"
    (root / ".atlas" / "orchestration" / "sdk-runtime").mkdir(parents=True)
    resolved_pid = detach_resident_driver(root=root, package_src=package_src)
    try:
        assert resolved_pid > 0
        assert resolved_pid == read_primary_lock_pid(root), (
            "detach_resident_driver()'s return value must already be the "
            "confirmed lock holder"
        )
        status = None
        for _ in range(20):
            status = load_status(root)
            if resolved_pid == status.GOVERNOR_PID:
                break
            time.sleep(0.25)
        assert status is not None and resolved_pid == status.GOVERNOR_PID, (
            "the resident's own self-reported os.getpid() must eventually "
            "agree with the confirmed identity (bounded wait: status "
            "persistence is not promised to be synchronized with lock "
            "acquisition, so this must not be asserted immediately)"
        )
    finally:
        _terminate_resident_tree(resolved_pid)


def test_read_primary_lock_pid_refuses_malformed_or_stale_lock(tmp_path: Path) -> None:
    """D146 successor test-matrix item 7: a malformed or stale lock file must
    resolve to "no confirmed holder" (0), never a value `detach_resident_
    driver()` could mistake for a live identity."""
    root = tmp_path / "runtime"
    lock_dir = root / ".atlas" / "orchestration" / "sdk-runtime"
    lock_dir.mkdir(parents=True)
    lock_path = lock_dir / LOCK_NAME

    lock_path.write_text("{not valid json", encoding="utf-8")
    assert read_primary_lock_pid(root) == 0

    # A syntactically valid but stale record: PID 0 is never a live process.
    lock_path.write_text(json.dumps({"pid": 0, "at": 0.0}), encoding="utf-8")
    assert read_primary_lock_pid(root) == 0

    # A confirmed-alive PID (this test process itself) round-trips.
    assert acquire_primary_lock(root)
    assert read_primary_lock_pid(root) == os.getpid()


def test_terminate_resident_tree_dispatches_by_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D146 review R2: the Windows-only ``taskkill`` fix must not become a
    non-Windows cleanup regression -- this repo's CI runs this exact test
    file on ``ubuntu-latest`` too. Verify dispatch without spawning any
    real process: the Windows branch calls ``taskkill`` (and nothing
    POSIX-only); the non-Windows branch never calls ``taskkill`` at all."""
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: calls.append(("run", args)),
    )
    # os.killpg/os.getpgid do not exist on Windows -- raising=False lets
    # this test define them for the duration of the test regardless of the
    # host platform it actually runs on.
    monkeypatch.setattr(
        os,
        "killpg",
        lambda pgid, sig: calls.append(("killpg", (pgid, sig))),
        raising=False,
    )
    monkeypatch.setattr(os, "getpgid", lambda pid: pid, raising=False)
    monkeypatch.setattr(
        os, "kill", lambda pid, sig: calls.append(("kill", (pid, sig)))
    )

    monkeypatch.setattr(os, "name", "nt")
    calls.clear()
    _terminate_resident_tree(4321)
    assert calls == [("run", ["taskkill", "/F", "/T", "/PID", "4321"])]

    monkeypatch.setattr(os, "name", "posix")
    calls.clear()
    _terminate_resident_tree(4321)
    assert calls == [("killpg", (4321, _POSIX_KILL_SIGNAL))]
    assert not any(name == "run" for name, _ in calls), (
        "non-Windows cleanup must never depend on taskkill"
    )

    # killpg unavailable/failing -> per-PID os.kill fallback, still no taskkill.
    def _raise_killpg(pgid: int, sig: int) -> None:
        raise OSError("no such process group")

    monkeypatch.setattr(os, "killpg", _raise_killpg)
    calls.clear()
    _terminate_resident_tree(4321)
    assert calls == [("kill", (4321, _POSIX_KILL_SIGNAL))]
    assert not any(name == "run" for name, _ in calls)

    # pid <= 0 is always a no-op on either platform.
    calls.clear()
    _terminate_resident_tree(0)
    assert calls == []


class _FakePopen:
    """Minimal Popen stand-in: only ``pid``/``poll`` are consumed by the launch path."""

    def __init__(self, pid: int, *, exited: bool = False) -> None:
        self.pid = pid
        self._exited = exited
        self.args: list[str] | None = None
        self.kwargs: dict[str, object] = {}

    def poll(self) -> int | None:
        return 0 if self._exited else None


def _capture_popen(
    monkeypatch: pytest.MonkeyPatch, fake_pid: int = 4242, *, exited: bool = False
) -> _FakePopen:
    fake = _FakePopen(fake_pid, exited=exited)

    def _popen(args: list[str], **kwargs: object) -> _FakePopen:
        fake.args = list(args)
        fake.kwargs = dict(kwargs)
        return fake

    monkeypatch.setattr(resident_windows.subprocess, "Popen", _popen)
    return fake


def test_detach_resident_driver_prefers_announced_lock_holder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test-matrix item 1/8: when the resident announces itself (lock file)
    the returned identity must be the announced holder, never the spawned
    launcher PID -- and the host-identity receipt must record the same."""
    root = tmp_path / "runtime"
    package_src = tmp_path / "repo" / "src"
    package_src.mkdir(parents=True)
    fake = _capture_popen(monkeypatch, fake_pid=4242)
    # Holder announced on the very first poll: no sleeping required.
    monkeypatch.setattr(resident_windows, "read_primary_lock_pid", lambda _root: 7777)
    resolved = detach_resident_driver(root=root, package_src=package_src)
    assert resolved == 7777
    assert resolved != int(fake.pid)
    receipt = json.loads(
        (root / ".atlas" / "orchestration" / "sdk-runtime" / "supervisor-host.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["supervisor_pid"] == 7777
    assert (root / ".atlas" / "orchestration" / "sdk-runtime" / "supervisor.pid").read_text(
        encoding="utf-8"
    ).strip() == "7777"


def test_detach_resident_driver_returns_zero_when_never_announced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test-matrix item 6: a resident that never announces within the bounded
    poll budget (genuine startup failure, or a launcher-host quirk) must
    return 0 -- an explicit, honest "not confirmed" -- never the launcher's
    `Popen.pid` presented as if it were the resident's identity. D146: the
    old fallback (spawned PID) is exactly the bug this successor closes;
    "no worse than before" is not the bar -- the launcher PID was never a
    safe representation of the resident to begin with."""
    root = tmp_path / "runtime"
    package_src = tmp_path / "repo" / "src"
    package_src.mkdir(parents=True)
    fake = _capture_popen(monkeypatch, fake_pid=4242)
    monkeypatch.setattr(resident_windows, "read_primary_lock_pid", lambda _root: 0)
    monkeypatch.setattr(resident_windows, "_RESIDENT_STARTUP_POLL_ATTEMPTS", 3)
    sleeps: list[float] = []
    monkeypatch.setattr(
        resident_windows.time, "sleep", lambda sec: sleeps.append(float(sec))
    )
    resolved = detach_resident_driver(root=root, package_src=package_src)
    assert resolved == 0
    assert resolved != int(fake.pid)
    assert len(sleeps) == 3
    assert all(sec == resident_windows._RESIDENT_STARTUP_POLL_INTERVAL_SEC for sec in sleeps)


def test_detach_resident_driver_returns_delayed_lock_after_launcher_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D146 review R1 (test-matrix items B/2): a launcher/trampoline that
    exits *before* its real-interpreter child acquires the primary lock is
    a valid, not-failed startup sequence -- venv launcher spawns the real
    interpreter, the launcher exits, the child keeps importing and later
    acquires the lock. The bounded wait must keep watching the lock itself
    for its full budget regardless of the launched process object's own
    liveness, and must return the confirmed identity once it appears --
    never 0 merely because the launcher was already gone when it did.

    Negative control: this fails against predecessor candidate
    `37b1619e431a0dd83c86bd9ebbf01f6a35924984`, whose poll loop broke
    (returning 0) the instant `proc.poll() is not None`, before the delayed
    lock below had a chance to appear.
    """
    root = tmp_path / "runtime"
    package_src = tmp_path / "repo" / "src"
    package_src.mkdir(parents=True)
    # The launched process object is already gone from the very first poll.
    _capture_popen(monkeypatch, fake_pid=4242, exited=True)
    # The real resident child announces the lock only on the 5th poll --
    # well within budget, but after the (already-exited) launcher.
    polls: list[int] = []

    def _delayed_lock(_root: Path) -> int:
        polls.append(1)
        return 9999 if len(polls) >= 5 else 0

    monkeypatch.setattr(resident_windows, "read_primary_lock_pid", _delayed_lock)
    monkeypatch.setattr(resident_windows, "_RESIDENT_STARTUP_POLL_ATTEMPTS", 80)
    monkeypatch.setattr(resident_windows.time, "sleep", lambda _sec: None)
    resolved = detach_resident_driver(root=root, package_src=package_src)
    assert resolved == 9999
    assert resolved != 0
    assert len(polls) == 5


def test_detach_resident_driver_returns_zero_after_launcher_exit_with_no_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D146 review R1 (test-matrix item C): removing the launcher-exit
    early-exit must not weaken the *bounded* failure case -- a launcher
    that exits and never produces a confirmable resident still returns 0
    only after the full poll budget, not before and not after."""
    root = tmp_path / "runtime"
    package_src = tmp_path / "repo" / "src"
    package_src.mkdir(parents=True)
    _capture_popen(monkeypatch, fake_pid=4242, exited=True)
    monkeypatch.setattr(resident_windows, "read_primary_lock_pid", lambda _root: 0)
    monkeypatch.setattr(resident_windows, "_RESIDENT_STARTUP_POLL_ATTEMPTS", 5)
    sleeps: list[float] = []
    monkeypatch.setattr(
        resident_windows.time, "sleep", lambda sec: sleeps.append(float(sec))
    )
    resolved = detach_resident_driver(root=root, package_src=package_src)
    assert resolved == 0
    # Full budget consumed -- the (already-exited) launcher does not
    # truncate the wait.
    assert len(sleeps) == 5


def test_detach_resident_driver_confirms_identity_with_launcher_still_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D146 review R1 (test-matrix items A/D): the ordinary case -- the
    launcher never exits during the poll (this host's own observed
    behavior) -- must still resolve correctly, both when the lock appears
    immediately and when it never appears within budget."""
    root = tmp_path / "runtime"
    package_src = tmp_path / "repo" / "src"
    package_src.mkdir(parents=True)
    _capture_popen(monkeypatch, fake_pid=4242, exited=False)
    monkeypatch.setattr(resident_windows, "read_primary_lock_pid", lambda _root: 8888)
    resolved = detach_resident_driver(root=root, package_src=package_src)
    assert resolved == 8888

    root2 = tmp_path / "runtime2"
    package_src2 = tmp_path / "repo2" / "src"
    package_src2.mkdir(parents=True)
    _capture_popen(monkeypatch, fake_pid=4243, exited=False)
    monkeypatch.setattr(resident_windows, "read_primary_lock_pid", lambda _root: 0)
    monkeypatch.setattr(resident_windows, "_RESIDENT_STARTUP_POLL_ATTEMPTS", 3)
    monkeypatch.setattr(resident_windows.time, "sleep", lambda _sec: None)
    resolved2 = detach_resident_driver(root=root2, package_src=package_src2)
    assert resolved2 == 0


def test_detach_resident_driver_invokes_exact_interpreter_module_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D146-A/D146-C non-spawn defense: the launch command must be an exact
    ``<interpreter> -m project_atlas.cli orchestrator governor-resident-run``
    invocation with the package source pinned on PYTHONPATH -- never a bare
    ``atlas`` resolved from PATH, which on this class of host can silently
    execute an unrelated stale Atlas installation (see the D146-C finding)."""
    root = tmp_path / "runtime"
    package_src = tmp_path / "repo" / "src"
    package_src.mkdir(parents=True)
    fake = _capture_popen(monkeypatch, fake_pid=4242)
    monkeypatch.setattr(resident_windows, "read_primary_lock_pid", lambda _root: 4242)
    interpreter = tmp_path / "repo" / ".venv" / "Scripts" / "python.exe"
    detach_resident_driver(root=root, package_src=package_src, python=str(interpreter))
    assert fake.args is not None
    assert fake.args == [
        str(interpreter),
        "-m",
        "project_atlas.cli",
        "orchestrator",
        "governor-resident-run",
        "--root",
        str(root),
        "--detached-worker",
    ]
    env = fake.kwargs["env"]
    assert isinstance(env, dict)
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(package_src)
    assert fake.kwargs["stdin"] is subprocess.DEVNULL
    assert fake.kwargs["cwd"] == str(root)


def test_observer_timeout_pending_is_not_ci_fail() -> None:
    obs = CiObservation(head_sha="a" * 40, status="PENDING")
    disp = classify_watch_session(
        watch_exit_code=1, watch_timed_out=True, observation=obs
    )
    assert disp == "CI_STILL_RUNNING"
    assert disp != "CI_TERMINAL_FAIL"


def test_observer_exit_without_observation_is_observer_exited() -> None:
    disp = classify_watch_session(
        watch_exit_code=1, watch_timed_out=True, observation=None
    )
    assert disp == "OBSERVER_EXITED"


def test_ask2_after_build_portfolio_passes(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    fixture = repo / "tests" / "fixtures" / "demo" / "estate" / "harbor-api"
    work = tmp_path / "work"
    source = work / "harbor-api"
    shutil.copytree(fixture, source)
    manifest = work / "manifest.json"
    vault = work / "vault"
    assert main(["init", "--output", str(vault)]) == EXIT_OK
    assert main(["discover", "--source", str(source), "--output", str(manifest)]) == EXIT_OK
    assert (
        main(
            [
                "ingest",
                "--manifest",
                str(manifest),
                "--vault",
                str(vault),
                "--source",
                str(source),
            ]
        )
        == EXIT_OK
    )
    assert main(["build-indexes", "--vault", str(vault)]) == EXIT_OK
    assert main(["build-portfolio", "--vault", str(vault)]) == EXIT_OK
    assert main(["validate", "--vault", str(vault)]) == EXIT_OK
    assert (
        main(
            [
                "ask2",
                "--vault",
                str(vault),
                "--project",
                "harbor-api",
                "--question",
                "audit logging",
                "--json",
            ]
        )
        == EXIT_OK
    )


def test_autonomy_gate_blocks_premature_d144_pattern() -> None:
    state = AutonomyReturnState(
        ready_nodes=1,
        uncertified_changes=6,
        derivable_successors=1,
        project_terminal=False,
    )
    assert may_emit_final_return(state) is False
