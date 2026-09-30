"""AS-ORIGIN-MATERIALIZED-SUPERSESSION-001.

Owner directive: D-ATLAS-ORIGINATION-MATERIALIZED-REVISION-SUPERSESSION.

Real incident this package fixes: a previously-MATERIALIZED origination
revision (``work_id`` stable, ``origination_identity`` content-addressed --
see ``identity.py``) stayed durably rehydratable forever once a LATER
authoritative-source revision for the SAME logical work became BLOCKED /
OWNER_HELD / otherwise not execution-ready, protected only by an incidental,
non-designed property (a MATERIALIZED record's ``base_pin`` is never
refreshed, so it eventually trails live main and the rehydration bridge's
own freshness check happens to reject it). ``reconcile_revision()``
(projection.py) is the real fix: the required invariant is *a materialized
revision is rehydratable iff it is still the current authoritative eligible
revision for its work_id*, enforced atomically, independent of base_pin.

These tests exercise ``reconcile_revision()`` and
``list_materialized_work_nodes()`` directly against the real projection
store (unit-level, below ``run_origination_scan()``'s own end-to-end
coverage in ``test_orchestration_origination_cli.py``).
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

from project_atlas.orchestration.origination.materialize import materialize_work_node
from project_atlas.orchestration.origination.pipeline import originate_all
from project_atlas.orchestration.origination.projection import (
    PROJECTION_NAME,
    OriginationProjectionError,
    OriginationRecord,
    list_materialized_work_nodes,
    load_projection,
    persist_proposed,
    reconcile_revision,
)
from project_atlas.orchestration.origination.risk import classify as classify_risk


def _run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
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


def _write_roadmap(root: Path, items: list[dict[str, object]]) -> None:
    (root / "docs").mkdir(parents=True, exist_ok=True)
    fence = json.dumps({"roadmap_items": items}, indent=2)
    (root / "docs" / "ROADMAP.md").write_text(
        f"## Roadmap record\n```json\n{fence}\n```\n", encoding="utf-8"
    )


def _eligible_repo(tmp_path: Path) -> Path:
    repo = _make_repo(tmp_path)
    (repo / "docs").mkdir(parents=True, exist_ok=True)
    (repo / "docs" / "REQUIREMENTS.md").write_text("# Requirements\nFR-1: do the thing.\n")
    test_path = repo / "tests" / "test_feature_x.py"
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(
        'import pytest\n\npytestmark = pytest.mark.skip(reason="not yet implemented")\n\n'
        "def test_placeholder():\n    assert True\n"
    )
    _write_roadmap(
        repo,
        [
            {
                "id": "feature-x",
                "title": "Feature X",
                "status": "NOT_STARTED",
                "lifecycle": "READY",
                "evidence": ["docs/REQUIREMENTS.md", "tests/test_feature_x.py"],
            }
        ],
    )
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "seed roadmap")
    sha = _run_git(repo, "rev-parse", "HEAD")
    _run_git(repo, "update-ref", "refs/remotes/origin/main", sha)
    return repo


def test_reconcile_revision_case_a_same_identity_replay_is_idempotent_and_supersedes_nothing(
    tmp_path: Path,
) -> None:
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    store = tmp_path / "origination-store"

    outcomes = originate_all(repo, "demo-project")
    proposal, policy = outcomes[0].proposal, outcomes[0].policy
    persist_proposed(store, proposal, policy)
    classification = classify_risk(
        proposed_scope=proposal.proposed_scope, success_criteria=proposal.success_criteria
    )
    node = materialize_work_node(
        proposal, classification, base_pin=main, surface_id=f"{proposal.project_id}-a"
    )

    first = reconcile_revision(
        store,
        origination_identity=proposal.origination_identity,
        package_id=proposal.work_id,
        work_node=node,
    )
    assert first.already_current is False
    assert first.superseded == ()
    assert first.materialized is not None

    # Case A: repeat call, SAME identity, SAME evidence -- a re-scan.
    second = reconcile_revision(
        store,
        origination_identity=proposal.origination_identity,
        package_id=proposal.work_id,
        work_node=node,
    )
    assert second.already_current is True
    assert second.superseded == ()  # never re-supersedes anything on replay
    assert second.materialized is not None
    assert second.materialized.work_node == first.materialized.work_node

    projection = load_projection(store)
    assert len(projection.records) == 1
    assert projection.records[0].state == "MATERIALIZED"


def test_reconcile_revision_case_b_new_blocked_revision_supersedes_without_materializing(
    tmp_path: Path,
) -> None:
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    store = tmp_path / "origination-store"

    outcomes = originate_all(repo, "demo-project")
    proposal_a, policy_a = outcomes[0].proposal, outcomes[0].policy
    persist_proposed(store, proposal_a, policy_a)
    classification = classify_risk(
        proposed_scope=proposal_a.proposed_scope, success_criteria=proposal_a.success_criteria
    )
    node_a = materialize_work_node(
        proposal_a, classification, base_pin=main, surface_id=f"{proposal_a.project_id}-a"
    )
    outcome_a = reconcile_revision(
        store,
        origination_identity=proposal_a.origination_identity,
        package_id=proposal_a.work_id,
        work_node=node_a,
    )
    assert outcome_a.materialized is not None

    # B: a new, distinct revision (different origination_identity), now
    # BLOCKED -- materialize_work_node() would raise PROPOSAL_BLOCKED for
    # it, so the scan-level caller never even builds a WorkNode; here we
    # exercise reconcile_revision() directly with work_node=None, exactly
    # what cli.py's MaterializationError handler does.
    proposal_b = proposal_a.model_copy(
        update={"origination_identity": "b" * 64, "blockers": ("EXTERNAL_BLOCKED: needs data",)}
    )
    persist_proposed(store, proposal_b, policy_a)

    outcome_b = reconcile_revision(
        store,
        origination_identity=proposal_b.origination_identity,
        package_id=proposal_b.work_id,
        work_node=None,
    )
    assert outcome_b.materialized is None
    assert len(outcome_b.superseded) == 1
    assert outcome_b.superseded[0].origination_identity == proposal_a.origination_identity
    assert outcome_b.superseded[0].state == "SUPERSEDED"
    # Historical evidence preserved -- same work_node as before, only
    # `state` changed.
    assert outcome_b.superseded[0].work_node == outcome_a.materialized.work_node

    projection = load_projection(store)
    a_record = next(
        r for r in projection.records if r.origination_identity == proposal_a.origination_identity
    )
    b_record = next(
        r for r in projection.records if r.origination_identity == proposal_b.origination_identity
    )
    assert a_record.state == "SUPERSEDED"
    assert b_record.state == "PROPOSED"
    assert b_record.work_node is None

    # list_materialized_work_nodes() must not return A any more.
    assert list_materialized_work_nodes(store) == ()


def test_reconcile_revision_case_c_new_ready_revision_supersedes_and_materializes(
    tmp_path: Path,
) -> None:
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    other_pin = "d" * 40
    store = tmp_path / "origination-store"

    outcomes = originate_all(repo, "demo-project")
    proposal_a, policy_a = outcomes[0].proposal, outcomes[0].policy
    persist_proposed(store, proposal_a, policy_a)
    classification = classify_risk(
        proposed_scope=proposal_a.proposed_scope, success_criteria=proposal_a.success_criteria
    )
    node_a = materialize_work_node(
        proposal_a, classification, base_pin=main, surface_id=f"{proposal_a.project_id}-a"
    )
    reconcile_revision(
        store,
        origination_identity=proposal_a.origination_identity,
        package_id=proposal_a.work_id,
        work_node=node_a,
    )

    proposal_c = proposal_a.model_copy(update={"origination_identity": "c" * 64})
    persist_proposed(store, proposal_c, policy_a)
    node_c = materialize_work_node(
        proposal_c, classification, base_pin=other_pin, surface_id=f"{proposal_c.project_id}-c"
    )
    outcome_c = reconcile_revision(
        store,
        origination_identity=proposal_c.origination_identity,
        package_id=proposal_c.work_id,
        work_node=node_c,
    )
    assert outcome_c.already_current is False
    assert len(outcome_c.superseded) == 1
    assert outcome_c.superseded[0].origination_identity == proposal_a.origination_identity
    assert outcome_c.materialized is not None
    assert outcome_c.materialized.origination_identity == proposal_c.origination_identity
    assert outcome_c.materialized.work_node is not None
    assert outcome_c.materialized.work_node["base_pin"] == other_pin

    active = list_materialized_work_nodes(store)
    assert len(active) == 1
    assert active[0].base_pin == other_pin


def test_reconcile_revision_case_d_ambiguous_active_revisions_fails_closed(
    tmp_path: Path,
) -> None:
    """Owner directive §5 Case D: never pick one arbitrarily. Constructs a
    corrupt store with TWO already-active revisions sharing one
    package_id (a shape ``reconcile_revision()`` itself should never
    produce -- simulating pre-migration/corrupt data, or a bug elsewhere)
    and proves a THIRD revision's reconciliation refuses to touch either.
    """
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    store = tmp_path / "origination-store"

    outcomes = originate_all(repo, "demo-project")
    proposal_a, policy_a = outcomes[0].proposal, outcomes[0].policy
    classification = classify_risk(
        proposed_scope=proposal_a.proposed_scope, success_criteria=proposal_a.success_criteria
    )
    node_a = materialize_work_node(
        proposal_a, classification, base_pin=main, surface_id=f"{proposal_a.project_id}-a"
    )
    node_b = materialize_work_node(
        proposal_a, classification, base_pin=main, surface_id=f"{proposal_a.project_id}-b"
    )
    proposal_b = proposal_a.model_copy(update={"origination_identity": "b" * 64})
    proposal_c = proposal_a.model_copy(update={"origination_identity": "c" * 64})

    # Directly construct a corrupt store: A and B both already MATERIALIZED
    # and active, sharing one package_id -- bypassing reconcile_revision()
    # entirely (which would never produce this shape) to simulate corrupt/
    # pre-migration data.
    store.mkdir(parents=True, exist_ok=True)
    from project_atlas.orchestration.autonomy.lease_projection import _write_atomic
    from project_atlas.orchestration.origination.projection import (
        OriginationProjection,
    )

    corrupt = OriginationProjection(
        records=(
            OriginationRecord(
                origination_identity=proposal_a.origination_identity,
                project_id=proposal_a.project_id,
                proposal=proposal_a.model_dump(mode="json"),
                policy_result=policy_a.model_dump(mode="json"),
                work_node=node_a.model_dump(mode="json"),
                state="MATERIALIZED",
            ),
            OriginationRecord(
                origination_identity=proposal_b.origination_identity,
                project_id=proposal_b.project_id,
                proposal=proposal_b.model_dump(mode="json"),
                policy_result=policy_a.model_dump(mode="json"),
                work_node=node_b.model_dump(mode="json"),
                state="MATERIALIZED",
            ),
            OriginationRecord(
                origination_identity=proposal_c.origination_identity,
                project_id=proposal_c.project_id,
                proposal=proposal_c.model_dump(mode="json"),
                policy_result=policy_a.model_dump(mode="json"),
                work_node=None,
                state="PROPOSED",
            ),
        )
    )
    _write_atomic(store / PROJECTION_NAME, corrupt.model_dump(mode="json"))

    # reconcile_revision() for C must fail closed rather than pick one of
    # A/B to supersede.
    try:
        reconcile_revision(
            store,
            origination_identity=proposal_c.origination_identity,
            package_id=proposal_c.work_id,
            work_node=node_a,
        )
        raise AssertionError("expected OriginationProjectionError")
    except OriginationProjectionError as exc:
        assert exc.code == "AMBIGUOUS_ACTIVE_REVISION"

    # Nothing changed: still both A and B MATERIALIZED, C still PROPOSED.
    projection = load_projection(store)
    states = {r.origination_identity: r.state for r in projection.records}
    assert states[proposal_a.origination_identity] == "MATERIALIZED"
    assert states[proposal_b.origination_identity] == "MATERIALIZED"
    assert states[proposal_c.origination_identity] == "PROPOSED"

    # The READ side independently fails closed too, for any caller that
    # goes through list_materialized_work_nodes() (the rehydration bridge)
    # rather than reconcile_revision() -- defense in depth.
    try:
        list_materialized_work_nodes(store)
        raise AssertionError("expected OriginationProjectionError")
    except OriginationProjectionError as exc:
        assert exc.code == "AMBIGUOUS_ACTIVE_REVISION"


def test_reconcile_revision_record_unknown_when_identity_never_proposed(tmp_path: Path) -> None:
    store = tmp_path / "origination-store"
    store.mkdir(parents=True, exist_ok=True)
    try:
        reconcile_revision(
            store,
            origination_identity="a" * 64,
            package_id="ORIG-doesnotexist",
            work_node=None,
        )
        raise AssertionError("expected OriginationProjectionError")
    except OriginationProjectionError as exc:
        assert exc.code == "RECORD_UNKNOWN"


def test_reconcile_revision_supersession_does_not_depend_on_base_pin(tmp_path: Path) -> None:
    """Owner directive §17: add an explicit regression where the old
    materialization's base_pin EQUALS current live main (i.e. it would
    still pass the rehydration bridge's independent base_pin freshness
    check) and authoritative source content changes to blocked WITHOUT
    changing that supplied base_pin. The old revision must still become
    non-rehydratable -- proving supersession is a real, designed
    invariant, not merely a side effect of base_pin going stale.
    """
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    store = tmp_path / "origination-store"

    outcomes = originate_all(repo, "demo-project")
    proposal_a, policy_a = outcomes[0].proposal, outcomes[0].policy
    persist_proposed(store, proposal_a, policy_a)
    classification = classify_risk(
        proposed_scope=proposal_a.proposed_scope, success_criteria=proposal_a.success_criteria
    )
    node_a = materialize_work_node(
        proposal_a, classification, base_pin=main, surface_id=f"{proposal_a.project_id}-a"
    )
    reconcile_revision(
        store,
        origination_identity=proposal_a.origination_identity,
        package_id=proposal_a.work_id,
        work_node=node_a,
    )

    proposal_b = proposal_a.model_copy(
        update={"origination_identity": "e" * 64, "blockers": ("EXTERNAL_BLOCKED: needs data",)}
    )
    persist_proposed(store, proposal_b, policy_a)
    outcome_b = reconcile_revision(
        store,
        origination_identity=proposal_b.origination_identity,
        package_id=proposal_b.work_id,
        work_node=None,  # blocked -- never materializes
    )
    assert len(outcome_b.superseded) == 1

    projection = load_projection(store)
    a_record = next(
        r for r in projection.records if r.origination_identity == proposal_a.origination_identity
    )
    # A's base_pin is UNCHANGED -- still exactly live main. If the fix
    # were (bugfully) relying on base_pin staleness, this record would
    # still look "fresh" to a naive base_pin check -- but it is SUPERSEDED
    # regardless, and list_materialized_work_nodes() excludes it purely
    # by state, never consulting base_pin at all.
    assert a_record.work_node is not None
    assert a_record.work_node["base_pin"] == main
    assert a_record.state == "SUPERSEDED"
    active = list_materialized_work_nodes(store)
    assert not any(node.package_id == proposal_a.work_id for node in active)


def test_reconcile_revision_closes_the_toctou_race_between_two_new_revisions(
    tmp_path: Path,
) -> None:
    """Same threat model as the pre-existing
    ``persist_materialized_if_no_active_conflict`` TOCTOU test, but for
    ``reconcile_revision()``: two REAL threads racing to reconcile two
    different ``origination_identity`` proposals sharing one
    ``package_id``, synchronized with a ``Barrier`` to maximize actual
    lock contention. Exactly one must end up as the sole active
    (MATERIALIZED) revision; the other must be superseded or itself
    supersede the first -- never both active, never neither, never a
    corrupt/ambiguous store.
    """
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    store = tmp_path / "origination-store"

    outcomes = originate_all(repo, "demo-project")
    proposal_a, policy_a = outcomes[0].proposal, outcomes[0].policy
    classification = classify_risk(
        proposed_scope=proposal_a.proposed_scope, success_criteria=proposal_a.success_criteria
    )
    node_a = materialize_work_node(
        proposal_a, classification, base_pin=main, surface_id=f"{proposal_a.project_id}-a"
    )
    proposal_b = proposal_a.model_copy(update={"origination_identity": "f" * 64})
    node_b = materialize_work_node(
        proposal_a, classification, base_pin=main, surface_id=f"{proposal_a.project_id}-b"
    )
    persist_proposed(store, proposal_a, policy_a)
    persist_proposed(store, proposal_b, policy_a)

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def _race(identity: str, node: object) -> None:
        try:
            barrier.wait()
            reconcile_revision(
                store,
                origination_identity=identity,
                package_id=proposal_a.work_id,
                work_node=node,  # type: ignore[arg-type]
            )
        except BaseException as exc:
            errors.append(exc)

    t1 = threading.Thread(target=_race, args=(proposal_a.origination_identity, node_a))
    t2 = threading.Thread(target=_race, args=(proposal_b.origination_identity, node_b))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert not t1.is_alive()
    assert not t2.is_alive()
    assert errors == []

    active = list_materialized_work_nodes(store)
    assert len(active) == 1

    projection = load_projection(store)
    assert len(projection.records) == 2
    states = {r.origination_identity: r.state for r in projection.records}
    superseded_count = sum(1 for state in states.values() if state == "SUPERSEDED")
    materialized_count = sum(1 for state in states.values() if state == "MATERIALIZED")
    assert superseded_count == 1
    assert materialized_count == 1


def test_legacy_conflict_primitive_never_resurrects_a_superseded_row(
    tmp_path: Path,
) -> None:
    """Independent-IV finding (delta round on PR #677, two review passes):
    `persist_materialized_if_no_active_conflict()` predates the
    SUPERSEDED state. Round 1 fix aligned its own-identity "already
    active" check with `_INACTIVE_STATES` -- but a Copilot review pass on
    that exact fix (thread #3, PR #677) correctly identified the fix
    itself was still wrong: once `state not in _INACTIVE_STATES` is
    FALSE for a SUPERSEDED row, the function fell through to its
    unconditional overwrite branch and RESURRECTED the row back to
    MATERIALIZED. A TERMINAL/SUPERSEDED transition is permanent (owner
    directive §4: "NO FUTURE AUTHORITY MAY BE DERIVED FROM THIS OLD
    ORIGINATION REVISION") -- the correct fix is for the own-identity
    check to key purely on "does a work_node already exist for this
    identity", never on its current state, so it always returns the
    frozen historical row unchanged instead.
    """
    from project_atlas.orchestration.origination.projection import (
        persist_materialized_if_no_active_conflict,
    )

    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    store = tmp_path / "origination-store"

    outcomes = originate_all(repo, "demo-project")
    proposal_a, policy_a = outcomes[0].proposal, outcomes[0].policy
    persist_proposed(store, proposal_a, policy_a)
    classification = classify_risk(
        proposed_scope=proposal_a.proposed_scope, success_criteria=proposal_a.success_criteria
    )
    node_a = materialize_work_node(
        proposal_a, classification, base_pin=main, surface_id=f"{proposal_a.project_id}-a"
    )
    reconcile_revision(
        store,
        origination_identity=proposal_a.origination_identity,
        package_id=proposal_a.work_id,
        work_node=node_a,
    )

    # B blocks A -- A is now genuinely SUPERSEDED, nothing else active for
    # this package_id (mirrors the exact incident shape).
    proposal_b = proposal_a.model_copy(
        update={"origination_identity": "9" * 64, "blockers": ("EXTERNAL_BLOCKED: needs data",)}
    )
    persist_proposed(store, proposal_b, policy_a)
    reconcile_revision(
        store,
        origination_identity=proposal_b.origination_identity,
        package_id=proposal_b.work_id,
        work_node=None,
    )
    superseded_row = next(
        r
        for r in load_projection(store).records
        if r.origination_identity == proposal_a.origination_identity
    )
    assert superseded_row.state == "SUPERSEDED"

    # Calling the legacy primitive directly for A's own (now-superseded)
    # identity must return it frozen and unchanged -- never resurrect it
    # back to MATERIALIZED, even with nothing else active for this
    # package_id (never even reaching the different-identity conflict
    # check the TOCTOU test above exercises).
    record, conflict = persist_materialized_if_no_active_conflict(
        store, proposal_a.origination_identity, node_a
    )
    assert conflict is None
    assert record is not None
    assert record.state == "SUPERSEDED"
    assert record.work_node == superseded_row.work_node

    projection_after = load_projection(store)
    unchanged_row = next(
        r
        for r in projection_after.records
        if r.origination_identity == proposal_a.origination_identity
    )
    assert unchanged_row.state == "SUPERSEDED"


def test_reconcile_revision_never_resurrects_a_permanently_resolved_identity(
    tmp_path: Path,
) -> None:
    """Owner directive §4 ("NO FUTURE AUTHORITY MAY BE DERIVED FROM THIS
    OLD ORIGINATION REVISION"): once an identity's row is SUPERSEDED, a
    repeat `reconcile_revision()` call for that EXACT SAME identity (a
    content revert to byte-identical prior text would legitimately
    produce it again, since origination_identity is content-addressed)
    must fail closed rather than resurrect it -- even with nothing else
    currently active for the package_id.
    """
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    store = tmp_path / "origination-store"

    outcomes = originate_all(repo, "demo-project")
    proposal_a, policy_a = outcomes[0].proposal, outcomes[0].policy
    persist_proposed(store, proposal_a, policy_a)
    classification = classify_risk(
        proposed_scope=proposal_a.proposed_scope, success_criteria=proposal_a.success_criteria
    )
    node_a = materialize_work_node(
        proposal_a, classification, base_pin=main, surface_id=f"{proposal_a.project_id}-a"
    )
    reconcile_revision(
        store,
        origination_identity=proposal_a.origination_identity,
        package_id=proposal_a.work_id,
        work_node=node_a,
    )

    # Supersede A with B, then leave nothing active for this package_id.
    proposal_b = proposal_a.model_copy(
        update={"origination_identity": "7" * 64, "blockers": ("EXTERNAL_BLOCKED: needs data",)}
    )
    persist_proposed(store, proposal_b, policy_a)
    reconcile_revision(
        store,
        origination_identity=proposal_b.origination_identity,
        package_id=proposal_b.work_id,
        work_node=None,
    )

    # A's exact evidence is proposed again -- same origination_identity,
    # since it is a pure function of the (unchanged) content.
    try:
        reconcile_revision(
            store,
            origination_identity=proposal_a.origination_identity,
            package_id=proposal_a.work_id,
            work_node=node_a,
        )
        raise AssertionError("expected OriginationProjectionError")
    except OriginationProjectionError as exc:
        assert exc.code == "IDENTITY_ALREADY_RESOLVED"

    a_record = next(
        r
        for r in load_projection(store).records
        if r.origination_identity == proposal_a.origination_identity
    )
    assert a_record.state == "SUPERSEDED"  # unchanged by the refused attempt


def test_reconcile_revision_rejects_a_mismatched_package_id(tmp_path: Path) -> None:
    """Independent-IV finding (copilot-pull-request-reviewer, PR #677):
    `reconcile_revision()` must not trust a caller-supplied `package_id`
    that disagrees with the record's own frozen `proposal["work_id"]` or
    the given `work_node.package_id` -- defense-in-depth; the one real
    caller always derives all three from the same proposal.
    """
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    store = tmp_path / "origination-store"

    outcomes = originate_all(repo, "demo-project")
    proposal, policy = outcomes[0].proposal, outcomes[0].policy
    persist_proposed(store, proposal, policy)
    classification = classify_risk(
        proposed_scope=proposal.proposed_scope, success_criteria=proposal.success_criteria
    )
    node = materialize_work_node(
        proposal, classification, base_pin=main, surface_id=f"{proposal.project_id}-a"
    )

    try:
        reconcile_revision(
            store,
            origination_identity=proposal.origination_identity,
            package_id="ORIG-totally-unrelated",
            work_node=node,
        )
        raise AssertionError("expected OriginationProjectionError")
    except OriginationProjectionError as exc:
        assert exc.code == "PACKAGE_ID_MISMATCH"

    projection = load_projection(store)
    assert len(projection.records) == 1
    assert projection.records[0].state == "PROPOSED"  # untouched by the rejected call


def test_list_materialized_work_nodes_contract_missing_vs_ambiguous(tmp_path: Path) -> None:
    """Owner directive §9 (PR #677 review): documentation and the actual
    API contract must agree exactly -- a missing/unreadable store is NOT
    treated as corruption (returns no candidates, matching this
    function's pre-existing, unchanged-by-this-package posture), while
    an ambiguous store (two active revisions sharing one package_id)
    DOES fail closed. Both halves of the real contract, tested directly
    against the one function, not merely asserted in a comment.
    """
    missing_store = tmp_path / "never-created"
    assert list_materialized_work_nodes(missing_store) == ()

    ambiguous_store = tmp_path / "ambiguous-store"
    ambiguous_store.mkdir()
    repo_parent = tmp_path / "repo-for-ambiguous"
    repo_parent.mkdir()
    repo = _eligible_repo(repo_parent)
    main = _run_git(repo, "rev-parse", "origin/main")
    outcomes = originate_all(repo, "demo-project")
    proposal_a, policy_a = outcomes[0].proposal, outcomes[0].policy
    classification = classify_risk(
        proposed_scope=proposal_a.proposed_scope, success_criteria=proposal_a.success_criteria
    )
    node_a = materialize_work_node(
        proposal_a, classification, base_pin=main, surface_id=f"{proposal_a.project_id}-a"
    )
    node_b = materialize_work_node(
        proposal_a, classification, base_pin=main, surface_id=f"{proposal_a.project_id}-b"
    )
    proposal_b = proposal_a.model_copy(update={"origination_identity": "5" * 64})
    from project_atlas.orchestration.autonomy.lease_projection import _write_atomic
    from project_atlas.orchestration.origination.projection import OriginationProjection

    corrupt = OriginationProjection(
        records=(
            OriginationRecord(
                origination_identity=proposal_a.origination_identity,
                project_id=proposal_a.project_id,
                proposal=proposal_a.model_dump(mode="json"),
                policy_result=policy_a.model_dump(mode="json"),
                work_node=node_a.model_dump(mode="json"),
                state="MATERIALIZED",
            ),
            OriginationRecord(
                origination_identity=proposal_b.origination_identity,
                project_id=proposal_b.project_id,
                proposal=proposal_b.model_dump(mode="json"),
                policy_result=policy_a.model_dump(mode="json"),
                work_node=node_b.model_dump(mode="json"),
                state="MATERIALIZED",
            ),
        )
    )
    _write_atomic(ambiguous_store / PROJECTION_NAME, corrupt.model_dump(mode="json"))
    try:
        list_materialized_work_nodes(ambiguous_store)
        raise AssertionError("expected OriginationProjectionError")
    except OriginationProjectionError as exc:
        assert exc.code == "AMBIGUOUS_ACTIVE_REVISION"


def test_reconcile_revision_denies_a_stale_source_snapshot_and_writes_nothing(
    tmp_path: Path,
) -> None:
    """Independent-verification finding F2 on PR #677: ``reconcile_
    revision()`` alone is last-caller-wins -- a scan stalled across a
    source edit could replay a reconciliation derived from a STALE
    snapshot and dethrone the genuinely newer revision (empirically
    reproduced pre-fix: final states v2=SUPERSEDED, v1=MATERIALIZED,
    with the stale revision served as the sole rehydratable authority
    until the next fresh scan). With the production checker
    (``cli._source_identity_still_current``, run INSIDE the reconcile
    lock exactly as ``run_origination_scan()`` now passes it), the stale
    replay is denied with ``STALE_SOURCE_SNAPSHOT`` before anything is
    superseded or materialized -- the store is byte-identical after the
    denial -- while a genuine source REVERT back to the old revision's
    exact content (same ``origination_identity``, but genuinely current
    again) still reconciles, preserving owner gate (d)'s verified revert
    semantics for an identity that never itself materialized. (An
    identity that DID once materialize and was then superseded is a
    different case: ``IDENTITY_ALREADY_RESOLVED`` permanently refuses it
    -- owner directive §4, see
    ``test_reconcile_revision_never_resurrects_a_permanently_resolved_
    identity`` above. Here v1's stale replay was denied before any
    ``work_node`` ever attached, so its revert is a legitimate
    first-time materialization of a genuinely current identity.)"""
    from functools import partial

    import pytest

    from project_atlas.orchestration.origination.cli import _source_identity_still_current

    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    store = tmp_path / "origination-store"

    # The soon-to-stall scan takes its source snapshot at revision v1.
    outcomes_v1 = originate_all(repo, "demo-project")
    proposal_v1, policy_v1 = outcomes_v1[0].proposal, outcomes_v1[0].policy

    # Source truth is edited to v2; a FRESH scan reconciles v2 first,
    # through the same checker production now always passes.
    _write_roadmap(
        repo,
        [
            {
                "id": "feature-x",
                "title": "Feature X v2 (current truth)",
                "status": "NOT_STARTED",
                "lifecycle": "READY",
                "evidence": ["docs/REQUIREMENTS.md", "tests/test_feature_x.py"],
            }
        ],
    )
    outcomes_v2 = originate_all(repo, "demo-project")
    proposal_v2, policy_v2 = outcomes_v2[0].proposal, outcomes_v2[0].policy
    assert proposal_v2.origination_identity != proposal_v1.origination_identity
    assert proposal_v2.work_id == proposal_v1.work_id
    persist_proposed(store, proposal_v2, policy_v2)
    classification_v2 = classify_risk(
        proposed_scope=proposal_v2.proposed_scope,
        success_criteria=proposal_v2.success_criteria,
    )
    node_v2 = materialize_work_node(
        proposal_v2, classification_v2, base_pin=main, surface_id=f"{proposal_v2.project_id}-x"
    )
    current = reconcile_revision(
        store,
        origination_identity=proposal_v2.origination_identity,
        package_id=proposal_v2.work_id,
        work_node=node_v2,
        still_current=partial(
            _source_identity_still_current,
            repo,
            "demo-project",
            proposal_v2.origination_identity,
        ),
    )
    assert current.materialized is not None  # current truth passes the checker

    # The stalled scan resumes and replays its v1-derived reconciliation
    # -- the exact production call shape, checker included.
    persist_proposed(store, proposal_v1, policy_v1)
    classification_v1 = classify_risk(
        proposed_scope=proposal_v1.proposed_scope,
        success_criteria=proposal_v1.success_criteria,
    )
    node_v1 = materialize_work_node(
        proposal_v1, classification_v1, base_pin=main, surface_id=f"{proposal_v1.project_id}-x"
    )
    before = (store / PROJECTION_NAME).read_bytes()
    with pytest.raises(OriginationProjectionError) as excinfo:
        reconcile_revision(
            store,
            origination_identity=proposal_v1.origination_identity,
            package_id=proposal_v1.work_id,
            work_node=node_v1,
            still_current=partial(
                _source_identity_still_current,
                repo,
                "demo-project",
                proposal_v1.origination_identity,
            ),
        )
    assert excinfo.value.code == "STALE_SOURCE_SNAPSHOT"
    # Fail closed to a no-op: byte-identical store, v2 still the sole
    # active revision, nothing superseded, nothing resurrected.
    assert (store / PROJECTION_NAME).read_bytes() == before
    states = {r.origination_identity: r.state for r in load_projection(store).records}
    assert states[proposal_v2.origination_identity] == "MATERIALIZED"
    assert states[proposal_v1.origination_identity] == "PROPOSED"
    active = list_materialized_work_nodes(store)
    assert len(active) == 1

    # Genuine revert: source truth goes BACK to v1's exact content --
    # v1's identity is current again, and the same checker now passes.
    _write_roadmap(
        repo,
        [
            {
                "id": "feature-x",
                "title": "Feature X",
                "status": "NOT_STARTED",
                "lifecycle": "READY",
                "evidence": ["docs/REQUIREMENTS.md", "tests/test_feature_x.py"],
            }
        ],
    )
    reverted = reconcile_revision(
        store,
        origination_identity=proposal_v1.origination_identity,
        package_id=proposal_v1.work_id,
        work_node=node_v1,
        still_current=partial(
            _source_identity_still_current,
            repo,
            "demo-project",
            proposal_v1.origination_identity,
        ),
    )
    assert reverted.materialized is not None
    assert [row.origination_identity for row in reverted.superseded] == [
        proposal_v2.origination_identity
    ]


def test_reconcile_revision_still_current_false_denies_before_any_write(tmp_path: Path) -> None:
    """Unit-level, primitive-only proof (independent of ``cli.py``'s own
    real checker): ``reconcile_revision(still_current=...)`` returning
    ``False`` must deny with ``STALE_SOURCE_SNAPSHOT`` and leave the
    store completely unchanged -- checked directly against the
    ``reconcile_revision()`` contract itself, not through a real scan.
    """
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    store = tmp_path / "origination-store"

    outcomes = originate_all(repo, "demo-project")
    proposal, policy = outcomes[0].proposal, outcomes[0].policy
    persist_proposed(store, proposal, policy)
    classification = classify_risk(
        proposed_scope=proposal.proposed_scope, success_criteria=proposal.success_criteria
    )
    node = materialize_work_node(
        proposal, classification, base_pin=main, surface_id=f"{proposal.project_id}-a"
    )

    before = load_projection(store)
    try:
        reconcile_revision(
            store,
            origination_identity=proposal.origination_identity,
            package_id=proposal.work_id,
            work_node=node,
            still_current=lambda: False,
        )
        raise AssertionError("expected OriginationProjectionError")
    except OriginationProjectionError as exc:
        assert exc.code == "STALE_SOURCE_SNAPSHOT"

    after = load_projection(store)
    assert before == after
    assert list_materialized_work_nodes(store) == ()


def test_reconcile_revision_still_current_raising_denies_before_any_write(
    tmp_path: Path,
) -> None:
    """Independent-IV finding on PR #678 (also independently flagged by
    an automated review on the same PR): a ``still_current`` callback
    that *raises* -- rather than cleanly returning ``False`` -- must be
    treated exactly like ``False`` (unverifiable is stale, not a crash).
    Before the fix, ``reconcile_revision()`` only guarded the boolean
    case (``not still_current()``); an exception from the callback
    propagated straight out of the locked critical section instead of
    denying with ``STALE_SOURCE_SNAPSHOT``, contradicting this
    function's own documented contract. Proven at the primitive level,
    independent of ``cli.py``'s own real checker (which happened to be
    safe only because it had its own separate try/except).
    """
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    store = tmp_path / "origination-store"

    outcomes = originate_all(repo, "demo-project")
    proposal, policy = outcomes[0].proposal, outcomes[0].policy
    persist_proposed(store, proposal, policy)
    classification = classify_risk(
        proposed_scope=proposal.proposed_scope, success_criteria=proposal.success_criteria
    )
    node = materialize_work_node(
        proposal, classification, base_pin=main, surface_id=f"{proposal.project_id}-a"
    )

    before = load_projection(store)

    def _raises() -> bool:
        raise RuntimeError("source tree unreadable mid-scan")

    try:
        reconcile_revision(
            store,
            origination_identity=proposal.origination_identity,
            package_id=proposal.work_id,
            work_node=node,
            still_current=_raises,
        )
        raise AssertionError("expected OriginationProjectionError")
    except OriginationProjectionError as exc:
        assert exc.code == "STALE_SOURCE_SNAPSHOT"

    after = load_projection(store)
    assert before == after
    assert list_materialized_work_nodes(store) == ()


def test_reconcile_revision_still_current_callback_never_consulted_on_idempotent_or_permanent_paths(
    tmp_path: Path,
) -> None:
    """The ``still_current`` callback is documented to be skipped on the
    two no-op paths (idempotent replay, permanent ``IDENTITY_ALREADY_
    RESOLVED`` refusal) since neither writes anything -- verified
    directly by supplying a callback that raises if ever called, proving
    it genuinely never runs on those paths (not merely "returns True by
    coincidence" in the shipped test above).
    """
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    store = tmp_path / "origination-store"

    def _boom() -> bool:
        raise AssertionError("still_current must not be consulted on this path")

    outcomes = originate_all(repo, "demo-project")
    proposal_a, policy_a = outcomes[0].proposal, outcomes[0].policy
    persist_proposed(store, proposal_a, policy_a)
    classification = classify_risk(
        proposed_scope=proposal_a.proposed_scope, success_criteria=proposal_a.success_criteria
    )
    node_a = materialize_work_node(
        proposal_a, classification, base_pin=main, surface_id=f"{proposal_a.project_id}-a"
    )
    first = reconcile_revision(
        store,
        origination_identity=proposal_a.origination_identity,
        package_id=proposal_a.work_id,
        work_node=node_a,
        still_current=None,
    )
    assert first.materialized is not None

    # Idempotent replay: must not consult _boom.
    replay = reconcile_revision(
        store,
        origination_identity=proposal_a.origination_identity,
        package_id=proposal_a.work_id,
        work_node=node_a,
        still_current=_boom,
    )
    assert replay.already_current is True

    # Supersede A with B, then attempt to resurrect A -- IDENTITY_ALREADY_
    # RESOLVED must fire before _boom is ever consulted.
    proposal_b = proposal_a.model_copy(
        update={"origination_identity": "3" * 64, "blockers": ("EXTERNAL_BLOCKED: x",)}
    )
    persist_proposed(store, proposal_b, policy_a)
    reconcile_revision(
        store,
        origination_identity=proposal_b.origination_identity,
        package_id=proposal_b.work_id,
        work_node=None,
        still_current=None,
    )
    try:
        reconcile_revision(
            store,
            origination_identity=proposal_a.origination_identity,
            package_id=proposal_a.work_id,
            work_node=node_a,
            still_current=_boom,
        )
        raise AssertionError("expected OriginationProjectionError")
    except OriginationProjectionError as exc:
        assert exc.code == "IDENTITY_ALREADY_RESOLVED"


def test_reconcile_revision_still_current_closes_the_toctou_race_under_real_threads(
    tmp_path: Path,
) -> None:
    """Real multi-threaded race, `still_current` included on both sides:
    a "stale" reconcile (whose checker always returns ``False``, as a
    genuinely-stale snapshot's would) races against a "current" one
    (whose checker always returns ``True``) for the SAME package_id,
    ``Barrier``-synchronized to maximize actual lock contention. The
    stale side must NEVER win, regardless of thread scheduling order --
    proving the checker's own denial is enforced INSIDE the lock, not
    racily checked-then-written.
    """
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    store = tmp_path / "origination-store"

    outcomes = originate_all(repo, "demo-project")
    proposal_a, policy_a = outcomes[0].proposal, outcomes[0].policy
    classification = classify_risk(
        proposed_scope=proposal_a.proposed_scope, success_criteria=proposal_a.success_criteria
    )
    node_stale = materialize_work_node(
        proposal_a, classification, base_pin=main, surface_id=f"{proposal_a.project_id}-stale"
    )
    proposal_current = proposal_a.model_copy(update={"origination_identity": "4" * 64})
    node_current = materialize_work_node(
        proposal_current,
        classification,
        base_pin=main,
        surface_id=f"{proposal_a.project_id}-current",
    )
    persist_proposed(store, proposal_a, policy_a)
    persist_proposed(store, proposal_current, policy_a)

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []
    results: dict[str, object] = {}

    def _race(label: str, identity: str, node: object, current: bool) -> None:
        try:
            barrier.wait()
            try:
                outcome = reconcile_revision(
                    store,
                    origination_identity=identity,
                    package_id=proposal_a.work_id,
                    work_node=node,  # type: ignore[arg-type]
                    still_current=lambda c=current: c,
                )
                results[label] = ("ok", outcome)
            except OriginationProjectionError as exc:
                results[label] = ("denied", exc.code)
        except BaseException as exc:
            errors.append(exc)

    t_stale = threading.Thread(
        target=_race, args=("stale", proposal_a.origination_identity, node_stale, False)
    )
    t_current = threading.Thread(
        target=_race,
        args=("current", proposal_current.origination_identity, node_current, True),
    )
    t_stale.start()
    t_current.start()
    t_stale.join(timeout=10)
    t_current.join(timeout=10)
    assert not t_stale.is_alive()
    assert not t_current.is_alive()
    assert errors == []

    # The stale side must always be denied -- regardless of which thread
    # the OS scheduled first, `still_current` is checked fresh for each
    # call, immediately before that call's own write, inside the lock.
    assert results["stale"] == ("denied", "STALE_SOURCE_SNAPSHOT")
    assert results["current"][0] == "ok"

    active = list_materialized_work_nodes(store)
    assert len(active) == 1
    assert active[0].mutation_surface.surface_id == f"{proposal_a.project_id}-current"
