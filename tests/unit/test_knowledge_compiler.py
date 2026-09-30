"""AS-CORE-003 deterministic knowledge compilation contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from project_atlas.domain import (
    AuthorityLevel,
    Claim,
    ClaimLifecycle,
    ProvenanceReference,
    ReviewEntry,
)
from project_atlas.knowledge_compiler import (
    authority_transition_allowed,
    compile_knowledge,
    lifecycle_transition_allowed,
    render_bundle,
    validate_lifecycle_transition,
)
from project_atlas.schema import validate_record

HASH_A = "a" * 64
HASH_B = "b" * 64


def _entry(
    source_id: str, path: str, value: str, sha256: str, classification: str
) -> dict[str, str]:
    if not value.startswith("# "):
        value = f"# Overview\n{value}"
    return {
        "source_id": source_id,
        "path": path,
        "classification": classification,
        "source": f"../../sources/imported-documents/{source_id}.md",
        "sha256": sha256,
        "text": value,
    }


def test_claim_contract_contains_required_evidence_fields() -> None:
    claim = Claim(
        claim_id="claim-1",
        project_id="project-1",
        subject="project-1",
        field="purpose",
        value="A governed project",
        provenance=[
            ProvenanceReference(
                source_id="source-1",
                project_id="project-1",
                resource="sources/imported-documents/source-1.md",
                sha256=HASH_A,
            )
        ],
        authority=AuthorityLevel.PRIMARY,
        extraction_method="semantic-locator:heading:test-heading",
    )
    assert claim.normalized_text == "A governed project"
    assert claim.source_hashes == [HASH_A]
    validate_record(claim, "claim")


def test_provenance_path_traversal_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ProvenanceReference(source_id="source-1", resource="../outside.md")


def test_closed_semantic_vocabularies_reject_unknown_values() -> None:
    provenance = [
        ProvenanceReference(
            source_id="source-1",
            project_id="project-1",
            resource="sources/source-1.md",
            sha256=HASH_A,
        )
    ]
    with pytest.raises(ValidationError):
        Claim(
            claim_id="claim-unknown-type",
            project_id="project-1",
            subject="project-1",
            claim_type="future-claim-type",
            field="purpose",
            value="A project",
            provenance=provenance,
        )
    with pytest.raises(ValidationError):
        ReviewEntry(
            review_id="review-unknown-category",
            project_id="project-1",
            category="future-review-category",
            subject_id="claim-1",
            reason="test",
        )
    with pytest.raises(ValidationError):
        ReviewEntry(
            review_id="review-unknown-status",
            project_id="project-1",
            category="pending-claim",
            subject_id="claim-1",
            reason="test",
            status="future-review-status",
        )


def test_authority_downgrade_requires_explicit_review() -> None:
    assert authority_transition_allowed(AuthorityLevel.MAINTAINED, AuthorityLevel.PRIMARY)
    assert not authority_transition_allowed(AuthorityLevel.PRIMARY, AuthorityLevel.GENERATED)
    assert not authority_transition_allowed(AuthorityLevel.REJECTED, AuthorityLevel.PRIMARY)


def test_conflicting_explicit_claims_remain_visible_and_queue_review(tmp_path: Path) -> None:
    # AS-CORE-004: true conflicts require the same semantic subject + field.
    shared = "semantic_subject: deployment-target\nsemantic_kind: doc\n"
    entries = [
        _entry(
            "source-a",
            "ARCHITECTURE.md",
            shared + "Deployment: port 8000",
            HASH_A,
            "architecture",
        ),
        _entry(
            "source-b",
            "OPERATIONS.md",
            shared + "Deployment: port 9000",
            HASH_B,
            "operations",
        ),
    ]
    bundle = compile_knowledge("project-1", entries, tmp_path)
    assert len(bundle.conflicts) == 1
    assert bundle.conflicts[0].state.value == "unresolved"
    assert bundle.conflicts[0].subject == "doc:deployment-target"
    assert {item.source_id for item in bundle.conflicts[0].claims} == {"source-a", "source-b"}
    assert any(item.category == "conflict" for item in bundle.reviews)
    assert all(claim.provenance for claim in bundle.claims)
    rendered = render_bundle(bundle, "project-1")
    assert "review/conflicts/project-1.json" in rendered
    conflict_state = json.loads(rendered["review/conflicts/project-1.json"])
    assert len(conflict_state["entries"]) == 1


def test_lifecycle_marks_unchanged_then_updated_and_retains_removed(tmp_path: Path) -> None:
    entry = _entry(
        "source-a", "README.md", "# Overview\nPurpose: first", HASH_A, "project-overview"
    )
    first = compile_knowledge("project-1", [entry], tmp_path)
    for relative, content in render_bundle(first, "project-1").items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    second = compile_knowledge("project-1", [entry], tmp_path)
    assert second.claims[0].lifecycle is ClaimLifecycle.UNCHANGED
    changed = _entry(
        "source-a", "README.md", "# Overview\nPurpose: changed", HASH_B, "project-overview"
    )
    third = compile_knowledge("project-1", [changed], tmp_path)
    assert third.claims[0].lifecycle is ClaimLifecycle.UPDATED

    removed = compile_knowledge("project-1", [], tmp_path)
    assert any(item.lifecycle is ClaimLifecycle.REMOVED_SOURCE for item in removed.lifecycle)


def test_lifecycle_transition_table_rejects_unsafe_edges() -> None:
    assert lifecycle_transition_allowed(ClaimLifecycle.NEW, ClaimLifecycle.UNCHANGED)
    assert lifecycle_transition_allowed(ClaimLifecycle.UPDATED, ClaimLifecycle.SUPERSEDED)
    assert not lifecycle_transition_allowed(ClaimLifecycle.NEW, ClaimLifecycle.STALE)
    assert not lifecycle_transition_allowed(ClaimLifecycle.REMOVED_SOURCE, ClaimLifecycle.UPDATED)
    with pytest.raises(ValueError):
        validate_lifecycle_transition(ClaimLifecycle.REMOVED_SOURCE, ClaimLifecycle.UPDATED)


def test_claim_identity_uses_durable_lineage_not_mutable_path(tmp_path: Path) -> None:
    original = _entry("source-a", "docs/README.md", "Purpose: stable", HASH_A, "project-overview")
    original["source_lineage_id"] = "sline-aaaaaaaaaaaaaaaaaaaa"
    moved = dict(original, path="archive/README.md")
    moved_claim = compile_knowledge("project-1", [moved], tmp_path).claims[0]
    original_claim = compile_knowledge("project-1", [original], tmp_path).claims[0]
    assert moved_claim.claim_id == original_claim.claim_id
    assert moved_claim.provenance[0].source_lineage_id == "sline-aaaaaaaaaaaaaaaaaaaa"


def test_new_lineage_generation_has_distinct_claim_namespace(tmp_path: Path) -> None:
    first = _entry("source-a", "SLOT.md", "Purpose: same text", HASH_A, "project-overview")
    second = dict(first, source_lineage_id="sline-bbbbbbbbbbbbbbbbbbbb")
    first["source_lineage_id"] = "sline-aaaaaaaaaaaaaaaaaaaa"
    first_claim = compile_knowledge("project-1", [first], tmp_path).claims[0]
    second_claim = compile_knowledge("project-1", [second], tmp_path).claims[0]
    assert first_claim.claim_id != second_claim.claim_id


def test_legacy_source_identity_emits_compatibility_receipt(tmp_path: Path) -> None:
    entry = _entry("source-legacy", "README.md", "Purpose: legacy", HASH_A, "project-overview")
    rendered = render_bundle(
        compile_knowledge("legacy-project", [entry], tmp_path), "legacy-project"
    )
    assert any("legacy-" in name for name in rendered if "receipts/claims" in name)
