"""AS-MISSION-VERTICAL-SLICE-001, DEVELOPMENT/RECOVERY -- the smallest
useful path: mission selection -> isolated workspace -> submitted action
-> diff/test evidence -> checkpoint -> handoff.

Durability follows the pattern the research this mission is built from
cites (LangGraph's thread/store distinction; Temporal's crash-window
reasoning): every state transition that matters is written BEFORE the
external effect it precedes, not after, and a checkpoint that says
"the adapter was invoked" without a subsequent CONFIRMED or FAILED is
reported as UNCERTAIN, never silently resumed as if it never started and
never blindly retried as if it definitely finished -- see `recovery.py`.

Ownership of the workspace is `lease.py`'s job, kept independent of this
module's own checkpoint state, matching `resident_driver`'s split between
LOCK_ATOMICITY and RECEIPT_ATOMICITY: a crash releases the lease
immediately (the OS does that), but the checkpoint on disk is what tells a
recovering reader whether the crashed run's own effect landed.

This is also the GOVERNED CALLER: it, not the adapter, is what enforces
`trusted_policy` (a run whose context claims a disallowed
`MERGE_AUTHORIZATION` is refused before anything runs) and validates
source freshness at dispatch time (a run whose context has gone stale
since compile time is refused by default, not silently executed against
outdated knowledge).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from project_atlas.orchestration.mission import MISSION_STATE_DIR_NAME
from project_atlas.orchestration.mission.adapter import AdapterResult, MissionAdapter
from project_atlas.orchestration.mission.context_packet import (
    MissionContextPacket,
    check_context_staleness,
)
from project_atlas.orchestration.mission.lease import (
    acquire_mission_lease,
    release_mission_lease,
)

CHECKPOINT_NAME = "mission-run-checkpoint.json"
CHECKPOINT_SCHEMA_VERSION = 1

RunState = Literal[
    "STARTED",
    "ADAPTER_INVOKED",
    "ADAPTER_CONFIRMED",
    "ADAPTER_FAILED",
    "ADAPTER_SPAWN_FAILED",
    "CLEANUP_UNCONFIRMED_BLOCKED",
    "COMPLETE",
]

# Terminal AND resolved -- a run in one of these states is done, and its
# recorded `result` is trustworthy. `CLEANUP_UNCONFIRMED_BLOCKED` is
# deliberately excluded: it is terminal in the sense that this run will
# never proceed further, but it is NOT resolved -- see that state's own
# handling below and in `recovery.py`.
_TERMINAL_RESOLVED_STATES: frozenset[RunState] = frozenset(
    {"ADAPTER_CONFIRMED", "ADAPTER_FAILED", "ADAPTER_SPAWN_FAILED", "COMPLETE"}
)


@dataclass(frozen=True)
class MissionRunCheckpoint:
    run_id: str
    mission_id: str
    workspace: str
    adapter_repr: str
    state: RunState
    created_at: float
    updated_at: float
    owner_pid: int
    idempotency_key: str
    result: dict[str, Any] | None = None
    schema_version: int = CHECKPOINT_SCHEMA_VERSION


def checkpoint_path(workspace: Path) -> Path:
    return workspace / MISSION_STATE_DIR_NAME / CHECKPOINT_NAME


CheckpointLoadStatus = Literal["ABSENT", "UNREADABLE", "MALFORMED", "INCOMPATIBLE", "VALID"]


@dataclass(frozen=True)
class CheckpointLoadResult:
    """Distinguishes every way loading a checkpoint can turn out, instead
    of collapsing "never existed" and "existed but is now unreadable or
    corrupt" into the same `None` -- a real, reported defect: a checkpoint
    corrupted AFTER a real external effect was recorded used to read back
    as `NO_RUN_FOUND, safe_to_retry=True`, silently discarding proof an
    effect had already happened."""

    status: CheckpointLoadStatus
    checkpoint: MissionRunCheckpoint | None
    detail: str


def load_checkpoint_detailed(workspace: Path) -> CheckpointLoadResult:
    """The full-fidelity loader. Also binds the checkpoint to the
    workspace it was found in: a checkpoint whose own recorded
    `workspace` field does not match where it was actually read from
    (e.g. copied or symlinked from elsewhere) is treated as MALFORMED --
    its identity cannot be trusted for this workspace."""
    path = checkpoint_path(workspace)
    if not path.is_file():
        return CheckpointLoadResult(status="ABSENT", checkpoint=None, detail="no checkpoint file")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return CheckpointLoadResult(status="UNREADABLE", checkpoint=None, detail=str(exc))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return CheckpointLoadResult(
            status="MALFORMED", checkpoint=None, detail=f"invalid JSON: {exc}"
        )
    if not isinstance(data, dict):
        return CheckpointLoadResult(
            status="MALFORMED", checkpoint=None, detail="checkpoint is not a JSON object"
        )
    schema_version = data.get("schema_version", CHECKPOINT_SCHEMA_VERSION)
    if schema_version != CHECKPOINT_SCHEMA_VERSION:
        return CheckpointLoadResult(
            status="INCOMPATIBLE",
            checkpoint=None,
            detail=f"schema_version={schema_version!r}, expected {CHECKPOINT_SCHEMA_VERSION!r}",
        )
    try:
        checkpoint = MissionRunCheckpoint(**data)
    except TypeError as exc:
        return CheckpointLoadResult(
            status="MALFORMED", checkpoint=None, detail=f"unexpected shape: {exc}"
        )
    resolved_workspace = str(workspace.resolve()) if workspace.exists() else str(workspace)
    if checkpoint.workspace not in (str(workspace), resolved_workspace):
        return CheckpointLoadResult(
            status="MALFORMED",
            checkpoint=None,
            detail=(
                f"checkpoint identity mismatch: recorded workspace "
                f"{checkpoint.workspace!r} does not match {resolved_workspace!r}"
            ),
        )
    return CheckpointLoadResult(status="VALID", checkpoint=checkpoint, detail="")


def load_checkpoint(workspace: Path) -> MissionRunCheckpoint | None:
    """Back-compat simple view: the checkpoint if VALID, else `None` --
    collapses ABSENT/UNREADABLE/MALFORMED/INCOMPATIBLE into the same
    "nothing usable" answer. Callers that must distinguish those cases
    (recovery reconciliation in particular, where the distinction is the
    whole point) must use `load_checkpoint_detailed()` instead."""
    return load_checkpoint_detailed(workspace).checkpoint


def _persist_checkpoint(workspace: Path, checkpoint: MissionRunCheckpoint) -> None:
    """Atomic write: temp file in the same directory, then `os.replace` --
    the established convention across this repository's own writers
    (scaffold.py, resident_driver.py's receipt, etc.)."""
    path = checkpoint_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    payload = json.dumps(asdict(checkpoint), indent=2, sort_keys=True) + "\n"
    tmp.write_text(payload, encoding="utf-8")
    _replace_with_retry(tmp, path)


def _replace_with_retry(
    src: Path, dst: Path, *, attempts: int = 25, delay_sec: float = 0.02
) -> None:
    """`os.replace()` can transiently fail on Windows with
    `PermissionError: [WinError 5] Access is denied` when another process
    (or even this same one, via a different handle) has `dst` open for
    reading at the exact instant of the rename -- a real, reproduced
    hazard: a benign checkpoint-polling reader (nothing exotic -- this
    package's own test helper) was enough to trigger it, crashing the
    writer with an uncaught exception. Windows lacks POSIX's guarantee
    that a rename always succeeds over an open file. The conflicting
    handle is normally held only briefly, so a short bounded retry
    resolves it without requiring every reader that will ever exist to
    cooperate with a special sharing mode."""
    last_exc: PermissionError | None = None
    for _ in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:
            last_exc = exc
            time.sleep(delay_sec)
    assert last_exc is not None
    raise last_exc


def _result_from_dict(data: dict[str, Any] | None) -> AdapterResult | None:
    if data is None:
        return None
    try:
        return AdapterResult(**data)
    except TypeError:
        return None


class WorkspaceUnavailableError(Exception):
    """Another live process already owns this workspace's lease."""


class UnreconciledPriorRunError(Exception):
    """This workspace has a prior checkpoint that is neither a resolved
    terminal outcome nor safely absent -- an unresolved in-flight run, or
    a checkpoint that is unreadable/malformed/incompatible and could be
    hiding a real recorded effect. Call `recovery.reconcile_mission_run()`
    and resolve it explicitly before starting a new run against this
    workspace; never silently retried or overwritten here."""


class ContextStaleError(Exception):
    """One or more of the context packet's sources have changed (or
    become unreadable) since it was compiled -- dispatch refused by
    default. Pass `allow_stale_context=True` to proceed anyway with a
    packet the caller has independently judged still usable."""


class PolicyRefusedError(Exception):
    """`trusted_policy` (caller-supplied authority, never derived from
    retrieved content) does not permit this run. This is the governed
    caller's own enforcement, independent of whatever the adapter itself
    would do."""


@dataclass(frozen=True)
class MissionRunResult:
    run_id: str
    checkpoint: MissionRunCheckpoint
    adapter_result: AdapterResult | None
    deduplicated: bool = False


def start_mission_run(
    *,
    mission_id: str,
    context: MissionContextPacket,
    adapter: MissionAdapter,
    workspace: Path,
    repo_root: Path,
    adapter_timeout_sec: float = 30.0,
    run_id: str | None = None,
    idempotency_key: str | None = None,
    allow_stale_context: bool = False,
) -> MissionRunResult:
    """Run one mission through the full path: claim the workspace,
    checkpoint BEFORE invoking the adapter, invoke it, checkpoint the
    outcome, release the workspace. Raises `WorkspaceUnavailableError` if
    another live run already owns `workspace`. Raises
    `UnreconciledPriorRunError` if a prior run against this workspace left
    an unresolved outcome. Raises `ContextStaleError` if the context has
    gone stale since compile time (unless `allow_stale_context=True`).
    Raises `PolicyRefusedError` if `context.trusted_policy` does not
    permit this run.

    IDEMPOTENCY: `idempotency_key` (default `f"{mission_id}:{context.base_head}"`)
    identifies "this exact logical operation" -- if a PRIOR run against
    this same workspace already reached a resolved terminal state under
    the SAME key, the adapter is NOT invoked again; the prior result is
    returned with `deduplicated=True`. This is enforced, not merely
    recorded: a real, reported defect let a completed run repeated with
    the same identity execute its effect twice.
    """
    rid = run_id or uuid.uuid4().hex
    key = idempotency_key or f"{mission_id}:{context.base_head}"

    merge_auth = context.trusted_policy.get("MERGE_AUTHORIZATION")
    if merge_auth not in (None, "NO"):
        raise PolicyRefusedError(
            f"trusted_policy.MERGE_AUTHORIZATION={merge_auth!r} is not permitted "
            f"by this governed caller"
        )

    if not acquire_mission_lease(workspace, run_id=rid):
        raise WorkspaceUnavailableError(f"workspace already owned: {workspace}")

    release_lease = True
    try:
        # Read+classify any existing checkpoint only AFTER we exclusively
        # hold the workspace -- doing this before acquiring the lease is
        # exactly the TOCTOU class of bug fixed in `recovery.py` (a
        # snapshot taken before ownership is established can be stale by
        # the time it is acted on).
        existing = load_checkpoint_detailed(workspace)
        if existing.status == "VALID" and existing.checkpoint is not None:
            prior = existing.checkpoint
            if prior.state in _TERMINAL_RESOLVED_STATES:
                if prior.idempotency_key == key:
                    return MissionRunResult(
                        run_id=prior.run_id,
                        checkpoint=prior,
                        adapter_result=_result_from_dict(prior.result),
                        deduplicated=True,
                    )
                # Different logical operation reusing this workspace --
                # not a retry of anything; proceed and overwrite below.
            else:
                raise UnreconciledPriorRunError(
                    f"workspace {workspace} has an unresolved prior run "
                    f"(state={prior.state!r}) -- reconcile it first"
                )
        elif existing.status in ("UNREADABLE", "MALFORMED", "INCOMPATIBLE"):
            raise UnreconciledPriorRunError(
                f"workspace {workspace} has a {existing.status} checkpoint "
                f"({existing.detail}) -- resolve before starting a new run"
            )
        # ABSENT: nothing prior. Proceed normally.

        if not allow_stale_context:
            staleness = check_context_staleness(repo_root, context)
            if staleness.superseded or staleness.unreadable:
                raise ContextStaleError(
                    f"context sources changed or became unreadable since compile time: "
                    f"superseded={staleness.superseded} unreadable={staleness.unreadable}"
                )

        now = time.time()
        checkpoint = MissionRunCheckpoint(
            run_id=rid,
            mission_id=mission_id,
            workspace=str(workspace.resolve()) if workspace.exists() else str(workspace),
            adapter_repr=repr(adapter),
            state="STARTED",
            created_at=now,
            updated_at=now,
            owner_pid=os.getpid(),
            idempotency_key=key,
            result=None,
        )
        _persist_checkpoint(workspace, checkpoint)

        # Written BEFORE the external effect, deliberately -- this is the
        # line that makes "crashed mid-adapter-call" distinguishable from
        # "never started" during recovery.
        checkpoint = _advance(checkpoint, "ADAPTER_INVOKED")
        _persist_checkpoint(workspace, checkpoint)

        result = adapter.run(workspace=workspace, context=context, timeout_sec=adapter_timeout_sec)

        if result.failure_class == "SPAWN_FAILED":
            # Proven pre-effect failure: nothing external could possibly
            # have happened, classified distinctly from a genuine
            # in-flight uncertainty.
            checkpoint = _advance(checkpoint, "ADAPTER_SPAWN_FAILED", result=asdict(result))
            _persist_checkpoint(workspace, checkpoint)
            return MissionRunResult(run_id=rid, checkpoint=checkpoint, adapter_result=result)

        if not result.cleanup_confirmed:
            # A timeout whose process-tree termination could not be
            # CONFIRMED (not merely attempted) -- do not release the
            # workspace for reuse while descendants might still be
            # mutating it. This is an explicit blocked state, not an
            # assumption of success.
            checkpoint = _advance(checkpoint, "CLEANUP_UNCONFIRMED_BLOCKED", result=asdict(result))
            _persist_checkpoint(workspace, checkpoint)
            release_lease = False
            return MissionRunResult(run_id=rid, checkpoint=checkpoint, adapter_result=result)

        state: RunState = "ADAPTER_CONFIRMED" if result.ok else "ADAPTER_FAILED"
        checkpoint = _advance(checkpoint, state, result=asdict(result))
        _persist_checkpoint(workspace, checkpoint)

        checkpoint = _advance(checkpoint, "COMPLETE")
        _persist_checkpoint(workspace, checkpoint)
        return MissionRunResult(run_id=rid, checkpoint=checkpoint, adapter_result=result)
    finally:
        if release_lease:
            release_mission_lease(workspace, run_id=rid)


def _advance(
    checkpoint: MissionRunCheckpoint, state: RunState, *, result: dict[str, Any] | None = None
) -> MissionRunCheckpoint:
    return MissionRunCheckpoint(
        run_id=checkpoint.run_id,
        mission_id=checkpoint.mission_id,
        workspace=checkpoint.workspace,
        adapter_repr=checkpoint.adapter_repr,
        state=state,
        created_at=checkpoint.created_at,
        updated_at=time.time(),
        owner_pid=checkpoint.owner_pid,
        idempotency_key=checkpoint.idempotency_key,
        result=result if result is not None else checkpoint.result,
    )
