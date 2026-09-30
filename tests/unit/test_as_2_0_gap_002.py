"""AS-2.0-GAP-002 — evidence gap prioritization."""

from __future__ import annotations

from project_atlas.domain import (
    AuthorityLevel,
    Claim,
    ClaimLifecycle,
    ClaimType,
    ConfidenceState,
    ProvenanceReference,
    ReviewState,
)
from project_atlas.intelligence import GAP_PRIORITY_IS_FACT
from project_atlas.intelligence.gap_priority import GapPriorityClass, prioritize_evidence_gaps

HASH_A = "a" * 64


def _claim(claim_id: str, *, value: str) -> Claim:
    return Claim(
        claim_id=claim_id,
        project_id="harbor-api",
        subject="project:harbor-api",
        claim_type=ClaimType.ARCHITECTURE,
        field="datastore",
        value=value,
        provenance=[
            ProvenanceReference(source_id="src-a", resource="docs/src-a.md", sha256=HASH_A)
        ],
        authority=AuthorityLevel.PRIMARY,
        confidence=ConfidenceState.HIGH,
        lifecycle=ClaimLifecycle.NEW,
        verification=ReviewState.UNREVIEWED,
    )


def test_priority_is_not_a_score_or_fact() -> None:
    assert GAP_PRIORITY_IS_FACT == "NO"
    found = prioritize_evidence_gaps(
        "harbor-api",
        [
            _claim("claim-a", value="PostgreSQL 15"),
            _claim("claim-b", value="PostgreSQL 16"),
        ],
    )
    assert found
    assert all(item.numeric_score is None for item in found)
    assert all(item.authority_note == "priority-not-fact" for item in found)
    assert any(item.priority_class is GapPriorityClass.CONTESTED_CORE_FACT for item in found)


def test_empty_project_is_blocking_unknown() -> None:
    found = prioritize_evidence_gaps("harbor-api", [])
    assert any(item.priority_class is GapPriorityClass.BLOCKING_UNKNOWN for item in found)
    assert "healthy" not in "".join(item.model_dump_json() for item in found)


def test_foreign_contested_pair_is_not_attributed_to_requested_project() -> None:
    """P1: prioritize_evidence_gaps must scope claims to project_id.

    A foreign contested pair must not become contested-core-fact on the
    requested project. Sibling public APIs already self-scope.
    """
    harbor = _claim("clm-harbor", value="PostgreSQL 15")
    foreign_a = Claim(
        claim_id="clm-foreign-15",
        project_id="other-proj",
        subject="project:other-proj",
        claim_type=ClaimType.ARCHITECTURE,
        field="cache",
        value="Redis 6",
        provenance=[
            ProvenanceReference(source_id="src-x", resource="docs/src-x.md", sha256=HASH_A)
        ],
        authority=AuthorityLevel.PRIMARY,
        confidence=ConfidenceState.HIGH,
        lifecycle=ClaimLifecycle.NEW,
        verification=ReviewState.UNREVIEWED,
    )
    foreign_b = Claim(
        claim_id="clm-foreign-16",
        project_id="other-proj",
        subject="project:other-proj",
        claim_type=ClaimType.ARCHITECTURE,
        field="cache",
        value="Redis 7",
        provenance=[
            ProvenanceReference(source_id="src-y", resource="docs/src-y.md", sha256=HASH_A)
        ],
        authority=AuthorityLevel.PRIMARY,
        confidence=ConfidenceState.HIGH,
        lifecycle=ClaimLifecycle.NEW,
        verification=ReviewState.UNREVIEWED,
    )
    harbor_only = prioritize_evidence_gaps("harbor-api", [harbor])
    mixed = prioritize_evidence_gaps("harbor-api", [harbor, foreign_a, foreign_b])
    harbor_classes = {item.priority_class for item in harbor_only}
    mixed_classes = {item.priority_class for item in mixed}
    assert GapPriorityClass.CONTESTED_CORE_FACT not in harbor_classes
    assert GapPriorityClass.CONTESTED_CORE_FACT not in mixed_classes
    assert mixed_classes == harbor_classes
