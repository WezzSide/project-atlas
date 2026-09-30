"""Tests for shared Claim Identity v2 primitives (AS-CORE-003)."""

from __future__ import annotations

import pytest

from project_atlas.claim_identity import (
    canonical_identity_key,
    claim_id_from_key,
    extract_claims,
    v2_claim_id,
)
from project_atlas.knowledge_compiler import _extract
from project_atlas.migrations.claim_v2_migration import (
    _extract_candidates,
    _SourceMetadata,
    _v1_claim_id,
    _v2_claim_id,
)


def test_canonical_identity_key_avoids_delimiter_collision() -> None:
    """F-001: embedded delimiters in components cannot produce the same key."""
    key_a = canonical_identity_key("a|b", "c", "purpose", "field", "loc")
    key_b = canonical_identity_key("a", "b|c", "purpose", "field", "loc")
    assert key_a != key_b
    assert key_a == canonical_identity_key("a|b", "c", "purpose", "field", "loc")


def test_canonical_identity_key_handles_unicode_and_whitespace() -> None:
    key = canonical_identity_key("pr\u00f6ject", "s\u00f6urce", "type", "f\u00efeld", "l\u00f2c")
    parsed = key
    assert parsed.startswith('["v2"')
    assert claim_id_from_key(key).startswith("claim-")


def test_canonical_identity_key_empty_field_is_distinct() -> None:
    key_empty = canonical_identity_key("p", "s", "t", "", "loc")
    key_missing = canonical_identity_key("p", "s", "t", "field", "loc")
    assert key_empty != key_missing


@pytest.mark.parametrize(
    ("project", "source", "claim_type", "field", "locator"),
    [
        ("p|a", "s", "type", "field", "loc"),
        ("p", "s|a", "type", "field", "loc"),
        ("p", "s", "ty|pe", "field", "loc"),
        ("p", "s", "type", "fi|eld", "loc"),
        ("p", "s", "type", "field", "lo|c"),
    ],
)
def test_identity_components_can_contain_pipe(
    project: str, source: str, claim_type: str, field: str, locator: str
) -> None:
    """F-001 regression: every component may legally contain a pipe."""
    key = canonical_identity_key(project, source, claim_type, field, locator)
    assert claim_id_from_key(key).startswith("claim-")


def test_compiler_and_migration_v2_claim_ids_match() -> None:
    """Rule parity: identical inputs produce identical v2 claim ids."""
    compiler_id = v2_claim_id(
        "project-uuid",
        "sline-abc123",
        "purpose",
        "purpose",
        "heading:overview",
    )
    migration_id = _v2_claim_id(
        "project-uuid",
        "sline-abc123",
        "purpose",
        "purpose",
        "heading:overview",
    )
    assert compiler_id == migration_id


def test_extract_claims_agrees_between_compiler_and_migration() -> None:
    """Rule parity: compiler and migration consume the same parsed candidates."""
    text = "# Overview\n\npurpose: test project\n\nrequires: nebula {#dep1}\n\nroadmap: active\n"
    compiler_claims, _extraction = _extract(
        "project",
        {
            "source_id": "source",
            "source_lineage_id": "sline-test",
            "project_uuid": "project-uuid",
            "path": "source.md",
            "classification": "project-overview",
            "source": "../../sources/imported-documents/source.md",
            "sha256": "0" * 64,
            "text": text,
        },
    )
    migration_claims = _extract_candidates(
        "project",
        {
            "source.md": _SourceMetadata(
                source_id="source",
                source_lineage_id="sline-test",
                project_identity="project-uuid",
                original_path="source.md",
                classification="project-overview",
            )
        },
        "commit",
        "source.md",
        text,
    )
    assert len(compiler_claims) == len(migration_claims) == 3
    for a, b in zip(compiler_claims, migration_claims, strict=False):
        assert a.claim_id == b.v2_claim_id
        assert a.claim_type.value == b.claim_type
        assert a.field == b.field
        assert a.extraction_method == f"semantic-locator:{b.stable_semantic_locator}"


def test_extract_claims_explicit_id_takes_precedence() -> None:
    text = "# Overview\n\npurpose: test {#explicit-purpose}\n"
    claims = extract_claims(text)
    assert len(claims) == 1
    assert claims[0]["locator"] == "id:explicit-purpose"
    assert claims[0]["value"] == "test"
    assert claims[0]["legacy_value"] == "test {#explicit-purpose}"


def test_identical_unresolved_locator_lines_all_survive_no_silent_drop() -> None:
    """AS-EXT-001A remediation (no-silent-drop contract): locator=None records
    are ungroupable for the §7.7 dedupe pass. Identical unresolved-locator
    occurrences must each survive so the caller diagnoses every line."""
    text = "- decision: same unresolved value\n- decision: same unresolved value\n"
    claims = extract_claims(text, withhold_unresolvable=True)
    assert len(claims) == 2
    assert all(claim["locator"] is None for claim in claims)
    assert all(claim["withheld"] for claim in claims)
    assert [claim["value"] for claim in claims] == ["same unresolved value"] * 2


def test_migration_reconstructs_v1_anchor_value_and_architecture_fallback() -> None:
    metadata = _SourceMetadata(
        source_id="source-architecture",
        source_lineage_id="sline-architecture",
        project_identity="project-uuid",
        original_path="docs/ARCHITECTURE.md",
        classification="architecture",
    )
    text = "# Runtime\n\nEvent-driven services {#runtime-design}\n"
    compiler, _extraction = _extract(
        "project",
        {
            "source_id": metadata.source_id,
            "source_lineage_id": metadata.source_lineage_id,
            "project_uuid": metadata.project_identity,
            "path": metadata.original_path,
            "classification": metadata.classification,
            "source": "../../sources/imported-documents/source-architecture.md",
            "sha256": "a" * 64,
            "text": text,
        },
    )
    migration = _extract_candidates(
        "project",
        {metadata.source_id: metadata},
        "commit",
        "sources/imported-documents/source-architecture.md",
        text,
    )
    assert len(compiler) == len(migration) == 1
    assert compiler[0].claim_id == migration[0].v2_claim_id
    assert migration[0].v1_claim_id == _v1_claim_id(
        metadata.project_identity,
        metadata.source_lineage_id,
        "architecture-statement",
        "architecture",
        "Event-driven services {#runtime-design}",
    )
