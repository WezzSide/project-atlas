"""AS-2.0-STATE-001 — derived project state synthesizer.

Derived state is not canonical, not Roadmap, and never writes truth.
"""

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
from project_atlas.intelligence import (
    DERIVED_STATE_WRITES_CANONICAL_TRUTH,
    PROJECT_STATE_IS_CANONICAL,
    FactStatus,
    StateContext,
    synthesize_project_state,
)
from project_atlas.intelligence.types import SourceObservation, ValidityWindowInput

HASH_A = "a" * 64


def _prov(source_id: str) -> ProvenanceReference:
    return ProvenanceReference(
        source_id=source_id,
        project_id="harbor-api",
        resource=f"docs/{source_id}.md",
        sha256=HASH_A,
    )


def _claim(
    claim_id: str,
    *,
    value: str,
    source_id: str = "src-a",
    project_id: str | None = "harbor-api",
    field: str = "datastore",
    authority: AuthorityLevel = AuthorityLevel.PRIMARY,
    confidence: ConfidenceState = ConfidenceState.HIGH,
    lifecycle: ClaimLifecycle = ClaimLifecycle.NEW,
    predecessor: str | None = None,
) -> Claim:
    return Claim(
        claim_id=claim_id,
        project_id=project_id,
        subject="project:harbor-api",
        claim_type=ClaimType.ARCHITECTURE,
        field=field,
        value=value,
        provenance=[_prov(source_id)],
        authority=authority,
        confidence=confidence,
        lifecycle=lifecycle,
        verification=ReviewState.UNREVIEWED,
        predecessor_claim_id=predecessor,
    )


def test_truth_boundary_is_not_canonical() -> None:
    assert PROJECT_STATE_IS_CANONICAL == "NO"
    assert DERIVED_STATE_WRITES_CANONICAL_TRUTH == "NO"


def test_stable_corroborated_project() -> None:
    state = synthesize_project_state(
        "harbor-api",
        [
            _claim("claim-a", value="PostgreSQL 16"),
            _claim("claim-b", value="PostgreSQL 16", source_id="src-b"),
        ],
        StateContext(
            sources=(
                SourceObservation(source_id="src-a", present=True),
                SourceObservation(source_id="src-b", present=True),
            )
        ),
    )
    assert state.authority_note == "derived-state-not-canonical"
    assert len(state.known_facts) == 1
    assert state.known_facts[0].status is FactStatus.DERIVED
    assert state.known_facts[0].value == "PostgreSQL 16"
    assert state.contested_facts == ()
    assert "healthy" not in state.model_dump_json()


def test_no_data_is_unknown_not_healthy() -> None:
    state = synthesize_project_state("harbor-api", [])
    assert state.known_facts == ()
    assert len(state.unknown_facts) == 1
    assert state.unknown_facts[0].status is FactStatus.UNKNOWN
    assert state.unknown_facts[0].why == "no-claims-present"
    dumped = state.model_dump_json().lower()
    assert "healthy" not in dumped
    assert "on track" not in dumped


def test_stale_evidence_is_stale_not_invalid() -> None:
    state = synthesize_project_state(
        "harbor-api",
        [_claim("claim-a", value="PostgreSQL 15")],
        StateContext(
            as_of_valid_time="2026-10-01",
            validity_windows=(
                ValidityWindowInput(
                    claim_id="claim-a",
                    valid_from="2024-01-01",
                    valid_to="2024-12-31",
                ),
            ),
        ),
    )
    assert len(state.stale_facts) == 1
    assert state.stale_facts[0].status is FactStatus.STALE
    assert "not-invalid" in state.stale_facts[0].why
    assert state.stale_facts[0].value == "PostgreSQL 15"


def test_conflicting_claims_are_contested_not_resolved() -> None:
    state = synthesize_project_state(
        "harbor-api",
        [
            _claim("claim-a", value="PostgreSQL 15"),
            _claim("claim-b", value="PostgreSQL 16", source_id="src-b"),
        ],
    )
    assert len(state.contested_facts) == 1
    assert state.contested_facts[0].status is FactStatus.CONTESTED
    assert state.contested_facts[0].value is None
    assert state.open_contradictions
    assert state.known_facts == ()


def test_temporal_transition_is_change_not_contest() -> None:
    state = synthesize_project_state(
        "harbor-api",
        [
            _claim("claim-mar", value="PostgreSQL 15"),
            _claim("claim-oct", value="PostgreSQL 16", source_id="src-b"),
        ],
        StateContext(
            as_of_valid_time="2024-10-15",
            validity_windows=(
                ValidityWindowInput(
                    claim_id="claim-mar",
                    valid_from="2024-03-01",
                    valid_to="2024-03-31",
                ),
                ValidityWindowInput(
                    claim_id="claim-oct",
                    valid_from="2024-10-01",
                    valid_to="2024-10-31",
                ),
            ),
        ),
    )
    assert state.contested_facts == ()
    assert state.open_contradictions == ()
    assert state.temporal_changes
    assert state.recently_changed_facts
    assert any(item.value == "PostgreSQL 16" for item in state.known_facts + state.stale_facts)


def test_missing_source_is_source_health_attention() -> None:
    state = synthesize_project_state(
        "harbor-api",
        [_claim("claim-a", value="PostgreSQL 16")],
        StateContext(sources=(SourceObservation(source_id="src-a", present=False, deleted=True),)),
    )
    assert state.source_health_concerns
    assert any(item.kind.value == "source-health" for item in state.attention_candidates)


def test_mixed_authority_stays_traceable() -> None:
    state = synthesize_project_state(
        "harbor-api",
        [
            _claim("claim-a", value="PostgreSQL 16", authority=AuthorityLevel.PRIMARY),
            _claim(
                "claim-b",
                value="PostgreSQL 16",
                source_id="src-b",
                authority=AuthorityLevel.INFERRED,
            ),
        ],
    )
    assert len(state.known_facts) == 1
    assert state.known_facts[0].status is FactStatus.DERIVED
    assert "claim-a" in state.known_facts[0].claim_ids
    assert "claim-b" in state.known_facts[0].claim_ids


def test_identity_ambiguity_is_attention_not_resolution() -> None:
    state = synthesize_project_state(
        "harbor-api",
        [
            _claim("claim-a", value="PostgreSQL 15"),
            _claim("claim-b", value="PostgreSQL 16", source_id="src-b"),
        ],
        StateContext(identity_ambiguous_claim_ids=("claim-a",)),
    )
    assert any(item.kind.value == "identity-ambiguity" for item in state.attention_candidates)
    assert state.contested_facts


def test_only_unknown_is_unknown_not_false() -> None:
    state = synthesize_project_state(
        "harbor-api",
        [
            _claim(
                "claim-a",
                value="unknown",
                confidence=ConfidenceState.UNKNOWN,
                authority=AuthorityLevel.PENDING,
            )
        ],
    )
    assert state.known_facts == ()
    assert state.unknown_facts
    assert state.unknown_facts[0].value is None
    assert "false" not in state.unknown_facts[0].why


def test_replay_and_order_independence() -> None:
    claims = [
        _claim("claim-z", value="PostgreSQL 16", source_id="src-z"),
        _claim("claim-a", value="PostgreSQL 16"),
        _claim("claim-m", value="api", field="runtime", source_id="src-m"),
    ]
    left = synthesize_project_state("harbor-api", claims)
    right = synthesize_project_state("harbor-api", list(reversed(claims)))
    assert left.model_dump() == right.model_dump()
    again = synthesize_project_state("harbor-api", claims)
    assert left.model_dump() == again.model_dump()


def test_cross_project_claims_do_not_leak() -> None:
    state = synthesize_project_state(
        "harbor-api",
        [
            _claim("claim-a", value="PostgreSQL 16", project_id="harbor-api"),
            _claim("claim-other", value="secret-other", project_id="other-proj"),
        ],
    )
    claim_ids = [claim_id for fact in state.known_facts for claim_id in fact.claim_ids]
    claim_ids.extend(claim_id for fact in state.unknown_facts for claim_id in fact.claim_ids)
    assert "claim-other" not in claim_ids
    assert "secret-other" not in state.model_dump_json()


def test_does_not_mutate_inputs() -> None:
    claims = [_claim("claim-a", value="PostgreSQL 16")]
    before = claims[0].model_dump()
    synthesize_project_state("harbor-api", claims)
    assert claims[0].model_dump() == before


def test_predecessor_marks_recent_change() -> None:
    state = synthesize_project_state(
        "harbor-api",
        [_claim("claim-a", value="PostgreSQL 16", predecessor="claim-old")],
    )
    assert state.recently_changed_facts
    assert "recently-changed" in state.recently_changed_facts[0].why
