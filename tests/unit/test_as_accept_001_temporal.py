"""AS-ACCEPT-001 Wave-A temporal cases (AX-TMP-*).

Oracles: INV-002, INV-004, INV-005 — temporal status is not rewritten by
import order, lexical/path winners, staging mtime, or authority resurrection.
"""

from __future__ import annotations

from datetime import datetime

from project_atlas.authority_evaluator import SourceArtifact, evaluate_disposition
from project_atlas.domain import Claim, ProvenanceReference
from project_atlas.domain.authority_semantics import AuthorityDisposition
from project_atlas.domain.temporal import ResolutionBasis, TemporalStatus
from project_atlas.domain.vocabulary import (
    AuthorityLevel,
    ClaimLifecycle,
    ClaimType,
    ConfidenceState,
    ReviewState,
)
from project_atlas.temporal_evaluator import evaluate_group
from project_atlas.temporal_evidence import ClaimTemporalContext, SourceTemporalFacts


def _ctx(
    claim_id: str,
    *,
    subject: str = "wp:AX-TMP",
    field: str = "package_status",
    value: str = "v",
    source_id: str = "source-a",
    authority: str = AuthorityLevel.VALIDATED_EXECUTION.value,
    facts: SourceTemporalFacts | None = None,
) -> ClaimTemporalContext:
    return ClaimTemporalContext(
        claim_id=claim_id,
        subject=subject,
        field=field,
        value=value,
        source_id=source_id,
        authority=authority,
        facts=facts or SourceTemporalFacts(source_id=source_id, path=f"docs/{source_id}.yaml"),
        path=f"docs/{source_id}.yaml",
    )


def _claim(
    claim_id: str,
    *,
    subject: str,
    field: str,
    value: str,
    source_id: str,
) -> Claim:
    return Claim(
        claim_id=claim_id,
        project_id="project-atlas",
        subject=subject,
        claim_type=ClaimType.PROJECT_PURPOSE,
        field=field,
        value=value,
        provenance=[
            ProvenanceReference(
                source_id=source_id,
                resource=f"sources/{source_id}.md",
                sha256="a" * 64,
            )
        ],
        authority=AuthorityLevel.MAINTAINED,
        confidence=ConfidenceState.MEDIUM,
        lifecycle=ClaimLifecycle.NEW,
        verification=ReviewState.UNREVIEWED,
    )


def test_ax_tmp_002_late_observation_does_not_flip_tip() -> None:
    """AX-TMP-002: late import of an old event must not flip an established tip.

    INV-002 / INV-005 — knowledge-time / observation order is not valid-time.
    """
    earlier = _ctx(
        "claim-earlier",
        value="certified",
        source_id="source-old",
        facts=SourceTemporalFacts(
            source_id="source-old",
            path="docs/evidence/post-merge.yaml",
            original_certification="abc",
            status_value="certified",
            document_timestamp=datetime(2025, 1, 1),
        ),
    )
    later = _ctx(
        "claim-later",
        value="recertified-merge-eligible",
        source_id="source-tip",
        facts=SourceTemporalFacts(
            source_id="source-tip",
            path="docs/evidence/recert.yaml",
            original_certification="abc",
            status_value="recertified-merge-eligible",
            has_post_merge_signal=True,
            document_timestamp=datetime(2026, 6, 1),
        ),
    )
    forward = evaluate_group([earlier, later], project_id="project-atlas", compilation_id="c1")
    reverse = evaluate_group([later, earlier], project_id="project-atlas", compilation_id="c1")
    assert forward.disposition.temporal_status is TemporalStatus.CURRENT
    assert forward.disposition.current_claim_id == "claim-later"
    assert "claim-earlier" in forward.disposition.historical_claim_ids
    # Import / list order must not change the tip (no ordinal/path winner).
    assert reverse.disposition.model_dump(mode="json") == forward.disposition.model_dump(
        mode="json"
    )


def test_ax_tmp_003_equal_timestamp_incompatible_values_unresolved() -> None:
    """AX-TMP-003: same timestamp + incompatible values → unresolved, never path winner.

    INV-004 — fail closed; no ordinal/path/lexical silent pick.
    """
    ts = datetime(2026, 3, 15, 12, 0, 0)
    a = _ctx(
        "claim-aaa",
        value="alpha-status",
        source_id="source-a",
        facts=SourceTemporalFacts(
            source_id="source-a",
            path="docs/evidence/z-late-path.yaml",
            document_timestamp=ts,
            status_value="alpha-status",
        ),
    )
    b = _ctx(
        "claim-zzz",
        value="beta-status",
        source_id="source-b",
        facts=SourceTemporalFacts(
            source_id="source-b",
            path="docs/evidence/a-early-path.yaml",
            document_timestamp=ts,
            status_value="beta-status",
        ),
    )
    result = evaluate_group([a, b], project_id="project-atlas", compilation_id="c1")
    assert result.disposition.current_claim_id is None
    assert result.disposition.temporal_status is TemporalStatus.UNRESOLVED
    assert result.disposition.resolution_basis in {
        ResolutionBasis.UNRESOLVED_AMBIGUOUS,
        ResolutionBasis.UNRESOLVED_SAME_SOURCE_MULTI,
    }
    # Lexical claim_id / path must not invent a winner.
    flipped = evaluate_group([b, a], project_id="project-atlas", compilation_id="c1")
    assert flipped.disposition.current_claim_id is None
    assert flipped.disposition.temporal_status is TemporalStatus.UNRESOLVED


def test_ax_tmp_006_staging_partial_does_not_replace_canonical_tip() -> None:
    """AX-TMP-006: PARTIAL/staging-looking newer residual must not displace tip.

    Staging-only / residual observations stay out of tip selection; canonical
    lifecycle tip remains current (INV-002 / INV-004).
    """
    tip = _ctx(
        "claim-tip",
        value="recertified-merge-eligible",
        source_id="source-tip",
        facts=SourceTemporalFacts(
            source_id="source-tip",
            path="docs/evidence/canonical-recert.yaml",
            original_certification="abc",
            status_value="recertified-merge-eligible",
            has_post_merge_signal=True,
            document_timestamp=datetime(2026, 1, 1),
        ),
    )
    prior = _ctx(
        "claim-prior",
        value="certified",
        source_id="source-prior",
        facts=SourceTemporalFacts(
            source_id="source-prior",
            path="docs/evidence/canonical-certified.yaml",
            original_certification="abc",
            status_value="certified",
            document_timestamp=datetime(2025, 6, 1),
        ),
    )
    # Staging residual: later document_timestamp, incompatible value, no supersession.
    staging = _ctx(
        "claim-staging",
        value="staging-candidate-planned",
        source_id="source-staging",
        facts=SourceTemporalFacts(
            source_id="source-staging",
            path="docs/evidence/staging-partial-candidate.yaml",
            status_value="Planned",
            document_timestamp=datetime(2026, 12, 31),
        ),
    )
    without_staging = evaluate_group([prior, tip], project_id="project-atlas", compilation_id="c1")
    with_staging = evaluate_group(
        [prior, tip, staging], project_id="project-atlas", compilation_id="c1"
    )
    assert without_staging.disposition.current_claim_id == "claim-tip"
    # Staging must not become current merely because it looks newer.
    assert with_staging.disposition.current_claim_id != "claim-staging"
    if with_staging.disposition.temporal_status is TemporalStatus.CURRENT:
        assert with_staging.disposition.current_claim_id == "claim-tip"
        assert "claim-staging" in with_staging.disposition.historical_claim_ids
    else:
        # Fail-closed unresolved is acceptable; silent staging win is not.
        assert with_staging.disposition.current_claim_id is None


def test_ax_tmp_010_historical_genesis_not_resurrected_by_authority() -> None:
    """AX-TMP-010: historical genesis stays historical; authority must not resurrect.

    Cross-suite with AS-CORE-006 INV — temporal historical_claim_ids remain
    ineligible for authoritative selection (INV-002 / INV-003).
    """
    historical = _claim(
        "claim-old",
        subject="wp:DEMO",
        field="title",
        value="Durable Name",
        source_id="src-old",
    )
    newer = _claim(
        "claim-new",
        subject="wp:DEMO",
        field="title",
        value="Episode Title",
        source_id="src-new",
    )
    # Temporal layer already marked genesis historical with episode as eligible.
    from project_atlas.domain.temporal import CurrentStateRecord

    disposition = CurrentStateRecord(
        subject="wp:DEMO",
        field="title",
        temporal_status=TemporalStatus.AUTHORITY_PENDING,
        resolution_basis=ResolutionBasis.TITLE_COLLAPSE,
        current_claim_id=None,
        historical_claim_ids=("claim-old",),
        participating_claim_ids=("claim-old", "claim-new"),
        rationale="title collapse; genesis historical under newer episode tip",
        compilation_id="compile-ax-tmp-010",
    )
    result = evaluate_disposition(
        disposition,
        {historical.claim_id: historical, newer.claim_id: newer},
        {
            "src-old": SourceArtifact(
                "src-old",
                "docs/evidence/DEMO-receipt.yaml",
                "package: DEMO\ntitle: Durable Name\nstatus: ok\n",
            ),
            "src-new": SourceArtifact(
                "src-new",
                "docs/evidence/DEMO-governor-remediation-receipt.yaml",
                "package: DEMO\ntitle: Episode Title\nprevious_blocked_candidate: abc\n",
            ),
        },
        compilation_id="compile-ax-tmp-010",
    )
    assert result is not None
    assert "claim-old" in result.temporally_ineligible_claim_ids
    assert result.authoritative_claim_id != "claim-old"
    assert result.authoritative_value != "Durable Name"
    # Historical genesis must not win; episode-only eligible → pending (no role match win).
    assert result.disposition in {
        AuthorityDisposition.AUTHORITY_PENDING,
        AuthorityDisposition.AUTHORITY_CONFLICT,
    }
    assert result.authoritative_claim_id is None
