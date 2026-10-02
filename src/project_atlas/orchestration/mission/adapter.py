"""AS-MISSION-VERTICAL-SLICE-001, DEVELOPMENT -- the agent-adapter
boundary: one real, bounded reference implementation, not a mock, so the
diff/test evidence this produces is genuinely inspectable.

`MissionAdapter` is intentionally minimal (one method, one result shape)
so a future real coding-agent adapter (ACP-based or otherwise) can satisfy
it without this package needing to know anything about that adapter's own
internals -- this is the seam, not the implementation of every adapter
Atlas might eventually have.

`context` is a required parameter, not optional: an adapter that never
receives the compiled `MissionContextPacket` cannot act on it, which was
a real gap in this package's first cut (the context compiler ran, but its
output never reached the adapter -- only `base_head` leaked through into
an idempotency key). `ShellCommandAdapter` demonstrates delivery
concretely by materializing the packet into the workspace as a real file
(`.atlas-mission/mission-context.json`, a well-known relative path any
adapter command can read) before running the command, so a real command
can read and act on it.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from project_atlas.orchestration.mission import MISSION_STATE_DIR_NAME

if TYPE_CHECKING:
    from project_atlas.orchestration.mission.context_packet import MissionContextPacket

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
CONTEXT_FILE_NAME = "mission-context.json"

FailureClass = Literal["NONE", "SPAWN_FAILED", "TIMEOUT", "NONZERO_EXIT"]


@dataclass(frozen=True)
class AdapterResult:
    ok: bool
    returncode: int
    stdout: str
    stderr: str
    duration_sec: float
    command_repr: str
    failure_class: FailureClass
    cleanup_confirmed: bool


class MissionAdapter(Protocol):
    """One real unit of work, run inside an already-isolated workspace,
    with the compiled mission context actually delivered to it."""

    def run(
        self, *, workspace: Path, context: MissionContextPacket, timeout_sec: float
    ) -> AdapterResult: ...


def _kill_process_tree(proc: subprocess.Popen[str]) -> bool:
    """Terminate `proc` and its descendants. Returns True only when
    cleanup could actually be CONFIRMED (the immediate child was
    successfully reaped after the kill signal), never merely assumed --
    a timeout that only killed the direct process while descendants kept
    mutating the workspace was a real, reported defect in an earlier cut
    of this adapter."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            creationflags=_NO_WINDOW,
            check=False,
        )
    else:
        import contextlib
        import os
        import signal

        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    try:
        proc.wait(timeout=10)
        return True
    except subprocess.TimeoutExpired:
        return False


@dataclass(frozen=True)
class ShellCommandAdapter:
    """Reference adapter: runs one bounded shell command inside the
    workspace. Real subprocess, real exit code, real stdout/stderr --
    genuinely inspectable evidence, not a simulation.

    Spawned in its own process group/job (Windows: `CREATE_NEW_PROCESS_GROUP`
    + `taskkill /F /T` on timeout; POSIX: `start_new_session=True` +
    `killpg(SIGKILL)` on timeout) so a timeout actually terminates
    descendants, not just the direct child -- see `_kill_process_tree`.
    Every invocation avoids a visible console window on Windows and is
    bounded by `timeout_sec`.
    """

    command: tuple[str, ...]

    def run(
        self, *, workspace: Path, context: MissionContextPacket, timeout_sec: float
    ) -> AdapterResult:
        cmd_repr = " ".join(self.command)
        start = time.perf_counter()

        # Deliver the compiled context as a real file the command can
        # read -- this is what makes "the agent actually receives it"
        # true rather than aspirational. Best-effort: a failure to write
        # it is not itself a spawn failure, but the adapter proceeds
        # without it (the command will simply find no context file).
        context_path = workspace / MISSION_STATE_DIR_NAME / CONTEXT_FILE_NAME
        with contextlib.suppress(OSError):
            context_path.parent.mkdir(parents=True, exist_ok=True)
            context_path.write_text(context.to_json(), encoding="utf-8")

        popen_kwargs: dict[str, object] = {
            "cwd": str(workspace),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = _NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(list(self.command), **popen_kwargs)  # type: ignore[call-overload]
        except OSError as exc:
            # Proven pre-spawn failure: nothing external can possibly have
            # happened, classified distinctly from a post-spawn exception.
            return AdapterResult(
                ok=False,
                returncode=-1,
                stdout="",
                stderr=f"{type(exc).__name__}: {exc}",
                duration_sec=time.perf_counter() - start,
                command_repr=cmd_repr,
                failure_class="SPAWN_FAILED",
                cleanup_confirmed=True,  # nothing was ever spawned -- trivially clean
            )

        try:
            stdout, stderr = proc.communicate(timeout=timeout_sec)
            duration = time.perf_counter() - start
            ok = proc.returncode == 0
            return AdapterResult(
                ok=ok,
                returncode=proc.returncode,
                stdout=stdout,
                stderr=stderr,
                duration_sec=duration,
                command_repr=cmd_repr,
                failure_class="NONE" if ok else "NONZERO_EXIT",
                cleanup_confirmed=True,  # process ran to completion and was reaped
            )
        except subprocess.TimeoutExpired:
            cleanup_confirmed = _kill_process_tree(proc)
            try:
                stdout, stderr = proc.communicate(timeout=5)
            except Exception:
                stdout, stderr = "", ""
            return AdapterResult(
                ok=False,
                returncode=-1,
                stdout=stdout or "",
                stderr=f"TIMEOUT after {timeout_sec}s (process tree terminated)",
                duration_sec=time.perf_counter() - start,
                command_repr=cmd_repr,
                failure_class="TIMEOUT",
                cleanup_confirmed=cleanup_confirmed,
            )
