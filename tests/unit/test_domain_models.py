"""Unit tests for the Epic B domain models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from project_atlas.domain import (
    Claim,
    ConceptRecord,
    ConceptType,
    ConflictRecord,
    KnowledgeState,
    LifecycleStatus,
    Maturity,
    ProvenanceReference,
    Relationship,
    RelationType,
    ReviewState,
    Severity,
    SourceRecord,
    ValidationFinding,
    ValidationGate,
)

SHA = "a" * 64


def _provenance() -> ProvenanceReference:
    return ProvenanceReference(source_id="src-readme", resource="sources/repositories/x/README.md")


class TestSourceRecord:
    def test_valid_minimal(self) -> None:
        record = SourceRecord(
            source_id="src-1", path="docs/a.md", media_type="text/markdown", size_bytes=10
        )
        assert record.classification_state.value == "unclassified"
        assert record.sha256 is None

    def test_valid_full(self) -> None:
        record = SourceRecord(
            source_id="src-1",
            path="docs/a.md",
            media_type="text/markdown",
            sha256=SHA,
            size_bytes=10,
            likely_project="PRJ-NEBULA",
        )
        assert record.sha256 == SHA

    def test_rejects_missing_required_fields(self) -> None:
        with pytest.raises(ValidationError):
            SourceRecord.model_validate({"source_id": "src-1"})

    def test_rejects_invalid_sha256(self) -> None:
        with pytest.raises(ValidationError):
            SourceRecord(
                source_id="src-1",
                path="a.md",
                media_type="text/markdown",
                size_bytes=1,
                sha256="not-a-hash",
            )

    def test_rejects_negative_size(self) -> None:
        with pytest.raises(ValidationError):
            SourceRecord(source_id="src-1", path="a.md", media_type="text/markdown", size_bytes=-1)

    def test_excluded_requires_reason(self) -> None:
        with pytest.raises(ValidationError, match="exclusion_reason"):
            SourceRecord(
                source_id="src-1",
                path="a.bin",
                media_type="application/octet-stream",
                size_bytes=1,
                classification_state="excluded",
            )

    def test_reason_only_valid_when_excluded(self) -> None:
        with pytest.raises(ValidationError, match="exclusion_reason"):
            SourceRecord(
                source_id="src-1",
                path="a.md",
                media_type="text/markdown",
                size_bytes=1,
                exclusion_reason="binary",
            )

    def test_excluded_with_reason_passes(self) -> None:
        record = SourceRecord(
            source_id="src-1",
            path="a.bin",
            media_type="application/octet-stream",
            size_bytes=1,
            classification_state="excluded",
            exclusion_reason="unsupported media type",
        )
        assert record.exclusion_reason == "unsupported media type"


class TestClaimAndProvenance:
    def test_valid_claim(self) -> None:
        claim = Claim(
            claim_id="clm-1",
            subject="PRJ-NEBULA",
            field="status",
            value="active",
            provenance=[_provenance()],
        )
        assert claim.verification is ReviewState.UNREVIEWED

    def test_claim_requires_provenance(self) -> None:
        """No claim without a traceable source (FR-007)."""
        with pytest.raises(ValidationError):
            Claim(
                claim_id="clm-1",
                subject="PRJ-NEBULA",
                field="status",
                value="active",
                provenance=[],
            )

    def test_provenance_requires_source_id_and_resource(self) -> None:
        with pytest.raises(ValidationError):
            ProvenanceReference.model_validate({"source_id": "src-1"})


class TestConceptRecord:
    def test_valid_with_vocabularies(self) -> None:
        concept = ConceptRecord(
            concept_id="PRJ-NEBULA",
            type=ConceptType.PROJECT,
            title="Nebula Control Platform",
            status=LifecycleStatus.ACTIVE,
            maturity=Maturity.PRODUCTION,
            knowledge_state=KnowledgeState.EVIDENCE_BACKED,
            sources=[_provenance()],
            relationships=[
                Relationship(type=RelationType.DEPENDS_ON, target="infrastructure/hosts/vps-01.md")
            ],
        )
        assert concept.status.value == "active"
        assert concept.review_state is ReviewState.UNREVIEWED

    def test_rejects_unknown_lifecycle_value(self) -> None:
        with pytest.raises(ValidationError):
            ConceptRecord(concept_id="c-1", type="Project", title="t", status="on-fire")

    def test_rejects_unknown_type(self) -> None:
        with pytest.raises(ValidationError):
            ConceptRecord(concept_id="c-1", type="Document", title="t")

    def test_rejects_empty_title(self) -> None:
        with pytest.raises(ValidationError):
            ConceptRecord(concept_id="c-1", type="Project", title="")


class TestConflictRecord:
    def test_rejects_unknown_conflict_type(self) -> None:
        with pytest.raises(ValidationError):
            ConflictRecord(
                conflict_id="conf-1",
                subject="PRJ-NEBULA",
                field="redis_version",
                claims=[
                    {"source_id": "s-1", "claim": "Redis 7"},
                    {"source_id": "s-2", "claim": "Redis 8"},
                ],
                conflict_type="future-conflict-type",
            )

    def test_requires_two_claims(self) -> None:
        with pytest.raises(ValidationError):
            ConflictRecord(
                conflict_id="conf-1",
                subject="PRJ-NEBULA",
                field="redis_version",
                claims=[{"source_id": "s-1", "claim": "Redis 7"}],
            )

    def test_unresolved_must_not_have_resolution(self) -> None:
        with pytest.raises(ValidationError, match="resolution"):
            ConflictRecord(
                conflict_id="conf-1",
                subject="PRJ-NEBULA",
                field="redis_version",
                claims=[
                    {"source_id": "s-1", "claim": "Redis 7"},
                    {"source_id": "s-2", "claim": "Redis 8"},
                ],
                resolution="picked 8",
            )

    def test_resolved_requires_resolution(self) -> None:
        with pytest.raises(ValidationError, match="resolution"):
            ConflictRecord(
                conflict_id="conf-1",
                subject="PRJ-NEBULA",
                field="redis_version",
                claims=[
                    {"source_id": "s-1", "claim": "Redis 7"},
                    {"source_id": "s-2", "claim": "Redis 8"},
                ],
                state="resolved",
            )


class TestValidationFinding:
    def test_valid(self) -> None:
        finding = ValidationFinding(
            finding_id="fnd-1",
            rule_id="link-unresolved",
            severity=Severity.ERROR,
            gate=ValidationGate.STRUCTURAL,
            message="broken link",
            path="projects/x/project.md",
        )
        assert finding.severity.value == "error"

    def test_rejects_unknown_severity(self) -> None:
        with pytest.raises(ValidationError):
            ValidationFinding(
                finding_id="fnd-1",
                rule_id="r",
                severity="fatal",
                gate="structural",
                message="m",
            )
