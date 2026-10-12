"""AS-ORCH-SELF-WAKE-RESIDENT-DRIVER-001 — resident status (observability only).

D-131: stale status must not masquerade as a live governor.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from project_atlas.orchestration.sdk.host import pid_is_alive, process_start_identity
from project_atlas.orchestration.sdk.models import STATE_DIR_RELATIVE

STATUS_NAME: Final[str] = "resident-status.json"
PACKAGE_ID: Final[str] = "AS-ORCH-SELF-WAKE-RESIDENT-DRIVER-001"
STALE_HEARTBEAT_SEC: Final[float] = 30.0


class ResidentStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    package_id: Literal["AS-ORCH-SELF-WAKE-RESIDENT-DRIVER-001"] = PACKAGE_ID  # type: ignore[assignment]
    GOVERNOR_PID: int = 0
    #: `host.process_start_identity(GOVERNOR_PID)`, snapshotted at the same
    #: moment `GOVERNOR_PID` was set. Absent on status files written before
    #: this field existed (defaults to "", handled the same as "unknown").
    #: Lets `status_claims_live` disprove a PID-reuse false positive instead
    #: of trusting `pid_is_alive(GOVERNOR_PID)` alone -- see AS-WIN-RESIDENT-
    #: LOCK-PID-REUSE. This is deliberately NOT `process_start_time` above,
    #: which is this process's own `time.time()` self-report and is never
    #: cross-checked against the OS, so it cannot disprove anything.
    GOVERNOR_PROCESS_START_IDENTITY: str = ""
    SERVICE_INSTANCE_ID: str = ""
    STARTED_AT: float = 0.0
    process_start_time: float = 0.0
    status_written_at: float = 0.0
    heartbeat_sequence: int = Field(default=0, ge=0)
    scheduler_tick_sequence: int = Field(default=0, ge=0)
    progress_sequence: int = Field(default=0, ge=0)
    LAST_SCHEDULER_TICK: float = 0.0
    LAST_PROGRESS_AT: float = 0.0
    NEXT_WAKE_AT: float | None = None
    READY_NODE_COUNT: int = 0
    ACTIVE_WORKER_COUNT: int = 0
    PENDING_EXTERNAL_EVENT_COUNT: int = 0
    OWNER_HELD_COUNT: int = 0
    LAST_EVENT_CONSUMED: str | None = None
    LAST_NODE_DISPATCHED: str | None = None
    DETACHED_SCHEDULER_TICK_COUNT: int = 0
    MANUAL_CONTINUE_COUNT: int = 0
    LOST_EVENT_COUNT: int = 0
    DUPLICATE_DISPATCH_COUNT: int = 0
    ACTIVE_PRIMARY_GOVERNOR_COUNT: int = 0
    WATCHDOG_PID: int = 0
    GLOBAL_OWNER_REQUIRED: Literal["YES", "NO"] = "NO"
    RESIDENT_GOVERNOR: Literal["YES", "NO"] = "NO"
    SESSION_BOUND_GOVERNOR: Literal["NO"] = "NO"
    SELF_WAKE_DRIVER: Literal["ACTIVE", "STOPPED"] = "STOPPED"
    EXTERNAL_TRIGGER_REQUIRED_FOR_NEXT_SCHEDULER_TICK: Literal["NO"] = "NO"
    CURSOR_API_KEY_PRESENT: Literal["YES", "NO"] = "NO"
    AUTHENTICATION_WORKS: Literal["YES", "NO", "UNKNOWN"] = "UNKNOWN"
    SECRET_LEAK_COUNT: int = 0
    CASE: Literal["A", "B", "C", "D", "E", "F", "G", "UNKNOWN"] = "UNKNOWN"
    merge_authorized: Literal[False] = False


def status_path(root: Path) -> Path:
    return root / STATE_DIR_RELATIVE / STATUS_NAME


def load_status(root: Path) -> ResidentStatus:
    path = status_path(root)
    if not path.is_file():
        return ResidentStatus(STARTED_AT=time.time())
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ResidentStatus(STARTED_AT=time.time())
    if not isinstance(data, dict):
        return ResidentStatus(STARTED_AT=time.time())
    data["merge_authorized"] = False
    # Tolerate schema v1 files during upgrade.
    if data.get("schema_version") == 1:
        data["schema_version"] = 2
    return ResidentStatus.model_validate(data)


def persist_status(root: Path, status: ResidentStatus) -> ResidentStatus:
    now = time.time()
    payload = status.model_copy(
        update={"merge_authorized": False, "status_written_at": now}
    )
    path = status_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return payload


def status_claims_live(status: ResidentStatus, *, now: float | None = None) -> bool:
    """True only if PID exists AND heartbeat is fresh. Stale file = not live.

    "PID exists" means: a live process with that pid, AND -- when a recorded
    identity is available to check it against -- that live process is
    positively the same one that last wrote this status, not a different
    process that happened to reuse the pid within the heartbeat window.
    A recorded identity absent (older status file) or unresolvable right
    now falls back to the pid-alive-only check this always did; only a
    positive disagreement counts as reuse. See AS-WIN-RESIDENT-LOCK-PID-REUSE.
    """
    ts = time.time() if now is None else now
    if status.GOVERNOR_PID <= 0 or not pid_is_alive(status.GOVERNOR_PID):
        return False
    recorded = status.GOVERNOR_PROCESS_START_IDENTITY
    if recorded not in {"", "unknown"}:
        live = process_start_identity(status.GOVERNOR_PID)
        if live not in {"", "unknown"} and live != recorded:
            return False
    written = status.status_written_at or status.LAST_SCHEDULER_TICK
    if written <= 0:
        return False
    return (ts - written) <= STALE_HEARTBEAT_SEC


def classify_runtime_case(
    *,
    process_exists: bool,
    ticks_advance: bool,
    ready_count: int,
    useful_dispatch: bool,
    watchdog_ok: bool,
    state_corrupt: bool = False,
) -> Literal["A", "B", "C", "D", "E", "F", "G"]:
    if state_corrupt:
        return "G"
    if not process_exists:
        return "B" if watchdog_ok else "F"
    if not ticks_advance:
        return "C"
    if ready_count > 0 and not useful_dispatch:
        return "D"
    if ready_count == 0:
        return "E" if ticks_advance else "C"
    return "A"
