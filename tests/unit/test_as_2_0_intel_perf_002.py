"""AS-2.0-INTEL-PERF-002 — candidate-build optimization without semantic change."""

from __future__ import annotations

import time

import pytest

from project_atlas.domain import (
    AuthorityLevel,
    Claim,
    ClaimLifecycle,
    ClaimType,
    ConfidenceState,
    ProvenanceReference,
    ReviewState,
)
from project_atlas.intelligence.contradictions import find_contradiction_candidates_report

HASH_A = "a" * 64


def _claim(index: int, *, groups: int) -> Claim:
    group = index % groups
    return Claim(
        claim_id=f"claim-{index:05d}",
        project_id="harbor-api",
        subject="project:harbor-api",
        claim_type=ClaimType.ARCHITECTURE,
        field=f"field-{group:04d}",
        value=f"value-{index % 3}",
        provenance=[
            ProvenanceReference(
                source_id=f"src-{index % 17}",
                resource=f"docs/src-{index % 17}.md",
                sha256=HASH_A,
            )
        ],
        authority=AuthorityLevel.MAINTAINED,
        confidence=ConfidenceState.MEDIUM,
        lifecycle=ClaimLifecycle.NEW,
        verification=ReviewState.UNREVIEWED,
    )


def test_materialize_false_preserves_counts_not_objects() -> None:
    claims = [_claim(index, groups=5) for index in range(60)]
    built, built_stats = find_contradiction_candidates_report(claims)
    empty, count_stats = find_contradiction_candidates_report(claims, materialize=False)
    assert empty == ()
    assert count_stats.candidate_count == built_stats.candidate_count == len(built)
    assert count_stats.pair_evaluations == built_stats.pair_evaluations
    assert count_stats.skipped_same_value == built_stats.skipped_same_value


def test_ids_remain_deterministic_after_build_optimization() -> None:
    claims = [_claim(index, groups=8) for index in range(80)]
    first, _stats = find_contradiction_candidates_report(claims)
    second, _again = find_contradiction_candidates_report(list(reversed(claims)))
    assert [item.candidate_id for item in first] == [item.candidate_id for item in second]


@pytest.mark.product_perf
def test_dense_10k_and_representative_100k_counts() -> None:
    dense = [_claim(index, groups=50) for index in range(10000)]
    started = time.perf_counter()
    _candidates, dense_stats = find_contradiction_candidates_report(dense)
    dense_elapsed = time.perf_counter() - started
    assert dense_stats.group_count == 50
    assert dense_elapsed < 20.0

    representative = [_claim(index, groups=20000) for index in range(100000)]
    started = time.perf_counter()
    _empty, wide_stats = find_contradiction_candidates_report(representative, materialize=False)
    wide_elapsed = time.perf_counter() - started
    assert wide_stats.claim_count == 100000
    assert wide_stats.group_count == 20000
    assert wide_elapsed < 15.0
