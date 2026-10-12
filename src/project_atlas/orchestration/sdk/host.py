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
from typing import Any, Literal

from project_atlas.orchestration.sdk.models import STATE_DIR_RELATIVE, SdkRuntimeError

SUPERVISOR_STOP_NAME = "supervisor.stop"
SUPERVISOR_LOCK_NAME = "supervisor.lock"
_LOCK_ACQUIRE_ATTEMPTS = 8
_HELD_LOCKS_GUARD = threading.Lock()
# root-key → instance_id for owners that acquired without an explicit release token
_HELD_INSTANCE_IDS: dict[str, str] = {}


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
        fast = _win_pid_is_alive_fast(pid)
        if fast is not None:
            return fast
        proc = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=no_window_creationflags(),
        )
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


#: 100-ns ticks from 0001-01-01 (.NET `DateTime` epoch) to 1601-01-01
#: (Win32 `FILETIME` epoch) -- the exact constant .NET's own
#: `DateTime.ToFileTimeUtc`/`FromFileTimeUtc` use internally, so a value
#: produced by the fast ctypes path below and one produced by the
#: PowerShell fallback (`Get-Process ... .StartTime.ToUniversalTime().Ticks`,
#: a .NET `DateTime.Ticks`) are numerically identical for the same instant.
#: That equivalence is deliberate: a lock record written by one path must
#: still compare equal against a value read back through the other -- e.g.
#: across an Atlas upgrade that changes which path is taken while a
#: supervisor from the previous version is still running.
_FILETIME_TO_DOTNET_TICKS_OFFSET = 504_911_232_000_000_000

#: Win32 handles for the fast path, resolved exactly once at import time --
#: not per call. `ctypes.POINTER(_FILETIME)` mints a distinct ctypes pointer
#: *type* each time `_FILETIME` is (re)defined; setting `.argtypes` on the
#: shared `kernel32.GetProcessTimes` function object from a `_FILETIME`
#: class that gets locally redefined on every call is a genuine data race
#: under concurrent callers -- one thread's redefinition can overwrite
#: `.argtypes` out from under another thread mid-call, raising
#: `ctypes.ArgumentError` ("expected LP__FILETIME instance instead of
#: pointer to _FILETIME"). Reproduced directly under
#: `test_n_thread_concurrent_acquire_only_one_holder` during development.
#: A single module-level type and a single `argtypes` assignment, done once
#: before any thread exists, has no such race.
_win_kernel32: Any = None
_WinFILETIME: Any = None

if os.name == "nt":
    try:
        import ctypes as _ctypes
        from ctypes import wintypes as _wintypes

        class _WinFILETIME(_ctypes.Structure):  # type: ignore[no-redef]
            _fields_ = (
                ("dwLowDateTime", _wintypes.DWORD),
                ("dwHighDateTime", _wintypes.DWORD),
            )

        _win_kernel32 = _ctypes.windll.kernel32
        _win_kernel32.OpenProcess.restype = _wintypes.HANDLE
        _win_kernel32.OpenProcess.argtypes = (
            _wintypes.DWORD,
            _wintypes.BOOL,
            _wintypes.DWORD,
        )
        _win_kernel32.GetProcessTimes.argtypes = (
            _wintypes.HANDLE,
            _ctypes.POINTER(_WinFILETIME),
            _ctypes.POINTER(_WinFILETIME),
            _ctypes.POINTER(_WinFILETIME),
            _ctypes.POINTER(_WinFILETIME),
        )
        _win_kernel32.GetProcessTimes.restype = _wintypes.BOOL
        _win_kernel32.CloseHandle.argtypes = (_wintypes.HANDLE,)
        _win_kernel32.WaitForSingleObject.argtypes = (_wintypes.HANDLE, _wintypes.DWORD)
        _win_kernel32.WaitForSingleObject.restype = _wintypes.DWORD
    except (AttributeError, OSError, ImportError):
        _win_kernel32 = None
        _WinFILETIME = None

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0x0
_WAIT_TIMEOUT = 0x102


def _win_process_start_ticks_fast(pid: int) -> int | None:
    """Win32 ``GetProcessTimes`` via ``ctypes``. No subprocess, no shell.

    Returns ``None`` on anything short of a confirmed creation time --
    invalid pid, exited pid, access denied, ``ctypes.windll`` unavailable
    at import time (non-Windows, or a hardened environment) -- so the
    caller's existing PowerShell path remains the single source of truth
    for every case this fast path does not itself positively resolve.
    This function only ever narrows how a positive answer is obtained; it
    never changes what counts as one.
    """
    if _win_kernel32 is None:
        return None

    handle = _win_kernel32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
    )
    if not handle:
        # NULL: no such pid, or access denied (e.g. a protected/elevated
        # process). Either way this is not a confirmed answer -- fall back.
        return None
    try:
        creation = _WinFILETIME()
        exit_time = _WinFILETIME()
        kernel_time = _WinFILETIME()
        user_time = _WinFILETIME()
        ok = _win_kernel32.GetProcessTimes(
            handle,
            _ctypes.byref(creation),
            _ctypes.byref(exit_time),
            _ctypes.byref(kernel_time),
            _ctypes.byref(user_time),
        )
        if not ok:
            return None
        filetime_ticks = int(creation.dwHighDateTime) << 32 | int(creation.dwLowDateTime)
        if filetime_ticks <= 0:
            return None
        return filetime_ticks + _FILETIME_TO_DOTNET_TICKS_OFFSET
    finally:
        _win_kernel32.CloseHandle(handle)


def _win_pid_is_alive_fast(pid: int) -> bool | None:
    """Win32 ``WaitForSingleObject`` via ``ctypes``. No subprocess, no shell.

    Returns ``None`` -- decline, defer to the ``tasklist`` fallback -- for
    anything this cannot itself positively resolve, most importantly a
    process this call cannot open a handle to (e.g. access denied on a
    protected process that is nonetheless alive): ``tasklist`` can usually
    still see such a process by enumeration even where ``OpenProcess``
    cannot open it, and asserting ``False`` here for an access-denied
    process would be a false "not alive". A timed wait of 0ms, not
    ``GetExitCodeProcess``, is used deliberately: a real process's own exit
    code can legitimately equal ``STILL_ACTIVE`` (259), which would make
    that API ambiguous for this exact question.
    """
    if _win_kernel32 is None:
        return None
    handle = _win_kernel32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_SYNCHRONIZE, False, int(pid)
    )
    if not handle:
        return None
    try:
        wait_result = _win_kernel32.WaitForSingleObject(handle, 0)
        if wait_result == _WAIT_TIMEOUT:
            return True
        if wait_result == _WAIT_OBJECT_0:
            return False
        return None
    finally:
        _win_kernel32.CloseHandle(handle)


def process_start_identity(pid: int) -> str:
    """Best-effort process start identity so PID reuse cannot inherit ownership.

    Windows tries an in-process ``GetProcessTimes`` first (microseconds, no
    child process); ``Get-Process`` in PowerShell (roughly two seconds of
    ``powershell.exe`` startup per call, measured) is the fallback for
    whatever the fast path does not positively resolve -- an exited pid, a
    protected process this call cannot open, or ``ctypes`` itself being
    unavailable. The fallback's behavior, including every failure mode, is
    unchanged: this only adds a faster way to reach the same answer for the
    common case, never a different answer.
    """
    if pid <= 0:
        return "unknown"
    if os.name == "nt":
        fast_ticks = _win_process_start_ticks_fast(pid)
        if fast_ticks is not None:
            return f"win:{fast_ticks}"
        try:
            ps_cmd = (
                f"(Get-Process -Id {int(pid)} -ErrorAction Stop)"
                ".StartTime.ToUniversalTime().Ticks"
            )
            proc = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    ps_cmd,
                ],
                capture_output=True,
                text=True,
                check=False,
                creationflags=no_window_creationflags(),
            )
            ticks = (proc.stdout or "").strip()
            if proc.returncode == 0 and ticks.isdigit():
                return f"win:{ticks}"
        except OSError:
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
