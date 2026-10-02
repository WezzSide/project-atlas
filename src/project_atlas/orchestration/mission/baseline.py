"""AS-MISSION-VERTICAL-SLICE-001, MEASUREMENT -- a small, honest,
reproducible baseline over representative missions.

Deliberately small (a handful of trials): this reports counts and
measured values with their sample size in view, not manufactured
precision. Percentages and averages over N<10 are misleading without the
denominator beside them, so every summary keeps it. Treat any improvement
claim derived from this as a target to re-measure against, not a proven
result -- consistent with this package's own founding directive.

Failure classes are kept genuinely separate: an adapter that ran and
returned nonzero is an AGENT_FAILURE; a workspace that could not be
claimed, a checkpoint that needs reconciliation, stale context, or an
adapter that could not even be spawned, is an INFRA_FAILURE; a run the
governed caller itself refused on policy grounds is POLICY_REFUSAL. Any
ONE mission's infrastructure failure is recorded and the baseline
CONTINUES to the next mission -- a single bad mission must not silently
discard every other trial's evidence, a real defect in this module's
first cut (only `WorkspaceUnavailableError` was caught; anything else,
including a plain `FileNotFoundError` from a misconfigured adapter
command, aborted the whole run).

Three distinctions this report deliberately keeps separate rather than
collapsing:

  COMMAND SUCCESS vs. HANDOFF SUCCESS -- an adapter command can exit 0
    while the surrounding governed pipeline still did not resolve
    cleanly (e.g. a deduplicated replay, or cleanup that couldn't be
    confirmed); `command_success` and `handoff_success` are tracked as
    two separate fields, not one.

  CONTEXT-BUILD LATENCY vs. DISPATCH LATENCY -- `context_build_latency_sec`
    is only the `compile_mission_context()` call. `dispatch_latency_sec`
    is the full pipeline through `start_mission_run()` returning
    (policy/staleness checks, checkpointing, and the adapter run itself).
    These are not the same number and reporting only one would misname
    whichever is missing.

  OWNER EFFORT (0 BY CONSTRUCTION) vs. COST (UNMEASURED) -- this harness
    has no human in its loop, so `owner_interventions_total` really is 0,
    structurally, every run. That is NOT the same claim as "cost is
    zero" -- this module has no token/compute cost accounting at all, so
    `cost_per_accepted_outcome_usd` is always `None`, explicitly marked
    UNMEASURED rather than presented as an observed zero.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from project_atlas.orchestration.mission.adapter import MissionAdapter
from project_atlas.orchestration.mission.context_packet import compile_mission_context
from project_atlas.orchestration.mission.execution import (
    ContextStaleError,
    PolicyRefusedError,
    UnreconciledPriorRunError,
    WorkspaceUnavailableError,
    start_mission_run,
)

ErrorClass = Literal["NONE", "AGENT_FAILURE", "INFRA_FAILURE", "POLICY_REFUSAL"]


@dataclass(frozen=True)
class MissionSpec:
    mission_id: str
    objective: str
    keywords: list[str]
    adapter: MissionAdapter


@dataclass(frozen=True)
class TrialResult:
    mission_id: str
    context_build_latency_sec: float
    dispatch_latency_sec: float | None
    context_sources_count: int
    context_approx_tokens: int
    command_success: bool | None
    handoff_success: bool
    error_class: ErrorClass
    detail: str


@dataclass(frozen=True)
class BaselineReport:
    trial_count: int
    trials: list[TrialResult]
    handoff_success_count: int
    by_error_class: dict[ErrorClass, int]
    context_build_latency_mean_sec: float | None
    context_build_latency_stdev_sec: float | None
    dispatch_latency_mean_sec: float | None
    dispatch_latency_stdev_sec: float | None
    owner_interventions_total: int
    cost_per_accepted_outcome_usd: float | None

    def summary_line(self) -> str:
        n = self.trial_count
        return (
            f"N={n} trials; HANDOFF_SUCCESS_RATE={self.handoff_success_count}/{n}; "
            f"by_error_class={dict(self.by_error_class)}; "
            f"CONTEXT_BUILD_LATENCY mean={self._fmt(self.context_build_latency_mean_sec)}s "
            f"stdev={self._fmt(self.context_build_latency_stdev_sec)}s (n={n}); "
            f"DISPATCH_LATENCY mean={self._fmt(self.dispatch_latency_mean_sec)}s "
            f"stdev={self._fmt(self.dispatch_latency_stdev_sec)}s "
            f"(n={self._dispatch_n()}, excludes trials that never reached dispatch); "
            f"owner_interventions_total={self.owner_interventions_total} "
            f"(0 BY CONSTRUCTION -- no human loop exists in this harness, not an empirical "
            f"measurement of a real deployment); "
            f"cost_per_accepted_outcome_usd={self.cost_per_accepted_outcome_usd} "
            f"(UNMEASURED -- no cost accounting hook exists, this is a gap, not an observed zero)"
        )

    def _dispatch_n(self) -> int:
        return sum(1 for t in self.trials if t.dispatch_latency_sec is not None)

    @staticmethod
    def _fmt(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.3f}"


def run_baseline(
    repo_root: Path, missions: list[MissionSpec], *, workspace_parent: Path
) -> BaselineReport:
    """Run each mission spec through the real KNOWLEDGE -> DEVELOPMENT
    path once and record what actually happened. Every trial is real: a
    real context compile against real repository documents, a real
    isolated workspace, a real adapter invocation. One mission's
    infrastructure failure does not abort the others."""
    trials: list[TrialResult] = []
    for spec in missions:
        workspace = workspace_parent / spec.mission_id
        t0 = time.perf_counter()
        try:
            packet = compile_mission_context(
                repo_root,
                mission_id=spec.mission_id,
                objective=spec.objective,
                keywords=spec.keywords,
            )
        except OSError as exc:
            trials.append(
                TrialResult(
                    mission_id=spec.mission_id,
                    context_build_latency_sec=time.perf_counter() - t0,
                    dispatch_latency_sec=None,
                    context_sources_count=0,
                    context_approx_tokens=0,
                    command_success=None,
                    handoff_success=False,
                    error_class="INFRA_FAILURE",
                    detail=f"context compile failed: {exc}",
                )
            )
            continue
        context_build_latency = time.perf_counter() - t0
        sources_count = len(packet.decisions) + len(packet.backlog_items) + len(
            packet.prior_related_work
        )

        t_dispatch_start = time.perf_counter()
        try:
            result = start_mission_run(
                mission_id=spec.mission_id,
                context=packet,
                adapter=spec.adapter,
                workspace=workspace,
                repo_root=repo_root,
                adapter_timeout_sec=30.0,
            )
        except (WorkspaceUnavailableError, UnreconciledPriorRunError, ContextStaleError) as exc:
            trials.append(
                TrialResult(
                    mission_id=spec.mission_id,
                    context_build_latency_sec=context_build_latency,
                    dispatch_latency_sec=None,
                    context_sources_count=sources_count,
                    context_approx_tokens=packet.manifest.approx_tokens,
                    command_success=None,
                    handoff_success=False,
                    error_class="INFRA_FAILURE",
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        except PolicyRefusedError as exc:
            trials.append(
                TrialResult(
                    mission_id=spec.mission_id,
                    context_build_latency_sec=context_build_latency,
                    dispatch_latency_sec=None,
                    context_sources_count=sources_count,
                    context_approx_tokens=packet.manifest.approx_tokens,
                    command_success=None,
                    handoff_success=False,
                    error_class="POLICY_REFUSAL",
                    detail=str(exc),
                )
            )
            continue
        except Exception as exc:
            # This is precisely the "adapter could not even be spawned"
            # (or any other unclassified infrastructure surprise) case --
            # recorded as INFRA_FAILURE, and the baseline CONTINUES to the
            # next mission rather than aborting with no report at all.
            trials.append(
                TrialResult(
                    mission_id=spec.mission_id,
                    context_build_latency_sec=context_build_latency,
                    dispatch_latency_sec=None,
                    context_sources_count=sources_count,
                    context_approx_tokens=packet.manifest.approx_tokens,
                    command_success=None,
                    handoff_success=False,
                    error_class="INFRA_FAILURE",
                    detail=f"unclassified: {type(exc).__name__}: {exc}",
                )
            )
            continue

        dispatch_latency = time.perf_counter() - t_dispatch_start
        command_success = result.adapter_result.ok if result.adapter_result is not None else None
        handoff_success = result.checkpoint.state == "COMPLETE" and bool(command_success)
        error_class: ErrorClass = "NONE" if handoff_success else "AGENT_FAILURE"
        detail = (
            ""
            if handoff_success
            else f"checkpoint={result.checkpoint.state} command_success={command_success}"
        )
        trials.append(
            TrialResult(
                mission_id=spec.mission_id,
                context_build_latency_sec=context_build_latency,
                dispatch_latency_sec=dispatch_latency,
                context_sources_count=sources_count,
                context_approx_tokens=packet.manifest.approx_tokens,
                command_success=command_success,
                handoff_success=handoff_success,
                error_class=error_class,
                detail=detail,
            )
        )

    build_times = [t.context_build_latency_sec for t in trials]
    dispatch_times = [t.dispatch_latency_sec for t in trials if t.dispatch_latency_sec is not None]
    by_class: dict[ErrorClass, int] = {
        "NONE": 0,
        "AGENT_FAILURE": 0,
        "INFRA_FAILURE": 0,
        "POLICY_REFUSAL": 0,
    }
    for t in trials:
        by_class[t.error_class] += 1

    return BaselineReport(
        trial_count=len(trials),
        trials=trials,
        handoff_success_count=sum(1 for t in trials if t.handoff_success),
        by_error_class=by_class,
        context_build_latency_mean_sec=statistics.fmean(build_times) if build_times else None,
        context_build_latency_stdev_sec=(
            statistics.stdev(build_times) if len(build_times) >= 2 else None
        ),
        dispatch_latency_mean_sec=statistics.fmean(dispatch_times) if dispatch_times else None,
        dispatch_latency_stdev_sec=(
            statistics.stdev(dispatch_times) if len(dispatch_times) >= 2 else None
        ),
        owner_interventions_total=0,
        cost_per_accepted_outcome_usd=None,
    )
