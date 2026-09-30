"""AS-2.0-GAP-002 — discrete evidence-gap prioritization.

Priority is a classification, not a numeric score and not a fact.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from project_atlas.domain import Claim
from project_atlas.intelligence.boundary import (
    GAP_PRIORITY_IS_FACT,
    GENERATED_BY,
    TRUTH_BOUNDARY_GAP_PRIORITY,
)
from project_atlas.intelligence.gaps import GapClass, GapCurrentStatus, detect_evidence_gaps
from project_atlas.intelligence.query import IntelligenceQuery, IntelligenceQueryKind
from project_atlas.intelligence.types import (
    AssessableClaim,
    SourceObservation,
    ValidityWindowInput,
    coerce_claims,
)

_CLASS_ORDER = {
    "blocking-unknown": 0,
    "contested-core-fact": 1,
    "stale-high-relevance": 2,
    "missing-provenance": 3,
    "authority-gap": 4,
    "temporal-gap": 5,
    "low-relevance": 6,
}


class GapPriorityClass(StrEnum):
    BLOCKING_UNKNOWN = "blocking-unknown"
    CONTESTED_CORE_FACT = "contested-core-fact"
    STALE_HIGH_RELEVANCE = "stale-high-relevance"
    MISSING_PROVENANCE = "missing-provenance"
    AUTHORITY_GAP = "authority-gap"
    TEMPORAL_GAP = "temporal-gap"
    LOW_RELEVANCE = "low-relevance"


class PrioritizedGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    package_id: Literal["AS-2.0-GAP-002"] = "AS-2.0-GAP-002"
    priority_id: str
    gap_id: str
    priority_class: GapPriorityClass
    reason: str
    numeric_score: None = None
    truth_boundary: str
    generated: dict[str, str] = Field(default_factory=lambda: {"by": GENERATED_BY})
    authority_note: Literal["priority-not-fact"] = "priority-not-fact"


def prioritize_evidence_gaps(
    project_id: str,
    claims: Sequence[Claim | AssessableClaim],
    *,
    sources: tuple[SourceObservation, ...] | None = None,
    validity_windows: tuple[ValidityWindowInput, ...] = (),
    identity_ambiguous_claim_ids: tuple[str, ...] = (),
    as_of_valid_time: str | None = None,
) -> tuple[PrioritizedGap, ...]:
    """Classify gaps into discrete priority classes. Never scores."""
    if GAP_PRIORITY_IS_FACT != "NO":
        raise RuntimeError("gap-priority-fact-flag-broken")
    source = project_id.strip()
    if not source:
        raise ValueError("project_id is required")
    scoped = tuple(item for item in coerce_claims(claims) if item.project_id == source)
    gaps = detect_evidence_gaps(
        IntelligenceQuery(
            project_id=source,
            kind=IntelligenceQueryKind.GAPS,
            as_of_valid_time=as_of_valid_time,
        ),
        scoped,
        sources=sources,
        validity_windows=validity_windows,
        identity_ambiguous_claim_ids=identity_ambiguous_claim_ids,
    )
    found: list[PrioritizedGap] = []
    for gap in gaps:
        priority, reason = _classify(gap.gap_class, gap.current_status)
        material = "|".join((gap.gap_id, priority.value, reason))
        found.append(
            PrioritizedGap(
                priority_id="gpr-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20],
                gap_id=gap.gap_id,
                priority_class=priority,
                reason=reason,
                truth_boundary=TRUTH_BOUNDARY_GAP_PRIORITY,
            )
        )
    found.sort(key=lambda item: (_CLASS_ORDER[item.priority_class.value], item.priority_id))
    return tuple(found)


def _classify(
    gap_class: GapClass,
    status: GapCurrentStatus,
) -> tuple[GapPriorityClass, str]:
    if status is GapCurrentStatus.UNKNOWN_FROM_NO_EVIDENCE:
        return GapPriorityClass.BLOCKING_UNKNOWN, "unknown-from-no-evidence-blocks-conclusion"
    if status is GapCurrentStatus.CONTESTED:
        return GapPriorityClass.CONTESTED_CORE_FACT, "contested-core-fact-is-not-resolved"
    if status is GapCurrentStatus.STALE:
        return GapPriorityClass.STALE_HIGH_RELEVANCE, "stale-is-not-invalid-but-needs-refresh"
    if gap_class is GapClass.MISSING_PROVENANCE:
        return GapPriorityClass.MISSING_PROVENANCE, "missing-provenance-limits-confidence"
    if gap_class is GapClass.MISSING_AUTHORITY:
        return GapPriorityClass.AUTHORITY_GAP, "authority-gap-is-not-a-score"
    if gap_class is GapClass.MISSING_VALIDITY_WINDOW:
        return GapPriorityClass.TEMPORAL_GAP, "temporal-gap-is-not-fabricated-history"
    return GapPriorityClass.LOW_RELEVANCE, "remaining-gap-without-a-higher-class"
