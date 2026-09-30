"""Single logical autonomous governor.

Answers WHAT_CAN_RUN NOW / WAIT / PARALLEL / OWNER. Executes only the
in-process pilot host. Never merges, never starts 001E, never mutates #396,
and never auto-dispatches a next 001D hop.
"""

from __future__ import annotations

from pathlib import Path

from project_atlas.orchestration.autonomy.continuation import select_next
from project_atlas.orchestration.autonomy.dag import IllegalTransitionError, apply_transition
from project_atlas.orchestration.autonomy.discovery import collect_live_inventory, discover
from project_atlas.orchestration.autonomy.evidence import make_bundle, write_bundle
from project_atlas.orchestration.autonomy.iv_routing import IvRoutingError, route_iv
from project_atlas.orchestration.autonomy.lease_projection import project_grant, project_release
from project_atlas.orchestration.autonomy.leases import grant_lease, release_lease
from project_atlas.orchestration.autonomy.models import (
    AUTONOMY_PACKAGE_ID,
    BOOTSTRAP_MAIN,
    BOOTSTRAP_TREE,
    ORIGINATION_SURFACE_SEMANTIC,
    PILOT_PACKAGE_ID,
    AgentCapability,
    AgentLease,
    AgentRecord,
    CertificationState,
    CiState,
    DagEdge,
    DiscoveryReport,
    EvidenceBundle,
    ExecutionHostClass,
    ExecutionPlan,
    GovernorState,
    IvRequirements,
    IvState,
    LiveInventory,
    MutationSurface,
    NodeState,
    OverlapState,
    OwnerGateKind,
    RiskTag,
    StopReason,
    TransitionRecord,
    TrustedAnchorRecord,
    WorkNode,
)
from project_atlas.orchestration.autonomy.overlap import overlap_gate, would_overlap
from project_atlas.orchestration.autonomy.owner_gates import OwnerGateError, require_owner
from project_atlas.orchestration.autonomy.remediation import (
    RemediationExhausted,
    can_remediate,
    consume_remediation_cycle,
)
from project_atlas.orchestration.autonomy.trust import (
    TrustError,
    classify_observation,
    evaluate_target_moved,
    load_runtime_anchor,
)

DEFAULT_AGENTS: tuple[AgentRecord, ...] = (
    AgentRecord(
        agent_id="governor-pilot-local",
        capabilities=(
            AgentCapability.DISCOVER,
            AgentCapability.IMPLEMENT,
            AgentCapability.REMEDIATE,
        ),
        available=True,
    ),
    AgentRecord(
        agent_id="governor-pilot-iv",
        capabilities=(AgentCapability.VERIFY, AgentCapability.ADVERSARIAL_REVIEW),
        available=True,
    ),
)


class GovernorError(ValueError):
    code = "GOVERNOR_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


# D-PHASE2A-1a: a dependency counts as satisfied once its own WorkNode has
# independently passed verification (CERTIFIED) or moved further along the
# owner/merge pipeline. It does NOT need to reach MERGED first -- a
# dependent should not have to wait on an owner-gated merge timeline for
# work whose correctness is already independently verified. CERTIFIED !=
# MERGED still holds; this is a leasing precondition, not a merge claim.
_DEPENDENCY_SATISFIED_STATES = frozenset(
    {
        NodeState.CERTIFIED,
        NodeState.OWNER_HELD,
        NodeState.MERGE_ELIGIBLE,
        NodeState.MERGED,
        NodeState.CLOSED,
    }
)


class AutonomousGovernor:
    """Authoritative in-process execution state for AS-ORCH-AUTONOMY-001."""

    def __init__(
        self,
        *,
        current_main: str,
        current_tree: str,
        trusted_anchor: TrustedAnchorRecord,
        agents: tuple[AgentRecord, ...] = DEFAULT_AGENTS,
        lease_projection_store: Path | None = None,
        origination_projection_store: Path | None = None,
    ) -> None:
        self._trusted = trusted_anchor
        self._lease_projection_store = lease_projection_store
        # Owner directive D-ATLAS-PR678-CASE-A-LEASE-AUTHORITY-CLOSURE
        # §8: the ONLY origination dependency this governor takes -- a
        # path to the durable projection whose current-revision state
        # `lease()` consults before granting NEW execution authority to
        # an origination-derived node. Deliberately NOT a project root:
        # the governor never rescans project sources, never re-derives
        # anything, and never decides what current truth IS. Origination
        # reconciles truth; the governor only refuses authority when a
        # node's provenance no longer matches what was already
        # reconciled.
        #
        # `None` (the default) is NOT "skip the check". A node that
        # CLAIMS an origination revision but cannot be verified against
        # one is denied a new lease outright
        # (`ORIGINATION_AUTHORITY_UNAVAILABLE` -- see
        # `_require_current_origination_authority()`). Leaving this unset
        # is therefore safe-but-restrictive, never permissive: failure to
        # verify must not expand authority.
        #
        # Nodes that are not origination-derived are unaffected either
        # way, so the pilot-only entry points legitimately do not wire it
        # (a pilot node carries no `origination_identity`). The real
        # production tick (`autonomy/cli.py::run_governor_loop_tick`)
        # does wire it, which is what keeps origination-derived work
        # leasable there.
        self._origination_projection_store = origination_projection_store
        target_moved = evaluate_target_moved(current_main, current_tree, trusted_anchor)
        self._nodes: list[WorkNode] = []
        self._agents = list(agents)
        self._leases: list[AgentLease] = []
        self._transitions: list[TransitionRecord] = []
        self._hard_blockers: list[str] = []
        if target_moved:
            self._hard_blockers.append("TARGET_MOVED")
        self._sequence = 0
        self._current_main = current_main
        self._current_tree = current_tree
        self._target_moved = target_moved
        self._trust_state = classify_observation(current_main, current_tree, trusted_anchor)
        self._ci_state = CiState.UNKNOWN
        self._iv_state = IvState.NOT_REQUIRED
        self._certification_state = CertificationState.NOT_STARTED
        self._last_verifier: str | None = None
        self._last_implementer: str | None = None
        self._remediation_needed = False

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def adopt_durable_sequence(self, sequence: int) -> None:
        """Advance the in-memory lease-id counter to match durable history.

        A fresh governor always starts ``_sequence = 0``. ``restore_lease()``
        already adopts one restored row's sequence, but an IDLE/STOPPED tick
        never restores a lease -- it mints ``LEASE-{n}`` from 1 again. If a
        prior process already recorded ``LEASE-1``, ``project_grant()``
        raises ``LEASE_REPLAY`` for an otherwise-new package. Adopt the
        highest durable created/released sequence so newly minted ids stay
        monotonic across process boundaries.
        """
        if sequence > self._sequence:
            self._sequence = sequence

    def snapshot(self) -> GovernorState:
        nodes = tuple(self._nodes)
        return GovernorState(
            current_main=self._current_main,
            current_tree=self._current_tree,
            trusted_runtime_main=self._trusted.trusted_main,
            trusted_runtime_tree=self._trusted.trusted_tree,
            bootstrap_main=BOOTSTRAP_MAIN,
            bootstrap_tree=BOOTSTRAP_TREE,
            trust_state=self._trust_state,
            target_moved=self._target_moved,
            nodes=nodes,
            agents=tuple(self._agents),
            leases=tuple(self._leases),
            dependencies=tuple(
                f"{edge.source}->{edge.target}" for edge in self._edges(nodes)
            ),
            dag_edges=self._edges(nodes),
            mutation_surfaces=tuple(
                sorted({node.mutation_surface.surface_id for node in nodes})
            ),
            overlap_state=overlap_gate(nodes) if nodes else OverlapState(
                parallel_execution=False,
                conflict_surfaces=(),
                reason="NO_NODES",
            ),
            ci_state=self._ci_state,
            iv_state=self._iv_state,
            certification_state=self._certification_state,
            owner_gates=tuple(
                node.owner_gate for node in nodes if node.owner_gate is not None
            ),
            hard_blockers=tuple(self._hard_blockers),
            sequence=self._sequence,
        )

    def _edges(self, nodes: tuple[WorkNode, ...]) -> tuple[DagEdge, ...]:
        edges: list[DagEdge] = []
        ids = {node.package_id for node in nodes}
        for node in nodes:
            for dep in node.dependencies:
                if dep in ids:
                    edges.append(DagEdge(source=dep, target=node.package_id))
        return tuple(edges)

    def plan(self) -> ExecutionPlan:
        nodes = tuple(self._nodes)
        ready = [node.package_id for node in nodes if node.state == NodeState.READY]
        waiting = [
            node.package_id
            for node in nodes
            if node.state in {NodeState.DISCOVERED, NodeState.LEASED, NodeState.BLOCKED}
            or (node.dependencies and node.state == NodeState.READY)
        ]
        owner = [
            node.package_id
            for node in nodes
            if node.owner_gate is not None
            or node.state in {NodeState.OWNER_HELD, NodeState.MERGE_ELIGIBLE}
        ]
        parallel_groups: list[tuple[str, ...]] = []
        overlap = overlap_gate(nodes)
        if overlap.parallel_execution:
            active = tuple(
                node.package_id
                for node in nodes
                if node.state in {NodeState.LEASED, NodeState.ACTIVE}
            )
            if len(active) > 1:
                parallel_groups.append(active)
        stop: StopReason | None = None
        if self._hard_blockers:
            stop = StopReason.HARD_BLOCKER
        elif not ready and any(node.state == NodeState.OWNER_HELD for node in nodes):
            stop = StopReason.OWNER_GATE
        elif not ready:
            stop = StopReason.NO_ELIGIBLE_WORK
        return ExecutionPlan(
            what_can_run_now=tuple(ready),
            what_must_wait=tuple(waiting),
            what_can_run_in_parallel=tuple(parallel_groups),
            what_requires_owner_authority=tuple(owner),
            stop_reason=stop,
        )

    def ingest_discovery(self, report: DiscoveryReport) -> WorkNode | None:
        if report.case == "A-B":
            if report.blocker and report.blocker not in self._hard_blockers:
                self._hard_blockers.append(report.blocker)
            return None
        if report.selected_package_id is None:
            return None
        selected = next(
            item for item in report.candidates if item.package_id == report.selected_package_id
        )
        node = self._pilot_node(report.inventory, selected.owner_gate)
        self._nodes.append(node)
        return node

    def _pilot_node(
        self,
        inventory: LiveInventory,
        owner_gate: OwnerGateKind | None,
    ) -> WorkNode:
        return WorkNode(
            package_id=PILOT_PACKAGE_ID,
            objective=(
                "Controlled non-destructive discovery+lease+evidence pilot "
                "proving the autonomous governor APIs"
            ),
            base_pin=inventory.current_main,
            dependencies=(),
            mutation_surface=MutationSurface(
                surface_id="orch-autonomy-pilot",
                paths=("src/project_atlas/orchestration/autonomy",),
                semantic="ORCHESTRATION_AUTONOMY_CONTROL_PLANE",
            ),
            execution_host_class=ExecutionHostClass.IN_PROCESS,
            agent_capabilities_required=(AgentCapability.DISCOVER, AgentCapability.IMPLEMENT),
            acceptance_criteria=(
                "DISCOVER_ELIGIBLE_NODE",
                "LEASE_IN_PROCESS_AGENT",
                "COLLECT_EVIDENCE",
                "ROUTE_IV",
                "STOP_AT_OWNER_GATE",
            ),
            test_requirements=("FOCUSED_AUTONOMY_TESTS",),
            iv_requirements=IvRequirements(
                certification_required=True,
                implementer_cannot_verify=True,
                adversarial_required=True,
            ),
            owner_gate=owner_gate,
            risk_tags=(RiskTag.CONTROL_PLANE, RiskTag.AUTHORIZATION),
        )

    def add_node(self, node: WorkNode) -> None:
        if any(existing.package_id == node.package_id for existing in self._nodes):
            raise GovernorError("duplicate package_id", code="DUPLICATE_NODE")
        self._nodes.append(node)

    def _replace(self, updated: WorkNode) -> None:
        self._nodes = [
            updated if item.package_id == updated.package_id else item for item in self._nodes
        ]

    def request_merge(self, package_id: str, *, owner_grant: bool = False) -> None:
        """Protected-main merge is owner gate A. Default grant is absent."""
        del package_id
        require_owner(OwnerGateKind.A_PROTECTED_MAIN_MERGE, owner_grant=owner_grant)
        raise IllegalTransitionError("governor cannot autonomously transition to MERGED")

    def request_acceptance_waiver(self, *, owner_grant: bool = False) -> None:
        require_owner(OwnerGateKind.B_ACCEPTANCE_WAIVER, owner_grant=owner_grant)

    def request_certified_object_mutation(self, *, owner_grant: bool = False) -> None:
        """Owner gate C: mutating a certified object (e.g. a frozen demo
        estate/showcase surface, a sealed evidence bundle) requires explicit
        owner authority, same as A/B. ORCHAUT-010 (2026-08-28): added as a
        real, importable enforcement primitive -- previously this gate
        existed only as a descriptive ``WorkNode.owner_gate`` tag with no
        dedicated call site to fail closed on, unlike A/B."""
        require_owner(OwnerGateKind.C_CERTIFIED_OBJECT_MUTATION, owner_grant=owner_grant)

    def request_security_governance_policy_change(self, *, owner_grant: bool = False) -> None:
        """Owner gate D: changing a security or governance policy (e.g. the
        DENY-list freeze surface, an owner-gate definition itself) requires
        explicit owner authority. ORCHAUT-010 (2026-08-28): see
        ``request_certified_object_mutation`` docstring for why this is new."""
        require_owner(OwnerGateKind.D_SECURITY_GOVERNANCE_POLICY, owner_grant=owner_grant)

    def request_destructive_op(self, *, owner_grant: bool = False) -> None:
        """Owner gate E: an irreversible or hard-to-reverse operation (data
        deletion, force-push, history rewrite) requires explicit owner
        authority. ORCHAUT-010 (2026-08-28): see
        ``request_certified_object_mutation`` docstring for why this is new."""
        require_owner(OwnerGateKind.E_DESTRUCTIVE_OPS, owner_grant=owner_grant)

    def request_material_external_spend(self, *, owner_grant: bool = False) -> None:
        """Owner gate F: an action with a material external cost (billed API
        usage, paid infrastructure) requires explicit owner authority.
        ORCHAUT-010 (2026-08-28): see ``request_certified_object_mutation``
        docstring for why this is new."""
        require_owner(OwnerGateKind.F_MATERIAL_EXTERNAL_SPEND, owner_grant=owner_grant)

    def transition(self, package_id: str, to_state: NodeState, reason: str) -> TransitionRecord:
        node = self._require_node(package_id)
        if to_state == NodeState.MERGED:
            self.request_merge(package_id, owner_grant=False)
        updated, record = apply_transition(
            node, to_state, reason=reason, sequence=self._next_sequence()
        )
        self._replace(updated)
        self._transitions.append(record)
        return record

    def mark_ready(self, package_id: str) -> TransitionRecord:
        return self.transition(package_id, NodeState.READY, "GOVERNOR_MARK_READY")

    def _require_current_origination_authority(self, node: WorkNode) -> None:
        """Refuse a NEW lease for an origination-derived node whose
        origination revision is no longer the current one (owner
        directive D-ATLAS-PR678-CASE-A-LEASE-AUTHORITY-CLOSURE §6).

        The gap this closes: `rehydration.py` already refuses to RESUME
        a durably-leased node across a process restart once its
        package_id has been revised
        (`has_ever_had_multiple_revisions()`), but nothing protected a
        node inside a single LONG-LIVED governor. `_originate()`'s
        discovery pass skips any package_id already known to this
        governor, so a node that reached READY under revision A and then
        sat unleased while a scan superseded it with revision B was
        never refreshed or evicted -- and `lease()` had no field to
        notice, because a `WorkNode` carried no origination provenance
        at all. It does now (`WorkNode.origination_identity`), so this
        is the check that field exists for.

        Deliberately NOT dependent on base_pin drift, main movement, or
        a process restart: a contract-only revision changes none of
        those, and that is exactly the case that previously slipped
        through.

        A MISSING `origination_identity` is deliberately NOT read as
        "this node needs no provenance". That would turn an additive,
        optional field into a fail-OPEN migration: an origination-derived
        node persisted before the field existed is exactly what
        `list_materialized_work_nodes()` rehydrates and `_originate()`
        marks READY, and it deserializes with `None` -- so it would skip
        precisely the check this method exists to apply. Absence is
        therefore split by what the node itself already records:

        - `mutation_surface.semantic == ORIGINATION_SURFACE_SEMANTIC`
          means origination BUILT this node (`materialize.py` is its
          only producer, and has set that marker unchanged since the
          module was created, so historical rows carry it too). Its
          provenance is missing and therefore UNPROVABLE: fail closed
          with `MISSING_ORIGINATION_IDENTITY`.

          Recovery, stated precisely rather than optimistically (an
          earlier draft said simply "transient and self-healing", which
          independent verification showed is only conditionally true):
          a genuine UPGRADE does heal, because the identity formula
          itself changed (see `identity.py`'s migration note), so the
          next `run_origination_scan()` derives a DIFFERENT identity,
          supersedes the legacy row, and materializes a fresh node that
          carries provenance. What does NOT heal is a row whose stored
          identity already equals what the CURRENT formula derives but
          whose `work_node` provenance was stripped -- store tampering,
          not an upgrade. There `run_origination_scan()` short-circuits
          at its already-materialized AS-IS branch (origination/cli.py)
          and never reaches `reconcile_revision()` at all, so nothing is
          written and the node stays refused until the source content
          actually changes. (An earlier draft of this note named
          `reconcile_revision()`'s idempotent fast path as the mechanism;
          independent verification instrumented the scan and showed
          `reconcile_revision()` is called zero times. The outcome was
          right, the mechanism named was not.)
          That is the correct outcome for a tampered store, but it is
          not self-healing and must not be described as such.
        - Any other semantic means the node is genuinely not
          origination-derived (`_pilot_node()` uses its own
          `ORCHESTRATION_AUTONOMY_CONTROL_PLANE`; tests build their
          own). It is not governed by origination freshness at all and
          leases exactly as it did before this check existed -- no
          sentinel identity is invented for it, and no projection row
          has to exist on its behalf.

        A node whose provenance IS present makes a positive claim -- "I
        was created from origination revision X" -- and that claim must
        be PROVEN current before it earns new authority. If this governor
        has no origination store, that proof is simply unavailable, and
        unavailable verification is NOT permission: deny with
        `ORIGINATION_AUTHORITY_UNAVAILABLE`, deliberately distinct from
        `STALE_ORIGINATION_IDENTITY` (which means current authority WAS
        established and differs). The real production tick does wire the
        store, but that is evidence about today's callers, not an
        invariant -- `add_node()`/`lease()` are public, and a future
        integration, harness, or refactor that forgets the dependency
        must never thereby widen what can be leased.

        One remaining non-case, which does not deny: an ALREADY-granted
        lease is untouched. This runs only on the path that grants NEW
        authority; nothing here revokes, cancels, or reaches into a live
        lease.

        Operational consequence, worth knowing before relying on this in
        an unattended loop: a refusal here reaches
        `AutonomousLoop._select_and_lease()` and stops the tick with
        `HARD_BLOCKER`, which `_may_resume_from_no_eligible_work()`
        deliberately never auto-resumes. That mechanism is pre-existing
        (it already governed TARGET_MOVED / SURFACE_OVERLAP /
        NODE_NOT_READY / DEPENDENCIES_NOT_SATISFIED) and this package does
        not change it -- but these three codes add new, ordinary-operation
        triggers for it. Independent verification confirmed that once a
        node is blocked this way, REPAIRING the store does not by itself
        release the loop: an operator must retire the stale loop state.
        Fail-closed, but not self-clearing.

        With provenance present AND a store to check it against,
        everything else fails CLOSED through a single comparison:
        `current_origination_identity()` returns `None` for "no current
        revision", "ambiguous/corrupt store", and "unreadable store"
        alike, so each is a mismatch and therefore a denial, without this
        method having to distinguish or catch them.

        Identity matching is NECESSARY, never sufficient: a match falls
        straight through to every pre-existing gate (dependencies,
        owner gate, surface overlap) unchanged. This is not an
        authorization token.
        """
        if node.origination_identity is None:
            if node.mutation_surface.semantic == ORIGINATION_SURFACE_SEMANTIC:
                raise GovernorError(
                    f"node {node.package_id!r} was built by origination "
                    f"(mutation surface {ORIGINATION_SURFACE_SEMANTIC}) but carries "
                    "no origination_identity -- its authority provenance predates "
                    "that field and cannot be proven current, so it must not "
                    "receive a new lease; re-run an origination scan to supersede "
                    "this legacy revision and materialize a fresh, provable one",
                    code="MISSING_ORIGINATION_IDENTITY",
                )
            return
        store = self._origination_projection_store
        if store is None:
            raise GovernorError(
                f"node {node.package_id!r} claims origination revision "
                f"{node.origination_identity!r}, but this governor has no "
                "origination projection to verify that claim against -- refusing "
                "to grant a new lease on unverifiable authority (failure to verify "
                "is not permission to execute)",
                code="ORIGINATION_AUTHORITY_UNAVAILABLE",
            )
        # Local import: `origination.projection` imports this package's
        # own `models`/`lease_projection`, so a module-level import here
        # would be circular. Same reason `rehydration.py` imports that
        # module inside its functions.
        from project_atlas.orchestration.origination.projection import (
            current_origination_identity,
        )

        current = current_origination_identity(store, node.package_id)
        if current != node.origination_identity:
            raise GovernorError(
                f"node {node.package_id!r} was materialized from origination "
                f"revision {node.origination_identity!r}, which is no longer the "
                f"current authoritative revision for this work "
                f"(current: {current!r}) -- refusing to grant NEW execution "
                f"authority to a superseded, ambiguous, or no-longer-present "
                f"origination revision",
                code="STALE_ORIGINATION_IDENTITY",
            )

    def _surface_recomputed_owner_gate(self, node: WorkNode) -> OwnerGateKind | None:
        """Recompute the owner gate from declared mutation paths.

        Stored ``WorkNode.owner_gate`` is recovery evidence, not authority.
        A hand-written MATERIALIZED origination row (or a directly
        constructed node) can omit the gate honest ``risk.classify`` +
        ``owner_gate_for`` would attach. Lease is the grant boundary:
        if the stored gate is absent and the declared surface is
        OWNER_HELD, require an owner grant anyway.
        AS-ORIG-PROJECTION-FORGE-001 / P1-ORIG-PROJECTION-FORGE-001.
        """
        from project_atlas.orchestration.origination.materialize import owner_gate_for
        from project_atlas.orchestration.origination.risk import classify

        classification = classify(
            proposed_scope=node.mutation_surface.paths,
            success_criteria=node.acceptance_criteria,
        )
        return owner_gate_for(classification)

    def _effective_owner_gate(self, node: WorkNode) -> OwnerGateKind | None:
        stored = node.owner_gate
        if stored is not None:
            return stored
        return self._surface_recomputed_owner_gate(node)

    def lease(
        self,
        package_id: str,
        agent_id: str,
        *,
        branch: str,
        worktree: str,
        owner_grant: bool = False,
        execution_host_class_override: ExecutionHostClass | None = None,
    ) -> AgentLease:
        if self._target_moved:
            raise GovernorError("refusing lease on moved target", code="TARGET_MOVED")
        node = self._require_node(package_id)
        if node.state != NodeState.READY:
            raise GovernorError("node is not READY", code="NODE_NOT_READY")
        self._require_current_origination_authority(node)
        # D-PHASE2A-1a: WorkNode.dependencies was accepted and stored by
        # every layer (adapter, policy, materialize) but never actually
        # enforced at the one place that grants real execution access --
        # lease(). A node reaching READY does not itself mean its
        # dependencies are satisfied (mark_ready() is a bare state
        # transition; `plan()`'s WHAT_MUST_WAIT bucket already treats a
        # READY node with outstanding dependencies as non-executable, but
        # nothing stopped a direct lease() call from bypassing that
        # advisory bucket entirely). This must fail closed HERE, at the
        # grant boundary, for the same reason the owner-gate check above
        # fails closed here rather than relying on a caller to have
        # consulted `plan()` first. Unknown/untracked dependency ids are
        # NOT assumed satisfied -- a dependency this governor instance has
        # never seen is treated as unsatisfied, not as "not applicable".
        unsatisfied = self._unsatisfied_dependencies(node)
        if unsatisfied:
            raise GovernorError(
                f"unsatisfied dependencies: {', '.join(unsatisfied)}",
                code="DEPENDENCIES_NOT_SATISFIED",
            )
        effective_gate = self._effective_owner_gate(node)
        if effective_gate is not None and effective_gate != OwnerGateKind.A_PROTECTED_MAIN_MERGE:
            # ORCHAUT-010 remediation round 2 (2026-08-28, independent-IV
            # finding): gate A already has its own dedicated, always-enforced
            # downstream check at the MERGED transition (`request_merge`
            # always raises without an owner grant) -- the controlled pilot's
            # tested contract (`run_controlled_pilot`,
            # test_controlled_pilot_stops_at_owner_gate) deliberately
            # executes+certifies a gate-A node before stopping at
            # OWNER_HELD, and that stays unchanged here. Gates B-F have no
            # such downstream checkpoint: `execute_leased()` performs the
            # node's actual in-process action, so for B-F the gate must fail
            # closed HERE, before any lease/execution happens at all --
            # AS-ORCH-001E's loop is not the only caller of lease() /
            # execute_leased(); run_controlled_pilot() and
            # continue_autonomous() reach this method directly too.
            #
            # AS-ORIG-PROJECTION-FORGE-001: a omitted stored gate is not
            # an O1 grant. Recompute from mutation_surface.paths so a
            # forged MATERIALIZED projection row cannot lease a security
            # surface by leaving owner_gate unset.
            try:
                require_owner(effective_gate, owner_grant=owner_grant)
            except OwnerGateError as exc:
                raise GovernorError(str(exc), code="OWNER_GATE_REQUIRED") from exc
        if would_overlap(tuple(self._nodes), node):
            raise GovernorError("surface overlap forbids lease", code="SURFACE_OVERLAP")
        agent = self._require_agent(agent_id)
        lease = grant_lease(
            lease_id=f"LEASE-{self._next_sequence()}",
            agent=agent,
            node=node,
            branch=branch,
            worktree=worktree,
            sequence=self._sequence,
        )
        if self._lease_projection_store is not None:
            project_grant(
                self._lease_projection_store,
                lease,
                live_main=self._current_main,
            )
        self._leases.append(lease)
        if execution_host_class_override is not None:
            # AS-ORCH-LOCAL-DISPATCH-001 (PR-C): an explicit, caller-
            # supplied execution-backend SELECTION -- applied only after
            # every authority check above (READY state, dependencies,
            # owner gate, surface overlap) AND the lease itself has
            # already been durably granted, so this can never widen what
            # a node is authorized to do, only choose HOW an
            # already-authorized, already-granted lease runs.
            # materialize.py always sets IN_PROCESS; this is the one
            # deliberate, explicit place that value can be overridden --
            # never silently, never as a default (this parameter has no
            # default other than None = unchanged, current behavior).
            #
            # Review finding (copilot-pull-request-reviewer, PR #662):
            # applying this BEFORE grant_lease()/project_grant() left a
            # node stuck with an overridden execution_host_class but no
            # actual lease if either of those later steps failed (e.g. an
            # unknown agent, or a projection-store write error) --
            # grant_lease() itself never reads node.execution_host_class
            # (only mutation_surface/state/capabilities), so applying the
            # override here, after both have already durably succeeded,
            # changes nothing about what the override can affect while
            # closing that partial-mutation window.
            node = node.model_copy(update={"execution_host_class": execution_host_class_override})
            self._replace(node)
        self.transition(package_id, NodeState.LEASED, f"LEASED_TO_{agent_id}")
        return lease

    def restore_lease(self, lease: AgentLease) -> None:
        """ORCH001E-011: reconstruct a previously-granted lease after a
        process restart, from durable projection evidence. Does **not**
        mint a new lease, does not consult owner gates again (the gate was
        already enforced -- correctly, per ORCHAUT-010 -- at the original
        `lease()` call that produced this same lease; re-checking it here
        would be redundant, not additional safety, because owner-gate
        classification is a static, intrinsic property of the WorkNode
        itself), and does not itself decide whether the caller's evidence
        is trustworthy -- the caller (``rehydration.py``) is responsible
        for validating `lease` against the durable lease projection and
        rejecting foreign/stale/mismatched rows before calling this. This
        method's own job is narrow: given a lease the caller has already
        established is genuine, restore the governor's in-memory
        bookkeeping (`self._leases`, node state) to match it -- nothing
        more.

        KNOWN LIMITATION (D-PHASE2A-1a independent-IV finding, not fixed
        here): unlike the owner-gate exemption above, `node.dependencies`
        satisfaction is a *temporal*, mutable property -- a dependency
        that was CERTIFIED/OWNER_HELD/MERGE_ELIGIBLE at the original
        `lease()` call can legally regress to BLOCKED/SUPERSEDED later
        (see `dag.ALLOWED_TRANSITIONS`), and this method does not
        re-verify it. It deliberately does NOT call
        `_unsatisfied_dependencies()` either, because
        `_restore_leased_node()` only reconstructs the single node being
        restored into a fresh governor's `self._nodes` -- not its
        dependency nodes -- so that check would spuriously fail every
        restoration of a node with any non-empty `dependencies` (unknown
        dependency id => treated as unsatisfied). Closing this gap
        properly requires rehydration to resolve each dependency's
        current state from a durable source (e.g. the origination
        projection store), which is out of scope for this method in
        isolation. Net risk: a dependent's lease can be restored across a
        process restart even if its dependency was rejected in the
        interim -- bounded, not silently escalating (no crash, no data
        corruption; the dependent is simply building on since-invalidated
        work until its own IV/certification catches the inconsistency).
        """
        if self._target_moved:
            raise GovernorError("refusing lease restoration on moved target", code="TARGET_MOVED")
        node = self._require_node(lease.package_id)
        if node.state != NodeState.READY:
            raise GovernorError(
                "node must be freshly-rediscovered READY to restore a lease onto it",
                code="NODE_NOT_READY",
            )
        if would_overlap(tuple(self._nodes), node):
            raise GovernorError("surface overlap forbids lease restoration", code="SURFACE_OVERLAP")
        # Independent-IV note (PR #638): for a direct double-call with the
        # exact same lease, the NODE_NOT_READY check above already fires
        # first on the second call (the node is LEASED by then), so this
        # replay guard is shadowed for that specific shape -- confirmed
        # harmless (no double-append either way). It remains real
        # defense-in-depth for other shapes, e.g. two different callers
        # racing to restore colliding lease_ids onto two different
        # still-READY nodes.
        if any(existing.lease_id == lease.lease_id for existing in self._leases):
            raise GovernorError("lease already present", code="LEASE_REPLAY")
        # ORCH001E-011 finding #4 (independent-IV): a fresh governor's
        # `_sequence` always starts at 0 (`__init__`), independent of
        # whatever sequence the lease being restored actually carries. If
        # left unadvanced, the next real `lease()` call on THIS governor
        # mints `LEASE-{self._next_sequence()}` starting back from 1 --
        # colliding with a `LEASE-*` id the durable lease projection
        # already has a row for -- and `project_grant()` raises
        # `LEASE_REPLAY`, breaking forward progress after every recovery
        # that restores a lease with sequence >= 1. Advance the logical
        # sequence counter to at least the restored lease's own sequence
        # before any further `_next_sequence()` call (here, via
        # `transition()` below, and in any subsequent `lease()`) so newly
        # minted ids can never reuse one already durably recorded.
        if lease.sequence > self._sequence:
            self._sequence = lease.sequence
        self._leases.append(lease)
        self.transition(lease.package_id, NodeState.LEASED, f"REHYDRATED_LEASE_{lease.agent_id}")

    def release_lease(self, lease_id: str) -> AgentLease:
        """Release a lease: in-memory bookkeeping and, when configured, the
        durable lease projection (`project_release()`).

        ORCH001E-011 finding #2 (independent-IV): previously only
        `run_controlled_pilot()` did this, inline, after a node reached its
        terminal state. `AutonomousLoop`'s real 001E tick path
        (`apply_observed_result()` -> IDLE) never released anything, so a
        successfully completed lease stayed `active=True` in the governor
        forever and (once the durable projection is enabled, as the CLI
        now does) `ACTIVE` in `leases.json` forever too -- rejecting the
        very next lease for the same package/worker as
        `DUPLICATE_ACTIVE_LEASE`/`FOREIGN_WORKER`. Extracted here so every
        terminal-completion caller releases a lease the exact same way.
        Idempotent: releasing an already-released lease is a no-op, not an
        error, so callers that don't track completion state precisely can
        call this unconditionally.
        """
        existing = next((item for item in self._leases if item.lease_id == lease_id), None)
        if existing is None:
            raise GovernorError(f"unknown lease {lease_id}", code="UNKNOWN_LEASE")
        if not existing.active:
            return existing
        released = release_lease(existing)
        if self._lease_projection_store is not None:
            project_release(
                self._lease_projection_store,
                released,
                live_main=self._current_main,
            )
        self._leases = [
            released if item.lease_id == lease_id else item for item in self._leases
        ]
        return released

    def execute_leased(self, lease_id: str) -> EvidenceBundle:
        """In-process execution of a real lease. Not a bypass stub."""
        lease = next((item for item in self._leases if item.lease_id == lease_id), None)
        if lease is None or not lease.active:
            raise GovernorError("lease not active", code="LEASE_INACTIVE")
        node = self._require_node(lease.package_id)
        if node.execution_host_class != ExecutionHostClass.IN_PROCESS:
            raise GovernorError("external host is not authorized", code="HOST_NOT_AUTHORIZED")
        self._last_implementer = lease.agent_id
        self.transition(lease.package_id, NodeState.ACTIVE, "IN_PROCESS_EXECUTE")
        payload: dict[str, object] = {
            "lease_id": lease.lease_id,
            "package_id": lease.package_id,
            "agent_id": lease.agent_id,
            "base_pin": lease.base_pin,
            "authorized_paths": list(lease.authorized_paths),
            "forbidden_paths": list(lease.forbidden_paths),
            "acceptance": list(node.acceptance_criteria),
        }
        bundle = make_bundle("PILOT_EXECUTION", payload)
        return bundle

    def route_and_verify(self, package_id: str, *, implementer_id: str) -> str:
        node = self._require_node(package_id)
        assignment = route_iv(node, implementer_id=implementer_id, agents=tuple(self._agents))
        if assignment.verifier_id == implementer_id and node.iv_requirements.certification_required:
            raise IvRoutingError("implementer cannot verify certification")
        self._last_verifier = assignment.verifier_id
        self._iv_state = assignment.state
        self.transition(package_id, NodeState.VERIFYING, f"IV_ROUTED_{assignment.verifier_id}")
        return assignment.verifier_id

    def complete_verification(self, package_id: str, *, passed: bool) -> None:
        node = self._require_node(package_id)
        if not passed:
            self._iv_state = IvState.FAIL
            if can_remediate(node):
                updated = consume_remediation_cycle(node)
                self._replace(updated)
                self._remediation_needed = True
                self.transition(package_id, NodeState.REMEDIATING, "IV_FAIL_REMEDIATE")
                return
            self._hard_blockers.append("IV_FAIL_REMEDIATION_EXHAUSTED")
            self.transition(package_id, NodeState.BLOCKED, "REMEDIATION_EXHAUSTED")
            return
        self._iv_state = IvState.PASS
        self._certification_state = CertificationState.CERTIFIED
        self.transition(package_id, NodeState.CERTIFIED, "IV_PASS")
        node = self._require_node(package_id)
        if node.owner_gate is not None:
            self.transition(package_id, NodeState.OWNER_HELD, "OWNER_GATE_AFTER_CERT")

    def remediate_and_resume(self, package_id: str) -> None:
        node = self._require_node(package_id)
        if node.state != NodeState.REMEDIATING:
            raise GovernorError("node is not remediating", code="NOT_REMEDIATING")
        if not can_remediate(node) and node.retry_policy.cycles_used >= 3:
            raise RemediationExhausted("MAX_AUTONOMOUS_REMEDIATION_CYCLES exceeded")
        self.transition(package_id, NodeState.ACTIVE, "BOUNDED_REMEDIATE_RESUME")

    def continue_autonomous(self, *, branch: str, worktree: str) -> ExecutionPlan:
        decision = select_next(tuple(self._nodes), hard_blockers=tuple(self._hard_blockers))
        if decision.next_package_id is None:
            plan = self.plan()
            return plan.model_copy(update={"stop_reason": decision.stop_reason})
        implementer = self._first_implementer()
        self.lease(decision.next_package_id, implementer.agent_id, branch=branch, worktree=worktree)
        return self.plan()

    def run_controlled_pilot(
        self,
        inventory: LiveInventory,
        *,
        branch: str,
        worktree: str,
        evidence_dir: Path | None = None,
        inject_iv_failure: bool = False,
    ) -> dict[str, object]:
        """End-to-end non-destructive pilot using the real governor APIs."""
        if self._target_moved:
            raise GovernorError("CASE=A-B target moved", code="TARGET_MOVED")
        report = discover(inventory, trusted=self._trusted)
        if report.case == "A-B":
            raise GovernorError(report.blocker or "A-B", code=report.blocker or "A-B")
        node = self.ingest_discovery(report)
        if node is None:
            # In-process API proof only. Not a live dispatch or 001E start.
            node = self._pilot_node(
                report.inventory,
                OwnerGateKind.A_PROTECTED_MAIN_MERGE,
            )
            self._nodes.append(node)
        self.mark_ready(node.package_id)
        implementer = self._first_implementer()
        lease = self.lease(
            node.package_id,
            implementer.agent_id,
            branch=branch,
            worktree=worktree,
        )
        bundle = self.execute_leased(lease.lease_id)
        verifier = self.route_and_verify(node.package_id, implementer_id=implementer.agent_id)
        if inject_iv_failure:
            self.complete_verification(node.package_id, passed=False)
            self.remediate_and_resume(node.package_id)
            self.route_and_verify(node.package_id, implementer_id=implementer.agent_id)
            self.complete_verification(node.package_id, passed=True)
            remediation = 1
        else:
            self.complete_verification(node.package_id, passed=True)
            remediation = 0
        continuation = select_next(tuple(self._nodes), hard_blockers=tuple(self._hard_blockers))
        self.release_lease(lease.lease_id)
        evidence_path = None
        if evidence_dir is not None:
            written = write_bundle(evidence_dir, "pilot-evidence.json", bundle)
            evidence_path = str(written)
        snapshot = self.snapshot()
        plan = self.plan()
        return {
            "schema_version": 1,
            "package_id": AUTONOMY_PACKAGE_ID,
            "pilot_package_id": node.package_id,
            "discovered": True,
            "selected_package_id": report.selected_package_id,
            "lease_id": lease.lease_id,
            "implementer_id": implementer.agent_id,
            "verifier_id": verifier,
            "implementer_equals_verifier": implementer.agent_id == verifier,
            "evidence_sha256": bundle.payload_sha256,
            "evidence_path": evidence_path,
            "remediation_cycles": remediation,
            "stop_reason": (
                continuation.stop_reason.value if continuation.stop_reason is not None else None
            ),
            "node_state": self._require_node(node.package_id).state.value,
            "plan": plan.model_dump(mode="json"),
            "governor": snapshot.model_dump(mode="json"),
            "r2_created": inventory.r2_created,
            "r7_created": inventory.r7_created,
            "authentic_r6_resumed": inventory.authentic_r6_resumed,
            "as_orch_001e_started": inventory.as_orch_001e_started,
            "merge_authorized": False,
            "merge_performed": False,
            "successor_execution_under_new_model": "NOT_YET_ACTIVE",
            "truth_boundary": snapshot.truth_boundary,
        }

    def _first_implementer(self) -> AgentRecord:
        for agent in self._agents:
            if agent.available and AgentCapability.IMPLEMENT in agent.capabilities:
                return agent
        raise GovernorError("no implementer available", code="NO_IMPLEMENTER")

    def _require_node(self, package_id: str) -> WorkNode:
        for node in self._nodes:
            if node.package_id == package_id:
                return node
        raise GovernorError(f"unknown package {package_id}", code="UNKNOWN_NODE")

    def _unsatisfied_dependencies(self, node: WorkNode) -> tuple[str, ...]:
        """Return the subset of `node.dependencies` not yet satisfied.

        A dependency id this governor instance has no WorkNode for at all
        is treated as unsatisfied (fail closed) rather than skipped --
        it may be tracked by a different, un-rehydrated governor instance,
        and this method has no way to distinguish "genuinely done
        elsewhere" from "never happened". See `_DEPENDENCY_SATISFIED_STATES`.
        """
        unsatisfied: list[str] = []
        for dep_id in node.dependencies:
            try:
                dep_node = self._require_node(dep_id)
            except GovernorError:
                unsatisfied.append(dep_id)
                continue
            if dep_node.state not in _DEPENDENCY_SATISFIED_STATES:
                unsatisfied.append(dep_id)
        return tuple(unsatisfied)

    def _require_agent(self, agent_id: str) -> AgentRecord:
        for agent in self._agents:
            if agent.agent_id == agent_id:
                return agent
        raise GovernorError(f"unknown agent {agent_id}", code="UNKNOWN_AGENT")


def run_live_pilot(
    repo: Path,
    *,
    evidence_dir: Path | None = None,
    trusted_anchor: TrustedAnchorRecord | None = None,
) -> dict[str, object]:
    try:
        trusted = trusted_anchor or load_runtime_anchor(allow_shipped=True)
    except TrustError as exc:
        raise GovernorError(str(exc), code=exc.code) from exc
    inventory = collect_live_inventory(repo)
    governor = AutonomousGovernor(
        current_main=inventory.current_main,
        current_tree=inventory.current_tree,
        trusted_anchor=trusted,
    )
    return governor.run_controlled_pilot(
        inventory,
        branch="feat/as-orch-autonomy-001",
        worktree=str(repo),
        evidence_dir=evidence_dir,
    )
