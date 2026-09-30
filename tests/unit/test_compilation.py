"""Unit tests for the compilation outcome state machine (AS-EXT-001A, §7.8)."""

from __future__ import annotations

import dataclasses
from itertools import pairwise

import pytest

from project_atlas.compilation import (
    TERMINAL_OUTCOMES,
    CompilationCandidate,
    CompilationOutcome,
    alters_canonical_state,
    appears_complete,
    compilation_transition_allowed,
    may_promote,
    triggers_lifecycle_promotion,
    validate_compilation_transition,
)


def test_required_flow_edges() -> None:
    """The exact §7.8 flow must be traversable for both endings."""
    flow_to_complete = [
        CompilationOutcome.START,
        CompilationOutcome.DISCOVERING,
        CompilationOutcome.EXTRACTING,
        CompilationOutcome.VALIDATING_CANDIDATE,
        CompilationOutcome.COMPLETE_CANDIDATE,
        CompilationOutcome.PROMOTING,
        CompilationOutcome.COMPLETE,
    ]
    for previous, current in pairwise(flow_to_complete):
        validate_compilation_transition(previous, current)

    flow_to_partial = [
        CompilationOutcome.START,
        CompilationOutcome.DISCOVERING,
        CompilationOutcome.EXTRACTING,
        CompilationOutcome.VALIDATING_CANDIDATE,
        CompilationOutcome.PARTIAL_CANDIDATE,
    ]
    for previous, current in pairwise(flow_to_partial):
        validate_compilation_transition(previous, current)

    assert compilation_transition_allowed(
        CompilationOutcome.VALIDATING_CANDIDATE, CompilationOutcome.FAILED
    )
    assert compilation_transition_allowed(
        CompilationOutcome.PROMOTING, CompilationOutcome.PROMOTION_FAILED
    )


def test_invalid_edges_rejected() -> None:
    invalid = [
        (CompilationOutcome.START, CompilationOutcome.COMPLETE),
        (CompilationOutcome.START, CompilationOutcome.EXTRACTING),
        (CompilationOutcome.DISCOVERING, CompilationOutcome.VALIDATING_CANDIDATE),
        (CompilationOutcome.EXTRACTING, CompilationOutcome.PROMOTING),
        # PROMOTING is reachable only from COMPLETE_CANDIDATE.
        (CompilationOutcome.PARTIAL_CANDIDATE, CompilationOutcome.PROMOTING),
        (CompilationOutcome.FAILED, CompilationOutcome.PROMOTING),
        (CompilationOutcome.VALIDATING_CANDIDATE, CompilationOutcome.PROMOTING),
        # Terminal states have no outgoing edges.
        (CompilationOutcome.COMPLETE, CompilationOutcome.START),
        (CompilationOutcome.PARTIAL_CANDIDATE, CompilationOutcome.START),
        (CompilationOutcome.FAILED, CompilationOutcome.DISCOVERING),
        (CompilationOutcome.PROMOTION_FAILED, CompilationOutcome.PROMOTING),
        (CompilationOutcome.PROMOTING, CompilationOutcome.PARTIAL_CANDIDATE),
    ]
    for previous, current in invalid:
        assert not compilation_transition_allowed(previous, current)
        with pytest.raises(ValueError, match="invalid compilation outcome transition"):
            validate_compilation_transition(previous, current)


def test_terminal_outcome_set() -> None:
    assert (
        frozenset(
            {
                CompilationOutcome.COMPLETE,
                CompilationOutcome.PARTIAL_CANDIDATE,
                CompilationOutcome.FAILED,
                CompilationOutcome.PROMOTION_FAILED,
            }
        )
        == TERMINAL_OUTCOMES
    )
    for outcome in TERMINAL_OUTCOMES:
        for target in CompilationOutcome:
            assert not compilation_transition_allowed(outcome, target)


def test_promotion_only_from_complete_candidate() -> None:
    for outcome in CompilationOutcome:
        expected = outcome is CompilationOutcome.COMPLETE_CANDIDATE
        assert may_promote(outcome) is expected, outcome


def test_partial_candidate_guarantees() -> None:
    """§7.8: PARTIAL_CANDIDATE is staging-only and never appears complete."""
    outcome = CompilationOutcome.PARTIAL_CANDIDATE
    assert not may_promote(outcome)
    assert not alters_canonical_state(outcome)
    assert not triggers_lifecycle_promotion(outcome)
    assert not appears_complete(outcome)


def test_failed_means_no_promotable_candidate() -> None:
    outcome = CompilationOutcome.FAILED
    assert not may_promote(outcome)
    assert not alters_canonical_state(outcome)
    assert not triggers_lifecycle_promotion(outcome)
    assert not appears_complete(outcome)


def test_promotion_failed_leaves_canonical_state_unchanged() -> None:
    """§7.8: PROMOTION_FAILED keeps canonical state; OCC rollback stays authoritative."""
    outcome = CompilationOutcome.PROMOTION_FAILED
    assert not alters_canonical_state(outcome)
    assert not triggers_lifecycle_promotion(outcome)
    assert not appears_complete(outcome)


def test_complete_is_the_only_complete_appearance() -> None:
    for outcome in CompilationOutcome:
        assert appears_complete(outcome) is (outcome is CompilationOutcome.COMPLETE)
        assert alters_canonical_state(outcome) is (outcome is CompilationOutcome.COMPLETE)


def test_candidate_immutability() -> None:
    candidate = CompilationCandidate(
        source_path="docs/evidence/example.yaml",
        outcome=CompilationOutcome.COMPLETE_CANDIDATE,
        claims_extracted=2,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        candidate.claims_extracted = 5  # type: ignore[misc]


def test_partial_candidate_requires_diagnostics() -> None:
    with pytest.raises(ValueError, match="PARTIAL_CANDIDATE requires at least one diagnostic"):
        CompilationCandidate(
            source_path="docs/plan.md",
            outcome=CompilationOutcome.PARTIAL_CANDIDATE,
            claims_withheld=1,
        )


def test_complete_candidate_must_not_withhold_claims() -> None:
    with pytest.raises(ValueError, match="COMPLETE_CANDIDATE must not withhold claims"):
        CompilationCandidate(
            source_path="docs/evidence/example.yaml",
            outcome=CompilationOutcome.COMPLETE_CANDIDATE,
            claims_extracted=1,
            claims_withheld=1,
        )


def test_negative_counters_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        CompilationCandidate(
            source_path="docs/evidence/example.yaml",
            outcome=CompilationOutcome.FAILED,
            claims_extracted=-1,
        )


def test_candidate_promotable_and_canonical_impact() -> None:
    complete = CompilationCandidate(
        source_path="docs/evidence/example.yaml",
        outcome=CompilationOutcome.COMPLETE_CANDIDATE,
        claims_extracted=3,
    )
    assert complete.promotable
    assert complete.canonical_impact == "none"  # not yet promoted

    promoted = CompilationCandidate(
        source_path="docs/evidence/example.yaml",
        outcome=CompilationOutcome.COMPLETE,
        claims_extracted=3,
    )
    assert promoted.canonical_impact == "promoted"

    partial = CompilationCandidate(
        source_path="docs/plan.md",
        outcome=CompilationOutcome.PARTIAL_CANDIDATE,
        claims_extracted=1,
        claims_withheld=1,
        diagnostics=("withheld: ambiguous heading locator",),
    )
    assert not partial.promotable
    assert partial.canonical_impact == "none"


def test_outcome_values_stable() -> None:
    """Serialization stability: outcome values are part of candidate output."""
    assert [outcome.value for outcome in CompilationOutcome] == [
        "START",
        "DISCOVERING",
        "EXTRACTING",
        "VALIDATING_CANDIDATE",
        "COMPLETE_CANDIDATE",
        "PARTIAL_CANDIDATE",
        "FAILED",
        "PROMOTING",
        "COMPLETE",
        "PROMOTION_FAILED",
    ]
