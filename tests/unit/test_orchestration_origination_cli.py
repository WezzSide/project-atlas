"""D-PHASE2A-3: ``orchestration.origination.cli.run_origination_scan()``.

Mirrors ``test_orchestration_autonomy_rehydration.py``'s real-git-repo
fixture pattern (a real, self-contained repo with a faked
``origin/main`` remote-tracking ref, no network access required) and
``test_orchestration_origination.py``'s roadmap-writing fixture pattern
-- this module is the one place both are needed together, since
``run_origination_scan()`` is the first origination entry point that
also consults live git inventory for ``base_pin``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

import pytest

from project_atlas.orchestration.autonomy.models import (
    CANONICAL_REPOSITORY_IDENTITY,
    AdvancementReason,
    ExecutionHostClass,
    NodeState,
    OwnerGateKind,
    TrustedAnchorRecord,
)
from project_atlas.orchestration.autonomy.trust import seal_anchor
from project_atlas.orchestration.origination.cli import EXIT_ERROR, EXIT_OK, run_origination_scan
from project_atlas.orchestration.origination.projection import load_projection


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


def _anchor(main: str, tree: str) -> TrustedAnchorRecord:
    return seal_anchor(
        TrustedAnchorRecord(
            repository_identity=CANONICAL_REPOSITORY_IDENTITY,
            trusted_main=main,
            trusted_tree=tree,
            predecessor_main="a" * 40,
            predecessor_tree="b" * 40,
            advancement_reason=AdvancementReason.VERIFIED_OWNER_AUTHORIZED_MERGE,
            source_package="AS-ORIGIN-001-TEST",
            source_directive="D-PHASE2A-3-TEST-FIXTURE",
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
            evidence_reference="tests/fixtures/d-phase2a-3.json",
            evidence_digest="cd" * 32,
            sequence=1,
            record_digest="00" * 32,
        )
    )


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


def _eligible_repo(tmp_path: Path) -> Path:
    repo = _make_repo(tmp_path)
    _write_plain_file(repo, "docs/REQUIREMENTS.md", "# Requirements\nFR-1: do the thing.\n")
    _write_skipped_test(repo, "tests/test_feature_x.py")
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
    return repo


def test_scan_materializes_a_ready_o1_proposal(tmp_path: Path) -> None:
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    store = tmp_path / "origination-store"

    payload, exit_code = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=store,
        explicit_trusted=_anchor(main, tree),
    )

    assert exit_code == EXIT_OK
    assert payload["eligible_count"] == 1
    assert payload["materialized_count"] == 1
    assert payload["not_materialized_count"] == 0
    assert payload["merge_authorized"] is False
    assert payload["execution_authorized"] is False
    materialized = payload["materialized"]
    assert isinstance(materialized, list)
    entry = materialized[0]
    assert entry["execution_ready"] is True
    assert entry["reason"] == "READY"
    assert entry["risk_class"] == "O1_LOW_RISK_SPECIFICATION_BOUND_IMPLEMENTATION"
    assert entry["owner_gate"] is None

    # Durably persisted -- a later reader can find the exact node this
    # scan materialized, not just the in-memory payload.
    projection = load_projection(store)
    assert len(projection.records) == 1
    record = projection.records[0]
    assert record.state == "MATERIALIZED"
    assert record.work_node is not None
    assert record.work_node["package_id"] == entry["work_id"]
    assert record.work_node["execution_host_class"] == ExecutionHostClass.IN_PROCESS.value
    assert record.work_node["state"] == NodeState.DISCOVERED.value


def test_acceptance_contract_scope_change_supersedes_stale_authority_same_roadmap_bytes(
    tmp_path: Path,
) -> None:
    """Owner directive D-ATLAS-AUTHORITY-SNAPSHOT-CONVERGENCE (P1 finding,
    chatgpt-codex-connector, PR #678): a WorkNode's authority-bearing
    scope can change while the roadmap item's own bytes stay identical --
    an attached acceptance contract can be edited independently of
    ``docs/ROADMAP.md``. Before this fix, ``origination_identity`` hashed
    only the roadmap item's own content digest, so a contract-only edit
    left it unchanged; ``run_origination_scan()`` then hit the
    already-materialized "report AS-IS" shortcut (``cli.py``) for the
    SAME identity and never even reached ``reconcile_revision()`` /
    ``still_current`` -- the OLD contract's ``proposed_scope`` stayed the
    durable, rehydratable authority forever, regardless of what the
    project's own acceptance contract said NOW.

    Real, production-path reproduction: two scans through
    ``run_origination_scan()`` (never a manual API sequence), same
    roadmap bytes throughout, only ``docs/acceptance-contracts.yaml``'s
    ``proposed_scope`` changes (S1 -> S2) between them.
    """
    repo = _eligible_repo(tmp_path)
    _write_plain_file(
        repo,
        "docs/acceptance-contracts.yaml",
        "contracts:\n"
        "  - item_id: feature-x\n"
        "    source_path: docs/ROADMAP.md\n"
        "    evidence: [docs/REQUIREMENTS.md]\n"
        "    proposed_scope: [src/thing.py]\n"
        "    success_criteria: ['K1: thing works']\n",
    )
    _write_plain_file(
        repo,
        ".atlas-project.yaml",
        "schema_version: 1\n"
        "project:\n"
        "  id: demo-project\n"
        "origination_acceptance_contracts: docs/acceptance-contracts.yaml\n",
    )
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    store = tmp_path / "origination-store"

    # Revision A: contract C1 (proposed_scope=[src/thing.py]).
    payload_a, exit_a = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=store,
        explicit_trusted=_anchor(main, tree),
    )
    assert exit_a == EXIT_OK
    assert payload_a["materialized_count"] == 1
    entry_a = cast(list[dict[str, object]], payload_a["materialized"])[0]
    assert entry_a["already_materialized"] is False
    work_id = entry_a["work_id"]

    projection_a = load_projection(store)
    assert len(projection_a.records) == 1
    record_a = projection_a.records[0]
    assert record_a.state == "MATERIALIZED"
    assert record_a.proposal["proposed_scope"] == ["src/thing.py"]
    identity_a = record_a.origination_identity

    # Revision B: SAME roadmap bytes, contract widens to C2
    # (proposed_scope S1 -> S2). No commit, no ROADMAP.md edit -- the
    # ONLY authority-bearing input that changes is the contract.
    _write_plain_file(
        repo,
        "docs/acceptance-contracts.yaml",
        "contracts:\n"
        "  - item_id: feature-x\n"
        "    source_path: docs/ROADMAP.md\n"
        "    evidence: [docs/REQUIREMENTS.md]\n"
        "    proposed_scope: [src/thing.py, src/extra.py]\n"
        "    success_criteria: ['K1: thing works']\n",
    )

    payload_b, exit_b = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=store,
        explicit_trusted=_anchor(main, tree),
    )
    assert exit_b == EXIT_OK
    assert payload_b["materialized_count"] == 1
    entry_b = cast(list[dict[str, object]], payload_b["materialized"])[0]

    # The stale (A) authority must NOT be what this scan reports: a new
    # revision materialized fresh, under the SAME logical work_id, and
    # explicitly superseded the old one -- never silently reused it.
    assert entry_b["work_id"] == work_id
    assert entry_b["already_materialized"] is False
    assert entry_b["superseded_prior_revisions"] == [identity_a]

    projection_b = load_projection(store)
    by_identity = {row.origination_identity: row for row in projection_b.records}
    assert len(by_identity) == 2
    assert by_identity[identity_a].state == "SUPERSEDED"
    # The superseded row's own frozen work_node is preserved exactly as
    # it was materialized -- history, not silently rewritten.
    assert by_identity[identity_a].proposal["proposed_scope"] == ["src/thing.py"]

    identity_b = next(k for k in by_identity if k != identity_a)
    assert by_identity[identity_b].state == "MATERIALIZED"
    assert by_identity[identity_b].proposal["proposed_scope"] == ["src/thing.py", "src/extra.py"]
    assert by_identity[identity_b].work_node is not None
    assert by_identity[identity_b].work_node["package_id"] == work_id


def test_scan_with_no_eligible_work_is_a_clean_empty_result(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")

    payload, exit_code = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=tmp_path / "origination-store",
        explicit_trusted=_anchor(main, tree),
    )

    assert exit_code == EXIT_OK
    assert payload["eligible_count"] == 0
    assert payload["materialized_count"] == 0
    assert payload["not_materialized_count"] == 0
    assert payload["materialized"] == []
    assert payload["not_materialized"] == []


def test_scan_never_raises_for_a_malformed_acceptance_contract(tmp_path: Path) -> None:
    """IV finding (PR #663 review, P1): run_origination_scan()'s own
    documented "never raises" contract did not catch
    AcceptanceContractConfigError -- a malformed contract escaped as an
    uncaught exception instead of the function's own fail-closed
    payload, exactly like every other configuration failure it already
    handles."""
    repo = _make_repo(tmp_path)
    _write_plain_file(repo, "docs/backlog.md", "- [ ] AAA-001 Real outstanding work\n")
    _write_plain_file(
        repo,
        "docs/acceptance-contracts.yaml",
        "contracts:\n"
        "  - item_id: AAA-001\n"
        "    source_path: docs/backlog.md\n"
        "    evidence: [docs/backlog.md]\n"
        "    proposed_scope: [src/thing.py]\n"
        "    success_criteria: []\n",  # empty -- schema-invalid
    )
    _write_plain_file(
        repo,
        ".atlas-project.yaml",
        "schema_version: 1\n"
        "project:\n"
        "  id: demo-project\n"
        "origination_sources:\n"
        "  - path: docs/backlog.md\n"
        "    format: markdown-task-list\n"
        "origination_acceptance_contracts: docs/acceptance-contracts.yaml\n",
    )
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")

    payload, exit_code = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=tmp_path / "origination-store",
        explicit_trusted=_anchor(main, tree),
    )

    assert exit_code == EXIT_ERROR
    assert payload["blocker"] == "ACCEPTANCE_CONTRACT_CONFIG_INVALID"
    assert payload["merge_authorized"] is False
    assert payload["execution_authorized"] is False


def test_second_scan_does_not_re_materialize_already_terminal_work(tmp_path: Path) -> None:
    """The correct successor-scan semantic (`originate_new_only`, not
    `originate_all`): once a package's origination_identity is durably
    TERMINAL, a later scan must not re-propose or re-materialize it --
    matching NO_DUPLICATE_ORIGINATION / RESTART_REPLAY."""
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    store = tmp_path / "origination-store"
    anchor = _anchor(main, tree)

    first, _ = run_origination_scan(
        root=repo, project_id="demo-project", origination_store=store, explicit_trusted=anchor
    )
    assert first["materialized_count"] == 1
    identity = cast("list[dict[str, object]]", first["materialized"])[0]["work_id"]

    from project_atlas.orchestration.origination.projection import mark_terminal

    projection = load_projection(store)
    record = projection.records[0]
    mark_terminal(store, record.origination_identity, node_state="CLOSED")

    second, exit_code = run_origination_scan(
        root=repo, project_id="demo-project", origination_store=store, explicit_trusted=anchor
    )
    assert exit_code == EXIT_OK
    assert second["eligible_count"] == 0
    assert second["materialized_count"] == 0
    # Durable record is untouched, not duplicated.
    assert len(load_projection(store).records) == 1
    del identity  # only needed to document what was closed


def test_scan_fails_closed_on_unsafe_project_id(tmp_path: Path) -> None:
    """Independent-IV finding (PR #647 round 1): an unsafe project_id
    (arbitrary characters, never validated in cli.py itself) used to
    escape run_origination_scan() as an uncaught pydantic.ValidationError
    from deep inside originate_new_only() -> SourceFact construction --
    breaking the function's own "never raises" contract. Must now be
    rejected at the top, before any downstream call, as a clean
    fail-closed payload."""
    repo = _make_repo(tmp_path)
    payload, exit_code = run_origination_scan(
        root=repo,
        project_id="bad project id!",
        origination_store=tmp_path / "origination-store",
        explicit_trusted=_anchor(
            _run_git(repo, "rev-parse", "origin/main"),
            _run_git(repo, "rev-parse", "origin/main^{tree}"),
        ),
    )
    assert exit_code == EXIT_ERROR
    assert payload["blocker"] == "INVALID_PROJECT_ID"
    assert payload["merge_authorized"] is False
    assert payload["execution_authorized"] is False
    # The scan never ran at all -- no store, no partial state.
    assert not (tmp_path / "origination-store").exists()


def test_scan_fails_closed_on_malformed_origination_source_config(tmp_path: Path) -> None:
    """PR-A review finding (chatgpt-codex-connector, P2): a project's own
    malformed ``origination_sources`` declaration used to escape
    ``run_origination_scan()`` as an uncaught ``OriginationSourceConfigError``
    -- breaking the function's documented "never raises" contract. Must
    come back as a clean fail-closed payload instead."""
    repo = _make_repo(tmp_path)
    (repo / ".atlas-project.yaml").write_text(
        "schema_version: 1\nproject:\n  id: fixture-proj\norigination_sources: not-a-list\n",
        encoding="utf-8",
    )
    payload, exit_code = run_origination_scan(
        root=repo,
        project_id="fixture-proj",
        origination_store=tmp_path / "origination-store",
        explicit_trusted=_anchor(
            _run_git(repo, "rev-parse", "origin/main"),
            _run_git(repo, "rev-parse", "origin/main^{tree}"),
        ),
    )
    assert exit_code == EXIT_ERROR
    assert payload["blocker"] == "ORIGINATION_SOURCE_CONFIG_INVALID"
    assert payload["merge_authorized"] is False
    assert payload["execution_authorized"] is False


def test_scan_fails_closed_on_cross_source_duplicate_item_id(tmp_path: Path) -> None:
    """PR-A review finding (chatgpt-codex-connector, P2): the same stable
    item_id declared authoritative by two different origination sources
    used to escape as an uncaught ``DuplicateItemIdError``. Must come
    back as a clean fail-closed payload instead."""
    repo = _make_repo(tmp_path)
    (repo / "docs").mkdir(parents=True, exist_ok=True)
    (repo / "docs" / "a.md").write_text("- [ ] AAA-001 From a\n", encoding="utf-8")
    (repo / "docs" / "b.md").write_text("- [ ] AAA-001 From b\n", encoding="utf-8")
    (repo / ".atlas-project.yaml").write_text(
        "schema_version: 1\nproject:\n  id: fixture-proj\n"
        "origination_sources:\n"
        "  - path: docs/a.md\n    format: markdown-task-list\n"
        "  - path: docs/b.md\n    format: markdown-task-list\n",
        encoding="utf-8",
    )
    payload, exit_code = run_origination_scan(
        root=repo,
        project_id="fixture-proj",
        origination_store=tmp_path / "origination-store",
        explicit_trusted=_anchor(
            _run_git(repo, "rev-parse", "origin/main"),
            _run_git(repo, "rev-parse", "origin/main^{tree}"),
        ),
    )
    assert exit_code == EXIT_ERROR
    assert payload["blocker"] == "ORIGINATION_DUPLICATE_ITEM_ID"
    assert payload["merge_authorized"] is False
    assert payload["execution_authorized"] is False


def test_scan_fails_closed_on_project_id_that_would_overflow_surface_id(
    tmp_path: Path,
) -> None:
    """Independent-IV finding (PR #647 round 1): a project_id that is
    individually valid (safe characters, <=128 chars, same bound
    SourceFact.project_id itself allows) can still combine with the
    fixed-length "-{work_id}" suffix this module appends to overflow
    MutationSurface.surface_id's own 128-char cap -- raising a raw
    ValidationError from inside materialize_work_node(), not a
    MaterializationError, so it used to escape the existing
    `except MaterializationError` entirely. 107 chars is one past this
    module's own 106-char bound (128 - 1 separator - 21-char work_id)."""
    repo = _eligible_repo(tmp_path)
    long_project_id = "p" * 107
    payload, exit_code = run_origination_scan(
        root=repo,
        project_id=long_project_id,
        origination_store=tmp_path / "origination-store",
        explicit_trusted=_anchor(
            _run_git(repo, "rev-parse", "origin/main"),
            _run_git(repo, "rev-parse", "origin/main^{tree}"),
        ),
    )
    assert exit_code == EXIT_ERROR
    assert payload["blocker"] == "INVALID_PROJECT_ID"
    assert not (tmp_path / "origination-store").exists()


def test_scan_fails_closed_on_trust_error(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    payload, exit_code = run_origination_scan(
        root=repo,
        project_id="demo-project",
        trust_store=tmp_path / "does-not-exist",
        origination_store=tmp_path / "origination-store",
    )
    assert exit_code == EXIT_ERROR
    assert payload["merge_authorized"] is False
    assert payload["execution_authorized"] is False
    assert "blocker" in payload
    # No projection store was ever created -- the scan never ran.
    assert not (tmp_path / "origination-store").exists()


def test_owner_held_proposal_is_still_materialized_but_owner_gated(tmp_path: Path) -> None:
    """materialize_work_node()'s own contract: OWNER_HELD risk-classified
    proposals are still materialized (not silently dropped), just routed
    to an owner_gate rather than reported execution_ready. The scan must
    surface this distinction, not collapse it into "not materialized".

    proposed_scope is derived from the roadmap item's own `evidence`
    paths (pipeline.py's `_proposed_scope`), not a separate declared
    field -- so an evidence path that itself touches a disqualifying
    fragment (risk.py's `_DISQUALIFYING_PATH_FRAGMENTS`, "migrations/"
    here) is what forces OWNER_HELD, mirrored here rather than
    duplicating risk.py's own list.
    """
    repo = _make_repo(tmp_path)
    _write_plain_file(repo, "docs/REQUIREMENTS.md", "# Requirements\nFR-1: do the risky thing.\n")
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

    payload, exit_code = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=tmp_path / "origination-store",
        explicit_trusted=_anchor(main, tree),
    )
    assert exit_code == EXIT_OK
    # The two buckets combined must equal the eligible count -- whichever
    # bucket it lands in, it must be reported somewhere, not silently
    # vanish.
    total = cast(int, payload["materialized_count"]) + cast(int, payload["not_materialized_count"])
    assert total == payload["eligible_count"] == 1
    # Independent-IV note (PR #647 round 1): asserting the specific
    # outcome directly, not defensively branching on which bucket it
    # landed in -- materialize_work_node()'s own documented contract is
    # that OWNER_HELD nodes ARE materialized, just gated, so this is the
    # one real, non-dead-code outcome for this fixture.
    assert payload["materialized_count"] == 1
    assert payload["not_materialized_count"] == 0
    entry = cast("list[dict[str, object]]", payload["materialized"])[0]
    assert entry["execution_ready"] is False
    assert entry["owner_gate"] in {kind.value for kind in OwnerGateKind}


def test_second_scan_of_still_in_progress_work_does_not_clobber_durable_node(
    tmp_path: Path,
) -> None:
    """D-PHASE2A-2 finding: `originate_new_only()` only excludes TERMINAL
    identities (by design -- see its own docstring), so a second scan
    against the SAME still-non-terminal evidence is a normal, expected
    occurrence once a live governed loop is actually discovering/leasing
    from this projection repeatedly, not an error case. The scan must
    NOT rebuild and overwrite the durable `work_node` in that situation:
    `rehydration.py`'s `find_materialized_work_node()` is the exact
    function a crashed-and-restarted process uses to reconstruct an
    ALREADY-LEASED node from this same durable record -- if a second
    scan clobbered it with a freshly-rebuilt WorkNode (state=DISCOVERED,
    a different base_pin if main moved since), that reconstruction would
    silently diverge from the real governed state the first process's
    lease was actually granted against.

    Proven concretely: advance the repo's `origin/main` between the two
    scans (so a rebuilt WorkNode would carry a different `base_pin` if
    the bug were still present), and assert the durable `work_node` is
    byte-identical after the second scan.
    """
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    store = tmp_path / "origination-store"

    first, _ = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=store,
        explicit_trusted=_anchor(main, tree),
    )
    assert first["materialized_count"] == 1
    first_entry = cast("list[dict[str, object]]", first["materialized"])[0]
    projection_after_first = load_projection(store)
    work_node_after_first = projection_after_first.records[0].work_node
    assert work_node_after_first is not None
    assert work_node_after_first["base_pin"] == main
    assert work_node_after_first["state"] == NodeState.DISCOVERED.value

    # Advance origin/main -- a rebuilt WorkNode would now carry a
    # DIFFERENT base_pin than the durably-recorded one, if the bug were
    # still present.
    _write_plain_file(repo, "docs/UNRELATED.md", "unrelated change\n")
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "advance main")
    new_sha = _run_git(repo, "rev-parse", "HEAD")
    _run_git(repo, "update-ref", "refs/remotes/origin/main", new_sha)
    assert new_sha != main

    new_main = _run_git(repo, "rev-parse", "origin/main")
    new_tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    second, exit_code = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=store,
        explicit_trusted=_anchor(new_main, new_tree),
    )

    assert exit_code == EXIT_OK
    assert second["eligible_count"] == 1  # not TERMINAL yet -- still "eligible"
    assert second["materialized_count"] == 1
    second_entry = cast("list[dict[str, object]]", second["materialized"])[0]
    assert second_entry["work_id"] == first_entry["work_id"]

    # The durable record itself is untouched: still exactly one record,
    # same base_pin as the FIRST scan (never rebuilt against the new
    # main), still DISCOVERED (never silently reset from whatever a real
    # governor would have advanced it to). This is the actual bug this
    # test exists to catch -- checked before the `already_materialized`
    # flag below so the failure surfaces as a real clobbered-base_pin
    # mismatch, not an incidental missing-key error on old code that
    # never had that flag at all.
    projection_after_second = load_projection(store)
    assert len(projection_after_second.records) == 1
    work_node_after_second = projection_after_second.records[0].work_node
    assert work_node_after_second == work_node_after_first
    assert work_node_after_second is not None
    assert work_node_after_second["base_pin"] == main
    assert work_node_after_second["base_pin"] != new_main

    assert second_entry["already_materialized"] is True
    assert first_entry["already_materialized"] is False


def test_content_revision_while_prior_work_is_active_supersedes_it_and_materializes_the_new_one(
    tmp_path: Path,
) -> None:
    """AS-ORIGIN-MATERIALIZED-SUPERSESSION-001 (owner directive
    D-ATLAS-ORIGINATION-MATERIALIZED-REVISION-SUPERSESSION §5 Case C):
    `origination_identity` hashes the item's content digest (`identity.py`)
    and therefore changes when a roadmap item's own content is revised,
    but `package_id` (`work_id_for()`) hashes only `project_id + item_id`
    and stays IDENTICAL across such a revision. Revising the SAME item's
    title between two scans -- while the first scan's active record for it
    is still MATERIALIZED (not TERMINAL/SUPERSEDED) -- must SUPERSEDE the
    prior record (never delete it -- historical evidence preserved) and
    materialize the new, still fully-eligible revision as the package_id's
    new current active revision. This replaces the old refuse-only
    ``PACKAGE_ID_ALREADY_ACTIVE`` behavior (superseded, formerly this same
    test asserted the opposite: that the old record stayed "completely
    untouched" and nothing new materialized -- that WAS the bug this
    package fixes).
    """
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    store = tmp_path / "origination-store"

    first, _ = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=store,
        explicit_trusted=_anchor(main, tree),
    )
    assert first["materialized_count"] == 1
    first_entry = cast("list[dict[str, object]]", first["materialized"])[0]
    first_identity = load_projection(store).records[0].origination_identity

    # Revise the SAME item ("id" unchanged -> same package_id) with
    # different content ("title" changed -> different item_digest ->
    # different origination_identity), while the first scan's record for
    # it is still MATERIALIZED (not TERMINAL/SUPERSEDED). The revision
    # itself declares no blockers -- still fully eligible (Case C).
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

    second, exit_code = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=store,
        explicit_trusted=_anchor(new_main, new_tree),
    )
    assert exit_code == EXIT_OK
    assert second["eligible_count"] == 1
    # The new revision materializes -- superseding, not refused behind,
    # the prior one.
    assert second["materialized_count"] == 1
    assert second["not_materialized_count"] == 0
    second_entry = cast("list[dict[str, object]]", second["materialized"])[0]
    assert second_entry["work_id"] == first_entry["work_id"]
    assert second_entry["superseded_prior_revisions"] == [first_identity]

    projection = load_projection(store)
    assert len(projection.records) == 2
    a_record = next(r for r in projection.records if r.origination_identity == first_identity)
    b_record = next(r for r in projection.records if r.origination_identity != first_identity)

    # A (the original) is preserved as historical evidence, never deleted
    # or rewritten as though never materialized -- only its `state`
    # changed.
    assert a_record.state == "SUPERSEDED"
    assert a_record.work_node is not None
    assert a_record.work_node["base_pin"] == main
    assert a_record.proposal["title"] == "Feature X"

    # B (the revision) is the new, sole CURRENT active revision.
    assert b_record.state == "MATERIALIZED"
    assert b_record.work_node is not None
    assert b_record.work_node["base_pin"] == new_main
    assert b_record.work_node["base_pin"] != main
    assert b_record.proposal["title"] == "Feature X (revised)"

    # Exactly one CURRENT active record for this package_id -- A is
    # excluded (SUPERSEDED), matching list_materialized_work_nodes()'s
    # own definition of "active".
    active_for_package = [
        row
        for row in projection.records
        if row.state not in {"TERMINAL", "SUPERSEDED"}
        and row.work_node is not None
        and row.work_node.get("package_id") == first_entry["work_id"]
    ]
    assert len(active_for_package) == 1
    assert active_for_package[0].origination_identity != first_identity


def test_content_revision_that_becomes_blocked_supersedes_the_prior_revision_without_materializing(
    tmp_path: Path,
) -> None:
    """AS-ORIGIN-MATERIALIZED-SUPERSESSION-001 (owner directive
    D-ATLAS-ORIGINATION-MATERIALIZED-REVISION-SUPERSESSION §5 Case B,
    mirroring the real INT-013 incident this package exists to fix): a
    content revision that makes the SAME logical item newly BLOCKED must
    still revoke the prior, now-stale MATERIALIZED revision's durable
    rehydratability -- source truth supersedes regardless of whether the
    new revision itself clears the materialization bar. Nothing new
    materializes (the new revision is blocked), but the OLD revision must
    not be left durably active/rehydratable either.
    """
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    store = tmp_path / "origination-store"

    first, _ = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=store,
        explicit_trusted=_anchor(main, tree),
    )
    assert first["materialized_count"] == 1
    first_identity = load_projection(store).records[0].origination_identity

    # Revise the SAME item to declare an explicit blocker -- the real
    # INT-013 shape: authoritative source truth changes to
    # EXTERNAL_BLOCKED while a prior, now-stale revision is still
    # MATERIALIZED.
    _write_roadmap(
        repo,
        [
            {
                "id": "feature-x",
                "title": "Feature X",
                "status": "NOT_STARTED",
                "lifecycle": "READY",
                "evidence": ["docs/REQUIREMENTS.md", "tests/test_feature_x.py"],
                "blockers": ["EXTERNAL_BLOCKED: needs owner-provided authentic project roots"],
            }
        ],
    )
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "declare feature-x EXTERNAL_BLOCKED")
    new_sha = _run_git(repo, "rev-parse", "HEAD")
    _run_git(repo, "update-ref", "refs/remotes/origin/main", new_sha)
    new_main = _run_git(repo, "rev-parse", "origin/main")
    new_tree = _run_git(repo, "rev-parse", "origin/main^{tree}")

    second, exit_code = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=store,
        explicit_trusted=_anchor(new_main, new_tree),
    )
    assert exit_code == EXIT_OK
    assert second["eligible_count"] == 1
    assert second["materialized_count"] == 0
    assert second["not_materialized_count"] == 1
    second_entry = cast("list[dict[str, object]]", second["not_materialized"])[0]
    assert second_entry["materialization_error_code"] == "PROPOSAL_BLOCKED"
    assert second_entry["execution_ready"] is False
    assert second_entry["superseded_prior_revisions"] == [first_identity]

    projection = load_projection(store)
    assert len(projection.records) == 2
    a_record = next(r for r in projection.records if r.origination_identity == first_identity)
    b_record = next(r for r in projection.records if r.origination_identity != first_identity)

    # A: preserved as historical evidence, transitioned to SUPERSEDED --
    # never deleted, never silently left MATERIALIZED.
    assert a_record.state == "SUPERSEDED"
    assert a_record.work_node is not None
    assert a_record.work_node["base_pin"] == main

    # B: honestly recorded as PROPOSED (blocked, never materialized) --
    # not silently discarded, not fabricated as materialized either.
    assert b_record.state == "PROPOSED"
    assert b_record.work_node is None

    # No CURRENT active record for this package_id at all now -- the
    # item is genuinely, machine-visibly not leaseable.
    from project_atlas.orchestration.origination.projection import list_materialized_work_nodes

    active_nodes = list_materialized_work_nodes(store)
    assert not any(node.package_id == a_record.work_node["package_id"] for node in active_nodes)

    # §17 (owner directive): re-confirm this is NOT an artifact of
    # base_pin having gone stale -- A's own base_pin is STILL exactly
    # live main's OLD value at the time it was superseded, and critically
    # the supersession happened without base_pin ever being consulted at
    # all (reconcile_revision() never reads base_pin). Prove it directly:
    # A is excluded from list_materialized_work_nodes() even though
    # nothing here ever compared any base_pin.
    assert a_record.work_node["base_pin"] == main  # unchanged, frozen historical fact


def test_reverse_transition_blocker_removed_lets_a_later_revision_materialize(
    tmp_path: Path,
) -> None:
    """Owner directive §10 (Reverse Transition Test): a blocked revision
    followed by a legitimate blocker-removal must still let the newer,
    now-eligible revision materialize -- this package's mechanism is
    revision RECONCILIATION, not permanent tombstoning of a work_id once
    any one of its revisions is ever blocked.

    Chain: A (READY, materializes) -> B (BLOCKED, supersedes A, itself
    never materializes) -> C (blocker legitimately removed, READY again,
    materializes -- superseding whatever is currently active, which by
    this point is nothing, since B never held authority).
    """
    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    store = tmp_path / "origination-store"

    a_result, _ = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=store,
        explicit_trusted=_anchor(main, tree),
    )
    assert a_result["materialized_count"] == 1
    a_identity = load_projection(store).records[0].origination_identity

    _write_roadmap(
        repo,
        [
            {
                "id": "feature-x",
                "title": "Feature X",
                "status": "NOT_STARTED",
                "lifecycle": "READY",
                "evidence": ["docs/REQUIREMENTS.md", "tests/test_feature_x.py"],
                "blockers": ["EXTERNAL_BLOCKED: needs owner data"],
            }
        ],
    )
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "block feature-x")
    b_sha = _run_git(repo, "rev-parse", "HEAD")
    _run_git(repo, "update-ref", "refs/remotes/origin/main", b_sha)
    b_main = _run_git(repo, "rev-parse", "origin/main")
    b_tree = _run_git(repo, "rev-parse", "origin/main^{tree}")

    b_result, _ = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=store,
        explicit_trusted=_anchor(b_main, b_tree),
    )
    assert b_result["materialized_count"] == 0
    assert b_result["not_materialized_count"] == 1
    b_identity = next(
        r.origination_identity
        for r in load_projection(store).records
        if r.origination_identity != a_identity
    )

    # Blocker legitimately removed -- a THIRD, distinct revision (title
    # unchanged from B's content? No -- must differ from BOTH A and B to
    # get its own origination_identity; drop the blocker to get back to
    # content identical to A's own original text would collide with A's
    # identity, which is a separate, deliberately out-of-scope edge case
    # -- use a distinct title so C is unambiguously its own revision).
    _write_roadmap(
        repo,
        [
            {
                "id": "feature-x",
                "title": "Feature X (unblocked)",
                "status": "NOT_STARTED",
                "lifecycle": "READY",
                "evidence": ["docs/REQUIREMENTS.md", "tests/test_feature_x.py"],
            }
        ],
    )
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "unblock feature-x")
    c_sha = _run_git(repo, "rev-parse", "HEAD")
    _run_git(repo, "update-ref", "refs/remotes/origin/main", c_sha)
    c_main = _run_git(repo, "rev-parse", "origin/main")
    c_tree = _run_git(repo, "rev-parse", "origin/main^{tree}")

    c_result, exit_code = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=store,
        explicit_trusted=_anchor(c_main, c_tree),
    )
    assert exit_code == EXIT_OK
    assert c_result["materialized_count"] == 1
    c_entry = cast("list[dict[str, object]]", c_result["materialized"])[0]
    # C did not need to supersede anything -- B never held authority.
    assert c_entry["superseded_prior_revisions"] == []

    projection = load_projection(store)
    assert len(projection.records) == 3
    a_record = next(r for r in projection.records if r.origination_identity == a_identity)
    b_record = next(r for r in projection.records if r.origination_identity == b_identity)
    c_record = next(
        r for r in projection.records if r.origination_identity not in {a_identity, b_identity}
    )

    # A: historical, superseded when B first revoked it.
    assert a_record.state == "SUPERSEDED"
    # B: historical, blocked revision, retained (never materialized, never
    # deleted) -- proves "blocked historical revision retained".
    assert b_record.state == "PROPOSED"
    assert b_record.work_node is None
    # C: the sole current active revision.
    assert c_record.state == "MATERIALIZED"
    assert c_record.work_node is not None
    assert c_record.work_node["base_pin"] == c_main

    from project_atlas.orchestration.origination.projection import list_materialized_work_nodes

    active_nodes = list_materialized_work_nodes(store)
    assert len(active_nodes) == 1
    assert active_nodes[0].package_id == c_entry["work_id"]
    assert active_nodes[0].base_pin == c_main


def test_multiple_revisions_maintain_at_most_one_current_active_revision_throughout(
    tmp_path: Path,
) -> None:
    """Owner directive §11 (Multiple Edits): revision A (READY) -> B
    (BLOCKED) -> C (READY) -> D (BLOCKED). One lineage, same work_id,
    chronological revisions; at every point at most one current eligible
    MATERIALIZED revision; historical revisions remain inspectable; no
    resurrection of an earlier revision once a later one supersedes it.
    """
    repo = _eligible_repo(tmp_path)
    store = tmp_path / "origination-store"

    def _scan_at_current_main() -> tuple[dict[str, object], str, str]:
        main = _run_git(repo, "rev-parse", "origin/main")
        tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
        result, exit_code = run_origination_scan(
            root=repo,
            project_id="demo-project",
            origination_store=store,
            explicit_trusted=_anchor(main, tree),
        )
        assert exit_code == EXIT_OK
        return result, main, tree

    def _commit_roadmap(title: str, *, blocked: bool) -> None:
        item: dict[str, object] = {
            "id": "feature-x",
            "title": title,
            "status": "NOT_STARTED",
            "lifecycle": "READY",
            "evidence": ["docs/REQUIREMENTS.md", "tests/test_feature_x.py"],
        }
        if blocked:
            item["blockers"] = ["EXTERNAL_BLOCKED: needs owner data"]
        _write_roadmap(repo, [item])
        _run_git(repo, "add", "-A")
        _run_git(repo, "commit", "-q", "-m", f"revise: {title}")
        sha = _run_git(repo, "rev-parse", "HEAD")
        _run_git(repo, "update-ref", "refs/remotes/origin/main", sha)

    def _active_package_ids() -> list[str]:
        from project_atlas.orchestration.origination.projection import (
            list_materialized_work_nodes,
        )

        return [node.package_id for node in list_materialized_work_nodes(store)]

    a_result, _, _ = _scan_at_current_main()
    assert a_result["materialized_count"] == 1
    a_identity = load_projection(store).records[0].origination_identity
    assert len(_active_package_ids()) == 1

    _commit_roadmap("Feature X (B, blocked)", blocked=True)
    b_result, _, _ = _scan_at_current_main()
    assert b_result["materialized_count"] == 0
    assert len(_active_package_ids()) == 0  # A superseded, B never materialized

    _commit_roadmap("Feature X (C, ready again)", blocked=False)
    c_result, _, _ = _scan_at_current_main()
    assert c_result["materialized_count"] == 1
    assert len(_active_package_ids()) == 1
    c_identity = next(
        r.origination_identity for r in load_projection(store).records if r.state == "MATERIALIZED"
    )

    _commit_roadmap("Feature X (D, blocked again)", blocked=True)
    d_result, _, _ = _scan_at_current_main()
    assert d_result["materialized_count"] == 0
    assert len(_active_package_ids()) == 0  # C superseded, D never materialized

    projection = load_projection(store)
    assert len(projection.records) == 4  # A, B, C, D -- all four preserved, none deleted
    states_by_identity = {r.origination_identity: r.state for r in projection.records}
    assert states_by_identity[a_identity] == "SUPERSEDED"
    assert states_by_identity[c_identity] == "SUPERSEDED"
    # B and D: both blocked revisions, both stayed PROPOSED -- neither
    # ever held nor was ever granted execution authority to revoke.
    for identity, state in states_by_identity.items():
        if identity not in {a_identity, c_identity}:
            assert state == "PROPOSED"
    # No resurrection: A never becomes active again once D supersedes-
    # by-proxy the lineage.
    assert not any(node == a_identity for node in _active_package_ids())


def test_persist_materialized_if_no_active_conflict_closes_the_toctou_race(
    tmp_path: Path,
) -> None:
    """D-PHASE2A-2 delta-IV finding: the original guard was a two-step
    sequence -- an UNLOCKED `find_active_record_by_package_id()` check,
    followed by a SEPARATE `persist_materialized()` call under its own
    lock. That left a TOCTOU window open: two concurrent callers could
    both observe "no conflict" before either wrote, producing two live
    records sharing one `package_id`. `persist_materialized_if_no_
    active_conflict()` closes this by performing the check and the
    write inside ONE `ProjectIdentityLock` critical section.

    Proven here with two REAL threads racing to materialize two
    different `origination_identity` proposals that share one
    `package_id`, synchronized with a `Barrier` to maximize actual
    contention on the lock -- not merely two sequential calls, which a
    lock would trivially serialize regardless of whether the
    check-then-write sequence inside it was itself atomic.
    """
    import threading

    from project_atlas.orchestration.origination.materialize import materialize_work_node
    from project_atlas.orchestration.origination.pipeline import originate_all
    from project_atlas.orchestration.origination.projection import (
        persist_materialized_if_no_active_conflict,
        persist_proposed,
    )
    from project_atlas.orchestration.origination.risk import classify as classify_risk

    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    store = tmp_path / "origination-store"

    outcomes = originate_all(repo, "demo-project")
    assert len(outcomes) == 1
    proposal_a, policy_a = outcomes[0].proposal, outcomes[0].policy
    # A second, DIFFERENT origination_identity claiming the SAME
    # package_id (work_id) -- simulating a content revision, the exact
    # scenario the sibling tests above exercise through a real scan
    # sequence, constructed directly here so both proposals can be
    # persisted/materialized independently of scan ordering.
    proposal_b = proposal_a.model_copy(update={"origination_identity": "b" * 64})

    persist_proposed(store, proposal_a, policy_a)
    persist_proposed(store, proposal_b, policy_a)

    classification = classify_risk(
        proposed_scope=proposal_a.proposed_scope, success_criteria=proposal_a.success_criteria
    )
    node_a = materialize_work_node(
        proposal_a, classification, base_pin=main, surface_id=f"{proposal_a.project_id}-a"
    )
    node_b = materialize_work_node(
        proposal_b, classification, base_pin=main, surface_id=f"{proposal_b.project_id}-b"
    )
    assert node_a.package_id == node_b.package_id  # the shared package_id this test is about

    results: dict[str, tuple[object, object]] = {}
    barrier = threading.Barrier(2)

    def _race(identity: str, node: object) -> None:
        barrier.wait()
        results[identity] = persist_materialized_if_no_active_conflict(store, identity, node)  # type: ignore[arg-type]

    t1 = threading.Thread(target=_race, args=(proposal_a.origination_identity, node_a))
    t2 = threading.Thread(target=_race, args=(proposal_b.origination_identity, node_b))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert not t1.is_alive()
    assert not t2.is_alive()

    result_a = results[proposal_a.origination_identity]
    result_b = results[proposal_b.origination_identity]
    # Exactly one of the two succeeded (a non-None materialized record);
    # the other was correctly refused as a conflict -- never both, and
    # never neither.
    successes = [r for r in (result_a, result_b) if r[0] is not None]
    conflicts = [r for r in (result_a, result_b) if r[1] is not None]
    assert len(successes) == 1
    assert len(conflicts) == 1

    projection = load_projection(store)
    active_for_package = [
        row
        for row in projection.records
        if row.state != "TERMINAL"
        and row.work_node is not None
        and row.work_node.get("package_id") == node_a.package_id
    ]
    assert len(active_for_package) == 1


def test_persist_materialized_if_no_active_conflict_does_not_clobber_same_identity(
    tmp_path: Path,
) -> None:
    """Same-identity persist must not overwrite an already-MATERIALIZED
    work_node. The different-identity TOCTOU test above does not pin
    this: persist_materialized_if_no_active_conflict() only treated a
    *different* origination_identity as a package-id conflict, then
    unconditionally wrote work_node + state. A second call for the
    same identity -- including a concurrent scan that missed the CLI
    skip from a stale snapshot -- would replace the durable node
    find_materialized_work_node() uses to reconstruct a leased node.
    """
    from project_atlas.orchestration.origination.materialize import materialize_work_node
    from project_atlas.orchestration.origination.pipeline import originate_all
    from project_atlas.orchestration.origination.projection import (
        persist_materialized_if_no_active_conflict,
        persist_proposed,
    )
    from project_atlas.orchestration.origination.risk import classify as classify_risk

    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    store = tmp_path / "origination-store"

    outcomes = originate_all(repo, "demo-project")
    assert len(outcomes) == 1
    proposal, policy = outcomes[0].proposal, outcomes[0].policy
    persist_proposed(store, proposal, policy)

    classification = classify_risk(
        proposed_scope=proposal.proposed_scope, success_criteria=proposal.success_criteria
    )
    first_node = materialize_work_node(
        proposal, classification, base_pin=main, surface_id=f"{proposal.project_id}-first"
    )
    other_pin = "b" * 40
    assert other_pin != main
    second_node = materialize_work_node(
        proposal, classification, base_pin=other_pin, surface_id=f"{proposal.project_id}-second"
    )

    first_record, first_conflict = persist_materialized_if_no_active_conflict(
        store, proposal.origination_identity, first_node
    )
    assert first_conflict is None
    assert first_record is not None
    assert first_record.work_node is not None
    assert first_record.work_node["base_pin"] == main

    second_record, second_conflict = persist_materialized_if_no_active_conflict(
        store, proposal.origination_identity, second_node
    )
    assert second_conflict is None
    assert second_record is not None
    assert second_record.work_node == first_record.work_node
    assert second_record.work_node is not None
    assert second_record.work_node["base_pin"] == main
    assert second_record.work_node["base_pin"] != other_pin

    projection = load_projection(store)
    assert len(projection.records) == 1
    assert projection.records[0].work_node == first_record.work_node
    assert projection.records[0].state == "MATERIALIZED"


def test_scan_reports_truthful_already_materialized_on_toctou_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cursor Bugbot finding on PR #654 (Low), still applicable to
    ``reconcile_revision()``: ``run_origination_scan()``'s unlocked
    ``existing`` read (from ``persist_proposed()``) can still see
    ``PROPOSED`` for an identity a concurrent scan materializes an instant
    later. ``reconcile_revision()`` correctly reports that identity's own
    idempotent replay (proven directly against the primitive elsewhere),
    but this call must report the truth about what actually won, not the
    locally-rebuilt node's fields regardless of which node actually won --
    a lie about durable truth whenever this process lost the race.

    Simulates the race by monkeypatching the reconciliation call to return
    an already-durable record for a *different* base_pin than the one this
    process would have built, with ``already_current=True`` and no
    supersession -- exactly ``reconcile_revision()``'s real idempotent
    return shape when a concurrent writer won first.
    """
    import project_atlas.orchestration.origination.cli as cli_module
    from project_atlas.orchestration.origination.materialize import materialize_work_node
    from project_atlas.orchestration.origination.pipeline import originate_all
    from project_atlas.orchestration.origination.projection import (
        OriginationRecord,
        ReconciliationOutcome,
    )
    from project_atlas.orchestration.origination.risk import classify as classify_risk

    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    store = tmp_path / "origination-store"

    outcomes = originate_all(repo, "demo-project")
    proposal, policy = outcomes[0].proposal, outcomes[0].policy
    classification = classify_risk(
        proposed_scope=proposal.proposed_scope, success_criteria=proposal.success_criteria
    )
    winning_pin = "c" * 40
    assert winning_pin != main
    winning_node = materialize_work_node(
        proposal, classification, base_pin=winning_pin, surface_id=f"{proposal.project_id}-won"
    )
    winning_record = OriginationRecord(
        origination_identity=proposal.origination_identity,
        project_id=proposal.project_id,
        proposal=proposal.model_dump(mode="json"),
        policy_result=policy.model_dump(mode="json"),
        work_node=winning_node.model_dump(mode="json"),
        state="MATERIALIZED",
    )

    def _fake_reconcile_revision(
        _store: Path,
        *,
        origination_identity: str,
        package_id: str,
        work_node: object,
        state: str = "MATERIALIZED",
        still_current: object = None,
    ) -> ReconciliationOutcome:
        return ReconciliationOutcome(
            superseded=(), materialized=winning_record, already_current=True
        )

    monkeypatch.setattr(cli_module, "reconcile_revision", _fake_reconcile_revision)

    payload, exit_code = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=store,
        explicit_trusted=_anchor(main, tree),
    )

    assert exit_code == EXIT_OK
    materialized = payload["materialized"]
    assert isinstance(materialized, list)
    assert len(materialized) == 1
    entry = materialized[0]
    # Truthful: this process lost the race, nothing it built was written.
    assert entry["already_materialized"] is True
    # Reports the durable winner's identity, not the locally-rebuilt loser.
    assert entry["work_id"] == winning_node.package_id


def test_scan_isolates_a_corrupt_durable_record_to_one_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Independent-verification finding (delta round on PR #654): the new
    ``WorkNode.model_validate(materialized_record.work_node)`` call this
    fix added must follow the same per-item isolation the sibling
    already-known-identity branch above it already has (a corrupt durable
    ``work_node`` there is reported as ``DURABLE_RECORD_CORRUPT`` for just
    that ``work_id``, never fatal to the rest of the batch -- the module's
    own docstring contract). An unguarded ``model_validate`` would let a
    ``pydantic.ValidationError`` escape into the function's outer generic
    handler, which fails the ENTIRE scan closed instead of isolating the
    one bad record.
    """
    import project_atlas.orchestration.origination.cli as cli_module
    from project_atlas.orchestration.origination.pipeline import originate_all
    from project_atlas.orchestration.origination.projection import (
        OriginationRecord,
        ReconciliationOutcome,
    )

    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    store = tmp_path / "origination-store"

    outcomes = originate_all(repo, "demo-project")
    proposal, policy = outcomes[0].proposal, outcomes[0].policy
    corrupt_record = OriginationRecord(
        origination_identity=proposal.origination_identity,
        project_id=proposal.project_id,
        proposal=proposal.model_dump(mode="json"),
        policy_result=policy.model_dump(mode="json"),
        work_node={"not": "a valid WorkNode"},
        state="MATERIALIZED",
    )

    def _fake_reconcile_revision(
        _store: Path,
        *,
        origination_identity: str,
        package_id: str,
        work_node: object,
        state: str = "MATERIALIZED",
        still_current: object = None,
    ) -> ReconciliationOutcome:
        return ReconciliationOutcome(
            superseded=(), materialized=corrupt_record, already_current=False
        )

    monkeypatch.setattr(cli_module, "reconcile_revision", _fake_reconcile_revision)

    payload, exit_code = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=store,
        explicit_trusted=_anchor(main, tree),
    )

    # Isolated, not fatal: EXIT_OK with the one bad outcome reported under
    # not_materialized, not an escaped exception / EXIT_ERROR for the
    # whole scan.
    assert exit_code == EXIT_OK
    assert payload["materialized_count"] == 0
    not_materialized = payload["not_materialized"]
    assert isinstance(not_materialized, list)
    assert len(not_materialized) == 1
    assert not_materialized[0]["materialization_error_code"] == "DURABLE_RECORD_CORRUPT"


def test_scan_reports_stale_snapshot_receipt_instead_of_reconciling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Independent-verification finding F2 on PR #677 -- the scan-side
    receipt half of the stale-snapshot guard (the store-side denial
    itself is proven in ``test_orchestration_origination_supersession
    .py``): when ``reconcile_revision()``'s in-lock ``still_current``
    check finds this scan's evidence no longer matches CURRENT source
    truth, the scan must fail closed to a per-item no-op receipt
    (``materialization_error_code == "STALE_SOURCE_SNAPSHOT"``) --
    observable, isolated to that one work_id, never a whole-scan error
    and never a durable write. Simulated the same way the sibling TOCTOU
    tests above simulate their races: by forcing the checker's verdict,
    since a real stalled-scan window cannot be produced by sequential
    calls in one process."""
    import project_atlas.orchestration.origination.cli as cli_module

    repo = _eligible_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    store = tmp_path / "origination-store"

    monkeypatch.setattr(
        cli_module,
        "_source_identity_still_current",
        lambda _root, _project_id, _identity: False,
    )
    payload, exit_code = run_origination_scan(
        root=repo,
        project_id="demo-project",
        origination_store=store,
        explicit_trusted=_anchor(main, tree),
    )

    # The scan itself completed -- a stale item is a per-item receipt,
    # not a whole-scan failure.
    assert exit_code == EXIT_OK
    assert payload["materialized_count"] == 0
    assert payload["not_materialized_count"] == 1
    not_materialized = payload["not_materialized"]
    assert isinstance(not_materialized, list)
    entry = not_materialized[0]
    assert entry["materialization_error_code"] == "STALE_SOURCE_SNAPSHOT"
    assert entry["superseded_prior_revisions"] == []

    # Fail closed to a no-op on the store: the proposal row is durable
    # (that write predates -- and is independent of -- reconciliation),
    # but nothing was materialized and nothing was superseded.
    projection = load_projection(store)
    assert [record.state for record in projection.records] == ["PROPOSED"]
    assert projection.records[0].work_node is None
