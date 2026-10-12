"""Running the supervisor as a durable service.

Uses the mechanism this repository already has for a long-lived governor host:
a detached process group, stdout to a log file beside the state, a stop file
for graceful shutdown, and a recorded process identity that survives PID reuse
(``sdk.host``). Installation and activation are explicit -- nothing here
enables a system service on your behalf.

`SERVICE_INSTALLED != SERVICE_RUNNING != PROGRAM_AUTHORIZED`. Installing writes
a launcher. Starting runs a supervisor. Neither grants the program anything the
approved program file did not already say.

WHO OWNS THE RECORDED PID. The launcher never writes the service's identity.
The service writes its own, on startup, from inside its own process. A launcher
that records the PID it spawned is recording the launcher's child, which on
some platforms is a shim that exits immediately -- and a supervisor identity
that names a dead shim makes "is it still running?" unanswerable. This is a
defect class this repository has hit before (PR #766), so the identity file
here is written by the process it describes and by nothing else.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from project_atlas.orchestration.program.enrollment import AgentStatus, load_registry
from project_atlas.orchestration.program.loader import LoadedProgram, load_program
from project_atlas.orchestration.program.models import ProgramError, ProgramStopReason
from project_atlas.orchestration.program.store import (
    _write_atomic,
    append_event,
    load_state,
    state_dir,
)
from project_atlas.orchestration.program.supervisor import ProgramSupervisor
from project_atlas.orchestration.sdk.host import (
    clear_supervisor_stop,
    pid_is_alive,
    process_start_identity,
    request_supervisor_stop,
    stop_requested,
)

SERVICE_DIR: Final[str] = "service"
IDENTITY_NAME: Final[str] = "service.json"
LOG_NAME: Final[str] = "service.log"

#: Stop reasons a service must not spin on. Each is either finished or waiting
#: on a person; retrying immediately would burn launches and change nothing.
TERMINAL_FOR_SERVICE: Final[frozenset[ProgramStopReason]] = frozenset(
    {
        ProgramStopReason.PROGRAM_COMPLETE,
        ProgramStopReason.OWNER_DECISION_REQUIRED,
        ProgramStopReason.RECONCILE_REQUIRED,
        ProgramStopReason.CANCELLED,
        ProgramStopReason.LIMIT_REACHED,
        ProgramStopReason.HARD_BLOCKER,
        ProgramStopReason.AWAITING_INDEPENDENT_VERIFICATION,
    }
)

#: Stop reasons a service resumes from after a pause. These mean "there was
#: nothing to do just then", which the next external event or settle can change.
RESUMABLE: Final[frozenset[ProgramStopReason]] = frozenset(
    {
        ProgramStopReason.NO_ELIGIBLE_WORK,
        ProgramStopReason.WAITING_ON_EXTERNAL_EVENT,
        ProgramStopReason.CYCLE_BUDGET_REACHED,
        # A paused program is waiting for an operator to unpause it, which is
        # a thing that happens while the service is still there. Exiting on
        # pause would make "resume" mean "start the service again", which is
        # not a pause.
        ProgramStopReason.PAUSED,
    }
)


class ServiceError(ProgramError):
    code = "PROGRAM_SERVICE_ERROR"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def service_dir(root: Path) -> Path:
    return state_dir(root) / SERVICE_DIR


def identity_path(root: Path) -> Path:
    return service_dir(root) / IDENTITY_NAME


def log_path(root: Path) -> Path:
    return service_dir(root) / LOG_NAME


def _bound_agents(loaded: LoadedProgram, registry_root: Path) -> tuple[Any, ...]:
    """Resolve every program role to its active, assigned registry record."""
    registry = load_registry(registry_root)
    program_path = str(loaded.source_path.expanduser().resolve())
    bound: list[Any] = []
    for role in loaded.profiles.profiles:
        candidates = [
            agent
            for agent in registry.agents.values()
            if agent.role == role
            and agent.status is AgentStatus.ACTIVE
            and agent.assigned_program == program_path
        ]
        if not candidates:
            raise ServiceError(
                f"no ACTIVE registry assignment for role {role!r} and program {program_path}",
                code="REGISTRY_BINDING_MISSING",
            )
        bound.extend(candidates)
    return tuple(bound)


@dataclass(frozen=True)
class ServiceIdentity:
    pid: int
    process_start_identity: str
    program_id: str
    program_path: str
    started_at: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "process_start_identity": self.process_start_identity,
            "program_id": self.program_id,
            "program_path": self.program_path,
            "started_at": self.started_at,
        }


def write_own_identity(root: Path, loaded: LoadedProgram) -> ServiceIdentity:
    """Called by the service process itself, from inside that process."""
    pid = os.getpid()
    identity = ServiceIdentity(
        pid=pid,
        process_start_identity=process_start_identity(pid),
        program_id=loaded.program.program_id,
        program_path=str(loaded.source_path),
        started_at=_utc_now(),
    )
    _write_atomic(
        identity_path(root),
        json.dumps(identity.to_public_dict(), indent=2, sort_keys=True) + "\n",
    )
    return identity


def read_identity(root: Path) -> ServiceIdentity | None:
    path = identity_path(root)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return ServiceIdentity(
            pid=int(raw["pid"]),
            process_start_identity=str(raw.get("process_start_identity", "unknown")),
            program_id=str(raw["program_id"]),
            program_path=str(raw["program_path"]),
            started_at=str(raw.get("started_at", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def service_is_alive(identity: ServiceIdentity | None) -> bool:
    """Is the recorded service still the process that recorded itself?

    A live PID is not enough: PIDs are reused, and a stranger's process
    answering to "is the supervisor running" is how an operator concludes their
    program is progressing when nothing is.
    """
    if identity is None or identity.pid <= 0:
        return False
    if not pid_is_alive(identity.pid):
        return False
    recorded = identity.process_start_identity
    if not recorded or recorded == "unknown":
        return False
    live = process_start_identity(identity.pid)
    if not live or live == "unknown":
        return False
    return live == recorded


def install(
    root: Path,
    program_path: Path,
    *,
    python: str | None = None,
    registry_root: Path | None = None,
) -> dict[str, Any]:
    """Write a launcher for this program. Activates nothing.

    Returns the launcher path plus a systemd user unit as *text*, not as an
    installed unit. Enabling a service is an outward-facing change to the
    operator's machine, and this package prints what to run rather than
    running it.
    """
    loaded = load_program(program_path)
    directory = service_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    interpreter = python or sys.executable
    program_arg = str(program_path.expanduser().resolve())
    root_arg = str(root.expanduser().resolve())
    registry_arg = (
        str(registry_root.expanduser().resolve()) if registry_root is not None else None
    )
    registry_flag = f"  --registry {registry_arg!r} \\\n" if registry_arg else ""

    script = directory / f"run-{loaded.program.program_id}.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "# Durable Atlas program supervisor. Does not merge, does not grant\n"
        "# any owner gate, and runs exactly the approved program named below.\n"
        "set -euo pipefail\n"
        f'exec {interpreter!r} -m project_atlas.orchestration.program.cli \\\n'
        f'  program service run --program {program_arg!r} \\\n'
        f'  --state-root {root_arg!r} \\\n'
        f'{registry_flag}'
        '  "$@"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)

    unit = (
        "[Unit]\n"
        f"Description=Atlas program supervisor ({loaded.program.program_id})\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={script}\n"
        f"ExecStop={interpreter} -m project_atlas.orchestration.program.cli "
        f"program service stop --program {program_arg} --state-root {root_arg}\n"
        "Restart=on-failure\n"
        "RestartSec=30\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    unit_file = directory / f"atlas-program-{loaded.program.program_id}.service"
    unit_file.write_text(unit, encoding="utf-8")

    return {
        "program_id": loaded.program.program_id,
        "launcher": str(script),
        "systemd_unit_file": str(unit_file),
        "installed": True,
        "activated": False,
        "activation_note": (
            "nothing was activated. To run it under systemd as your own user: "
            f"cp {unit_file} ~/.config/systemd/user/ && systemctl --user "
            f"daemon-reload && systemctl --user enable --now "
            f"atlas-program-{loaded.program.program_id}.service"
        ),
        "foreground_note": (
            f"to run it in the foreground instead: {script}"
        ),
        "truth_boundary": (
            "SERVICE_INSTALLED != SERVICE_RUNNING != PROGRAM_AUTHORIZED"
        ),
    }


def start(
    root: Path,
    program_path: Path,
    *,
    python: str | None = None,
    registry_root: Path | None = None,
    allow_unregistered: bool = False,
) -> dict[str, Any]:
    """Detach a service process for this program, if one is not already live."""
    loaded = load_program(program_path)
    existing = read_identity(root)
    if service_is_alive(existing):
        assert existing is not None
        raise ServiceError(
            f"a service for {existing.program_id} is already running "
            f"(pid {existing.pid})",
            code="SERVICE_ALREADY_RUNNING",
        )
    if registry_root is None and not allow_unregistered:
        raise ServiceError(
            "detached service start requires --registry; pass allow_unregistered "
            "only for the explicitly supported unregistered mode",
            code="REGISTRY_REQUIRED",
        )
    if registry_root is not None:
        # Validate the durable binding before creating a child process.  A
        # launcher must never start an enrolled program and discover a missing
        # or unreadable registry only after detaching.
        _bound_agents(loaded, registry_root)
    directory = service_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    clear_supervisor_stop(state_dir(root))

    interpreter = python or sys.executable
    argv = [
        interpreter,
        "-m",
        "project_atlas.orchestration.program.cli",
        "program",
        "service",
        "run",
        "--program",
        str(program_path.expanduser().resolve()),
        "--state-root",
        str(root.expanduser().resolve()),
    ]
    if registry_root is not None:
        argv += ["--registry", str(registry_root.expanduser().resolve())]
    elif allow_unregistered:
        argv += ["--allow-unregistered"]
    creationflags = 0
    if os.name == "nt":  # pragma: no cover - Windows
        creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(
            getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    handle = log_path(root).open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            argv,
            cwd=str(loaded.workspace),
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            start_new_session=(os.name != "nt"),
        )
    finally:
        handle.close()

    # Wait for the SERVICE to write its own identity. The PID above belongs to
    # what this call spawned, which is not necessarily the process that ends up
    # supervising -- and recording a launcher's child as the supervisor is
    # exactly the defect this repository hit in PR #766.
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        identity = read_identity(root)
        if (
            identity is not None
            and identity.pid != existing_pid(existing)
            and service_is_alive(identity)
        ):
            return {
                "program_id": loaded.program.program_id,
                "service": identity.to_public_dict(),
                "log": str(log_path(root)),
                "spawned_pid": process.pid,
                "identity_written_by": "the service process itself",
                "note": (
                    "the recorded pid is the one the service wrote from "
                    "inside its own process, not the pid this call spawned"
                ),
            }
        if process.poll() is not None:
            break
        time.sleep(0.25)
    raise ServiceError(
        "the service did not record its own identity within 30s; check "
        f"{log_path(root)}",
        code="SERVICE_DID_NOT_START",
    )


def existing_pid(identity: ServiceIdentity | None) -> int:
    return identity.pid if identity is not None else -1


def stop(root: Path) -> dict[str, Any]:
    """Ask a running service to stop before its next launch."""
    request_supervisor_stop(state_dir(root))
    identity = read_identity(root)
    append_event(root, "SERVICE_STOP_REQUESTED", {"pid": existing_pid(identity)})
    return {
        "stop_requested": True,
        "service": identity.to_public_dict() if identity else None,
        "service_alive": service_is_alive(identity),
        "note": (
            "the service stops before starting anything further and terminates "
            "any worker it is currently running; whatever such a worker had "
            "already done is recorded as UNCERTAIN, never assumed either way"
        ),
    }


def status(
    root: Path,
    program_path: Path,
    *,
    registry_root: Path | None = None,
) -> dict[str, Any]:
    loaded = load_program(program_path)
    identity = read_identity(root)
    enrolled_agents = (
        _bound_agents(loaded, registry_root) if registry_root is not None else ()
    )
    supervisor = ProgramSupervisor(
        loaded,
        state_root=root,
        enrolled_agents=enrolled_agents,
        registry_root=registry_root,
    )
    state = load_state(root)
    return {
        "program_id": loaded.program.program_id,
        "service": identity.to_public_dict() if identity else None,
        "service_alive": service_is_alive(identity),
        "stop_requested": stop_requested(state_dir(root)),
        "log": str(log_path(root)) if log_path(root).is_file() else None,
        "program_status": supervisor.status(),
        "last_stop_reason": (
            state.last_stop_reason.value
            if state is not None and state.last_stop_reason
            else None
        ),
        "truth_boundary": (
            "SERVICE_INSTALLED != SERVICE_RUNNING != PROGRAM_AUTHORIZED"
        ),
    }


def run(
    root: Path,
    program_path: Path,
    *,
    poll_seconds: float = 30.0,
    max_rounds: int | None = None,
    registry_root: Path | None = None,
    allow_unregistered: bool = True,
    sleeper: Any = time.sleep,
) -> dict[str, Any]:
    """The service body: supervise until finished, blocked, or asked to stop.

    Each round is a full ``supervisor.start()``, which itself runs many cycles.
    Between rounds the service pauses rather than spinning: a round that ended
    with nothing eligible ended because an external event had not landed yet,
    and hammering the same probe changes nothing.

    RECOVERY IS NOT REPLAY. A round that follows an interrupted one begins with
    the same restart reconciliation any start does -- interrupted attempts are
    classified from evidence, resumed where the runtime supports it, and
    otherwise left for a person. The service never redispatches a task because
    a previous round ended untidily.
    """
    loaded = load_program(program_path)
    identity = write_own_identity(root, loaded)
    append_event(
        root,
        "SERVICE_STARTED",
        {"pid": identity.pid, "program_id": loaded.program.program_id},
    )
    rounds: list[dict[str, Any]] = []
    try:
        while max_rounds is None or len(rounds) < max_rounds:
            if stop_requested(state_dir(root)):
                rounds.append({"round": len(rounds) + 1, "stop_reason": "STOP_REQUESTED"})
                break
            if registry_root is None and not allow_unregistered:
                raise ServiceError(
                    "detached service run requires a registry binding",
                    code="REGISTRY_REQUIRED",
                )
            enrolled_agents = (
                _bound_agents(loaded, registry_root)
                if registry_root is not None
                else ()
            )
            supervisor = ProgramSupervisor(
                loaded,
                state_root=root,
                enrolled_agents=enrolled_agents,
                registry_root=registry_root,
            )
            report = supervisor.start()
            rounds.append(
                {
                    "round": len(rounds) + 1,
                    "stop_reason": report.stop_reason.value,
                    "launches_this_run": report.launches_this_run,
                    "program_complete": report.complete,
                }
            )
            if report.stop_reason in TERMINAL_FOR_SERVICE:
                break
            if report.stop_reason not in RESUMABLE:
                # An unrecognised reason is not something to loop on. Stopping
                # and saying which one it was beats spinning on a state this
                # code does not understand.
                break
            sleeper(poll_seconds)
    finally:
        append_event(
            root,
            "SERVICE_EXITED",
            {"pid": identity.pid, "rounds": len(rounds)},
        )
    final = rounds[-1] if rounds else {"stop_reason": "NO_ROUNDS"}
    return {
        "program_id": loaded.program.program_id,
        "service": identity.to_public_dict(),
        "rounds": rounds,
        "final_stop_reason": final.get("stop_reason"),
        "program_complete": bool(final.get("program_complete", False)),
        "merge_authorized": False,
    }
