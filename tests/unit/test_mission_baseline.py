"""AS-MISSION-VERTICAL-SLICE-001, MEASUREMENT -- regression coverage for
`baseline.py`, focused on the review-thread finding (T9) and the metric
distinctions the remediation directive asked for.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from project_atlas.orchestration.mission.adapter import ShellCommandAdapter
from project_atlas.orchestration.mission.baseline import MissionSpec, run_baseline


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_thread_t9_one_missions_unclassified_exception_does_not_abort_the_baseline():
    """Review thread: an exception `run_baseline()`'s own handlers don't
    specifically expect (a badly-behaved third-party adapter that raises
    instead of returning a classified `AdapterResult` -- the reference
    `ShellCommandAdapter` never does this, see T1's fix, but nothing
    prevents a FUTURE adapter from failing to follow that discipline) must
    be recorded as INFRA_FAILURE for that ONE mission, and the baseline
    must CONTINUE to the remaining missions -- a real, reported defect in
    this module's first cut only caught `WorkspaceUnavailableError`."""

    class RaisingAdapter:
        def run(self, *, workspace, context, timeout_sec):
            raise RuntimeError("a badly-behaved adapter that doesn't return AdapterResult")

    missions = [
        MissionSpec(
            mission_id="M-BAD-ADAPTER",
            objective="deliberately misbehaving adapter",
            keywords=["lock"],
            adapter=RaisingAdapter(),
        ),
        MissionSpec(
            mission_id="M-GOOD",
            objective="a real, working mission",
            keywords=["lock"],
            adapter=ShellCommandAdapter(command=(sys.executable, "-c", "print('ok')")),
        ),
    ]
    report = run_baseline(
        _repo_root(), missions, workspace_parent=Path(tempfile.mkdtemp())
    )
    assert report.trial_count == 2, "both missions must be recorded, not just the first"
    trial_1, trial_2 = report.trials
    assert trial_1.mission_id == "M-BAD-ADAPTER"
    assert trial_1.error_class == "INFRA_FAILURE"
    assert trial_1.handoff_success is False
    assert trial_2.mission_id == "M-GOOD"
    assert trial_2.error_class == "NONE"
    assert trial_2.handoff_success is True


def test_missing_executable_is_agent_failure_not_infra_failure():
    """Relationship between T1 and T9: because `ShellCommandAdapter`
    itself now catches a spawn failure and returns a classified
    `AdapterResult` (T1's fix) rather than raising, a missing executable
    no longer reaches `run_baseline()`'s exception handlers at all -- it
    is a normal, classified AGENT_FAILURE-shaped result, correctly
    distinct from the genuinely-unclassified-exception case T9 covers."""
    missions = [
        MissionSpec(
            mission_id="M-MISSING-EXE",
            objective="o",
            keywords=["lock"],
            adapter=ShellCommandAdapter(command=("this-does-not-exist-anywhere-12345",)),
        ),
    ]
    report = run_baseline(
        _repo_root(), missions, workspace_parent=Path(tempfile.mkdtemp())
    )
    trial = report.trials[0]
    assert trial.error_class == "AGENT_FAILURE"
    assert trial.command_success is False
    assert trial.handoff_success is False


def test_command_success_and_handoff_success_are_tracked_separately():
    missions = [
        MissionSpec(
            mission_id="M-COMMAND-OK",
            objective="o",
            keywords=["lock"],
            adapter=ShellCommandAdapter(command=(sys.executable, "-c", "print('ok')")),
        ),
    ]
    report = run_baseline(
        _repo_root(), missions, workspace_parent=Path(tempfile.mkdtemp())
    )
    trial = report.trials[0]
    assert trial.command_success is True
    assert trial.handoff_success is True


def test_context_build_latency_and_dispatch_latency_are_distinct_fields():
    missions = [
        MissionSpec(
            mission_id="M-LATENCY",
            objective="o",
            keywords=["lock"],
            adapter=ShellCommandAdapter(command=(sys.executable, "-c", "print('ok')")),
        ),
    ]
    report = run_baseline(
        _repo_root(), missions, workspace_parent=Path(tempfile.mkdtemp())
    )
    trial = report.trials[0]
    assert trial.context_build_latency_sec is not None
    assert trial.dispatch_latency_sec is not None
    # These measure genuinely different spans of work -- not required to
    # be equal, just both present and independently meaningful.
    assert trial.context_build_latency_sec >= 0
    assert trial.dispatch_latency_sec >= 0


def test_owner_effort_and_cost_are_not_conflated():
    report = run_baseline(_repo_root(), [], workspace_parent=Path(tempfile.mkdtemp()))
    assert report.owner_interventions_total == 0  # by construction, always
    assert report.cost_per_accepted_outcome_usd is None  # UNMEASURED, not zero
    summary = report.summary_line()
    assert "BY CONSTRUCTION" in summary
    assert "UNMEASURED" in summary


def test_policy_refusal_error_class_bucket_exists_and_starts_at_zero():
    """`run_baseline()`'s own missions never set a disallowed
    `trusted_policy` (it compiles a plain context per mission with no
    override), so this run legitimately has zero policy refusals -- the
    bucket existing and being 0/N (not omitted) is what this test pins;
    `execution.py`'s own `PolicyRefusedError` enforcement itself is
    covered directly in `test_mission_vertical_slice.py`."""
    missions = [
        MissionSpec(
            mission_id="M-ORDINARY",
            objective="o",
            keywords=["lock"],
            adapter=ShellCommandAdapter(command=(sys.executable, "-c", "print('ok')")),
        ),
    ]
    report = run_baseline(
        _repo_root(), missions, workspace_parent=Path(tempfile.mkdtemp())
    )
    assert "POLICY_REFUSAL" in report.by_error_class
    assert report.by_error_class["POLICY_REFUSAL"] == 0
