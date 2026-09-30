"""Shared real-process spawn/snapshot/cleanup helpers for process probes.

Every probe that spawns a real OS process uses these so that domain C
(process cleanup proof) is enforced structurally rather than trusted: a
pre-spawn snapshot, a post-cleanup snapshot, and an explicit leak count --
never inferred from a command's exit code alone.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass

#: CREATE_NO_WINDOW. Every subprocess spawned by this package -- not only
#: DETACHED_PROCESS workloads -- must pass this: running under a
#: non-native-console host (e.g. an MSYS2/Git-Bash pty, which provides no
#: real Win32 console for a child to inherit) makes Windows allocate a
#: brand-new, visible console window for *any* console-subsystem child
#: (git.exe, cmd.exe, powershell.exe, taskkill.exe, tasklist.exe, a bare
#: python.exe) that doesn't explicitly suppress it -- a real, user-reported
#: incident, not a theoretical one. Every module in this package imports
#: this single constant rather than redefining it.
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_NO_WINDOW = NO_WINDOW  # backward-compat alias within this module


def snapshot_pids(command_line_substring: str) -> set[int]:
    """PIDs of currently-live processes whose command line contains
    ``command_line_substring``. Windows-only (Win32_Process); returns an
    empty set elsewhere -- callers on non-Windows must not treat that as a
    real zero-process observation."""
    if sys.platform != "win32":
        return set()
    # Exclude $PID (this PowerShell invocation's own process): its -Command
    # argument necessarily contains command_line_substring as a literal
    # diagnostic-tool artifact, which would otherwise self-match every time
    # and make a true zero-leak result structurally unreachable.
    ps = (
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ $_.CommandLine -like '*{command_line_substring}*' -and $_.ProcessId -ne $PID }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
        check=False,
        creationflags=_NO_WINDOW,
    )
    pids: set[int] = set()
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if line.isdigit():
            pids.add(int(line))
    return pids


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    # D146 lesson (PR #769): an if/early-return followed by POSIX-only code
    # makes mypy flag that code unreachable on a win32 target (this repo's
    # `warn_unreachable = true`) -- an if/else keeps both platform branches
    # correctly scoped instead.
    if sys.platform == "win32":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=_NO_WINDOW,
        )
        return str(pid) in (result.stdout or "")
    else:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True


def terminate_tree(pid: int) -> None:
    """Best-effort, forceful termination of ``pid`` and its live descendants."""
    if pid <= 0:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            check=False,
            creationflags=_NO_WINDOW,
        )
    else:
        try:
            os.killpg(os.getpgid(pid), 9)  # SIGKILL
        except OSError:
            try:
                os.kill(pid, 9)
            except OSError:
                pass


def win32_process_info(pid: int) -> dict[str, object] | None:
    """One process's ProcessId/ParentProcessId/ExecutablePath (Windows-only)."""
    if sys.platform != "win32" or pid <= 0:
        return None
    ps = (
        f"$p = Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\" | "
        "Select-Object ProcessId,ParentProcessId,ExecutablePath; "
        "if ($p) { $p | ConvertTo-Json -Compress } else { 'null' }"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
        check=False,
        creationflags=_NO_WINDOW,
    )
    try:
        data = json.loads(result.stdout.strip() or "null")
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


@dataclass
class CleanupProof:
    """PRE/POST process-count evidence for one probe's scratch spawns."""

    marker: str
    pre_count: int
    post_count: int
    leaked_pids: tuple[int, ...]

    @property
    def leak_free(self) -> bool:
        return self.post_count == 0 and not self.leaked_pids

    def as_evidence(self) -> dict[str, object]:
        return {
            "cleanup_marker": self.marker,
            "pre_spawn_count": self.pre_count,
            "post_cleanup_count": self.post_count,
            "leak_free": self.leak_free,
        }


def with_cleanup_proof(marker: str, spawned_pids: list[int]) -> CleanupProof:
    """Terminate every pid in ``spawned_pids`` (tree-kill), then verify via a
    fresh process enumeration -- not by trusting the termination call's own
    exit status. Post-termination removal from the process table is not
    instantaneous relative to taskkill returning, so the post-snapshot is
    polled briefly (bounded) rather than taken once immediately -- this
    absorbs normal OS latency without masking a genuine leak, which would
    still be present at every poll."""
    pre = snapshot_pids(marker)
    for pid in spawned_pids:
        terminate_tree(pid)
    post = snapshot_pids(marker)
    for _ in range(4):
        if not post:
            break
        time.sleep(0.4)
        post = snapshot_pids(marker)
    return CleanupProof(
        marker=marker, pre_count=len(pre), post_count=len(post), leaked_pids=tuple(sorted(post))
    )
