"""Durable program state: checkpoints written before the effects they name.

Two artifacts, deliberately separate:

  ``state.json``   the reconciled current picture -- task states, attempt
                   counters, launch counts, the supervisor identity that owns
                   the program. Rewritten atomically, always readable.

  ``events.jsonl`` an append-only log of every transition, in order. A
                   checkpoint that says "the adapter was invoked" and is never
                   followed by a terminal record is exactly how an interrupted
                   run is detected -- so the log is appended and flushed
                   BEFORE the effect it precedes, never after.

Neither is authority. Both are evidence. A worker cannot write either.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from project_atlas.orchestration.autonomy.models import NodeState
from project_atlas.orchestration.program.models import (
    PACKAGE_ID,
    TRUTH_BOUNDARY,
    AttemptPhase,
    ExecutionConfidence,
    FailureClass,
    ProgramError,
    ProgramStopReason,
)

STATE_NAME: Final[str] = "state.json"
EVENTS_NAME: Final[str] = "events.jsonl"
EVIDENCE_DIR: Final[str] = "evidence"
#: Program state lives beside, never inside, the workspace the workers mutate,
#: so a worker's own diff can never contain the supervisor's checkpoint.
DEFAULT_STATE_RELATIVE: Final[Path] = Path(".atlas") / "orchestration" / "program"


class StoreError(ProgramError):
    code = "PROGRAM_STORE_ERROR"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class AttemptRecord(BaseModel):
    """One attempt at one task: the durable trace of a single dispatch."""

    model_config = ConfigDict(extra="forbid")

    attempt_id: str = Field(min_length=1, max_length=256)
    task_id: str = Field(min_length=1, max_length=128)
    attempt_number: int = Field(ge=1, le=10_000)
    idempotency_key: str = Field(min_length=1, max_length=256)
    profile_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    adapter: str = Field(min_length=1, max_length=64)
    #: Digest of the fully resolved effective profile. Two attempts with the
    #: same key but different digests are different work, and the key includes
    #: this so they can never be mistaken for a duplicate of one another.
    profile_digest: str = Field(min_length=64, max_length=64)
    base_pin: str = Field(min_length=40, max_length=40)
    lease_id: str | None = None
    phase: AttemptPhase = AttemptPhase.INTENT_RECORDED
    #: Runtime session identity, assigned by the supervisor BEFORE launch so an
    #: interrupted invocation is still addressable. ``None`` for adapters with
    #: no session concept.
    runtime_session_id: str | None = Field(default=None, max_length=256)
    process_pid: int | None = Field(default=None, ge=0, le=2**31 - 1)
    #: Guards against PID reuse: a live PID whose start identity differs is a
    #: different process, not our worker.
    process_start_identity: str | None = Field(default=None, max_length=256)
    started_at: str = Field(default_factory=_utc_now)
    ended_at: str | None = None
    exit_status: int | None = None
    confidence: ExecutionConfidence | None = None
    failure_class: FailureClass | None = None
    #: Worker's own claim about what it did. Recorded, never trusted.
    worker_reported: str | None = Field(default=None, max_length=8192)
    #: Whether every acceptance check the supervisor ran passed.
    acceptance_passed: bool | None = None
    acceptance_detail: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
    #: Relative paths of evidence written under ``evidence/``.
    evidence_paths: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    estimated_cost_usd: float | None = None
    #: Tool/permission requests the runtime denied. 0 means "not observed",
    #: never "definitely none" -- some runtimes cannot report denials.
    policy_denials: int = Field(default=0, ge=0, le=1_000_000)
    usage: dict[str, Any] = Field(default_factory=dict)
    notes: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    merge_authorized: Literal[False] = False


class TaskRecord(BaseModel):
    """Reconciled per-task state."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=128)
    state: NodeState = NodeState.DISCOVERED
    attempts: int = Field(default=0, ge=0, le=10_000)
    launches: int = Field(default=0, ge=0, le=10_000)
    last_attempt_id: str | None = None
    last_failure_class: FailureClass | None = None
    #: Observer id for an unresolved external precondition, if any.
    pending_observer_id: str | None = None
    #: Set when a task passed local acceptance but still needs a distinct
    #: agent to certify it. TASK_COMPLETE != PROGRAM_COMPLETE, and acceptance
    #: != independent verification.
    awaiting_independent_verification: bool = False
    verified_by_agent_id: str | None = None
    #: Fingerprint of the observable evidence at the end of the last attempt.
    #: Progress is "this changed", not "there were more messages".
    progress_fingerprint: str | None = None
    reason: str = Field(default="", max_length=512)


class HandoffRecord(BaseModel):
    """An operator explicitly enrolling an existing stored session.

    Neither supported runtime can attach to a *running* interactive session,
    and this package never adopts a process it did not start. What it does
    support is the controlled alternative: an operator names a session the
    runtime has stored, and the next dispatch for that task continues that
    conversation in a new supervised run instead of starting from nothing.

    The enrolment is a recorded human act -- ``enrolled_by`` and ``note`` say
    who asked and why. It is consumed exactly once: a handoff that fired and
    then sat around would resume the same session on a later attempt, which is
    a duplicate dispatch wearing a different name.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=128)
    adapter: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=256)
    enrolled_by: str = Field(min_length=1, max_length=256)
    enrolled_at: str = Field(default_factory=_utc_now)
    note: str = Field(default="", max_length=1024)
    consumed_by_attempt_id: str | None = None
    #: What the adapter's own probe said about this session at enrolment time.
    #: ``None`` means the adapter could not tell, which is recorded rather
    #: than smoothed over -- an operator enrolling an id the runtime has never
    #: heard of should be able to see that nothing corroborated it.
    session_observed: bool | None = None


class ProgramStateRecord(BaseModel):
    """The reconciled picture of one approved program."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    package_id: Literal["AS-ORCH-PROGRAM-SUPERVISOR-001"] = PACKAGE_ID
    truth_boundary: str = TRUTH_BOUNDARY
    program_id: str = Field(min_length=1, max_length=128)
    program_digest: str = Field(min_length=64, max_length=64)
    base_pin: str = Field(min_length=40, max_length=40)
    created_at: str = Field(default_factory=_utc_now)
    updated_at: str = Field(default_factory=_utc_now)
    #: Which supervisor instance most recently owned this program.
    supervisor_instance_id: str | None = None
    supervisor_pid: int | None = None
    total_launches: int = Field(default=0, ge=0, le=1_000_000)
    total_cycles: int = Field(default=0, ge=0, le=10_000_000)
    idle_cycles: int = Field(default=0, ge=0, le=1_000_000)
    estimated_cost_usd: float = Field(default=0.0, ge=0.0)
    cancel_requested: bool = False
    #: Stops NEW dispatch while leaving running workers alone. Reversible;
    #: cancellation is not.
    paused: bool = False
    paused_by: str | None = None
    paused_at: str | None = None
    complete: bool = False
    last_stop_reason: ProgramStopReason | None = None
    tasks: dict[str, TaskRecord] = Field(default_factory=dict)
    attempts: dict[str, AttemptRecord] = Field(default_factory=dict)
    #: Pending and spent session hand-offs, keyed by task id.
    handoffs: dict[str, HandoffRecord] = Field(default_factory=dict)
    merge_authorized: Literal[False] = False
    execution_authorized: Literal[False] = False


def state_dir(root: Path) -> Path:
    """Program state directory for a supervisor root."""
    return root / DEFAULT_STATE_RELATIVE


def state_path(root: Path) -> Path:
    return state_dir(root) / STATE_NAME


def events_path(root: Path) -> Path:
    return state_dir(root) / EVENTS_NAME


def evidence_dir(root: Path) -> Path:
    return state_dir(root) / EVIDENCE_DIR


def _write_atomic(target: Path, text: str) -> None:
    """Write, fsync, then rename. A torn state file is not a state file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    fd = os.open(str(tmp), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, target)


def load_state(root: Path) -> ProgramStateRecord | None:
    path = state_path(root)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StoreError(
            f"program state at {path} is unreadable: {exc}",
            code="STATE_UNREADABLE",
        ) from exc
    return ProgramStateRecord.model_validate(raw)


def persist_state(root: Path, state: ProgramStateRecord) -> Path:
    state.updated_at = _utc_now()
    path = state_path(root)
    _write_atomic(path, json.dumps(state.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")
    return path


def append_event(root: Path, name: str, payload: dict[str, Any]) -> None:
    """Append one durable event and fsync it.

    fsync matters here and nowhere else in this module: this log is the only
    record that survives an abrupt kill between two state writes, and a
    checkpoint that is still sitting in the page cache when the machine dies
    is a checkpoint that was never taken.
    """
    path = events_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": _utc_now(), "event": name, **payload}
    line = json.dumps(row, sort_keys=True, default=str) + "\n"
    fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def read_events(root: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    path = events_path(root)
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                # A partially written final line after a hard kill. Recorded
                # as a residue rather than discarded silently.
                rows.append({"event": "MALFORMED_EVENT_LINE", "raw_bytes": len(text)})
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    if limit is not None and limit >= 0:
        return rows[-limit:]
    return rows


def write_evidence(root: Path, relative_name: str, payload: dict[str, Any]) -> Path:
    """Write one evidence document under the program's evidence directory."""
    base = evidence_dir(root).resolve()
    target = (evidence_dir(root) / relative_name).resolve()
    if not target.is_relative_to(base):
        raise StoreError(
            "evidence path escapes the program evidence directory",
            code="EVIDENCE_PATH_ESCAPE",
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(target, json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    return target
