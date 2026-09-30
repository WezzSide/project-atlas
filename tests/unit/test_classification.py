"""Unit tests for specific-first source classification (AS-EXT-001A, §7.1)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from project_atlas.classification import ClassificationRecord, classify_source

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "as-ext-001a"


def test_record_is_frozen() -> None:
    record = classify_source("docs/evidence/x.yaml", "schema_version: 1\n")
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.source_kind = "markdown"  # type: ignore[misc]


def test_explicit_schema_marker_tier() -> None:
    record = classify_source(
        "docs/random/result.yaml",
        "schema_version: 1\nreceipt_type: atlas-core-certification\nstatus: certified\n",
    )
    assert record.source_kind == "evidence-yaml"
    assert record.document_profile == "atlas-receipt"
    assert record.classification_rule == "marker:schema_version+receipt_type"
    assert record.parser_id == "evidence-yaml"


def test_project_manifest_tier() -> None:
    record = classify_source(".atlas-project.yaml", "schema_version: 1\n")
    assert record.source_kind == "project-manifest"
    assert record.parser_id == "project-manifest"


def test_known_evidence_path_tier() -> None:
    record = classify_source("docs/evidence/AS-CORE-002-post-merge-receipt.yaml", "status: x\n")
    assert record.source_kind == "evidence-yaml"
    assert record.classification_rule == "path:docs/evidence/*.yaml"
    assert record.classification_confidence == "high"


def test_adr_path_and_title_tiers() -> None:
    by_path = classify_source("docs/adr/ADR-003-agent-event-ingestion-contract.md", "# t\n")
    assert by_path.source_kind == "adr"
    assert by_path.classification_rule == "path:docs/adr/ADR-*.md"
    by_title = classify_source("docs/decisions/adr-copy.md", "# ADR-099 — Something\n")
    assert by_title.source_kind == "adr"
    assert by_title.classification_rule == "title:# ADR-NNN"


def test_work_package_path_tier() -> None:
    record = classify_source("docs/work-packages/AS-EXT-001A.md", "# AS-EXT-001A\n")
    assert record.source_kind == "work-package"


def test_backlog_roadmap_worklog_tier() -> None:
    assert classify_source("docs/backlog.md", "# Backlog\n").source_kind == "backlog"
    assert classify_source("WORKLOG.md", "# Log\n").source_kind == "worklog"
    assert classify_source("docs/implementation-roadmap.md", "# R\n").source_kind == "roadmap"
    assert classify_source("docs/master-roadmap.md", "# R\n").source_kind == "roadmap"


def test_dedicated_yaml_without_marker() -> None:
    record = classify_source("config/values.yaml", "key: value\n")
    assert record.source_kind == "structured-yaml"
    assert record.document_profile == "unknown-yaml-profile"
    assert record.classification_confidence == "medium"
    assert record.parser_id == "evidence-yaml"


def test_registered_verify_profile() -> None:
    record = classify_source(
        "docs/architecture-governance/VERIFY-AS-RET-SEQUENCING-DECISION.md", "# VERIFY\n"
    )
    assert record.source_kind == "structured-markdown"
    assert record.document_profile == "verify-structured"
    assert record.classification_rule == "registered-profile:verify-structured"
    assert record.parser_id == "verify-profile"


def test_generic_markdown_and_unsupported() -> None:
    md = classify_source("docs/plan.md", "# Project Atlas\n")
    assert md.source_kind == "markdown"
    assert md.parser_id == "kv-markdown"
    other = classify_source("blob.xyz", "anything")
    assert other.source_kind == "unsupported"
    assert other.parser_id == "none"


def test_keywords_never_override_structural_classification() -> None:
    """§7.1/§10: evidence files must not classify as architecture via keywords."""
    text = (
        "# Architecture and design notes\n"
        "This design describes the architecture of the validation system.\n"
    )
    record = classify_source("docs/evidence/AS-SYNTH-architecture-heavy.yaml", text)
    assert record.source_kind == "evidence-yaml"
    assert record.source_kind != "architecture"


def test_classification_deterministic() -> None:
    args = ("docs/evidence/x.yaml", "schema_version: 1\nreceipt_type: atlas-x\n")
    assert classify_source(*args) == classify_source(*args)


def test_repo_evidence_corpus_classifies_structurally() -> None:
    """All real evidence receipts classify as evidence-yaml, never as
    architecture/markdown solely due to content (§10 MUST)."""
    evidence_dir = REPO_ROOT / "docs" / "evidence"
    receipts = sorted(evidence_dir.glob("*.yaml"))
    # 31 frozen P0 receipts plus later work-package receipts (e.g. AS-EXT-001A).
    assert len(receipts) >= 31, f"P0 corpus expectation: {len(receipts)} receipts"
    for receipt in receipts:
        rel = receipt.relative_to(REPO_ROOT).as_posix()
        record = classify_source(rel, receipt.read_text(encoding="utf-8"))
        assert record.source_kind == "evidence-yaml", rel
        assert record.parser_id == "evidence-yaml", rel


def test_repo_structured_sources_classify_as_expected() -> None:
    """Corpus anchors: VERIFY profile, ADRs, work packages, roadmaps."""
    checks = {
        "docs/architecture-governance/VERIFY-AS-RET-SEQUENCING-DECISION.md": "structured-markdown",
        "docs/adr/ADR-003-agent-event-ingestion-contract.md": "adr",
        "docs/adr/ADR-006-github-repository-governance-baseline.md": "adr",
        "docs/work-packages/AS-GH-001.md": "work-package",
        "docs/backlog.md": "backlog",
        "docs/implementation-roadmap.md": "roadmap",
        "docs/master-roadmap.md": "roadmap",
        "WORKLOG.md": "worklog",
        "docs/plan.md": "markdown",
    }
    for rel, expected_kind in checks.items():
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert classify_source(rel, text).source_kind == expected_kind, rel


def test_fixture_unsupported_source_is_unsupported() -> None:
    record = classify_source(
        "synthetic/unsupported-source.xyz",
        (FIXTURES / "synthetic" / "unsupported-source.xyz").read_text(encoding="utf-8"),
    )
    assert record.source_kind == "unsupported"
    assert isinstance(record, ClassificationRecord)
