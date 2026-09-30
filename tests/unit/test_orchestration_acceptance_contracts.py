"""AS-ORIGIN-ACCEPTANCE-001 (PR-D): explicit backlog acceptance contracts.

Fixtures are synthetic and self-contained (built under ``tmp_path``),
mirroring ``test_orchestration_origination_sources.py``'s own style.
Nothing here matches on ``project-atlas``, ``INT-013``, or
``docs/backlog.md`` literally.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.orchestration.origination.acceptance_contracts import (
    _MAX_EVIDENCE_ENTRIES,
    AcceptanceContract,
    AcceptanceContractConfigError,
    apply_acceptance_contracts,
    load_acceptance_contracts,
)
from project_atlas.orchestration.origination.adapter import EligibleRoadmapItem
from project_atlas.orchestration.origination.pipeline import originate_all
from project_atlas.orchestration.origination.proposal import RiskClass
from project_atlas.orchestration.origination.sources import eligible_work_items


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_project_marker(root: Path, contracts_path: str | None) -> None:
    body = "schema_version: 1\nproject:\n  id: fixture-proj\n"
    if contracts_path is not None:
        body += f"origination_acceptance_contracts: {contracts_path}\n"
    _write(root, ".atlas-project.yaml", body)


def _skip_marked_test(root: Path, rel: str = "tests/test_stub.py") -> str:
    """A real file whose content genuinely carries the skip marker
    ``extract_corroborating_facts()`` scans for -- the only kind of
    evidence path that can ever make ``corroborating_signal`` True."""
    _write(
        root,
        rel,
        'import pytest\n\npytestmark = pytest.mark.skip(reason="not yet implemented")\n',
    )
    return rel


def _backlog_item(
    root: Path, rel: str = "docs/backlog.md", line: str = "- [ ] AAA-001 Task\n"
) -> None:
    _write(root, rel, line)


# ---------------------------------------------------------------------------
# load_acceptance_contracts(): explicit-authority config loading
# ---------------------------------------------------------------------------


def test_no_marker_yields_no_contracts(tmp_path: Path) -> None:
    assert load_acceptance_contracts(tmp_path) == ()


def test_marker_without_contracts_key_yields_no_contracts(tmp_path: Path) -> None:
    _write_project_marker(tmp_path, None)
    assert load_acceptance_contracts(tmp_path) == ()


def test_valid_single_contract_is_loaded(tmp_path: Path) -> None:
    evidence = _skip_marked_test(tmp_path)
    _write(
        tmp_path,
        "docs/acceptance-contracts.yaml",
        "contracts:\n"
        "  - item_id: AAA-001\n"
        "    source_path: docs/backlog.md\n"
        f"    evidence: [{evidence}]\n"
        "    proposed_scope: [src/thing.py]\n"
        '    success_criteria: ["The thing works"]\n',
    )
    _write_project_marker(tmp_path, "docs/acceptance-contracts.yaml")
    contracts = load_acceptance_contracts(tmp_path)
    assert len(contracts) == 1
    contract = contracts[0]
    assert contract.item_id == "AAA-001"
    assert contract.source_path == "docs/backlog.md"
    assert contract.evidence == (evidence,)
    assert contract.proposed_scope == ("src/thing.py",)
    assert contract.success_criteria == ("The thing works",)
    assert contract.key == ("docs/backlog.md", "AAA-001")


def test_contracts_key_present_but_not_a_string_fails_closed(tmp_path: Path) -> None:
    _write(tmp_path, ".atlas-project.yaml", "origination_acceptance_contracts:\n  - x\n")
    with pytest.raises(AcceptanceContractConfigError):
        load_acceptance_contracts(tmp_path)


def test_contracts_path_traversal_fails_closed(tmp_path: Path) -> None:
    _write_project_marker(tmp_path, "../outside.yaml")
    with pytest.raises(AcceptanceContractConfigError):
        load_acceptance_contracts(tmp_path)


def test_contracts_path_missing_file_fails_closed(tmp_path: Path) -> None:
    _write_project_marker(tmp_path, "docs/does-not-exist.yaml")
    with pytest.raises(AcceptanceContractConfigError):
        load_acceptance_contracts(tmp_path)


def test_contracts_file_not_a_mapping_fails_closed(tmp_path: Path) -> None:
    _write(tmp_path, "docs/acceptance-contracts.yaml", "- just\n- a\n- list\n")
    _write_project_marker(tmp_path, "docs/acceptance-contracts.yaml")
    with pytest.raises(AcceptanceContractConfigError):
        load_acceptance_contracts(tmp_path)


def test_missing_contracts_list_fails_closed(tmp_path: Path) -> None:
    _write(tmp_path, "docs/acceptance-contracts.yaml", "schema_version: 1\n")
    _write_project_marker(tmp_path, "docs/acceptance-contracts.yaml")
    with pytest.raises(AcceptanceContractConfigError):
        load_acceptance_contracts(tmp_path)


def test_empty_contracts_list_fails_closed(tmp_path: Path) -> None:
    _write(tmp_path, "docs/acceptance-contracts.yaml", "contracts: []\n")
    _write_project_marker(tmp_path, "docs/acceptance-contracts.yaml")
    with pytest.raises(AcceptanceContractConfigError):
        load_acceptance_contracts(tmp_path)


def test_empty_proposed_scope_fails_closed(tmp_path: Path) -> None:
    evidence = _skip_marked_test(tmp_path)
    _write(
        tmp_path,
        "docs/acceptance-contracts.yaml",
        "contracts:\n"
        "  - item_id: AAA-001\n"
        "    source_path: docs/backlog.md\n"
        f"    evidence: [{evidence}]\n"
        "    proposed_scope: []\n"
        '    success_criteria: ["criteria"]\n',
    )
    _write_project_marker(tmp_path, "docs/acceptance-contracts.yaml")
    with pytest.raises(AcceptanceContractConfigError):
        load_acceptance_contracts(tmp_path)


def test_missing_acceptance_evidence_fails_closed(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "docs/acceptance-contracts.yaml",
        "contracts:\n"
        "  - item_id: AAA-001\n"
        "    source_path: docs/backlog.md\n"
        "    evidence: []\n"
        "    proposed_scope: [src/thing.py]\n"
        '    success_criteria: ["criteria"]\n',
    )
    _write_project_marker(tmp_path, "docs/acceptance-contracts.yaml")
    with pytest.raises(AcceptanceContractConfigError):
        load_acceptance_contracts(tmp_path)


def test_missing_success_criteria_fails_closed(tmp_path: Path) -> None:
    evidence = _skip_marked_test(tmp_path)
    _write(
        tmp_path,
        "docs/acceptance-contracts.yaml",
        "contracts:\n"
        "  - item_id: AAA-001\n"
        "    source_path: docs/backlog.md\n"
        f"    evidence: [{evidence}]\n"
        "    proposed_scope: [src/thing.py]\n"
        "    success_criteria: []\n",
    )
    _write_project_marker(tmp_path, "docs/acceptance-contracts.yaml")
    with pytest.raises(AcceptanceContractConfigError):
        load_acceptance_contracts(tmp_path)


def test_evidence_path_traversal_fails_closed(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "docs/acceptance-contracts.yaml",
        "contracts:\n"
        "  - item_id: AAA-001\n"
        "    source_path: docs/backlog.md\n"
        "    evidence: [../outside.py]\n"
        "    proposed_scope: [src/thing.py]\n"
        '    success_criteria: ["criteria"]\n',
    )
    _write_project_marker(tmp_path, "docs/acceptance-contracts.yaml")
    with pytest.raises(AcceptanceContractConfigError):
        load_acceptance_contracts(tmp_path)


def test_evidence_path_outside_project_root_by_nonexistence_fails_closed(tmp_path: Path) -> None:
    """A safely-shaped but real-file-nonexistent evidence path must also
    fail closed -- shape alone is not proof the file exists inside the
    project root."""
    _write(
        tmp_path,
        "docs/acceptance-contracts.yaml",
        "contracts:\n"
        "  - item_id: AAA-001\n"
        "    source_path: docs/backlog.md\n"
        "    evidence: [tests/does_not_exist.py]\n"
        "    proposed_scope: [src/thing.py]\n"
        '    success_criteria: ["criteria"]\n',
    )
    _write_project_marker(tmp_path, "docs/acceptance-contracts.yaml")
    with pytest.raises(AcceptanceContractConfigError):
        load_acceptance_contracts(tmp_path)


def test_duplicate_contract_fails_closed(tmp_path: Path) -> None:
    evidence = _skip_marked_test(tmp_path)
    _write(
        tmp_path,
        "docs/acceptance-contracts.yaml",
        "contracts:\n"
        "  - item_id: AAA-001\n"
        "    source_path: docs/backlog.md\n"
        f"    evidence: [{evidence}]\n"
        "    proposed_scope: [src/thing.py]\n"
        '    success_criteria: ["criteria"]\n'
        "  - item_id: AAA-001\n"
        "    source_path: docs/backlog.md\n"
        f"    evidence: [{evidence}]\n"
        "    proposed_scope: [src/thing2.py]\n"
        '    success_criteria: ["criteria 2"]\n',
    )
    _write_project_marker(tmp_path, "docs/acceptance-contracts.yaml")
    with pytest.raises(AcceptanceContractConfigError):
        load_acceptance_contracts(tmp_path)


def test_same_item_id_different_source_path_is_not_a_duplicate(tmp_path: Path) -> None:
    """The compound (source_path, item_id) key means the same item_id
    declared for two DIFFERENT sources is not ambiguous."""
    evidence = _skip_marked_test(tmp_path)
    _write(
        tmp_path,
        "docs/acceptance-contracts.yaml",
        "contracts:\n"
        "  - item_id: AAA-001\n"
        "    source_path: docs/backlog.md\n"
        f"    evidence: [{evidence}]\n"
        "    proposed_scope: [src/a.py]\n"
        '    success_criteria: ["criteria a"]\n'
        "  - item_id: AAA-001\n"
        "    source_path: docs/second.md\n"
        f"    evidence: [{evidence}]\n"
        "    proposed_scope: [src/b.py]\n"
        '    success_criteria: ["criteria b"]\n',
    )
    _write_project_marker(tmp_path, "docs/acceptance-contracts.yaml")
    contracts = load_acceptance_contracts(tmp_path)
    assert {c.key for c in contracts} == {
        ("docs/backlog.md", "AAA-001"),
        ("docs/second.md", "AAA-001"),
    }


def test_dependencies_field_is_rejected_not_silently_accepted(tmp_path: Path) -> None:
    """IV finding (PR #663 review): a validated-but-never-enforced
    `dependencies`/`forbidden_paths` field is a real trap for a future
    contract author who reasonably assumes the docstring means what it
    says -- neither can be safely wired today (see the module
    docstring), so neither exists in the schema at all. A contract that
    declares either must fail closed as an unrecognized field, never be
    silently accepted and silently do nothing."""
    evidence = _skip_marked_test(tmp_path)
    _write(
        tmp_path,
        "docs/acceptance-contracts.yaml",
        "contracts:\n"
        "  - item_id: AAA-001\n"
        "    source_path: docs/backlog.md\n"
        f"    evidence: [{evidence}]\n"
        "    proposed_scope: [src/a.py]\n"
        '    success_criteria: ["criteria"]\n'
        "    dependencies: [BBB-001]\n",
    )
    _write_project_marker(tmp_path, "docs/acceptance-contracts.yaml")
    with pytest.raises(AcceptanceContractConfigError):
        load_acceptance_contracts(tmp_path)


def test_forbidden_paths_field_is_rejected_not_silently_accepted(tmp_path: Path) -> None:
    evidence = _skip_marked_test(tmp_path)
    _write(
        tmp_path,
        "docs/acceptance-contracts.yaml",
        "contracts:\n"
        "  - item_id: AAA-001\n"
        "    source_path: docs/backlog.md\n"
        f"    evidence: [{evidence}]\n"
        "    proposed_scope: [src/a.py]\n"
        '    success_criteria: ["criteria"]\n'
        "    forbidden_paths: [secrets/]\n",
    )
    _write_project_marker(tmp_path, "docs/acceptance-contracts.yaml")
    with pytest.raises(AcceptanceContractConfigError):
        load_acceptance_contracts(tmp_path)


def test_malformed_item_id_fails_closed(tmp_path: Path) -> None:
    evidence = _skip_marked_test(tmp_path)
    _write(
        tmp_path,
        "docs/acceptance-contracts.yaml",
        "contracts:\n"
        '  - item_id: "not a valid id!!"\n'
        "    source_path: docs/backlog.md\n"
        f"    evidence: [{evidence}]\n"
        "    proposed_scope: [src/a.py]\n"
        '    success_criteria: ["criteria"]\n',
    )
    _write_project_marker(tmp_path, "docs/acceptance-contracts.yaml")
    with pytest.raises(AcceptanceContractConfigError):
        load_acceptance_contracts(tmp_path)


def test_unknown_extra_field_fails_closed(tmp_path: Path) -> None:
    """extra='forbid' -- an unrecognized field (e.g. a typo, or a
    would-be owner-gate override this schema does not define) must fail
    closed, never be silently ignored."""
    evidence = _skip_marked_test(tmp_path)
    _write(
        tmp_path,
        "docs/acceptance-contracts.yaml",
        "contracts:\n"
        "  - item_id: AAA-001\n"
        "    source_path: docs/backlog.md\n"
        f"    evidence: [{evidence}]\n"
        "    proposed_scope: [src/a.py]\n"
        '    success_criteria: ["criteria"]\n'
        "    owner_gate: FORCE_NONE\n",
    )
    _write_project_marker(tmp_path, "docs/acceptance-contracts.yaml")
    with pytest.raises(AcceptanceContractConfigError):
        load_acceptance_contracts(tmp_path)


# ---------------------------------------------------------------------------
# apply_acceptance_contracts(): merge behavior and authority containment
# ---------------------------------------------------------------------------


def _item(
    item_id: str, source_path: str = "docs/backlog.md", **overrides: object
) -> EligibleRoadmapItem:
    base = dict(
        item_id=item_id,
        item_digest="a" * 64,
        title="t",
        status="NOT_STARTED",
        lifecycle="READY",
        depends_on=(),
        blockers=(),
        evidence=(),
        roadmap_text="",
        roadmap_digest="b" * 64,
        source_path=source_path,
    )
    base.update(overrides)
    return EligibleRoadmapItem(**base)  # type: ignore[arg-type]


def _contract(
    item_id: str, source_path: str = "docs/backlog.md", **overrides: object
) -> AcceptanceContract:
    base = dict(
        item_id=item_id,
        source_path=source_path,
        evidence=("tests/test_stub.py",),
        proposed_scope=("src/thing.py",),
        success_criteria=("It works",),
    )
    base.update(overrides)
    return AcceptanceContract(**base)  # type: ignore[arg-type]


def test_apply_merges_evidence_and_sets_overrides() -> None:
    items = (_item("AAA-001", evidence=("docs/existing.md",)),)
    contracts = (_contract("AAA-001"),)
    merged = apply_acceptance_contracts(items, contracts)
    assert len(merged) == 1
    result = merged[0]
    assert result.evidence == ("docs/existing.md", "tests/test_stub.py")
    assert result.contract_proposed_scope == ("src/thing.py",)
    assert result.contract_success_criteria == ("It works",)


def test_apply_with_no_matching_contract_leaves_item_unchanged() -> None:
    items = (_item("AAA-001"),)
    merged = apply_acceptance_contracts(items, ())
    assert merged == items
    assert merged[0].contract_proposed_scope is None
    assert merged[0].contract_success_criteria is None


def test_apply_never_touches_blockers_or_dependencies() -> None:
    """Governance/owner-gate preservation: a contract widens evidence,
    never authority -- an item a project has already declared blocked
    stays blocked regardless of any contract attached to it."""
    items = (_item("AAA-001", blockers=("owner decision required",), depends_on=("BBB-001",)),)
    contracts = (_contract("AAA-001"),)
    merged = apply_acceptance_contracts(items, contracts)
    assert merged[0].blockers == ("owner decision required",)
    assert merged[0].depends_on == ("BBB-001",)


def test_apply_rejects_contract_for_unknown_item_id() -> None:
    items = (_item("AAA-001"),)
    contracts = (_contract("ZZZ-999"),)
    with pytest.raises(AcceptanceContractConfigError):
        apply_acceptance_contracts(items, contracts)


def test_apply_rejects_contract_for_item_already_completed_or_gone() -> None:
    """A completed item is never present in `items` in the first place
    (eligible_*_items only returns NOT_STARTED/IN_PROGRESS + READY) --
    so a stale contract for one fails exactly like an unknown item_id."""
    items: tuple[EligibleRoadmapItem, ...] = ()
    contracts = (_contract("AAA-001"),)
    with pytest.raises(AcceptanceContractConfigError):
        apply_acceptance_contracts(items, contracts)


def test_apply_does_not_leak_authority_across_items() -> None:
    """A contract for item A must never alter item B's fields --
    cross-item authority leakage."""
    items = (_item("AAA-001"), _item("BBB-001"))
    contracts = (_contract("AAA-001"),)
    merged = {item.item_id: item for item in apply_acceptance_contracts(items, contracts)}
    assert merged["AAA-001"].contract_proposed_scope == ("src/thing.py",)
    assert merged["BBB-001"].contract_proposed_scope is None
    assert merged["BBB-001"].evidence == ()


def test_apply_does_not_leak_across_same_item_id_different_source() -> None:
    """Compound-key containment: a contract keyed to one source_path
    must never apply to a same-named item_id from a different source."""
    items = (
        _item("AAA-001", source_path="docs/backlog.md"),
        _item("AAA-001", source_path="docs/second.md"),
    )
    contracts = (_contract("AAA-001", source_path="docs/backlog.md"),)
    result = apply_acceptance_contracts(items, contracts)
    merged = {(item.source_path, item.item_id): item for item in result}
    assert merged[("docs/backlog.md", "AAA-001")].contract_proposed_scope is not None
    assert merged[("docs/second.md", "AAA-001")].contract_proposed_scope is None


# ---------------------------------------------------------------------------
# Full pipeline: a real acceptance contract is what flips execution_ready
# ---------------------------------------------------------------------------


def test_task_list_item_with_valid_contract_becomes_execution_ready(tmp_path: Path) -> None:
    """The directive's core claim: a bare checkbox stays OWNER_HELD /
    execution_ready=False (see test_orchestration_origination_sources.py
    ::test_evidence_free_task_list_item_is_owner_held_not_leasable_o1),
    but attaching a genuine, valid acceptance contract -- with evidence
    that actually carries a skip/xfail marker -- makes the SAME,
    UNMODIFIED policy/risk gates accept it."""
    _backlog_item(tmp_path)
    evidence = _skip_marked_test(tmp_path)
    _write(
        tmp_path,
        "docs/acceptance-contracts.yaml",
        "contracts:\n"
        "  - item_id: AAA-001\n"
        "    source_path: docs/backlog.md\n"
        f"    evidence: [{evidence}]\n"
        "    proposed_scope: [src/thing.py]\n"
        '    success_criteria: ["The thing behaves correctly"]\n',
    )
    _write(
        tmp_path,
        ".atlas-project.yaml",
        "schema_version: 1\n"
        "project:\n"
        "  id: fixture-proj\n"
        "origination_sources:\n"
        "  - path: docs/backlog.md\n"
        "    format: markdown-task-list\n"
        "origination_acceptance_contracts: docs/acceptance-contracts.yaml\n",
    )
    outcomes = originate_all(tmp_path, "fixture-proj")
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.proposal.risk_class == RiskClass.O1_LOW_RISK_SPECIFICATION_BOUND_IMPLEMENTATION
    assert outcome.policy.execution_ready is True
    assert outcome.policy.corroborating_signal is True
    assert outcome.proposal.success_criteria == ("The thing behaves correctly",)
    assert outcome.proposal.proposed_scope == ("src/thing.py",)


def test_contract_evidence_without_a_real_marker_still_fails_policy(tmp_path: Path) -> None:
    """A contract cannot force execution_ready merely by existing -- its
    evidence path must genuinely carry a skip/xfail marker, exactly as
    for a docs/ROADMAP.md item. This is not a bug in this module; it is
    the existing, unmodified policy gate doing its job."""
    _backlog_item(tmp_path)
    _write(tmp_path, "tests/test_plain.py", "def test_ok():\n    assert True\n")
    _write(
        tmp_path,
        "docs/acceptance-contracts.yaml",
        "contracts:\n"
        "  - item_id: AAA-001\n"
        "    source_path: docs/backlog.md\n"
        "    evidence: [tests/test_plain.py]\n"
        "    proposed_scope: [src/thing.py]\n"
        '    success_criteria: ["criteria"]\n',
    )
    _write(
        tmp_path,
        ".atlas-project.yaml",
        "schema_version: 1\n"
        "project:\n"
        "  id: fixture-proj\n"
        "origination_sources:\n"
        "  - path: docs/backlog.md\n"
        "    format: markdown-task-list\n"
        "origination_acceptance_contracts: docs/acceptance-contracts.yaml\n",
    )
    outcomes = originate_all(tmp_path, "fixture-proj")
    assert len(outcomes) == 1
    assert outcomes[0].policy.corroborating_signal is False
    assert outcomes[0].policy.execution_ready is False


def test_blocked_task_list_item_stays_blocked_even_with_a_valid_contract(tmp_path: Path) -> None:
    """Owner-gate preservation end to end: blocker language in the title
    still refuses execution_ready even once a contract supplies real
    evidence -- a contract can never clear a declared blocker."""
    _backlog_item(tmp_path, line="- [ ] GATE-001 Owner merge gate (not this package)\n")
    evidence = _skip_marked_test(tmp_path)
    _write(
        tmp_path,
        "docs/acceptance-contracts.yaml",
        "contracts:\n"
        "  - item_id: GATE-001\n"
        "    source_path: docs/backlog.md\n"
        f"    evidence: [{evidence}]\n"
        "    proposed_scope: [src/thing.py]\n"
        '    success_criteria: ["criteria"]\n',
    )
    _write(
        tmp_path,
        ".atlas-project.yaml",
        "schema_version: 1\n"
        "project:\n"
        "  id: fixture-proj\n"
        "origination_sources:\n"
        "  - path: docs/backlog.md\n"
        "    format: markdown-task-list\n"
        "origination_acceptance_contracts: docs/acceptance-contracts.yaml\n",
    )
    outcomes = originate_all(tmp_path, "fixture-proj")
    assert len(outcomes) == 1
    assert outcomes[0].proposal.blockers  # still present, not cleared
    assert outcomes[0].policy.execution_ready is False


def test_external_blocked_item_stays_blocked_even_with_a_valid_contract(
    tmp_path: Path,
) -> None:
    """Real incident, made load-bearing (owner review, 2026-09-02): the
    actual INT-013 backlog item was originated ``execution_ready = True``
    via its acceptance contract while ``docs/product/CODER-ALPHA-NORTH-
    STAR.md`` -- a SEPARATE, authoritative source the origination
    pipeline does not read -- classified it ``EXTERNAL_BLOCKED``. The
    real fix was not to touch the acceptance-contract mechanism (the
    sibling test above already proves contracts can never clear a
    declared blocker) but to declare that blocker where the pipeline DOES
    look: the task-list item's own title/continuation text in
    ``docs/backlog.md`` (see ``_BLOCKER_KEYWORDS``'s own
    ``"external_blocked"`` entry). This test is the ``EXTERNAL_BLOCKED``-
    keyword-specific sibling of
    ``test_blocked_task_list_item_stays_blocked_even_with_a_valid_
    contract`` (which uses a different keyword) -- confirming a fully
    valid, evidence-carrying contract still cannot flip
    ``execution_ready`` once this exact keyword is present, the same
    keyword the real INT-013 backlog entry now carries."""
    _backlog_item(
        tmp_path,
        line=(
            "- [ ] SYNC-001 Run the authentic estate sync\n"
            "  (EXTERNAL_BLOCKED -- requires owner-provided authentic "
            "project roots; committed fixture projects do not satisfy "
            "this gate)\n"
        ),
    )
    evidence = _skip_marked_test(tmp_path)
    _write(
        tmp_path,
        "docs/acceptance-contracts.yaml",
        "contracts:\n"
        "  - item_id: SYNC-001\n"
        "    source_path: docs/backlog.md\n"
        f"    evidence: [{evidence}]\n"
        "    proposed_scope: [src/thing.py]\n"
        '    success_criteria: ["criteria"]\n',
    )
    _write(
        tmp_path,
        ".atlas-project.yaml",
        "schema_version: 1\n"
        "project:\n"
        "  id: fixture-proj\n"
        "origination_sources:\n"
        "  - path: docs/backlog.md\n"
        "    format: markdown-task-list\n"
        "origination_acceptance_contracts: docs/acceptance-contracts.yaml\n",
    )
    outcomes = originate_all(tmp_path, "fixture-proj")
    assert len(outcomes) == 1
    outcome = outcomes[0]
    # ACCEPTANCE_CONTRACT_CAN_WIDEN_EVIDENCE = YES: the contract's real,
    # marker-carrying evidence still gets merged in.
    assert outcome.proposal.success_criteria == ("criteria",)
    # ACCEPTANCE_CONTRACT_CAN_CLEAR_BLOCKER = NO: but that widened
    # evidence never overrides the declared blocker.
    assert outcome.proposal.blockers
    assert any("external_blocked" in b.lower() for b in outcome.proposal.blockers)
    assert outcome.policy.execution_ready is False


def test_eligible_work_items_applies_contracts(tmp_path: Path) -> None:
    _backlog_item(tmp_path)
    evidence = _skip_marked_test(tmp_path)
    _write(
        tmp_path,
        "docs/acceptance-contracts.yaml",
        "contracts:\n"
        "  - item_id: AAA-001\n"
        "    source_path: docs/backlog.md\n"
        f"    evidence: [{evidence}]\n"
        "    proposed_scope: [src/thing.py]\n"
        '    success_criteria: ["criteria"]\n',
    )
    _write(
        tmp_path,
        ".atlas-project.yaml",
        "schema_version: 1\n"
        "project:\n"
        "  id: fixture-proj\n"
        "origination_sources:\n"
        "  - path: docs/backlog.md\n"
        "    format: markdown-task-list\n"
        "origination_acceptance_contracts: docs/acceptance-contracts.yaml\n",
    )
    items = eligible_work_items(tmp_path)
    assert len(items) == 1
    assert items[0].contract_proposed_scope == ("src/thing.py",)


def test_no_contract_declared_leaves_pipeline_completely_unaffected(tmp_path: Path) -> None:
    """A project with no origination_acceptance_contracts key at all
    behaves exactly as before PR-D -- no import-time or behavior change
    for the overwhelming majority of callers."""
    _backlog_item(tmp_path)
    _write(
        tmp_path,
        ".atlas-project.yaml",
        "schema_version: 1\n"
        "project:\n"
        "  id: fixture-proj\n"
        "origination_sources:\n"
        "  - path: docs/backlog.md\n"
        "    format: markdown-task-list\n",
    )
    outcomes = eligible_work_items(tmp_path)
    assert len(outcomes) == 1
    assert outcomes[0].contract_proposed_scope is None
    assert outcomes[0].contract_success_criteria is None


# ---------------------------------------------------------------------------
# IV round-1 fixes (PR #663 review): marker-parsing parity, the uncaught-
# exception gap in run_origination_scan(), and the evidence/provenance cap
# ---------------------------------------------------------------------------


def test_project_marker_parses_to_non_mapping_fails_closed(tmp_path: Path) -> None:
    """IV finding: this used to silently return () for the SAME
    malformed-.atlas-project.yaml shape (a scalar/list from a templating
    error or truncated file) that load_origination_sources() correctly
    raises for -- despite this module's own docstring already claiming
    that parity. Distinct from test_contracts_file_not_a_mapping_
    fails_closed, which covers the SIDECAR contracts file, not the
    project marker itself."""
    _write(tmp_path, ".atlas-project.yaml", "- just\n- a\n- list\n")
    with pytest.raises(AcceptanceContractConfigError):
        load_acceptance_contracts(tmp_path)


def test_evidence_merge_exceeding_provenance_cap_fails_closed_not_a_raw_crash(
    tmp_path: Path,
) -> None:
    """IV finding (PR #663 review, P2): a schema-valid contract whose
    evidence, once merged with an item's own pre-existing evidence,
    would exceed Provenance.consulted_digests's 16-entry cap used to
    escape as a raw, confusing pydantic.ValidationError from deep inside
    pipeline.py::_build_outcome() instead of this module's own
    AcceptanceContractConfigError."""
    marks = []
    for index in range(_MAX_EVIDENCE_ENTRIES):
        rel = f"tests/test_stub_{index}.py"
        _write(
            tmp_path,
            rel,
            'import pytest\n\npytestmark = pytest.mark.skip(reason="n/a")\n',
        )
        marks.append(rel)
    item = _item("AAA-001", evidence=("docs/existing.md",))
    contract = _contract("AAA-001", evidence=tuple(marks))
    with pytest.raises(AcceptanceContractConfigError):
        apply_acceptance_contracts((item,), (contract,))
