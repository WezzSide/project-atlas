"""The Codex CLI runtime adapter.

Flags and event shapes below were read from ``codex exec --help`` on the
installed build (``codex-cli 0.153.4``) and confirmed against real runs before
being encoded. Nothing here was copied from the Claude Code adapter: the two
runtimes differ in ways that matter, and pretending otherwise is how a
continuation setting from one client silently breaks the other.

Where Codex differs from Claude Code, and why the supervisor is told:

  NO PRE-ASSIGNED SESSION ID. Claude Code takes ``--session-id`` and will use
  the identity we hand it, so a crash between writing the dispatch intent and
  the spawn still leaves an addressable session. ``codex exec`` mints its own
  ``thread_id`` and reports it in its first ``thread.started`` event. The
  identity therefore exists only on the runtime's own output stream -- which
  is why this adapter streams that stream straight to a file
  (``stdout_path``) rather than buffering it in a pipe. A supervisor that dies
  mid-run still finds the thread id on disk, and the attempt is resumable
  instead of merely unknown.

  JSONL EVENTS, NOT ONE RESULT OBJECT. ``--json`` emits a newline-delimited
  event stream (``thread.started``, ``turn.started``, ``item.completed``,
  ``turn.completed``). There is no single terminal object carrying an
  ``is_error`` flag, so completion is established by the presence of
  ``turn.completed``, not by exit status alone.

  A REAL FILESYSTEM SANDBOX. ``--sandbox read-only|workspace-write|
  danger-full-access`` is enforced by the runtime, unlike Claude Code's
  ``--add-dir``, which confines nothing without ``--restricted``. This is the
  one boundary Codex enforces *more* strongly, and the support matrix says so.

  NO COST FIGURE. ``turn.completed`` carries token usage and nothing else.
  This adapter reports ``estimated_cost_usd = None`` rather than inventing a
  number, and declares ``supports_cost_limit = False`` -- there is no
  per-launch budget flag to forward.

WHY EXIT STATUS IS NOT ENOUGH, concretely and observed. Asked under
``--sandbox read-only`` to create a file, Codex exited 0, completed its turn,
and reported "Unable to create `blocked.txt`: the current workspace is
read-only." A clean exit, a completed turn, and the task not done. Acceptance
is what catches that, and acceptance is the supervisor's job, not this
adapter's.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
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
    version_at_least,
)
from project_atlas.orchestration.program.models import ExecutionConfidence, FailureClass
from project_atlas.orchestration.program.profiles import (
    AdapterKind,
    AgentProfile,
    CredentialMechanism,
)

ADAPTER_ID: Final[str] = AdapterKind.CODEX.value
EXECUTABLE: Final[str] = "codex"

#: ``codex exec resume <SESSION_ID>`` is the resume contract this adapter uses.
#: Pinned to the build it was verified against; an older CLI may name it
#: differently, and guessing is what this check exists to prevent.
MIN_VERSION_FOR_EXEC_RESUME: Final[str] = "0.150.0"

#: SIGTERM leaves the turn unfinished with no terminal event, the same shape as
#: a cancelled run. Both are uncertain, never failures.
SIGTERM_EXIT_STATUS: Final[int] = 143

#: ``permission_mode`` is the profile's runtime-neutral vocabulary. Codex has
#: its own, narrower one. The mapping is explicit and one-way conservative:
#: every mode that is not unambiguously a write mode maps to ``read-only``.
SANDBOX_FOR_MODE: Final[dict[str, str]] = {
    "plan": "read-only",
    "dontAsk": "read-only",
    "manual": "read-only",
    "acceptEdits": "workspace-write",
    "auto": "workspace-write",
    "bypassPermissions": "danger-full-access",
}

_VERSION_RE: Final[re.Pattern[str]] = re.compile(r"\d+\.\d+\.\d+")

_CREDENTIAL_MARKERS: Final[tuple[str, ...]] = (
    "not logged in",
    "please run `codex login`",
    "codex login",
    "unauthorized",
    "authentication",
    "invalid api key",
)
_QUOTA_MARKERS: Final[tuple[str, ...]] = (
    "quota",
    "rate limit",
    "usage limit",
    "credit balance",
    "billing",
    "insufficient",
)


class CodexAdapter:
    """Runs one bounded ``codex exec`` invocation per attempt."""

    def __init__(self, executable: str = EXECUTABLE) -> None:
        self._executable = executable
        self._version: str | None = None
        self._version_probed = False

    # ---------------------------------------------------------------- probes

    def _resolve(self) -> str:
        found = shutil.which(self._executable)
        if found is None:
            raise AdapterUnavailableError(
                f"{self._executable!r} is not on PATH",
                code="RUNTIME_NOT_INSTALLED",
            )
        return found

    def _detect_version(self) -> str | None:
        if self._version_probed:
            return self._version
        self._version_probed = True
        try:
            completed = subprocess.run(
                [self._resolve(), "--version"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError, AdapterUnavailableError):
            self._version = None
            return None
        match = _VERSION_RE.search(completed.stdout or "")
        self._version = match.group(0) if match else None
        return self._version

    @property
    def capabilities(self) -> AdapterCapabilities:
        version = self._detect_version()
        return AdapterCapabilities(
            adapter_id=ADAPTER_ID,
            supports_resume=version_at_least(version, MIN_VERSION_FOR_EXEC_RESUME),
            # True, but earned differently from Claude Code's: the identity is
            # read back off the streamed event file rather than handed to the
            # runtime up front. See the module docstring.
            supports_session_probe=True,
            # codex mints its own thread id; it never uses one we assign.
            accepts_assigned_session=False,
            supports_cost_limit=False,
            reports_cost=False,
            supports_result_schema=True,
            version=version,
        )

    def preflight(self, profile: AgentProfile) -> None:
        if profile.adapter is not AdapterKind.CODEX:
            raise AdapterUnavailableError(
                f"profile {profile.profile_id} does not use the codex adapter",
                code="ADAPTER_MISMATCH",
            )
        if profile.credential is CredentialMechanism.ANTHROPIC_API_KEY_ENV:
            raise AdapterUnavailableError(
                "the codex adapter does not authenticate with an Anthropic "
                "API key; declare SUBSCRIPTION_OAUTH (codex login) instead",
                code="CREDENTIAL_MECHANISM_CONFLICT",
            )
        self._resolve()
        version = self._detect_version()
        if not version_at_least(version, profile.adapter_min_version):
            raise AdapterUnavailableError(
                f"codex {version or 'unknown'} is below the profile's "
                f"adapter_min_version {profile.adapter_min_version}",
                code="RUNTIME_TOO_OLD",
            )

    def events_path(self, request: AdapterRequest) -> Path:
        return request.evidence_dir / f"{request.attempt_id}.codex-events.jsonl"

    def probe_run_started(self, request: AdapterRequest) -> bool | None:
        """Did this attempt's run reach the runtime?

        The streamed event file is written by the child itself, so its
        existence with at least one parseable event is direct evidence the
        runtime started. Its absence returns ``None``, not ``False``: the file
        is created at spawn time, and a crash a microsecond either side of
        that is exactly the case where claiming certainty would be wrong.
        """
        path = self.events_path(request)
        if not path.is_file():
            return None
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip():
                    return True
        except OSError:
            return None
        return None

    def recover_session_id(self, request: AdapterRequest) -> str | None:
        """Read the thread id off the streamed events, if it got that far."""
        return _thread_id(_read_events(self.events_path(request)))

    # ------------------------------------------------------------------ run

    def build_argv(self, request: AdapterRequest) -> list[str]:
        """Assemble the fixed argv for one invocation.

        The instruction never appears on the command line: ``-`` tells
        ``codex exec`` to read it from stdin. Keeping untrusted text off argv
        means no amount of quoting in a program file can reshape the command.
        """
        profile = request.profile
        argv: list[str] = [self._resolve(), "exec"]
        if request.resume_session_id:
            argv += ["resume", request.resume_session_id]
        argv += ["--json"]
        argv += ["--sandbox", SANDBOX_FOR_MODE[profile.permission_mode]]
        argv += ["--cd", str(request.workspace)]
        argv += ["--skip-git-repo-check"]
        if profile.permission_mode == "bypassPermissions":
            argv += ["--dangerously-bypass-approvals-and-sandbox"]
        if profile.model:
            argv += ["--model", profile.model]
        for extra in profile.workspace.additional_dirs:
            argv += ["--add-dir", str((request.workspace / extra).resolve())]
        if profile.isolated_runtime:
            argv += ["--ignore-user-config"]
        if profile.result_schema is not None:
            schema_file = request.evidence_dir / f"{request.attempt_id}.schema.json"
            schema_file.parent.mkdir(parents=True, exist_ok=True)
            schema_file.write_text(
                json.dumps(profile.result_schema, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            argv += ["--output-schema", str(schema_file)]
        argv += [
            "--output-last-message",
            str(request.evidence_dir / f"{request.attempt_id}.codex-last.txt"),
        ]
        argv += ["-"]
        return argv

    def run(self, request: AdapterRequest) -> AdapterOutcome:
        started = time.monotonic()
        request.evidence_dir.mkdir(parents=True, exist_ok=True)
        profile = request.profile
        argv = self.build_argv(request)
        events_file = self.events_path(request)
        last_message_file = (
            request.evidence_dir / f"{request.attempt_id}.codex-last.txt"
        )

        env = build_child_env(profile, extra=dict(request.extra_env))

        # The event stream goes to a file, so the returned stdout is the same
        # bytes read back and there is nothing to do with it here -- the file
        # is parsed below and named in the evidence either way.
        exit_status, _stdout, stderr, pid, identity, terminal = run_child_to_completion(
            argv,
            cwd=request.workspace,
            env=env,
            stdin_text=request.instruction,
            timeout_seconds=request.timeout_seconds,
            cancel_requested=request.cancel_requested,
            stdout_path=events_file,
        )
        duration = time.monotonic() - started

        events = _read_events(events_file)
        session_id = _thread_id(events) or request.resume_session_id
        reported = None
        if last_message_file.is_file():
            try:
                reported = last_message_file.read_text(
                    encoding="utf-8", errors="replace"
                )[-8192:]
            except OSError:
                reported = None
        if reported is None:
            reported = _last_agent_message(events)

        confidence, failure, terminal_state = _classify(
            terminal=terminal,
            exit_status=exit_status,
            events=events,
            stderr=stderr,
        )

        evidence = [events_file.name]
        if last_message_file.is_file():
            evidence.append(last_message_file.name)
        summary_name = f"{request.attempt_id}.codex-summary.json"
        (request.evidence_dir / summary_name).write_text(
            json.dumps(
                {
                    "adapter": ADAPTER_ID,
                    "argv": argv,
                    "exit_status": exit_status,
                    "terminal_state": terminal_state,
                    "thread_id": session_id,
                    "event_count": len(events),
                    "error_items": _error_items(events),
                    "usage": _usage(events),
                    "stderr_tail": (stderr or "")[-8192:],
                },
                indent=2,
                sort_keys=True,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )
        evidence.append(summary_name)

        notes = [
            "codex reports token usage only; no cost figure is available and "
            "none is invented here",
        ]
        errors = _error_items(events)
        if errors:
            notes.append(f"runtime emitted {len(errors)} error item(s)")

        return AdapterOutcome(
            attempt_id=request.attempt_id,
            task_id=request.task_id,
            launched=True,
            confidence=confidence,
            terminal_state=terminal_state,
            exit_status=exit_status,
            session_id=session_id,
            pid=pid,
            process_start_identity=identity,
            reported=reported,
            structured=_structured(events),
            usage=_usage(events),
            estimated_cost_usd=None,
            evidence=tuple(evidence),
            failure_class=failure,
            duration_seconds=duration,
            notes=tuple(notes),
        )


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            # A partial final line after a hard kill. Recorded as residue
            # rather than discarded, so "the stream was truncated" stays
            # visible instead of looking like a clean short run.
            rows.append({"type": "__malformed__", "bytes": len(stripped)})
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _thread_id(events: list[dict[str, Any]]) -> str | None:
    for row in events:
        if row.get("type") == "thread.started":
            value = row.get("thread_id")
            if isinstance(value, str) and value:
                return value
    return None


def _turn_completed(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in reversed(events):
        if row.get("type") == "turn.completed":
            return row
    return None


def _turn_failed(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in reversed(events):
        if row.get("type") in {"turn.failed", "turn.aborted"}:
            return row
    return None


def _usage(events: list[dict[str, Any]]) -> dict[str, Any]:
    completed = _turn_completed(events)
    if completed is None:
        return {}
    usage = completed.get("usage")
    return usage if isinstance(usage, dict) else {}


def _items(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for row in events:
        if row.get("type") != "item.completed":
            continue
        item = row.get("item")
        if isinstance(item, dict) and item.get("type") == kind:
            found.append(item)
    return found


def _error_items(events: list[dict[str, Any]]) -> list[str]:
    """Error items the runtime reported.

    Recorded, never automatically fatal. A real run emitted an error item
    about a duplicated hooks config and then completed its turn perfectly
    well; treating any error item as a failed run would have thrown away a
    good result over a configuration warning.
    """
    messages: list[str] = []
    for item in _items(events, "error"):
        text = item.get("message")
        if isinstance(text, str):
            messages.append(text[:512])
    for row in events:
        if row.get("type") == "error":
            text = row.get("message")
            if isinstance(text, str):
                messages.append(text[:512])
    return messages


def _last_agent_message(events: list[dict[str, Any]]) -> str | None:
    messages = _items(events, "agent_message")
    if not messages:
        return None
    text = messages[-1].get("text")
    return text[-8192:] if isinstance(text, str) else None


def _structured(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The final message parsed as JSON, when a result schema was requested.

    Evidence, never authority: a schema-shaped answer is still the worker's
    own account of what it did, and acceptance is decided from the workspace.
    """
    text = _last_agent_message(events)
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _matches(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in markers)


def _classify(
    *,
    terminal: str,
    exit_status: int | None,
    events: list[dict[str, Any]],
    stderr: str,
) -> tuple[ExecutionConfidence, FailureClass | None, str]:
    """Decide what the adapter actually observed.

      1. cancelled / timed out          -> UNCERTAIN, always
      2. SIGTERM exit status            -> UNCERTAIN (turn left unfinished)
      3. an explicit turn failure       -> FAILED, classified by cause
      4. no ``turn.completed`` at all   -> UNCERTAIN if the stream was cut,
                                           FAILED if the process exited nonzero
      5. ``turn.completed`` + exit 0    -> CONFIRMED execution
    """
    if terminal in {"cancelled", "timeout"}:
        return ExecutionConfidence.UNCERTAIN, FailureClass.UNCERTAIN_OUTCOME, terminal
    if exit_status == SIGTERM_EXIT_STATUS:
        return (
            ExecutionConfidence.UNCERTAIN,
            FailureClass.UNCERTAIN_OUTCOME,
            "terminated",
        )

    failed = _turn_failed(events)
    completed = _turn_completed(events)
    haystack = "\n".join([*_error_items(events), stderr or ""])

    if failed is not None:
        return (
            ExecutionConfidence.FAILED,
            _failure_from_text(haystack + "\n" + json.dumps(failed, default=str)),
            str(failed.get("type", "turn.failed")),
        )

    if completed is None:
        if not events:
            # Nothing at all reached the stream. The runtime either never
            # started or died before its first event; either way this process
            # cannot say which, so it does not.
            return (
                ExecutionConfidence.UNCERTAIN,
                FailureClass.UNCERTAIN_OUTCOME,
                "no_events",
            )
        if exit_status not in (0, None):
            return (
                ExecutionConfidence.FAILED,
                _failure_from_text(haystack),
                "no_turn_completed",
            )
        return (
            ExecutionConfidence.UNCERTAIN,
            FailureClass.UNCERTAIN_OUTCOME,
            "truncated_event_stream",
        )

    if exit_status not in (0, None):
        # A completed turn and a nonzero exit is a genuine contradiction. It
        # is reported as FAILED rather than resolved in the runtime's favour.
        return (
            ExecutionConfidence.FAILED,
            _failure_from_text(haystack),
            "completed_with_nonzero_exit",
        )

    return ExecutionConfidence.CONFIRMED, None, "turn.completed"


def _failure_from_text(text: str) -> FailureClass:
    if _matches(text, _QUOTA_MARKERS) or _matches(text, _CREDENTIAL_MARKERS):
        return FailureClass.QUOTA_OR_CREDENTIAL
    return FailureClass.TRANSIENT_INFRASTRUCTURE
