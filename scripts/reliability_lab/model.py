"""ATLAS_WINDOWS_RELIABILITY_RESULT_V1 -- the per-experiment result contract.

Every experiment is a *controlled* engineering test, not an uncontrolled
fuzzer: it declares its hypothesis, contract, and expected invariant
before running, executes a bounded, seeded, timeout-guarded number of
trials, and reports quantitative outcome -- never a bare PASS/FAIL with no
evidence behind it.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Literal

Verdict = Literal["PASS", "FAIL", "INCONCLUSIVE", "NOT_APPLICABLE"]
EpistemicState = Literal["OBSERVED", "DERIVED", "UNKNOWN"]
FailureClass = Literal[
    "LAB_DEFECT",
    "ATLAS_PRODUCT_DEFECT",
    "WINDOWS_PLATFORM_BEHAVIOR",
    "ENVIRONMENT_CONFIGURATION",
    "UNKNOWN",
]


@dataclass(slots=True)
class LatencyStats:
    """Reliability observations, not a performance benchmark: min/median/
    p95/max over however many samples this run actually produced."""

    samples: int
    min_ms: float | None
    median_ms: float | None
    p95_ms: float | None
    max_ms: float | None

    @classmethod
    def from_samples(cls, values_ms: list[float]) -> LatencyStats:
        if not values_ms:
            return cls(samples=0, min_ms=None, median_ms=None, p95_ms=None, max_ms=None)
        ordered = sorted(values_ms)
        p95_index = min(len(ordered) - 1, max(0, round(0.95 * (len(ordered) - 1))))
        return cls(
            samples=len(ordered),
            min_ms=ordered[0],
            median_ms=statistics.median(ordered),
            p95_ms=ordered[p95_index],
            max_ms=ordered[-1],
        )

    def to_dict(self) -> dict[str, float | int | None]:
        return {
            "samples": self.samples,
            "min_ms": self.min_ms,
            "median_ms": self.median_ms,
            "p95_ms": self.p95_ms,
            "max_ms": self.max_ms,
        }


@dataclass(slots=True)
class FailureArtifact:
    """Enough to reproduce a failure without re-running the whole suite."""

    iteration: int
    error_class: str
    detail: str
    contract_violated: str
    pids: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class ExperimentResult:
    experiment_id: str
    domain: str
    hypothesis: str
    contract: str
    stress_dimension: str
    expected_invariant: str
    seed: int | None
    iterations: int
    successes: int
    failures: int
    timeouts: int
    cleanup_failures: int
    verdict: Verdict
    epistemic_state: EpistemicState
    latency: LatencyStats
    failure_artifacts: list[FailureArtifact] = field(default_factory=list)
    failure_class: FailureClass | None = None
    notes: str = ""

    @property
    def failure_rate(self) -> float | None:
        if self.iterations == 0:
            return None
        return self.failures / self.iterations

    def to_dict(self) -> dict[str, object]:
        return {
            "experiment_id": self.experiment_id,
            "domain": self.domain,
            "hypothesis": self.hypothesis,
            "contract": self.contract,
            "stress_dimension": self.stress_dimension,
            "expected_invariant": self.expected_invariant,
            "seed": self.seed,
            "iterations": self.iterations,
            "successes": self.successes,
            "failures": self.failures,
            "failure_rate": self.failure_rate,
            "timeouts": self.timeouts,
            "cleanup_failures": self.cleanup_failures,
            "verdict": self.verdict,
            "epistemic_state": self.epistemic_state,
            "latency": self.latency.to_dict(),
            "failure_artifacts": [
                {
                    "iteration": a.iteration,
                    "error_class": a.error_class,
                    "detail": a.detail,
                    "contract_violated": a.contract_violated,
                    "pids": a.pids,
                }
                for a in self.failure_artifacts[:10]  # bounded -- evidence, not a full dump
            ],
            "failure_class": self.failure_class,
            "notes": self.notes,
        }


def verdict_from_counts(
    *, iterations: int, failures: int, timeouts: int, allow_zero_iterations: bool = False
) -> Verdict:
    """Never silently promote UNKNOWN/no-data to PASS."""
    if iterations == 0:
        return "NOT_APPLICABLE" if allow_zero_iterations else "INCONCLUSIVE"
    if failures == 0 and timeouts == 0:
        return "PASS"
    return "FAIL"
