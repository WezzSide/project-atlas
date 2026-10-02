"""AS-MISSION-VERTICAL-SLICE-001, RECOVERY -- UI closure, worker
interruption, stale ownership and an uncertain external-operation result
must never silently lose progress, duplicate an effect, or resume an
unauthorized write. Ambiguous outcomes are reconciled, never blindly
retried.

Two genuinely different situations, kept genuinely distinct:

  UI RECONNECTION -- the worker (the process that owns the workspace
    lease) is still alive. A reader closing and reopening a view onto
    this run changes nothing about the run itself; it just re-observes
    current state. `reconcile_mission_run()` reports `STILL_RUNNING` and
    touches nothing.

  WORKER RECOVERY -- the worker died (the lease is no longer held). What
    happened depends entirely on which checkpoint state it died in:
    `STARTED` (adapter never invoked -- nothing external happened, safe
    to retry as a NEW run), `ADAPTER_INVOKED` (the adapter call was made
    but no confirmed outcome was ever recorded -- genuinely UNCERTAIN,
    the external effect may or may not have landed, never silently
    retried), a resolved terminal state (the outcome WAS recorded before
    death -- known, not ambiguous), or `CLEANUP_UNCONFIRMED_BLOCKED` (a
    timeout whose process-tree termination could not be confirmed --
    stays blocked, never silently treated as clean).

COHERENT OWNERSHIP, NOT TWO INDEPENDENT READS: this module briefly
ACQUIRES the workspace lease itself before reading the checkpoint, rather
than reading the checkpoint and separately probing the lease as two
unrelated observations. A real defect lived in exactly that gap: a worker
could have a `STARTED` checkpoint at the moment of the checkpoint read,
then advance to `ADAPTER_INVOKED`, perform its effect, and exit -- all
before the (separate, later) lease probe -- so the probe reported the
lease free and this function returned `safe_to_retry=True` from the
stale `STARTED` snapshot, permitting the effect to be duplicated. A
successful lease acquisition here is itself proof no one else held it at
that instant, and (since we now hold it) proof nothing else can be
mutating the checkpoint while we read it -- eliminating the gap instead
of narrowing it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from project_atlas.orchestration.mission.execution import (
    MissionRunCheckpoint,
    load_checkpoint,
    load_checkpoint_detailed,
)
from project_atlas.orchestration.mission.lease import (
    acquire_mission_lease,
    release_mission_lease,
)

ReconciliationOutcome = Literal[
    "NO_RUN_FOUND",
    "STILL_RUNNING",
    "ALREADY_COMPLETE",
    "SAFE_TO_RETRY_NEVER_STARTED_EXTERNAL_EFFECT",
    "KNOWN_OUTCOME_CLEANUP_ONLY",
    "UNCERTAIN_REQUIRES_RECONCILIATION",
    "CLEANUP_UNCONFIRMED_BLOCKED",
    "UNKNOWN_CHECKPOINT_UNSAFE_TO_RETRY",
]


@dataclass(frozen=True)
class ReconciliationResult:
    run_id: str | None
    outcome: ReconciliationOutcome
    detail: str
    safe_to_retry: bool
    checkpoint: MissionRunCheckpoint | None


def reconcile_mission_run(workspace: Path) -> ReconciliationResult:
    """Determine what actually happened to the mission run that last
    touched `workspace`. Momentarily acquires (and immediately releases)
    the workspace lease under a private, single-use reconciler identity to
    read the checkpoint under coherent ownership -- see module docstring.
    If the lease cannot be acquired, a live worker genuinely owns it right
    now (UI-reconnect, not recovery) and nothing is read under ownership;
    the checkpoint is only peeked at, read-only, for reporting which run
    is live, never to justify a retry decision."""
    reconciler_id = f"reconciler-{uuid.uuid4().hex}"
    acquired = acquire_mission_lease(workspace, run_id=reconciler_id)
    if not acquired:
        checkpoint = load_checkpoint(workspace)
        return ReconciliationResult(
            run_id=checkpoint.run_id if checkpoint is not None else None,
            outcome="STILL_RUNNING",
            detail="workspace lease is held by a live process; run is live",
            safe_to_retry=False,
            checkpoint=checkpoint,
        )

    try:
        loaded = load_checkpoint_detailed(workspace)
    finally:
        release_mission_lease(workspace, run_id=reconciler_id)

    if loaded.status == "ABSENT":
        return ReconciliationResult(
            run_id=None,
            outcome="NO_RUN_FOUND",
            detail="no checkpoint found in this workspace",
            safe_to_retry=True,
            checkpoint=None,
        )
    if loaded.status != "VALID":
        # UNREADABLE / MALFORMED / INCOMPATIBLE: this checkpoint EXISTS
        # but cannot be trusted -- it could be hiding a real recorded
        # effect corrupted after the fact. Preserving `UNKNOWN` here
        # (never defaulting to "safe") is exactly what closes the
        # "corrupt checkpoint after an effect returned NO_RUN_FOUND with
        # safe_to_retry=True" defect: absent and unreadable are no longer
        # the same answer.
        return ReconciliationResult(
            run_id=None,
            outcome="UNKNOWN_CHECKPOINT_UNSAFE_TO_RETRY",
            detail=f"{loaded.status}: {loaded.detail}",
            safe_to_retry=False,
            checkpoint=None,
        )

    checkpoint = loaded.checkpoint
    assert checkpoint is not None  # VALID status guarantees this

    if checkpoint.state == "COMPLETE":
        return ReconciliationResult(
            run_id=checkpoint.run_id,
            outcome="ALREADY_COMPLETE",
            detail="run finished and released its own lease normally",
            safe_to_retry=False,
            checkpoint=checkpoint,
        )
    if checkpoint.state == "CLEANUP_UNCONFIRMED_BLOCKED":
        return ReconciliationResult(
            run_id=checkpoint.run_id,
            outcome="CLEANUP_UNCONFIRMED_BLOCKED",
            detail=(
                "an adapter timeout's process-tree cleanup could not be confirmed -- the "
                "workspace remains blocked pending manual verification, never silently "
                "released for reuse while descendants might still be mutating it"
            ),
            safe_to_retry=False,
            checkpoint=checkpoint,
        )
    if checkpoint.state == "STARTED":
        return ReconciliationResult(
            run_id=checkpoint.run_id,
            outcome="SAFE_TO_RETRY_NEVER_STARTED_EXTERNAL_EFFECT",
            detail="worker died before the adapter was invoked -- no external effect to reconcile",
            safe_to_retry=True,
            checkpoint=checkpoint,
        )
    if checkpoint.state in ("ADAPTER_CONFIRMED", "ADAPTER_FAILED", "ADAPTER_SPAWN_FAILED"):
        return ReconciliationResult(
            run_id=checkpoint.run_id,
            outcome="KNOWN_OUTCOME_CLEANUP_ONLY",
            detail=(
                f"the adapter's outcome WAS recorded ({checkpoint.state}) before the worker "
                "died reaching COMPLETE -- the outcome is known, not ambiguous; only the final "
                "cleanup step (releasing the lease, which the OS already did on crash) was skipped"
            ),
            safe_to_retry=False,
            checkpoint=checkpoint,
        )
    # checkpoint.state == "ADAPTER_INVOKED": the adapter call was made, but
    # this process died before any confirmed/failed outcome was recorded.
    # Whether the external effect landed is genuinely unknown from this
    # checkpoint alone -- reconciliation (inspecting the workspace/target
    # system for evidence the effect actually happened) must happen before
    # any retry, never an automatic blind retry.
    return ReconciliationResult(
        run_id=checkpoint.run_id,
        outcome="UNCERTAIN_REQUIRES_RECONCILIATION",
        detail=(
            "worker died mid-adapter-invocation with no recorded outcome -- the external effect "
            "may or may not have landed; inspect the workspace for evidence before retrying, "
            "never retry blindly"
        ),
        safe_to_retry=False,
        checkpoint=checkpoint,
    )
