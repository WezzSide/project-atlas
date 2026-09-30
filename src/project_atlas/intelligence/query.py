"""AS-2.0-INTEL-003 — deterministic intelligence query contract.

Read-only library query over derived assessments, contradiction
candidates, and project state. Not a LIVE_API route. Not authority.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from enum import StrEnum
from typing import Literal, assert_never

from pydantic import BaseModel, ConfigDict, Field

from project_atlas.domain import Claim
from project_atlas.intelligence.boundary import GENERATED_BY, TRUTH_BOUNDARY_QUERY
from project_atlas.intelligence.contradictions import (
    ContradictionCandidate,
    ContradictionContext,
    find_contradiction_candidates,
)
from project_atlas.intelligence.derived_state import (
    DerivedFact,
    DerivedProjectState,
    StateContext,
    synthesize_project_state,
)
from project_atlas.intelligence.evidence import assess_evidence_many
from project_atlas.intelligence.timewin import IntelligenceTimeError, parse_instant
from project_atlas.intelligence.types import (
    AssessableClaim,
    AssessmentContext,
    EvidenceAssessment,
    SourceObservation,
    ValidityWindowInput,
    coerce_claims,
)


class IntelligenceQueryKind(StrEnum):
    EVIDENCE = "evidence"
    CONFLICTS = "conflicts"
    STATE = "state"
    EXPLAIN = "explain"
    GAPS = "gaps"
    ATTENTION = "attention"
    CHANGE = "change"
    CONTEXT = "context"
    EXPLAIN_GRAPH = "explain-graph"
    GAP_PRIORITY = "gap-priority"
    DEPENDENCIES = "dependencies"
    DECISION = "decision"


class QueryOutcome(StrEnum):
    ANSWER = "answer"
    NONANSWER = "nonanswer"
    INVALID = "invalid"


class SlotStatus(StrEnum):
    OBSERVED = "observed"
    DERIVED = "derived"
    UNKNOWN = "unknown"
    CONTESTED = "contested"
    STALE = "stale"
    NO_EVIDENCE = "no-evidence"
    INVALID = "invalid"


class IntelligenceQuery(BaseModel):
    """Deterministic query. Wall-clock as-of is invalid."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    kind: IntelligenceQueryKind
    subject: str | None = None
    field: str | None = None
    claim_id: str | None = None
    as_of_valid_time: str | None = None


class IntelligenceAnswer(BaseModel):
    """Query result envelope. Payload lists may be empty; empty ≠ healthy."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    schema_version: Literal[1] = 1
    package_id: Literal["AS-2.0-INTEL-003"] = "AS-2.0-INTEL-003"
    query_id: str
    kind: IntelligenceQueryKind
    outcome: QueryOutcome
    status: SlotStatus
    project_id: str
    subject: str | None = None
    field: str | None = None
    claim_id: str | None = None
    as_of_valid_time: str | None = None
    reason: str
    assessments: tuple[EvidenceAssessment, ...] = ()
    candidates: tuple[ContradictionCandidate, ...] = ()
    facts: tuple[DerivedFact, ...] = ()
    state: DerivedProjectState | None = None
    explanation: dict[str, object] | None = None
    gaps: tuple[dict[str, object], ...] = ()
    changes: tuple[dict[str, object], ...] = ()
    risks: tuple[dict[str, object], ...] = ()
    context: dict[str, object] | None = None
    explain_graph: dict[str, object] | None = None
    prioritized_gaps: tuple[dict[str, object], ...] = ()
    dependencies: tuple[dict[str, object], ...] = ()
    decision: dict[str, object] | None = None
    truth_boundary: str
    generated: dict[str, str] = Field(default_factory=lambda: {"by": GENERATED_BY})
    authority_note: Literal["query-not-authoritative"] = "query-not-authoritative"


def query_intelligence(
    query: IntelligenceQuery,
    claims: Sequence[Claim | AssessableClaim],
    *,
    sources: tuple[SourceObservation, ...] | None = None,
    validity_windows: tuple[ValidityWindowInput, ...] = (),
    identity_ambiguous_claim_ids: tuple[str, ...] = (),
) -> IntelligenceAnswer:
    """Answer a derived-intelligence query. Never writes. Never invents as-of."""
    query_id = _query_id(query)
    if not query.project_id.strip():
        return _invalid(query, query_id, "project_id-required")
    if query.as_of_valid_time is not None:
        try:
            parse_instant(query.as_of_valid_time, field="as-of")
        except IntelligenceTimeError as exc:
            return _invalid(query, query_id, str(exc))

    scoped = tuple(
        item for item in coerce_claims(claims) if item.project_id == query.project_id
    )
    if query.claim_id:
        scoped = tuple(item for item in scoped if item.claim_id == query.claim_id)
    if query.subject:
        scoped = tuple(item for item in scoped if item.subject == query.subject)
    if query.field:
        scoped = tuple(item for item in scoped if item.field == query.field)

    if query.kind is IntelligenceQueryKind.EVIDENCE:
        return _answer_evidence(query, query_id, scoped, sources, validity_windows)
    if query.kind is IntelligenceQueryKind.CONFLICTS:
        return _answer_conflicts(
            query, query_id, scoped, sources, validity_windows, identity_ambiguous_claim_ids
        )
    if query.kind is IntelligenceQueryKind.STATE:
        return _answer_state(
            query, query_id, claims, sources, validity_windows, identity_ambiguous_claim_ids
        )
    if query.kind is IntelligenceQueryKind.EXPLAIN:
        from project_atlas.intelligence.explain import explanation_for_query

        trace, status, reason, assessments = explanation_for_query(
            query,
            scoped,
            sources=sources,
            validity_windows=validity_windows,
            identity_ambiguous_claim_ids=identity_ambiguous_claim_ids,
        )
        return IntelligenceAnswer(
            query_id=query_id,
            kind=query.kind,
            outcome=QueryOutcome.ANSWER if scoped else QueryOutcome.NONANSWER,
            status=status,
            project_id=query.project_id,
            subject=query.subject,
            field=query.field,
            claim_id=query.claim_id,
            as_of_valid_time=query.as_of_valid_time,
            reason=reason,
            assessments=assessments,
            explanation=trace.model_dump(),
            truth_boundary=TRUTH_BOUNDARY_QUERY,
        )
    if query.kind is IntelligenceQueryKind.ATTENTION:
        return _answer_attention(
            query, query_id, scoped, sources, validity_windows, identity_ambiguous_claim_ids
        )
    if query.kind is IntelligenceQueryKind.CHANGE:
        return _answer_change(query, query_id, scoped, validity_windows)
    if query.kind is IntelligenceQueryKind.CONTEXT:
        return _answer_context(
            query, query_id, scoped, sources, validity_windows, identity_ambiguous_claim_ids
        )
    if query.kind is IntelligenceQueryKind.GAPS:
        from project_atlas.intelligence.gaps import gaps_for_query

        found, status, reason = gaps_for_query(
            query,
            scoped,
            sources=sources,
            validity_windows=validity_windows,
            identity_ambiguous_claim_ids=identity_ambiguous_claim_ids,
        )
        return IntelligenceAnswer(
            query_id=query_id,
            kind=query.kind,
            outcome=QueryOutcome.ANSWER if found else QueryOutcome.NONANSWER,
            status=status,
            project_id=query.project_id,
            subject=query.subject,
            field=query.field,
            claim_id=query.claim_id,
            as_of_valid_time=query.as_of_valid_time,
            reason=reason,
            gaps=tuple(item.model_dump() for item in found),
            truth_boundary=TRUTH_BOUNDARY_QUERY,
        )
    if query.kind is IntelligenceQueryKind.EXPLAIN_GRAPH:
        return _answer_explain_graph(
            query, query_id, scoped, sources, validity_windows, identity_ambiguous_claim_ids
        )
    if query.kind is IntelligenceQueryKind.GAP_PRIORITY:
        return _answer_gap_priority(
            query, query_id, scoped, sources, validity_windows, identity_ambiguous_claim_ids
        )
    if query.kind is IntelligenceQueryKind.DEPENDENCIES:
        return _answer_dependencies(query, query_id, scoped)
    if query.kind is IntelligenceQueryKind.DECISION:
        return _answer_decision(
            query, query_id, scoped, sources, validity_windows, identity_ambiguous_claim_ids
        )
    assert_never(query.kind)


def _answer_evidence(
    query: IntelligenceQuery,
    query_id: str,
    scoped: tuple[AssessableClaim, ...],
    sources: tuple[SourceObservation, ...] | None,
    validity_windows: tuple[ValidityWindowInput, ...],
) -> IntelligenceAnswer:
    if not scoped:
        return _nonanswer(query, query_id, SlotStatus.NO_EVIDENCE, "no-matching-claims")
    assessments = assess_evidence_many(
        scoped,
        AssessmentContext(
            as_of_valid_time=query.as_of_valid_time,
            sources=sources,
            peer_claims=scoped,
            validity_windows=validity_windows,
        ),
    )
    status = _status_from_assessments(assessments)
    return IntelligenceAnswer(
        query_id=query_id,
        kind=query.kind,
        outcome=QueryOutcome.ANSWER,
        status=status,
        project_id=query.project_id,
        subject=query.subject,
        field=query.field,
        claim_id=query.claim_id,
        as_of_valid_time=query.as_of_valid_time,
        reason="evidence-assessments-derived-from-matching-claims",
        assessments=assessments,
        truth_boundary=TRUTH_BOUNDARY_QUERY,
    )


def _answer_conflicts(
    query: IntelligenceQuery,
    query_id: str,
    scoped: tuple[AssessableClaim, ...],
    sources: tuple[SourceObservation, ...] | None,
    validity_windows: tuple[ValidityWindowInput, ...],
    identity_ambiguous_claim_ids: tuple[str, ...],
) -> IntelligenceAnswer:
    candidates = find_contradiction_candidates(
        scoped,
        ContradictionContext(
            as_of_valid_time=query.as_of_valid_time,
            validity_windows=validity_windows,
            sources=sources,
            identity_ambiguous_claim_ids=identity_ambiguous_claim_ids,
        ),
    )
    if not scoped:
        return _nonanswer(query, query_id, SlotStatus.NO_EVIDENCE, "no-matching-claims")
    status = SlotStatus.CONTESTED if candidates else SlotStatus.UNKNOWN
    reason = (
        "contradiction-candidates-present"
        if candidates
        else "no-contradiction-candidates-not-proven-consistency"
    )
    return IntelligenceAnswer(
        query_id=query_id,
        kind=query.kind,
        outcome=QueryOutcome.ANSWER,
        status=status,
        project_id=query.project_id,
        subject=query.subject,
        field=query.field,
        claim_id=query.claim_id,
        as_of_valid_time=query.as_of_valid_time,
        reason=reason,
        candidates=candidates,
        truth_boundary=TRUTH_BOUNDARY_QUERY,
    )


def _answer_state(
    query: IntelligenceQuery,
    query_id: str,
    claims: Sequence[Claim | AssessableClaim],
    sources: tuple[SourceObservation, ...] | None,
    validity_windows: tuple[ValidityWindowInput, ...],
    identity_ambiguous_claim_ids: tuple[str, ...],
) -> IntelligenceAnswer:
    state = synthesize_project_state(
        query.project_id,
        claims,
        StateContext(
            as_of_valid_time=query.as_of_valid_time,
            sources=sources,
            validity_windows=validity_windows,
            identity_ambiguous_claim_ids=identity_ambiguous_claim_ids,
        ),
    )
    facts = state.known_facts + state.unknown_facts + state.stale_facts + state.contested_facts
    if query.subject:
        facts = tuple(item for item in facts if item.subject == query.subject)
    if query.field:
        facts = tuple(item for item in facts if item.field == query.field)
    status = _status_from_facts(facts) if facts else SlotStatus.NO_EVIDENCE
    return IntelligenceAnswer(
        query_id=query_id,
        kind=query.kind,
        outcome=QueryOutcome.ANSWER if facts else QueryOutcome.NONANSWER,
        status=status,
        project_id=query.project_id,
        subject=query.subject,
        field=query.field,
        claim_id=query.claim_id,
        as_of_valid_time=query.as_of_valid_time,
        reason="derived-project-state-filtered-to-query-scope",
        facts=facts,
        state=state,
        truth_boundary=TRUTH_BOUNDARY_QUERY,
    )


def _answer_attention(
    query: IntelligenceQuery,
    query_id: str,
    scoped: tuple[AssessableClaim, ...],
    sources: tuple[SourceObservation, ...] | None,
    validity_windows: tuple[ValidityWindowInput, ...],
    identity_ambiguous_claim_ids: tuple[str, ...],
) -> IntelligenceAnswer:
    from project_atlas.intelligence.risk import detect_risk_signals

    signals = detect_risk_signals(
        query.project_id,
        scoped,
        sources=sources,
        validity_windows=validity_windows,
        identity_ambiguous_claim_ids=identity_ambiguous_claim_ids,
        as_of_valid_time=query.as_of_valid_time,
    )
    status = SlotStatus.NO_EVIDENCE if not scoped else SlotStatus.UNKNOWN
    if any(item.risk_class.value == "attention" for item in signals):
        status = SlotStatus.CONTESTED
    return IntelligenceAnswer(
        query_id=query_id,
        kind=query.kind,
        outcome=QueryOutcome.ANSWER if signals else QueryOutcome.NONANSWER,
        status=status,
        project_id=query.project_id,
        subject=query.subject,
        field=query.field,
        claim_id=query.claim_id,
        as_of_valid_time=query.as_of_valid_time,
        reason="risk-signals-are-not-facts",
        risks=tuple(item.model_dump() for item in signals),
        truth_boundary=TRUTH_BOUNDARY_QUERY,
    )


def _answer_change(
    query: IntelligenceQuery,
    query_id: str,
    scoped: tuple[AssessableClaim, ...],
    validity_windows: tuple[ValidityWindowInput, ...],
) -> IntelligenceAnswer:
    from project_atlas.intelligence.change import detect_semantic_changes

    found = detect_semantic_changes(scoped, validity_windows=validity_windows)
    if query.subject:
        found = tuple(item for item in found if item.subject == query.subject)
    if query.field:
        found = tuple(item for item in found if item.field == query.field)
    return IntelligenceAnswer(
        query_id=query_id,
        kind=query.kind,
        outcome=QueryOutcome.ANSWER if found else QueryOutcome.NONANSWER,
        status=SlotStatus.DERIVED if found else SlotStatus.NO_EVIDENCE,
        project_id=query.project_id,
        subject=query.subject,
        field=query.field,
        claim_id=query.claim_id,
        as_of_valid_time=query.as_of_valid_time,
        reason="semantic-change-is-not-regression",
        changes=tuple(item.model_dump() for item in found),
        truth_boundary=TRUTH_BOUNDARY_QUERY,
    )


def _answer_context(
    query: IntelligenceQuery,
    query_id: str,
    scoped: tuple[AssessableClaim, ...],
    sources: tuple[SourceObservation, ...] | None,
    validity_windows: tuple[ValidityWindowInput, ...],
    identity_ambiguous_claim_ids: tuple[str, ...],
) -> IntelligenceAnswer:
    from project_atlas.intelligence.agent_context import compose_agent_context

    context = compose_agent_context(
        query.project_id,
        scoped,
        sources=sources,
        validity_windows=validity_windows,
        identity_ambiguous_claim_ids=identity_ambiguous_claim_ids,
        as_of_valid_time=query.as_of_valid_time,
    )
    status = SlotStatus.NO_EVIDENCE if not scoped else SlotStatus.DERIVED
    if context.contested_facts:
        status = SlotStatus.CONTESTED
    elif context.unknown_facts and not context.known_facts:
        status = SlotStatus.UNKNOWN
    return IntelligenceAnswer(
        query_id=query_id,
        kind=query.kind,
        outcome=QueryOutcome.ANSWER,
        status=status,
        project_id=query.project_id,
        subject=query.subject,
        field=query.field,
        claim_id=query.claim_id,
        as_of_valid_time=query.as_of_valid_time,
        reason="derived-agent-context-is-not-authority",
        context=context.model_dump(),
        truth_boundary=TRUTH_BOUNDARY_QUERY,
    )


def _answer_explain_graph(
    query: IntelligenceQuery,
    query_id: str,
    scoped: tuple[AssessableClaim, ...],
    sources: tuple[SourceObservation, ...] | None,
    validity_windows: tuple[ValidityWindowInput, ...],
    identity_ambiguous_claim_ids: tuple[str, ...],
) -> IntelligenceAnswer:
    from project_atlas.intelligence.explain_graph import build_explanation_graph

    graph = build_explanation_graph(
        query.project_id,
        scoped,
        sources=sources,
        validity_windows=validity_windows,
        identity_ambiguous_claim_ids=identity_ambiguous_claim_ids,
        as_of_valid_time=query.as_of_valid_time,
    )
    return IntelligenceAnswer(
        query_id=query_id,
        kind=query.kind,
        outcome=QueryOutcome.ANSWER if graph.nodes else QueryOutcome.NONANSWER,
        status=SlotStatus.DERIVED if graph.nodes else SlotStatus.NO_EVIDENCE,
        project_id=query.project_id,
        subject=query.subject,
        field=query.field,
        claim_id=query.claim_id,
        as_of_valid_time=query.as_of_valid_time,
        reason="explanation-graph-is-not-authority",
        explain_graph=graph.model_dump(),
        truth_boundary=TRUTH_BOUNDARY_QUERY,
    )


def _answer_gap_priority(
    query: IntelligenceQuery,
    query_id: str,
    scoped: tuple[AssessableClaim, ...],
    sources: tuple[SourceObservation, ...] | None,
    validity_windows: tuple[ValidityWindowInput, ...],
    identity_ambiguous_claim_ids: tuple[str, ...],
) -> IntelligenceAnswer:
    from project_atlas.intelligence.gap_priority import prioritize_evidence_gaps

    found = prioritize_evidence_gaps(
        query.project_id,
        scoped,
        sources=sources,
        validity_windows=validity_windows,
        identity_ambiguous_claim_ids=identity_ambiguous_claim_ids,
        as_of_valid_time=query.as_of_valid_time,
    )
    return IntelligenceAnswer(
        query_id=query_id,
        kind=query.kind,
        outcome=QueryOutcome.ANSWER if found else QueryOutcome.NONANSWER,
        status=SlotStatus.UNKNOWN if found else SlotStatus.NO_EVIDENCE,
        project_id=query.project_id,
        subject=query.subject,
        field=query.field,
        claim_id=query.claim_id,
        as_of_valid_time=query.as_of_valid_time,
        reason="gap-priority-is-not-a-score",
        prioritized_gaps=tuple(item.model_dump() for item in found),
        truth_boundary=TRUTH_BOUNDARY_QUERY,
    )


def _answer_dependencies(
    query: IntelligenceQuery,
    query_id: str,
    scoped: tuple[AssessableClaim, ...],
) -> IntelligenceAnswer:
    from project_atlas.intelligence.dependencies import detect_project_dependencies

    found = detect_project_dependencies(query.project_id, scoped)
    return IntelligenceAnswer(
        query_id=query_id,
        kind=query.kind,
        outcome=QueryOutcome.ANSWER if found else QueryOutcome.NONANSWER,
        status=SlotStatus.OBSERVED if found else SlotStatus.NO_EVIDENCE,
        project_id=query.project_id,
        subject=query.subject,
        field=query.field,
        claim_id=query.claim_id,
        as_of_valid_time=query.as_of_valid_time,
        reason="dependency-is-not-inferred",
        dependencies=tuple(item.model_dump() for item in found),
        truth_boundary=TRUTH_BOUNDARY_QUERY,
    )


def _answer_decision(
    query: IntelligenceQuery,
    query_id: str,
    scoped: tuple[AssessableClaim, ...],
    sources: tuple[SourceObservation, ...] | None,
    validity_windows: tuple[ValidityWindowInput, ...],
    identity_ambiguous_claim_ids: tuple[str, ...],
) -> IntelligenceAnswer:
    from project_atlas.intelligence.decision import compose_decision_candidate

    candidate = compose_decision_candidate(
        query.project_id,
        scoped,
        sources=sources,
        validity_windows=validity_windows,
        identity_ambiguous_claim_ids=identity_ambiguous_claim_ids,
        as_of_valid_time=query.as_of_valid_time,
    )
    status = SlotStatus.CONTESTED if candidate.conflicts else SlotStatus.UNKNOWN
    if not scoped:
        status = SlotStatus.NO_EVIDENCE
    return IntelligenceAnswer(
        query_id=query_id,
        kind=query.kind,
        outcome=QueryOutcome.ANSWER,
        status=status,
        project_id=query.project_id,
        subject=query.subject,
        field=query.field,
        claim_id=query.claim_id,
        as_of_valid_time=query.as_of_valid_time,
        reason="decision-candidate-is-not-a-command",
        decision=candidate.model_dump(),
        truth_boundary=TRUTH_BOUNDARY_QUERY,
    )


def _status_from_assessments(items: tuple[EvidenceAssessment, ...]) -> SlotStatus:
    factors = {factor.value for item in items for factor in item.limiting_factors}
    if "contradictory-evidence" in factors:
        return SlotStatus.CONTESTED
    if "temporal-stale" in factors:
        return SlotStatus.STALE
    if any(item.confidence_class.value == "unknown" for item in items):
        return SlotStatus.UNKNOWN
    if len(items) == 1:
        return SlotStatus.OBSERVED
    return SlotStatus.DERIVED


def _status_from_facts(facts: tuple[DerivedFact, ...]) -> SlotStatus:
    if any(item.status.value == "contested" for item in facts):
        return SlotStatus.CONTESTED
    if any(item.status.value == "stale" for item in facts):
        return SlotStatus.STALE
    if any(item.status.value == "unknown" for item in facts):
        return SlotStatus.UNKNOWN
    if any(item.status.value == "derived" for item in facts):
        return SlotStatus.DERIVED
    return SlotStatus.OBSERVED


def _query_id(query: IntelligenceQuery) -> str:
    material = "|".join(
        (
            query.project_id,
            query.kind.value,
            query.subject or "",
            query.field or "",
            query.claim_id or "",
            query.as_of_valid_time or "",
        )
    )
    return "iq-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def _invalid(query: IntelligenceQuery, query_id: str, reason: str) -> IntelligenceAnswer:
    return IntelligenceAnswer(
        query_id=query_id,
        kind=query.kind,
        outcome=QueryOutcome.INVALID,
        status=SlotStatus.INVALID,
        project_id=query.project_id,
        subject=query.subject,
        field=query.field,
        claim_id=query.claim_id,
        as_of_valid_time=query.as_of_valid_time,
        reason=reason,
        truth_boundary=TRUTH_BOUNDARY_QUERY,
    )


def _nonanswer(
    query: IntelligenceQuery,
    query_id: str,
    status: SlotStatus,
    reason: str,
) -> IntelligenceAnswer:
    return IntelligenceAnswer(
        query_id=query_id,
        kind=query.kind,
        outcome=QueryOutcome.NONANSWER,
        status=status,
        project_id=query.project_id,
        subject=query.subject,
        field=query.field,
        claim_id=query.claim_id,
        as_of_valid_time=query.as_of_valid_time,
        reason=reason,
        truth_boundary=TRUTH_BOUNDARY_QUERY,
    )
