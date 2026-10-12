"""Waiting on external events, over the existing durable observer registry.

PENDING_EXTERNAL_EVENT != PROGRAM_BLOCK. A task waiting on CI, a deploy, or a
controlled test fixture does not stop the supervisor: it stops *that task*.
Independent eligible work keeps running, which is the property this module
exists to preserve.

Everything durable here is ``orchestration.sdk.external_observers``: observer
rows, exponential backoff on a transient probe failure, once-only consumption
of a terminal event. This module adds the probe -- a fixed argv whose exit
status is the signal -- and the mapping from probe result to observer status.
No second observer store, no second backoff policy.

A task whose precondition is unresolved stays ``DISCOVERED``. That is not a
workaround for the missing state: the existing DAG has no edge from
``BLOCKED`` back to ``READY``, so parking a merely-waiting task in ``BLOCKED``
would strand it permanently, while ``DISCOVERED -> READY`` is exactly the
"became eligible" transition the DAG already models.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from project_atlas.orchestration.program.adapters.base import build_child_env
from project_atlas.orchestration.program.models import ExternalPrecondition, ProgramError
from project_atlas.orchestration.program.profiles import AgentProfile
from project_atlas.orchestration.sdk.external_observers import (
    ExternalObserver,
    ObserverStatus,
    consume_terminal_event,
    load_observer_registry,
    make_observer,
    park_observer_backoff,
    persist_observer_registry,
    register_observer,
    update_observer_status,
)


class WaitError(ProgramError):
    code = "PROGRAM_WAIT_ERROR"


class WaitOutcome(StrEnum):
    """What one poll of one precondition established."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    STILL_PENDING = "STILL_PENDING"
    #: The probe itself could not run. Backed off, never mistaken for FAILED:
    #: "I could not ask" and "the answer was no" are different facts.
    PROBE_UNAVAILABLE = "PROBE_UNAVAILABLE"
    #: The precondition's own ``max_wait_seconds`` elapsed.
    TIMED_OUT = "TIMED_OUT"


@dataclass(frozen=True)
class WaitResult:
    observer_id: str
    outcome: WaitOutcome
    detail: str
    #: Set only on the transition into a terminal state, and only the first
    #: time -- a repeated event is consumed once and reported as already seen,
    #: which is what stops a duplicate dispatch.
    newly_terminal: bool = False


def observer_id_for(program_id: str, task_id: str, precondition_id: str) -> str:
    return f"{program_id}:{task_id}:{precondition_id}"


def ensure_observer(
    root: Path,
    *,
    program_id: str,
    task_id: str,
    precondition: ExternalPrecondition,
    generation: int = 0,
    now: float | None = None,
) -> ExternalObserver:
    """Register the observer for a precondition, idempotently."""
    observer_id = observer_id_for(program_id, task_id, precondition.precondition_id)
    registry = load_observer_registry(root)
    existing = registry.observers.get(observer_id)
    if existing is not None:
        return existing
    observer = make_observer(
        observer_id=observer_id,
        observer_type=precondition.observer_type,
        package_id=task_id,
        generation=generation,
        external_id=precondition.external_id,
        now=now,
        poll_after_sec=0.0,
    )
    register_observer(root, observer)
    return observer


def _probe(
    precondition: ExternalPrecondition,
    *,
    workspace: Path,
    profile: AgentProfile,
) -> tuple[int | None, str]:
    try:
        completed = subprocess.run(
            list(precondition.probe_argv),
            cwd=str(workspace),
            env=build_child_env(profile),
            capture_output=True,
            text=True,
            timeout=precondition.probe_timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"probe timed out after {precondition.probe_timeout_seconds}s"
    except (OSError, ValueError) as exc:
        return None, f"probe could not run: {exc}"
    tail = ((completed.stdout or "") + (completed.stderr or ""))[-2048:]
    return completed.returncode, tail


def poll_precondition(
    root: Path,
    *,
    program_id: str,
    task_id: str,
    precondition: ExternalPrecondition,
    workspace: Path,
    profile: AgentProfile,
    now: float | None = None,
) -> WaitResult:
    """Poll one precondition once. Never blocks longer than the probe timeout."""
    ts = time.time() if now is None else now
    observer_id = observer_id_for(program_id, task_id, precondition.precondition_id)
    registry = load_observer_registry(root)
    observer = registry.observers.get(observer_id)
    if observer is None:
        raise WaitError(
            f"no observer registered for {observer_id}", code="OBSERVER_MISSING"
        )

    if observer.status in {
        ObserverStatus.TERMINAL_PASS,
        ObserverStatus.TERMINAL_FAIL,
        ObserverStatus.CANCELLED,
    }:
        # Already terminal. Consumption is once-only, so a repeated event
        # here reports newly_terminal=False and the caller does not act twice.
        first = consume_terminal_event(
            root, observer_id=observer_id, event_key=f"{observer_id}:{observer.status.value}"
        )
        outcome = (
            WaitOutcome.PASSED
            if observer.status is ObserverStatus.TERMINAL_PASS
            else WaitOutcome.FAILED
        )
        return WaitResult(
            observer_id=observer_id,
            outcome=outcome,
            detail=f"observer already {observer.status.value}",
            newly_terminal=first,
        )

    if ts - observer.created_at > precondition.max_wait_seconds:
        update_observer_status(
            root,
            observer_id,
            status=ObserverStatus.TERMINAL_FAIL,
            next_poll_at=ts,
            last_error="MAX_WAIT_EXCEEDED",
        )
        first = consume_terminal_event(
            root,
            observer_id=observer_id,
            event_key=f"{observer_id}:{ObserverStatus.TERMINAL_FAIL.value}",
        )
        return WaitResult(
            observer_id=observer_id,
            outcome=WaitOutcome.TIMED_OUT,
            detail=(
                f"waited {ts - observer.created_at:.0f}s, past "
                f"max_wait_seconds={precondition.max_wait_seconds:.0f}"
            ),
            newly_terminal=first,
        )

    if observer.next_poll_at > ts:
        return WaitResult(
            observer_id=observer_id,
            outcome=WaitOutcome.STILL_PENDING,
            detail=f"next poll in {observer.next_poll_at - ts:.1f}s",
        )

    exit_status, detail = _probe(precondition, workspace=workspace, profile=profile)

    if exit_status is None:
        park_observer_backoff(root, observer_id, now=ts, error="PROBE_UNAVAILABLE")
        return WaitResult(
            observer_id=observer_id,
            outcome=WaitOutcome.PROBE_UNAVAILABLE,
            detail=detail,
        )

    if exit_status == precondition.pass_exit_code:
        update_observer_status(
            root,
            observer_id,
            status=ObserverStatus.TERMINAL_PASS,
            next_poll_at=ts,
        )
        first = consume_terminal_event(
            root,
            observer_id=observer_id,
            event_key=f"{observer_id}:{ObserverStatus.TERMINAL_PASS.value}",
        )
        return WaitResult(
            observer_id=observer_id,
            outcome=WaitOutcome.PASSED,
            detail=detail or "probe reported the pass status",
            newly_terminal=first,
        )

    if (
        precondition.fail_exit_code is not None
        and exit_status == precondition.fail_exit_code
    ):
        update_observer_status(
            root,
            observer_id,
            status=ObserverStatus.TERMINAL_FAIL,
            next_poll_at=ts,
            last_error=f"probe exit {exit_status}",
        )
        first = consume_terminal_event(
            root,
            observer_id=observer_id,
            event_key=f"{observer_id}:{ObserverStatus.TERMINAL_FAIL.value}",
        )
        return WaitResult(
            observer_id=observer_id,
            outcome=WaitOutcome.FAILED,
            detail=detail or f"probe reported the fail status ({exit_status})",
            newly_terminal=first,
        )

    update_observer_status(
        root,
        observer_id,
        status=ObserverStatus.RUNNING,
        next_poll_at=ts + precondition.poll_interval_seconds,
    )
    return WaitResult(
        observer_id=observer_id,
        outcome=WaitOutcome.STILL_PENDING,
        detail=detail or f"probe exit {exit_status}, still pending",
    )


def cancel_observers(root: Path, *, program_id: str) -> int:
    """Cancel every non-terminal observer belonging to a program.

    Used when the program is cancelled or hits a limit: an outstanding
    observer is a promise to keep polling, and leaving one armed after the
    program stopped would make a later run believe it is still waiting on
    something nobody is watching.
    """
    registry = load_observer_registry(root)
    prefix = f"{program_id}:"
    cancelled = 0
    changed = False
    for observer_id, observer in list(registry.observers.items()):
        if not observer_id.startswith(prefix):
            continue
        if observer.status in {
            ObserverStatus.TERMINAL_PASS,
            ObserverStatus.TERMINAL_FAIL,
            ObserverStatus.CANCELLED,
        }:
            continue
        registry.observers[observer_id] = observer.model_copy(
            update={"status": ObserverStatus.CANCELLED, "last_error": "PROGRAM_STOPPED"}
        )
        cancelled += 1
        changed = True
    if changed:
        persist_observer_registry(root, registry)
    return cancelled
