"""The runtime-adapter seam.

An adapter starts one concrete agent runtime, observes it, and reports what it
saw. It never decides whether the task passed: that is acceptance's job, and
acceptance is evaluated by the supervisor from the workspace, not from
anything the worker said.

The contract is deliberately small so a second runtime can be added without
the supervisor learning anything about it. What every adapter must report:

  * run and task identity                 -> ``AdapterRequest`` echoed back
  * process / session identity            -> ``pid`` / ``session_id``
  * start and terminal state              -> ``launched`` / ``terminal_state``
  * exit status and structured result     -> ``exit_status`` / ``structured``
  * evidence locations                    -> ``evidence``
  * usage measurements where available    -> ``usage`` / ``estimated_cost_usd``
  * whether the outcome is confirmed      -> ``confidence``

Capabilities are declared, not assumed. ``supports_resume`` and
``supports_session_probe`` are what the supervisor consults before deciding
whether an interrupted attempt may be continued -- never a guess based on the
adapter's name.
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Protocol

from project_atlas.orchestration.program.models import (
    ExecutionConfidence,
    FailureClass,
    ProgramError,
)
from project_atlas.orchestration.program.profiles import AgentProfile

#: Environment names always forwarded to a child runtime. Everything else must
#: be named in the profile's ``env_allowlist``: a worker should not inherit the
#: supervisor's whole environment by accident, and an inherited credential
#: variable can silently change which account a run bills.
BASE_ENV_NAMES: Final[tuple[str, ...]] = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR")

_VERSION_RE: Final[re.Pattern[str]] = re.compile(r"(\d+)\.(\d+)\.(\d+)")


class AdapterError(ProgramError):
    code = "ADAPTER_ERROR"


class AdapterUnavailableError(AdapterError):
    """The runtime is not installed, not authenticated, or too old."""

    code = "ADAPTER_UNAVAILABLE"


@dataclass(frozen=True)
class AdapterCapabilities:
    """What this adapter can actually do. Declared, never inferred."""

    adapter_id: str
    #: Can an interrupted invocation be continued in place rather than relaunched?
    supports_resume: bool
    #: Can the adapter tell, after a crash, whether a given run ever started?
    supports_session_probe: bool
    #: Will the runtime use a session identity the supervisor assigns *before*
    #: launch? Distinct from ``supports_session_probe``, and the distinction is
    #: load-bearing: Claude Code takes ``--session-id`` and can be addressed
    #: from the moment the intent is written, while Codex mints its own and
    #: only reveals it once running. Both are probeable; only one can be
    #: addressed in advance, and conflating them makes a checkpoint record an
    #: identity the runtime never heard of.
    accepts_assigned_session: bool
    #: Does the runtime enforce a per-launch spend cap?
    supports_cost_limit: bool
    #: Does the runtime report a (client-side estimated) cost?
    reports_cost: bool
    #: Does the runtime accept a structured-output schema?
    supports_result_schema: bool
    version: str | None = None


@dataclass(frozen=True)
class AdapterRequest:
    """Everything one invocation needs. Built by the supervisor, never by a worker."""

    program_id: str
    task_id: str
    attempt_id: str
    attempt_number: int
    idempotency_key: str
    instruction: str
    workspace: Path
    profile: AgentProfile
    #: Pre-assigned session identity, written to the checkpoint BEFORE launch.
    session_id: str | None
    #: Set when continuing an interrupted invocation instead of starting one.
    resume_session_id: str | None
    timeout_seconds: int
    evidence_dir: Path
    #: Consulted between polls while a child runs. Returning True asks the
    #: adapter to terminate the child and report an UNCERTAIN outcome.
    cancel_requested: Callable[[], bool] | None = None
    extra_env: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AdapterOutcome:
    """What the adapter observed. Evidence, never a verdict on the task."""

    attempt_id: str
    task_id: str
    #: False only when the runtime was never started at all.
    launched: bool
    confidence: ExecutionConfidence
    #: Terminal state as the adapter classifies it, e.g. "completed",
    #: "api_error", "timeout", "cancelled". Free-form, recorded, not routed on.
    terminal_state: str
    exit_status: int | None
    session_id: str | None
    pid: int | None
    process_start_identity: str | None
    #: Worker's own final message. Recorded; never treated as acceptance.
    reported: str | None
    structured: dict[str, Any] | None
    usage: dict[str, Any]
    estimated_cost_usd: float | None
    evidence: tuple[str, ...]
    #: Set when the adapter can classify the failure itself (a rejected
    #: credential, a refused permission). ``None`` leaves classification to
    #: the supervisor, which then keys on acceptance.
    failure_class: FailureClass | None
    #: How many tool or permission requests the runtime denied during the run.
    #: A run that was denied what it needed and then failed acceptance did not
    #: fail for a reason another attempt would fix, so this changes the failure
    #: class from a retryable one to POLICY_REFUSAL. Adapters that cannot
    #: report denials structurally report 0, and the support matrix says so --
    #: 0 means "not observed", never "definitely none".
    duration_seconds: float
    notes: tuple[str, ...] = ()
    policy_denials: int = 0


class RuntimeAdapter(Protocol):
    """One concrete agent runtime."""

    @property
    def capabilities(self) -> AdapterCapabilities:  # pragma: no cover - protocol
        ...

    def preflight(self, profile: AgentProfile) -> None:  # pragma: no cover - protocol
        """Raise ``AdapterUnavailableError`` if this profile cannot run now."""

    def run(self, request: AdapterRequest) -> AdapterOutcome:  # pragma: no cover
        """Execute one invocation to a terminal state and report what happened."""

    def probe_run_started(
        self, request: AdapterRequest
    ) -> bool | None:  # pragma: no cover - protocol
        """Did the run named by ``request.session_id`` ever start?

        ``True``  -- evidence the runtime started this session.
        ``False`` -- the runtime keeps such evidence and none exists.
        ``None``  -- this adapter cannot tell, so the outcome stays uncertain.
        """


def build_child_env(
    profile: AgentProfile,
    *,
    parent: Mapping[str, str] | None = None,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Construct the child's environment explicitly.

    Two reasons this is not ``os.environ.copy()``:

    1. A worker should get what the profile says it gets. An unrelated
       variable in the operator's shell should not change how a governed run
       behaves.
    2. Credential precedence is a real hazard, observed rather than imagined.
       Claude Code prefers ``ANTHROPIC_API_KEY`` over a logged-in
       subscription when both are present. An inherited key from the
       operator's shell therefore silently redirects every run to a different
       account -- and if that account is out of credit, every launch fails
       with an HTTP 400 that looks nothing like a credential problem. Under
       ``SUBSCRIPTION_OAUTH`` this function omits the variable entirely.

    Values are read here and passed to the child. They are never returned to
    a caller that persists them, never logged, and never placed in evidence.
    """
    source = dict(os.environ if parent is None else parent)
    env: dict[str, str] = {}
    for name in BASE_ENV_NAMES:
        value = source.get(name)
        if value is not None:
            env[name] = value
    for name in profile.env_allowlist:
        value = source.get(name)
        if value is not None:
            env[name] = value
    for name, value in (extra or {}).items():
        env[name] = value
    return env


def version_at_least(observed: str | None, required: str | None) -> bool:
    """Component-wise version comparison over the first ``N.N.N`` in each string.

    Returns True when no requirement is stated. Returns False when a
    requirement is stated and the observed version cannot be parsed -- an
    unreadable version is not a satisfied one.
    """
    if not required:
        return True
    want = _VERSION_RE.search(required)
    if want is None:
        raise AdapterError(
            f"adapter_min_version {required!r} is not a version",
            code="BAD_VERSION_REQUIREMENT",
        )
    if not observed:
        return False
    have = _VERSION_RE.search(observed)
    if have is None:
        return False
    return tuple(int(part) for part in have.groups()) >= tuple(
        int(part) for part in want.groups()
    )


def process_start_identity(pid: int) -> str:
    """Delegate to the existing host implementation.

    Reused rather than reimplemented: ``sdk.host`` already solves PID reuse on
    both Linux and Windows, and two different answers to "is this still the
    same process" is exactly the kind of drift that makes a restart unsafe.
    """
    from project_atlas.orchestration.sdk.host import (
        process_start_identity as _identity,
    )

    return _identity(pid)


def pid_is_alive(pid: int) -> bool:
    from project_atlas.orchestration.sdk.host import pid_is_alive as _alive

    return _alive(pid)


def run_child_to_completion(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    stdin_text: str | None,
    timeout_seconds: int,
    cancel_requested: Callable[[], bool] | None,
    poll_interval: float = 0.25,
    stdout_path: Path | None = None,
) -> tuple[int | None, str, str, int | None, str | None, str]:
    """Run one child process, honouring cancellation and a wall-clock bound.

    Returns ``(exit_status, stdout, stderr, pid, start_identity, terminal)``
    where ``terminal`` is one of ``completed`` / ``timeout`` / ``cancelled``.

    ``stdout_path`` redirects the child's stdout straight to that file instead
    of a pipe, and the content is read back before returning. This is not a
    convenience: a runtime that announces its session identity on its own
    output stream only tells us that identity if the stream survives the
    supervisor dying. Buffered in a pipe it is lost with the process; written
    to a file it is still there for the next start to read -- the difference
    between an interrupted run being resumable and being unknowable.

    The child is started in its own process group and signalled as a group:
    an agent runtime spawns its own children (shells, test runners), and
    signalling only the direct child leaves those running while this process
    believes it has stopped everything. On cancellation and on timeout the
    group is asked to terminate and then killed if it does not; either way the
    external effect of whatever it had already done is *unknown*, which is why
    both paths report a non-``completed`` terminal state that the caller turns
    into ``UNCERTAIN`` rather than into a failure.
    """
    stdout_handle = None
    if stdout_path is not None:
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_handle = stdout_path.open("wb")
    popen_kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "env": dict(env),
        "stdout": stdout_handle if stdout_handle is not None else subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        "text": True,
    }
    if hasattr(os, "setsid"):
        popen_kwargs["start_new_session"] = True
    else:  # pragma: no cover - Windows
        popen_kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )

    started = time.monotonic()
    try:
        process = subprocess.Popen(argv, **popen_kwargs)
    except BaseException:
        if stdout_handle is not None:
            stdout_handle.close()
        raise
    pid = process.pid
    identity = process_start_identity(pid)
    if stdin_text is not None and process.stdin is not None:
        # The child must see EOF on stdin -- `claude -p` reads the prompt
        # until the stream closes -- so the handle is closed here rather than
        # left to `communicate()`. `communicate()` then has to be told the
        # handle is gone: it closes `self.stdin` itself and raises
        # "I/O operation on closed file" if it is still set. Detaching it is
        # the documented way round that, and this was not a theoretical
        # concern: it made every real Claude Code launch fail with an
        # UNCERTAIN outcome on the first end-to-end run.
        try:
            process.stdin.write(stdin_text)
            process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            # The child exited before reading its prompt. Not fatal here: the
            # exit status and whatever it printed are still collected below,
            # and the caller classifies from those.
            pass
        finally:
            with contextlib.suppress(BrokenPipeError, OSError, ValueError):
                process.stdin.close()
            process.stdin = None

    terminal = "completed"
    deadline = started + float(timeout_seconds)
    while True:
        if process.poll() is not None:
            break
        now = time.monotonic()
        if cancel_requested is not None and cancel_requested():
            terminal = "cancelled"
            _terminate_group(process)
            break
        if now >= deadline:
            terminal = "timeout"
            _terminate_group(process)
            break
        time.sleep(poll_interval)

    try:
        stdout, stderr = process.communicate(timeout=30)
    except subprocess.TimeoutExpired:  # pragma: no cover - stubborn child
        process.kill()
        stdout, stderr = process.communicate()

    if stdout_handle is not None:
        stdout_handle.close()
        assert stdout_path is not None
        try:
            stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            # The stream is on disk either way; failing to read it back must
            # not turn a completed run into an error. The file is named in the
            # outcome's evidence regardless.
            stdout = ""
    return process.returncode, stdout or "", stderr or "", pid, identity, terminal


def _terminate_group(process: subprocess.Popen[str]) -> None:
    """SIGTERM the child's process group, then SIGKILL what survives."""
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        else:  # pragma: no cover - Windows
            process.terminate()
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.terminate()
        except OSError:
            return
    try:
        process.wait(timeout=15)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        else:  # pragma: no cover - Windows
            process.kill()
    except (ProcessLookupError, PermissionError, OSError):
        with_fallback = getattr(process, "kill", None)
        if with_fallback is not None:
            try:
                with_fallback()
            except OSError:
                return
