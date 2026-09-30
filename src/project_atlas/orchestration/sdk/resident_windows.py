"""Windows resident host — DETACHED_PROCESS + continuous watchdog.

Least privilege. No secrets on the command line.

Honesty boundary:
  WATCHDOG_SESSION_BOUND = NO for Cursor/terminal/launcher exit within a
  logged-on user session. Cold-boot without user logon is NOT claimed when
  schtasks registration is denied (operational prerequisite).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Final

from project_atlas.orchestration.sdk.host import (
    host_state_dir,
    write_host_identity,
)
from project_atlas.orchestration.sdk.models import STATE_DIR_RELATIVE
from project_atlas.orchestration.sdk.resident_driver import read_primary_lock_pid
from project_atlas.orchestration.sdk.resident_status import load_status, status_claims_live

TASK_NAME: Final[str] = "AtlasGovernorResident"
WRAPPER_NAME: Final[str] = "atlas-resident-driver.cmd"
WATCHDOG_PID_NAME: Final[str] = "resident-watchdog.pid"
WATCHDOG_INTERVAL_SEC: Final[float] = 20.0

#: D146 (native-Windows evidence, confirmed via Win32_Process ancestry): a
#: venv's ``Scripts\python.exe`` on Windows can be a launcher/trampoline
#: stub rather than a copy of the real interpreter -- confirmed on-host by
#: binary size/hash against the base interpreter this venv's ``pyvenv.cfg``
#: names, and by ``Win32_Process`` ancestry showing the launcher spawn the
#: real interpreter as its *child* (the child's ``ParentProcessId`` equals
#: the launcher's own PID). ``Popen.pid`` is then the launcher's PID, not
#: the PID that actually runs the resident loop and calls
#: ``acquire_primary_lock`` -- exactly the identity this function's callers
#: need. This poll budget is newly introduced here for this poll only --
#: no other caller in this module already depends on it;
#: ``ensure_resident_alive``'s own poll loop below uses a different budget
#: (12 * 0.5s = 6s) for a different purpose (waiting for status to claim
#: liveness, not for lock acquisition). 80 * 0.25s = 20s gives real headroom
#: for a slow subprocess spawn + full package import on slow-I/O hosts.
_RESIDENT_STARTUP_POLL_ATTEMPTS: Final[int] = 80
_RESIDENT_STARTUP_POLL_INTERVAL_SEC: Final[float] = 0.25


def _creationflags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(
        getattr(subprocess, "DETACHED_PROCESS", 0)
    )


def write_resident_wrapper(
    *,
    root: Path,
    package_src: Path,
    python: str | None = None,
) -> Path:
    """Write a .cmd launcher that sets PYTHONPATH and runs the resident loop."""
    interpreter = python or sys.executable
    runtime = root / STATE_DIR_RELATIVE
    runtime.mkdir(parents=True, exist_ok=True)
    wrapper = runtime / WRAPPER_NAME
    body = (
        "@echo off\r\n"
        "setlocal\r\n"
        f'set "PYTHONPATH={package_src};%PYTHONPATH%"\r\n'
        f'"{interpreter}" -m project_atlas.cli orchestrator governor-resident-run '
        f'--root "{root}" --detached-worker\r\n'
        "endlocal\r\n"
    )
    wrapper.write_text(body, encoding="utf-8")
    return wrapper


def detach_resident_driver(
    *,
    root: Path,
    package_src: Path,
    python: str | None = None,
) -> int:
    """Start resident loop in a new Windows process group.

    Returns the PID that actually holds the primary lock -- confirmed via
    ``read_primary_lock_pid`` -- never merely ``Popen.pid`` (D146: on a
    Windows venv whose interpreter is a launcher stub, ``Popen.pid`` names
    the launcher, a *parent* of the process that actually runs the resident
    loop and acquires the lock; the two are not interchangeable). Audited:
    the sole production caller (``ensure_resident_alive``) and the
    on-disk host-identity receipt this writes (read by no code in this
    repository today) both only need a confirmed identity or an honest
    absence of one -- neither needs the launcher's PID specifically.

    Fail-honest, not fail-silent: if the resident's own identity is not
    confirmed within the poll budget -- a genuine startup failure, or a
    launcher-host quirk this poll doesn't account for -- returns ``0``
    (this module's existing convention for "no confirmed PID", shared with
    ``read_primary_lock_pid``/``read_watchdog_pid``), never the launcher PID
    presented as if it were confirmed. A caller that needs "something to
    poll for regardless" must call ``read_primary_lock_pid(root)`` itself,
    exactly as ``ensure_resident_alive`` and the D146 regression tests do.
    """
    interpreter = python or sys.executable
    args = [
        interpreter,
        "-m",
        "project_atlas.cli",
        "orchestrator",
        "governor-resident-run",
        "--root",
        str(root),
        "--detached-worker",
    ]
    log_dir = host_state_dir(root)
    log_dir.mkdir(parents=True, exist_ok=True)
    log = (log_dir / "resident-driver.stdout.log").open("a", encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(package_src) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    # D146 (R1): the returned Popen handle is deliberately not consulted
    # after this point -- see the poll loop below for why.
    subprocess.Popen(
        args,
        cwd=str(root),
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=_creationflags(),
        start_new_session=(os.name != "nt"),
        env=env,
        close_fds=True,
    )
    resolved_pid = 0
    for _ in range(_RESIDENT_STARTUP_POLL_ATTEMPTS):
        holder = read_primary_lock_pid(root)
        if holder > 0:
            resolved_pid = holder
            break
        # D146 review (R1): the launched process object exiting is NOT
        # sufficient evidence the resident cannot still appear -- a
        # launcher/trampoline can legitimately exit *before* its
        # real-interpreter child finishes importing and acquires the
        # primary lock (detach-and-exit is a valid launcher pattern, not
        # only "launcher waits for child"; this repo's own host observed
        # the latter, but the poll must not assume every launcher does).
        # `proc` is intentionally not consulted here: the only thing that
        # establishes authoritative identity is the primary lock itself,
        # so the bounded wait watches only that, for its full budget,
        # regardless of the launched process object's own lifetime.
        time.sleep(_RESIDENT_STARTUP_POLL_INTERVAL_SEC)
    write_host_identity(
        root,
        pid=resolved_pid,
        backend="RESIDENT_SELF_WAKE",
        package_head="AS-ORCH-SELF-WAKE-RESIDENT-DRIVER-001",
        worktree=str(
            package_src.parent.parent if package_src.name == "src" else package_src
        ),
    )
    return resolved_pid


def run_watchdog_loop(
    *,
    root: Path,
    package_src: Path,
    python: str | None = None,
    interval_sec: float = WATCHDOG_INTERVAL_SEC,
) -> None:
    """Blocking watchdog loop (intended to run under DETACHED_PROCESS)."""
    write_watchdog_pid(root, os.getpid())
    while True:
        ensure_resident_alive(root=root, package_src=package_src, python=python)
        time.sleep(interval_sec)


def detach_continuous_watchdog(
    *,
    root: Path,
    package_src: Path,
    python: str | None = None,
    interval_sec: float = WATCHDOG_INTERVAL_SEC,
) -> int:
    """Detached watchdog: restart resident if status claims dead.

    Survives Cursor/terminal exit within the user session. Cold-boot only if
    schtasks/Startup also installed (may be ACCESS_DENIED).
    """
    interpreter = python or sys.executable
    code = (
        "from pathlib import Path; "
        "from project_atlas.orchestration.sdk.resident_windows import run_watchdog_loop; "
        f"run_watchdog_loop(root=Path(r'{root}'), package_src=Path(r'{package_src}'), "
        f"python=r'{interpreter}', interval_sec={interval_sec})"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(package_src) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    log_dir = host_state_dir(root)
    log_dir.mkdir(parents=True, exist_ok=True)
    log = (log_dir / "resident-watchdog.stdout.log").open("a", encoding="utf-8")
    proc = subprocess.Popen(
        [interpreter, "-c", code],
        cwd=str(root),
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=_creationflags(),
        start_new_session=(os.name != "nt"),
        env=env,
        close_fds=True,
    )
    write_watchdog_pid(root, int(proc.pid))
    return int(proc.pid)


def write_watchdog_pid(root: Path, pid: int) -> None:
    path = root / STATE_DIR_RELATIVE / WATCHDOG_PID_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n", encoding="utf-8")
    status = load_status(root)
    status.WATCHDOG_PID = pid
    from project_atlas.orchestration.sdk.resident_status import persist_status

    persist_status(root, status)


def read_watchdog_pid(root: Path) -> int:
    path = root / STATE_DIR_RELATIVE / WATCHDOG_PID_NAME
    if not path.is_file():
        return 0
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def ensure_resident_alive(
    *,
    root: Path,
    package_src: Path,
    python: str | None = None,
) -> dict[str, object]:
    """Restart resident if not live. Idempotent."""
    from project_atlas.orchestration.sdk.resident_driver import clear_stop
    from project_atlas.orchestration.sdk.resident_mission import persist_mission

    for _ in range(12):
        status = load_status(root)
        if status_claims_live(status):
            return {
                "action": "noop",
                "pid": status.GOVERNOR_PID,
                "live": True,
            }
        holder = read_primary_lock_pid(root)
        if holder > 0:
            time.sleep(0.5)
            continue
        break

    status = load_status(root)
    if status_claims_live(status):
        return {
            "action": "noop",
            "pid": status.GOVERNOR_PID,
            "live": True,
        }

    holder = read_primary_lock_pid(root)
    if holder > 0:
        return {
            "action": "noop",
            "pid": holder,
            "live": True,
        }

    persist_mission(root)
    clear_stop(root)
    pid = detach_resident_driver(root=root, package_src=package_src, python=python)
    # D146: `pid` is 0 when detach_resident_driver() could not confirm the
    # resident's own identity within its poll budget -- report that
    # honestly rather than always claiming "live".
    return {"action": "restarted", "pid": pid, "live": pid > 0}


def register_windows_logon_task(
    *,
    root: Path,
    package_src: Path,
    python: str | None = None,
    task_name: str = TASK_NAME,
) -> dict[str, object]:
    """Register a current-user logon task. May fail without elevation (document)."""
    if os.name != "nt":
        return {"registered": False, "reason": "not_windows"}
    wrapper = write_resident_wrapper(root=root, package_src=package_src, python=python)
    create = subprocess.run(
        [
            "schtasks",
            "/Create",
            "/TN",
            task_name,
            "/TR",
            str(wrapper),
            "/SC",
            "ONLOGON",
            "/RL",
            "LIMITED",
            "/F",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    receipt = {
        "registered": create.returncode == 0,
        "task_name": task_name,
        "wrapper": str(wrapper),
        "exit_code": create.returncode,
        "stdout_tail": (create.stdout or "")[-400:],
        "stderr_tail": (create.stderr or "")[-400:],
        "WATCHDOG_SESSION_BOUND": "NO_WITHIN_USER_LOGON",
        "COLD_BOOT_CLAIMED": create.returncode == 0,
        "merge_authorized": False,
    }
    path = root / STATE_DIR_RELATIVE / "resident-windows-task.json"
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def resident_pid_alive(root: Path) -> bool:
    return status_claims_live(load_status(root))
