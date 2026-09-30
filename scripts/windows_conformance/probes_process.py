"""Domain A (process identity) and Domain B (process termination) probes.

Every probe here that spawns a real process supplies domain-C cleanup
evidence via :func:`procutil.with_cleanup_proof` -- a pre/post
``Win32_Process`` enumeration, not an inferred-from-exit-code claim.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from windows_conformance import procutil
from windows_conformance.model import ProbeOutcome

_MARKER_PREFIX = "atlas-win-conformance-probe"

#: CREATE_NO_WINDOW. A DETACHED_PROCESS parent has no console of its own,
#: so Windows allocates a brand-new, visible console window for any
#: console-subsystem child (plain python.exe) it spawns unless this is set
#: -- the exact real, user-reported incident already documented in
#: project_atlas.orchestration.sdk.host.no_window_creationflags(). Every
#: real spawn in this file (including nested spawns embedded in the probe
#: subprocess's own source text) must set it.
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _unique_marker() -> str:
    """A marker unique to this single spawn, never reused across probes or
    across harness invocations -- a shared constant marker would let a
    process leaked by an *earlier* run (or a concurrent one) silently
    inflate a later probe's pre/post leak counts, exactly the class of
    harness-internal false positive this framework must not produce."""
    return f"{_MARKER_PREFIX}-{uuid.uuid4().hex[:12]}"


def _spawn_reporter(python: str, extra_sleep: float = 0.0) -> tuple[subprocess.Popen[bytes], str]:
    """Spawn a detached child that prints its own PID then sleeps briefly,
    tagged with a fresh unique marker on its command line so process
    enumeration can find only *this spawn*, never another probe's or run's."""
    marker = _unique_marker()
    code = (
        "import os,sys,time;"
        f"print(os.getpid());sys.stdout.flush();time.sleep({extra_sleep + 2.0})"
    )
    creationflags = 0
    start_new_session = False
    if sys.platform == "win32":
        creationflags = (
            int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            | int(getattr(subprocess, "DETACHED_PROCESS", 0))
            | _NO_WINDOW
        )
    else:
        start_new_session = True
    proc = subprocess.Popen(
        [python, "-c", code, f"# {marker}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        start_new_session=start_new_session,
    )
    return proc, marker


def probe_win_proc_001_venv_launcher_stub() -> ProbeOutcome:
    """WIN-PROC-001: is this venv's ``Scripts\\python.exe`` a launcher/
    trampoline stub distinct from the base interpreter, or a real copy?"""
    contract = (
        "Atlas must not assume sys.executable is the actual interpreter binary "
        "on every Windows venv-creation tool; code that needs the real running "
        "process identity must confirm it via an authoritative signal (e.g. a "
        "primary-lock file the child itself writes), not derive it from Popen.pid."
    )
    if sys.platform != "win32":
        return ProbeOutcome(
            "WIN-PROC-001",
            "process_identity",
            contract,
            "not on Windows",
            "NOT_APPLICABLE",
            "OBSERVED",
        )
    venv_python = Path(sys.executable)
    pyvenv_cfg = venv_python.parent.parent / "pyvenv.cfg"
    if not pyvenv_cfg.is_file():
        return ProbeOutcome(
            "WIN-PROC-001",
            "process_identity",
            contract,
            "not running inside a venv (no pyvenv.cfg found)",
            "NOT_TESTED",
            "UNKNOWN",
        )
    home_line = next(
        (
            line.split("=", 1)[1].strip()
            for line in pyvenv_cfg.read_text(encoding="utf-8").splitlines()
            if line.strip().lower().startswith("home")
        ),
        None,
    )
    if not home_line:
        return ProbeOutcome(
            "WIN-PROC-001",
            "process_identity",
            contract,
            "pyvenv.cfg has no 'home' entry",
            "NOT_TESTED",
            "UNKNOWN",
        )
    base_python = Path(home_line) / "python.exe"
    if not base_python.is_file():
        return ProbeOutcome(
            "WIN-PROC-001",
            "process_identity",
            contract,
            f"base interpreter named by pyvenv.cfg does not exist: {base_python}",
            "NOT_TESTED",
            "UNKNOWN",
        )
    venv_bytes = venv_python.read_bytes()
    base_bytes = base_python.read_bytes()
    is_stub = venv_bytes != base_bytes
    return ProbeOutcome(
        "WIN-PROC-001",
        "process_identity",
        contract,
        (
            f"venv python.exe ({len(venv_bytes)} bytes) "
            f"{'differs from' if is_stub else 'matches'} base interpreter "
            f"({len(base_bytes)} bytes) -> "
            f"{'launcher/trampoline stub confirmed' if is_stub else 'real interpreter copy'}"
        ),
        "PASS",  # the contract is "don't assume" -- observing+recording either way satisfies it
        "OBSERVED",
        evidence={
            "venv_python_size": len(venv_bytes),
            "base_python_size": len(base_bytes),
            "venv_python_sha256": hashlib.sha256(venv_bytes).hexdigest(),
            "base_python_sha256": hashlib.sha256(base_bytes).hexdigest(),
            "is_launcher_stub": is_stub,
        },
    )


def probe_win_proc_002_popen_pid_vs_workload_pid() -> ProbeOutcome:
    """WIN-PROC-002: does ``Popen.pid`` equal the PID the spawned code
    actually runs under? Demonstrates reality; does not assume either
    answer."""
    contract = (
        "Atlas code that needs to identify/manage the spawned workload must not "
        "assume Popen.pid == the workload's own os.getpid(); it must confirm."
    )
    proc, marker = _spawn_reporter(sys.executable)
    pre = procutil.snapshot_pids(marker)
    try:
        line = proc.stdout.readline() if proc.stdout else b""
        reported_pid = int(line.strip() or b"0")
    except (ValueError, OSError):
        reported_pid = 0
    matches = reported_pid > 0 and reported_pid == proc.pid
    ancestry = procutil.win32_process_info(reported_pid) if reported_pid else None
    proof = procutil.with_cleanup_proof(marker, [proc.pid, reported_pid])
    proc.wait(timeout=5)
    return ProbeOutcome(
        "WIN-PROC-002",
        "process_identity",
        contract,
        (
            f"Popen.pid={proc.pid}, workload-reported os.getpid()={reported_pid} -> "
            f"{'SAME process' if matches else 'DIFFERENT processes'}"
        ),
        "PASS",  # observation itself always succeeds; the *fact* is recorded in evidence
        "OBSERVED",
        evidence={
            "popen_pid_equals_workload_pid": matches,
            "workload_ancestry": ancestry,
            **proof.as_evidence(),
        },
    )


def probe_win_proc_003_detached_child_ancestry() -> ProbeOutcome:
    """WIN-PROC-003: for a DETACHED_PROCESS/CREATE_NEW_PROCESS_GROUP spawn,
    what does ParentProcessId actually show once the launcher has handed
    off to the real interpreter?"""
    contract = (
        "A detached spawn's process tree shape (launcher -> real interpreter, "
        "possibly deeper) must be observed directly via the OS, never assumed "
        "from the spawn call's own return value."
    )
    if sys.platform != "win32":
        return ProbeOutcome(
            "WIN-PROC-003",
            "process_identity",
            contract,
            "not on Windows",
            "NOT_APPLICABLE",
            "OBSERVED",
        )
    proc, marker = _spawn_reporter(sys.executable, extra_sleep=1.0)
    try:
        line = proc.stdout.readline() if proc.stdout else b""
        reported_pid = int(line.strip() or b"0")
    except (ValueError, OSError):
        reported_pid = 0
    info = procutil.win32_process_info(reported_pid) if reported_pid else None
    is_child_of_popen = info is not None and info.get("ParentProcessId") == proc.pid
    proof = procutil.with_cleanup_proof(marker, [proc.pid, reported_pid])
    proc.wait(timeout=5)
    return ProbeOutcome(
        "WIN-PROC-003",
        "process_identity",
        contract,
        (
            f"workload PID {reported_pid} ParentProcessId="
            f"{info.get('ParentProcessId') if info else 'unknown'} "
            f"(Popen.pid={proc.pid}) -> "
            f"{'confirmed direct child of the spawned handle' if is_child_of_popen else 'NOT a direct child (deeper chain or unrelated)'}"
        ),
        "PASS",
        "OBSERVED" if info else "UNKNOWN",
        evidence={"ancestry": info, "is_direct_child_of_popen_pid": is_child_of_popen, **proof.as_evidence()},
    )


def probe_win_term_001_os_kill_sigterm_reliability() -> ProbeOutcome:
    """WIN-TERM-001: does ``os.kill(pid, SIGTERM)`` reliably terminate a
    DETACHED_PROCESS/CREATE_NEW_PROCESS_GROUP child on this host?"""
    contract = (
        "Atlas cleanup code must not assume os.kill(pid, SIGTERM) reliably "
        "terminates a detached Windows child; it must verify termination via "
        "independent process enumeration before claiming cleanup succeeded."
    )
    import os
    import signal

    proc, marker = _spawn_reporter(sys.executable, extra_sleep=3.0)
    try:
        line = proc.stdout.readline() if proc.stdout else b""
        reported_pid = int(line.strip() or b"0")
    except (ValueError, OSError):
        reported_pid = 0
    kill_raised: str | None = None
    try:
        os.kill(reported_pid, signal.SIGTERM)
    except OSError as exc:
        kill_raised = f"{type(exc).__name__}: {exc}"
    time.sleep(1.0)
    still_alive = procutil.process_alive(reported_pid)
    # cleanup regardless of what os.kill did, via the proven-reliable path
    proof = procutil.with_cleanup_proof(marker, [proc.pid, reported_pid])
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    reliable = kill_raised is None and not still_alive
    return ProbeOutcome(
        "WIN-TERM-001",
        "process_termination",
        contract,
        (
            f"os.kill(SIGTERM) on detached child {reported_pid}: "
            f"{'raised ' + kill_raised if kill_raised else 'did not raise'}; "
            f"process still alive 1s later: {still_alive} -> "
            f"{'reliable' if reliable else 'NOT reliable on this host/process class'}"
        ),
        "PASS",
        "OBSERVED" if reported_pid else "UNKNOWN",
        evidence={
            "os_kill_raised": kill_raised,
            "still_alive_after_os_kill": still_alive,
            "os_kill_reliable": reliable,
            **proof.as_evidence(),
        },
    )


def probe_win_term_002_taskkill_without_tree_flag() -> ProbeOutcome:
    """WIN-TERM-002: does ``taskkill /F /PID`` (no ``/T``) reach a live
    grandchild, or only the direct target?"""
    contract = (
        "Atlas cleanup that must guarantee no descendant survives has to use "
        "the tree-kill form explicitly -- a bare /PID kill must not be assumed "
        "to reach descendants."
    )
    if sys.platform != "win32":
        return ProbeOutcome(
            "WIN-TERM-002",
            "process_termination",
            contract,
            "not on Windows",
            "NOT_APPLICABLE",
            "OBSERVED",
        )
    # parent spawns a child that itself spawns a grandchild reporter.
    marker = _unique_marker()
    code = (
        "import subprocess,sys,os;"
        "p=subprocess.Popen([sys.executable,'-c',"
        "'import os,time;print(os.getpid());import sys;sys.stdout.flush();time.sleep(4)'"
        f",'# {marker}'],stdout=subprocess.PIPE,creationflags=0x08000000);"
        "print(p.pid,flush=True);"
        "line=p.stdout.readline();print(line.decode().strip(),flush=True);"
        "p.wait()"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", code, f"# {marker}"],
        stdout=subprocess.PIPE,
        creationflags=int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        | int(getattr(subprocess, "DETACHED_PROCESS", 0))
        | _NO_WINDOW,
    )
    assert parent.stdout is not None
    child_pid = int((parent.stdout.readline() or b"0").strip() or b"0")
    grandchild_pid = int((parent.stdout.readline() or b"0").strip() or b"0")
    subprocess.run(
        ["taskkill", "/F", "/PID", str(parent.pid)],
        capture_output=True,
        check=False,
        creationflags=_NO_WINDOW,
    )
    time.sleep(0.8)
    grandchild_survived = procutil.process_alive(grandchild_pid)
    proof = procutil.with_cleanup_proof(marker, [parent.pid, child_pid, grandchild_pid])
    try:
        parent.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    return ProbeOutcome(
        "WIN-TERM-002",
        "process_termination",
        contract,
        (
            f"taskkill /F /PID {parent.pid} (no /T): grandchild {grandchild_pid} "
            f"survived={grandchild_survived}"
        ),
        "PASS",
        "OBSERVED" if grandchild_pid else "UNKNOWN",
        evidence={"grandchild_survived_without_tree_flag": grandchild_survived, **proof.as_evidence()},
    )


def probe_win_term_003_taskkill_tree_flag_reaches_descendants() -> ProbeOutcome:
    """WIN-TERM-003: does ``taskkill /F /T /PID`` reach a live grandchild?"""
    contract = (
        "taskkill /F /T /PID is the mechanism Atlas relies on to guarantee "
        "no descendant of a detached spawn survives cleanup; this must be "
        "proven on this host, not assumed from the flag's documented meaning."
    )
    if sys.platform != "win32":
        return ProbeOutcome(
            "WIN-TERM-003",
            "process_termination",
            contract,
            "not on Windows",
            "NOT_APPLICABLE",
            "OBSERVED",
        )
    marker = _unique_marker()
    code = (
        "import subprocess,sys;"
        "p=subprocess.Popen([sys.executable,'-c',"
        "'import os,time;print(os.getpid());import sys;sys.stdout.flush();time.sleep(4)'"
        f",'# {marker}'],stdout=subprocess.PIPE,creationflags=0x08000000);"
        "print(p.pid,flush=True);"
        "line=p.stdout.readline();print(line.decode().strip(),flush=True);"
        "p.wait()"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", code, f"# {marker}"],
        stdout=subprocess.PIPE,
        creationflags=int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        | int(getattr(subprocess, "DETACHED_PROCESS", 0))
        | _NO_WINDOW,
    )
    assert parent.stdout is not None
    child_pid = int((parent.stdout.readline() or b"0").strip() or b"0")
    grandchild_pid = int((parent.stdout.readline() or b"0").strip() or b"0")
    proof = procutil.with_cleanup_proof(marker, [parent.pid, child_pid, grandchild_pid])
    try:
        parent.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    return ProbeOutcome(
        "WIN-TERM-003",
        "process_termination",
        contract,
        f"taskkill /F /T /PID reached the whole tree -> leak_free={proof.leak_free}",
        "PASS" if proof.leak_free else "FAIL",
        "OBSERVED" if grandchild_pid else "UNKNOWN",
        evidence=proof.as_evidence(),
    )


def probe_win_term_004_dead_and_invalid_pid_are_noops() -> ProbeOutcome:
    """WIN-TERM-004: terminating an already-dead or invalid PID must not
    raise, hang, or falsely report a live-process kill."""
    contract = "Cleanup helpers must be safe no-ops on already-dead or invalid PIDs."
    errors: list[str] = []
    for bad_pid in (999_999_999, 0, -1, -999_999):
        try:
            procutil.terminate_tree(bad_pid)
        except Exception as exc:  # noqa: BLE001 -- this probe's whole point is to catch any raise
            errors.append(f"{bad_pid}: {type(exc).__name__}: {exc}")
    return ProbeOutcome(
        "WIN-TERM-004",
        "process_termination",
        contract,
        f"terminate_tree on dead/invalid PIDs (999999999, 0, -1, -999999): {len(errors)} raised",
        "PASS" if not errors else "FAIL",
        "OBSERVED",
        evidence={"raised": errors},
    )


PROBES = [
    probe_win_proc_001_venv_launcher_stub,
    probe_win_proc_002_popen_pid_vs_workload_pid,
    probe_win_proc_003_detached_child_ancestry,
    probe_win_term_001_os_kill_sigterm_reliability,
    probe_win_term_002_taskkill_without_tree_flag,
    probe_win_term_003_taskkill_tree_flag_reaches_descendants,
    probe_win_term_004_dead_and_invalid_pid_are_noops,
]
