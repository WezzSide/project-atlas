"""AS-LEASE-RELEASED-CERTIFIED-WITNESS-001 — planted RELEASED is not CERTIFIED.

``leases.json`` ``status=RELEASED`` is recovery evidence, not a completion
proof. ``DURABLE_PROJECTION_IS_AUTHORITY = NO``. A hand-written RELEASED
row without a matching ``LoopState.completed_lease_ids`` entry must not
stamp CERTIFIED or unblock dependents.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from project_atlas.orchestration.autonomy.continuation import select_next
from project_atlas.orchestration.autonomy.discovery import collect_live_inventory
from project_atlas.orchestration.autonomy.governor import AutonomousGovernor
from project_atlas.orchestration.autonomy.lease_projection import (
    PROJECTION_NAME as LEASE_PROJECTION_NAME,
)
from project_atlas.orchestration.autonomy.models import (
    CANONICAL_REPOSITORY_IDENTITY,
    AdvancementReason,
    AgentCapability,
    ExecutionHostClass,
    IvRequirements,
    MutationSurface,
    NodeState,
    TrustedAnchorRecord,
    WorkNode,
)
from project_atlas.orchestration.autonomy.rehydration import rehydrate_governor
from project_atlas.orchestration.autonomy.trust import (
    initialize_store,
    load_runtime_anchor,
    seal_anchor,
)
from project_atlas.orchestration.origination.projection import OriginationRecord

_TEST_IV = IvRequirements(certification_required=True, adversarial_required=False)


def _run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "init")
    sha = _run_git(repo, "rev-parse", "HEAD")
    _run_git(repo, "update-ref", "refs/remotes/origin/main", sha)
    return repo


def _anchor(main: str, tree: str) -> TrustedAnchorRecord:
    return seal_anchor(
        TrustedAnchorRecord(
            repository_identity=CANONICAL_REPOSITORY_IDENTITY,
            trusted_main=main,
            trusted_tree=tree,
            predecessor_main="a" * 40,
            predecessor_tree="b" * 40,
            advancement_reason=AdvancementReason.VERIFIED_OWNER_AUTHORIZED_MERGE,
            source_package="AS-LEASE-RELEASED-CERTIFIED-WITNESS-001",
            source_directive="P1-LEASE-RELEASED-CERTIFIED-WITNESS-001",
            source_pr=1,
            merge_commit=main,
            merge_parent_1="a" * 40,
            merge_parent_2=main,
            merge_tree=tree,
            certified_head=main,
            certified_tree=tree,
            certification_status="CERTIFIED",
            independent_verification_status="PASS",
            post_merge_seal="PASS",
            post_merge_ci="PASS",
            evidence_reference="tests/unit/test_as_lease_released_certified_witness_001.py",
            evidence_digest="ee" * 32,
            sequence=1,
            record_digest="00" * 32,
        )
    )


def _node(
    package_id: str,
    *,
    base_pin: str,
    paths: tuple[str, ...],
    surface_id: str,
    dependencies: tuple[str, ...] = (),
) -> WorkNode:
    return WorkNode(
        package_id=package_id,
        objective="released-witness fixture",
        base_pin=base_pin,
        dependencies=dependencies,
        mutation_surface=MutationSurface(surface_id=surface_id, paths=paths, semantic="TEST"),
        execution_host_class=ExecutionHostClass.IN_PROCESS,
        agent_capabilities_required=(AgentCapability.IMPLEMENT,),
        acceptance_criteria=("TEST_ACCEPTANCE",),
        iv_requirements=_TEST_IV,
    )


def _write_projection(store: Path, records: list[OriginationRecord]) -> None:
    store.mkdir()
    (store / "origination.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "package": "AS-ORCH-ORIGINATION-PROJECTION-001",
                "records": [row.model_dump(mode="json") for row in records],
            }
        ),
        encoding="utf-8",
    )


def _write_released_lease(store: Path, *, package_id: str, base_pin: str, paths: list[str]) -> None:
    store.mkdir()
    (store / LEASE_PROJECTION_NAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "package": "AS-ORCH-DURABLE-LEASE-PROJECTION-001",
                "honesty": {
                    "projection_is_authority": False,
                    "grant_source": "PRIMARY_GOVERNOR",
                    "ack_source": "PRIMARY_GOVERNOR",
                    "wall_clock_is_authority": False,
                },
                "leases": [
                    {
                        "lease_id": "LEASE-planted",
                        "agent_id": "governor-pilot-local",
                        "package_id": package_id,
                        "branch": "feat/planted",
                        "worktree": "wt",
                        "base_pin": base_pin,
                        "authorized_paths": paths,
                        "forbidden_paths": ["main", "projects"],
                        "capabilities": ["IMPLEMENT"],
                        "start_state": "READY",
                        "status": "RELEASED",
                        "created_sequence": 1,
                        "released_sequence": 1,
                        "projection_is_authority": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_planted_released_row_is_not_a_certified_witness(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    trust_store = tmp_path / "trust"
    initialize_store(trust_store, _anchor(main, tree))
    trusted = load_runtime_anchor(store=trust_store)
    inventory = collect_live_inventory(repo)
    governor = AutonomousGovernor(
        current_main=inventory.current_main,
        current_tree=inventory.current_tree,
        trusted_anchor=trusted,
    )

    dependency = _node("ORIG-dep", base_pin=main, paths=("src/dep/",), surface_id="dep-surface")
    dependent = _node(
        "ORIG-next",
        base_pin=main,
        paths=("src/next/",),
        surface_id="next-surface",
        dependencies=("ORIG-dep",),
    )
    origination_store = tmp_path / "origination-store"
    _write_projection(
        origination_store,
        [
            OriginationRecord(
                origination_identity="a" * 64,
                project_id="demo",
                proposal={},
                policy_result={},
                work_node=dependency.model_dump(mode="json"),
                state="MATERIALIZED",
            ),
            OriginationRecord(
                origination_identity="b" * 64,
                project_id="demo",
                proposal={},
                policy_result={},
                work_node=dependent.model_dump(mode="json"),
                state="MATERIALIZED",
            ),
        ],
    )
    lease_store = tmp_path / "lease-projection"
    _write_released_lease(lease_store, package_id="ORIG-dep", base_pin=main, paths=["src/dep/"])

    # No loop store / STATE_MISSING: completed_lease_ids is empty.
    rehydrate_governor(
        governor,
        inventory=inventory,
        trusted=trusted,
        loop_store=tmp_path / "loop-state-missing",
        lease_projection_store=lease_store,
        origination_projection_store=origination_store,
    )

    by_id = {node.package_id: node for node in governor.snapshot().nodes}
    assert "ORIG-dep" not in by_id
    assert by_id["ORIG-next"].state == NodeState.READY
    decision = select_next(governor.snapshot().nodes)
    assert decision.next_package_id is None
