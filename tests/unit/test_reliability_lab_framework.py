"""Meta-tests for ATLAS_WINDOWS_RELIABILITY_LAB_V1: the harness's own
mechanics (result model, registry shape, mode scaling, timeout discipline)
-- not a live run of every experiment.

IMPORTANT (session-specific, keep until resolved): live execution of this
package's experiments repeatedly produced user-visible console-window
disruption during development that could not be conclusively root-caused
via static review or targeted monitoring (WindowsTerminal.exe/conhost.exe
process counts stayed flat across every isolated check attempted). Tests
in this file that would spawn real processes are marked
``pytest.mark.skip`` with that context, rather than silently included --
run them deliberately, outside a session where that disruption matters,
once the remaining gap (if any) is understood. Tests that exercise only
the result model / scaffolding (no subprocess spawns) run normally.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from reliability_lab.model import (  # noqa: E402
    ExperimentResult,
    FailureArtifact,
    LatencyStats,
    verdict_from_counts,
)
from reliability_lab.registry import ALL_EXPERIMENTS, experiment_names  # noqa: E402
from reliability_lab.runctl import scaled_iterations  # noqa: E402

_SPAWNS_PROCESSES = pytest.mark.skip(
    reason=(
        "Deliberately not run automatically: live execution of this package's "
        "experiments produced repeated, user-visible console-window disruption "
        "during development that isn't fully root-caused yet (WindowsTerminal/"
        "conhost process counts stayed flat across every isolated diagnostic "
        "attempted, so the remaining gap -- if any -- isn't understood). "
        "Run explicitly and deliberately (pytest -m '' or by name) in a session "
        "where that risk is acceptable, not as part of routine test collection."
    )
)


# ---------------------------------------------------------------------------
# Registry shape (no process spawns)
# ---------------------------------------------------------------------------


def test_registry_has_at_least_fifteen_experiment_classes() -> None:
    assert len(ALL_EXPERIMENTS) >= 15


def test_every_experiment_function_has_a_unique_name() -> None:
    names = experiment_names()
    assert len(names) == len(set(names))


def test_experiment_ids_are_declared_via_run_trials_not_hardcoded_elsewhere() -> None:
    """Every experiment function's docstring should name its own
    EXP-<DOMAIN>-<NNN> id -- a structural check that catches an experiment
    added to the registry without a proper id, without executing it."""
    import re

    pattern = re.compile(r"EXP-[A-Z]+-\d{3}")
    undocumented = [fn.__name__ for fn in ALL_EXPERIMENTS if not pattern.search(fn.__doc__ or "")]
    assert not undocumented, f"experiments missing an EXP-<DOMAIN>-<NNN> id: {undocumented}"


# ---------------------------------------------------------------------------
# Mode scaling / timeout discipline (no process spawns)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "base", "expected"),
    [("quick", 100, 20), ("standard", 100, 100), ("soak", 100, 500)],
)
def test_scaled_iterations_matches_declared_mode_multipliers(
    mode: str, base: int, expected: int
) -> None:
    assert scaled_iterations(base, mode) == expected


def test_scaled_iterations_never_returns_zero() -> None:
    assert scaled_iterations(1, "quick") >= 1


# ---------------------------------------------------------------------------
# Result model invariants (no process spawns)
# ---------------------------------------------------------------------------


def test_verdict_never_pass_with_zero_iterations() -> None:
    """UNKNOWN != HEALTHY, restated for the reliability contract: zero
    trials must never be reported as PASS."""
    assert verdict_from_counts(iterations=0, failures=0, timeouts=0) == "INCONCLUSIVE"
    assert (
        verdict_from_counts(iterations=0, failures=0, timeouts=0, allow_zero_iterations=True)
        == "NOT_APPLICABLE"
    )


def test_verdict_fail_on_any_failure_or_timeout() -> None:
    assert verdict_from_counts(iterations=10, failures=1, timeouts=0) == "FAIL"
    assert verdict_from_counts(iterations=10, failures=0, timeouts=1) == "FAIL"
    assert verdict_from_counts(iterations=10, failures=0, timeouts=0) == "PASS"


def test_latency_stats_from_empty_samples_is_all_none() -> None:
    stats = LatencyStats.from_samples([])
    assert stats.samples == 0
    assert stats.min_ms is None
    assert stats.median_ms is None
    assert stats.p95_ms is None
    assert stats.max_ms is None


def test_latency_stats_p95_is_never_less_than_median() -> None:
    """Negative control: proves the percentile computation is actually
    doing something, not just returning a fixed/wrong index."""
    stats = LatencyStats.from_samples([float(x) for x in range(1, 101)])  # 1..100
    assert stats.median_ms is not None and stats.p95_ms is not None
    assert stats.p95_ms >= stats.median_ms
    assert stats.min_ms == 1.0
    assert stats.max_ms == 100.0


def test_experiment_result_bounds_failure_artifacts_to_ten() -> None:
    """Evidence, not a full dump -- proves the receipt won't balloon on a
    high-failure-rate experiment."""
    artifacts = [
        FailureArtifact(iteration=i, error_class="X", detail="d", contract_violated="c")
        for i in range(50)
    ]
    result = ExperimentResult(
        experiment_id="EXP-TEST-000",
        domain="test",
        hypothesis="h",
        contract="c",
        stress_dimension="s",
        expected_invariant="e",
        seed=1,
        iterations=50,
        successes=0,
        failures=50,
        timeouts=0,
        cleanup_failures=0,
        verdict="FAIL",
        epistemic_state="OBSERVED",
        latency=LatencyStats.from_samples([1.0, 2.0]),
        failure_artifacts=artifacts,
    )
    assert len(result.to_dict()["failure_artifacts"]) == 10
    assert result.failure_rate == 1.0


# ---------------------------------------------------------------------------
# Live experiment execution -- deliberately skipped by default; see module
# docstring. Kept here (not deleted) so the intent and the exact command to
# run them deliberately are both visible in-repo.
# ---------------------------------------------------------------------------


@_SPAWNS_PROCESSES
def test_a_single_experiment_runs_and_cleans_up() -> None:
    from reliability_lab.experiments_process import experiment_proc_rapid_cycle

    result = experiment_proc_rapid_cycle("quick", seed=1)
    assert result.cleanup_failures == 0
    assert result.iterations > 0


@_SPAWNS_PROCESSES
def test_full_quick_suite_produces_a_well_formed_receipt() -> None:
    from reliability_lab.runner import run_suite

    receipt = run_suite("quick", seed=1)
    assert receipt["schema"] == "ATLAS_WINDOWS_RELIABILITY_LAB_V1"
    assert receipt["summary"]["experiment_classes"] >= 15
    assert receipt["summary"]["global_leak_count"] == 0
    assert receipt["summary"]["lab_errors"] == 0
