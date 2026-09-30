"""Detached Windows/Linux supervisor host. No secrets, no elevation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from project_atlas.orchestration.sdk.models import STATE_DIR_RELATIVE, SdkRuntimeError

SUPERVISOR_STOP_NAME = "supervisor.stop"
SUPERVISOR_LOCK_NAME = "supervisor.lock"
_LOCK_ACQUIRE_ATTEMPTS = 8
_HELD_LOCKS_GUARD = threading.Lock()
# root-key → instance_id for owners that acquired without an explicit release token
_HELD_INSTANCE_IDS: dict[str, str] = {}


#: Upper bound for the short Windows process probes below (``tasklist``,
#: ``powershell``). They answer a question about a PID, so they either respond
#: quickly or something is wrong; without a bound a wedged child would hang the
#: caller indefinitely, and these run from a resident driver's polling loop.
_WINDOWS_PROBE_TIMEOUT_SECONDS = 30


def no_window_creationflags() -> int:
    """Windows-only ``creationflags`` for a subprocess call that must
    never show a console window, no-op (``0``) everywhere else.

    Real, user-reported incident (recurring across this session): a
    resident/detached driver process spawned with no console of its own
    (``DETACHED_PROCESS``, see ``resident_windows.py``) still shells out
    to console-subsystem executables (``gh``, ``tasklist``,
    ``powershell``) via plain ``subprocess.run()``/``Popen()``. Windows'
    default behavior for a console-subsystem child whose parent has no
    console of its own is to allocate a BRAND NEW, visible console
    window for it -- so every one of those calls popped a real terminal
    window in front of the user, each one showing that one command's own
    output (e.g. a ``gh run view``/``gh api`` failure against whatever
    git remote happened to resolve in a throwaway ``tmp_path`` test
    fixture, unrelated to any repository the user actually has open).
    PR #669 already fixed the SEPARATE, more severe problem of that
    detached process never being cleaned up (a real PID-scoped leak);
    this closes the narrower-but-still-real problem of the window itself
    ever appearing in the first place, for calls that run and exit
    quickly. ``subprocess.CREATE_NO_WINDOW`` tells Windows never to
    allocate one, regardless of the parent's own console state.
    """
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def host_state_dir(root: Path) -> Path:
    return root / STATE_DIR_RELATIVE


def write_host_identity(
    root: Path,
    *,
    pid: int,
    backend: str,
    package_head: str,
    worktree: str,
) -> Path:
    store = host_state_dir(root)
    store.mkdir(parents=True, exist_ok=True)
    payload = {
        "supervisor_pid": pid,
        "supervisor_backend": backend,
        "supervisor_package_head": package_head,
        "supervisor_worktree": worktree,
        "supervisor_command": "atlas orchestrator governor-service-run",
        "merge_authorized": False,
        "execution_authorized": False,
    }
    target = store / "supervisor-host.json"
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (store / "supervisor.pid").write_text(f"{pid}\n", encoding="utf-8")
    return target


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            proc = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                check=False,
                creationflags=no_window_creationflags(),
                stdin=subprocess.DEVNULL,
                timeout=_WINDOWS_PROBE_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return str(pid) in (proc.stdout or "")
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def detach_governor_service(
    *,
    root: Path,
    python: str | None = None,
    extra_args: list[str] | None = None,
) -> int:
    """Start governor-service-run in a new process group and return its PID."""
    interpreter = python or sys.executable
    args = [
        interpreter,
        "-m",
        "project_atlas.cli",
        "orchestrator",
        "governor-service-run",
        "--root",
        str(root),
        *(extra_args or []),
    ]
    creationflags = 0
    if os.name == "nt":
        creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(
            getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    log_dir = host_state_dir(root)
    log_dir.mkdir(parents=True, exist_ok=True)
    log = (log_dir / "supervisor.stdout.log").open("a", encoding="utf-8")
    proc = subprocess.Popen(
        args,
        cwd=str(root),
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
        start_new_session=(os.name != "nt"),
    )
    return int(proc.pid)


def stop_requested(root: Path) -> bool:
    return (host_state_dir(root) / SUPERVISOR_STOP_NAME).is_file()


def _write_atomic_text(path: Path, content: str) -> None:
    """Replace ``path`` atomically via same-directory temp + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def request_supervisor_stop(root: Path) -> None:
    path = host_state_dir(root) / SUPERVISOR_STOP_NAME
    _write_atomic_text(path, "stop\n")


def clear_supervisor_stop(root: Path) -> None:
    path = host_state_dir(root) / SUPERVISOR_STOP_NAME
    if path.is_file():
        path.unlink()


def new_supervisor_instance_id() -> str:
    """Mint a supervisor instance token. Ownership is instance-scoped, not PID-only."""
    return uuid.uuid4().hex


def process_start_identity(pid: int) -> str:
    """Best-effort process start identity so PID reuse cannot inherit ownership."""
    if pid <= 0:
        return "unknown"
    if os.name == "nt":
        try:
            ps_cmd = (
                f"(Get-Process -Id {int(pid)} -ErrorAction Stop)"
                ".StartTime.ToUniversalTime().Ticks"
            )
            proc = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    ps_cmd,
                ],
                capture_output=True,
                text=True,
                check=False,
                creationflags=no_window_creationflags(),
                stdin=subprocess.DEVNULL,
                timeout=_WINDOWS_PROBE_TIMEOUT_SECONDS,
            )
            ticks = (proc.stdout or "").strip()
            if proc.returncode == 0 and ticks.isdigit():
                return f"win:{ticks}"
        except (OSError, subprocess.TimeoutExpired):
            pass
        return "unknown"
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        close = raw.rfind(")")
        if close < 0:
            return "unknown"
        fields = raw[close + 1 :].split()
        # Field 22 in /proc/<pid>/stat is starttime (index 19 after comm).
        return f"linux:{fields[19]}"
    except (OSError, IndexError, ValueError):
        return "unknown"


def _root_key(root: Path) -> str:
    try:
        return str(root.resolve())
    except OSError:
        return str(root)


def _remember_held_instance(root: Path, instance_id: str) -> None:
    with _HELD_LOCKS_GUARD:
        _HELD_INSTANCE_IDS[_root_key(root)] = instance_id


def _forget_held_instance(root: Path, instance_id: str | None = None) -> None:
    key = _root_key(root)
    with _HELD_LOCKS_GUARD:
        held = _HELD_INSTANCE_IDS.get(key)
        if held is None:
            return
        if instance_id is None or held == instance_id:
            _HELD_INSTANCE_IDS.pop(key, None)


def _lookup_held_instance(root: Path) -> str | None:
    with _HELD_LOCKS_GUARD:
        return _HELD_INSTANCE_IDS.get(_root_key(root))


@dataclass(frozen=True)
class SupervisorLockRecord:
    pid: int
    instance_id: str
    process_start_identity: str


def read_supervisor_lock_pid(root: Path) -> int:
    path = host_state_dir(root) / SUPERVISOR_LOCK_NAME
    record = _read_lock_record(path)
    if record is None or record == "corrupt":
        return 0
    if record.pid > 0 and pid_is_alive(record.pid):
        live_start = process_start_identity(record.pid)
        if (
            record.process_start_identity not in {"", "unknown"}
            and live_start not in {"", "unknown"}
            and live_start != record.process_start_identity
        ):
            return 0
        return record.pid
    return 0


def _lock_payload(pid: int, instance_id: str) -> bytes:
    payload = {
        "pid": pid,
        "instance_id": instance_id,
        "process_start_identity": process_start_identity(pid),
    }
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _read_lock_record(
    path: Path,
) -> SupervisorLockRecord | Literal["corrupt"] | None:
    """Return lock record, None when absent, 'corrupt' when unreadable/incomplete."""
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return "corrupt"
    if not raw.strip():
        return "corrupt"
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return "corrupt"
        pid = int(data.get("pid", 0))
        instance_id = data.get("instance_id")
        start = data.get("process_start_identity", "unknown")
        if not isinstance(instance_id, str) or not instance_id.strip():
            # Legacy/partial PID-only locks: treat as reclaimable only when dead.
            if pid > 0 and not pid_is_alive(pid):
                return SupervisorLockRecord(
                    pid=pid,
                    instance_id="",
                    process_start_identity="unknown",
                )
            return "corrupt"
        if not isinstance(start, str) or not start:
            start = "unknown"
        return SupervisorLockRecord(
            pid=pid,
            instance_id=instance_id.strip(),
            process_start_identity=start,
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return "corrupt"


def _live_foreign_owner(record: SupervisorLockRecord, me: int, my_id: str) -> bool:
    """True when an independent live supervisor still owns the lock."""
    if record.pid == me and record.instance_id == my_id:
        return False
    if record.pid > 0 and pid_is_alive(record.pid):
        live_start = process_start_identity(record.pid)
        if (
            record.process_start_identity not in {"", "unknown"}
            and live_start not in {"", "unknown"}
            and live_start != record.process_start_identity
        ):
            # PID reused by a new process — prior ownership must not be inherited.
            return False
    if record.pid == me and record.instance_id != my_id:
        # Same process, different supervisor instance — must not share ownership.
        return True
    if record.pid <= 0:
        return False
    return pid_is_alive(record.pid)


def acquire_supervisor_lock(root: Path, *, instance_id: str | None = None) -> bool:
    """Fail closed when another live supervisor instance already owns the host lock.

    Ownership is (pid + instance_id [+ process_start_identity]), not PID alone.
    Omitting ``instance_id`` mints a fresh instance token (independent contender).
    Same exact instance may re-enter idempotently when the same token is supplied
    or when this process still holds the remembered token for ``root``.
    """
    path = host_state_dir(root) / SUPERVISOR_LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    me = os.getpid()
    # Omitting instance_id always mints a fresh instance token so two contenders
    # in the same process cannot silently share PID-only ownership.
    my_id = instance_id if instance_id is not None else new_supervisor_instance_id()
    payload = _lock_payload(me, my_id)
    for _attempt in range(_LOCK_ACQUIRE_ATTEMPTS):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            record = _read_lock_record(path)
            if record is None:
                continue
            if record == "corrupt":
                return False
            assert isinstance(record, SupervisorLockRecord)
            if record.pid == me and record.instance_id == my_id:
                _remember_held_instance(root, my_id)
                return True
            if _live_foreign_owner(record, me, my_id):
                return False
            try:
                path.unlink(missing_ok=True)
            except OSError:
                return False
            continue
        except OSError:
            return False
        try:
            os.write(fd, payload)
            _remember_held_instance(root, my_id)
            return True
        finally:
            os.close(fd)
    return False


def release_supervisor_lock(root: Path, *, instance_id: str | None = None) -> None:
    """Release only when this exact supervisor instance owns the lock."""
    path = host_state_dir(root) / SUPERVISOR_LOCK_NAME
    if not path.is_file():
        _forget_held_instance(root, instance_id)
        return
    token = instance_id or _lookup_held_instance(root)
    me = os.getpid()
    record = _read_lock_record(path)
    if record is None or record == "corrupt":
        return
    assert isinstance(record, SupervisorLockRecord)
    if record.pid != me:
        return
    if token is None or record.instance_id != token:
        return
    try:
        path.unlink()
    except OSError:
        return
    _forget_held_instance(root, token)


def assert_single_supervisor_or_raise(
    root: Path, *, instance_id: str | None = None
) -> str:
    """Acquire the singleton lock or raise SERVICE_DOUBLE_START. Returns instance id."""
    token = instance_id or new_supervisor_instance_id()
    if not acquire_supervisor_lock(root, instance_id=token):
        raise SdkRuntimeError(
            "another live supervisor already owns this host",
            code="SERVICE_DOUBLE_START",
        )
    return token
