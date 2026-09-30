"""CLI runners for the autonomous governor. Not dispatch and not merge."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

from pydantic import ValidationError

from project_atlas.orchestration.autonomy.discovery import (
    DiscoveryError,
    collect_live_inventory,
    discover,
)
from project_atlas.orchestration.autonomy.governor import AutonomousGovernor, run_live_pilot
from project_atlas.orchestration.autonomy.lease_projection import (
    RELATIVE_DEFAULT as LEASE_PROJECTION_RELATIVE_DEFAULT,
)
from project_atlas.orchestration.autonomy.lease_projection import (
    ProjectionError,
)
from project_atlas.orchestration.autonomy.lease_recovery import (
    LeaseRecoveryError,
    release_stalled_lease_after_exhausted_dispatch,
)
from project_atlas.orchestration.autonomy.local_dispatch_port import (
    LocalDispatchError,
    LocalProcessDispatchPort,
)
from project_atlas.orchestration.autonomy.loop import (
    STATE_DIR_RELATIVE,
    AutonomousLoop,
    LoopError,
)
from project_atlas.orchestration.autonomy.models import (
    AUTONOMY_PACKAGE_ID,
    BOOTSTRAP_MAIN,
    BOOTSTRAP_TREE,
    ExecutionHostClass,
    LiveInventory,
    NodeState,
    TrustCheckpointProof,
    TrustedAnchorRecord,
)
from project_atlas.orchestration.autonomy.rehydration import RehydrationError, rehydrate_governor
from project_atlas.orchestration.autonomy.trust import (
    LiveGitObserver,
    TrustError,
    TrustRepairCarrier,
    advance_via_checkpoint_recovery,
    initialize_store,
    load_runtime_anchor,
    load_shipped_initial_anchor,
    normalize_repository_identity,
    observe_repository_identity,
    require_trust_current_for_merge,
)
from project_atlas.orchestration.local_process_transport import LocalProcessExecutorConfig
from project_atlas.orchestration.origination.projection import (
    RELATIVE_DEFAULT as ORIGINATION_PROJECTION_RELATIVE_DEFAULT,
)
from project_atlas.orchestration.origination.projection import sync_terminal_governed_states

EXIT_OK = 0
EXIT_ERROR = 1


def _fail_closed(detail: str, *, blocker: str = "TRUST_UNVERIFIABLE") -> dict[str, object]:
    return {
        "schema_version": 1,
        "package_id": AUTONOMY_PACKAGE_ID,
        "case": "A-B",
        "blocker": blocker,
        "detail": detail,
        "target_moved": blocker == "TARGET_MOVED",
        "trust_state": "UNVERIFIABLE" if blocker != "TARGET_MOVED" else "TARGET_MOVED",
        "static_bootstrap_pin_as_runtime_authority": False,
        "merge_authorized": False,
        "execution_authorized": False,
    }


def _load_trusted(
    *,
    trust_store: Path | None,
    allow_shipped: bool,
    explicit: TrustedAnchorRecord | None = None,
) -> TrustedAnchorRecord:
    return load_runtime_anchor(
        store=trust_store,
        explicit=explicit,
        allow_shipped=allow_shipped and trust_store is None and explicit is None,
    )


def _load_inventory(path: Path | None, repo: Path) -> LiveInventory:
    if path is None:
        return collect_live_inventory(repo)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("inventory must be a JSON object")
    return LiveInventory.model_validate(payload)


def _node_counts(nodes: tuple[object, ...]) -> dict[str, int]:
    ready = 0
    owner_held = 0
    blocked = 0
    for node in nodes:
        state = getattr(node, "state", None)
        if state is NodeState.READY:
            ready += 1
        elif state is NodeState.OWNER_HELD:
            owner_held += 1
        elif state is NodeState.BLOCKED:
            blocked += 1
    return {
        "ready_node_count": ready,
        "owner_held_node_count": owner_held,
        "blocked_node_count": blocked,
    }


def run_governor_status(
    *,
    root: Path,
    trust_store: Path | None = None,
) -> tuple[dict[str, object], int]:
    try:
        trusted = _load_trusted(trust_store=trust_store, allow_shipped=True)
        inventory = collect_live_inventory(root)
    except TrustError as exc:
        return _fail_closed(str(exc), blocker=exc.code), EXIT_ERROR
    except DiscoveryError as exc:
        return _fail_closed(str(exc), blocker="DISCOVERY_UNOBSERVABLE"), EXIT_ERROR
    governor = AutonomousGovernor(
        current_main=inventory.current_main,
        current_tree=inventory.current_tree,
        trusted_anchor=trusted,
    )
    snapshot = governor.snapshot()
    plan = governor.plan()
    counts = _node_counts(snapshot.nodes)
    next_eligible = plan.what_can_run_now[0] if plan.what_can_run_now else None
    next_state = None
    next_gate = None
    if next_eligible is not None:
        node = next(item for item in snapshot.nodes if item.package_id == next_eligible)
        next_state = node.state.value
        next_gate = node.owner_gate.value if node.owner_gate is not None else None
    report: dict[str, object] = {
        "schema_version": 1,
        "package_id": AUTONOMY_PACKAGE_ID,
        "observed_main": inventory.current_main,
        "observed_tree": inventory.current_tree,
        "current_main": inventory.current_main,
        "current_tree": inventory.current_tree,
        "bootstrap_main": BOOTSTRAP_MAIN,
        "bootstrap_tree": BOOTSTRAP_TREE,
        "trusted_runtime_main": trusted.trusted_main,
        "trusted_runtime_tree": trusted.trusted_tree,
        "target_moved": snapshot.target_moved,
        "trust_state": snapshot.trust_state.value,
        "static_bootstrap_pin_as_runtime_authority": False,
        "worktree_status": inventory.worktree_status,
        "open_relevant_prs": list(inventory.open_relevant_prs),
        "active_successor_packages": list(inventory.active_successor_packages),
        "r2_created": inventory.r2_created,
        "r7_created": inventory.r7_created,
        "authentic_r6_resumed": inventory.authentic_r6_resumed,
        "as_orch_001e_started": inventory.as_orch_001e_started,
        "next_eligible_node": next_eligible,
        "next_eligible_node_state": next_state,
        "next_eligible_node_owner_gate": next_gate,
        **counts,
        "plan": plan.model_dump(mode="json"),
        "merge_authorized": False,
        "execution_authorized": False,
    }
    return report, EXIT_ERROR if snapshot.target_moved else EXIT_OK


def run_governor_discover(
    *,
    root: Path,
    inventory_path: Path | None = None,
    trust_store: Path | None = None,
) -> tuple[dict[str, object], int]:
    try:
        trusted = _load_trusted(trust_store=trust_store, allow_shipped=True)
        inventory = _load_inventory(inventory_path, root)
    except TrustError as exc:
        return _fail_closed(str(exc), blocker=exc.code), EXIT_ERROR
    except DiscoveryError as exc:
        return {
            "schema_version": 1,
            "package_id": AUTONOMY_PACKAGE_ID,
            "case": "A-B",
            "blocker": "DISCOVERY_UNOBSERVABLE",
            "detail": str(exc),
            "static_bootstrap_pin_as_runtime_authority": False,
        }, EXIT_ERROR
    report = discover(inventory, trusted=trusted)
    payload = report.model_dump(mode="json")
    payload["static_bootstrap_pin_as_runtime_authority"] = False
    payload["trusted_runtime_main"] = trusted.trusted_main
    payload["trusted_runtime_tree"] = trusted.trusted_tree
    payload["next_eligible_node"] = report.selected_package_id
    return payload, EXIT_ERROR if report.case == "A-B" else EXIT_OK


def run_governor_pilot(
    *,
    root: Path,
    evidence_dir: Path | None = None,
    inventory_path: Path | None = None,
    trust_store: Path | None = None,
    stdin: TextIO | None = None,
) -> tuple[dict[str, object], int]:
    del stdin
    try:
        trusted = _load_trusted(trust_store=trust_store, allow_shipped=True)
        if inventory_path is not None:
            inventory = _load_inventory(inventory_path, root)
            governor = AutonomousGovernor(
                current_main=inventory.current_main,
                current_tree=inventory.current_tree,
                trusted_anchor=trusted,
            )
            result = governor.run_controlled_pilot(
                inventory,
                branch="feat/as-orch-autonomy-001",
                worktree=str(root),
                evidence_dir=evidence_dir,
            )
            return result, EXIT_OK
        result = run_live_pilot(root, evidence_dir=evidence_dir, trusted_anchor=trusted)
        return result, EXIT_OK
    except TrustError as exc:
        return _fail_closed(str(exc), blocker=exc.code), EXIT_ERROR
    except DiscoveryError as exc:
        return {
            "schema_version": 1,
            "package_id": AUTONOMY_PACKAGE_ID,
            "case": "A-B",
            "blocker": "DISCOVERY_UNOBSERVABLE",
            "detail": str(exc),
            "merge_authorized": False,
            "static_bootstrap_pin_as_runtime_authority": False,
        }, EXIT_ERROR


def run_governor_loop_tick(
    *,
    root: Path,
    trust_store: Path | None = None,
    loop_store: Path | None = None,
    origination_store: Path | None = None,
    local_execution_config: LocalProcessExecutorConfig | None = None,
    local_execution_argv_template: tuple[str, ...] | None = None,
) -> tuple[dict[str, object], int]:
    """One 001E tick. Never merges or grants owner authority.

    D-PHASE2A-2: ``origination_store`` (``None``, the default, resolves
    to ``orchestration.origination.projection.RELATIVE_DEFAULT`` under
    ``root`` -- the same ``loop_store``/``lease_store``-style "``None``
    means use the default path" convention this module already uses,
    not an opt-out) closes the gap D-PHASE2A-1 explicitly deferred --
    what ``run_origination_scan()`` durably materializes now actually
    reaches this live tick's governor (via ``rehydrate_governor()``'s
    origination-discovery pass) and, once a governed node it originated
    reaches ``dag.TERMINAL_STATES``, the durable origination record is
    synced back to ``TERMINAL`` so a later successor scan correctly
    excludes it. This is safe by construction for every existing caller
    that has never run an origination scan: an origination store with no
    file on disk yet loads as an empty projection (zero candidates,
    zero behavior change) -- there is no separate opt-out parameter
    because none is needed.

    AS-ORCH-LOCAL-DISPATCH-001 (PR-C): ``local_execution_config`` /
    ``local_execution_argv_template`` are both ``None`` by default -- the
    real ``atlas`` CLI does not pass them today, so every existing
    invocation of this function is completely unaffected (a newly-leased
    node still executes exactly as before, via the pre-existing
    ``IN_PROCESS`` metadata-bundle path). Passing BOTH explicitly is the
    only way to opt in: an operator-configured, already-``enabled=True``
    ``LocalProcessExecutorConfig`` (PR-B's own disabled-by-default gate)
    plus a non-empty, fixed argv template. Only then does a newly-leased
    node route through ``LocalProcessDispatchPort`` -- a real, scope-
    audited local subprocess -- instead of the metadata-only stand-in.
    Passing only one of the two is a configuration error, not a partial
    enablement; there is no implicit fallback to network/cloud/billing
    execution either way.
    """
    try:
        if (local_execution_config is None) != (local_execution_argv_template is None):
            raise LocalDispatchError(
                "local_execution_config and local_execution_argv_template must "
                "both be provided, or neither -- partial local-execution "
                "configuration is refused rather than silently ignored",
                code="LOCAL_EXECUTION_PARTIALLY_CONFIGURED",
            )
        trusted = _load_trusted(trust_store=trust_store, allow_shipped=trust_store is None)
        inventory = collect_live_inventory(root)
        lease_store = root / LEASE_PROJECTION_RELATIVE_DEFAULT
        origin_store = origination_store or (root / ORIGINATION_PROJECTION_RELATIVE_DEFAULT)
        governor = AutonomousGovernor(
            current_main=inventory.current_main,
            current_tree=inventory.current_tree,
            trusted_anchor=trusted,
            lease_projection_store=lease_store,
            # Owner directive D-ATLAS-PR678-CASE-A-LEASE-AUTHORITY-CLOSURE
            # §8: the SAME `origin_store` this tick already resolves for
            # `rehydrate_governor()` below. Wiring it here is what makes
            # `governor.lease()` able to refuse an origination-derived
            # node whose revision is no longer current -- including
            # inside a single long-lived process, with no restart and no
            # base_pin movement. This is the one real autonomous
            # lease-granting path; the pilot-only entry points do not
            # need it (a pilot node carries no origination provenance).
            origination_projection_store=origin_store,
        )
        store = loop_store or (root / STATE_DIR_RELATIVE)
        # AS-ORCH-LOCAL-DISPATCH-001 (PR-C review finding, chatgpt-codex-
        # connector): the dispatch port (and its execution_host_class
        # override) must exist BEFORE rehydrate_governor() runs, not
        # after -- rehydrate_governor() is the one place that can
        # reconstruct a node/lease for the DISPATCHING/AWAITING_RESULT
        # phases a real crash mid-dispatch leaves on disk (see its own
        # docstring), and doing so requires re-applying this SAME
        # override to the reconstructed node. Constructing the port only
        # afterward made that reconstruction -- and therefore this
        # port's own recover()/find_active_dispatch_id() -- permanently
        # unreachable through this real production entrypoint.
        dispatch_port: LocalProcessDispatchPort | None = None
        host_class_override: ExecutionHostClass | None = None
        if local_execution_config is not None and local_execution_argv_template is not None:
            dispatch_port = LocalProcessDispatchPort(
                config=local_execution_config,
                argv_template=local_execution_argv_template,
            )
            host_class_override = ExecutionHostClass.LOCAL_PROCESS
        # ORCH001E-011: a fresh AutonomousGovernor starts with an empty node
        # list on every CLI invocation, while `store` may already hold
        # LoopState from a prior process (LEASED, mid-execution, ...). This
        # rehydration pass must run before constructing AutonomousLoop, so
        # the node lookups tick()/recover() perform find what a prior
        # process would have found -- or this call raises RehydrationError
        # first and neither loop nor governor is ever handed inconsistent
        # state.
        rehydrate_governor(
            governor,
            inventory=inventory,
            trusted=trusted,
            loop_store=store,
            lease_projection_store=lease_store,
            origination_projection_store=origin_store,
            execution_host_class_override=host_class_override,
        )
        loop = AutonomousLoop(
            governor=governor,
            trusted=trusted,
            store=store,
            root=root,
            dispatch=dispatch_port,
            execution_host_class_override=host_class_override,
        )
        result = loop.tick()
        if result.phase.value == "IDLE":
            # ORCH001E-011 finding #2 (independent-IV): now that this CLI
            # entrypoint enables the durable lease projection
            # (`lease_projection_store=lease_store` above),
            # `AutonomousLoop.apply_observed_result()` returning the loop to
            # IDLE after a terminal completion does NOT itself release the
            # lease it just finished -- unlike `run_controlled_pilot()`.
            # The only way the loop phase is IDLE with a still-`active`
            # governor lease is exactly that just-completed case (IDLE is
            # otherwise the loop's untouched starting phase, when there is
            # no lease at all yet); release every such lease, both the
            # in-memory bookkeeping and the durable projection, so a later
            # lease for the same package/worker is not rejected as
            # DUPLICATE_ACTIVE_LEASE/FOREIGN_WORKER or mistaken for a
            # replay.
            for existing_lease in governor.snapshot().leases:
                if existing_lease.active:
                    governor.release_lease(existing_lease.lease_id)
        # D-PHASE2A-2: write-back half of the origination <-> governor
        # bridge. Runs on every tick's final observed state (including
        # phases where the loop stopped without leasing/dispatching
        # anything this call), which is correct and cheap: it is a pure
        # read+compare over already-in-memory governor nodes, and
        # sync_terminal_governed_states() itself no-ops immediately if
        # no node is CLOSED yet.
        sync_terminal_governed_states(origin_store, governor.snapshot().nodes)
        payload = result.model_dump(mode="json")
        payload["package_id"] = "AS-ORCH-001E"
        payload["merge_authorized"] = False
        payload["execution_authorized"] = False
        return payload, EXIT_OK if result.phase.value != "FAILED_CLOSED" else EXIT_ERROR
    except (
        TrustError,
        DiscoveryError,
        LoopError,
        RehydrationError,
        ProjectionError,
        LocalDispatchError,
    ) as exc:
        code = getattr(exc, "code", "LOOP_FAILED_CLOSED")
        return _fail_closed(str(exc), blocker=code), EXIT_ERROR


def observed_repository_identity(remote_url: str) -> str:
    """CLI helper for evidence capture. Not an authority grant."""
    return normalize_repository_identity(remote_url)


def run_trust_checkpoint(
    *,
    root: Path,
    trust_store: Path,
    proof_path: Path,
    bootstrap_from_shipped: bool = False,
) -> tuple[dict[str, object], int]:
    """AS-ORCH-AUTONOMY-001 stale-runtime-anchor CHECKPOINT RECOVERY.

    Deliberate, explicit operator surface -- ``--trust-store`` and
    ``--proof`` are both REQUIRED (no implicit target from currently
    observed main). Never invoked automatically by the governor or by
    normal loop-tick observation; a genuine, separately-authored,
    owner-signed ``TrustCheckpointProof`` is always required. See
    ``trust.py``'s ``advance_via_checkpoint_recovery()`` for the full
    verification contract, and its module docstring for why this is a
    distinct capability from ordinary single-hop
    ``advance_trusted_anchor()`` advancement (which this command never
    touches, weakens, or bypasses).

    ``bootstrap_from_shipped``: only relevant the FIRST time a runtime
    store is used at ``trust_store`` -- initializes it from the verified
    shipped anchor via the existing ``initialize_store()`` contract
    (refuses to overwrite a differing existing record; a no-op if the
    store already holds the identical record). Does not itself advance
    anything; the checkpoint proof is still separately verified and
    applied after.
    """
    try:
        if bootstrap_from_shipped:
            initialize_store(trust_store, load_shipped_initial_anchor())
        current = load_runtime_anchor(store=trust_store)
        try:
            raw = json.loads(proof_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TrustError(
                f"checkpoint proof file could not be read: {exc.__class__.__name__}",
                code="PROOF_UNREADABLE",
            ) from exc
        if not isinstance(raw, dict):
            raise TrustError("checkpoint proof must be a JSON object", code="PROOF_INVALID")
        try:
            proof = TrustCheckpointProof.model_validate(raw)
        except ValidationError as exc:
            raise TrustError(
                f"checkpoint proof is schema-invalid: {exc}", code="PROOF_INVALID"
            ) from exc
        repository_identity = observe_repository_identity(root)
        new_record = advance_via_checkpoint_recovery(
            current,
            proof,
            LiveGitObserver(root),
            store=trust_store,
            expected_repository_identity=repository_identity,
        )
    except (TrustError, OSError) as exc:
        # OSError (IV finding, PR #664): store I/O -- e.g. a permission-
        # denied writing to --trust-store -- previously escaped uncaught
        # here instead of producing the same clean, fail-closed JSON
        # report every other genuine problem in this command already
        # gets.
        code = getattr(exc, "code", None) or (
            "CHECKPOINT_STORE_IO_ERROR" if isinstance(exc, OSError) else "CHECKPOINT_DENIED"
        )
        return {
            "schema_version": 1,
            "package_id": AUTONOMY_PACKAGE_ID,
            "case": "TRUST-CHECKPOINT",
            "checkpoint_advanced": False,
            "blocker": code,
            "detail": str(exc),
            "merge_authorized": False,
            "execution_authorized": False,
        }, EXIT_ERROR
    return {
        "schema_version": 1,
        "package_id": AUTONOMY_PACKAGE_ID,
        "case": "TRUST-CHECKPOINT",
        "checkpoint_advanced": True,
        "previous_anchor": {
            "trusted_main": current.trusted_main,
            "trusted_tree": current.trusted_tree,
            "sequence": current.sequence,
        },
        "new_anchor": {
            "trusted_main": new_record.trusted_main,
            "trusted_tree": new_record.trusted_tree,
            "sequence": new_record.sequence,
            "advancement_reason": new_record.advancement_reason.value,
        },
        "first_parent_hop_count": proof.first_parent_hop_count,
        "first_parent_chain_digest": proof.first_parent_chain_digest,
        "evidence_reference": proof.evidence_reference,
        "independent_verification": proof.independent_verification,
        "merge_authorized": False,
        "execution_authorized": False,
    }, EXIT_OK


def run_trust_check_before_merge(
    *,
    root: Path,
    trust_store: Path,
    repair_source_pr: int | None = None,
    repair_reason: str | None = None,
) -> tuple[dict[str, object], int]:
    """M2: trust/main integration interlock, real operator surface.

    Reviewer finding (Codex, PR #672 -- "the new precondition is never
    invoked by production code"): ``trust.py``'s
    ``require_trust_current_for_merge()`` existed only as a library
    function with no real call site. This command IS the call site --
    the actual thing to run before ``gh pr merge`` (or equivalent), same
    standalone-entrypoint precedent as ``trust-checkpoint`` (``src/
    project_atlas/cli.py`` stays frozen; see
    ``_build_standalone_parser()``'s own docstring for why).

    ``--repair-pr``/``--repair-reason``: both required together (never
    one alone) to construct the one narrow, explicit
    ``TrustRepairCarrier`` exception -- a PR whose entire purpose is
    restoring trust synchronization itself. Omit both for the routine
    case (trust must already be current). This command never mutates
    trust state either way -- it only reports whether the precondition
    holds.
    """
    carrier: TrustRepairCarrier | None = None
    try:
        if repair_source_pr is not None or repair_reason is not None:
            if repair_source_pr is None or repair_reason is None:
                raise TrustError(
                    "--repair-pr and --repair-reason must both be supplied together",
                    code="INTERLOCK_MISUSE",
                )
            carrier = TrustRepairCarrier(source_pr=repair_source_pr, reason=repair_reason)
        repository_identity = observe_repository_identity(root)
        anchor = require_trust_current_for_merge(
            store=trust_store,
            topology=LiveGitObserver(root),
            expected_repository_identity=repository_identity,
            trust_repair_carrier=carrier,
        )
    except (TrustError, OSError) as exc:
        code = getattr(exc, "code", None) or (
            "TRUST_CHECK_STORE_IO_ERROR"
            if isinstance(exc, OSError)
            else "TRUST_NOT_CURRENT_FOR_MERGE"
        )
        return {
            "schema_version": 1,
            "package_id": AUTONOMY_PACKAGE_ID,
            "case": "TRUST-CHECK-BEFORE-MERGE",
            "merge_permitted": False,
            "blocker": code,
            "detail": str(exc),
            "merge_authorized": False,
            "execution_authorized": False,
        }, EXIT_ERROR
    return {
        "schema_version": 1,
        "package_id": AUTONOMY_PACKAGE_ID,
        "case": "TRUST-CHECK-BEFORE-MERGE",
        "merge_permitted": True,
        "trusted_main": anchor.trusted_main,
        "trusted_tree": anchor.trusted_tree,
        "sequence": anchor.sequence,
        "repair_carrier_used": carrier is not None,
        "merge_authorized": False,
        "execution_authorized": False,
    }, EXIT_OK


def run_release_stalled_lease(
    *,
    root: Path,
    lease_id: str,
    loop_store: Path | None = None,
    lease_projection_store: Path | None = None,
) -> tuple[dict[str, object], int]:
    """AS-ORCH-LEASE-RECOVERY-001, real operator surface (same
    standalone-entrypoint precedent as ``trust-checkpoint``/
    ``trust-check-before-merge``): the one legitimate way to mark a lease
    ``ABANDONED`` after its owning loop reached a genuine ``RESOURCE_
    BOUNDARY`` stop with every dispatch attempt exhausted in real failure.
    See ``lease_recovery.py``'s own module docstring for the incident
    this closes and why evidence-gating (not a caller-supplied claim) is
    the only thing that makes it safe to expose here. Never merges, never
    grants execution/merge authority, never touches trust or
    ``origination.json`` directly.
    """
    resolved_loop_store = loop_store or (root / STATE_DIR_RELATIVE)
    resolved_lease_store = lease_projection_store or (root / LEASE_PROJECTION_RELATIVE_DEFAULT)
    try:
        result = release_stalled_lease_after_exhausted_dispatch(
            root,
            lease_id=lease_id,
            loop_store=resolved_loop_store,
            lease_projection_store=resolved_lease_store,
        )
    except (LeaseRecoveryError, LoopError, ProjectionError, LocalDispatchError) as exc:
        code = getattr(exc, "code", None) or "LEASE_RECOVERY_ERROR"
        return {
            "schema_version": 1,
            "package_id": AUTONOMY_PACKAGE_ID,
            "case": "RELEASE-STALLED-LEASE",
            "lease_id": lease_id,
            "abandoned": False,
            "blocker": code,
            "detail": str(exc),
            "merge_authorized": False,
            "execution_authorized": False,
        }, EXIT_ERROR
    return {
        "schema_version": 1,
        "package_id": AUTONOMY_PACKAGE_ID,
        "case": "RELEASE-STALLED-LEASE",
        **result,
        "merge_authorized": False,
        "execution_authorized": False,
    }, EXIT_OK


def _build_standalone_parser() -> argparse.ArgumentParser:
    """Standalone parser for ``trust-checkpoint``.

    ``src/project_atlas/cli.py`` (the ``atlas`` top-level entry point,
    where every OTHER orchestrator subcommand -- ``governor-status``,
    ``governor-pilot``, etc. -- is wired) is currently frozen: its own
    Golden-Demo isolation guard (``tests/unit/
    test_atlas3_demo_isolation_001.py::test_cli_mutation_is_additive_only``)
    requires any diff touching that file to land within the
    ``register_atlas3_parsers``/``dispatch_atlas3`` extension points,
    which this trust/security-governance change has no legitimate reason
    to route through. A prior, unrelated change (DOGFOOD-001, see
    WORKLOG.md) hit the same wall and dropped its own ``cli.py`` edit
    rather than force an unrelated exception through that guard; this
    follows the same precedent. This standalone entrypoint is the
    deliberate, real operator surface until ``cli.py``'s freeze lifts:
    ``python -m project_atlas.orchestration.autonomy.cli trust-checkpoint
    --trust-store <path> --proof <path> [--root <path>]
    [--bootstrap-from-shipped]``. Not a hidden helper -- ``--trust-store``
    and ``--proof`` are both required, machine-readable JSON is printed
    to stdout, and the exit code follows this module's own EXIT_OK/
    EXIT_ERROR convention exactly like every other command here.
    """
    parser = argparse.ArgumentParser(prog="python -m project_atlas.orchestration.autonomy.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    checkpoint = sub.add_parser(
        "trust-checkpoint",
        help=(
            "ONE-TIME owner-authorized stale-runtime-anchor recovery via an "
            "explicit TrustCheckpointProof. Never invoked automatically. "
            "Does not merge."
        ),
    )
    checkpoint.add_argument(
        "--root", type=Path, default=None, help="Repository root (default: cwd)."
    )
    checkpoint.add_argument(
        "--trust-store",
        type=Path,
        required=True,
        help="Durable runtime trusted-anchor store (required -- never implicit).",
    )
    checkpoint.add_argument(
        "--proof",
        type=Path,
        required=True,
        help="Path to a TrustCheckpointProof JSON file (required -- never implicit).",
    )
    checkpoint.add_argument(
        "--bootstrap-from-shipped",
        action="store_true",
        help=(
            "First-time only: initialize --trust-store from the verified "
            "shipped anchor if it has no current record yet."
        ),
    )
    check_before_merge = sub.add_parser(
        "trust-check-before-merge",
        help=(
            "M2 trust/main integration interlock: fail closed unless the "
            "trust store's anchor matches live main, or an explicit "
            "--repair-pr/--repair-reason justification is supplied. Never "
            "invoked automatically. Does not merge; does not mutate trust."
        ),
    )
    check_before_merge.add_argument(
        "--root", type=Path, default=None, help="Repository root (default: cwd)."
    )
    check_before_merge.add_argument(
        "--trust-store",
        type=Path,
        required=True,
        help="Durable runtime trusted-anchor store (required -- never implicit).",
    )
    check_before_merge.add_argument(
        "--repair-pr",
        type=int,
        default=None,
        help="Source PR number for the one narrow TrustRepairCarrier exception "
        "(requires --repair-reason too).",
    )
    check_before_merge.add_argument(
        "--repair-reason",
        type=str,
        default=None,
        help="Non-empty reason for the TrustRepairCarrier exception (requires --repair-pr too).",
    )
    release_stalled = sub.add_parser(
        "release-stalled-lease",
        help=(
            "AS-ORCH-LEASE-RECOVERY-001: mark a lease ABANDONED after its "
            "loop reached a genuine RESOURCE_BOUNDARY stop with every real "
            "dispatch attempt exhausted in failure. Refuses without real, "
            "on-disk evidence. Never merges; never grants authority."
        ),
    )
    release_stalled.add_argument(
        "--root", type=Path, default=None, help="Repository root (default: cwd)."
    )
    release_stalled.add_argument(
        "--lease-id",
        type=str,
        required=True,
        help="The stalled lease to evaluate for abandonment (required -- never implicit).",
    )
    release_stalled.add_argument(
        "--loop-store",
        type=Path,
        default=None,
        help="Durable loop-state store (default: <root>/.atlas/orchestration/autonomy/loop).",
    )
    release_stalled.add_argument(
        "--lease-store",
        type=Path,
        default=None,
        help="Durable lease-projection store (default: <root>/.atlas/orchestration/autonomy).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Standalone entrypoint. See ``_build_standalone_parser()``."""
    args = _build_standalone_parser().parse_args(argv)
    if args.command == "trust-checkpoint":
        report, exit_code = run_trust_checkpoint(
            root=Path(args.root or Path.cwd()),
            trust_store=args.trust_store,
            proof_path=args.proof,
            bootstrap_from_shipped=bool(args.bootstrap_from_shipped),
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return exit_code
    if args.command == "trust-check-before-merge":
        report, exit_code = run_trust_check_before_merge(
            root=Path(args.root or Path.cwd()),
            trust_store=args.trust_store,
            repair_source_pr=args.repair_pr,
            repair_reason=args.repair_reason,
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return exit_code
    if args.command == "release-stalled-lease":
        report, exit_code = run_release_stalled_lease(
            root=Path(args.root or Path.cwd()),
            lease_id=args.lease_id,
            loop_store=args.loop_store,
            lease_projection_store=args.lease_store,
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return exit_code
    return 2  # argparse usage error convention, matches the top-level atlas CLI


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
