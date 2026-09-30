"""AS-CORE-004 S2: per-record semantic subject derivation."""

from __future__ import annotations

import unicodedata

from project_atlas.classification import ClassificationRecord, classify_source
from project_atlas.domain import SemanticSubject, SemanticSubjectKind
from project_atlas.evidence_compiler import extract_source
from project_atlas.subject_derivation import (
    derive_semantic_subject,
    detect_duplicate_semantic_subjects,
)


def _cls(path: str, text: str = "") -> ClassificationRecord:
    return classify_source(path, text)


def test_explicit_semantic_id() -> None:
    text = "semantic_subject: my-stable-id\nsemantic_kind: doc\nStatus: active\n"
    result = derive_semantic_subject(
        project="project-atlas",
        source_id="source-aaaa",
        path="docs/notes/renamed.md",
        text=text,
        classification=_cls("docs/notes/renamed.md", text),
    )
    assert result.resolved
    assert result.serialized() == "doc:my-stable-id"


def test_work_package_id_from_path() -> None:
    path = "docs/work-packages/AS-EXT-001A.md"
    text = "# AS-EXT-001A\nStatus: certified\n"
    result = derive_semantic_subject(
        project="project-atlas",
        source_id="source-wp1",
        path=path,
        text=text,
        classification=_cls(path, text),
    )
    assert result.serialized() == "wp:AS-EXT-001A"


def test_adr_id_from_path() -> None:
    path = "docs/adr/ADR-007-example.md"
    text = "# ADR-007: Example\nStatus: accepted\n"
    result = derive_semantic_subject(
        project="project-atlas",
        source_id="source-adr",
        path=path,
        text=text,
        classification=_cls(path, text),
    )
    assert result.serialized() == "adr:ADR-007"


def test_durable_generic_document_identity() -> None:
    path = "docs/guides/overview.md"
    text = "# Overview\nPurpose: explain the system\n"
    result = derive_semantic_subject(
        project="project-atlas",
        source_id="source-fcb48476ce167a33",
        path=path,
        text=text,
        classification=_cls(path, text),
    )
    assert result.serialized() == "doc:source-fcb48476ce167a33"


def test_source_move_preserves_document_subject() -> None:
    text = "# Overview\nPurpose: explain the system\n"
    source_id = "source-fcb48476ce167a33"
    old = derive_semantic_subject(
        project="project-atlas",
        source_id=source_id,
        path="docs/guides/overview.md",
        text=text,
        classification=_cls("docs/guides/overview.md", text),
    )
    new = derive_semantic_subject(
        project="project-atlas",
        source_id=source_id,
        path="docs/archive/overview-moved.md",
        text=text,
        classification=_cls("docs/archive/overview-moved.md", text),
    )
    assert old.serialized() == new.serialized() == f"doc:{source_id}"


def test_display_rename_preserves_work_package_subject() -> None:
    path = "docs/work-packages/AS-CORE-004.md"
    before = derive_semantic_subject(
        project="project-atlas",
        source_id="source-wp",
        path=path,
        text="# AS-CORE-004\nTitle: Old Title\nStatus: active\n",
        classification=_cls(path, "# AS-CORE-004\n"),
    )
    after = derive_semantic_subject(
        project="project-atlas",
        source_id="source-wp",
        path=path,
        text="# AS-CORE-004\nTitle: New Display Name\nStatus: active\n",
        classification=_cls(path, "# AS-CORE-004\n"),
    )
    assert before.serialized() == after.serialized() == "wp:AS-CORE-004"


def test_missing_stable_id_fails_closed() -> None:
    path = "docs/work-packages/unnamed-package.md"
    text = "# Untitled package\nStatus: draft\n"
    result = derive_semantic_subject(
        project="project-atlas",
        source_id="source-x",
        path=path,
        text=text,
        classification=_cls(path, text),
    )
    assert not result.resolved
    assert result.ambiguous
    assert result.subject is None


def test_duplicate_stable_ids_fail_closed_without_path_winner() -> None:
    collisions = detect_duplicate_semantic_subjects(
        [
            ("source-b", "docs/work-packages/AS-EXT-001A.md", "wp:AS-EXT-001A"),
            ("source-a", "docs/work-packages/AS-EXT-001A-copy.md", "wp:AS-EXT-001A"),
            ("source-c", "docs/other.md", "doc:source-c"),
            # Receipts may reference the same WP without being definitional owners.
            ("source-r", "docs/evidence/r.yaml", "wp:AS-EXT-001A"),
        ]
    )
    assert len(collisions) == 1
    assert collisions[0].serialized == "wp:AS-EXT-001A"
    assert collisions[0].definitional_source_ids == ("source-a", "source-b")
    assert collisions[0].definitional_source_paths == (
        "docs/work-packages/AS-EXT-001A-copy.md",
        "docs/work-packages/AS-EXT-001A.md",
    )


def test_non_ascii_explicit_subject_rejected() -> None:
    """Stable keys: NFC then bounded ASCII — non-ASCII letters fail closed."""
    composed = "cafe\u00e9-id"
    decomposed = "cafe\u0065\u0301-id"
    assert composed != decomposed
    assert unicodedata.normalize("NFC", decomposed) == composed
    for key in (composed, decomposed):
        text = f"semantic_subject: {key}\nsemantic_kind: doc\n"
        result = derive_semantic_subject(
            project="p",
            source_id="s1",
            path="docs/a.md",
            text=text,
            classification=_cls("docs/a.md", text),
        )
        assert not result.resolved
        assert result.ambiguous
        assert result.subject is None
        assert result.reason is not None
        assert "invalid semantic subject key" in result.reason


def test_receipt_derives_work_package_not_receipt_subject() -> None:
    text = (
        "schema_version: 1\n"
        "receipt_type: atlas-core-receipt\n"
        "work_package: AS-EXT-001A\n"
        "status: certified\n"
    )
    entry = {
        "source_id": "source-receipt1",
        "path": "docs/evidence/wp-receipt.yaml",
        "text": text,
        "classification": "validation",
        "source": "../../sources/imported-documents/docs/evidence/wp-receipt.yaml",
        "sha256": "a" * 64,
    }
    extraction = extract_source("project-atlas", entry)
    assert extraction.records
    assert all(record.subject == "wp:AS-EXT-001A" for record in extraction.records)
    # Source is evidence; subject is the work package.
    assert not any(
        record.subject and record.subject.startswith("evidence:") for record in extraction.records
    )


def test_no_silent_project_fallback_for_generic_doc() -> None:
    result = derive_semantic_subject(
        project="project-atlas",
        source_id="source-zzzz",
        path="README.md",
        text="# README\nPurpose: hello\n",
        classification=_cls("README.md", "# README\n"),
    )
    assert result.serialized() == "doc:source-zzzz"
    assert result.subject is not None
    assert result.subject.kind is SemanticSubjectKind.DOCUMENT
    assert result.serialized() != SemanticSubject.project("project-atlas").serialize()
