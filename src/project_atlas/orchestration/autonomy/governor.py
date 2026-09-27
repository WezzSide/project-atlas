"""Single logical autonomous governor.

Answers WHAT_CAN_RUN NOW / WAIT / PARALLEL / OWNER. Executes only the
in-process pilot host. Never merges, never starts 001E, never mutates #396,
and never auto-dispatches a next 001D hop.
"""

from __future__ import annotations

from collections.abc import Callable
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
from project_atlas.orchestration.autonomy.owner_gates import require_owner
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
    ) -> None:
        self._trusted = trusted_anchor
        self._lease_projection_store = lease_projection_store
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
        self._execution_guards: dict[str, Callable[[], bool]] = {}

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

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
            dependencies=tuple(f"{edge.source}->{edge.target}" for edge in self._edges(nodes)),
            dag_edges=self._edges(nodes),
            mutation_surfaces=tuple(sorted({node.mutation_surface.surface_id for node in nodes})),
            overlap_state=overlap_gate(nodes)
            if nodes
            else OverlapState(
                parallel_execution=False,
                conflict_surfaces=(),
                reason="NO_NODES",
            ),
            ci_state=self._ci_state,
            iv_state=self._iv_state,
            certification_state=self._certification_state,
            owner_gates=tuple(node.owner_gate for node in nodes if node.owner_gate is not None),
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

    def register_execution_guard(self, package_id: str, guard: Callable[[], bool]) -> None:
        """Require current external authority before leasing or executing a node."""
        self._execution_guards[package_id] = guard

    def restore_blocked_materialization(
        self, package_id: str, *, revalidate: Callable[[], bool]
    ) -> TransitionRecord:
        """Restore a mailbox node only through its current-context revalidation."""
        node = self._require_node(package_id)
        if node.state != NodeState.BLOCKED or not revalidate():
            raise GovernorError(
                "blocked node cannot be restored without current materialization validation",
                code="MATERIALIZATION_REVALIDATION_REQUIRED",
            )
        updated, record = apply_transition(
            node,
            NodeState.READY,
            reason="MAILBOX_MATERIALIZATION_REVALIDATED",
            sequence=self._next_sequence(),
            allow_materialization_recovery=True,
        )
        self._replace(updated)
        self._transitions.append(record)
        return record

    def _require_execution_guard(self, package_id: str) -> None:
        guard = self._execution_guards.get(package_id)
        if guard is None:
            return
        try:
            allowed = bool(guard())
        except Exception:
            allowed = False
        if not allowed:
            raise GovernorError(
                "current mailbox authority is not validated",
                code="MAILBOX_AUTHORITY_REVALIDATION_REQUIRED",
            )

    def mark_ready(self, package_id: str) -> TransitionRecord:
        return self.transition(package_id, NodeState.READY, "GOVERNOR_MARK_READY")

    def lease(
        self,
        package_id: str,
        agent_id: str,
        *,
        branch: str,
        worktree: str,
    ) -> AgentLease:
        if self._target_moved:
            raise GovernorError("refusing lease on moved target", code="TARGET_MOVED")
        node = self._require_node(package_id)
        if node.state != NodeState.READY:
            raise GovernorError("node is not READY", code="NODE_NOT_READY")
        self._require_execution_guard(package_id)
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
        self.transition(package_id, NodeState.LEASED, f"LEASED_TO_{agent_id}")
        return lease

    def execute_leased(self, lease_id: str) -> EvidenceBundle:
        """In-process execution of a real lease. Not a bypass stub."""
        lease = next((item for item in self._leases if item.lease_id == lease_id), None)
        if lease is None or not lease.active:
            raise GovernorError("lease not active", code="LEASE_INACTIVE")
        node = self._require_node(lease.package_id)
        self._require_execution_guard(lease.package_id)
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
        for item in self._leases:
            if item.lease_id == lease.lease_id:
                released = release_lease(item)
                if self._lease_projection_store is not None:
                    project_release(
                        self._lease_projection_store,
                        released,
                        live_main=self._current_main,
                    )
                self._leases = [
                    released if row.lease_id == lease.lease_id else row for row in self._leases
                ]
                break
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
