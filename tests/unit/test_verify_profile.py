"""Unit tests for the VERIFY structured profile (AS-EXT-001A, directive §7.6)."""

from __future__ import annotations

from pathlib import Path

from project_atlas.schema import validate_record
from project_atlas.verify_profile import parse_verify_document

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "as-ext-001a"
SOURCE_PATH = "docs/architecture-governance/VERIFY-AS-RET-SEQUENCING-DECISION.md"


def _parse(fixture: str):  # type: ignore[no-untyped-def]
    text = (FIXTURES / fixture).read_text(encoding="utf-8")
    return parse_verify_document(text, source_path=SOURCE_PATH)


def test_expected_claims_with_distinct_subjects_and_locators() -> None:
    result = _parse("real/f01-verify-structured-document.md")
    by_locator = {record.stable_semantic_locator: record for record in result.records}
    assert set(by_locator) == {
        "yamlpath:status",
        "yamlpath:decision",
        "yamlpath:verify_disposition.status",
        "yamlpath:as_ret_disposition.status",
    }
    assert by_locator["yamlpath:status"].normalized_value == "decided"
    assert by_locator["yamlpath:decision"].normalized_value == "formally-close-verify-as-superseded"
    assert by_locator["yamlpath:verify_disposition.status"].normalized_value == "superseded"
    assert (
        by_locator["yamlpath:as_ret_disposition.status"].normalized_value
        == "may-proceed-to-governance-rereview"
    )
    # Distinct subjects: block-scoped review subjects, never one shared bucket (§7.6).
    assert by_locator["yamlpath:verify_disposition.status"].subject == "review:verify-disposition"
    assert by_locator["yamlpath:as_ret_disposition.status"].subject == "review:as-ret-disposition"


def test_zero_collision() -> None:
    """§7.6: no two records share the identity-relevant tuple."""
    result = _parse("real/f01-verify-structured-document.md")
    tuples = [
        (
            record.claim_type.value,
            record.subject,
            record.normalized_field,
            record.stable_semantic_locator,
        )
        for record in result.records
    ]
    assert len(tuples) == len(set(tuples))
    # Repeated sibling keys in the source (status x3, merge_authorized x2)
    # must produce distinct values, not a false semantic conflict.
    # AS-CORE-004: status leaves refine to review_status under review subjects.
    status_records = [
        r for r in result.records if r.normalized_field in {"status", "review_status"}
    ]
    assert {r.normalized_value for r in status_records} == {
        "decided",
        "superseded",
        "may-proceed-to-governance-rereview",
    }


def test_excerpt_fixture_parses_equivalently() -> None:
    result = _parse("real/f01-verify-collision-excerpt.md")
    assert {r.stable_semantic_locator for r in result.records} == {
        "yamlpath:status",
        "yamlpath:decision",
        "yamlpath:verify_disposition.status",
        "yamlpath:as_ret_disposition.status",
    }
    assert result.diagnostics == ()


def test_records_validate_against_parser_output_schema() -> None:
    result = _parse("real/f01-verify-structured-document.md")
    assert result.records
    for record in result.records:
        validate_record(record, "parser-output")
        assert record.parser_id == "verify-profile"
        assert record.document_profile == "verify-structured"


def test_locators_embed_no_line_numbers_or_values() -> None:
    result = _parse("real/f01-verify-structured-document.md")
    for record in result.records:
        locator = record.stable_semantic_locator
        assert "decided" not in locator
        assert "superseded" not in locator
        assert not any(char.isdigit() for char in locator)


def test_non_claim_fields_stay_visible_as_metadata() -> None:
    result = _parse("real/f01-verify-structured-document.md")
    assert "yamlpath:decision_owner" in result.metadata_paths
    assert "yamlpath:verify_disposition.merge_authorized" in result.metadata_paths
    assert "yamlpath:as_ret_disposition.merge_authorized" in result.metadata_paths


def test_zero_whole_run_abort_on_bad_input() -> None:
    """§7.6: malformed/missing blocks become diagnostics, never exceptions."""
    missing = parse_verify_document("# Title only\n\nplain prose\n", source_path="x.md")
    assert missing.records == ()
    assert missing.diagnostics

    malformed = parse_verify_document("# T\n\nstatus: [unbalanced\n\n## Next\n", source_path="x.md")
    assert malformed.records == ()
    assert any("metadata block rejected" in item for item in malformed.diagnostics)


def test_top_level_claims_carry_source_spans() -> None:
    result = _parse("real/f01-verify-structured-document.md")
    by_locator = {record.stable_semantic_locator: record for record in result.records}
    status = by_locator["yamlpath:status"]
    assert status.source_span.start_line == 3  # source line of `status: decided`
    nested = by_locator["yamlpath:verify_disposition.status"]
    assert nested.source_span.start_line is None  # span where known (§7.2)
