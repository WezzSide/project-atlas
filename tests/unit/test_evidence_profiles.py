"""Unit tests for evidence receipt profiles (AS-EXT-001A, directive §7.5)."""

from __future__ import annotations

from pathlib import Path

from project_atlas.evidence_profiles import (
    ReceiptFieldClass,
    ReceiptSupportStatus,
    assess_receipt,
    assess_receipt_bytes,
    classify_root_key,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "as-ext-001a"
ALL_STATUSES = {
    ReceiptSupportStatus.RECOGNIZED,
    ReceiptSupportStatus.PARTIALLY_RECOGNIZED,
    ReceiptSupportStatus.UNKNOWN_PROFILE,
    ReceiptSupportStatus.INVALID,
}


def _fields_by_locator(assessment):  # type: ignore[no-untyped-def]
    return {field.locator: field for field in assessment.fields}


def test_all_31_real_receipts_parse_and_get_status() -> None:
    """§10 MUST: all 31 receipts structurally parse; every one gets a status."""
    receipts = sorted((REPO_ROOT / "docs" / "evidence").glob("*.yaml"))
    # 31 frozen P0 receipts plus later work-package receipts (e.g. AS-EXT-001A).
    assert len(receipts) >= 31
    for receipt in receipts:
        assessment = assess_receipt_bytes(receipt.read_bytes())
        assert assessment.status in ALL_STATUSES, receipt.name
        # All real receipts are valid YAML mappings: never INVALID.
        assert assessment.status is not ReceiptSupportStatus.INVALID, receipt.name
        assert assessment.fields, receipt.name


def test_nested_receipt_recognized_with_addressable_nested_review() -> None:
    """F-03: schema-heterogeneous receipt; architecture.governor_review addressable."""
    assessment = assess_receipt_bytes(
        (FIXTURES / "real" / "f03-evidence-nested-as-core-003-receipt.yaml").read_bytes()
    )
    assert assessment.status is ReceiptSupportStatus.RECOGNIZED
    fields = _fields_by_locator(assessment)
    assert fields["yamlpath:status"].field_class is ReceiptFieldClass.USER_FACING_CLAIM
    assert fields["yamlpath:status"].concept == "status"
    assert fields["yamlpath:package"].field_class is ReceiptFieldClass.USER_FACING_CLAIM
    assert "yamlpath:architecture.governor_review" in fields
    # Non-universal fields (merged_to_main_at in 2/31) are provenance, never assumed.
    assert fields["yamlpath:merged_to_main_at"].field_class is ReceiptFieldClass.PROVENANCE_METADATA
    # Diagnostic-only detail blocks (P0 synthesis section 6).
    validation_fields = [
        field for locator, field in fields.items() if locator.startswith("yamlpath:validation")
    ]
    assert validation_fields
    assert all(
        field.field_class is ReceiptFieldClass.DIAGNOSTIC_ONLY for field in validation_fields
    )


def test_flat_receipt_field_classification() -> None:
    """First batch-aborting file: commit provenance, unknown blocks visible."""
    assessment = assess_receipt_bytes(
        (FIXTURES / "real" / "evidence-flat-as-core-002-post-merge-receipt.yaml").read_bytes()
    )
    assert assessment.status in {
        ReceiptSupportStatus.RECOGNIZED,
        ReceiptSupportStatus.PARTIALLY_RECOGNIZED,
    }
    fields = _fields_by_locator(assessment)
    assert fields["yamlpath:status"].field_class is ReceiptFieldClass.USER_FACING_CLAIM
    assert fields["yamlpath:work_package_id"].field_class is ReceiptFieldClass.USER_FACING_CLAIM
    assert fields["yamlpath:merge_commit"].field_class is ReceiptFieldClass.PROVENANCE_METADATA
    assert fields["yamlpath:branch"].field_class is ReceiptFieldClass.PROVENANCE_METADATA
    validation_fields = [
        field for locator, field in fields.items() if locator.startswith("yamlpath:validation")
    ]
    assert validation_fields
    assert all(
        field.field_class is ReceiptFieldClass.DIAGNOSTIC_ONLY for field in validation_fields
    )


def test_many_unknown_fields_partially_recognized_and_preserved() -> None:
    assessment = assess_receipt_bytes(
        (FIXTURES / "synthetic" / "many-unknown-fields.yaml").read_bytes()
    )
    assert assessment.status is ReceiptSupportStatus.PARTIALLY_RECOGNIZED
    fields = _fields_by_locator(assessment)
    # Unknown singleton keys stay visible as UNKNOWN STRUCTURED METADATA.
    for key in ("uuid_genesis", "quantum_certifier", "hyperdrive_score"):
        locator = f"yamlpath:{key}"
        assert locator in fields
        assert fields[locator].field_class is ReceiptFieldClass.UNKNOWN_STRUCTURED_METADATA
        assert fields[locator].concept is None
    assert assessment.counts[ReceiptFieldClass.UNKNOWN_STRUCTURED_METADATA.value] >= 6
    # *_commit singletons are still commit provenance by suffix rule (§6).
    assert (
        fields["yamlpath:gov_001_evidence_commit"].field_class
        is ReceiptFieldClass.PROVENANCE_METADATA
    )
    # Known fields still classified normally.
    assert fields["yamlpath:status"].field_class is ReceiptFieldClass.USER_FACING_CLAIM
    assert "uuid_genesis" in fields["yamlpath:uuid_genesis"].locator  # preserved, not dropped


def test_gov_001_style_commit_keys_are_provenance() -> None:
    concept, field_class = classify_root_key("evidence_commit")
    assert concept == "commit-provenance"
    assert field_class is ReceiptFieldClass.PROVENANCE_METADATA


def test_unknown_profile_status() -> None:
    assessment = assess_receipt_bytes(
        (FIXTURES / "synthetic" / "unknown-receipt-profile.yaml").read_bytes()
    )
    assert assessment.status is ReceiptSupportStatus.UNKNOWN_PROFILE
    assert assessment.profile_id is None
    assert any("unknown receipt profile" in item for item in assessment.diagnostics)


def test_malformed_and_duplicate_key_receipts_are_invalid() -> None:
    malformed = assess_receipt_bytes((FIXTURES / "synthetic" / "malformed-yaml.yaml").read_bytes())
    assert malformed.status is ReceiptSupportStatus.INVALID
    assert any("malformed-yaml" in item for item in malformed.diagnostics)

    duplicate = assess_receipt_bytes(
        (FIXTURES / "synthetic" / "duplicate-yaml-keys.yaml").read_bytes()
    )
    assert duplicate.status is ReceiptSupportStatus.INVALID
    assert any("duplicate-yaml-key" in item for item in duplicate.diagnostics)


def test_non_mapping_document_is_invalid() -> None:
    assessment = assess_receipt_bytes(b"- just\n- a\n- list\n")
    assert assessment.status is ReceiptSupportStatus.INVALID


def test_reordered_mappings_assess_identically() -> None:
    first = assess_receipt_bytes((FIXTURES / "synthetic" / "reordered-mapping-a.yaml").read_bytes())
    second = assess_receipt_bytes(
        (FIXTURES / "synthetic" / "reordered-mapping-b.yaml").read_bytes()
    )
    assert first.status == second.status
    assert first.profile_id == second.profile_id
    key = lambda field: (field.locator, field.field_class.value)  # noqa: E731
    assert sorted(first.fields, key=key) == sorted(second.fields, key=key)


def test_no_fallback_to_markdown_or_line_regex() -> None:
    """§7.5: receipts never degrade to generic Markdown/line-regex parsing —
    unrecognized YAML is a mapping-level status, not a text scan."""
    assessment = assess_receipt({"not_a_receipt": True, "quorum": {"present": 7}})
    assert assessment.status is ReceiptSupportStatus.UNKNOWN_PROFILE
    assert all(field.locator.startswith("yamlpath:") for field in assessment.fields)
