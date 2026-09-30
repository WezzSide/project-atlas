"""AS-INTEL-MIXED-UNKNOWN-001 — mixed UNKNOWN is not observed.

``_status_from_facts`` / ``_status_from_assessments`` treated a HIGH/observed
fact plus an UNKNOWN sibling as top-level ``observed`` / ``derived``.
UNKNOWN must remain UNKNOWN.
"""

from __future__ import annotations

from project_atlas.intelligence.derived_state import DerivedFact, FactStatus
from project_atlas.intelligence.query import (
    SlotStatus,
    _status_from_assessments,
    _status_from_facts,
)
from project_atlas.intelligence.types import ConfidenceClass, EvidenceAssessment


def _fact(status: FactStatus, fact_id: str) -> DerivedFact:
    return DerivedFact.model_construct(
        fact_id=fact_id,
        project_id="harbor-api",
        subject="project:harbor-api",
        field="datastore",
        status=status,
        confidence_class=(
            ConfidenceClass.UNKNOWN
            if status is FactStatus.UNKNOWN
            else ConfidenceClass.HIGH
        ),
        claim_ids=(),
        evidence_refs=(),
        limiting_factors=(),
        why="test",
    )


def test_mixed_observed_and_unknown_facts_stay_unknown() -> None:
    mixed = (
        _fact(FactStatus.OBSERVED, "a"),
        _fact(FactStatus.UNKNOWN, "b"),
    )
    assert _status_from_facts(mixed) is SlotStatus.UNKNOWN


def test_all_observed_facts_remain_observed() -> None:
    assert (
        _status_from_facts((_fact(FactStatus.OBSERVED, "a"),))
        is SlotStatus.OBSERVED
    )


def test_mixed_high_and_unknown_assessments_stay_unknown() -> None:
    high = EvidenceAssessment.model_construct(
        assessment_id="ea-high",
        claim_id="claim-a",
        project_id="harbor-api",
        confidence_class=ConfidenceClass.HIGH,
        limiting_factors=(),
    )
    unknown = EvidenceAssessment.model_construct(
        assessment_id="ea-unk",
        claim_id="claim-b",
        project_id="harbor-api",
        confidence_class=ConfidenceClass.UNKNOWN,
        limiting_factors=(),
    )
    assert _status_from_assessments((high, unknown)) is SlotStatus.UNKNOWN
