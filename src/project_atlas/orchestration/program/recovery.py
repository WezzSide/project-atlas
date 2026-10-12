"""Restart reconciliation. Ambiguity is preserved, never resolved by wishing.

A supervisor that restarts finds attempts in every phase. What it may do with
each one depends entirely on which phase it died in and on evidence it can
still gather -- never on an assumption:

  ``INTENT_RECORDED``      the ambiguity window. The supervisor decided to
                           launch; it may or may not have reached the spawn.
                           Resolved by asking the adapter whether that exact
                           run ever started (``probe_run_started``), which is
                           possible because the session identity was assigned
                           and written down BEFORE the launch.
  ``ADAPTER_INVOKED``      the worker was definitely started and no terminal
                           outcome was recorded. If the process is still
                           alive it is still ours to wait for; if it is gone,
                           the effect is unknown.
  ``ADAPTER_RETURNED``     the run finished, acceptance was never evaluated.
                           Acceptance is read-only and repeatable, so this
                           one is simply finished.
  ``ACCEPTANCE_EVALUATED`` bookkeeping only remains.
  ``TERMINAL``             nothing to do.

The one thing this module never does is relaunch a task because it could not
tell what happened. ``NEEDS_RECONCILIATION`` is a real outcome that stops the
program and asks the operator, and it is preferred over a duplicate effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from project_atlas.orchestration.program.adapters.base import (
    AdapterCapabilities,
    AdapterRequest,
    RuntimeAdapter,
    pid_is_alive,
    process_start_identity,
)
from project_atlas.orchestration.program.models import (
    AttemptPhase,
    ExecutionConfidence,
    ProgramTask,
)
from project_atlas.orchestration.program.store import AttemptRecord


class RecoveryAction(StrEnum):
    """What the supervisor may do with an interrupted attempt."""

    #: Nothing was started. The same attempt may be launched.
    SAFE_TO_LAUNCH = "SAFE_TO_LAUNCH"
    #: A worker is still running under a live, verified process identity.
    WORKER_STILL_RUNNING = "WORKER_STILL_RUNNING"
    #: The worker ran; continue that exact session rather than starting again.
    RESUME_SESSION = "RESUME_SESSION"
    #: The run finished; only acceptance remains, and acceptance is repeatable.
    EVALUATE_ACCEPTANCE = "EVALUATE_ACCEPTANCE"
    #: Outcome unknown and not recoverable automatically. Operator decision.
    NEEDS_RECONCILIATION = "NEEDS_RECONCILIATION"
    #: Already finished.
    ALREADY_TERMINAL = "ALREADY_TERMINAL"


@dataclass(frozen=True)
class RecoveryVerdict:
    action: RecoveryAction
    confidence: ExecutionConfidence | None
    reason: str
    #: Session to continue when the action is RESUME_SESSION.
    resume_session_id: str | None = None


def worker_still_alive(attempt: AttemptRecord) -> bool:
    """Is the recorded process still the process we started?

    A live PID is not enough. PIDs are reused, and treating a stranger's
    process as our worker means either waiting forever on something that will
    never report, or -- worse -- concluding our own run is still in flight and
    never reconciling it. The recorded start identity is what makes the answer
    trustworthy; when it was never recorded, the honest answer is "no", which
    routes the attempt into reconciliation rather than into a false wait.
    """
    pid = attempt.process_pid
    if pid is None or pid <= 0:
        return False
    if not pid_is_alive(pid):
        return False
    recorded = attempt.process_start_identity
    if not recorded or recorded == "unknown":
        return False
    live = process_start_identity(pid)
    if not live or live == "unknown":
        return False
    return live == recorded


def classify_attempt(
    attempt: AttemptRecord,
    *,
    task: ProgramTask,
    adapter: RuntimeAdapter,
    capabilities: AdapterCapabilities,
    request: AdapterRequest,
) -> RecoveryVerdict:
    """Decide what may be done with one interrupted attempt."""
    if attempt.phase is AttemptPhase.TERMINAL:
        return RecoveryVerdict(
            action=RecoveryAction.ALREADY_TERMINAL,
            confidence=attempt.confidence,
            reason="attempt already reached a terminal phase",
        )

    if attempt.confidence is ExecutionConfidence.UNCERTAIN:
        # Confidence outranks phase. An attempt can reach ADAPTER_RETURNED --
        # the adapter did return -- and still have brought back no usable
        # answer: a cancelled run, a timeout, a SIGTERM, a result that could
        # not be parsed. Routing that into "just evaluate acceptance" would
        # quietly convert an unknown external effect into a verdict, which is
        # precisely the conversion this package refuses to make anywhere.
        if capabilities.supports_resume and attempt.runtime_session_id:
            return RecoveryVerdict(
                action=RecoveryAction.RESUME_SESSION,
                confidence=ExecutionConfidence.UNCERTAIN,
                reason=(
                    "the previous run's outcome is unknown; its own session "
                    "can be continued rather than a second one started"
                ),
                resume_session_id=attempt.runtime_session_id,
            )
        return RecoveryVerdict(
            action=RecoveryAction.NEEDS_RECONCILIATION,
            confidence=ExecutionConfidence.UNCERTAIN,
            reason=(
                f"the previous run reached phase {attempt.phase.value} with an "
                "uncertain outcome, and this adapter cannot continue its "
                "session; what it changed is unknown"
            ),
        )

    if attempt.phase is AttemptPhase.ACCEPTANCE_EVALUATED:
        return RecoveryVerdict(
            action=RecoveryAction.EVALUATE_ACCEPTANCE,
            confidence=attempt.confidence,
            reason="acceptance was evaluated but the outcome was never sealed",
        )

    if attempt.phase is AttemptPhase.ADAPTER_RETURNED:
        return RecoveryVerdict(
            action=RecoveryAction.EVALUATE_ACCEPTANCE,
            confidence=attempt.confidence,
            reason=(
                "the adapter returned and acceptance was never evaluated; "
                "acceptance is read-only and repeatable"
            ),
        )

    if attempt.phase is AttemptPhase.ADAPTER_INVOKED:
        if worker_still_alive(attempt):
            return RecoveryVerdict(
                action=RecoveryAction.WORKER_STILL_RUNNING,
                confidence=None,
                reason=(
                    f"pid {attempt.process_pid} is alive and its start identity "
                    "matches the one recorded at launch"
                ),
            )
        if capabilities.supports_resume and attempt.runtime_session_id:
            return RecoveryVerdict(
                action=RecoveryAction.RESUME_SESSION,
                confidence=ExecutionConfidence.UNCERTAIN,
                reason=(
                    "the worker was launched and is gone; continuing its own "
                    "session rather than starting a second one"
                ),
                resume_session_id=attempt.runtime_session_id,
            )
        return RecoveryVerdict(
            action=RecoveryAction.NEEDS_RECONCILIATION,
            confidence=ExecutionConfidence.UNCERTAIN,
            reason=(
                "the worker was launched, is gone, recorded no outcome, and "
                f"the {capabilities.adapter_id} adapter cannot resume a "
                "session; its external effect is unknown"
            ),
        )

    # INTENT_RECORDED -- the ambiguity window.
    started = adapter.probe_run_started(request)
    if started is True:
        if capabilities.supports_resume and attempt.runtime_session_id:
            return RecoveryVerdict(
                action=RecoveryAction.RESUME_SESSION,
                confidence=ExecutionConfidence.UNCERTAIN,
                reason=(
                    "the runtime has evidence this session started even though "
                    "no invocation checkpoint was written; continuing that "
                    "session, not launching a new one"
                ),
                resume_session_id=attempt.runtime_session_id,
            )
        return RecoveryVerdict(
            action=RecoveryAction.NEEDS_RECONCILIATION,
            confidence=ExecutionConfidence.UNCERTAIN,
            reason=(
                "the runtime has evidence this run started, no outcome was "
                "recorded, and this adapter cannot resume it"
            ),
        )

    if started is False:
        if task.retry_safe_when_no_launch_evidence:
            return RecoveryVerdict(
                action=RecoveryAction.SAFE_TO_LAUNCH,
                confidence=None,
                reason=(
                    "the adapter keeps launch evidence and has none for this "
                    "run, and the task declares its effect safe to repeat"
                ),
            )
        return RecoveryVerdict(
            action=RecoveryAction.NEEDS_RECONCILIATION,
            confidence=ExecutionConfidence.UNCERTAIN,
            reason=(
                "no launch evidence exists, but the task does not declare its "
                "effect safe to repeat; relaunching could duplicate work the "
                "evidence merely failed to capture"
            ),
        )

    return RecoveryVerdict(
        action=RecoveryAction.NEEDS_RECONCILIATION,
        confidence=ExecutionConfidence.UNCERTAIN,
        reason=(
            f"the {capabilities.adapter_id} adapter cannot tell whether this "
            "run ever started; the outcome stays uncertain rather than being "
            "guessed in either direction"
        ),
    )
