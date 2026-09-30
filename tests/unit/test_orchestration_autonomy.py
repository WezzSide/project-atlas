"""AS-ORCH-AUTONOMY-001 governor, DAG, leases, overlap, owner gates, evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from project_atlas.cli import EXIT_ERROR, EXIT_OK, main
from project_atlas.orchestration.autonomy.adversarial import requires_adversarial_review
from project_atlas.orchestration.autonomy.continuation import select_next
from project_atlas.orchestration.autonomy.dag import IllegalTransitionError, apply_transition
from project_atlas.orchestration.autonomy.discovery import (
    DiscoveryError,
    collect_live_inventory,
    discover,
)
from project_atlas.orchestration.autonomy.evidence import (
    EvidenceError,
    file_sha256,
    make_bundle,
    write_bundle,
)
from project_atlas.orchestration.autonomy.governor import AutonomousGovernor, GovernorError
from project_atlas.orchestration.autonomy.leases import (
    ScopeExpansionError,
    expand_lease,
    grant_lease,
)
from project_atlas.orchestration.autonomy.models import (
    CANONICAL_REPOSITORY_IDENTITY,
    EXPECTED_BASE_MAIN,
    EXPECTED_BASE_TREE,
    INITIAL_RETARGET_MAIN,
    INITIAL_RETARGET_TREE,
    PILOT_PACKAGE_ID,
    AdvancementReason,
    AgentCapability,
    ExecutionHostClass,
    ExecutionPlan,
    IvRequirements,
    LiveInventory,
    MutationSurface,
    NodeState,
    OwnerGateKind,
    RiskTag,
    StopReason,
    TrustedAnchorRecord,
    WorkNode,
)
from project_atlas.orchestration.autonomy.overlap import overlap_gate, would_overlap
from project_atlas.orchestration.autonomy.owner_gates import (
    OwnerGateError,
    classify_requested_action,
)
from project_atlas.orchestration.autonomy.remediation import (
    MAX_AUTONOMOUS_REMEDIATION_CYCLES,
    RemediationExhausted,
    consume_remediation_cycle,
)
from project_atlas.orchestration.autonomy.trust import seal_anchor
from project_atlas.schema import available_schemas, validate_record

PIN = EXPECTED_BASE_MAIN


def _anchor(main: str = EXPECTED_BASE_MAIN, tree: str = EXPECTED_BASE_TREE) -> TrustedAnchorRecord:
    predecessor = "1111111111111111111111111111111111111111"
    certified = "3333333333333333333333333333333333333333"
    return seal_anchor(
        TrustedAnchorRecord(
            repository_identity=CANONICAL_REPOSITORY_IDENTITY,
            trusted_main=main,
            trusted_tree=tree,
            predecessor_main=predecessor,
            predecessor_tree="2222222222222222222222222222222222222222",
            advancement_reason=AdvancementReason.VERIFIED_OWNER_AUTHORIZED_MERGE,
            source_package="AS-ORCH-AUTONOMY-001-TEST",
            source_directive="D-AUTONOMY-TEST-001",
            source_pr=1,
            merge_commit=main,
            merge_parent_1=predecessor,
            merge_parent_2=certified,
            merge_tree=tree,
            certified_head=certified,
            certified_tree=tree,
            certification_status="CERTIFIED",
            independent_verification_status="PASS",
            post_merge_seal="PASS",
            post_merge_ci="PASS",
            evidence_reference="tests/unit/test-anchor.json",
            evidence_digest="aa" * 32,
            sequence=1,
            record_digest="00" * 32,
        )
    )


def _inventory(**overrides: Any) -> LiveInventory:
    payload: dict[str, Any] = {
        "current_main": EXPECTED_BASE_MAIN,
        "current_tree": EXPECTED_BASE_TREE,
        "worktree_status": "CLEAN",
        "open_relevant_prs": ("396",),
        "active_successor_packages": (),
        "r2_created": "NO",
        "r7_created": "NO",
        "authentic_r6_resumed": "NO",
        "as_orch_001e_started": "NO",
        "pr396_mutated": "NO",
    }
    payload.update(overrides)
    return LiveInventory.model_validate(payload)


def _node(
    package_id: str,
    *,
    state: NodeState = NodeState.READY,
    surface: str = "surface-a",
    semantic: str = "SEMANTIC_A",
    paths: tuple[str, ...] = ("src/a",),
    owner_gate: OwnerGateKind | None = None,
    deps: tuple[str, ...] = (),
    capabilities: tuple[AgentCapability, ...] = (AgentCapability.IMPLEMENT,),
) -> WorkNode:
    return WorkNode(
        package_id=package_id,
        objective="test node",
        base_pin=PIN,
        dependencies=deps,
        mutation_surface=MutationSurface(
            surface_id=surface,
            paths=paths,
            semantic=semantic,
        ),
        execution_host_class=ExecutionHostClass.IN_PROCESS,
        agent_capabilities_required=capabilities,
        acceptance_criteria=("PASS",),
        iv_requirements=IvRequirements(certification_required=True),
        owner_gate=owner_gate,
        state=state,
    )


def test_schemas_registered() -> None:
    kinds = available_schemas()
    assert "autonomy-governor-report" in kinds
    assert "autonomy-work-node" in kinds
    assert "autonomy-lease" in kinds
    assert "autonomy-trusted-anchor" in kinds
    assert "autonomy-loop-state" in kinds


def test_work_node_and_plan_schema_parity() -> None:
    node = _node("PKG-A")
    validate_record(node, "autonomy-work-node")
    plan = ExecutionPlan(
        what_can_run_now=("PKG-A",),
        what_must_wait=(),
        what_can_run_in_parallel=(),
        what_requires_owner_authority=(),
    )
    validate_record(plan, "autonomy-governor-report")


def test_illegal_dag_transition_fail_closed() -> None:
    node = _node("PKG-A", state=NodeState.DISCOVERED)
    with pytest.raises(IllegalTransitionError):
        apply_transition(node, NodeState.MERGED, reason="nope", sequence=1)


def test_legal_ready_to_leased() -> None:
    node = _node("PKG-A", state=NodeState.READY)
    updated, record = apply_transition(node, NodeState.LEASED, reason="lease", sequence=1)
    assert updated.state is NodeState.LEASED
    assert record.from_state is NodeState.READY
    assert record.to_state is NodeState.LEASED


def test_governor_cannot_merge() -> None:
    gov = AutonomousGovernor(
        current_main=EXPECTED_BASE_MAIN,
        current_tree=EXPECTED_BASE_TREE,
        trusted_anchor=_anchor(),
    )
    gov.add_node(_node("PKG-A", state=NodeState.MERGE_ELIGIBLE))
    with pytest.raises(OwnerGateError):
        gov.request_merge("PKG-A")
    with pytest.raises(IllegalTransitionError):
        apply_transition(
            gov.snapshot().nodes[0],
            NodeState.MERGED,
            reason="merge",
            sequence=1,
        )


def test_owner_gates_a_through_f_fail_closed() -> None:
    """ORCHAUT-010 (2026-08-28): this test's name previously overclaimed --
    it confirmed ``classify_requested_action`` *tags* all six gates but only
    ever exercised *enforcement* for gate B (``request_acceptance_waiver``).
    C/D/E/F had zero enforcement call sites anywhere in the package (only
    descriptive ``WorkNode.owner_gate`` tags), matching backlog item
    ORCHAUT-010's exact finding. Now genuinely exercises all six: A via
    ``request_merge`` (kept as an ``IllegalTransitionError``-raising
    override -- see ``test_governor_cannot_merge`` for A's own dedicated
    coverage, reproduced here as OwnerGateError since the gate check runs
    first), B through F via one dedicated ``request_*`` method per gate,
    each added specifically to close this gap and confirmed here to fail
    closed with no owner grant, exactly like A/B already did."""
    gov = AutonomousGovernor(
        current_main=EXPECTED_BASE_MAIN,
        current_tree=EXPECTED_BASE_TREE,
        trusted_anchor=_anchor(),
    )
    gates = classify_requested_action(
        merge_to_protected_main=True,
        waive_acceptance=True,
        mutate_certified_object=True,
        change_security_or_governance_policy=True,
        destructive_ops=True,
        material_external_spend=True,
    )
    assert set(gates) == set(OwnerGateKind)

    gov.add_node(_node("PKG-GATE-A", state=NodeState.MERGE_ELIGIBLE))
    with pytest.raises(OwnerGateError):
        gov.request_merge("PKG-GATE-A")
    with pytest.raises(OwnerGateError):
        gov.request_acceptance_waiver()
    with pytest.raises(OwnerGateError):
        gov.request_certified_object_mutation()
    with pytest.raises(OwnerGateError):
        gov.request_security_governance_policy_change()
    with pytest.raises(OwnerGateError):
        gov.request_destructive_op()
    with pytest.raises(OwnerGateError):
        gov.request_material_external_spend()

    # The generic no-grant-no-pass contract is symmetric for every gate --
    # supplying owner_grant=True must let each one through cleanly (the
    # package's role is validating the gate check itself, not performing
    # the action; a real grant artifact is expected to originate outside
    # this package, per owner_gates.py's own module docstring).
    gov.request_acceptance_waiver(owner_grant=True)
    gov.request_certified_object_mutation(owner_grant=True)
    gov.request_security_governance_policy_change(owner_grant=True)
    gov.request_destructive_op(owner_grant=True)
    gov.request_material_external_spend(owner_grant=True)


def test_lease_and_forbidden_scope_expansion() -> None:
    gov = AutonomousGovernor(
        current_main=EXPECTED_BASE_MAIN,
        current_tree=EXPECTED_BASE_TREE,
        trusted_anchor=_anchor(),
    )
    gov.add_node(
        _node(
            "PKG-A",
            capabilities=(AgentCapability.DISCOVER, AgentCapability.IMPLEMENT),
        )
    )
    lease = gov.lease(
        "PKG-A",
        "governor-pilot-local",
        branch="feat/as-orch-autonomy-001",
        worktree="repo",
    )
    validate_record(lease, "autonomy-lease")
    with pytest.raises(ScopeExpansionError):
        expand_lease(lease)
    agent = next(item for item in gov.snapshot().agents if item.agent_id == "governor-pilot-local")
    ready = _node(
        "PKG-B",
        capabilities=(AgentCapability.DISCOVER, AgentCapability.IMPLEMENT),
        surface="surface-b",
        semantic="SEMANTIC_B",
        paths=("src/b",),
    )
    with pytest.raises(ScopeExpansionError):
        grant_lease(
            lease_id="LEASE-WIDE",
            agent=agent,
            node=ready,
            branch="feat/x",
            worktree="repo",
            sequence=9,
            authorized_paths=("src/b", "src/outside"),
        )


@pytest.mark.parametrize(
    "gate",
    [
        OwnerGateKind.B_ACCEPTANCE_WAIVER,
        OwnerGateKind.C_CERTIFIED_OBJECT_MUTATION,
        OwnerGateKind.D_SECURITY_GOVERNANCE_POLICY,
        OwnerGateKind.E_DESTRUCTIVE_OPS,
        OwnerGateKind.F_MATERIAL_EXTERNAL_SPEND,
    ],
)
def test_lease_fails_closed_for_owner_gated_node(gate: OwnerGateKind) -> None:
    """ORCHAUT-010 remediation round 2 (independent-IV finding): lease() /
    execute_leased() are reachable directly (run_controlled_pilot,
    continue_autonomous), not only through AutonomousLoop.select_next.
    A node tagged owner_gate B-F must never be leased -- and therefore
    never executed or certified -- without an explicit owner grant, no
    matter which caller reaches lease().
    """
    gov = AutonomousGovernor(
        current_main=EXPECTED_BASE_MAIN,
        current_tree=EXPECTED_BASE_TREE,
        trusted_anchor=_anchor(),
    )
    gov.add_node(_node("PKG-GATED", owner_gate=gate))
    with pytest.raises(GovernorError) as exc_info:
        gov.lease("PKG-GATED", "governor-pilot-local", branch="feat/x", worktree="repo")
    assert exc_info.value.code == "OWNER_GATE_REQUIRED"
    node = gov.snapshot().nodes[0]
    assert node.state is NodeState.READY  # unchanged: never leased


def test_lease_still_allows_gate_a_pilot_execution() -> None:
    """Gate A keeps its existing, separately-enforced behavior: lease()
    still allows it through (request_merge alone blocks the actual MERGED
    transition), preserving the controlled pilot's tested contract
    (test_controlled_pilot_stops_at_owner_gate: execute + certify, then
    stop at OWNER_HELD -- never at lease time).
    """
    gov = AutonomousGovernor(
        current_main=EXPECTED_BASE_MAIN,
        current_tree=EXPECTED_BASE_TREE,
        trusted_anchor=_anchor(),
    )
    gov.add_node(_node("PKG-A", owner_gate=OwnerGateKind.A_PROTECTED_MAIN_MERGE))
    lease = gov.lease("PKG-A", "governor-pilot-local", branch="feat/x", worktree="repo")
    assert lease.package_id == "PKG-A"


def test_work_node_rejects_self_dependency() -> None:
    """D-PHASE2A-1a independent-IV finding: a self-dependency is not a
    meaningful "wait for this other work" edge. Before this fix,
    continuation.py's select_next() treated `dep == node.package_id` as
    always-satisfied while governor.py's new dependency check treated it
    as always-unsatisfied (a node must be READY to reach lease(), and
    READY is never in _DEPENDENCY_SATISFIED_STATES) -- select_next()
    would pick such a node believing it ready, then governor.lease()
    would reject it with DEPENDENCIES_NOT_SATISFIED, uncaught by
    AutonomousLoop's exception handling, crashing the loop. Reject the
    self-dependency at the model boundary instead, so neither layer can
    ever observe one.
    """
    with pytest.raises(ValidationError):
        _node("PKG-SELF", deps=("PKG-SELF",))


def test_lease_fails_closed_for_unsatisfied_dependency() -> None:
    """D-PHASE2A-1a: WorkNode.dependencies was accepted end-to-end by the
    origination pipeline (adapter -> policy -> materialize) but never
    actually enforced by the one call that grants real execution access.
    A dependency whose own WorkNode has not yet reached a
    _DEPENDENCY_SATISFIED_STATES state must block lease() outright.
    """
    gov = AutonomousGovernor(
        current_main=EXPECTED_BASE_MAIN,
        current_tree=EXPECTED_BASE_TREE,
        trusted_anchor=_anchor(),
    )
    gov.add_node(_node("PKG-PREREQ", state=NodeState.DISCOVERED, surface="pre", paths=("src/pre",)))
    gov.add_node(_node("PKG-DEPENDENT", deps=("PKG-PREREQ",)))
    with pytest.raises(GovernorError) as exc_info:
        gov.lease("PKG-DEPENDENT", "governor-pilot-local", branch="feat/x", worktree="repo")
    assert exc_info.value.code == "DEPENDENCIES_NOT_SATISFIED"
    assert "PKG-PREREQ" in str(exc_info.value)
    dependent = next(n for n in gov.snapshot().nodes if n.package_id == "PKG-DEPENDENT")
    assert dependent.state is NodeState.READY  # unchanged: never leased


def test_lease_fails_closed_for_unknown_dependency() -> None:
    """A dependency id this governor instance has no WorkNode for at all
    (e.g. tracked only by a different, un-rehydrated governor instance)
    must be treated as unsatisfied -- not silently skipped.
    """
    gov = AutonomousGovernor(
        current_main=EXPECTED_BASE_MAIN,
        current_tree=EXPECTED_BASE_TREE,
        trusted_anchor=_anchor(),
    )
    gov.add_node(_node("PKG-DEPENDENT", deps=("PKG-NEVER-SEEN",)))
    with pytest.raises(GovernorError) as exc_info:
        gov.lease("PKG-DEPENDENT", "governor-pilot-local", branch="feat/x", worktree="repo")
    assert exc_info.value.code == "DEPENDENCIES_NOT_SATISFIED"
    assert "PKG-NEVER-SEEN" in str(exc_info.value)


@pytest.mark.parametrize(
    "satisfied_state",
    [
        NodeState.CERTIFIED,
        NodeState.OWNER_HELD,
        NodeState.MERGE_ELIGIBLE,
        NodeState.MERGED,
        NodeState.CLOSED,
    ],
)
def test_lease_succeeds_once_dependency_reaches_satisfied_state(satisfied_state: NodeState) -> None:
    gov = AutonomousGovernor(
        current_main=EXPECTED_BASE_MAIN,
        current_tree=EXPECTED_BASE_TREE,
        trusted_anchor=_anchor(),
    )
    gov.add_node(_node("PKG-PREREQ", state=satisfied_state, surface="pre", paths=("src/pre",)))
    gov.add_node(_node("PKG-DEPENDENT", deps=("PKG-PREREQ",)))
    lease = gov.lease("PKG-DEPENDENT", "governor-pilot-local", branch="feat/x", worktree="repo")
    assert lease.package_id == "PKG-DEPENDENT"


def test_lease_dependency_check_is_additive_for_dependency_free_pilot_node() -> None:
    """Backward compatibility: the pilot node (and every pre-existing
    caller) always has dependencies=() -- the new check must be a no-op
    for it, matching every other lease test in this module that never
    passes `deps=`.
    """
    gov = AutonomousGovernor(
        current_main=EXPECTED_BASE_MAIN,
        current_tree=EXPECTED_BASE_TREE,
        trusted_anchor=_anchor(),
    )
    gov.add_node(_node("PKG-A"))
    lease = gov.lease("PKG-A", "governor-pilot-local", branch="feat/x", worktree="repo")
    assert lease.package_id == "PKG-A"


def test_overlap_gate_blocks_shared_surface() -> None:
    left = _node("PKG-A", state=NodeState.LEASED, surface="shared", paths=("src/x",))
    right = _node("PKG-B", state=NodeState.READY, surface="shared", paths=("src/x",))
    assert would_overlap((left,), right)
    decision = overlap_gate((left, right.model_copy(update={"state": NodeState.ACTIVE})))
    assert decision.parallel_execution is False
    assert "shared" in decision.conflict_surfaces


def test_overlap_allows_disjoint_surfaces() -> None:
    left = _node(
        "PKG-A",
        state=NodeState.LEASED,
        surface="one",
        semantic="SEM_ONE",
        paths=("src/one",),
    )
    right = _node(
        "PKG-B",
        state=NodeState.ACTIVE,
        surface="two",
        semantic="SEM_TWO",
        paths=("src/two",),
    )
    decision = overlap_gate((left, right))
    assert decision.parallel_execution is True


def test_continuation_stops_at_owner_gate() -> None:
    held = _node(
        "PKG-A",
        state=NodeState.OWNER_HELD,
        owner_gate=OwnerGateKind.A_PROTECTED_MAIN_MERGE,
    )
    decision = select_next((held,))
    assert decision.next_package_id is None
    assert decision.stop_reason is StopReason.OWNER_GATE


def test_continuation_selects_ready_when_no_owner_gate() -> None:
    ready = _node("PKG-B", state=NodeState.READY)
    decision = select_next((ready,))
    assert decision.next_package_id == "PKG-B"
    assert decision.stop_reason is None


@pytest.mark.parametrize(
    "gate",
    [
        OwnerGateKind.C_CERTIFIED_OBJECT_MUTATION,
        OwnerGateKind.D_SECURITY_GOVERNANCE_POLICY,
        OwnerGateKind.E_DESTRUCTIVE_OPS,
        OwnerGateKind.F_MATERIAL_EXTERNAL_SPEND,
    ],
)
def test_continuation_never_selects_ready_owner_gated_node(gate: OwnerGateKind) -> None:
    """ORCHAUT-010 regression: a node can reach READY (dependency-ready)
    while still carrying an owner_gate C-F tag. READY != owner-authorized.
    Prior to remediation, `select_next` only skipped owner-gated nodes when
    `state != READY`, which is never true inside the `ready` list -- so a
    READY node tagged C/D/E/F was selected and returned for lease exactly
    like an ungated node, silently bypassing the gate.
    """
    gated = _node("PKG-GATED", state=NodeState.READY, owner_gate=gate)
    decision = select_next((gated,))
    assert decision.next_package_id is None
    assert decision.stop_reason is StopReason.OWNER_GATE


def test_continuation_skips_owner_gated_ready_node_for_ungated_sibling() -> None:
    """A READY owner-gated node must not be selected even when a normal
    READY node is also available -- the gated one is skipped, not picked.
    """
    gated = _node(
        "PKG-GATED",
        state=NodeState.READY,
        owner_gate=OwnerGateKind.E_DESTRUCTIVE_OPS,
        surface="surface-gated",
        semantic="SEMANTIC_GATED",
        paths=("src/gated",),
    )
    ungated = _node("PKG-FREE", state=NodeState.READY)
    decision = select_next((gated, ungated))
    assert decision.next_package_id == "PKG-FREE"
    assert decision.stop_reason is None


def test_evidence_hash_is_reconstructable(tmp_path: Path) -> None:
    first = make_bundle("PILOT_EXECUTION", {"a": 1, "b": "x"})
    second = make_bundle("PILOT_EXECUTION", {"b": "x", "a": 1})
    assert first.payload_sha256 == second.payload_sha256
    path = write_bundle(tmp_path, "bundle.json", first)
    assert len(file_sha256(path)) == 64
    with pytest.raises(EvidenceError):
        write_bundle(tmp_path, "../escape.json", first)


def test_iv_implementer_cannot_verify() -> None:
    gov = AutonomousGovernor(
        current_main=EXPECTED_BASE_MAIN,
        current_tree=EXPECTED_BASE_TREE,
        trusted_anchor=_anchor(),
    )
    gov.add_node(
        _node(
            "PKG-A",
            capabilities=(AgentCapability.DISCOVER, AgentCapability.IMPLEMENT),
            owner_gate=OwnerGateKind.A_PROTECTED_MAIN_MERGE,
        )
    )
    gov.lease("PKG-A", "governor-pilot-local", branch="feat/x", worktree="repo")
    gov.execute_leased(gov.snapshot().leases[0].lease_id)
    verifier = gov.route_and_verify("PKG-A", implementer_id="governor-pilot-local")
    assert verifier == "governor-pilot-iv"
    assert verifier != "governor-pilot-local"


def test_remediation_blocks_after_three_cycles() -> None:
    node = _node("PKG-A")
    used = node
    for _ in range(MAX_AUTONOMOUS_REMEDIATION_CYCLES):
        used = consume_remediation_cycle(used)
    assert used.retry_policy.cycles_used == 3
    with pytest.raises(RemediationExhausted):
        consume_remediation_cycle(used)


def test_adversarial_trigger_for_control_plane() -> None:
    assert requires_adversarial_review((RiskTag.CONTROL_PLANE, RiskTag.AUTHORIZATION))


def test_discovery_selects_dispatch_primitive_not_closed_slots() -> None:
    report = discover(_inventory(), trusted=_anchor())
    assert report.case == "A-A-PREFLIGHT"
    assert report.selected_package_id is None
    assert report.blocker == "OWNER_GATE"
    rejected = {item.package_id: item.reason for item in report.candidates if not item.eligible}
    assert rejected["AS-ORCH-001D-R2"] == "SUPERSEDED_CLOSED_SEMANTIC_DELTA_ZERO"
    assert rejected["AS-ORCH-001D-R7"] == "OBSOLETE_NO_DEFINED_SEMANTIC"
    assert rejected["AS-ORCH-001E"] == "IMPLEMENTED_PENDING_OWNER_MERGE"
    assert rejected["AS-ORCH-001D-R6"] == "SUPERSEDED_CLOSED_DO_NOT_MUTATE_PR_396"
    assert rejected["AS-ORCH-001D"] == "MERGED_AND_SEALED_ON_TRUSTED_MAIN"
    assert PILOT_PACKAGE_ID in rejected


def test_live_inventory_fails_closed_without_git(tmp_path: Path) -> None:
    with pytest.raises(DiscoveryError):
        collect_live_inventory(tmp_path)


def test_live_inventory_does_not_walk_parent_repo() -> None:
    nested = Path(__file__).resolve().parents[2] / "src"
    with pytest.raises(DiscoveryError, match="not a git repository"):
        collect_live_inventory(nested)


def test_discovery_drift_is_case_a_b() -> None:
    report = discover(
        _inventory(current_main="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
        trusted=_anchor(),
    )
    assert report.case == "A-B"
    assert report.blocker == "TARGET_MOVED"


def test_governor_plan_answers() -> None:
    gov = AutonomousGovernor(
        current_main=EXPECTED_BASE_MAIN,
        current_tree=EXPECTED_BASE_TREE,
        trusted_anchor=_anchor(),
    )
    gov.add_node(_node("PKG-A", owner_gate=OwnerGateKind.A_PROTECTED_MAIN_MERGE))
    gov.add_node(
        _node(
            "PKG-B",
            state=NodeState.BLOCKED,
            surface="other",
            semantic="SEM_B",
            paths=("src/b",),
        )
    )
    plan = gov.plan()
    assert "PKG-A" in plan.what_can_run_now
    assert "PKG-B" in plan.what_must_wait
    assert "PKG-A" in plan.what_requires_owner_authority
    assert plan.merge_authorized is False


def test_controlled_pilot_stops_at_owner_gate(tmp_path: Path) -> None:
    gov = AutonomousGovernor(
        current_main=EXPECTED_BASE_MAIN,
        current_tree=EXPECTED_BASE_TREE,
        trusted_anchor=_anchor(),
    )
    result = gov.run_controlled_pilot(
        _inventory(),
        branch="feat/as-orch-autonomy-001",
        worktree="repo",
        evidence_dir=tmp_path,
    )
    assert result["discovered"] is True
    assert result["selected_package_id"] is None
    assert result["implementer_equals_verifier"] is False
    assert result["stop_reason"] == "OWNER_GATE"
    assert result["node_state"] == "OWNER_HELD"
    assert result["merge_authorized"] is False
    assert result["r2_created"] == "NO"
    assert result["as_orch_001e_started"] == "NO"
    assert (tmp_path / "pilot-evidence.json").is_file()


def test_controlled_pilot_bounded_remediation() -> None:
    gov = AutonomousGovernor(
        current_main=EXPECTED_BASE_MAIN,
        current_tree=EXPECTED_BASE_TREE,
        trusted_anchor=_anchor(),
    )
    result = gov.run_controlled_pilot(
        _inventory(),
        branch="feat/as-orch-autonomy-001",
        worktree="repo",
        inject_iv_failure=True,
    )
    assert result["remediation_cycles"] == 1
    assert result["node_state"] == "OWNER_HELD"


def test_cli_governor_discover(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    inventory = tmp_path / "inv.json"
    inventory.write_text(
        _inventory(
            current_main=INITIAL_RETARGET_MAIN, current_tree=INITIAL_RETARGET_TREE
        ).model_dump_json(),
        encoding="utf-8",
    )
    code = main(
        [
            "orchestrator",
            "governor-discover",
            "--root",
            str(tmp_path),
            "--inventory",
            str(inventory),
        ]
    )
    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["selected_package_id"] is None
    assert payload["blocker"] == "OWNER_GATE"


def test_cli_governor_pilot(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    inventory = tmp_path / "inv.json"
    inventory.write_text(
        _inventory(
            current_main=INITIAL_RETARGET_MAIN, current_tree=INITIAL_RETARGET_TREE
        ).model_dump_json(),
        encoding="utf-8",
    )
    evidence = tmp_path / "ev"
    code = main(
        [
            "orchestrator",
            "governor-pilot",
            "--root",
            str(tmp_path),
            "--inventory",
            str(inventory),
            "--evidence-dir",
            str(evidence),
        ]
    )
    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["package_id"] == "AS-ORCH-AUTONOMY-001"
    assert payload["merge_authorized"] is False


def test_cli_governor_status_moved_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def _fake_inventory(_root: Path) -> LiveInventory:
        return _inventory(current_main="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")

    monkeypatch.setattr(
        "project_atlas.orchestration.autonomy.cli.collect_live_inventory",
        _fake_inventory,
    )
    code = main(["orchestrator", "governor-status", "--root", str(tmp_path)])
    assert code == EXIT_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["target_moved"] is True


def test_001a_authority_invariants_untouched() -> None:
    from project_atlas.orchestration.models import (
        NextTransition,
        OrchestrationDecision,
        WorkflowState,
    )

    decision = OrchestrationDecision(
        valid=True,
        workflow_state=WorkflowState.OWNER_REQUIRED,
        next_transition=NextTransition.OWNER_REQUIRED,
        owner_required=True,
    )
    assert decision.execution_authorized is False
    assert decision.merge_authorized is False
