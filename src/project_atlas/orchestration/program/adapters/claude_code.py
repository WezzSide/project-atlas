"""The Claude Code runtime adapter.

Flags and result fields below were read from ``claude --help`` on the
installed build and cross-checked against the current official documentation
(``https://code.claude.com/docs/en/headless``) before use. Nothing here was
copied from another client: continuation settings are not portable between
agent CLIs, and this adapter uses only options this runtime documents.

What the runtime gives us that matters to a supervisor:

  ``--print``                 non-interactive, one turn to a terminal state
  ``--output-format json``    a structured result object, not terminal text
  ``--session-id <uuid>``     session identity assigned *by us*, before launch
  ``--resume <session-id>``   continue that exact session instead of relaunching
  ``--permission-mode``       runtime-enforced permission baseline
  ``--permission-prompts none`` nobody can approve; anything that would prompt
                              is denied instead of hanging forever unattended
  ``--allowedTools`` / ``--disallowedTools`` / ``--tools``  runtime-enforced
  ``--max-budget-usd``        per-launch cap, against the runtime's own
                              client-side estimate -- not an account limit
  ``--json-schema``           validates the worker's final answer's shape

WHY EXIT STATUS IS NOT ENOUGH, concretely. A real probe against this build
returned exit status 1 with ``"subtype": "success"`` in the same payload,
because ``subtype`` describes the shape of the result message, not the fate of
the run. The fields that actually carried the outcome were ``is_error: true``,
``terminal_reason: "api_error"``, ``api_error_status: 400`` and
``result: "Credit balance is too low"``. This adapter therefore classifies on
``is_error`` / ``terminal_reason`` / ``api_error_status``, and never on
``subtype``. And even a fully clean run is only ever reported as CONFIRMED
*execution* -- acceptance is decided by the supervisor from the workspace.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import uuid
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

ADAPTER_ID: Final[str] = AdapterKind.CLAUDE_CODE.value
EXECUTABLE: Final[str] = "claude"

#: ``--permission-prompts`` landed in 2.1.259. Below that the flag is rejected
#: outright with an unknown-option error, so an unattended run on an older
#: build would sit waiting for an approval that can never come.
MIN_VERSION_FOR_PERMISSION_PROMPTS: Final[str] = "2.1.259"
#: ``--resume <session-id>`` finds a session by id in any project on the
#: machine from 2.1.223; before that it only looked in the current directory.
#: Resume from a different cwd is exactly what recovery needs.
MIN_VERSION_FOR_CROSS_DIR_RESUME: Final[str] = "2.1.223"

#: SIGTERM to a ``claude -p`` run is documented to exit 143 with the in-flight
#: turn unfinished and no result recorded. That is the definition of an
#: uncertain outcome, not a failure.
SIGTERM_EXIT_STATUS: Final[int] = 143

#: Where the runtime stores session transcripts. Used only as a "did this
#: session ever start" probe. The layout is an implementation detail of the
#: runtime, so its ABSENCE is reported as "no evidence", never as proof that
#: nothing happened -- see ``probe_run_started``.
_SESSION_ROOT: Final[Path] = Path.home() / ".claude" / "projects"

#: ``terminal_reason`` values that mean a ceiling the OPERATOR configured was
#: reached. Deterministic under UNCHANGED limits, so an automatic retry re-runs
#: the same task to the same stop -- which is why it is not retryable here. It
#: is not a transient fault, and not an account problem: the account is
#: untouched and has quota left. Raising the limit and starting again is
#: ordinary recovery and remains available to the operator.
_CONFIGURED_LIMIT_REASONS: Final[frozenset[str]] = frozenset({"budget_exhausted"})

_ERROR_CLASSES: Final[dict[str, FailureClass]] = {
    "authentication_failed": FailureClass.QUOTA_OR_CREDENTIAL,
    "oauth_org_not_allowed": FailureClass.QUOTA_OR_CREDENTIAL,
    "account_on_hold": FailureClass.QUOTA_OR_CREDENTIAL,
    "billing_error": FailureClass.QUOTA_OR_CREDENTIAL,
    "cloud_credential_error": FailureClass.QUOTA_OR_CREDENTIAL,
    "rate_limit": FailureClass.TRANSIENT_INFRASTRUCTURE,
    "overloaded": FailureClass.TRANSIENT_INFRASTRUCTURE,
    "server_error": FailureClass.TRANSIENT_INFRASTRUCTURE,
    "invalid_request": FailureClass.INVALID_TASK_INPUT,
    "model_not_found": FailureClass.INVALID_TASK_INPUT,
}

#: Message fragments the runtime returns in ``result`` for account-level
#: refusals. Matched case-insensitively, and only ever used to *narrow* a
#: classification the status code already made -- never to invent one.
_QUOTA_MARKERS: Final[tuple[str, ...]] = (
    "credit balance",
    "insufficient credit",
    "quota",
    "usage limit",
    "rate limit",
    "billing",
)
_CREDENTIAL_MARKERS: Final[tuple[str, ...]] = (
    "authentication",
    "invalid api key",
    "unauthorized",
    "not logged in",
    "please run /login",
)


class ClaudeCodeAdapter:
    """Runs one bounded ``claude --print`` invocation per attempt."""

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
        match = re.search(r"\d+\.\d+\.\d+", completed.stdout or "")
        self._version = match.group(0) if match else None
        return self._version

    @property
    def capabilities(self) -> AdapterCapabilities:
        version = self._detect_version()
        return AdapterCapabilities(
            adapter_id=ADAPTER_ID,
            supports_resume=version_at_least(version, MIN_VERSION_FOR_CROSS_DIR_RESUME),
            supports_session_probe=True,
            accepts_assigned_session=True,
            supports_cost_limit=True,
            reports_cost=True,
            supports_result_schema=True,
            version=version,
        )

    def preflight(self, profile: AgentProfile) -> None:
        if profile.adapter is not AdapterKind.CLAUDE_CODE:
            raise AdapterUnavailableError(
                f"profile {profile.profile_id} does not use the claude-code adapter",
                code="ADAPTER_MISMATCH",
            )
        self._resolve()
        version = self._detect_version()
        if not version_at_least(version, MIN_VERSION_FOR_PERMISSION_PROMPTS):
            raise AdapterUnavailableError(
                f"claude {version or 'unknown'} predates "
                f"{MIN_VERSION_FOR_PERMISSION_PROMPTS}, which introduced "
                "--permission-prompts; an unattended run on this build would "
                "wait for an approval nobody can give",
                code="RUNTIME_TOO_OLD",
            )
        if not version_at_least(version, profile.adapter_min_version):
            raise AdapterUnavailableError(
                f"claude {version or 'unknown'} is below the profile's "
                f"adapter_min_version {profile.adapter_min_version}",
                code="RUNTIME_TOO_OLD",
            )

    def probe_run_started(self, request: AdapterRequest) -> bool | None:
        """Look for the transcript the runtime writes for a session id.

        Present  -> the runtime started this session. True.
        Absent   -> ``None``, not ``False``. The transcript layout is the
                    runtime's own business and session persistence can be
                    switched off, so "I could not find it" is not the same
                    statement as "it never ran". Returning ``None`` keeps the
                    attempt UNCERTAIN, which is the honest answer and the one
                    that stops the supervisor from relaunching on a guess.
        """
        session_id = request.session_id
        if not session_id or not _SESSION_ROOT.is_dir():
            return None
        name = f"{session_id}.jsonl"
        try:
            for child in _SESSION_ROOT.iterdir():
                if child.is_dir() and (child / name).is_file():
                    return True
        except OSError:
            return None
        return None

    # ------------------------------------------------------------------ run

    def build_argv(self, request: AdapterRequest) -> list[str]:
        """Assemble the fixed argv for one invocation.

        The instruction never appears on the command line: it is written to
        the child's stdin. A prompt is untrusted text as far as process
        construction is concerned, and keeping it off argv means no amount of
        quoting in a program file can reshape the command.
        """
        profile = request.profile
        argv: list[str] = [self._resolve(), "--print"]
        argv += ["--output-format", "json"]
        argv += ["--permission-mode", profile.permission_mode]
        argv += ["--permission-prompts", "none"]

        if request.resume_session_id:
            argv += ["--resume", request.resume_session_id]
        elif request.session_id:
            argv += ["--session-id", request.session_id]

        if profile.model:
            argv += ["--model", profile.model]
        if profile.tools is not None:
            argv += ["--tools", ",".join(profile.tools) if profile.tools else ""]
        if profile.allowed_tools:
            argv += ["--allowedTools", ",".join(profile.allowed_tools)]
        if profile.disallowed_tools:
            argv += ["--disallowedTools", ",".join(profile.disallowed_tools)]
        for extra in profile.workspace.additional_dirs:
            argv += ["--add-dir", str((request.workspace / extra).resolve())]
        if profile.workspace.restricted:
            argv += ["--restricted"]
        if profile.isolated_runtime:
            argv += ["--bare"]
        if profile.result_schema is not None:
            argv += ["--json-schema", json.dumps(profile.result_schema, sort_keys=True)]
        budget = profile.limits.max_estimated_cost_usd
        if budget is not None:
            argv += ["--max-budget-usd", f"{budget:.6f}"]
        return argv

    def run(self, request: AdapterRequest) -> AdapterOutcome:
        started = time.monotonic()
        profile = request.profile

        # Profile coherence is checked BEFORE anything touches PATH. A profile
        # whose declared credential mechanism contradicts its own allow-list is
        # broken wherever it runs, and reporting "the runtime is not installed"
        # for it -- which is what the earlier ordering did on a machine without
        # the runtime -- names the wrong defect and sends the reader looking in
        # the wrong place. Found by CI, which has neither runtime installed.
        extra_env = dict(request.extra_env)
        # Explicitly do NOT forward an inherited API key under
        # SUBSCRIPTION_OAUTH: this runtime prefers the key over the logged-in
        # subscription, so an unrelated variable in the operator's shell would
        # silently redirect every run to a different account. build_child_env
        # only copies names the profile allow-lists, so simply not allow-listing
        # it is enough -- this check exists to make a misconfigured profile fail
        # loudly instead of billing somewhere unexpected.
        if (
            profile.credential is CredentialMechanism.SUBSCRIPTION_OAUTH
            and "ANTHROPIC_API_KEY" in profile.env_allowlist
        ):
            raise AdapterUnavailableError(
                "profile declares SUBSCRIPTION_OAUTH but allow-lists "
                "ANTHROPIC_API_KEY; the runtime would prefer the key and "
                "the declared credential mechanism would be a fiction",
                code="CREDENTIAL_MECHANISM_CONFLICT",
            )

        request.evidence_dir.mkdir(parents=True, exist_ok=True)
        argv = self.build_argv(request)
        env = build_child_env(profile, extra=extra_env)

        exit_status, stdout, stderr, pid, identity, terminal = run_child_to_completion(
            argv,
            cwd=request.workspace,
            env=env,
            stdin_text=request.instruction,
            timeout_seconds=request.timeout_seconds,
            cancel_requested=request.cancel_requested,
        )
        duration = time.monotonic() - started

        parsed = _parse_result(stdout)
        evidence_name = f"{request.attempt_id}.claude-result.json"
        (request.evidence_dir / evidence_name).write_text(
            json.dumps(
                {
                    "adapter": ADAPTER_ID,
                    "argv": _redact_argv(argv),
                    "exit_status": exit_status,
                    "terminal_state": terminal,
                    "result": parsed,
                    "stderr_tail": (stderr or "")[-8192:],
                },
                indent=2,
                sort_keys=True,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )

        confidence, failure, terminal_state = _classify(
            terminal=terminal,
            exit_status=exit_status,
            parsed=parsed,
            stderr=stderr,
        )

        session_id = None
        if isinstance(parsed, dict):
            raw_session = parsed.get("session_id")
            if isinstance(raw_session, str):
                session_id = raw_session
        session_id = session_id or request.resume_session_id or request.session_id

        usage: dict[str, Any] = {}
        cost: float | None = None
        reported: str | None = None
        structured: dict[str, Any] | None = None
        if isinstance(parsed, dict):
            raw_usage = parsed.get("usage")
            if isinstance(raw_usage, dict):
                usage = raw_usage
            raw_cost = parsed.get("total_cost_usd")
            if isinstance(raw_cost, (int, float)):
                cost = float(raw_cost)
            raw_result = parsed.get("result")
            if isinstance(raw_result, str):
                reported = raw_result[-8192:]
            raw_structured = parsed.get("structured_output")
            if isinstance(raw_structured, dict):
                structured = raw_structured

        notes: list[str] = []
        if cost is not None:
            notes.append(
                "total_cost_usd is the runtime's own client-side estimate and "
                "may differ from the billed amount"
            )
        denials = parsed.get("permission_denials") if isinstance(parsed, dict) else None
        denial_count = len(denials) if isinstance(denials, list) else 0
        if denial_count:
            notes.append(
                f"runtime denied {denial_count} permission request(s); the "
                "worker did not have everything it asked for"
            )

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
            structured=structured,
            usage=usage,
            estimated_cost_usd=cost,
            evidence=(evidence_name,),
            failure_class=failure,
            duration_seconds=duration,
            notes=tuple(notes),
            policy_denials=denial_count,
        )


def new_session_id() -> str:
    """A fresh session id, assigned before launch so a crash stays addressable."""
    return str(uuid.uuid4())


def _parse_result(stdout: str) -> dict[str, Any] | None:
    """Parse the single JSON result object from ``--output-format json``.

    Tolerates leading noise (a startup warning that reached stdout) by taking
    the last well-formed JSON object on the stream, and returns ``None`` when
    there is none -- which is itself a signal, not something to paper over.
    """
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        loaded = None
    if isinstance(loaded, dict):
        return loaded
    for line in reversed(text.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            row = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            return row
    return None


def _matches(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in markers)


def _classify(
    *,
    terminal: str,
    exit_status: int | None,
    parsed: dict[str, Any] | None,
    stderr: str,
) -> tuple[ExecutionConfidence, FailureClass | None, str]:
    """Decide what the adapter actually observed.

    Precedence is deliberate and safety-first:

      1. cancelled / timed out            -> UNCERTAIN, always
      2. SIGTERM exit status              -> UNCERTAIN (turn left unfinished)
      3. no parseable result at all       -> UNCERTAIN (we cannot say)
      4. ``is_error`` on the result       -> FAILED, classified by cause
      5. clean result                     -> CONFIRMED execution

    ``subtype`` is never consulted. It reported ``"success"`` on an observed
    run that failed with HTTP 400.

    A ceiling the operator configured (``terminal_reason`` in
    ``_CONFIGURED_LIMIT_REASONS``) is FAILED rather than UNCERTAIN: the run
    stopped for a reason we asked for, at a point the runtime reported, so the
    outcome is known even though acceptance never ran.

    Three things that classification does NOT say, because each has been
    misread before:

      * FAILED does not certify that no side effect occurred. A worker may have
        written before it was stopped. What FAILED asserts is that the outcome
        is *known*, not that the workspace is clean -- inspect it.
      * ``LIMIT_EXHAUSTED`` suppresses AUTOMATIC retry under unchanged limits.
        It does not mean permanently unrecoverable: raising the ceiling and
        starting a new program is ordinary recovery, and is the operator's call.
      * Whether a particular run left anything unresolved is case-specific
        evidence from that run, never a property of this class.
    """
    if terminal in {"cancelled", "timeout"}:
        return ExecutionConfidence.UNCERTAIN, FailureClass.UNCERTAIN_OUTCOME, terminal
    if exit_status == SIGTERM_EXIT_STATUS:
        return (
            ExecutionConfidence.UNCERTAIN,
            FailureClass.UNCERTAIN_OUTCOME,
            "terminated",
        )
    if parsed is None:
        return (
            ExecutionConfidence.UNCERTAIN,
            FailureClass.UNCERTAIN_OUTCOME,
            "no_structured_result",
        )

    terminal_reason = parsed.get("terminal_reason")
    reason = terminal_reason if isinstance(terminal_reason, str) else "unknown"
    is_error = bool(parsed.get("is_error"))
    result_text = parsed.get("result")
    message = result_text if isinstance(result_text, str) else ""
    status = parsed.get("api_error_status")
    error_key = parsed.get("error")

    if not is_error:
        return ExecutionConfidence.CONFIRMED, None, reason

    failure: FailureClass | None = None
    # A configured per-run ceiling reports itself here and nowhere usable else:
    # on the observed run ``error``, ``api_error_status`` and ``result`` were all
    # None, so every message- and status-based branch below is blind to it and
    # the function fell through to its TRANSIENT_INFRASTRUCTURE default -- which
    # is retryable, and a retry must hit the identical ceiling.
    #
    # ``terminal_reason`` is used rather than ``subtype`` deliberately: this
    # function already reads and returns ``terminal_reason``, whereas ``subtype``
    # is documented above as untrustworthy (it said "success" on a run that
    # failed with HTTP 400).
    if reason in _CONFIGURED_LIMIT_REASONS:
        failure = FailureClass.LIMIT_EXHAUSTED
    if failure is None and isinstance(error_key, str):
        failure = _ERROR_CLASSES.get(error_key)
    if failure is None and isinstance(status, int):
        if status in {401, 402, 403} or status == 429:
            failure = FailureClass.QUOTA_OR_CREDENTIAL
        elif status == 400:
            # 400 covers both "your request was malformed" and account-level
            # refusals such as an exhausted credit balance. The message is
            # what separates them, and getting it wrong means either
            # retrying a dead account or giving up on a fixable prompt.
            failure = (
                FailureClass.QUOTA_OR_CREDENTIAL
                if _matches(message, _QUOTA_MARKERS)
                else FailureClass.INVALID_TASK_INPUT
            )
        elif 500 <= status < 600:
            failure = FailureClass.TRANSIENT_INFRASTRUCTURE
    if failure is None:
        haystack = f"{message}\n{stderr or ''}"
        if _matches(haystack, _QUOTA_MARKERS) or _matches(haystack, _CREDENTIAL_MARKERS):
            failure = FailureClass.QUOTA_OR_CREDENTIAL
        else:
            failure = FailureClass.TRANSIENT_INFRASTRUCTURE
    return ExecutionConfidence.FAILED, failure, reason


def _redact_argv(argv: list[str]) -> list[str]:
    """Evidence records the shape of the command, never a secret in it.

    Nothing this adapter puts on argv is a credential today -- the prompt
    travels on stdin and credentials come from the environment. This exists
    so that stays true if a future flag ever carries one.
    """
    redacted: list[str] = []
    skip_next = False
    sensitive = {"--api-key", "--token", "--settings"}
    for item in argv:
        if skip_next:
            redacted.append("<redacted>")
            skip_next = False
            continue
        redacted.append(item)
        if item in sensitive:
            skip_next = True
    return redacted
