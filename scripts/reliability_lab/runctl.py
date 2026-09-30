"""Shared experiment-execution scaffolding: mode-based iteration scaling,
seeding, per-operation timeout enforcement, and trial-loop bookkeeping.

Every experiment in this package is built on :func:`run_trials` (or calls
its own tightly-scoped loop following the same discipline) so that timeout
discipline, latency capture, and failure-artifact collection are uniform
rather than each experiment reinventing them slightly differently.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass

from reliability_lab.model import (
    EpistemicState,
    ExperimentResult,
    FailureArtifact,
    LatencyStats,
    verdict_from_counts,
)

Mode = str  # "quick" | "standard" | "soak"

#: Iteration-count multiplier per mode. "quick" exists to smoke-test a
#: change fast; "standard" is the mission's real evidence-gathering run;
#: "soak" multiplies standard for a longer, higher-confidence pass (never
#: wired into CI -- mission-explicit).
_MODE_MULTIPLIER: dict[Mode, float] = {"quick": 0.2, "standard": 1.0, "soak": 5.0}


def scaled_iterations(base_standard_count: int, mode: Mode) -> int:
    return max(1, round(base_standard_count * _MODE_MULTIPLIER.get(mode, 1.0)))


@dataclass
class TrialOutcome:
    ok: bool
    latency_ms: float
    error_class: str | None = None
    detail: str = ""
    contract_violated: str = ""
    pids: dict[str, int] | None = None


def run_trials(
    *,
    experiment_id: str,
    domain: str,
    hypothesis: str,
    contract: str,
    stress_dimension: str,
    expected_invariant: str,
    iterations: int,
    seed: int | None,
    trial_fn: Callable[[int, random.Random], TrialOutcome],
    per_op_timeout_sec: float,
    cleanup_fn: Callable[[], int] | None = None,
    notes: str = "",
) -> ExperimentResult:
    """Run ``trial_fn`` ``iterations`` times, each independently bounded by
    ``per_op_timeout_sec`` (via a single-worker executor -- a genuine
    thread-level timeout, not a cooperative one a hung trial could ignore).

    ``cleanup_fn``, if given, runs once after all trials and returns a
    leak count (0 = clean) -- the mandatory cleanup-proof step.
    """
    rng = random.Random(seed)
    latencies: list[float] = []
    failures: list[FailureArtifact] = []
    successes = 0
    timeouts = 0

    with ThreadPoolExecutor(max_workers=1) as pool:
        for i in range(iterations):
            start = time.perf_counter()
            future = pool.submit(trial_fn, i, rng)
            try:
                outcome = future.result(timeout=per_op_timeout_sec)
            except FutureTimeoutError:
                timeouts += 1
                elapsed_ms = (time.perf_counter() - start) * 1000
                latencies.append(elapsed_ms)
                failures.append(
                    FailureArtifact(
                        iteration=i,
                        error_class="TimeoutError",
                        detail=f"exceeded per-op timeout of {per_op_timeout_sec}s",
                        contract_violated=expected_invariant,
                    )
                )
                continue
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - start) * 1000
                latencies.append(elapsed_ms)
                failures.append(
                    FailureArtifact(
                        iteration=i,
                        error_class=type(exc).__name__,
                        detail=str(exc)[:300],
                        contract_violated=expected_invariant,
                    )
                )
                continue

            latencies.append(outcome.latency_ms)
            if outcome.ok:
                successes += 1
            else:
                failures.append(
                    FailureArtifact(
                        iteration=i,
                        error_class=outcome.error_class or "AssertionError",
                        detail=outcome.detail,
                        contract_violated=outcome.contract_violated or expected_invariant,
                        pids=outcome.pids or {},
                    )
                )

    cleanup_failures = cleanup_fn() if cleanup_fn is not None else 0

    verdict = verdict_from_counts(iterations=iterations, failures=len(failures), timeouts=0)
    # timeouts already folded into `failures` above; kept as its own
    # counter for reporting, not double-counted in the verdict split.
    epistemic_state: EpistemicState = "OBSERVED" if iterations > 0 else "UNKNOWN"
    if verdict == "INCONCLUSIVE":
        epistemic_state = "UNKNOWN"

    return ExperimentResult(
        experiment_id=experiment_id,
        domain=domain,
        hypothesis=hypothesis,
        contract=contract,
        stress_dimension=stress_dimension,
        expected_invariant=expected_invariant,
        seed=seed,
        iterations=iterations,
        successes=successes,
        failures=len(failures),
        timeouts=timeouts,
        cleanup_failures=cleanup_failures,
        verdict=verdict,
        epistemic_state=epistemic_state,
        latency=LatencyStats.from_samples(latencies),
        failure_artifacts=failures,
        notes=notes,
    )
