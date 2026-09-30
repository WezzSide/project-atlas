"""AS-E-006: classification_method audit field on SourceRecord (FR + ADV)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from project_atlas.classification import (
    apply_classification_method,
    classify_source,
)
from project_atlas.domain import SourceRecord
from project_atlas.ingestion import apply_classification_audits_to_manifest
from project_atlas.schema import SchemaValidationError, validate_record

SHA = "a" * 64


def _unclassified(**kwargs: object) -> SourceRecord:
    payload: dict[str, object] = {
        "source_id": "src-1",
        "path": "docs/a.md",
        "media_type": "text/markdown",
        "size_bytes": 10,
        "sha256": SHA,
    }
    payload.update(kwargs)
    return SourceRecord.model_validate(payload)


# --- E006-FR ---


def test_e006_fr001_optional_field_default_null() -> None:
    record = _unclassified()
    assert record.classification_method is None
    assert record.classification_state.value == "unclassified"


def test_e006_fr001_schema_accepts_method() -> None:
    record = SourceRecord(
        source_id="src-1",
        path="docs/adr/ADR-001.md",
        media_type="text/markdown",
        size_bytes=10,
        sha256=SHA,
        classification_state="classified",
        classification_method="path:docs/adr/ADR-*.md",
    )
    validate_record(record, "source-record")
    dumped = record.model_dump(mode="json")
    assert dumped["classification_method"] == "path:docs/adr/ADR-*.md"


def test_e006_fr002_stamp_from_classification_rule() -> None:
    source = _unclassified(path="docs/adr/ADR-001.md")
    ext = classify_source("docs/adr/ADR-001.md", "# ADR-001 Title\n")
    stamped = apply_classification_method(source, ext)
    assert stamped.classification_method == ext.classification_rule
    assert stamped.classification_method == "path:docs/adr/ADR-*.md"
    assert stamped.classification_state.value == "classified"


def test_e006_fr003_unclassified_rejects_method() -> None:
    with pytest.raises(ValidationError, match="classification_method"):
        SourceRecord(
            source_id="src-1",
            path="a.md",
            media_type="text/markdown",
            size_bytes=1,
            classification_state="unclassified",
            classification_method="extension:md",
        )


def test_e006_fr003_excluded_rejects_method() -> None:
    with pytest.raises(ValidationError, match="classification_method"):
        SourceRecord(
            source_id="src-1",
            path="a.bin",
            media_type="application/octet-stream",
            size_bytes=1,
            classification_state="excluded",
            exclusion_reason="binary",
            classification_method="extension:md",
        )


def test_e006_fr003_excluded_stamp_keeps_null() -> None:
    excluded = SourceRecord(
        source_id="src-1",
        path="a.bin",
        media_type="application/octet-stream",
        size_bytes=1,
        classification_state="excluded",
        exclusion_reason="binary",
    )
    ext = classify_source("docs/a.md", "# hi\n")
    stamped = apply_classification_method(excluded, ext)
    assert stamped.classification_method is None
    assert stamped.classification_state.value == "excluded"


def test_e006_fr004_unknown_for_unsupported() -> None:
    source = _unclassified(path="blob.bin", media_type="application/octet-stream")
    ext = classify_source("blob.bin", "\x00\x01")
    stamped = apply_classification_method(source, ext)
    assert stamped.classification_method == "no-matching-rule"
    assert stamped.classification_state.value == "unknown"


def test_e006_fr005_deterministic_repeat() -> None:
    source = _unclassified(path="docs/backlog.md")
    text = "# Backlog\n- item\n"
    a = apply_classification_method(source, classify_source("docs/backlog.md", text))
    b = apply_classification_method(source, classify_source("docs/backlog.md", text))
    assert a.classification_method == b.classification_method == "path:docs/backlog.md"
    assert a.model_dump(mode="json") == b.model_dump(mode="json")


def test_e006_fr004_manifest_audit_wire() -> None:
    source = _unclassified(path="WORKLOG.md", source_id="src-wl")
    stamped = apply_classification_method(source, classify_source("WORKLOG.md", "# log\n"))
    manifest = {
        "schema_version": 1,
        "source_root": "/tmp",
        "sources": [
            {
                "source_id": "src-wl",
                "path": "WORKLOG.md",
                "classification_state": "unclassified",
                "classification_method": None,
                "sha256": SHA,
            }
        ],
        "duplicates": {},
        "agent_events": [],
        "inventory_sha256": "x",
        "last_batch_inventory_sha256": "y",
    }
    out = apply_classification_audits_to_manifest(manifest, {"src-wl": stamped})
    row = out["sources"][0]
    assert row["classification_method"] == "path:WORKLOG.md"
    assert row["classification_state"] == "classified"
    assert out["inventory_sha256"] != "x"
    assert out["last_batch_inventory_sha256"] == "y"


# --- ADV ---


def test_e006_adv_no_ext_precedence_rewrite(tmp_path: Path) -> None:
    """EXT rule tables remain the sole precedence surface (no D-006 invent)."""
    classification_py = (
        Path(__file__).resolve().parents[2] / "src" / "project_atlas" / "classification.py"
    ).read_text(encoding="utf-8")
    assert "def classify_source(" in classification_py
    assert "def apply_classification_method(" in classification_py
    # Stamp helper must not redefine tier markers / plugin registry.
    assert "plugin" not in classification_py.lower() or "no plugin framework" in classification_py
    assert "trust_score" not in classification_py
    assert "trust-score" not in classification_py
    # Marker still present — precedence tiers not deleted.
    assert "Tier 1" in classification_py and "Tier 9" in classification_py
    _ = tmp_path  # keep pytest signature stable for future filesystem ADV


def test_e006_adv_no_trust_score_field() -> None:
    record = SourceRecord(
        source_id="src-1",
        path="docs/a.md",
        media_type="text/markdown",
        size_bytes=1,
        classification_state="classified",
        classification_method="extension:md",
    )
    payload = record.model_dump(mode="json")
    assert "trust" not in json.dumps(payload).lower() or "classification_method" in payload
    assert "trust_score" not in payload
    with pytest.raises(ValidationError):
        SourceRecord.model_validate({**payload, "trust_score": 0.9})


def test_e006_adv_schema_forbids_extra_trust() -> None:
    with pytest.raises(SchemaValidationError):
        validate_record(
            {
                "source_id": "src-1",
                "path": "a.md",
                "media_type": "text/markdown",
                "size_bytes": 1,
                "classification_state": "classified",
                "classification_method": "extension:md",
                "trust_score": 1,
            },
            "source-record",
        )
