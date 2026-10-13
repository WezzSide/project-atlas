"""Domains A (spawn stress), B (rapid spawn/terminate), C (process-tree
termination), and L (console/window suppression)."""

from __future__ import annotations

import contextlib
import random
import subprocess
import sys
import time
import uuid
from typing import Any

from reliability_lab import procutil
from reliability_lab.model import ExperimentResult
from reliability_lab.runctl import Mode, TrialOutcome, run_trials, scaled_iterations

_SPAWNED_MARKERS: list[str] = []  # module-scope, reset per experiment run


def _marker() -> str:
    m = f"atlas-lab-{uuid.uuid4().hex[:12]}"
    _SPAWNED_MARKERS.append(m)
    return m


def _spawn(code: str, marker: str, extra_flags: int = 0) -> subprocess.Popen[bytes]:
    creationflags = (
        int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        | int(getattr(subprocess, "DETACHED_PROCESS", 0))
        | procutil.NO_WINDOW
        | extra_flags
    )
    return subprocess.Popen(
        [sys.executable, "-c", code, f"# {marker}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )


def _cleanup_all_markers() -> int:
    """Mandatory cleanup-proof step: terminate anything still matching any
    marker this experiment run produced, then verify via fresh enumeration."""
    leaked = 0
    for m in _SPAWNED_MARKERS:
        remaining = procutil.snapshot_pids(m)
        for pid in remaining:
            procutil.terminate_tree(pid)
        time.sleep(0.2)
        still = procutil.snapshot_pids(m)
        leaked += len(still)
    _SPAWNED_MARKERS.clear()
    return leaked


# ---------------------------------------------------------------------------
# Domain A -- spawn stress
# ---------------------------------------------------------------------------


def experiment_proc_spawn_divergence(mode: Mode, seed: int) -> ExperimentResult:
    """EXP-PROC-001: repeated venv-launcher spawns -- does Popen.pid diverge
    from the workload's own os.getpid() consistently, intermittently, or
    never, and what's the identity-convergence latency distribution?"""
    n = scaled_iterations(30, mode)
    _SPAWNED_MARKERS.clear()

    def trial(i: int, rng: random.Random) -> TrialOutcome:
        marker = _marker()
        code = "import os,sys,time;print(os.getpid());sys.stdout.flush();time.sleep(1.5)"
        start = time.perf_counter()
        proc = _spawn(code, marker)
        try:
            line = proc.stdout.readline() if proc.stdout else b""
            reported = int(line.strip() or b"0")
        except (ValueError, OSError):
            reported = 0
        latency_ms = (time.perf_counter() - start) * 1000
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
        ok = reported > 0
        return TrialOutcome(
            ok=ok,
            latency_ms=latency_ms,
            error_class=None if ok else "NoPidReported",
            detail="" if ok else "workload never reported a PID",
            contract_violated="spawn must always produce an observable workload identity",
            pids={"popen_pid": proc.pid, "workload_pid": reported},
        )

    return run_trials(
        experiment_id="EXP-PROC-001",
        domain="process_spawn_stress",
        hypothesis="Every spawn yields an observable, non-zero workload identity, under repeats.",
        contract="spawn code must always be able to identify the workload it spawned",
        stress_dimension="repetition (venv launcher -> real interpreter spawn)",
        expected_invariant="workload PID > 0 on every trial",
        seed=seed,
        iterations=n,
        trial_fn=trial,
        per_op_timeout_sec=8.0,
        cleanup_fn=_cleanup_all_markers,
        notes=(
            "Identity observability only, not launcher==workload equality (see conformance "
            "WIN-PROC-002)."
        ),
    )


def experiment_proc_ancestry_stability(mode: Mode, seed: int) -> ExperimentResult:
    """EXP-PROC-002: is the launcher->real-interpreter ParentProcessId
    relationship stable across repeated spawns, or does it ever diverge?"""
    n = scaled_iterations(15, mode)
    _SPAWNED_MARKERS.clear()

    def trial(i: int, rng: random.Random) -> TrialOutcome:
        marker = _marker()
        code = "import os,sys,time;print(os.getpid());sys.stdout.flush();time.sleep(1.0)"
        start = time.perf_counter()
        proc = _spawn(code, marker)
        try:
            line = proc.stdout.readline() if proc.stdout else b""
            reported = int(line.strip() or b"0")
        except (ValueError, OSError):
            reported = 0
        info = procutil.win32_process_info(reported) if reported else None
        latency_ms = (time.perf_counter() - start) * 1000
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
        is_child = info is not None and info.get("ParentProcessId") == proc.pid
        return TrialOutcome(
            ok=is_child,
            latency_ms=latency_ms,
            error_class=None if is_child else "AncestryMismatch",
            detail="" if is_child else f"ancestry={info}",
            contract_violated="the observed workload must be a direct child of the spawned handle",
            pids={"popen_pid": proc.pid, "workload_pid": reported},
        )

    return run_trials(
        experiment_id="EXP-PROC-002",
        domain="process_spawn_stress",
        hypothesis=(
            "Launcher->real-interpreter ancestry (ParentProcessId) is stable across repeated "
            "spawns."
        ),
        contract="ancestry used for identity confirmation must not intermittently diverge",
        stress_dimension="repetition",
        expected_invariant="ParentProcessId == Popen.pid on every trial",
        seed=seed,
        iterations=n,
        trial_fn=trial,
        per_op_timeout_sec=8.0,
        cleanup_fn=_cleanup_all_markers,
    )


# ---------------------------------------------------------------------------
# Domain B -- rapid spawn/terminate, fault injection
# ---------------------------------------------------------------------------


def experiment_proc_rapid_cycle(mode: Mode, seed: int) -> ExperimentResult:
    """EXP-PROC-003: rapid spawn -> identify -> terminate -> verify-gone
    cycles. Looks for late descendants, stale identity, or a cycle where
    'gone' was reported before it actually was."""
    n = scaled_iterations(25, mode)
    _SPAWNED_MARKERS.clear()

    def trial(i: int, rng: random.Random) -> TrialOutcome:
        marker = _marker()
        code = "import os,sys,time;print(os.getpid());sys.stdout.flush();time.sleep(3)"
        start = time.perf_counter()
        proc = _spawn(code, marker)
        try:
            line = proc.stdout.readline() if proc.stdout else b""
            reported = int(line.strip() or b"0")
        except (ValueError, OSError):
            reported = 0
        procutil.terminate_tree(reported or proc.pid)
        gone = not procutil.process_alive(reported) if reported else True
        latency_ms = (time.perf_counter() - start) * 1000
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
        return TrialOutcome(
            ok=gone,
            latency_ms=latency_ms,
            error_class=None if gone else "StillAliveAfterTerminate",
            detail="" if gone else f"pid {reported} still alive after terminate_tree",
            contract_violated="terminate-then-verify must observe the process actually gone",
            pids={"workload_pid": reported},
        )

    return run_trials(
        experiment_id="EXP-PROC-003",
        domain="rapid_spawn_terminate",
        hypothesis=(
            "Rapid spawn/identify/terminate/verify cycles never leave a 'gone' process actually "
            "alive."
        ),
        contract="terminate_tree + immediate re-check must agree with reality, not report success",
        stress_dimension="rapid repetition, no settle time between cycles",
        expected_invariant="process reported gone is actually gone",
        seed=seed,
        iterations=n,
        trial_fn=trial,
        per_op_timeout_sec=10.0,
        cleanup_fn=_cleanup_all_markers,
    )


def experiment_proc_forced_early_exit_and_delay(mode: Mode, seed: int) -> ExperimentResult:
    """EXP-PROC-004 (fault injection): half the trials force the child to
    exit immediately (simulating a crashed/failed workload); half delay
    startup by a random amount before reporting its PID (simulating slow
    I/O). Cleanup must succeed either way, with no leak."""
    n = scaled_iterations(20, mode)
    _SPAWNED_MARKERS.clear()

    def trial(i: int, rng: random.Random) -> TrialOutcome:
        marker = _marker()
        forced_exit = i % 2 == 0
        if forced_exit:
            code = "import sys;sys.exit(1)"
        else:
            delay = rng.uniform(0.2, 1.2)
            code = (
                f"import os,sys,time;time.sleep({delay});print(os.getpid());"
                "sys.stdout.flush();time.sleep(1)"
            )
        start = time.perf_counter()
        proc = _spawn(code, marker)
        try:
            line = proc.stdout.readline() if proc.stdout else b""
            reported = int(line.strip() or b"0")
        except (ValueError, OSError):
            reported = 0
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            procutil.terminate_tree(proc.pid)
        latency_ms = (time.perf_counter() - start) * 1000
        if forced_exit:
            ok = reported == 0  # correctly observed no identity, not a fake one
            detail = "" if ok else f"forced-exit case still reported PID {reported}"
        else:
            ok = reported > 0
            detail = "" if ok else "delayed-start case never reported a PID within budget"
        return TrialOutcome(
            ok=ok,
            latency_ms=latency_ms,
            error_class=None if ok else "IdentityContractViolation",
            detail=detail,
            contract_violated=(
                "forced-exit yields no identity; delayed-start still yields one eventually"
            ),
            pids={"popen_pid": proc.pid, "workload_pid": reported},
        )

    return run_trials(
        experiment_id="EXP-PROC-004",
        domain="rapid_spawn_terminate",
        hypothesis=(
            "Forced early exit and delayed startup are each handled per their own contract, not "
            "conflated."
        ),
        contract=(
            "a crashed workload has no live identity; a slow-but-healthy one is not given up on "
            "prematurely"
        ),
        stress_dimension="fault injection: forced exit / injected startup delay",
        expected_invariant="identity presence matches actual workload health",
        seed=seed,
        iterations=n,
        trial_fn=trial,
        per_op_timeout_sec=10.0,
        cleanup_fn=_cleanup_all_markers,
    )


# ---------------------------------------------------------------------------
# Domain C -- process tree termination
# ---------------------------------------------------------------------------


def experiment_proc_tree_termination_stress(mode: Mode, seed: int) -> ExperimentResult:
    """EXP-PROC-005: parent -> child -> grandchild trees, varying whether
    the parent is still alive when termination is attempted, stressed
    repeatedly. Requires zero leaked descendants every time."""
    n = scaled_iterations(15, mode)
    _SPAWNED_MARKERS.clear()

    def trial(i: int, rng: random.Random) -> TrialOutcome:
        marker = _marker()
        parent_exits_early = i % 3 == 0
        parent_code = (
            "import subprocess,sys,time;"
            "p=subprocess.Popen([sys.executable,'-c',"
            "'import os,time;print(os.getpid());import sys;sys.stdout.flush();time.sleep(5)'"
            f",'# {marker}'],stdout=subprocess.PIPE,creationflags=0x08000000);"
            "print(p.pid,flush=True);"
            "line=p.stdout.readline();print(line.decode().strip(),flush=True);"
            + ("time.sleep(0.3)" if parent_exits_early else "p.wait()")
        )
        start = time.perf_counter()
        parent = _spawn(parent_code, marker)
        assert parent.stdout is not None  # _spawn always sets stdout=PIPE
        try:
            int((parent.stdout.readline() or b"0").strip() or b"0")
            grandchild_pid = int((parent.stdout.readline() or b"0").strip() or b"0")
        except (ValueError, OSError):
            _grandchild_via_popen, grandchild_pid = 0, 0
        if parent_exits_early:
            time.sleep(0.5)
        procutil.terminate_tree(parent.pid)
        time.sleep(0.3)
        gc_gone = not procutil.process_alive(grandchild_pid) if grandchild_pid else True
        latency_ms = (time.perf_counter() - start) * 1000
        with contextlib.suppress(subprocess.TimeoutExpired):
            parent.wait(timeout=5)
        return TrialOutcome(
            ok=gc_gone,
            latency_ms=latency_ms,
            error_class=None if gc_gone else "GrandchildSurvivedTreeKill",
            detail=(
                ""
                if gc_gone
                else f"grandchild {grandchild_pid} survived, early={parent_exits_early}"
            ),
            contract_violated=(
                "terminate_tree must reach grandchildren even if the parent already exited"
            ),
            pids={"parent_pid": parent.pid, "grandchild_pid": grandchild_pid},
        )

    return run_trials(
        experiment_id="EXP-PROC-005",
        domain="process_tree_termination",
        hypothesis="taskkill /F /T reaches a grandchild after the immediate parent has exited.",
        contract="no descendant of a terminated tree may survive intermediate-process lifetime",
        stress_dimension="varying parent-alive/parent-exited-early state, repeated",
        expected_invariant="zero surviving descendants after terminate_tree",
        seed=seed,
        iterations=n,
        trial_fn=trial,
        per_op_timeout_sec=12.0,
        cleanup_fn=_cleanup_all_markers,
    )


# ---------------------------------------------------------------------------
# Domain L -- console/window suppression
# ---------------------------------------------------------------------------


def experiment_console_suppression(mode: Mode, seed: int) -> ExperimentResult:
    """EXP-PROC-006: quantifies that CREATE_NO_WINDOW eliminates new
    conhost.exe allocation across many spawns. Deliberately does NOT
    include a without-the-flag comparison arm: that already caused three
    real, user-visible incidents during this lab's own predecessor's
    development, and reproducing known-disruptive behavior for a
    quantification isn't worth it -- the baseline (flag absent -> window
    appears) is already established, real, documented evidence, not a gap
    this experiment needs to re-open."""
    n = scaled_iterations(15, mode)
    _SPAWNED_MARKERS.clear()

    def trial(i: int, rng: random.Random) -> TrialOutcome:
        marker = _marker()
        code = "import os,sys,time;print(os.getpid());sys.stdout.flush();time.sleep(0.8)"
        before = _conhost_snapshot()
        start = time.perf_counter()
        proc = _spawn(code, marker)
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            procutil.terminate_tree(proc.pid)
        latency_ms = (time.perf_counter() - start) * 1000
        after = _conhost_snapshot()
        new_conhosts = after - before
        ok = new_conhosts <= 0
        return TrialOutcome(
            ok=ok,
            latency_ms=latency_ms,
            error_class=None if ok else "NewConsoleWindowDetected",
            detail="" if ok else f"conhost count increased by {new_conhosts}",
            contract_violated="CREATE_NO_WINDOW must prevent new console allocation for any spawn",
        )

    return run_trials(
        experiment_id="EXP-PROC-006",
        domain="console_window_suppression",
        hypothesis=(
            "CREATE_NO_WINDOW, applied to every spawn in this package, never allows a new "
            "conhost.exe to appear."
        ),
        contract="no experiment run may pop a visible console window on the user's desktop",
        stress_dimension="repetition",
        expected_invariant="conhost.exe process count never increases across a spawn+wait cycle",
        seed=seed,
        iterations=n,
        trial_fn=trial,
        per_op_timeout_sec=8.0,
        cleanup_fn=_cleanup_all_markers,
        notes="No without-CREATE_NO_WINDOW comparison arm -- see docstring.",
    )


def _conhost_snapshot() -> int:
    import subprocess as sp

    if sys.platform != "win32":
        return 0
    ps = "(Get-CimInstance Win32_Process -Filter \"Name='conhost.exe'\").Count"
    result = sp.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
        check=False,
        creationflags=procutil.NO_WINDOW,
    )
    try:
        return int((result.stdout or "0").strip() or "0")
    except ValueError:
        return 0


EXPERIMENTS: list[Any] = [
    experiment_proc_spawn_divergence,
    experiment_proc_ancestry_stability,
    experiment_proc_rapid_cycle,
    experiment_proc_forced_early_exit_and_delay,
    experiment_proc_tree_termination_stress,
    experiment_console_suppression,
]
