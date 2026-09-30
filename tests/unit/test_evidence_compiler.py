"""Per-source structured evidence compilation (AS-EXT-001A, §7.8/§7.9/§10)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.compilation import CompilationOutcome
from project_atlas.domain import CanonicalImpact, DiagnosticCode
from project_atlas.evidence_compiler import extract_source
from project_atlas.knowledge_compiler import compile_knowledge, render_bundle
from project_atlas.schema import validate_record

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "as-ext-001a"
REPO_RECEIPTS = Path(__file__).resolve().parents[2] / "docs" / "evidence"
HASH = "a" * 64


def _entry(path: str, text: str, classification: str = "unknown") -> dict[str, str]:
    return {
        "source_id": f"src-{path.replace('/', '-').replace('.', '-')}",
        "source_lineage_id": "sline-test",
        "project_uuid": "project-uuid",
        "path": path,
        "classification": classification,
        "source": f"../../sources/imported-documents/{path}",
        "sha256": HASH,
        "text": text,
    }


def _fixture_entry(rel: str, path: str | None = None) -> dict[str, str]:
    text = (FIXTURES / rel).read_text(encoding="utf-8")
    return _entry(path or rel.removeprefix("real/").removeprefix("synthetic/"), text)


# --- real receipt corpus: all 31 receipts complete without failure (§10) ---


def test_all_repo_receipts_complete_without_failure() -> None:
    receipts = sorted(REPO_RECEIPTS.glob("*.yaml"))
    assert len(receipts) >= 31  # the frozen P0 receipt corpus (31) plus later receipts
    outcomes = []
    for receipt in receipts:
        path = f"docs/evidence/{receipt.name}"
        extraction = extract_source("project", _entry(path, receipt.read_text(encoding="utf-8")))
        outcomes.append(extraction.candidate.outcome)
        assert extraction.candidate.outcome is not CompilationOutcome.FAILED, path
        assert extraction.candidate.outcome is CompilationOutcome.COMPLETE_CANDIDATE, path
    assert all(outcome is CompilationOutcome.COMPLETE_CANDIDATE for outcome in outcomes)


def test_receipt_claims_use_yamlpath_locators() -> None:
    entry = _fixture_entry(
        "real/evidence-flat-as-core-002-post-merge-receipt.yaml", "docs/evidence/flat.yaml"
    )
    extraction = extract_source("project", entry)
    assert extraction.candidate.outcome is CompilationOutcome.COMPLETE_CANDIDATE
    assert extraction.records
    assert all(record.locator.startswith("yamlpath:") for record in extraction.records)
    assert all(record.parser_id == "evidence-yaml" for record in extraction.records)
    assert {record.field for record in extraction.records} >= {"package_status"}
    statuses = [record for record in extraction.records if record.field == "package_status"]
    assert statuses and statuses[0].extraction_method.startswith("evidence-yaml:yamlpath:")


def test_nested_receipt_sequence_locators() -> None:
    entry = _fixture_entry(
        "real/f03-evidence-nested-as-core-003-receipt.yaml", "docs/evidence/nested.yaml"
    )
    extraction = extract_source("project", entry)
    assert extraction.candidate.outcome is CompilationOutcome.COMPLETE_CANDIDATE
    locators = [record.locator for record in extraction.records]
    assert len(locators) == len(set(locators))


# --- adversarial YAML: explicit failure, never abort (§8/§10) ---------------


def test_malformed_yaml_fails_with_diagnostic() -> None:
    entry = _fixture_entry("synthetic/malformed-yaml.yaml", "docs/evidence/bad.yaml")
    extraction = extract_source("project", entry)
    assert extraction.candidate.outcome is CompilationOutcome.FAILED
    assert extraction.candidate.claims_extracted == 0
    assert extraction.records == ()
    assert extraction.diagnostics[0].code is DiagnosticCode.PARSER_FAILURE
    assert extraction.diagnostics[0].continued is True
    assert extraction.diagnostics[0].canonical_impact is CanonicalImpact.BLOCKED


def test_duplicate_keys_fail_explicitly() -> None:
    entry = _fixture_entry("synthetic/duplicate-yaml-keys.yaml", "docs/evidence/dup.yaml")
    extraction = extract_source("project", entry)
    assert extraction.candidate.outcome is CompilationOutcome.FAILED
    assert extraction.diagnostics[0].code is DiagnosticCode.DUPLICATE_YAML_KEY


def test_unknown_profile_receipt_completes_with_visibility() -> None:
    entry = _fixture_entry("synthetic/unknown-receipt-profile.yaml", "docs/evidence/unknown.yaml")
    extraction = extract_source("project", entry)
    assert extraction.candidate.outcome is CompilationOutcome.COMPLETE_CANDIDATE
    codes = {diagnostic.code for diagnostic in extraction.diagnostics}
    assert DiagnosticCode.UNKNOWN_RECEIPT_PROFILE in codes


def test_many_unknown_fields_preserved_with_visibility() -> None:
    entry = _fixture_entry("synthetic/many-unknown-fields.yaml", "docs/evidence/many.yaml")
    extraction = extract_source("project", entry)
    assert extraction.candidate.outcome is CompilationOutcome.COMPLETE_CANDIDATE
    codes = {diagnostic.code for diagnostic in extraction.diagnostics}
    assert DiagnosticCode.UNKNOWN_STRUCTURED_FIELD in codes


# --- VERIFY structured profile (§7.6/§10) ------------------------------------


def test_verify_document_yields_distinct_claims_no_collision() -> None:
    entry = _fixture_entry(
        "real/f01-verify-structured-document.md",
        "docs/architecture-governance/VERIFY-AS-CORE-003.md",
    )
    extraction = extract_source("project", entry)
    assert extraction.candidate.outcome is CompilationOutcome.COMPLETE_CANDIDATE
    locators = [record.locator for record in extraction.records]
    assert len(locators) == len(set(locators))
    assert all(locator.startswith("yamlpath:") for locator in locators)
    bundle = compile_knowledge("project", [entry], Path.cwd())
    assert len(bundle.claims) == len(extraction.records)


# --- kv-markdown withholding (§7.7/§7.8) -------------------------------------


def test_unresolvable_line_withheld_not_silent() -> None:
    entry = _entry("docs/notes.md", "status: certified\n")
    extraction = extract_source("project", entry)
    assert extraction.candidate.outcome is CompilationOutcome.PARTIAL_CANDIDATE
    assert extraction.candidate.claims_withheld == 1
    assert extraction.candidate.claims_extracted == 0
    assert extraction.diagnostics[0].code is DiagnosticCode.UNRESOLVED_LOCATOR
    assert extraction.candidate.diagnostics  # partial requires diagnostics


def test_identical_unresolved_lines_each_diagnosed_no_silent_drop() -> None:
    """AS-EXT-001A remediation: two identical unresolved-locator lines must
    yield two withheld records and two UNRESOLVED_LOCATOR diagnostics — the
    §7.7 dedupe pass must not collapse locator=None records."""
    entry = _entry("docs/notes.md", "status: certified\nstatus: certified\n")
    extraction = extract_source("project", entry)
    assert extraction.candidate.outcome is CompilationOutcome.PARTIAL_CANDIDATE
    assert extraction.candidate.claims_withheld == 2
    assert extraction.candidate.claims_extracted == 0
    unresolved = [
        diagnostic
        for diagnostic in extraction.diagnostics
        if diagnostic.code is DiagnosticCode.UNRESOLVED_LOCATOR
    ]
    assert len(unresolved) == 2


def test_duplicate_explicit_ids_withheld_as_partial() -> None:
    text = "## A\n\nstatus: certified {#dup}\n\n## B\n\nstatus: superseded {#dup}\n"
    entry = _entry("docs/dup.md", text)
    extraction = extract_source("project", entry)
    assert extraction.candidate.outcome is CompilationOutcome.PARTIAL_CANDIDATE
    assert extraction.candidate.claims_withheld == 2
    assert all(
        diagnostic.code is DiagnosticCode.AMBIGUOUS_IDENTITY
        for diagnostic in extraction.diagnostics
    )


def test_unsupported_source_kind_visible_and_isolated() -> None:
    entry = _fixture_entry("synthetic/unsupported-source.xyz", "data/blob.xyz")
    extraction = extract_source("project", entry)
    assert extraction.candidate.outcome is CompilationOutcome.FAILED
    assert extraction.diagnostics[0].code is DiagnosticCode.UNSUPPORTED_SOURCE_KIND
    assert extraction.diagnostics[0].continued is True


# --- compiler integration: isolation, promotion, reporting (§7.8/§10) --------


def test_one_bad_source_never_aborts_good_sources(tmp_path: Path) -> None:
    good = _entry("docs/README.md", "# Overview\nPurpose: governed project\n")
    bad = _fixture_entry("synthetic/malformed-yaml.yaml", "docs/evidence/bad.yaml")
    bundle = compile_knowledge("project", [good, bad], tmp_path)
    assert len(bundle.claims) == 1
    assert bundle.claims[0].field == "purpose"
    outcomes = {candidate.outcome for candidate in bundle.candidates}
    assert outcomes == {CompilationOutcome.COMPLETE_CANDIDATE, CompilationOutcome.FAILED}
    assert bundle.status["sources_complete"] == 1
    assert bundle.status["sources_failed"] == 1
    assert bundle.diagnostics


def test_partial_candidate_never_alters_canonical_state(tmp_path: Path) -> None:
    partial = _entry("docs/notes.md", "status: certified\n")
    good = _entry("docs/README.md", "# Overview\nPurpose: governed project\n")
    bundle = compile_knowledge("project", [partial, good], tmp_path)
    assert len(bundle.claims) == 1  # only the COMPLETE source promotes
    assert bundle.status["sources_partial"] == 1
    assert bundle.status["claims_withheld"] == 1
    rendered = render_bundle(bundle, "project")
    outcomes = json.loads(rendered["state/compilation-outcomes/project.json"])
    by_path = {item["source_path"]: item for item in outcomes["candidates"]}
    assert by_path["docs/notes.md"]["outcome"] == "PARTIAL_CANDIDATE"
    assert by_path["docs/README.md"]["outcome"] == "COMPLETE_CANDIDATE"


def test_diagnostics_render_schema_valid_and_reconcile(tmp_path: Path) -> None:
    entries = [
        _entry("docs/notes.md", "status: certified\n"),
        _fixture_entry("synthetic/malformed-yaml.yaml", "docs/evidence/bad.yaml"),
        _entry("docs/README.md", "# Overview\nPurpose: governed project\n"),
    ]
    bundle = compile_knowledge("project", entries, tmp_path)
    rendered = render_bundle(bundle, "project")
    diagnostics = json.loads(rendered["state/diagnostics/project.json"])
    for record in diagnostics["diagnostics"]:
        validate_record(record, "diagnostic")
    # Diagnostics reconcile with withheld/failed sources: nothing drops silently.
    outcomes = json.loads(rendered["state/compilation-outcomes/project.json"])
    non_complete = [
        item for item in outcomes["candidates"] if item["outcome"] != "COMPLETE_CANDIDATE"
    ]
    assert len(non_complete) == 2
    assert len(diagnostics["diagnostics"]) == sum(
        1 for item in non_complete if item["diagnostics"]
    ) or len(diagnostics["diagnostics"]) >= len(non_complete)


def test_extraction_deterministic_replay() -> None:
    entry = _fixture_entry(
        "real/f03-evidence-nested-as-core-003-receipt.yaml", "docs/evidence/nested.yaml"
    )
    first = extract_source("project", entry)
    second = extract_source("project", entry)
    assert first == second


# --- intra-source locator collisions: withheld, never batch-abort (§7.7/§7.8)

# Adversarial repro A: keys distinct pre-normalization, NFC-collapsed
# locators identical (café U+00E9 vs café U+0065+U+0301).
REPRO_A_NFC_COLLISION = (
    "schema_version: 1\n"
    "receipt_type: atlas-core-work-package\n"
    "status:\n"
    "  caf\u00e9: alpha\n"
    "  cafe\u0301: beta\n"
)

# Adversarial repro B: repeated stable sequence keys yield identical
# yamlpath locators with different values.
REPRO_B_SEQUENCE_KEY_COLLISION = (
    "schema_version: 1\n"
    "receipt_type: atlas-core-work-package\n"
    "status:\n"
    "  - id: same\n"
    "    x: alpha\n"
    "  - id: same\n"
    "    x: beta\n"
)


def _collision_entry(text: str) -> dict[str, str]:
    return _entry("docs/evidence/colliding.yaml", text)


def test_repro_a_nfc_collided_locators_withheld_not_abort() -> None:
    extraction = extract_source("project", _collision_entry(REPRO_A_NFC_COLLISION))
    assert extraction.candidate.outcome is CompilationOutcome.PARTIAL_CANDIDATE
    assert extraction.candidate.claims_withheld == 2
    assert extraction.candidate.claims_extracted == 0
    assert extraction.records == ()
    assert all(
        diagnostic.code is DiagnosticCode.DUPLICATE_LOCATOR for diagnostic in extraction.diagnostics
    )
    assert all(diagnostic.continued for diagnostic in extraction.diagnostics)


def test_repro_b_repeated_stable_sequence_keys_withheld_not_abort() -> None:
    extraction = extract_source("project", _collision_entry(REPRO_B_SEQUENCE_KEY_COLLISION))
    assert extraction.candidate.outcome is CompilationOutcome.PARTIAL_CANDIDATE
    assert extraction.candidate.claims_withheld == 2
    # The identical-value `id` leaf dedupes to one statement (§7.7 step 1);
    # only the different-value `x` leaves are withheld.
    assert len(extraction.records) == 1
    assert extraction.records[0].locator == "yamlpath:status[id=same].id"
    withheld = [
        diagnostic
        for diagnostic in extraction.diagnostics
        if diagnostic.code is DiagnosticCode.DUPLICATE_LOCATOR
    ]
    assert len(withheld) == 2
    assert all(diagnostic.continued for diagnostic in withheld)


def test_identical_value_collision_keeps_first_statement() -> None:
    """Same locator + same value is one repeated statement, not a collision."""
    text = (
        "schema_version: 1\nreceipt_type: atlas-core-work-package\n"
        "status:\n  caf\u00e9: alpha\n  cafe\u0301: alpha\n"
    )
    extraction = extract_source("project", _collision_entry(text))
    assert extraction.candidate.outcome is CompilationOutcome.COMPLETE_CANDIDATE
    assert extraction.candidate.claims_extracted == 1
    assert extraction.records[0].value == "alpha"


@pytest.mark.parametrize("collision_text", [REPRO_A_NFC_COLLISION, REPRO_B_SEQUENCE_KEY_COLLISION])
def test_colliding_source_never_blocks_good_sibling(tmp_path: Path, collision_text: str) -> None:
    """§10 MUST: one bad source does not prevent extraction from independent
    good sources — the colliding source degrades to PARTIAL, the good source
    still promotes, and no exception escapes per-source isolation."""
    good = _entry("docs/README.md", "# Overview\nPurpose: governed project\n")
    colliding = _collision_entry(collision_text)
    bundle = compile_knowledge("project", [good, colliding], tmp_path)
    assert len(bundle.claims) == 1
    assert bundle.claims[0].field == "purpose"
    by_path = {candidate.source_path: candidate for candidate in bundle.candidates}
    assert by_path["docs/README.md"].outcome is CompilationOutcome.COMPLETE_CANDIDATE
    assert by_path["docs/evidence/colliding.yaml"].outcome is (CompilationOutcome.PARTIAL_CANDIDATE)
    assert bundle.status["sources_partial"] == 1
    assert bundle.status["claims_withheld"] == 2
