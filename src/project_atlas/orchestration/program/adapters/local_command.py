"""FIXTURE ADAPTER -- a deterministic local worker.

LABEL: ``FIXTURE``. Everything this adapter produces is labelled as such in
its outcome notes and in the evidence it writes. A program run entirely
through this adapter proves supervisor behaviour: selection, ordering,
leasing, checkpointing, restart reconciliation, waiting, cancellation and
limits. It proves **nothing** about compatibility with a real agent runtime.
``FIXTURE_RUN != REAL_RUNTIME_COMPATIBILITY``.

It exists so the acceptance program can inject faults on purpose -- a worker
that claims success but changes nothing, one that hangs, one that exits with a
credential-shaped error -- without spending a model call to do it.

The command is a fixed argv from the approved program, never a shell string,
and never anything a worker wrote.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Final

from project_atlas.orchestration.program.adapters.base import (
    AdapterCapabilities,
    AdapterOutcome,
    AdapterRequest,
    AdapterUnavailableError,
    build_child_env,
    run_child_to_completion,
)
from project_atlas.orchestration.program.models import ExecutionConfidence, FailureClass
from project_atlas.orchestration.program.profiles import AdapterKind, AgentProfile

ADAPTER_ID: Final[str] = AdapterKind.LOCAL_COMMAND.value
FIXTURE_LABEL: Final[str] = "FIXTURE"

#: Environment variables the fixture worker is given so a test script can
#: behave differently per attempt without the supervisor special-casing it.
_INSTRUCTION_ENV: Final[str] = "ATLAS_PROGRAM_INSTRUCTION"
_ATTEMPT_ENV: Final[str] = "ATLAS_PROGRAM_ATTEMPT"
_TASK_ENV: Final[str] = "ATLAS_PROGRAM_TASK"
_SESSION_ENV: Final[str] = "ATLAS_PROGRAM_SESSION"
_WORKSPACE_ENV: Final[str] = "ATLAS_PROGRAM_WORKSPACE"


class LocalCommandAdapter:
    """Runs a fixed argv as the worker. Deterministic by construction."""

    def __init__(self, argv: tuple[str, ...]) -> None:
        if not argv:
            raise AdapterUnavailableError(
                "local-command adapter requires a non-empty argv",
                code="ADAPTER_ARGV_MISSING",
            )
        self._argv = argv

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            adapter_id=ADAPTER_ID,
            # A plain command has no conversation to continue. Saying so is
            # what makes the supervisor use the recovery contract instead of
            # relaunching, rather than discovering the absence at run time.
            supports_resume=False,
            # It does, however, leave a durable marker before doing anything
            # else, which is a genuine "did this ever start" signal.
            supports_session_probe=True,
            # The fixture worker is handed its session id in the environment
            # and writes it into its own start marker, so an assigned identity
            # is genuinely used rather than merely recorded.
            accepts_assigned_session=True,
            supports_cost_limit=False,
            reports_cost=False,
            supports_result_schema=False,
            version=FIXTURE_LABEL,
        )

    def preflight(self, profile: AgentProfile) -> None:
        if profile.adapter is not AdapterKind.LOCAL_COMMAND:
            raise AdapterUnavailableError(
                f"profile {profile.profile_id} does not use the local-command adapter",
                code="ADAPTER_MISMATCH",
            )

    def _marker_path(self, request: AdapterRequest) -> Path:
        return request.evidence_dir / f"{request.attempt_id}.started"

    def probe_run_started(self, request: AdapterRequest) -> bool | None:
        """The marker is written before the child is spawned, so its presence
        means the launch was reached and its absence means it was not."""
        return self._marker_path(request).is_file()

    def run(self, request: AdapterRequest) -> AdapterOutcome:
        started = time.monotonic()
        request.evidence_dir.mkdir(parents=True, exist_ok=True)
        marker = self._marker_path(request)
        marker.write_text(
            json.dumps(
                {
                    "label": FIXTURE_LABEL,
                    "attempt_id": request.attempt_id,
                    "task_id": request.task_id,
                    "session_id": request.session_id,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        env = build_child_env(
            request.profile,
            extra={
                _INSTRUCTION_ENV: request.instruction,
                _ATTEMPT_ENV: str(request.attempt_number),
                _TASK_ENV: request.task_id,
                _SESSION_ENV: request.session_id or "",
                _WORKSPACE_ENV: str(request.workspace),
                **dict(request.extra_env),
            },
        )

        exit_status, stdout, stderr, pid, identity, terminal = run_child_to_completion(
            self._argv,
            cwd=request.workspace,
            env=env,
            stdin_text=None,
            timeout_seconds=request.timeout_seconds,
            cancel_requested=request.cancel_requested,
        )
        duration = time.monotonic() - started

        transcript = request.evidence_dir / f"{request.attempt_id}.transcript.json"
        payload: dict[str, Any] = {
            "label": FIXTURE_LABEL,
            "adapter": ADAPTER_ID,
            "argv": list(self._argv),
            "exit_status": exit_status,
            "terminal_state": terminal,
            "stdout": stdout[-16_384:],
            "stderr": stderr[-16_384:],
        }
        transcript.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        if terminal in {"cancelled", "timeout"}:
            confidence = ExecutionConfidence.UNCERTAIN
            failure: FailureClass | None = FailureClass.UNCERTAIN_OUTCOME
        elif exit_status == 0:
            confidence = ExecutionConfidence.CONFIRMED
            failure = None
        else:
            confidence = ExecutionConfidence.FAILED
            failure = _classify_fixture_exit(exit_status, stderr)

        return AdapterOutcome(
            attempt_id=request.attempt_id,
            task_id=request.task_id,
            launched=True,
            confidence=confidence,
            terminal_state=terminal,
            exit_status=exit_status,
            session_id=request.session_id,
            pid=pid,
            process_start_identity=identity,
            reported=stdout.strip()[-8192:] or None,
            structured=None,
            usage={},
            estimated_cost_usd=None,
            evidence=(marker.name, transcript.name),
            failure_class=failure,
            duration_seconds=duration,
            notes=(
                f"{FIXTURE_LABEL}: deterministic local worker, not a real agent runtime",
            ),
        )


#: Exit statuses the fixture worker uses to request a specific failure class,
#: so fault injection exercises the supervisor's real classification paths
#: instead of a test-only branch inside the supervisor.
_FIXTURE_EXIT_CLASSES: Final[dict[int, FailureClass]] = {
    20: FailureClass.TRANSIENT_INFRASTRUCTURE,
    21: FailureClass.INVALID_TASK_INPUT,
    22: FailureClass.POLICY_REFUSAL,
    23: FailureClass.QUOTA_OR_CREDENTIAL,
}


def _classify_fixture_exit(exit_status: int | None, stderr: str) -> FailureClass:
    if exit_status is not None and exit_status in _FIXTURE_EXIT_CLASSES:
        return _FIXTURE_EXIT_CLASSES[exit_status]
    if exit_status is not None and exit_status >= 126:
        # 126/127 are "cannot execute" / "not found": the task's own argv is
        # wrong, which no amount of retrying fixes.
        return FailureClass.INVALID_TASK_INPUT
    return FailureClass.TRANSIENT_INFRASTRUCTURE
