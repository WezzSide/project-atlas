"""AS-2.0-INTEL-001 — evidence quality + uncertainty core.

Derived assessment is not authority and must not mutate claims or sources.
"""

from __future__ import annotations

from copy import deepcopy

from project_atlas.domain import (
    AuthorityLevel,
    Claim,
    ClaimLifecycle,
    ClaimType,
    ConfidenceState,
    ProvenanceReference,
    ReviewState,
)
from project_atlas.intelligence import (
    DERIVED_INTELLIGENCE_IS_AUTHORITY,
    PACKAGE_INTEL_001,
    UNKNOWN_IS_VALID,
    AssessableClaim,
    AssessmentContext,
    ConfidenceClass,
    LimitingFactor,
    LineageIntegrity,
    SourceObservation,
    ValidityWindowInput,
    assess_evidence,
    assess_evidence_many,
)
from project_atlas.source_identity import lineage_id

HASH_A = "a" * 64
HASH_B = "b" * 64
PROJECT_UUID = "00000000-0000-4000-8000-000000000001"


def _prov(
    source_id: str,
    *,
    lineage: str | None = None,
    sha: str | None = HASH_A,
    resource: str | None = None,
) -> ProvenanceReference:
    return ProvenanceReference(
        source_id=source_id,
        source_lineage_id=lineage,
        project_id="harbor-api",
        resource=resource or f"docs/{source_id}.md",
        sha256=sha,
    )


def _claim(
    claim_id: str,
    *,
    value: str = "PostgreSQL 16",
    field: str = "datastore",
    subject: str = "project:harbor-api",
    source_id: str = "src-adr",
    lineage: str | None = None,
    authority: AuthorityLevel = AuthorityLevel.PRIMARY,
    confidence: ConfidenceState = ConfidenceState.HIGH,
    lifecycle: ClaimLifecycle = ClaimLifecycle.NEW,
    provenance: list[ProvenanceReference] | None = None,
    project_id: str | None = "harbor-api",
) -> Claim:
    refs = provenance if provenance is not None else [_prov(source_id, lineage=lineage)]
    return Claim(
        claim_id=claim_id,
        project_id=project_id,
        source_lineage_id=lineage,
        subject=subject,
        claim_type=ClaimType.ARCHITECTURE,
        field=field,
        value=value,
        provenance=refs,
        authority=authority,
        confidence=confidence,
        lifecycle=lifecycle,
        verification=ReviewState.UNREVIEWED,
    )


def test_package_truth_boundary_is_explicit() -> None:
    assert DERIVED_INTELLIGENCE_IS_AUTHORITY == "NO"
    assert UNKNOWN_IS_VALID == "YES"
    assert PACKAGE_INTEL_001 == "AS-2.0-INTEL-001"


def test_single_strong_authoritative_source_is_high() -> None:
    claim = _claim("claim-strong")
    result = assess_evidence(
        claim,
        AssessmentContext(
            sources=(
                SourceObservation(
                    source_id="src-adr",
                    present=True,
                    lineage_integrity=LineageIntegrity.OK,
                ),
            )
        ),
    )
    assert result.confidence_class is ConfidenceClass.HIGH
    assert result.authority_note == "derived-not-authoritative"
    assert "EVIDENCE ASSESSMENT ≠ AUTHORITY" in result.truth_boundary
    assert result.dimensions.source_presence == "present"
    assert LimitingFactor.SINGLE_SOURCE in result.limiting_factors


def test_multiple_corroborating_sources_do_not_claim_independence() -> None:
    lineage_a = lineage_id(PROJECT_UUID, "docs/adr.md", HASH_A, 1)
    lineage_b = lineage_id(PROJECT_UUID, "docs/runbook.md", HASH_B, 1)
    claim = _claim("claim-multi", lineage=lineage_a, source_id="src-adr")
    peer = AssessableClaim.from_claim(
        _claim("claim-peer", source_id="src-runbook", lineage=lineage_b)
    )
    result = assess_evidence(
        claim,
        AssessmentContext(
            sources=(
                SourceObservation(
                    source_id="src-adr",
                    source_lineage_id=lineage_a,
                    present=True,
                    lineage_integrity=LineageIntegrity.OK,
                ),
                SourceObservation(
                    source_id="src-runbook",
                    source_lineage_id=lineage_b,
                    present=True,
                    lineage_integrity=LineageIntegrity.OK,
                ),
            ),
            peer_claims=(peer,),
        ),
    )
    assert result.confidence_class is ConfidenceClass.HIGH
    assert result.dimensions.corroborating_lineage_count >= 2
    assert result.dimensions.independence_known is False
    assert LimitingFactor.INDEPENDENCE_UNKNOWN in result.limiting_factors
    assert "source-independence-not-knowable-from-path-or-id" in result.unknown_factors


def test_multiple_copies_of_same_lineage_do_not_inflate() -> None:
    lineage = lineage_id(PROJECT_UUID, "docs/adr.md", HASH_A, 1)
    claim = _claim(
        "claim-copies",
        lineage=lineage,
        provenance=[
            _prov("src-adr", lineage=lineage, resource="docs/adr.md"),
            _prov("src-adr-copy", lineage=lineage, resource="docs/adr-copy.md"),
        ],
    )
    once = assess_evidence(_claim("claim-once", lineage=lineage, source_id="src-adr"))
    copies = assess_evidence(claim)
    assert copies.confidence_class is once.confidence_class
    assert LimitingFactor.SAME_LINEAGE_ONLY in copies.limiting_factors
    assert copies.dimensions.distinct_lineage_count == 1


def test_authority_mismatch_caps_at_low() -> None:
    claim = _claim("claim-mismatch", authority=AuthorityLevel.CONFLICTING)
    result = assess_evidence(claim)
    assert result.confidence_class is ConfidenceClass.LOW
    assert LimitingFactor.AUTHORITY_MISMATCH in result.limiting_factors


def test_temporally_stale_evidence_is_low() -> None:
    claim = _claim("claim-stale")
    result = assess_evidence(
        claim,
        AssessmentContext(
            as_of_valid_time="2026-10-01",
            validity_windows=(
                ValidityWindowInput(
                    claim_id="claim-stale",
                    valid_from="2024-01-01",
                    valid_to="2024-12-31",
                    evidence_kind="document-declared",
                ),
            ),
        ),
    )
    assert result.confidence_class is ConfidenceClass.LOW
    assert LimitingFactor.TEMPORAL_STALE in result.limiting_factors
    assert result.evaluation_context == "as-of-valid-time"
    assert result.dimensions.temporal_applicability == "stale"


def test_future_not_yet_valid_evidence_stays_unknown() -> None:
    claim = _claim("claim-future")
    result = assess_evidence(
        claim,
        AssessmentContext(
            as_of_valid_time="2024-03-01",
            validity_windows=(
                ValidityWindowInput(
                    claim_id="claim-future",
                    valid_from="2026-01-01",
                    valid_to="2026-12-31",
                    evidence_kind="document-declared",
                ),
            ),
        ),
    )
    assert result.confidence_class is ConfidenceClass.UNKNOWN
    assert LimitingFactor.TEMPORAL_NOT_YET_VALID in result.limiting_factors


def test_conflicting_evidence_is_low_and_traceable() -> None:
    claim = _claim("claim-pg16", value="PostgreSQL 16")
    peer = AssessableClaim.from_claim(
        _claim("claim-pg15", value="PostgreSQL 15", source_id="src-b")
    )
    result = assess_evidence(claim, AssessmentContext(peer_claims=(peer,)))
    assert result.confidence_class is ConfidenceClass.LOW
    assert LimitingFactor.CONTRADICTORY_EVIDENCE in result.limiting_factors
    assert result.contradicting_evidence
    assert result.contradicting_evidence[0].claim_id == "claim-pg15"


def test_missing_provenance_is_unknown() -> None:
    bare = AssessableClaim(
        claim_id="claim-bare",
        project_id="harbor-api",
        subject="project:harbor-api",
        field="datastore",
        value="PostgreSQL 16",
        authority=AuthorityLevel.PRIMARY,
        confidence=ConfidenceState.HIGH,
    )
    result = assess_evidence(bare)
    assert result.confidence_class is ConfidenceClass.UNKNOWN
    assert LimitingFactor.MISSING_PROVENANCE in result.limiting_factors
    assert LimitingFactor.UNSUPPORTED_CLAIM in result.limiting_factors


def test_missing_source_is_low() -> None:
    claim = _claim("claim-missing-src")
    result = assess_evidence(
        claim,
        AssessmentContext(
            sources=(
                SourceObservation(
                    source_id="src-adr",
                    present=False,
                    deleted=True,
                    lineage_integrity=LineageIntegrity.OK,
                ),
            )
        ),
    )
    assert result.confidence_class is ConfidenceClass.LOW
    assert LimitingFactor.MISSING_SOURCE in result.limiting_factors
    assert result.dimensions.source_presence == "missing"


def test_unknown_claim_remains_unknown() -> None:
    claim = _claim(
        "claim-unknown",
        value="unknown",
        authority=AuthorityLevel.PENDING,
        confidence=ConfidenceState.UNKNOWN,
    )
    result = assess_evidence(claim)
    assert result.confidence_class is ConfidenceClass.UNKNOWN
    assert LimitingFactor.UNKNOWN_CLAIM in result.limiting_factors


def test_unsupported_claim_is_unknown() -> None:
    bare = AssessableClaim(
        claim_id="claim-unsupported",
        subject="project:harbor-api",
        field="datastore",
        value="PostgreSQL 16",
        authority=AuthorityLevel.INFERRED,
    )
    result = assess_evidence(bare)
    assert result.confidence_class is ConfidenceClass.UNKNOWN
    assert LimitingFactor.UNSUPPORTED_CLAIM in result.limiting_factors


def test_same_source_observed_repeatedly_does_not_inflate() -> None:
    lineage = lineage_id(PROJECT_UUID, "docs/adr.md", HASH_A, 1)
    claim = _claim(
        "claim-repeat",
        lineage=lineage,
        provenance=[
            _prov("src-adr", lineage=lineage, resource="docs/adr.md"),
            _prov("src-adr", lineage=lineage, resource="docs/adr.md", sha=HASH_A),
        ],
    )
    result = assess_evidence(
        claim,
        AssessmentContext(
            sources=(
                SourceObservation(
                    source_id="src-adr",
                    source_lineage_id=lineage,
                    present=True,
                    observation_count=4,
                    lineage_integrity=LineageIntegrity.OK,
                ),
            )
        ),
    )
    assert result.confidence_class is ConfidenceClass.HIGH
    assert result.dimensions.repeated_same_source is True
    assert result.dimensions.distinct_lineage_count == 1
    assert LimitingFactor.REPEATED_SAME_SOURCE in result.limiting_factors


def test_source_moved_durable_identity_preserved() -> None:
    lineage = lineage_id(PROJECT_UUID, "docs/adr.md", HASH_A, 1)
    claim = _claim("claim-moved", lineage=lineage, source_id="src-adr")
    result = assess_evidence(
        claim,
        AssessmentContext(
            sources=(
                SourceObservation(
                    source_id="src-adr",
                    source_lineage_id=lineage,
                    present=True,
                    path_moved=True,
                    first_seen_path="docs/adr.md",
                    current_path="docs/decisions/adr.md",
                    lineage_integrity=LineageIntegrity.OK,
                ),
            )
        ),
    )
    assert result.confidence_class is ConfidenceClass.HIGH
    assert result.dimensions.durable_identity_preserved_after_move is True
    assert "durable-lineage-preserved-after-move" in result.confidence_reasons
    assert LimitingFactor.MISSING_SOURCE not in result.limiting_factors


def test_assessment_does_not_mutate_claim_or_context() -> None:
    claim = _claim("claim-immut")
    ctx = AssessmentContext(
        peer_claims=(AssessableClaim.from_claim(_claim("claim-other", value="PostgreSQL 15")),),
        sources=(SourceObservation(source_id="src-adr", present=True),),
    )
    before_claim = claim.model_dump()
    before_ctx = ctx.model_dump()
    assess_evidence(claim, ctx)
    assert claim.model_dump() == before_claim
    assert ctx.model_dump() == before_ctx


def test_replay_is_deterministic_and_order_independent() -> None:
    first = _claim("claim-a", value="PostgreSQL 16")
    second = _claim("claim-b", value="PostgreSQL 15", source_id="src-b")
    ctx = AssessmentContext(
        as_of_valid_time="2026-01-01",
        sources=(
            SourceObservation(source_id="src-adr", present=True),
            SourceObservation(source_id="src-b", present=True),
        ),
    )
    left = assess_evidence_many([first, second], ctx)
    right = assess_evidence_many([second, first], ctx)
    assert [item.model_dump() for item in left] == [item.model_dump() for item in right]
    again = assess_evidence_many([first, second], ctx)
    assert [item.model_dump() for item in left] == [item.model_dump() for item in again]


def test_no_numeric_probability_field() -> None:
    result = assess_evidence(_claim("claim-no-prob"))
    dumped = result.model_dump()
    assert "probability" not in dumped
    assert "score" not in dumped
    assert "p_value" not in dumped
    assert result.generated == {"by": "project-atlas"}


def test_wall_clock_as_of_fails_closed() -> None:
    result = assess_evidence(
        _claim("claim-now"),
        AssessmentContext(as_of_valid_time="now"),
    )
    assert LimitingFactor.TEMPORAL_UNKNOWN in result.limiting_factors
    assert any("wall-clock-forbidden" in item for item in result.unknown_factors)


def test_cross_project_peers_are_ignored() -> None:
    claim = _claim("claim-a", project_id="harbor-api")
    other = AssessableClaim.from_claim(
        _claim("claim-b", project_id="other-proj", value="PostgreSQL 15")
    )
    result = assess_evidence(claim, AssessmentContext(peer_claims=(other,)))
    assert LimitingFactor.CONTRADICTORY_EVIDENCE not in result.limiting_factors
    assert result.contradicting_evidence == ()


def test_deepcopy_inputs_match_original_after_assessment() -> None:
    claim = _claim("claim-copy")
    snapshot = deepcopy(claim)
    assess_evidence(claim)
    assert claim == snapshot
