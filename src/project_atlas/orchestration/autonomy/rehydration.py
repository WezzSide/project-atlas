"""ORCH001E-011: cross-process governor/loop rehydration.

``run_governor_loop_tick()`` (cli.py) constructs a brand-new, empty
``AutonomousGovernor`` on every CLI invocation. Only the loop's own
``LoopState`` (phase, active_package_id, active_lease_id, ...) survives a
process restart on disk; the governor's in-memory node list does not. Any
code path that looks up a node by package_id against ``governor.snapshot()
.nodes`` (``AutonomousLoop._dispatch_leased``, ``AutonomousLoop.
apply_observed_result``) then raises an uncaught ``StopIteration`` for any
loop_state that reflects in-flight work, because the freshly-constructed
governor never saw that work.

This module is the fix: given a fresh governor, the live inventory, and the
persisted loop/lease-projection state, reconstruct just enough of the
governor's node list for process N+1 to continue where process N left off
-- or fail closed when the durable evidence is not sufficient to safely do
so. It deliberately reuses the existing durable artifacts already in the
repository (``AS-ORCH-DURABLE-LEASE-PROJECTION-001`` in
``lease_projection.py``, ``discover()``/``ingest_discovery()`` in
discovery.py/governor.py) rather than inventing a second, competing
persisted-DAG model.

PERSISTED_AUTHORITY != AGENT_CLAIM: every fact this module trusts comes from
a durable, previously-written artifact (the loop store, the lease
projection) -- never from an unverified in-memory claim, and never from a
freshly-recomputed value that merely happens to match by coincidence.

Scope, deliberately narrow:

- IDLE / STOPPED / FAILED_CLOSED / no persisted loop state at all: nothing
  is in flight. Only the harmless, already-existing origination pass
  (``discover()`` + ``ingest_discovery()``) runs.
- LEASED (with both ``active_package_id`` and ``active_lease_id`` set): the
  one case where durable evidence -- the lease projection row, cross-checked
  against the live inventory's current main -- is sufficient to safely
  reconstruct the exact node and lease that was granted. Reconstruction is
  possible today only for ``PILOT_PACKAGE_ID``, the only node shape the
  governor knows how to deterministically rebuild from inventory alone
  (``AutonomousGovernor._pilot_node``); any other package_id fails closed
  with ``NODE_NOT_REHYDRATABLE`` rather than fabricate a ``WorkNode`` whose
  mutation surface, acceptance criteria, and IV requirements were never
  durably recorded anywhere.
- DISPATCHING / AWAITING_RESULT: the same LEASED-phase node/lease
  reconstruction (``_restore_leased_node``) is attempted first, using
  the SAME durable evidence (lease projection row + origination
  projection record) the LEASED case already trusts -- this is not new
  trust, only reusing it for two more phases. If that reconstruction
  succeeds, this function returns normally instead of failing closed;
  the caller is then expected to construct ``AutonomousLoop`` (passing
  the SAME ``dispatch``/``execution_host_class_override`` it used
  before restarting) and call its own ``recover()``, which is the
  actual crash-recovery logic -- it asks the ``DispatchPort`` itself
  what really happened (``recover()``/``find_active_dispatch_id()``),
  never guesses. Review finding (chatgpt-codex-connector, PR #662):
  without this, a dispatch port's own recovery machinery was
  unreachable through the real ``run_governor_loop_tick()`` entry
  point -- this function raised before ``AutonomousLoop`` was even
  constructed. If reconstruction is not possible (not an
  origination-derived package, no durable lease row, a stale/corrupt
  record) this still fails closed with
  ``EXECUTION_STATE_NOT_REHYDRATABLE``, unchanged from before -- this
  is strictly additive, never a new way to bypass the fail-closed
  default.
- VALIDATING: a not-yet-finalized verification outcome, no durable
  record of exactly how far it got, and no ``DispatchPort`` seam to ask
  either (verification is a governor-internal step, not a dispatch).
  Guessing here risks double-counting a real side effect. Still fails
  closed immediately with ``EXECUTION_STATE_NOT_REHYDRATABLE``.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from project_atlas.orchestration.autonomy.dag import IllegalTransitionError
from project_atlas.orchestration.autonomy.discovery import discover
from project_atlas.orchestration.autonomy.governor import AutonomousGovernor, GovernorError
from project_atlas.orchestration.autonomy.lease_projection import (
    ProjectedLease,
    ProjectionError,
    active_rows,
    load_projection,
    reap_orphaned_lease_releases,
)
from project_atlas.orchestration.autonomy.loop import LoopError, LoopPhase, load_loop_state
from project_atlas.orchestration.autonomy.models import (
    PILOT_PACKAGE_ID,
    AgentCapability,
    AgentLease,
    AgentRecord,
    DiscoveryReport,
    ExecutionHostClass,
    LiveInventory,
    NodeState,
    OwnerGateKind,
    TrustedAnchorRecord,
    WorkNode,
)

#: Loop phase with a not-yet-finalized verification outcome, no durable
#: record of exactly how far it got, and no DispatchPort seam to ask --
#: reconstructing a node here would mean guessing at reality rather than
#: reading it from evidence. Always fails closed.
_VERIFICATION_IN_FLIGHT_PHASES: frozenset[LoopPhase] = frozenset({LoopPhase.VALIDATING})

#: Loop phases where a real DispatchPort seam exists to ask "what actually
#: happened" (AutonomousLoop.recover()) -- node/lease reconstruction is
#: attempted here via the SAME durable evidence the LEASED case already
#: trusts, so the caller can reach that seam instead of failing closed
#: before ever constructing AutonomousLoop.
_DISPATCH_RECOVERABLE_PHASES: frozenset[LoopPhase] = frozenset(
    {LoopPhase.DISPATCHING, LoopPhase.AWAITING_RESULT}
)

#: Loop phases with nothing in flight for the governor to rehydrate.
_NO_ACTIVE_LEASE_PHASES: frozenset[LoopPhase] = frozenset(
    {LoopPhase.IDLE, LoopPhase.STOPPED, LoopPhase.FAILED_CLOSED}
)


class RehydrationError(ValueError):
    """Fail-closed rehydration error. Never an authority grant."""

    code = "REHYDRATION_FAILED_CLOSED"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


def rehydrate_governor(
    governor: AutonomousGovernor,
    *,
    inventory: LiveInventory,
    trusted: TrustedAnchorRecord,
    loop_store: Path,
    lease_projection_store: Path,
    origination_projection_store: Path | None = None,
    execution_host_class_override: ExecutionHostClass | None = None,
) -> None:
    """Bring a freshly-constructed ``governor`` up to date with durable
    state a prior process persisted, or fail closed.

    Must run before constructing ``AutonomousLoop`` / calling ``tick()`` /
    ``recover()`` on ``governor``, so that any node lookup those perform
    against ``governor.snapshot().nodes`` finds what a prior process would
    have found -- or the caller never reaches those lookups at all, because
    this function already raised.

    ``origination_projection_store`` (D-PHASE2A, ADR-033): optional, and
    ``None`` by default -- every existing caller that does not pass it
    gets byte-identical behavior to before this parameter existed (the
    pilot-only rehydration path). When provided, a leased package_id that
    is not the pilot is additionally checked against the durable
    origination projection (``orchestration.origination.projection``)
    before falling back to the pre-existing ``NODE_NOT_REHYDRATABLE``
    fail-closed outcome.

    ``execution_host_class_override`` (AS-ORCH-LOCAL-DISPATCH-001, PR-C):
    ``None`` by default -- every existing caller is unaffected. A
    granted lease's ``execution_host_class`` override
    (``governor.lease()``'s own same-named parameter) lives only on the
    in-memory ``WorkNode``, never in the durable lease projection or the
    origination-materialized record -- it does not survive a process
    restart on its own. The caller of ``run_governor_loop_tick()``
    already re-supplies the SAME runtime configuration (a fixed,
    operator-set ``local_execution_config``/``argv_template``) on every
    invocation, restart included, so re-applying it here to a
    reconstructed node is not a new grant -- it restores exactly the
    same in-memory fact a live, uninterrupted process would still have
    had. Applied identically for LEASED/DISPATCHING/AWAITING_RESULT
    reconstruction (see ``_restore_leased_node``).

    AS-ORIGIN-MATERIALIZED-SUPERSESSION-001 (owner directive
    D-ATLAS-ORIGINATION-MATERIALIZED-REVISION-SUPERSESSION §12, in-flight
    sibling audit): ``governor`` MUST be a freshly-constructed instance
    this function has not been called on before, and this function must
    never be called twice on the SAME ``governor`` instance across an
    intervening state change (e.g. a ``run_origination_scan()`` call that
    supersedes a revision this same governor already discovered/added).
    Architectural finding, verified empirically: as of this package, the
    ONLY production call site in this repository is
    ``orchestration.autonomy.cli.run_governor_loop_tick()``, which
    constructs a brand-new ``AutonomousGovernor()`` immediately before
    every single call (never reused across invocations, never passed in
    from a caller) -- so a governor that already holds a since-superseded
    origination-derived node in memory, while this function's OWN read of
    ``list_materialized_work_nodes()`` reflects the newer, reconciled
    truth, cannot occur through any real, currently-existing driver of
    this system. This is a caller-discipline precondition, not something
    this function can itself detect or enforce (it has no way to know
    whether ``governor`` is genuinely fresh) -- a future caller that
    violates it would be introducing a new, currently-nonexistent
    execution path, at which point THAT caller's own review must
    establish it is safe against a stale in-memory node, not assume this
    function makes it safe automatically.

    D-ATLAS-PR677-REVISION-IDENTITY-BINDING-FINALIZATION §8 (result/
    receipt binding audit, recorded here rather than adding new binding
    fields -- see that directive's own §2/§15: identify the narrow
    authoritative boundary, do not add redundant checks mechanically at
    every function): a dispatch receipt/result is never independently
    checked against origination revision identity anywhere, and does not
    need to be. Every path that could ever CONSUME a receipt (``AutonomousLoop
    .recover()``, ``apply_observed_result()``) is reached only via a
    ``WorkNode`` already sitting in ``governor.snapshot().nodes`` -- and
    within one process, that node list is populated EXCLUSIVELY by this
    function (``rehydrate_governor()``) plus ``AutonomousLoop`` itself
    calling ``governor.lease()``/``add_node()`` against nodes THIS SAME
    function already vetted this tick. Combined with the §12 finding
    above (every real caller constructs a fresh governor and calls this
    function exactly once per tick) and the ``has_ever_had_multiple_
    revisions()`` guard in ``_restore_leased_node()`` below (refusing to
    even reconstruct a node/lease once its package_id has ever been
    revised), there is no reachable path where a dispatch result could
    be bound to, or replayed against, a stale/superseded revision's
    ``WorkNode`` -- the tick that would have produced or consumed such a
    binding is refused upstream, before dispatch or result-processing
    code ever runs. A separate revision-identity field on receipts would
    duplicate this guarantee at a boundary that is never actually
    reachable in a stale state, which is exactly the "redundant check"
    this package's own directive says not to add.
    """
    try:
        loop_state = load_loop_state(loop_store)
    except LoopError as exc:
        if exc.code == "STATE_MISSING":
            # No process has ever ticked this root. Nothing to rehydrate;
            # AutonomousLoop's own constructor will create fresh IDLE state.
            _originate(
                governor,
                inventory=inventory,
                trusted=trusted,
                origination_projection_store=origination_projection_store,
                lease_projection_store=lease_projection_store,
                completed_lease_ids=(),
            )
            return
        # STATE_CORRUPT / TARGET_MOVED / etc: genuinely fail closed. The
        # caller's own except clause for LoopError already handles this the
        # same way AutonomousLoop's constructor would have.
        raise

    if loop_state.phase in _VERIFICATION_IN_FLIGHT_PHASES:
        raise RehydrationError(
            f"cannot safely rehydrate governor state for in-flight phase "
            f"{loop_state.phase.value}: no durable record of exactly how far "
            f"execution progressed before the prior process exited, and no "
            f"DispatchPort seam to ask (verification is governor-internal, "
            f"not a dispatch)",
            code="EXECUTION_STATE_NOT_REHYDRATABLE",
        )

    # AUTONOMY_PROJECTION_ERROR_RECOVERY_BOUNDARY (post-#654 follow-up):
    # heal any lease this exact loop already durably proved complete
    # (``completed_lease_ids``) whose projection row never made it to
    # RELEASED -- independent of the current phase, since the stranded
    # lease can be an arbitrarily old entry in that history, unrelated to
    # whatever phase this rehydration is otherwise handling. Runs BEFORE
    # ``_originate()`` (independent-verification finding): that call
    # takes its own ``load_projection()`` snapshot to build
    # ``active_ids``/``released_revisions`` for CERTIFIED-witness
    # dependency exposure -- reaping first means a dependent blocked on
    # the just-healed lease becomes visible THIS tick, not one full
    # rehydration later (still safe either order: ``select_next()``'s
    # missing-dependency check fails closed in the interim regardless).
    try:
        reap_orphaned_lease_releases(lease_projection_store, loop_state.completed_lease_ids)
    except ProjectionError as exc:
        raise RehydrationError(str(exc), code=exc.code) from exc

    newly_discovered, report = _originate(
        governor,
        inventory=inventory,
        trusted=trusted,
        origination_projection_store=origination_projection_store,
        lease_projection_store=lease_projection_store,
        completed_lease_ids=loop_state.completed_lease_ids,
    )

    if loop_state.phase in _NO_ACTIVE_LEASE_PHASES:
        return

    if loop_state.phase not in ({LoopPhase.LEASED} | _DISPATCH_RECOVERABLE_PHASES):
        # pragma: no cover - defensive; every LoopPhase value is handled by
        # one of the branches above or this one.
        raise RehydrationError(
            f"unrecognized loop phase {loop_state.phase.value!r} during rehydration",
            code="STATE_INCONSISTENT",
        )

    package_id = loop_state.active_package_id
    lease_id = loop_state.active_lease_id
    if package_id is None or lease_id is None:
        raise RehydrationError(
            f"{loop_state.phase.value} phase is missing active_package_id or "
            f"active_lease_id",
            code="STATE_INCONSISTENT",
        )
    try:
        _restore_leased_node(
            governor,
            inventory=inventory,
            package_id=package_id,
            lease_id=lease_id,
            already_discovered_package_ids=newly_discovered,
            candidate_owner_gates={c.package_id: c.owner_gate for c in report.candidates},
            lease_projection_store=lease_projection_store,
            origination_projection_store=origination_projection_store,
            execution_host_class_override=execution_host_class_override,
        )
    except RehydrationError:
        if loop_state.phase is LoopPhase.LEASED:
            # Unchanged, pre-existing behavior: the LEASED case's own
            # specific error code (NODE_NOT_REHYDRATABLE, STALE_LEASE,
            # LEASE_NOT_PROJECTED, ...) propagates as-is.
            raise
        # DISPATCHING / AWAITING_RESULT: reconstruction was not possible
        # (not an origination-derived/pilot package, no durable lease
        # row, a stale/corrupt record, ...) -- fail closed exactly as
        # this phase always has, never a new bypass.
        raise RehydrationError(
            f"cannot safely rehydrate governor state for in-flight phase "
            f"{loop_state.phase.value}: node/lease reconstruction was not "
            f"possible from durable evidence",
            code="EXECUTION_STATE_NOT_REHYDRATABLE",
        ) from None


def _originate(
    governor: AutonomousGovernor,
    *,
    inventory: LiveInventory,
    trusted: TrustedAnchorRecord,
    origination_projection_store: Path | None = None,
    lease_projection_store: Path | None = None,
    completed_lease_ids: tuple[str, ...] = (),
) -> tuple[frozenset[str], DiscoveryReport]:
    """Run the existing, already-deterministic discovery pass. Fixes
    "originate new work on a fresh process" for the IDLE case, and is a
    harmless no-op read whenever nothing is currently eligible. Returns
    every package_id this pass newly added to ``governor`` (empty if
    none) and the full discovery report, so callers needing evidence
    about a specific package_id (e.g. its owner_gate) don't have to call
    ``discover()`` a second time.

    ORCH001E-011 finding #1 (independent-IV): ``ingest_discovery()`` only
    ever creates a node in ``DISCOVERED`` -- never ``READY`` -- and
    ``AutonomousLoop._select_and_lease()`` (via ``select_next()``) only
    ever considers ``READY`` nodes. ``run_controlled_pilot()`` already
    knows this and calls ``mark_ready()`` right after ``ingest_discovery()``
    (see below in this same module); rehydration's own origination pass
    must run the identical transition, or a freshly-originated node sits
    in ``DISCOVERED`` forever and the very next tick reports
    ``NO_ELIGIBLE_WORK`` despite discovery having just found real work.

    D-PHASE2A-2: when ``origination_projection_store`` is provided, this
    same "originate new work on a fresh process" pass ALSO discovers
    every ``MATERIALIZED`` (non-``TERMINAL``) origination-derived
    ``WorkNode`` not already known to ``governor``
    (``origination.projection.list_materialized_work_nodes()``) and adds
    + marks-ready each one, exactly like the pilot path already does --
    this is what makes ``run_origination_scan()``'s durable output
    actually reach the live governed DAG, closing D-PHASE2A-1's own
    explicitly-deferred gap. A node is marked READY regardless of
    whether it carries an ``owner_gate``: READY means dependency-ready,
    not owner-authorized (see ``continuation.select_next()``'s own
    docstring/ORCHAUT-010) -- ``_select_and_lease()`` and ``lease()``
    both independently, defense-in-depth fail closed on an owner-gated
    node before any autonomous execution, so marking it READY here does
    not grant anything; it only makes the node visible for the owner's
    own eventual lease with ``owner_grant=True``.

    A candidate whose ``add_node``/``mark_ready`` call fails (a
    package_id collision this pass's own dedup did not catch, or any
    other governed-DAG rejection) is simply skipped for THIS pass, not
    fatal to the others or to the pilot discovery above -- matching
    ``run_origination_scan()``'s own per-outcome isolation. The durable
    origination record is untouched either way; a later pass can pick
    it up once whatever blocked it resolves.

    ``lease_projection_store``, if provided, is load-bearing -- not
    best-effort. A corrupt store is not "no history": swallowing
    ``ProjectionError`` here used to re-add already-leased nodes and
    then crash inside ``governor.lease()`` -> ``project_grant()``. Fail
    closed the same way the LEASED-recovery path already does.

    Replay protection must not erase a completed node from the DAG.
    ``select_next()`` and ``lease()`` both treat a missing dependency as
    unsatisfied, so omitting a *corroborated* RELEASED package as a
    CERTIFIED witness stranded every later dependent. An ACTIVE row
    stays excluded so the LEASED restore path can rebuild it READY. A
    later content revision that materializes under the same
    ``package_id`` with a new ``base_pin`` (prior origination record
    already ``TERMINAL``) is not the leased revision and must still be
    ``add_node``/``mark_ready``'d.

    AS-LEASE-RELEASED-CERTIFIED-WITNESS-001: ``leases.json``
    ``status=RELEASED`` is recovery evidence, not a completion proof.
    ``DURABLE_PROJECTION_IS_AUTHORITY = NO``. A planted RELEASED row
    must not stamp CERTIFIED or unblock dependents. Only a RELEASED
    row whose ``lease_id`` also appears in durable
    ``LoopState.completed_lease_ids`` is a CERTIFIED witness.
    Uncorroborated RELEASED stays out of rediscovery.

    A materialized node whose ``base_pin`` is stale against live main is
    not marked READY -- leasing it would raise uncaught ``STALE_LEASE``.
    It is left durable for a later deliberate refresh; the rest of the
    discovery pass continues.
    """
    report = discover(inventory, trusted=trusted)
    node = governor.ingest_discovery(report)
    added: set[str] = set()
    if node is not None:
        governor.mark_ready(node.package_id)
        added.add(node.package_id)

    active_ids: set[str] = set()
    released_revisions: set[tuple[str, str]] = set()
    released_lease_ids: dict[tuple[str, str], set[str]] = {}
    completed_ids = frozenset(completed_lease_ids)
    if lease_projection_store is not None:
        try:
            ever_leased_projection = load_projection(lease_projection_store)
        except ProjectionError as exc:
            raise RehydrationError(str(exc), code=exc.code) from exc
        durable_sequence = 0
        for row in ever_leased_projection.leases:
            durable_sequence = max(durable_sequence, row.created_sequence)
            if row.released_sequence is not None:
                durable_sequence = max(durable_sequence, row.released_sequence)
        governor.adopt_durable_sequence(durable_sequence)
        active_ids = {row.package_id for row in active_rows(ever_leased_projection)}
        for row in ever_leased_projection.leases:
            if row.status != "RELEASED" or row.package_id in active_ids:
                continue
            key = (row.package_id, row.base_pin)
            released_revisions.add(key)
            released_lease_ids.setdefault(key, set()).add(row.lease_id)

    if origination_projection_store is not None:
        from project_atlas.orchestration.origination.projection import (
            OriginationProjectionError,
            list_materialized_work_nodes,
        )

        known = {item.package_id for item in governor.snapshot().nodes}
        known.update(active_ids)
        # AS-ORIGIN-MATERIALIZED-SUPERSESSION-001 (owner directive
        # D-ATLAS-ORIGINATION-MATERIALIZED-REVISION-SUPERSESSION §7):
        # `list_materialized_work_nodes()` is this bridge's only source of
        # "what origination-derived work is currently discoverable" -- it
        # now fails closed (`AMBIGUOUS_ACTIVE_REVISION`) rather than
        # silently returning two candidates sharing one `package_id`
        # (which this loop's own `known` dedup would otherwise resolve by
        # first-seen order -- exactly the "pick one arbitrarily" this
        # bridge must never do). Load-bearing for that specific failure:
        # an AMBIGUOUS_ACTIVE_REVISION store must stop this rehydration
        # pass, never silently proceed with a partial/arbitrary node
        # list -- caught and converted below. An unreadable/missing
        # store, by contrast, is NOT treated as corruption here (matching
        # `list_materialized_work_nodes()`'s own, narrower "never raises
        # except for ambiguity" contract, unchanged from before this
        # package): it returns no candidates, the same fail-open-toward-
        # doing-nothing-new posture this bridge already had before
        # `SUPERSEDED`/ambiguity existed at all -- this is not a new
        # correctness gap this package introduces, and widening it is a
        # separate, deliberate hardening decision, not made here.
        try:
            candidates = list_materialized_work_nodes(origination_projection_store)
        except OriginationProjectionError as exc:
            raise RehydrationError(str(exc), code=exc.code) from exc
        for candidate in candidates:
            if candidate.package_id in known:
                continue
            try:
                if (candidate.package_id, candidate.base_pin) in released_revisions:
                    key = (candidate.package_id, candidate.base_pin)
                    witness_ids = released_lease_ids.get(key, set())
                    corroborated = bool(completed_ids.intersection(witness_ids))
                    if corroborated:
                        governor.add_node(
                            candidate.model_copy(update={"state": NodeState.CERTIFIED})
                        )
                    # Uncorroborated RELEASED is not a completion witness
                    # and is not rediscovered as READY work.
                    known.add(candidate.package_id)
                    continue
                if candidate.base_pin != inventory.current_main:
                    continue
                governor.add_node(candidate)
                governor.mark_ready(candidate.package_id)
            except (GovernorError, IllegalTransitionError):
                continue
            known.add(candidate.package_id)
            added.add(candidate.package_id)
    return frozenset(added), report


def _restore_leased_node(
    governor: AutonomousGovernor,
    *,
    inventory: LiveInventory,
    package_id: str,
    lease_id: str,
    already_discovered_package_ids: frozenset[str],
    candidate_owner_gates: dict[str, OwnerGateKind | None],
    lease_projection_store: Path,
    origination_projection_store: Path | None = None,
    execution_host_class_override: ExecutionHostClass | None = None,
) -> None:
    origination_node: WorkNode | None = None
    if package_id != PILOT_PACKAGE_ID:
        # D-PHASE2A (ADR-033): the governor's one deterministic
        # inventory-only node factory (the pilot node) is not the only
        # source of durable truth any more. An origination-derived
        # package's full WorkNode -- mutation surface, acceptance
        # criteria, IV requirements, risk classification -- IS durably
        # persisted, by ``orchestration.origination.projection``, exactly
        # so it can be honestly rebuilt here instead of failing closed.
        if origination_projection_store is not None:
            from project_atlas.orchestration.origination.projection import (
                find_materialized_work_node,
                has_ever_had_multiple_revisions,
            )

            # AS-ORIGIN-MATERIALIZED-SUPERSESSION-001 independent-IV
            # finding (chatgpt-codex-connector, PR #677, P1): a durably
            # projected lease names only `package_id` -- never the
            # `origination_identity` it was actually granted against
            # (`WorkNode` carries no such field). If this package_id has
            # ever had more than one origination revision,
            # `find_materialized_work_node()`'s honestly-current result
            # cannot be proven to be the SAME revision this lease (or the
            # loop state naming it) was originally recorded against --
            # refuse rather than risk resuming a stale lease's authority
            # against a swapped, possibly more-restricted revision. See
            # `has_ever_had_multiple_revisions()`'s own docstring.
            if has_ever_had_multiple_revisions(origination_projection_store, package_id):
                raise RehydrationError(
                    f"package {package_id!r} has had more than one "
                    "origination revision -- a durably-leased lease cannot "
                    "be safely proven to still belong to the current "
                    "active revision, refusing to recover it rather than "
                    "risk resuming against a swapped revision's authority",
                    code="REVISION_IDENTITY_UNVERIFIABLE",
                )
            origination_node = find_materialized_work_node(
                origination_projection_store, package_id
            )
        if origination_node is None:
            # No pilot factory, and no durable origination record either
            # (or none was even configured for this caller) -- there is
            # genuinely nothing trustworthy to rebuild this package_id's
            # WorkNode from. Same fail-closed outcome as before this
            # parameter existed.
            raise RehydrationError(
                f"package {package_id!r} has no durable node definition to "
                f"rehydrate from",
                code="NODE_NOT_REHYDRATABLE",
            )

    try:
        projection = load_projection(lease_projection_store)
    except ProjectionError as exc:
        raise RehydrationError(str(exc), code=exc.code) from exc

    row = next((item for item in active_rows(projection) if item.lease_id == lease_id), None)
    if row is None:
        raise RehydrationError(
            f"lease {lease_id!r} for package {package_id!r} has no durable "
            f"active projection row -- cannot confirm it was ever really "
            f"granted",
            code="LEASE_NOT_PROJECTED",
        )
    if row.package_id != package_id:
        raise RehydrationError(
            f"projected lease {lease_id!r} belongs to package "
            f"{row.package_id!r}, not the loop-persisted {package_id!r}",
            code="FOREIGN_PACKAGE",
        )
    if row.base_pin != inventory.current_main:
        raise RehydrationError(
            f"projected lease {lease_id!r} is pinned to {row.base_pin!r}, "
            f"which is stale against the current main {inventory.current_main!r}",
            code="STALE_LEASE",
        )

    try:
        capabilities = tuple(AgentCapability(value) for value in row.capabilities)
    except ValueError as exc:
        raise RehydrationError(
            f"projected lease {lease_id!r} has an unrecognized capability value",
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
            # The only two AgentLease fields the durable projection does not
            # carry are always these two fixed constants -- see
            # leases.py:grant_lease, the sole place an AgentLease is ever
            # minted. A full lease is therefore losslessly reconstructable
            # from a ProjectedLease row plus these constants.
            expected_output="EVIDENCE_BUNDLE",
            expiry_or_terminal_condition="UNTIL_NODE_TERMINAL",
            active=True,
            sequence=row.created_sequence,
        )
    except ValidationError as exc:
        # ORCH001E-011 finding #5 (independent-IV): the projection file
        # already passed its own pydantic schema (`load_projection()`), but
        # a `ProjectedLease` row's fields are individually less constrained
        # than `AgentLease`'s (e.g. `authorized_paths`/`forbidden_paths`
        # content, id patterns). A tampered/edited-but-still-schema-valid
        # row can therefore fail *this* reconstruction. Fail closed with a
        # structured RehydrationError instead of letting a raw
        # ValidationError crash `run_governor_loop_tick()` uncaught.
        raise RehydrationError(
            f"projected lease {lease_id!r} does not reconstruct into a valid "
            f"AgentLease: {exc}",
            code="STATE_CORRUPT",
        ) from exc

    try:
        if package_id not in already_discovered_package_ids:
            # Neither discover()/ingest_discovery() (pilot) nor the
            # origination discovery pass (D-PHASE2A-2) (re)added this
            # node during THIS process's `_originate()` call -- build it
            # fresh via the same deterministic factory/durable record
            # `lease()` itself would have used, then walk it through the
            # real transition machinery (DISCOVERED -> READY) rather than
            # fabricating a node that starts life already in a state no
            # real code path produces.
            if origination_node is not None:
                # D-PHASE2A: the exact WorkNode a prior process
                # materialized and durably recorded -- not rebuilt from
                # inventory, because it cannot be (its mutation surface,
                # acceptance criteria, and risk classification came from
                # real project evidence, not from anything the live
                # inventory alone could ever reconstruct).
                node = origination_node
            else:
                if package_id not in candidate_owner_gates:
                    # discover()'s candidate table is the single source of
                    # truth for a pilot node's owner_gate (see discovery.py).
                    # If the package isn't even a known candidate there is
                    # nothing trustworthy to build the node's owner_gate from.
                    raise RehydrationError(
                        f"package {package_id!r} is not a known discovery candidate",
                        code="NODE_NOT_REHYDRATABLE",
                    )
                owner_gate = candidate_owner_gates[package_id]
                node = governor._pilot_node(inventory, owner_gate)
            governor.add_node(node)
            governor.mark_ready(package_id)

        rebuilt_node = next(
            (item for item in governor.snapshot().nodes if item.package_id == package_id),
            None,
        )
        if rebuilt_node is None:  # pragma: no cover - defensive, see add_node/mark_ready above
            raise RehydrationError(
                f"package {package_id!r} has no rebuilt node to validate the "
                f"projected lease against",
                code="NODE_NOT_REHYDRATABLE",
            )
        _validate_lease_row_against_node(
            row,
            lease,
            node=rebuilt_node,
            agents=governor.snapshot().agents,
        )

        governor.restore_lease(lease)
        if execution_host_class_override is not None:
            # AS-ORCH-LOCAL-DISPATCH-001 (PR-C): mirrors governor.lease()'s
            # own override-after-grant ordering (see that method's own
            # comment) -- applied only after the lease is fully restored
            # and durably confirmed, so this can never widen what the
            # restored node is authorized to do, only re-establish the
            # SAME in-memory fact (which node executes where) a live,
            # uninterrupted process would still have had. See
            # rehydrate_governor()'s own docstring for why re-supplying
            # this on every restart is not a new grant. Re-fetched fresh
            # from the governor (not the earlier `rebuilt_node` capture)
            # since `restore_lease()` itself just transitioned the
            # node's state (e.g. READY -> LEASED) -- overwriting with the
            # pre-transition snapshot would silently revert that.
            current = next(
                item for item in governor.snapshot().nodes if item.package_id == package_id
            )
            overridden = current.model_copy(
                update={"execution_host_class": execution_host_class_override}
            )
            governor._replace(overridden)
    except (GovernorError, IllegalTransitionError) as exc:
        # A prior process's real governor would have gone through these
        # exact same transitions successfully to reach LEASED in the first
        # place (add_node -> mark_ready -> restore_lease mirrors
        # discovered -> ready -> leased). If any step fails here against
        # the live inventory, the durable evidence and current reality
        # have genuinely diverged (e.g. a surface overlap that did not
        # exist before, or the target moved) -- fail closed rather than
        # leave the governor in a partially-rehydrated state.
        code = getattr(exc, "code", "REHYDRATION_FAILED_CLOSED")
        raise RehydrationError(str(exc), code=code) from exc


def _validate_lease_row_against_node(
    row: ProjectedLease,
    lease: AgentLease,
    *,
    node: WorkNode,
    agents: tuple[AgentRecord, ...],
) -> None:
    """ORCH001E-011 finding #3 (independent-IV): ``AutonomousGovernor.
    restore_lease()`` deliberately does not repeat ``grant_lease()``'s
    agent/capability/state/scope checks -- by its own documented contract,
    because a genuine prior ``lease()`` call already enforced them once.
    That makes this function, called just before ``restore_lease()``, the
    ONLY remaining checkpoint before a projected ``leases.json`` row --
    schema-valid, but possibly corrupted, hand-edited, or adversarial -- is
    trusted as a real historical grant. Re-run every check ``grant_lease()``
    performs (``leases.py``), against the CURRENT agent registry and the
    just-rebuilt ``node`` -- never the row's own unverified claims -- and
    fail closed on the first mismatch.
    """
    agent = next((item for item in agents if item.agent_id == row.agent_id), None)
    if agent is None:
        raise RehydrationError(
            f"projected lease {row.lease_id!r} references unregistered agent "
            f"{row.agent_id!r}",
            code="UNKNOWN_AGENT",
        )
    if not agent.available:
        raise RehydrationError(
            f"projected lease {row.lease_id!r} references unavailable agent "
            f"{row.agent_id!r}",
            code="AGENT_UNAVAILABLE",
        )
    have = frozenset(agent.capabilities)
    if not all(item in have for item in node.agent_capabilities_required):
        raise RehydrationError(
            f"projected lease {row.lease_id!r} agent {row.agent_id!r} does not "
            f"hold the capabilities node {node.package_id!r} requires",
            code="CAPABILITY_MISMATCH",
        )
    if lease.capabilities != node.agent_capabilities_required:
        raise RehydrationError(
            f"projected lease {row.lease_id!r} recorded capabilities do not "
            f"match node {node.package_id!r}'s required capabilities",
            code="CAPABILITY_MISMATCH",
        )
    if row.start_state != NodeState.READY:
        raise RehydrationError(
            f"projected lease {row.lease_id!r} records start_state "
            f"{row.start_state.value!r}: only READY is a valid lease start state",
            code="INVALID_START_STATE",
        )
    surface = frozenset(node.mutation_surface.paths)
    extra_authorized = frozenset(row.authorized_paths) - surface
    if extra_authorized:
        raise RehydrationError(
            f"projected lease {row.lease_id!r} authorized_paths "
            f"{sorted(extra_authorized)!r} exceed node {node.package_id!r}'s "
            f"mutation surface {sorted(surface)!r}",
            code="SCOPE_EXPANSION",
        )
    # grant_lease()'s own default_forbidden = ("main", "projects")
    # (leases.py) is the baseline protection every genuine lease carries
    # unless a caller explicitly narrows it -- no call site in this
    # codebase ever does. A row whose forbidden_paths dropped that
    # baseline would let a rehydrated lease legitimately touch surfaces a
    # real grant never could have.
    baseline_forbidden = frozenset(("main", "projects"))
    if not baseline_forbidden <= frozenset(row.forbidden_paths):
        raise RehydrationError(
            f"projected lease {row.lease_id!r} forbidden_paths "
            f"{sorted(row.forbidden_paths)!r} do not include the baseline "
            f"protected paths {sorted(baseline_forbidden)!r}",
            code="SCOPE_EXPANSION",
        )
