"""AS-ORIG-PROJECTION-FORGE-001 — omitted owner_gate is not an O1 grant.

A schema-valid hand-written MATERIALIZED origination row (empty proposal /
policy_result) is accepted by ``list_materialized_work_nodes``. Stored
``WorkNode.owner_gate`` is recovery evidence, not authority. ``lease()``
must recompute the gate from ``mutation_surface.paths`` so a security
surface cannot be leased by leaving the stored gate unset.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.orchestration.autonomy.governor import AutonomousGovernor, GovernorError
from project_atlas.orchestration.autonomy.models import (
    CANONICAL_REPOSITORY_IDENTITY,
    EXPECTED_BASE_MAIN,
    EXPECTED_BASE_TREE,
    ORIGINATION_SURFACE_SEMANTIC,
    AdvancementReason,
    AgentCapability,
    ExecutionHostClass,
    IvRequirements,
    MutationSurface,
    NodeState,
    OwnerGateKind,
    TrustedAnchorRecord,
    WorkNode,
)
from project_atlas.orchestration.autonomy.trust import seal_anchor
from project_atlas.orchestration.origination.materialize import owner_gate_for
from project_atlas.orchestration.origination.projection import (
    OriginationRecord,
    list_materialized_work_nodes,
)
from project_atlas.orchestration.origination.risk import classify

PIN = EXPECTED_BASE_MAIN
SECURITY_PATH = "src/security/auth.py"
HONEST_O1_PATH = "docs/readme.md"


def _anchor() -> TrustedAnchorRecord:
    predecessor = "1111111111111111111111111111111111111111"
    certified = "3333333333333333333333333333333333333333"
    return seal_anchor(
        TrustedAnchorRecord(
            repository_identity=CANONICAL_REPOSITORY_IDENTITY,
            trusted_main=PIN,
            trusted_tree=EXPECTED_BASE_TREE,
            predecessor_main=predecessor,
            predecessor_tree="2222222222222222222222222222222222222222",
            advancement_reason=AdvancementReason.VERIFIED_OWNER_AUTHORIZED_MERGE,
            source_package="AS-ORIG-PROJECTION-FORGE-001",
            source_directive="P1-ORIG-PROJECTION-FORGE-001",
            source_pr=1,
            merge_commit=PIN,
            merge_parent_1=predecessor,
            merge_parent_2=certified,
            merge_tree=EXPECTED_BASE_TREE,
            certified_head=certified,
            certified_tree=EXPECTED_BASE_TREE,
            certification_status="CERTIFIED",
            independent_verification_status="PASS",
            post_merge_seal="PASS",
            post_merge_ci="PASS",
            evidence_reference="tests/unit/test_as_orig_projection_forge_001.py",
            evidence_digest="cc" * 32,
            sequence=1,
            record_digest="00" * 32,
        )
    )


def _node(
    package_id: str,
    *,
    paths: tuple[str, ...],
    owner_gate: OwnerGateKind | None = None,
    semantic: str = "TEST",
    origination_identity: str | None = None,
    state: NodeState = NodeState.READY,
) -> WorkNode:
    return WorkNode(
        package_id=package_id,
        objective="origination forge fixture",
        base_pin=PIN,
        mutation_surface=MutationSurface(
            surface_id="orig-forge-surface",
            paths=paths,
            semantic=semantic,
        ),
        execution_host_class=ExecutionHostClass.IN_PROCESS,
        agent_capabilities_required=(AgentCapability.IMPLEMENT,),
        acceptance_criteria=("BOUNDED_SPECIFICATION_CRITERION",),
        iv_requirements=IvRequirements(certification_required=True),
        owner_gate=owner_gate,
        origination_identity=origination_identity,
        state=state,
    )


def _governor() -> AutonomousGovernor:
    return AutonomousGovernor(
        current_main=PIN,
        current_tree=EXPECTED_BASE_TREE,
        trusted_anchor=_anchor(),
    )


def test_honest_classify_marks_security_path_owner_held() -> None:
    classification = classify(
        proposed_scope=(SECURITY_PATH,),
        success_criteria=("BOUNDED_SPECIFICATION_CRITERION",),
    )
    assert owner_gate_for(classification) is OwnerGateKind.D_SECURITY_GOVERNANCE_POLICY


def test_forged_ungated_security_surface_cannot_lease_without_owner_grant() -> None:
    gov = _governor()
    gov.add_node(_node("ORIG-forge-ungated", paths=(SECURITY_PATH,), owner_gate=None))
    with pytest.raises(GovernorError) as exc_info:
        gov.lease(
            "ORIG-forge-ungated",
            "governor-pilot-local",
            branch="feat/forge",
            worktree="wt",
            owner_grant=False,
        )
    assert exc_info.value.code == "OWNER_GATE_REQUIRED"
    assert gov.snapshot().nodes[0].state is NodeState.READY


def test_forged_ungated_security_surface_leases_with_explicit_owner_grant() -> None:
    gov = _governor()
    gov.add_node(_node("ORIG-forge-granted", paths=(SECURITY_PATH,), owner_gate=None))
    lease = gov.lease(
        "ORIG-forge-granted",
        "governor-pilot-local",
        branch="feat/forge",
        worktree="wt",
        owner_grant=True,
    )
    assert lease.package_id == "ORIG-forge-granted"


def test_honest_o1_omitted_gate_still_leases() -> None:
    gov = _governor()
    gov.add_node(_node("ORIG-honest-o1", paths=(HONEST_O1_PATH,), owner_gate=None))
    lease = gov.lease(
        "ORIG-honest-o1",
        "governor-pilot-local",
        branch="feat/o1",
        worktree="wt",
        owner_grant=False,
    )
    assert lease.package_id == "ORIG-honest-o1"


def test_handwritten_materialized_projection_cannot_lease_ungated_security(
    tmp_path: Path,
) -> None:
    """Projection is not authority: empty proposal/policy_result + omitted
    owner_gate on a security path must still fail closed at lease()."""
    identity = "a" * 64
    forged = _node(
        "ORIG-forge-projection",
        paths=(SECURITY_PATH,),
        owner_gate=None,
        origination_identity=identity,
        semantic=ORIGINATION_SURFACE_SEMANTIC,
        state=NodeState.DISCOVERED,
    )
    store = tmp_path / "origination-store"
    store.mkdir()
    (store / "origination.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "package": "AS-ORCH-ORIGINATION-PROJECTION-001",
                "records": [
                    OriginationRecord(
                        origination_identity=identity,
                        project_id="demo",
                        proposal={},
                        policy_result={},
                        work_node=forged.model_dump(mode="json"),
                        state="MATERIALIZED",
                    ).model_dump(mode="json"),
                ],
            }
        ),
        encoding="utf-8",
    )
    loaded = list_materialized_work_nodes(store)
    assert len(loaded) == 1
    assert loaded[0].owner_gate is None
    assert loaded[0].mutation_surface.paths == (SECURITY_PATH,)

    gov = AutonomousGovernor(
        current_main=PIN,
        current_tree=EXPECTED_BASE_TREE,
        trusted_anchor=_anchor(),
        origination_projection_store=store,
    )
    gov.add_node(loaded[0])
    gov.mark_ready(loaded[0].package_id)
    with pytest.raises(GovernorError) as exc_info:
        gov.lease(
            loaded[0].package_id,
            "governor-pilot-local",
            branch="feat/forge",
            worktree="wt",
            owner_grant=False,
        )
    assert exc_info.value.code == "OWNER_GATE_REQUIRED"
