"""AS-ORCH-LEASE-RECOVERY-001 -- evidence-gated release of a lease its own
loop got permanently stuck on.

Real incident that motivates this module (M3, first supervised-autonomous-
run attempt against INT-013, 2026-09-02): a governed loop hit
``RESOURCE_BOUNDARY`` (``loop.py``'s ``MAX_TICKS_PER_INVOCATION``) while a
lease (``LEASE-7``) was still ``ACTIVE`` in the durable lease projection.
Two real gaps compounded:

1. ``rehydrate_governor()`` only reconstructs a lease into a fresh
   governor when the loop's OWN persisted phase is ``LEASED``/
   ``DISPATCHING``/``AWAITING_RESULT`` (``rehydration.py``); once the loop
   reaches ``STOPPED``, ``governor.release_lease()`` is permanently
   unreachable for that lease through the normal flow -- there is no
   session boundary that hands it back.
2. ``rehydration._originate()`` historically treated ANY ``RELEASED``
   lease for a ``(package_id, base_pin)`` as proof the work reached a
   real terminal outcome (a "CERTIFIED witness"). AS-LEASE-RELEASED-
   CERTIFIED-WITNESS-001 now requires durable
   ``LoopState.completed_lease_ids`` to corroborate that ``lease_id``
   before stamping CERTIFIED. A bare hand-edit of the lease projection
   file (flipping ``ACTIVE`` -> ``RELEASED`` with nothing behind it)
   must not fabricate a CERTIFIED result. This was attempted once
   during the M3 investigation, caught before it reached anywhere
   durable (``sync_terminal_governed_states()`` only writes back for
   ``dag.TERMINAL_STATES = {CLOSED}``, which CERTIFIED is not), and
   reverted.

This module is the one legitimate way to release a lease stranded by gap
(1), built so it cannot be used to reproduce the gap-(2) fabrication: it
refuses unless it can read REAL, already-persisted local-process dispatch
receipts (``local_dispatch_port.list_dispatch_receipts()``) proving every
attempt for that exact lease genuinely failed, and the loop's own durable
state corroborates that this exact lease is what it is stuck on, via a
``RESOURCE_BOUNDARY`` stop specifically -- never ``OWNER_GATE``/
``SAFETY_BOUNDARY``/``HARD_BLOCKER``, which stay exactly as terminal as the
loop itself already makes them.

Owner review round 2 (2026-09-02): "genuinely failed" must be POSITIVELY
proven, not merely "not proven to have succeeded". The original check
(refuse only if a receipt showed ``status == "COMPLETED" and
authority_clean is True``) silently accepted a missing ``status``, a
``RUNNING`` receipt (real and well-formed, but genuinely unresolved --
not failed), an unrecognized status string, or a tampered non-bool
``authority_clean`` as if any of those were proof of failure. Every
receipt this module consults is now validated against the real
production schema (``local_dispatch_port.LocalDispatchReceipt``) and
bound to its own durable slot, lease, and package identity BEFORE this
module ever inspects its ``status`` -- see that model's and
``list_dispatch_receipts()``'s own docstrings. The acceptance check
itself is now a positive one: every receipt's ``status`` must equal
``FAILED`` exactly (the one real production value that is proof of a
terminal, exhausted failure -- ``dispatch_once()`` never writes
``COMPLETED`` unless the exit was clean and authority-clean, so treating
``COMPLETED`` as an unconditional success signal loses no real meaning).
Anything else -- missing, ``RUNNING``, ``COMPLETED``, or malformed --
refuses the release: this mechanism recovers from real, exhausted
failure, never discards real in-flight or successful work, and never
manufactures failure evidence out of its own absence.

Even a fully evidence-gated release is not enough on its own: writing
``status: "RELEASED"`` would still look like a success row. This
module therefore writes the lease projection's separate ``ABANDONED``
status (``lease_projection.project_abandon()``) instead of ``RELEASED``.
``_originate()``'s CERTIFIED-witness set only ever looks at
corroborated ``RELEASED`` rows, so an ``ABANDONED`` row is invisible
to it -- the node it names simply falls through to the ordinary
``add_node()``/``mark_ready()`` path on its next materialization, a
genuine fresh retry, never a fabricated certificate.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from project_atlas.orchestration.autonomy.lease_projection import (
    load_projection,
    project_abandon,
)
from project_atlas.orchestration.autonomy.local_dispatch_port import (
    LocalDispatchReceiptStatus,
    list_dispatch_receipts,
)
from project_atlas.orchestration.autonomy.loop import LoopPhase, load_loop_state
from project_atlas.orchestration.autonomy.models import AgentCapability, AgentLease, StopReason


class LeaseRecoveryError(ValueError):
    code = "LEASE_RECOVERY_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


def release_stalled_lease_after_exhausted_dispatch(
    root: Path,
    *,
    lease_id: str,
    loop_store: Path,
    lease_projection_store: Path,
) -> dict[str, object]:
    """Mark ``lease_id`` ``ABANDONED`` in the durable lease projection --
    ONLY when every real, already-persisted local-process dispatch
    receipt for it proves genuine, exhausted failure, and the loop's own
    durable state corroborates that this exact lease is what it is
    permanently stuck on via a ``RESOURCE_BOUNDARY`` stop. Refuses -- does
    not guess, does not accept a caller-supplied claim in place of real
    evidence -- in every other case. See the module docstring for the
    incident that makes this evidence gate load-bearing rather than
    decorative, and for why ``ABANDONED`` (never ``RELEASED``) is what
    gets written.

    Does not touch trust, does not touch ``origination.json``, does not
    itself re-lease or re-dispatch anything -- it only frees the package
    for a genuine future retry, exactly as ``NodeState`` would already
    allow for any other never-leased candidate.
    """
    resolved_root = root.expanduser().resolve()

    loop_state = load_loop_state(loop_store)
    if loop_state.phase is not LoopPhase.STOPPED:
        raise LeaseRecoveryError(
            f"loop is not STOPPED (phase={loop_state.phase.value!r}) -- this "
            "recovery path is only for a loop that has already reached a "
            "terminal stop, never a live one",
            code="LOOP_NOT_STOPPED",
        )
    if loop_state.stop_reason is not StopReason.RESOURCE_BOUNDARY:
        stop_reason_value = (
            loop_state.stop_reason.value if loop_state.stop_reason is not None else None
        )
        raise LeaseRecoveryError(
            f"stop_reason={stop_reason_value!r} is not RESOURCE_BOUNDARY -- "
            "OWNER_GATE/SAFETY_BOUNDARY/HARD_BLOCKER/other stops are not "
            "this mechanism's business; they stay exactly as terminal as "
            "the loop itself already makes them",
            code="NOT_RESOURCE_BOUNDARY_STOP",
        )
    if loop_state.active_lease_id != lease_id:
        raise LeaseRecoveryError(
            f"loop's own active_lease_id ({loop_state.active_lease_id!r}) "
            f"does not match the requested lease_id ({lease_id!r})",
            code="LEASE_ID_MISMATCH",
        )

    projection = load_projection(lease_projection_store)
    row = next((item for item in projection.leases if item.lease_id == lease_id), None)
    if row is None:
        raise LeaseRecoveryError(f"unknown lease {lease_id!r}", code="LEASE_UNKNOWN")
    if row.status != "ACTIVE":
        raise LeaseRecoveryError(
            f"lease {lease_id!r} is already {row.status!r}, not ACTIVE -- "
            "nothing to release",
            code="LEASE_NOT_ACTIVE",
        )

    # list_dispatch_receipts() itself now fails closed (LocalDispatchError)
    # on a malformed receipt, a receipt whose own dispatch_id disagrees
    # with the durable slot it was read from, or a receipt naming a
    # different lease -- see its own docstring and LocalDispatchReceipt's.
    # package_id is bound here too, since only this caller has the
    # governing lease projection row to check it against.
    receipts = list_dispatch_receipts(
        resolved_root, lease_id=lease_id, expected_package_id=row.package_id
    )
    if not receipts:
        raise LeaseRecoveryError(
            f"no dispatch receipt was ever recorded for {lease_id!r} -- "
            "there is no evidence this lease was ever genuinely attempted, "
            "so there is no evidence it genuinely failed either",
            code="NO_DISPATCH_EVIDENCE",
        )
    consulted_dispatch_ids: list[str] = []
    for receipt in receipts:
        consulted_dispatch_ids.append(receipt.dispatch_id)
        # Owner review round 2 (2026-09-02): POSITIVE proof of a real,
        # terminal, exhausted failure -- not merely the absence of a
        # positive success signal. The prior check
        # (`status == "COMPLETED" and authority_clean is True`) treated
        # "not proven to have succeeded" as "proven to have failed": a
        # missing status, RUNNING (real, well-formed, but genuinely
        # unresolved -- see LocalDispatchReceiptStatus's own docstring),
        # an unrecognized status string, or a tampered non-bool
        # authority_clean would all silently pass through as acceptable
        # failure evidence. `status == FAILED` exactly is the ONLY real
        # production shape that is positive proof of a terminal failure
        # (dispatch_once() never writes COMPLETED unless the exit was
        # clean AND authority-clean); every other value -- including
        # RUNNING and COMPLETED -- refuses the release. Schema/identity
        # malformation is already excluded upstream by
        # list_dispatch_receipts() itself, so every `receipt` reaching
        # this loop is a real, well-formed, self-consistent
        # LocalDispatchReceipt; this is a pure status-value decision.
        if receipt.status is not LocalDispatchReceiptStatus.FAILED:
            raise LeaseRecoveryError(
                f"receipt {receipt.dispatch_id!r} has status "
                f"{receipt.status.value!r}, not FAILED -- that is not "
                "positive proof this attempt genuinely, terminally failed "
                "(RUNNING means still-unresolved, not failed; COMPLETED "
                "means it succeeded); releasing it here would either "
                "discard real in-flight/successful work or release on "
                "evidence that proves nothing, and is refused",
                code="NOT_POSITIVELY_PROVEN_FAILED",
            )

    try:
        capabilities = tuple(AgentCapability(value) for value in row.capabilities)
    except ValueError as exc:
        raise LeaseRecoveryError(
            f"lease {lease_id!r} has an unrecognized capability value: {exc}",
            code="STATE_CORRUPT",
        ) from exc
    try:
        lease = AgentLease(
            lease_id=row.lease_id,
            agent_id=row.agent_id,
            package_id=row.package_id,
            branch=row.branch,
            worktree=row.worktree,
            base_pin=row.base_pin,
            authorized_paths=row.authorized_paths,
            forbidden_paths=row.forbidden_paths,
            capabilities=capabilities,
            start_state=row.start_state,
            expected_output="EVIDENCE_BUNDLE",
            expiry_or_terminal_condition="UNTIL_NODE_TERMINAL",
            active=False,
            sequence=row.created_sequence,
        )
    except ValidationError as exc:
        raise LeaseRecoveryError(
            f"lease {lease_id!r} does not reconstruct into a valid "
            f"AgentLease: {exc}",
            code="STATE_CORRUPT",
        ) from exc

    # Abandoned against the lease's OWN recorded base_pin, never current
    # live main -- exactly reap_orphaned_lease_releases()'s own reasoning
    # (lease_projection.py): finalizing historical bookkeeping for a
    # provably-exhausted attempt must not depend on whether main has since
    # moved, or project_abandon()'s reject_stale_base would wrongly refuse.
    project_abandon(lease_projection_store, lease, live_main=row.base_pin)

    return {
        "lease_id": lease_id,
        "package_id": row.package_id,
        "abandoned": True,
        "evidence_receipt_count": len(receipts),
        "evidence_dispatch_ids": tuple(consulted_dispatch_ids),
    }


__all__ = [
    "LeaseRecoveryError",
    "release_stalled_lease_after_exhausted_dispatch",
]
