"""D-PHASE2A-2: origination -> live governed DAG bridge.

Focused, integration, and adversarial coverage for the wiring closed here:
what `orchestration.origination.cli.run_origination_scan()` durably
materializes now actually reaches `orchestration.autonomy.cli.
run_governor_loop_tick()`'s governor (discovery), and a governed node that
reaches `dag.TERMINAL_STATES` is synced back to the origination projection
as `TERMINAL` (write-back) -- explicitly deferred by D-PHASE2A-1
("Wiring origination into the live governed DAG/lease/dispatch loop...
a separate later PR", `docs/backlog.md`).

Contract this file exists to prove, matching the directive's own diagram:

    ORIGINATION_PROPOSAL -> MATERIALIZED_WORK_NODE -> DURABLE_PROJECTION
        -> GOVERNOR DISCOVERY/REHYDRATION -> READY ONLY WHEN ELIGIBLE
        -> DEPENDENCY CHECK -> OWNER-GATE CHECK -> LEASE -> EXECUTION

    PROPOSAL != EXECUTION_AUTHORITY
    MATERIALIZED != READY (governor.add_node() alone never marks READY)
    READY != LEASE (an owner-gated or dependency-blocked READY node is
        never autonomously leased)
    LEASE != DISPATCH
    CERTIFIED != MERGED
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

import pytest

from project_atlas.orchestration.autonomy.cli import run_governor_loop_tick
from project_atlas.orchestration.autonomy.continuation import select_next
from project_atlas.orchestration.autonomy.dag import TERMINAL_STATES
from project_atlas.orchestration.autonomy.governor import AutonomousGovernor, GovernorError
from project_atlas.orchestration.autonomy.lease_projection import (
    PROJECTION_NAME as LEASE_PROJECTION_NAME,
)
from project_atlas.orchestration.autonomy.lease_projection import (
    RELATIVE_DEFAULT as LEASE_PROJECTION_RELATIVE_DEFAULT,
)
from project_atlas.orchestration.autonomy.lease_projection import (
    LeaseProjection,
    ProjectedLease,
)
from project_atlas.orchestration.autonomy.lease_projection import (
    load_projection as load_lease_projection,
)
from project_atlas.orchestration.autonomy.loop import AutonomousLoop, LoopPhase
from project_atlas.orchestration.autonomy.models import (
    CANONICAL_REPOSITORY_IDENTITY,
    ORIGINATION_SURFACE_SEMANTIC,
    AdvancementReason,
    AgentCapability,
    ExecutionHostClass,
    IvRequirements,
    MutationSurface,
    NodeState,
    OwnerGateKind,
    StopReason,
    TrustedAnchorRecord,
    WorkNode,
)
from project_atlas.orchestration.autonomy.rehydration import RehydrationError, rehydrate_governor
from project_atlas.orchestration.autonomy.trust import (
    initialize_store,
    load_runtime_anchor,
    seal_anchor,
)
from project_atlas.orchestration.origination.cli import run_origination_scan
from project_atlas.orchestration.origination.projection import (
    RELATIVE_DEFAULT as ORIGINATION_PROJECTION_RELATIVE_DEFAULT,
)
from project_atlas.orchestration.origination.projection import (
    OriginationRecord,
    list_materialized_work_nodes,
    load_projection,
    mark_terminal,
    sync_terminal_governed_states,
)

_TEST_IV_REQUIREMENTS = IvRequirements(certification_required=True, adversarial_required=False)


def _run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _make_repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
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
            source_package="D-PHASE2A-2-TEST",
            source_directive="D-PHASE2A-2-TEST-FIXTURE",
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
            evidence_reference="tests/fixtures/d-phase2a-2.json",
            evidence_digest="cd" * 32,
            sequence=1,
            record_digest="00" * 32,
        )
    )


def _minimal_work_node(
    package_id: str,
    *,
    base_pin: str,
    surface_id: str,
    paths: tuple[str, ...] = ("src/",),
    state: NodeState = NodeState.DISCOVERED,
    dependencies: tuple[str, ...] = (),
) -> WorkNode:
    """A structurally-valid, minimal WorkNode for tests that only care
    about package_id/mutation_surface/state -- every other field is a
    fixed, arbitrary-but-valid placeholder."""
    return WorkNode(
        package_id=package_id,
        objective="test fixture node",
        base_pin=base_pin,
        dependencies=dependencies,
        mutation_surface=MutationSurface(surface_id=surface_id, paths=paths, semantic="TEST"),
        execution_host_class=ExecutionHostClass.IN_PROCESS,
        agent_capabilities_required=(AgentCapability.IMPLEMENT,),
        acceptance_criteria=("TEST_ACCEPTANCE",),
        iv_requirements=_TEST_IV_REQUIREMENTS,
        state=state,
    )


def _make_trust_store(tmp_path: Path, main: str, tree: str, name: str = "trust") -> Path:
    store = tmp_path / name
    initialize_store(store, _anchor(main, tree))
    return store


def _write_roadmap(root: Path, items: list[dict[str, object]]) -> None:
    (root / "docs").mkdir(parents=True, exist_ok=True)
    fence = json.dumps({"roadmap_items": items}, indent=2)
    (root / "docs" / "ROADMAP.md").write_text(
        f"## Roadmap record\n```json\n{fence}\n```\n", encoding="utf-8"
    )


def _write_skipped_test(root: Path, rel_path: str) -> None:
    full = root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(
        'import pytest\n\npytestmark = pytest.mark.skip(reason="not yet implemented")\n\n'
        "def test_placeholder():\n    assert True\n",
        encoding="utf-8",
    )


def _write_plain_file(root: Path, rel_path: str, content: str = "# doc\n") -> None:
    full = root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")


def _eligible_repo(tmp_path: Path, *, item_id: str = "feature-x", name: str = "repo") -> Path:
    repo = _make_repo(tmp_path, name)
    _write_plain_file(repo, "docs/REQUIREMENTS.md", "# Requirements\nFR-1: do the thing.\n")
    _write_skipped_test(repo, f"tests/test_{item_id.replace('-', '_')}.py")
    _write_roadmap(
        repo,
        [
            {
                "id": item_id,
                "title": item_id,
                "status": "NOT_STARTED",
                "lifecycle": "READY",
                "evidence": [
                    "docs/REQUIREMENTS.md",
                    f"tests/test_{item_id.replace('-', '_')}.py",
                ],
            }
        ],
    )
    return repo


# --------------------------------------------------------------------------- #
# Focused: the wiring itself -- discovery, ready-only-when-eligible, lease.
# --------------------------------------------------------------------------- #
def test_run_governor_loop_tick_discovers_and_leases_materialized_origination_node(
    tmp_path: Path,
) -> None:
    """The core D-PHASE2A-2 proof: a real origination scan's durable output
    is picked up by the live tick entry point, with no manual `add_node()`/
    `mark_ready()` call in between -- the exact gap D-PHASE2A-1 deferred.
    """
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    origination_store = tmp_path / "origination-store"
    trust_store = _make_trust_store(tmp_path, main, tree)

    scan_payload, scan_exit = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=origination_store,
        trust_store=trust_store,
    )
    assert scan_exit == 0
    assert scan_payload["materialized_count"] == 1
    work_id = scan_payload["materialized"][0]["work_id"]  # type: ignore[index]

    # MATERIALIZED != READY: before any tick, the durable record exists,
    # but no governor has ever seen it -- proven by reading the record
    # directly, independent of the tick this test is about to run.
    nodes_before_tick = list_materialized_work_nodes(origination_store)
    assert any(n.package_id == work_id for n in nodes_before_tick)
    assert all(n.state == NodeState.DISCOVERED for n in nodes_before_tick)

    payload, exit_code = run_governor_loop_tick(
        root=repo,
        trust_store=trust_store,
        origination_store=origination_store,
    )
    assert exit_code == 0
    assert payload["merge_authorized"] is False
    assert payload["execution_authorized"] is False
    # The tick must have found and leased this node -- not the hardcoded
    # pilot, which discover()'s real candidates are never eligible=True for.
    assert payload["package_id"] == "AS-ORCH-001E"
    lease_projection = load_lease_projection(repo / LEASE_PROJECTION_RELATIVE_DEFAULT)
    assert any(row.package_id == work_id for row in lease_projection.leases)


# --------------------------------------------------------------------------- #
# Terminal sync-back (write-back half of the bridge).
# --------------------------------------------------------------------------- #
def test_terminal_governed_state_syncs_back_to_origination_projection(tmp_path: Path) -> None:
    """Once a governed node reaches dag.TERMINAL_STATES (CLOSED), the
    durable origination record must be marked TERMINAL so a later
    successor scan correctly excludes it -- proven directly against
    sync_terminal_governed_states(), the write-back function itself,
    rather than driving a full autonomous flow all the way to CLOSED
    (which requires IV/verification steps this bridge does not itself
    perform -- see AUTONOMOUS_IMPLEMENTATION_EXECUTION's own honesty
    boundary, D-PHASE2A-1).
    """
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    origination_store = tmp_path / "origination-store"

    scan_payload, _ = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=origination_store,
        explicit_trusted=_anchor(main, tree),
    )
    work_id = scan_payload["materialized"][0]["work_id"]  # type: ignore[index]

    record_before = next(
        r
        for r in load_projection(origination_store).records
        if r.work_node is not None and r.work_node.get("package_id") == work_id
    )
    assert record_before.state == "MATERIALIZED"

    closed_node = WorkNode.model_validate(record_before.work_node).model_copy(
        update={"state": NodeState.CLOSED}
    )
    assert closed_node.state in TERMINAL_STATES

    synced = sync_terminal_governed_states(origination_store, [closed_node])
    assert synced == (record_before.origination_identity,)

    record_after = next(
        r
        for r in load_projection(origination_store).records
        if r.origination_identity == record_before.origination_identity
    )
    assert record_after.state == "TERMINAL"
    assert record_after.terminal_node_state == "CLOSED"

    # A second sync call is a safe no-op: the record is already TERMINAL,
    # so it's correctly excluded from consideration the second time.
    synced_again = sync_terminal_governed_states(origination_store, [closed_node])
    assert synced_again == ()


def test_non_closed_states_are_not_synced_as_terminal(tmp_path: Path) -> None:
    """OWNER_HELD (or any other non-CLOSED state) must NOT be synced --
    deliberately narrower than mark_terminal()'s own broader example
    list, per sync_terminal_governed_states()'s documented scoping
    decision."""
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    origination_store = tmp_path / "origination-store"

    scan_payload, _ = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=origination_store,
        explicit_trusted=_anchor(main, tree),
    )
    work_id = scan_payload["materialized"][0]["work_id"]  # type: ignore[index]
    record = next(
        r
        for r in load_projection(origination_store).records
        if r.work_node is not None and r.work_node.get("package_id") == work_id
    )
    for not_closed in (
        NodeState.CERTIFIED,
        NodeState.OWNER_HELD,
        NodeState.BLOCKED,
        NodeState.MERGED,
        NodeState.MERGE_ELIGIBLE,
    ):
        node = WorkNode.model_validate(record.work_node).model_copy(update={"state": not_closed})
        assert sync_terminal_governed_states(origination_store, [node]) == ()
    # Confirm the record genuinely never moved.
    still_materialized = next(
        r
        for r in load_projection(origination_store).records
        if r.origination_identity == record.origination_identity
    )
    assert still_materialized.state == "MATERIALIZED"


def test_sync_skips_ambiguous_package_id_with_multiple_active_records(tmp_path: Path) -> None:
    """D-PHASE2A-2 independent-IV finding, round 2: `sync_terminal_governed_
    states()` matches purely by `package_id`. If more than one non-TERMINAL
    durable record ever shares one `package_id` (should not occur once
    `origination/cli.py`'s `persist_materialized_if_no_active_conflict()`
    guard is in place prospectively -- proven separately in
    `test_orchestration_origination_cli.py` -- but this function must not
    assume that invariant holds for every store it is ever handed, e.g. one
    written before this fix, or a future bug elsewhere), it must refuse to
    guess which one actually produced the closed governed node: NEITHER is
    marked TERMINAL, not one chosen arbitrarily. Picking wrong would
    permanently and silently close a genuinely distinct, never-executed
    proposal."""
    origination_store = tmp_path / "origination-store"
    origination_store.mkdir()
    main = "a" * 40
    first = _minimal_work_node(
        "ORIG-ambiguous", base_pin=main, surface_id="first-surface", paths=("src/first/",)
    )
    second = _minimal_work_node(
        "ORIG-ambiguous", base_pin=main, surface_id="second-surface", paths=("src/second/",)
    )
    (origination_store / "origination.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "package": "AS-ORCH-ORIGINATION-PROJECTION-001",
                "records": [
                    OriginationRecord(
                        origination_identity="a" * 64,
                        project_id="demo",
                        proposal={},
                        policy_result={},
                        work_node=first.model_dump(mode="json"),
                        state="MATERIALIZED",
                    ).model_dump(mode="json"),
                    OriginationRecord(
                        origination_identity="b" * 64,
                        project_id="demo",
                        proposal={},
                        policy_result={},
                        work_node=second.model_dump(mode="json"),
                        state="MATERIALIZED",
                    ).model_dump(mode="json"),
                ],
            }
        ),
        encoding="utf-8",
    )

    closed_node = first.model_copy(update={"state": NodeState.CLOSED})
    synced = sync_terminal_governed_states(origination_store, [closed_node])
    assert synced == ()

    # Neither record moved -- both still MATERIALIZED, ambiguity preserved
    # honestly rather than resolved by guessing.
    records_after = load_projection(origination_store).records
    assert {r.state for r in records_after} == {"MATERIALIZED"}
    assert len(records_after) == 2

    # Order-independence: the SAME ambiguity, but the governed node
    # observed as CLOSED is built from `second` instead of `first`. The
    # verdict must be identical -- neither record is ever synced, no
    # matter which of the two ambiguous rows the closed node's own
    # fields happen to resemble (the function never actually inspects
    # which one -- it keys purely on package_id -- this just confirms
    # that symmetry holds and nothing about `second` accidentally
    # resolves the ambiguity `first` didn't).
    closed_node_via_second = second.model_copy(update={"state": NodeState.CLOSED})
    synced_reverse = sync_terminal_governed_states(origination_store, [closed_node_via_second])
    assert synced_reverse == ()
    records_after_reverse = load_projection(origination_store).records
    assert {r.state for r in records_after_reverse} == {"MATERIALIZED"}
    assert len(records_after_reverse) == 2


def test_sync_does_not_close_a_revision_that_superseded_the_actually_closed_one(
    tmp_path: Path,
) -> None:
    """AS-ORIGIN-MATERIALIZED-SUPERSESSION-001 independent-IV finding
    (chatgpt-codex-connector, PR #677, P1): if revision A is governed
    in-flight, a scan supersedes it with revision B (which materializes
    and becomes the SOLE active row for the package_id), and A's own
    governed node THEN reaches CLOSED (a real, legitimate close of the
    OLD, already-superseded work), the naive "exactly one active row for
    this package_id" check must NOT mark B TERMINAL -- B was never
    executed at all. `WorkNode` carries no `origination_identity`, so
    the closed node (built to look exactly like A) cannot be
    distinguished from B by package_id alone; the fix is conservative:
    any package_id that has EVER had more than one revision (active or
    SUPERSEDED) is never auto-synced.
    """
    origination_store = tmp_path / "origination-store"
    origination_store.mkdir()
    main = "a" * 40
    revision_a = _minimal_work_node(
        "ORIG-swapped", base_pin=main, surface_id="revision-a-surface", paths=("src/a/",)
    )
    revision_b = _minimal_work_node(
        "ORIG-swapped", base_pin=main, surface_id="revision-b-surface", paths=("src/b/",)
    )
    (origination_store / "origination.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "package": "AS-ORCH-ORIGINATION-PROJECTION-001",
                "records": [
                    OriginationRecord(
                        origination_identity="a" * 64,
                        project_id="demo",
                        proposal={"work_id": "ORIG-swapped"},
                        policy_result={},
                        work_node=revision_a.model_dump(mode="json"),
                        state="SUPERSEDED",
                    ).model_dump(mode="json"),
                    OriginationRecord(
                        origination_identity="b" * 64,
                        project_id="demo",
                        proposal={"work_id": "ORIG-swapped"},
                        policy_result={},
                        work_node=revision_b.model_dump(mode="json"),
                        state="MATERIALIZED",
                    ).model_dump(mode="json"),
                ],
            }
        ),
        encoding="utf-8",
    )

    # A's OWN governed node reaches CLOSED -- structurally identical to
    # revision_a (same package_id/base_pin/surface), exactly what a real
    # in-flight governor would report for the work it was actually
    # tracking.
    closed_a = revision_a.model_copy(update={"state": NodeState.CLOSED})
    synced = sync_terminal_governed_states(origination_store, [closed_a])
    assert synced == ()  # nothing synced -- the ambiguity is refused, not guessed

    records_after = load_projection(origination_store).records
    states = {r.origination_identity: r.state for r in records_after}
    assert states["a" * 64] == "SUPERSEDED"  # unchanged
    # The critical assertion: B must NOT be silently marked TERMINAL for
    # A's closure -- B was never executed.
    assert states["b" * 64] == "MATERIALIZED"


def test_sync_ignores_nodes_that_do_not_match_any_origination_record(tmp_path: Path) -> None:
    """A CLOSED governor node whose package_id has no origination record
    at all (e.g. the hardcoded pilot node) must not raise or otherwise
    misbehave -- it simply has nothing to sync."""
    origination_store = tmp_path / "origination-store"
    origination_store.mkdir()
    unrelated_node = _minimal_work_node(
        "orch-autonomy-pilot",
        base_pin="a" * 40,
        surface_id="unrelated-surface",
        state=NodeState.CLOSED,
    )
    assert sync_terminal_governed_states(origination_store, [unrelated_node]) == ()


# --------------------------------------------------------------------------- #
# READY != LEASE: owner-gated and dependency-blocked origination nodes are
# discovered and marked READY, but never autonomously leased.
# --------------------------------------------------------------------------- #
def test_owner_gated_origination_node_is_discovered_but_never_autonomously_leased(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    _write_plain_file(repo, "docs/REQUIREMENTS.md", "# Requirements\nFR-1: risky.\n")
    _write_plain_file(repo, "migrations/001_init.sql", "-- migration\n")
    _write_skipped_test(repo, "tests/test_risky.py")
    _write_roadmap(
        repo,
        [
            {
                "id": "risky-item",
                "title": "Risky Item",
                "status": "NOT_STARTED",
                "lifecycle": "READY",
                "evidence": [
                    "docs/REQUIREMENTS.md",
                    "migrations/001_init.sql",
                    "tests/test_risky.py",
                ],
            }
        ],
    )
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    origination_store = tmp_path / "origination-store"
    trust_store = _make_trust_store(tmp_path, main, tree)

    scan_payload, _ = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=origination_store,
        trust_store=trust_store,
    )
    entry = scan_payload["materialized"][0]  # type: ignore[index]
    assert entry["owner_gate"] in {kind.value for kind in OwnerGateKind}

    payload, exit_code = run_governor_loop_tick(
        root=repo, trust_store=trust_store, origination_store=origination_store
    )
    assert exit_code == 0
    # OWNER_GATE, never leased/dispatched -- proven by the loop's own
    # stop_reason, not merely by absence of an error.
    assert payload["stop_reason"] == "OWNER_GATE"
    assert payload["dispatched"] is False


# --------------------------------------------------------------------------- #
# Adversarial matrix: bridge-specific failure modes.
# --------------------------------------------------------------------------- #
def test_corrupt_materialized_work_node_is_skipped_not_fatal(tmp_path: Path) -> None:
    """One malformed durable record must not hide every other legitimate
    one -- list_materialized_work_nodes() skips it, not raises."""
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    origination_store = tmp_path / "origination-store"
    origination_store.mkdir()

    good_node = _minimal_work_node(
        "ORIG-good", base_pin=main, surface_id="good-surface", paths=("src/good/",)
    )
    projection_path = origination_store / "origination.json"
    projection_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "package": "AS-ORCH-ORIGINATION-PROJECTION-001",
                "records": [
                    OriginationRecord(
                        origination_identity="a" * 64,
                        project_id="demo",
                        proposal={},
                        policy_result={},
                        work_node=good_node.model_dump(mode="json"),
                        state="MATERIALIZED",
                    ).model_dump(mode="json"),
                    OriginationRecord(
                        origination_identity="b" * 64,
                        project_id="demo",
                        proposal={},
                        policy_result={},
                        work_node={"package_id": "ORIG-corrupt", "not": "a valid WorkNode"},
                        state="MATERIALIZED",
                    ).model_dump(mode="json"),
                ],
            }
        ),
        encoding="utf-8",
    )

    nodes = list_materialized_work_nodes(origination_store)
    assert {n.package_id for n in nodes} == {"ORIG-good"}


def test_duplicate_package_id_across_two_origination_records_fails_closed(
    tmp_path: Path,
) -> None:
    """AS-ORIGIN-MATERIALIZED-SUPERSESSION-001 (owner directive
    D-ATLAS-ORIGINATION-MATERIALIZED-REVISION-SUPERSESSION §7) SUPERSEDES
    this test's own prior behavior. Two DIFFERENT origination_identity
    records that somehow durably resolved to the SAME work_node.package_id
    (a data-integrity edge case: a corrupted/hand-edited store, a
    pre-supersession-migration store, or a future bug elsewhere) used to
    be silently resolved by `add_node()`'s own DUPLICATE_NODE check
    rejecting whichever arrived second (first-seen-in-file-order wins) --
    exactly the "pick one arbitrarily" the owner directive prohibits for
    an ambiguous active revision (§5 Case D, §7). `list_materialized_
    work_nodes()` now detects this itself and fails the WHOLE read closed
    (`AMBIGUOUS_ACTIVE_REVISION`) rather than silently choosing a winner
    by iteration order -- `rehydrate_governor()` converts this into a
    `RehydrationError`, exactly like an unreadable lease projection
    already fails this same pass closed above. This is a broader blast
    radius than isolating just the ambiguous package_id (an unrelated,
    unambiguous "ORIG-clean" candidate in the SAME store also fails to be
    discovered this tick) -- a deliberate choice: an ambiguous/corrupt
    origination store is a signal something already went wrong upstream
    of this read, and surfacing that loudly (a failed tick, visible in
    its own JSON payload) is safer than silently masking it by
    discovering everything else as if nothing were wrong.

    (A mutation-surface OVERLAP between two DISCOVERED/READY candidates
    is not itself an add_node()/mark_ready() failure -- would_overlap()
    is checked only at lease() time, by design, so two overlapping READY
    nodes coexisting in the DAG is not an error this pass needs to
    catch; only an actual attempt to lease both would be, and that is
    already covered by _select_and_lease()'s own SURFACE_OVERLAP
    handling, tested elsewhere. That case is unrelated to this one: this
    test is about two records sharing the SAME package_id, not two
    distinct package_ids whose mutation surfaces happen to overlap.)
    """
    repo = _make_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    trust_store = _make_trust_store(tmp_path, main, tree)
    trusted = load_runtime_anchor(store=trust_store)

    from project_atlas.orchestration.autonomy.discovery import collect_live_inventory

    inventory = collect_live_inventory(repo)
    governor = AutonomousGovernor(
        current_main=inventory.current_main,
        current_tree=inventory.current_tree,
        trusted_anchor=trusted,
    )

    origination_store = tmp_path / "origination-store"
    origination_store.mkdir()
    # Both records resolve to the SAME package_id -- the adversarial case.
    first = _minimal_work_node(
        "ORIG-dup", base_pin=main, surface_id="first-surface", paths=("src/first/",)
    )
    second = _minimal_work_node(
        "ORIG-dup", base_pin=main, surface_id="second-surface", paths=("src/second/",)
    )
    clean = _minimal_work_node(
        "ORIG-clean", base_pin=main, surface_id="clean-surface", paths=("src/clean/",)
    )
    (origination_store / "origination.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "package": "AS-ORCH-ORIGINATION-PROJECTION-001",
                "records": [
                    OriginationRecord(
                        origination_identity="a" * 64,
                        project_id="demo",
                        proposal={},
                        policy_result={},
                        work_node=first.model_dump(mode="json"),
                        state="MATERIALIZED",
                    ).model_dump(mode="json"),
                    OriginationRecord(
                        origination_identity="b" * 64,
                        project_id="demo",
                        proposal={},
                        policy_result={},
                        work_node=second.model_dump(mode="json"),
                        state="MATERIALIZED",
                    ).model_dump(mode="json"),
                    OriginationRecord(
                        origination_identity="c" * 64,
                        project_id="demo",
                        proposal={},
                        policy_result={},
                        work_node=clean.model_dump(mode="json"),
                        state="MATERIALIZED",
                    ).model_dump(mode="json"),
                ],
            }
        ),
        encoding="utf-8",
    )

    try:
        rehydrate_governor(
            governor,
            inventory=inventory,
            trusted=trusted,
            loop_store=tmp_path / "loop-state",
            lease_projection_store=tmp_path / "lease-projection",
            origination_projection_store=origination_store,
        )
        raise AssertionError("expected RehydrationError")
    except RehydrationError as exc:
        assert exc.code == "AMBIGUOUS_ACTIVE_REVISION"

    # Fails closed for the WHOLE pass -- neither "ORIG-dup" candidate was
    # picked, and the unrelated, unambiguous "ORIG-clean" candidate was
    # also NOT discovered this tick (broader blast radius, deliberately --
    # see the docstring above).
    package_ids = [n.package_id for n in governor.snapshot().nodes]
    assert "ORIG-dup" not in package_ids
    assert "ORIG-clean" not in package_ids


def test_second_tick_does_not_duplicate_already_discovered_node(tmp_path: Path) -> None:
    """A materialized node discovered on tick N must not be re-added (and
    raise DUPLICATE_NODE) on tick N+1 against the SAME still-non-terminal
    durable record -- the exact cross-process continuation scenario a
    real recurring loop produces."""
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    origination_store = tmp_path / "origination-store"
    trust_store = _make_trust_store(tmp_path, main, tree)

    run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=origination_store,
        trust_store=trust_store,
    )

    first_payload, first_exit = run_governor_loop_tick(
        root=repo, trust_store=trust_store, origination_store=origination_store
    )
    assert first_exit == 0
    assert first_payload["stop_reason"] != "FAILED_CLOSED"

    second_payload, second_exit = run_governor_loop_tick(
        root=repo, trust_store=trust_store, origination_store=origination_store
    )
    assert second_exit == 0
    assert second_payload["stop_reason"] != "FAILED_CLOSED"


def test_malformed_origination_projection_file_does_not_crash_tick(tmp_path: Path) -> None:
    """A corrupt origination.json must not crash run_governor_loop_tick()
    -- list_materialized_work_nodes() fails closed to empty, exactly like
    find_materialized_work_node() already does for the single-lookup
    case."""
    repo = _make_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    trust_store = _make_trust_store(tmp_path, main, tree)

    origination_store = tmp_path / "origination-store"
    origination_store.mkdir()
    (origination_store / "origination.json").write_text("{not valid json", encoding="utf-8")

    payload, exit_code = run_governor_loop_tick(
        root=repo, trust_store=trust_store, origination_store=origination_store
    )
    assert exit_code == 0
    assert payload["stop_reason"] != "FAILED_CLOSED"


def test_completed_dependency_stays_visible_for_dependent_on_next_tick(
    tmp_path: Path,
) -> None:
    """A RELEASED lease must keep the completed dependency in the DAG as a
    CERTIFIED witness. Excluding it entirely made ``select_next()`` /
    ``lease()`` treat the missing id as unsatisfied, so a later
    materialized dependent could never be leased.
    """
    repo = _make_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    trust_store = _make_trust_store(tmp_path, main, tree)
    trusted = load_runtime_anchor(store=trust_store)

    from project_atlas.orchestration.autonomy.discovery import collect_live_inventory

    inventory = collect_live_inventory(repo)
    governor = AutonomousGovernor(
        current_main=inventory.current_main,
        current_tree=inventory.current_tree,
        trusted_anchor=trusted,
    )

    origination_store = tmp_path / "origination-store"
    origination_store.mkdir()
    dependency = _minimal_work_node(
        "ORIG-dep", base_pin=main, surface_id="dep-surface", paths=("src/dep/",)
    )
    dependent = _minimal_work_node(
        "ORIG-next",
        base_pin=main,
        surface_id="next-surface",
        paths=("src/next/",),
        dependencies=("ORIG-dep",),
    )
    (origination_store / "origination.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "package": "AS-ORCH-ORIGINATION-PROJECTION-001",
                "records": [
                    OriginationRecord(
                        origination_identity="a" * 64,
                        project_id="demo",
                        proposal={},
                        policy_result={},
                        work_node=dependency.model_dump(mode="json"),
                        state="MATERIALIZED",
                    ).model_dump(mode="json"),
                    OriginationRecord(
                        origination_identity="b" * 64,
                        project_id="demo",
                        proposal={},
                        policy_result={},
                        work_node=dependent.model_dump(mode="json"),
                        state="MATERIALIZED",
                    ).model_dump(mode="json"),
                ],
            }
        ),
        encoding="utf-8",
    )

    lease_store = tmp_path / "lease-projection"
    lease_store.mkdir()
    (lease_store / LEASE_PROJECTION_NAME).write_text(
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
                        "lease_id": "LEASE-1",
                        "agent_id": "governor-pilot-local",
                        "package_id": "ORIG-dep",
                        "branch": "feat/completed-dep",
                        "worktree": "wt",
                        "base_pin": main,
                        "authorized_paths": ["src/dep/"],
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

    rehydrate_governor(
        governor,
        inventory=inventory,
        trusted=trusted,
        loop_store=tmp_path / "loop-state",
        lease_projection_store=lease_store,
        origination_projection_store=origination_store,
    )

    by_id = {node.package_id: node for node in governor.snapshot().nodes}
    assert by_id["ORIG-dep"].state == NodeState.CERTIFIED
    assert by_id["ORIG-next"].state == NodeState.READY
    decision = select_next(governor.snapshot().nodes)
    assert decision.next_package_id == "ORIG-next"
    assert decision.stop_reason is None


def test_reaped_dependency_release_exposes_dependent_in_the_same_tick(
    tmp_path: Path,
) -> None:
    """AUTONOMY_PROJECTION_ERROR_RECOVERY_BOUNDARY, independent-
    verification finding: the test above proves a RELEASED row is a
    valid CERTIFIED witness, but writes the row as RELEASED from the
    start with no prior loop state -- ``rehydrate_governor()`` takes its
    early ``STATE_MISSING`` return and never reaches
    ``reap_orphaned_lease_releases()`` at all, so it cannot prove
    anything about the reaper's OWN interaction with dependency
    exposure. This test drives the actual reaper path: ORIG-dep's lease
    row is still ACTUALLY ACTIVE, but the durable loop state already
    proves its work complete (``completed_lease_ids``). A single
    ``rehydrate_governor()`` call must both heal the release AND expose
    ORIG-dep as a CERTIFIED witness so ORIG-next is READY-and-selectable
    in this SAME call -- not one rehydration later -- which is only true
    because the reaper runs before ``_originate()`` takes its
    dependency-exposure snapshot (the ordering this finding corrected).
    """
    from project_atlas.orchestration.autonomy.loop import (
        LoopPhase,
        initial_loop_state,
        persist_loop_state,
        seal_loop_state,
    )

    repo = _make_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    trust_store = _make_trust_store(tmp_path, main, tree)
    trusted = load_runtime_anchor(store=trust_store)

    from project_atlas.orchestration.autonomy.discovery import collect_live_inventory

    inventory = collect_live_inventory(repo)
    governor = AutonomousGovernor(
        current_main=inventory.current_main,
        current_tree=inventory.current_tree,
        trusted_anchor=trusted,
    )

    origination_store = tmp_path / "origination-store"
    origination_store.mkdir()
    dependency = _minimal_work_node(
        "ORIG-dep", base_pin=main, surface_id="dep-surface-2", paths=("src/dep2/",)
    )
    dependent = _minimal_work_node(
        "ORIG-next",
        base_pin=main,
        surface_id="next-surface-2",
        paths=("src/next2/",),
        dependencies=("ORIG-dep",),
    )
    (origination_store / "origination.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "package": "AS-ORCH-ORIGINATION-PROJECTION-001",
                "records": [
                    OriginationRecord(
                        origination_identity="c" * 64,
                        project_id="demo",
                        proposal={},
                        policy_result={},
                        work_node=dependency.model_dump(mode="json"),
                        state="MATERIALIZED",
                    ).model_dump(mode="json"),
                    OriginationRecord(
                        origination_identity="d" * 64,
                        project_id="demo",
                        proposal={},
                        policy_result={},
                        work_node=dependent.model_dump(mode="json"),
                        state="MATERIALIZED",
                    ).model_dump(mode="json"),
                ],
            }
        ),
        encoding="utf-8",
    )

    lease_store = tmp_path / "lease-projection"
    lease_store.mkdir()
    (lease_store / LEASE_PROJECTION_NAME).write_text(
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
                        "lease_id": "LEASE-1",
                        "agent_id": "governor-pilot-local",
                        "package_id": "ORIG-dep",
                        "branch": "feat/completed-dep",
                        "worktree": "wt",
                        "base_pin": main,
                        "authorized_paths": ["src/dep2/"],
                        "forbidden_paths": ["main", "projects"],
                        "capabilities": ["IMPLEMENT"],
                        "start_state": "READY",
                        # Still ACTIVE: the release write is the one
                        # that was lost. Only completed_lease_ids below
                        # proves the work itself actually finished.
                        "status": "ACTIVE",
                        "created_sequence": 1,
                        "released_sequence": None,
                        "projection_is_authority": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    loop_store = tmp_path / "loop-state"
    state = initial_loop_state(trusted).model_copy(
        update={
            "phase": LoopPhase.IDLE,
            "completed_lease_ids": ("LEASE-1",),
        }
    )
    persist_loop_state(loop_store, seal_loop_state(state))

    rehydrate_governor(
        governor,
        inventory=inventory,
        trusted=trusted,
        loop_store=loop_store,
        lease_projection_store=lease_store,
        origination_projection_store=origination_store,
    )

    # The release was actually healed...
    healed_row = load_lease_projection(lease_store).leases[0]
    assert healed_row.status == "RELEASED"
    # ...AND the dependent is already selectable THIS SAME call -- not
    # one extra rehydration later.
    by_id = {node.package_id: node for node in governor.snapshot().nodes}
    assert by_id["ORIG-dep"].state == NodeState.CERTIFIED
    assert by_id["ORIG-next"].state == NodeState.READY
    decision = select_next(governor.snapshot().nodes)
    assert decision.next_package_id == "ORIG-next"
    assert decision.stop_reason is None


def test_corrupt_lease_store_fails_closed_instead_of_crashing(tmp_path: Path) -> None:
    """A corrupt lease projection must not be treated as empty history.
    Swallowing ``ProjectionError`` used to re-add already-leased nodes and
    then crash inside ``lease()`` -> ``project_grant()``; the tick must
    return a structured fail-closed payload instead.
    """
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    origination_store = tmp_path / "origination-store"
    trust_store = _make_trust_store(tmp_path, main, tree)

    run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=origination_store,
        trust_store=trust_store,
    )

    lease_dir = repo / LEASE_PROJECTION_RELATIVE_DEFAULT
    lease_dir.mkdir(parents=True, exist_ok=True)
    (lease_dir / LEASE_PROJECTION_NAME).write_text("{not valid json", encoding="utf-8")

    payload, exit_code = run_governor_loop_tick(
        root=repo, trust_store=trust_store, origination_store=origination_store
    )
    assert exit_code == 1
    assert payload["blocker"] == "STATE_CORRUPT"
    assert payload["merge_authorized"] is False
    assert payload["execution_authorized"] is False


def test_revised_work_reaches_governor_after_prior_revision_lease_released(
    tmp_path: Path,
) -> None:
    """A content revision that materializes under the same package_id
    once the prior record is TERMINAL must still be add_node'd /
    mark_ready'd. A RELEASED lease row for that package_id is history of
    the earlier revision, not a reason to skip the new WorkNode --
    project_grant() already accepts a new lease_id after release;
    discovery was the only blocker.
    """
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    origination_store = tmp_path / "origination-store"

    first, _ = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=origination_store,
        explicit_trusted=_anchor(main, tree),
    )
    assert first["materialized_count"] == 1
    work_id = first["materialized"][0]["work_id"]  # type: ignore[index]
    first_record = load_projection(origination_store).records[0]
    first_node = next(
        node
        for node in list_materialized_work_nodes(origination_store)
        if node.package_id == work_id
    )

    lease_store = tmp_path / "lease-projection"
    lease_store.mkdir()
    released = ProjectedLease(
        lease_id="lease-prior-revision",
        agent_id="governor-pilot-local",
        package_id=str(work_id),
        branch="feat/prior-revision",
        worktree="wt-prior-revision",
        base_pin=first_node.base_pin,
        authorized_paths=first_node.mutation_surface.paths,
        forbidden_paths=("main", "projects"),
        capabilities=tuple(cap.value for cap in first_node.agent_capabilities_required),
        start_state=NodeState.READY,
        status="RELEASED",
        created_sequence=1,
        released_sequence=2,
    )
    (lease_store / LEASE_PROJECTION_NAME).write_text(
        json.dumps(LeaseProjection(leases=(released,)).model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    mark_terminal(origination_store, first_record.origination_identity, node_state="CLOSED")

    _write_roadmap(
        repo,
        [
            {
                "id": "feature-x",
                "title": "Feature X (revised)",
                "status": "NOT_STARTED",
                "lifecycle": "READY",
                "evidence": ["docs/REQUIREMENTS.md", "tests/test_feature_x.py"],
            }
        ],
    )
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "revise feature-x")
    new_sha = _run_git(repo, "rev-parse", "HEAD")
    _run_git(repo, "update-ref", "refs/remotes/origin/main", new_sha)
    new_main = _run_git(repo, "rev-parse", "origin/main")
    new_tree = _run_git(repo, "rev-parse", "origin/main^{tree}")

    third, exit_code = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=origination_store,
        explicit_trusted=_anchor(new_main, new_tree),
    )
    assert exit_code == 0
    assert third["materialized_count"] == 1
    assert third["materialized"][0]["work_id"] == work_id  # type: ignore[index]

    from project_atlas.orchestration.autonomy.discovery import collect_live_inventory

    trusted = _anchor(new_main, new_tree)
    inventory = collect_live_inventory(repo)
    governor = AutonomousGovernor(
        current_main=inventory.current_main,
        current_tree=inventory.current_tree,
        trusted_anchor=trusted,
    )
    rehydrate_governor(
        governor,
        inventory=inventory,
        trusted=trusted,
        loop_store=tmp_path / "loop-state",
        lease_projection_store=lease_store,
        origination_projection_store=origination_store,
    )

    discovered = [node for node in governor.snapshot().nodes if node.package_id == work_id]
    assert len(discovered) == 1
    assert discovered[0].base_pin == new_main
    assert discovered[0].base_pin != main
    assert discovered[0].state == NodeState.READY


def test_stopped_no_eligible_work_resumes_when_origination_adds_ready_node(
    tmp_path: Path,
) -> None:
    """Codex P1: STOPPED/NO_ELIGIBLE_WORK must not permanently ignore a
    later materialized origination node. OWNER/SAFETY/RESOURCE stops stay
    terminal.
    """
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    origination_store = tmp_path / "origination-store"
    trust_store = _make_trust_store(tmp_path, main, tree)

    first, first_exit = run_governor_loop_tick(
        root=repo, trust_store=trust_store, origination_store=origination_store
    )
    assert first_exit == 0
    assert first["phase"] == "STOPPED"
    assert first["stop_reason"] == "NO_ELIGIBLE_WORK"

    scan, scan_exit = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=origination_store,
        trust_store=trust_store,
    )
    assert scan_exit == 0
    assert scan["materialized_count"] == 1
    work_id = scan["materialized"][0]["work_id"]  # type: ignore[index]

    second, second_exit = run_governor_loop_tick(
        root=repo, trust_store=trust_store, origination_store=origination_store
    )
    assert second_exit == 0
    assert second["stop_reason"] != "FAILED_CLOSED"
    lease_projection = load_lease_projection(repo / LEASE_PROJECTION_RELATIVE_DEFAULT)
    assert any(row.package_id == work_id for row in lease_projection.leases)


def test_stale_materialized_base_pin_is_not_marked_ready(tmp_path: Path) -> None:
    """A persisted node pinned to a previous main must not be marked READY
    after live main advances -- leasing it would raise uncaught STALE_LEASE.
    """
    repo = _make_repo(tmp_path)
    old_main = _run_git(repo, "rev-parse", "origin/main")
    (repo / "README.md").write_text("moved\n", encoding="utf-8")
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "advance main")
    new_sha = _run_git(repo, "rev-parse", "HEAD")
    _run_git(repo, "update-ref", "refs/remotes/origin/main", new_sha)
    new_main = _run_git(repo, "rev-parse", "origin/main")
    new_tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    assert new_main != old_main

    trust_store = _make_trust_store(tmp_path, new_main, new_tree)
    trusted = load_runtime_anchor(store=trust_store)
    from project_atlas.orchestration.autonomy.discovery import collect_live_inventory

    inventory = collect_live_inventory(repo)
    governor = AutonomousGovernor(
        current_main=inventory.current_main,
        current_tree=inventory.current_tree,
        trusted_anchor=trusted,
    )
    origination_store = tmp_path / "origination-store"
    origination_store.mkdir()
    stale = _minimal_work_node(
        "ORIG-stale", base_pin=old_main, surface_id="stale-surface", paths=("src/stale/",)
    )
    (origination_store / "origination.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "package": "AS-ORCH-ORIGINATION-PROJECTION-001",
                "records": [
                    OriginationRecord(
                        origination_identity="a" * 64,
                        project_id="demo",
                        proposal={},
                        policy_result={},
                        work_node=stale.model_dump(mode="json"),
                        state="MATERIALIZED",
                    ).model_dump(mode="json"),
                ],
            }
        ),
        encoding="utf-8",
    )
    rehydrate_governor(
        governor,
        inventory=inventory,
        trusted=trusted,
        loop_store=tmp_path / "loop-state",
        lease_projection_store=tmp_path / "lease-projection",
        origination_projection_store=origination_store,
    )
    assert all(node.package_id != "ORIG-stale" for node in governor.snapshot().nodes)


# --------------------------------------------------------------------------- #
# Real cross-process proof: two genuinely separate OS processes, sharing only
# filesystem state -- not two objects in one interpreter. §7's own standard.
# --------------------------------------------------------------------------- #
_PROCESS_ONE_SCRIPT = """
import json
import sys
from pathlib import Path

sys.path.insert(0, {src_path!r})
from project_atlas.orchestration.autonomy.cli import run_governor_loop_tick
from project_atlas.orchestration.autonomy.lease_projection import (
    RELATIVE_DEFAULT as LEASE_PROJECTION_RELATIVE_DEFAULT,
)
from project_atlas.orchestration.autonomy.lease_projection import load_projection
from project_atlas.orchestration.origination.cli import run_origination_scan

root = Path({root!r})
trust_store = Path({trust_store!r})
origination_store = Path({origination_store!r})

scan_payload, scan_exit = run_origination_scan(
    root=root,
    project_id="demo-project",
    origination_store=origination_store,
    trust_store=trust_store,
)
assert scan_exit == 0, scan_payload
assert scan_payload["materialized_count"] == 1, scan_payload

tick_payload, tick_exit = run_governor_loop_tick(
    root=root, trust_store=trust_store, origination_store=origination_store
)
lease_projection = load_projection(root / LEASE_PROJECTION_RELATIVE_DEFAULT)
print(json.dumps({{
    "scan_payload": scan_payload,
    "tick_exit": tick_exit,
    "tick_payload": tick_payload,
    "lease_projection_package_ids": [row.package_id for row in lease_projection.leases],
}}))
"""

_PROCESS_TWO_SCRIPT = """
import json
import sys
from pathlib import Path

sys.path.insert(0, {src_path!r})
from project_atlas.orchestration.autonomy.cli import run_governor_loop_tick

root = Path({root!r})
trust_store = Path({trust_store!r})
origination_store = Path({origination_store!r})

tick_payload, tick_exit = run_governor_loop_tick(
    root=root, trust_store=trust_store, origination_store=origination_store
)
print(json.dumps({{"tick_exit": tick_exit, "tick_payload": tick_payload}}))
"""


def test_real_two_process_continuation_does_not_replay_or_crash(tmp_path: Path) -> None:
    """Process N originates + materializes + ticks (discovers, leases,
    and -- since execution_host_class is always IN_PROCESS for
    origination-materialized nodes -- dispatches/executes) against a
    real evidence-backed repo, using ONLY the consolidated CLI entry
    points (never manually calling governor.add_node()/lease() by
    hand). It then exits -- a genuinely separate OS process, not an
    object this test keeps alive.

    Process N+1 -- a SEPARATE `python` subprocess, sharing only the
    filesystem (the loop store, lease projection, and origination
    projection this repo's own `root` durably persisted) -- reconstructs
    from that same disk state and ticks again.

    This is the exact scenario `_originate()`'s `lease_projection_store`
    fix (found by this file's own earlier in-process adversarial test)
    exists for: a fresh, empty-node governor in the second process must
    not re-discover and re-attempt to lease the same already-leased
    package_id. Proven here across a REAL process boundary, not two
    objects in one interpreter.
    """
    import sys

    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    trust_store = _make_trust_store(tmp_path, main, tree)
    origination_store = tmp_path / "origination-store"

    src_path = str(Path(__file__).resolve().parents[2] / "src")

    script_one = tmp_path / "process_one.py"
    script_one.write_text(
        _PROCESS_ONE_SCRIPT.format(
            src_path=src_path,
            root=str(repo),
            trust_store=str(trust_store),
            origination_store=str(origination_store),
        ),
        encoding="utf-8",
    )
    result_one = subprocess.run(
        [sys.executable, str(script_one)], capture_output=True, text=True, timeout=60
    )
    assert result_one.returncode == 0, result_one.stderr
    output_one = json.loads(result_one.stdout.strip().splitlines()[-1])
    assert output_one["tick_exit"] == 0, output_one
    tick_one = output_one["tick_payload"]
    assert tick_one["stop_reason"] != "FAILED_CLOSED", tick_one
    # Process N genuinely leased something this run -- not a vacuous
    # NO_ELIGIBLE_WORK stop that would make process N+1's check
    # meaningless. Checked against the DURABLE lease projection (not the
    # tick's own transient `dispatched` flag, which reflects the LAST
    # internal step's own result object, not necessarily true for the
    # overall tick when IN_PROCESS execution completes the full lease ->
    # dispatch -> validate -> complete cycle synchronously within one
    # call and the loop lands back at IDLE).
    work_id = output_one["scan_payload"]["materialized"][0]["work_id"]
    assert work_id in output_one["lease_projection_package_ids"], output_one

    script_two = tmp_path / "process_two.py"
    script_two.write_text(
        _PROCESS_TWO_SCRIPT.format(
            src_path=src_path,
            root=str(repo),
            trust_store=str(trust_store),
            origination_store=str(origination_store),
        ),
        encoding="utf-8",
    )
    result_two = subprocess.run(
        [sys.executable, str(script_two)], capture_output=True, text=True, timeout=60
    )
    assert result_two.returncode == 0, result_two.stderr
    output_two = json.loads(result_two.stdout.strip().splitlines()[-1])
    assert output_two["tick_exit"] == 0, output_two
    tick_two = output_two["tick_payload"]
    # The real proof: process N+1, from a completely fresh governor
    # (zero in-memory nodes), does NOT re-discover and re-lease the same
    # package -- no crash (LEASE_REPLAY used to propagate uncaught here),
    # and no second dispatch of already-completed work.
    assert tick_two["stop_reason"] != "FAILED_CLOSED", tick_two
    assert tick_two["dispatched"] is False, tick_two


# --------------------------------------------------------------------------- #
# AS-ORIGIN-MATERIALIZED-SUPERSESSION-001 (owner directive
# D-ATLAS-ORIGINATION-MATERIALIZED-REVISION-SUPERSESSION): the real
# INT-013-shaped incident, reproduced end to end through the REAL,
# supported scanner (run_origination_scan()) and the REAL production
# tick entry point (run_governor_loop_tick()) -- never hand-edited state.
# --------------------------------------------------------------------------- #
def test_a_revision_that_becomes_blocked_is_never_leased_by_a_later_real_tick(
    tmp_path: Path,
) -> None:
    """The real incident, reproduced with the real scanner and the real
    production tick entrypoint (owner directive §9/§14): an item is
    originated and materialized while fully eligible; source truth then
    changes so the SAME logical item is EXTERNAL_BLOCKED; a fresh scan
    (the supported reconciler) supersedes the stale revision; a
    subsequent REAL `run_governor_loop_tick()` -- the actual production
    entrypoint, never a hand-rolled governor -- must not discover, mark
    READY, lease, or dispatch the superseded work_id. §17: the item's own
    ``base_pin`` is left EXACTLY equal to the OLD live main throughout
    (never advanced, never touched) -- the tick still correctly refuses
    it, proving this is not merely base_pin going stale.
    """
    repo = _eligible_repo(tmp_path, item_id="int-013-like")
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    origination_store = tmp_path / "origination-store"
    trust_store = _make_trust_store(tmp_path, main, tree)

    first_scan, first_exit = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=origination_store,
        trust_store=trust_store,
    )
    assert first_exit == 0
    assert first_scan["materialized_count"] == 1
    work_id = first_scan["materialized"][0]["work_id"]  # type: ignore[index]
    old_identity = load_projection(origination_store).records[0].origination_identity

    # Authoritative source truth changes: the SAME logical item (id
    # unchanged -> same package_id) now declares an explicit blocker --
    # the real INT-013 shape (EXTERNAL_BLOCKED, needs owner-provided
    # authentic project roots). Deliberately does NOT touch git so that
    # `origin/main` (and therefore the stale revision's own frozen
    # `base_pin`) never moves -- the §17 requirement.
    _write_roadmap(
        repo,
        [
            {
                "id": "int-013-like",
                "title": "int-013-like",
                "status": "NOT_STARTED",
                "lifecycle": "READY",
                "evidence": [
                    "docs/REQUIREMENTS.md",
                    "tests/test_int_013_like.py",
                ],
                "blockers": ["EXTERNAL_BLOCKED: needs owner-provided authentic project roots"],
            }
        ],
    )
    # No git commit, no `origin/main` update -- base_pin stays identical.

    second_scan, second_exit = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=origination_store,
        trust_store=trust_store,
    )
    assert second_exit == 0
    assert second_scan["materialized_count"] == 0
    assert second_scan["not_materialized_count"] == 1
    second_entry = second_scan["not_materialized"][0]  # type: ignore[index]
    assert second_entry["materialization_error_code"] == "PROPOSAL_BLOCKED"
    assert second_entry["execution_ready"] is False
    assert second_entry["superseded_prior_revisions"] == [old_identity]

    old_record = next(
        r
        for r in load_projection(origination_store).records
        if r.origination_identity == old_identity
    )
    assert old_record.state == "SUPERSEDED"
    assert old_record.work_node is not None
    # §17: base_pin genuinely unchanged -- this is not a "went stale"
    # coincidence, and the tick below still refuses it.
    assert old_record.work_node["base_pin"] == main

    # The real production entrypoint: a fresh, empty governor, exactly as
    # every real invocation constructs it.
    payload, exit_code = run_governor_loop_tick(
        root=repo,
        trust_store=trust_store,
        origination_store=origination_store,
    )
    assert exit_code == 0
    assert payload["stop_reason"] != "FAILED_CLOSED"

    lease_projection = load_lease_projection(repo / LEASE_PROJECTION_RELATIVE_DEFAULT)
    leased_package_ids = {row.package_id for row in lease_projection.leases}
    assert work_id not in leased_package_ids
    assert payload.get("package_id") != work_id
    assert payload["dispatched"] is False

    # Defense-in-depth confirmation at the read side too: the superseded
    # revision is genuinely excluded from what a governor could ever
    # discover, independent of the tick's own outcome above.
    active_nodes = list_materialized_work_nodes(origination_store)
    assert not any(node.package_id == work_id for node in active_nodes)


# --------------------------------------------------------------------------- #
# Owner directive D-ATLAS-PR678-CASE-A-LEASE-AUTHORITY-CLOSURE:
# READY != LEASE, extended -- a node whose ORIGINATION REVISION went stale
# must not receive NEW execution authority, even inside one long-lived
# governor process (no restart, no base_pin movement).
# --------------------------------------------------------------------------- #
def _write_contract(repo: Path, *, proposed_scope: str, success_criteria: str) -> None:
    """Declare one acceptance contract for ``_eligible_repo``'s feature-x item."""
    _write_plain_file(
        repo,
        "docs/acceptance-contracts.yaml",
        "contracts:\n"
        "  - item_id: feature-x\n"
        "    source_path: docs/ROADMAP.md\n"
        "    evidence: [docs/REQUIREMENTS.md]\n"
        f"    proposed_scope: [{proposed_scope}]\n"
        f"    success_criteria: [{success_criteria!r}]\n",
    )
    _write_plain_file(
        repo,
        ".atlas-project.yaml",
        "schema_version: 1\n"
        "project:\n"
        "  id: demo-project\n"
        "origination_acceptance_contracts: docs/acceptance-contracts.yaml\n",
    )


def _scan_and_load_node(repo: Path, trust_store: Path, origination_store: Path) -> WorkNode:
    payload, exit_code = run_origination_scan(
        root=repo, project_id="demo-project", trust_store=trust_store
    )
    assert exit_code == 0, payload
    assert payload["materialized_count"] == 1, payload
    nodes = list_materialized_work_nodes(origination_store)
    assert len(nodes) == 1
    return nodes[0]


def test_origination_node_leases_normally_while_its_revision_is_current(
    tmp_path: Path,
) -> None:
    """Control for the stale-revision test below (regression matrix A/H):
    the new lease-time currentness check must not break the NORMAL path.
    A node whose origination revision IS still current leases exactly as
    it always did -- proving a denial in the sibling test below is caused
    by staleness specifically, not by the check rejecting everything."""
    repo = _eligible_repo(tmp_path)
    _write_contract(repo, proposed_scope="src/thing.py", success_criteria="K1: thing works")
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    trust_store = _make_trust_store(tmp_path, main, tree)
    origination_store = repo / ORIGINATION_PROJECTION_RELATIVE_DEFAULT

    node = _scan_and_load_node(repo, trust_store, origination_store)
    # D-ATLAS-PR678 §5: the materialized node carries the exact identity
    # its own durable origination record was written under.
    record = next(
        row
        for row in load_projection(origination_store).records
        if row.work_node is not None and row.work_node.get("package_id") == node.package_id
    )
    assert node.origination_identity is not None
    assert node.origination_identity == record.origination_identity

    governor = AutonomousGovernor(
        current_main=main,
        current_tree=tree,
        trusted_anchor=load_runtime_anchor(store=trust_store),
        origination_projection_store=origination_store,
    )
    governor.add_node(node)
    governor.mark_ready(node.package_id)

    lease = governor.lease(node.package_id, "governor-pilot-local", branch="feat/x", worktree="wt")
    assert lease.package_id == node.package_id


def test_long_lived_governor_refuses_lease_for_stale_origination_revision(
    tmp_path: Path,
) -> None:
    """THE load-bearing regression for owner directive
    D-ATLAS-PR678-CASE-A-LEASE-AUTHORITY-CLOSURE §10.

    ``rehydration.py::has_ever_had_multiple_revisions()`` already refuses
    to RESUME a durably-leased node across a process RESTART once its
    package_id has been revised. Nothing protected the same node inside a
    single LONG-LIVED governor: ``_originate()``'s discovery pass skips
    any package_id the governor already knows, so a node that reached
    READY under revision A and then sat unleased while a scan superseded
    it with revision B was never refreshed or evicted -- and ``lease()``
    had no field to notice, because a ``WorkNode`` carried no origination
    provenance at all.

    Deliberately adversarial about what it does NOT rely on:

    - the governor object is never rebuilt, and never rehydrated;
    - ``docs/ROADMAP.md`` is never touched (the task text is
      byte-identical across both scans) -- ONLY the acceptance contract's
      mutation scope changes, which is exactly the case that used to
      leave ``origination_identity`` unchanged and therefore invisible;
    - ``base_pin`` never moves (no commit between the scans), so this
      cannot pass by accident through the pre-existing stale-base_pin
      path.
    """
    repo = _eligible_repo(tmp_path)
    _write_contract(repo, proposed_scope="src/thing.py", success_criteria="K1: thing works")
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    trust_store = _make_trust_store(tmp_path, main, tree)
    origination_store = repo / ORIGINATION_PROJECTION_RELATIVE_DEFAULT

    node_a = _scan_and_load_node(repo, trust_store, origination_store)
    identity_a = node_a.origination_identity
    assert identity_a is not None

    governor = AutonomousGovernor(
        current_main=main,
        current_tree=tree,
        trusted_anchor=load_runtime_anchor(store=trust_store),
        origination_projection_store=origination_store,
    )
    governor.add_node(node_a)
    governor.mark_ready(node_a.package_id)

    # Authority-bearing input changes: roadmap/task text untouched, only
    # the acceptance contract's mutation scope widens. Then reconcile via
    # the supported production scanner -- no manual .atlas state edits.
    _write_contract(
        repo,
        proposed_scope="src/thing.py, src/extra.py",
        success_criteria="K1: thing works",
    )
    second_payload, second_exit = run_origination_scan(
        root=repo, project_id="demo-project", trust_store=trust_store
    )
    assert second_exit == 0
    assert second_payload["materialized_count"] == 1
    second_entry = cast(list[dict[str, object]], second_payload["materialized"])[0]
    assert second_entry["superseded_prior_revisions"] == [identity_a]

    # SAME governor object, never restarted, still holding node A at READY.
    stale = next(item for item in governor.snapshot().nodes if item.package_id == node_a.package_id)
    assert stale.state == NodeState.READY
    assert stale.origination_identity == identity_a
    assert stale.base_pin == main  # base_pin did NOT move

    with pytest.raises(GovernorError) as excinfo:
        governor.lease(node_a.package_id, "governor-pilot-local", branch="feat/x", worktree="wt")
    assert excinfo.value.code == "STALE_ORIGINATION_IDENTITY"

    # The refusal is a refusal, not a mutation: no lease was granted, and
    # the node is left exactly as it was for a deliberate later refresh.
    assert governor.snapshot().leases == ()


def test_success_criteria_only_change_also_denies_a_stale_lease(tmp_path: Path) -> None:
    """Regression matrix D: the sibling test above changes the contract's
    mutation SCOPE; this one changes only its SUCCESS CRITERIA. Both are
    authority-bearing acceptance-contract outputs bound into
    ``origination_identity`` (``identity.py``), so both must produce a new
    revision and deny the old node a new lease -- criteria are what a
    result is judged against, so a node carrying obsolete criteria is
    exactly as stale as one carrying an obsolete scope."""
    repo = _eligible_repo(tmp_path)
    _write_contract(repo, proposed_scope="src/thing.py", success_criteria="K1: thing works")
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    trust_store = _make_trust_store(tmp_path, main, tree)
    origination_store = repo / ORIGINATION_PROJECTION_RELATIVE_DEFAULT

    node_a = _scan_and_load_node(repo, trust_store, origination_store)
    governor = AutonomousGovernor(
        current_main=main,
        current_tree=tree,
        trusted_anchor=load_runtime_anchor(store=trust_store),
        origination_projection_store=origination_store,
    )
    governor.add_node(node_a)
    governor.mark_ready(node_a.package_id)

    _write_contract(
        repo, proposed_scope="src/thing.py", success_criteria="K2: a materially different bar"
    )
    _, second_exit = run_origination_scan(
        root=repo, project_id="demo-project", trust_store=trust_store
    )
    assert second_exit == 0

    with pytest.raises(GovernorError) as excinfo:
        governor.lease(node_a.package_id, "governor-pilot-local", branch="feat/x", worktree="wt")
    assert excinfo.value.code == "STALE_ORIGINATION_IDENTITY"


def test_non_origination_node_leases_unaffected_by_the_currentness_check(
    tmp_path: Path,
) -> None:
    """Regression matrix H / directive §9: a node that is not
    origination-derived (``origination_identity is None`` -- the pilot
    factory, and nodes built directly like this one) is not governed by
    origination freshness at all. It must lease exactly as it did before
    the check existed: no sentinel identity is fabricated for it, and no
    projection record has to exist on its behalf -- note the governor
    below IS wired to an origination store that contains nothing about
    this package_id whatsoever."""
    repo = _make_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    trust_store = _make_trust_store(tmp_path, main, tree)

    node = _minimal_work_node("MANUAL-NODE-001", base_pin=main, surface_id="manual-surface")
    assert node.origination_identity is None

    governor = AutonomousGovernor(
        current_main=main,
        current_tree=tree,
        trusted_anchor=load_runtime_anchor(store=trust_store),
        origination_projection_store=repo / ORIGINATION_PROJECTION_RELATIVE_DEFAULT,
    )
    governor.add_node(node)
    governor.mark_ready(node.package_id)

    lease = governor.lease(node.package_id, "governor-pilot-local", branch="feat/x", worktree="wt")
    assert lease.package_id == "MANUAL-NODE-001"


def test_work_node_persisted_before_provenance_existed_still_deserializes() -> None:
    """Directive §4 backwards-compatibility, proven against the real
    persisted shape rather than asserted: a ``work_node`` dict written
    before ``origination_identity`` existed simply lacks the key. It must
    still validate, defaulting to ``None``, so no store migration is
    required -- which also means such a legacy node is treated as
    non-origination (legacy lease behavior), never handed a fabricated
    identity."""
    legacy = _minimal_work_node("LEGACY-001", base_pin="a" * 40, surface_id="legacy").model_dump(
        mode="json"
    )
    del legacy["origination_identity"]
    assert "origination_identity" not in legacy

    restored = WorkNode.model_validate(legacy)
    assert restored.origination_identity is None
    assert restored.package_id == "LEGACY-001"


def test_result_from_stale_contract_revision_cannot_terminalize_the_current_one(
    tmp_path: Path,
) -> None:
    """Regression matrix K / directive §13: make the EXISTING result-replay
    guard load-bearing against the NEW acceptance-contract revision
    semantics, rather than duplicating it.

    ``sync_terminal_governed_states()`` already refuses to auto-sync any
    package_id that has ever had more than one revision (the sibling test
    above proves that for a roadmap-content revision). This proves the
    same protection now covers a revision produced by a CONTRACT-ONLY
    edit -- previously impossible to even reach, because a contract edit
    left ``origination_identity`` unchanged and therefore produced no
    second revision at all.

    Attack shape: revision A is governed and legitimately reaches CLOSED,
    but by then the current authority is revision B (a widened contract
    scope). A's completion must not be credited to B -- B was never
    executed, and silently marking it TERMINAL would permanently exclude
    genuinely-unexecuted work from every future scan.
    """
    repo = _eligible_repo(tmp_path)
    _write_contract(repo, proposed_scope="src/thing.py", success_criteria="K1: thing works")
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    trust_store = _make_trust_store(tmp_path, main, tree)
    origination_store = repo / ORIGINATION_PROJECTION_RELATIVE_DEFAULT

    node_a = _scan_and_load_node(repo, trust_store, origination_store)
    identity_a = node_a.origination_identity

    # Contract-only authority change: roadmap bytes untouched.
    _write_contract(
        repo, proposed_scope="src/thing.py, src/extra.py", success_criteria="K1: thing works"
    )
    _, second_exit = run_origination_scan(
        root=repo, project_id="demo-project", trust_store=trust_store
    )
    assert second_exit == 0

    records = load_projection(origination_store).records
    states = {row.origination_identity: row.state for row in records}
    assert len(states) == 2
    identity_b = next(key for key in states if key != identity_a)
    assert states[identity_a] == "SUPERSEDED"
    assert states[identity_b] == "MATERIALIZED"

    # A's own governed node now legitimately closes -- carrying A's
    # provenance, which is exactly what a real in-flight governor holds.
    closed_a = node_a.model_copy(update={"state": NodeState.CLOSED})
    synced = sync_terminal_governed_states(origination_store, [closed_a])
    assert synced == ()

    after = {
        row.origination_identity: row.state for row in load_projection(origination_store).records
    }
    assert after[identity_a] == "SUPERSEDED"
    # The critical assertion: B is NOT terminalized by A's result.
    assert after[identity_b] == "MATERIALIZED"


def test_origination_identity_is_deterministic_and_path_normalized(tmp_path: Path) -> None:
    """Directive §14: ``proposed_scope``/``success_criteria`` now
    participate in a durable primary identity, so identity determinism is
    load-bearing.

    Two properties proven here against the real scan path:

    1. Repeated derivation from byte-identical inputs yields the SAME
       identity -- no set-iteration or hash-seed leakage. (The derived
       default scope is built from a ``set`` in
       ``pipeline.py::_proposed_scope()`` but returned ``sorted()``;
       contract-supplied scope keeps its declared order, which is itself
       deterministic.)
    2. A contract declaring Windows-style ``\\`` separators normalizes to
       the SAME identity as one declaring ``/`` -- so a vault scanned on
       Windows and on Linux agrees, rather than silently forking every
       node's identity by platform.
    """
    repo = _eligible_repo(tmp_path)
    _write_contract(repo, proposed_scope="src/thing.py", success_criteria="K1: thing works")
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    trust_store = _make_trust_store(tmp_path, main, tree)
    origination_store = repo / ORIGINATION_PROJECTION_RELATIVE_DEFAULT

    first = _scan_and_load_node(repo, trust_store, origination_store)

    # Same inputs, a completely separate store: identity must be stable.
    repo_again = _eligible_repo(tmp_path, name="repo-again")
    _write_contract(repo_again, proposed_scope="src/thing.py", success_criteria="K1: thing works")
    again_store = repo_again / ORIGINATION_PROJECTION_RELATIVE_DEFAULT
    again_main = _run_git(repo_again, "rev-parse", "origin/main")
    again_tree = _run_git(repo_again, "rev-parse", "origin/main^{tree}")
    again_trust = _make_trust_store(tmp_path, again_main, again_tree, name="trust-again")
    second = _scan_and_load_node(repo_again, again_trust, again_store)
    assert second.origination_identity == first.origination_identity

    # Windows-style separators in the declared scope must normalize to the
    # same canonical path -- and therefore the same identity.
    repo_win = _eligible_repo(tmp_path, name="repo-win")
    _write_contract(repo_win, proposed_scope="src\\thing.py", success_criteria="K1: thing works")
    win_store = repo_win / ORIGINATION_PROJECTION_RELATIVE_DEFAULT
    win_main = _run_git(repo_win, "rev-parse", "origin/main")
    win_tree = _run_git(repo_win, "rev-parse", "origin/main^{tree}")
    win_trust = _make_trust_store(tmp_path, win_main, win_tree, name="trust-win")
    win = _scan_and_load_node(repo_win, win_trust, win_store)
    assert win.origination_identity == first.origination_identity


def test_legacy_origination_node_without_provenance_is_denied_a_lease(
    tmp_path: Path,
) -> None:
    """Owner directive D-ATLAS-PR678-LEGACY-ORIGINATION-NODE-FAIL-CLOSED-CHECK:

        OPTIONAL FIELD != FAIL-OPEN MIGRATION.

    ``WorkNode.origination_identity`` is additive and optional, so an
    origination-derived node persisted BEFORE it existed deserializes as
    ``None``. That absence must never be read as "this node needs no
    provenance" -- if it were, every legacy row would silently bypass the
    lease-time currentness check, which is exactly the authority the
    check exists to gate.

    The bypass is genuinely reachable, which is why this guard is not
    redundant: ``rehydration._originate()`` loads active rows straight
    out of the durable projection via
    ``list_materialized_work_nodes()`` and marks them READY, and the
    ``has_ever_had_multiple_revisions()`` guard only covers RESUMING an
    already-projected lease, never a fresh grant.

    The discriminator is the node's own historical provenance --
    ``mutation_surface.semantic``, set by ``materialize.py`` (origination's
    only WorkNode producer) and unchanged since that module was created,
    so legacy rows carry it too. No migration metadata is invented.

    This test builds the legacy shape the honest way: materialize a real
    node through the production path, then DELETE the key from its
    persisted dict, which is byte-for-byte what a pre-field store holds.
    """
    repo = _eligible_repo(tmp_path)
    _write_contract(repo, proposed_scope="src/thing.py", success_criteria="K1: thing works")
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    trust_store = _make_trust_store(tmp_path, main, tree)
    origination_store = repo / ORIGINATION_PROJECTION_RELATIVE_DEFAULT

    node = _scan_and_load_node(repo, trust_store, origination_store)
    assert node.origination_identity is not None
    # The historical marker legacy rows really carry.
    assert node.mutation_surface.semantic == ORIGINATION_SURFACE_SEMANTIC

    legacy_payload = node.model_dump(mode="json")
    del legacy_payload["origination_identity"]
    legacy_node = WorkNode.model_validate(legacy_payload)
    assert legacy_node.origination_identity is None
    assert legacy_node.mutation_surface.semantic == ORIGINATION_SURFACE_SEMANTIC

    governor = AutonomousGovernor(
        current_main=main,
        current_tree=tree,
        trusted_anchor=load_runtime_anchor(store=trust_store),
        origination_projection_store=origination_store,
    )
    governor.add_node(legacy_node)
    governor.mark_ready(legacy_node.package_id)

    with pytest.raises(GovernorError) as excinfo:
        governor.lease(
            legacy_node.package_id, "governor-pilot-local", branch="feat/x", worktree="wt"
        )
    assert excinfo.value.code == "MISSING_ORIGINATION_IDENTITY"
    assert governor.snapshot().leases == ()


def test_legacy_origination_node_is_denied_even_without_an_origination_store(
    tmp_path: Path,
) -> None:
    """The legacy refusal must not be conditioned on the governor having
    been wired to an origination store: an unprovable node is unprovable
    either way, and a caller that simply forgot to wire the store must
    not thereby WIDEN what can be leased. (Contrast the provenance-present
    path, which deliberately preserves byte-identical legacy behavior for
    an unwired governor -- proven by the sibling non-origination and
    restart tests.)"""
    repo = _eligible_repo(tmp_path)
    _write_contract(repo, proposed_scope="src/thing.py", success_criteria="K1: thing works")
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    trust_store = _make_trust_store(tmp_path, main, tree)
    origination_store = repo / ORIGINATION_PROJECTION_RELATIVE_DEFAULT

    node = _scan_and_load_node(repo, trust_store, origination_store)
    legacy_payload = node.model_dump(mode="json")
    del legacy_payload["origination_identity"]
    legacy_node = WorkNode.model_validate(legacy_payload)

    governor = AutonomousGovernor(
        current_main=main,
        current_tree=tree,
        trusted_anchor=load_runtime_anchor(store=trust_store),
        # deliberately NOT wired
    )
    governor.add_node(legacy_node)
    governor.mark_ready(legacy_node.package_id)

    with pytest.raises(GovernorError) as excinfo:
        governor.lease(
            legacy_node.package_id, "governor-pilot-local", branch="feat/x", worktree="wt"
        )
    assert excinfo.value.code == "MISSING_ORIGINATION_IDENTITY"


def test_legacy_origination_row_heals_through_a_real_second_scan(
    tmp_path: Path,
) -> None:
    """The legacy refusal is transient for a GENUINE UPGRADE -- proven by
    actually performing one, not by asserting it.

    Independent verification (PR #678) correctly rejected the earlier
    version of this test: it built an in-memory legacy node by stripping
    a copy, then "recovered" by reading back the SAME durable row, which
    had never lost its identity. It never mutated the store and never
    re-ran the scanner, so it proved nothing about healing.

    This version reproduces the real upgrade shape:

    1. materialize through the production scanner;
    2. rewrite the DURABLE row the way a pre-provenance Atlas would have
       left it -- ``work_node`` with no ``origination_identity`` key, and
       the row keyed under an OLD-FORMULA identity (the payload format
       changed, so a genuine legacy row's identity never equals what the
       current formula derives -- see ``identity.py``'s migration note);
    3. prove the rehydrated legacy node is refused;
    4. run a REAL second ``run_origination_scan()``;
    5. prove it supersedes the legacy row and materializes a fresh,
       provenance-carrying node that leases.
    """
    repo = _eligible_repo(tmp_path)
    _write_contract(repo, proposed_scope="src/thing.py", success_criteria="K1: thing works")
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    trust_store = _make_trust_store(tmp_path, main, tree)
    origination_store = repo / ORIGINATION_PROJECTION_RELATIVE_DEFAULT

    node = _scan_and_load_node(repo, trust_store, origination_store)
    package_id = node.package_id

    # Rewrite the DURABLE store into genuine pre-provenance shape: the row
    # keyed under an old-formula identity, and a work_node with no
    # origination_identity key at all.
    legacy_identity = "1" * 64
    store_file = origination_store / "origination.json"
    raw = json.loads(store_file.read_text(encoding="utf-8"))
    assert len(raw["records"]) == 1
    row = raw["records"][0]
    row["origination_identity"] = legacy_identity
    del row["work_node"]["origination_identity"]
    store_file.write_text(json.dumps(raw), encoding="utf-8")

    legacy_node = next(
        item
        for item in list_materialized_work_nodes(origination_store)
        if item.package_id == package_id
    )
    assert legacy_node.origination_identity is None  # genuinely stripped on disk
    assert legacy_node.mutation_surface.semantic == ORIGINATION_SURFACE_SEMANTIC

    trusted = load_runtime_anchor(store=trust_store)
    blocked = AutonomousGovernor(
        current_main=main,
        current_tree=tree,
        trusted_anchor=trusted,
        origination_projection_store=origination_store,
    )
    blocked.add_node(legacy_node)
    blocked.mark_ready(package_id)
    with pytest.raises(GovernorError) as excinfo:
        blocked.lease(package_id, "governor-pilot-local", branch="feat/x", worktree="wt")
    assert excinfo.value.code == "MISSING_ORIGINATION_IDENTITY"

    # THE ACTUAL RECOVERY STEP the previous version omitted.
    _, scan_exit = run_origination_scan(
        root=repo, project_id="demo-project", trust_store=trust_store
    )
    assert scan_exit == 0

    states = {
        row.origination_identity: row.state for row in load_projection(origination_store).records
    }
    assert states[legacy_identity] == "SUPERSEDED"  # legacy row retired, not deleted

    healed = next(
        item
        for item in list_materialized_work_nodes(origination_store)
        if item.package_id == package_id
    )
    assert healed.origination_identity is not None
    assert healed.origination_identity != legacy_identity

    recovered = AutonomousGovernor(
        current_main=main,
        current_tree=tree,
        trusted_anchor=trusted,
        origination_projection_store=origination_store,
    )
    recovered.add_node(healed)
    recovered.mark_ready(package_id)
    lease = recovered.lease(package_id, "governor-pilot-local", branch="feat/x", worktree="wt")
    assert lease.package_id == package_id


def test_identity_bearing_node_is_denied_when_no_currentness_store_is_available(
    tmp_path: Path,
) -> None:
    """Owner directive D-ATLAS-PR678-UNWIRED-GOVERNOR-FAIL-CLOSED-FINAL:

        FAILURE TO VERIFY NEVER BECOMES PERMISSION TO EXECUTE.

    A node carrying ``origination_identity`` makes a positive claim -- "I
    was created from origination revision X". Granting it a NEW lease
    requires PROVING X is still current. A governor with no origination
    projection cannot perform that proof, so it must deny rather than
    fall back to pre-provenance behavior.

    The production tick does wire the store, but that is evidence about
    today's callers, not an invariant: ``add_node()``/``lease()`` are
    public, and a future integration, harness, or refactor that forgets
    the dependency must not silently convert CURRENTNESS CHECK
    UNAVAILABLE into CURRENTNESS CHECK SKIPPED.

    Distinct from ``STALE_ORIGINATION_IDENTITY`` on purpose: that means
    current authority WAS established and differs; this means current
    authority could not be established at all.
    """
    repo = _eligible_repo(tmp_path)
    _write_contract(repo, proposed_scope="src/thing.py", success_criteria="K1: thing works")
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    trust_store = _make_trust_store(tmp_path, main, tree)
    origination_store = repo / ORIGINATION_PROJECTION_RELATIVE_DEFAULT

    # A genuinely CURRENT, identity-bearing node -- nothing stale about
    # it. The only thing missing is the governor's ability to check.
    node = _scan_and_load_node(repo, trust_store, origination_store)
    assert node.origination_identity is not None
    assert node.mutation_surface.semantic == ORIGINATION_SURFACE_SEMANTIC

    governor = AutonomousGovernor(
        current_main=main,
        current_tree=tree,
        trusted_anchor=load_runtime_anchor(store=trust_store),
        # deliberately NOT wired to an origination projection
    )
    governor.add_node(node)
    governor.mark_ready(node.package_id)

    with pytest.raises(GovernorError) as excinfo:
        governor.lease(node.package_id, "governor-pilot-local", branch="feat/x", worktree="wt")
    assert excinfo.value.code == "ORIGINATION_AUTHORITY_UNAVAILABLE"
    assert governor.snapshot().leases == ()

    # Control: the SAME node, same everything, leases fine once the
    # governor can actually verify it -- proving the denial is caused by
    # missing verification capability, not by the node being unleasable.
    wired = AutonomousGovernor(
        current_main=main,
        current_tree=tree,
        trusted_anchor=load_runtime_anchor(store=trust_store),
        origination_projection_store=origination_store,
    )
    wired.add_node(node)
    wired.mark_ready(node.package_id)
    lease = wired.lease(node.package_id, "governor-pilot-local", branch="feat/x", worktree="wt")
    assert lease.package_id == node.package_id


def test_non_origination_node_still_leases_without_any_origination_store(
    tmp_path: Path,
) -> None:
    """Directive §5 control: the fail-closed rules must not make
    origination infrastructure MANDATORY for legitimate non-origination
    work. A pilot-shaped node (no identity, non-origination semantic) on
    a governor with no origination store at all must lease exactly as it
    always did -- otherwise the autonomous pilot and every
    non-origination integration would have been broken by this PR."""
    repo = _make_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    trust_store = _make_trust_store(tmp_path, main, tree)

    node = _minimal_work_node("MANUAL-NODE-002", base_pin=main, surface_id="manual-surface-2")
    assert node.origination_identity is None
    assert node.mutation_surface.semantic != ORIGINATION_SURFACE_SEMANTIC

    governor = AutonomousGovernor(
        current_main=main,
        current_tree=tree,
        trusted_anchor=load_runtime_anchor(store=trust_store),
        # no origination store, and none needed
    )
    governor.add_node(node)
    governor.mark_ready(node.package_id)

    lease = governor.lease(node.package_id, "governor-pilot-local", branch="feat/x", worktree="wt")
    assert lease.package_id == "MANUAL-NODE-002"


def test_lease_denied_when_current_origination_record_is_ambiguous(tmp_path: Path) -> None:
    """§9 matrix: CURRENT IDENTITY AMBIGUOUS = DENIED.

    ``current_origination_identity()`` returns ``None`` when more than one
    active row claims a package_id -- ambiguity is not authority, mirroring
    ``find_materialized_work_node()``'s established contract. That collapses
    into the same single mismatch comparison, so a corrupt/ambiguous store
    can never be resolved by silently picking a winner.
    """
    origination_store = tmp_path / "origination-store"
    origination_store.mkdir()
    main = "a" * 40
    node = _minimal_work_node(
        "ORIG-ambiguous", base_pin=main, surface_id="ambiguous-surface"
    ).model_copy(update={"origination_identity": "a" * 64})

    other = node.model_copy(update={"origination_identity": "b" * 64})
    (origination_store / "origination.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "package": "AS-ORCH-ORIGINATION-PROJECTION-001",
                "records": [
                    OriginationRecord(
                        origination_identity="a" * 64,
                        project_id="demo",
                        proposal={"work_id": "ORIG-ambiguous"},
                        policy_result={},
                        work_node=node.model_dump(mode="json"),
                        state="MATERIALIZED",
                    ).model_dump(mode="json"),
                    OriginationRecord(
                        origination_identity="b" * 64,
                        project_id="demo",
                        proposal={"work_id": "ORIG-ambiguous"},
                        policy_result={},
                        work_node=other.model_dump(mode="json"),
                        state="MATERIALIZED",
                    ).model_dump(mode="json"),
                ],
            }
        ),
        encoding="utf-8",
    )

    repo = _make_repo(tmp_path)
    real_main = _run_git(repo, "rev-parse", "origin/main")
    real_tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    trust_store = _make_trust_store(tmp_path, real_main, real_tree)
    leasable = node.model_copy(update={"base_pin": real_main})

    governor = AutonomousGovernor(
        current_main=real_main,
        current_tree=real_tree,
        trusted_anchor=load_runtime_anchor(store=trust_store),
        origination_projection_store=origination_store,
    )
    governor.add_node(leasable)
    governor.mark_ready(leasable.package_id)

    with pytest.raises(GovernorError) as excinfo:
        governor.lease(leasable.package_id, "governor-pilot-local", branch="feat/x", worktree="wt")
    assert excinfo.value.code == "STALE_ORIGINATION_IDENTITY"


def test_lease_denied_when_no_current_origination_record_exists(tmp_path: Path) -> None:
    """§9 matrix: CURRENT IDENTITY MISSING = DENIED.

    A node claiming a revision that the projection no longer holds as
    active (its row went TERMINAL, or the store holds nothing for this
    package at all) has nothing to prove its authority against. Same
    single comparison, same denial -- an absent current revision is not
    an implicit yes.
    """
    origination_store = tmp_path / "origination-store"
    origination_store.mkdir()
    (origination_store / "origination.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "package": "AS-ORCH-ORIGINATION-PROJECTION-001",
                "records": [],
            }
        ),
        encoding="utf-8",
    )

    repo = _make_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    trust_store = _make_trust_store(tmp_path, main, tree)

    node = _minimal_work_node("ORIG-orphan", base_pin=main, surface_id="orphan-surface").model_copy(
        update={"origination_identity": "c" * 64}
    )

    governor = AutonomousGovernor(
        current_main=main,
        current_tree=tree,
        trusted_anchor=load_runtime_anchor(store=trust_store),
        origination_projection_store=origination_store,
    )
    governor.add_node(node)
    governor.mark_ready(node.package_id)

    with pytest.raises(GovernorError) as excinfo:
        governor.lease(node.package_id, "governor-pilot-local", branch="feat/x", worktree="wt")
    assert excinfo.value.code == "STALE_ORIGINATION_IDENTITY"


def _loop_for(governor: AutonomousGovernor, trusted, repo: Path, tmp_path: Path, name: str):
    return AutonomousLoop(
        governor=governor,
        trusted=trusted,
        store=tmp_path / f"loop-{name}",
        root=repo,
    )


def _assert_hard_blocker_receipt(result, *, expected_code: str) -> None:
    """Every property owner directive
    D-MAIN-ATLAS-LOOP-HARD-BLOCKER-CLOSURE §2 requires of a
    governor-authority refusal that reaches the loop boundary."""
    # 3. intended HARD_BLOCKER outcome
    assert result.stop_reason == StopReason.HARD_BLOCKER
    # 4. receipt preserves the ACTUAL denial code, not just the bucket
    assert result.stop_detail == expected_code
    # 5. never mislabelled as retryable contention
    assert result.stop_reason != StopReason.PROJECTION_CONTENTION
    # 7. no lease granted
    assert result.lease_id is None
    # 8. no dispatch attempt created
    assert result.dispatched is False
    assert result.dispatch_id is None
    # 9. no execution authority minted
    assert result.authority_granted is False
    assert result.execution_authorized is False
    assert result.merge_authorized is False
    assert result.phase == LoopPhase.STOPPED


def test_loop_hard_blocks_on_stale_origination_identity(tmp_path: Path) -> None:
    """§2 / STALE_ORIGINATION_IDENTITY across the real governor->loop
    boundary: a node superseded while it sat READY and unleased."""
    repo = _eligible_repo(tmp_path)
    _write_contract(repo, proposed_scope="src/thing.py", success_criteria="K1: thing works")
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    trust_store = _make_trust_store(tmp_path, main, tree)
    origination_store = repo / ORIGINATION_PROJECTION_RELATIVE_DEFAULT

    node = _scan_and_load_node(repo, trust_store, origination_store)
    trusted = load_runtime_anchor(store=trust_store)
    governor = AutonomousGovernor(
        current_main=main,
        current_tree=tree,
        trusted_anchor=trusted,
        origination_projection_store=origination_store,
    )
    governor.add_node(node)
    governor.mark_ready(node.package_id)

    _write_contract(
        repo, proposed_scope="src/thing.py, src/extra.py", success_criteria="K1: thing works"
    )
    _, exit_code = run_origination_scan(
        root=repo, project_id="demo-project", trust_store=trust_store
    )
    assert exit_code == 0

    loop = _loop_for(governor, trusted, repo, tmp_path, "stale")
    # 6. the tick does not crash
    result = loop.tick()
    _assert_hard_blocker_receipt(result, expected_code="STALE_ORIGINATION_IDENTITY")

    # 10/11. no automatic resume, and a subsequent tick does not spin
    # against the same blocked node -- HARD_BLOCKER is explicitly not
    # resumed by `_may_resume_from_no_eligible_work()`.
    second = loop.tick()
    assert second.stop_reason == StopReason.HARD_BLOCKER
    assert second.dispatched is False
    assert second.lease_id is None
    assert governor.snapshot().leases == ()


def test_loop_hard_blocks_when_origination_authority_is_unavailable(tmp_path: Path) -> None:
    """§2 / ORIGINATION_AUTHORITY_UNAVAILABLE across the real boundary:
    an identity-bearing node whose currentness cannot be established
    because the governor has no origination projection wired."""
    repo = _eligible_repo(tmp_path)
    _write_contract(repo, proposed_scope="src/thing.py", success_criteria="K1: thing works")
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    trust_store = _make_trust_store(tmp_path, main, tree)
    origination_store = repo / ORIGINATION_PROJECTION_RELATIVE_DEFAULT

    node = _scan_and_load_node(repo, trust_store, origination_store)
    trusted = load_runtime_anchor(store=trust_store)
    governor = AutonomousGovernor(
        current_main=main,
        current_tree=tree,
        trusted_anchor=trusted,
        # deliberately NOT wired
    )
    governor.add_node(node)
    governor.mark_ready(node.package_id)

    loop = _loop_for(governor, trusted, repo, tmp_path, "unavailable")
    result = loop.tick()
    _assert_hard_blocker_receipt(result, expected_code="ORIGINATION_AUTHORITY_UNAVAILABLE")
    assert governor.snapshot().leases == ()


def test_loop_hard_blocks_on_missing_origination_identity(tmp_path: Path) -> None:
    """§2 / MISSING_ORIGINATION_IDENTITY across the real boundary: a
    legacy origination-built node persisted before the provenance field
    existed."""
    repo = _eligible_repo(tmp_path)
    _write_contract(repo, proposed_scope="src/thing.py", success_criteria="K1: thing works")
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    trust_store = _make_trust_store(tmp_path, main, tree)
    origination_store = repo / ORIGINATION_PROJECTION_RELATIVE_DEFAULT

    node = _scan_and_load_node(repo, trust_store, origination_store)
    legacy_payload = node.model_dump(mode="json")
    del legacy_payload["origination_identity"]
    legacy_node = WorkNode.model_validate(legacy_payload)
    assert legacy_node.origination_identity is None

    trusted = load_runtime_anchor(store=trust_store)
    governor = AutonomousGovernor(
        current_main=main,
        current_tree=tree,
        trusted_anchor=trusted,
        origination_projection_store=origination_store,
    )
    governor.add_node(legacy_node)
    governor.mark_ready(legacy_node.package_id)

    loop = _loop_for(governor, trusted, repo, tmp_path, "legacy")
    result = loop.tick()
    _assert_hard_blocker_receipt(result, expected_code="MISSING_ORIGINATION_IDENTITY")
    assert governor.snapshot().leases == ()


def test_loop_still_leases_a_current_node_normally(tmp_path: Path) -> None:
    """§2 item 12 / §3 control: the loop's new refusal handling must not
    convert a legitimate eligible node into a denial, a retryable
    contention, or a silent omission. A current, identity-bearing node
    still leases and dispatches through the very same loop path the three
    tests above are blocked on."""
    repo = _eligible_repo(tmp_path)
    _write_contract(repo, proposed_scope="src/thing.py", success_criteria="K1: thing works")
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    trust_store = _make_trust_store(tmp_path, main, tree)
    origination_store = repo / ORIGINATION_PROJECTION_RELATIVE_DEFAULT

    node = _scan_and_load_node(repo, trust_store, origination_store)
    trusted = load_runtime_anchor(store=trust_store)
    governor = AutonomousGovernor(
        current_main=main,
        current_tree=tree,
        trusted_anchor=trusted,
        origination_projection_store=origination_store,
    )
    governor.add_node(node)
    governor.mark_ready(node.package_id)

    loop = _loop_for(governor, trusted, repo, tmp_path, "current")
    result = loop.tick()

    # Not blocked, and specifically not blocked by an authority refusal.
    assert result.stop_reason != StopReason.HARD_BLOCKER
    assert result.stop_detail is None
    assert governor.snapshot().leases != ()


def test_tampered_row_with_current_identity_but_no_provenance_does_not_heal(
    tmp_path: Path,
) -> None:
    """The documented NON-healing counter-case, locked as executable truth.

    Independent verification (PR #678) found that the recovery story holds
    for a genuine upgrade but NOT for a row whose stored identity already
    equals what the current formula derives while its ``work_node``
    provenance has been stripped -- store tampering, not an upgrade.
    ``run_origination_scan()`` short-circuits at its already-materialized
    AS-IS branch and never reaches ``reconcile_revision()``, so nothing
    is written and the node stays refused.

    That is the correct outcome for a tampered store (Atlas must not
    invent provenance), but the docstrings must not call it self-healing,
    and this test exists so that claim cannot silently drift back.
    """
    repo = _eligible_repo(tmp_path)
    _write_contract(repo, proposed_scope="src/thing.py", success_criteria="K1: thing works")
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    trust_store = _make_trust_store(tmp_path, main, tree)
    origination_store = repo / ORIGINATION_PROJECTION_RELATIVE_DEFAULT

    node = _scan_and_load_node(repo, trust_store, origination_store)
    package_id = node.package_id
    current_identity = node.origination_identity

    # Strip ONLY the node's provenance, leaving the row keyed under the
    # identity the current formula still derives.
    store_file = origination_store / "origination.json"
    raw = json.loads(store_file.read_text(encoding="utf-8"))
    del raw["records"][0]["work_node"]["origination_identity"]
    store_file.write_text(json.dumps(raw), encoding="utf-8")

    _, scan_exit = run_origination_scan(
        root=repo, project_id="demo-project", trust_store=trust_store
    )
    assert scan_exit == 0

    # The scan wrote nothing new: same single row, same identity, still
    # provenance-less. No fabricated identity, no silent repair.
    records = load_projection(origination_store).records
    assert len(records) == 1
    assert records[0].origination_identity == current_identity
    assert "origination_identity" not in records[0].work_node

    still_blocked = next(
        item
        for item in list_materialized_work_nodes(origination_store)
        if item.package_id == package_id
    )
    assert still_blocked.origination_identity is None

    governor = AutonomousGovernor(
        current_main=main,
        current_tree=tree,
        trusted_anchor=load_runtime_anchor(store=trust_store),
        origination_projection_store=origination_store,
    )
    governor.add_node(still_blocked)
    governor.mark_ready(package_id)
    with pytest.raises(GovernorError) as excinfo:
        governor.lease(package_id, "governor-pilot-local", branch="feat/x", worktree="wt")
    assert excinfo.value.code == "MISSING_ORIGINATION_IDENTITY"
