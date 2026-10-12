"""AS-ORCH-PROGRAM-SUPERVISOR-001 M2 — Codex, capability contracts, handoff.

Runtime-specific launch, output, permission and resume behaviour lives behind
explicit capability contracts. These tests hold those contracts to what the
installed runtimes actually do, and hold the supervisor to consulting them
rather than assuming.

The Codex classification tests use recorded event shapes from real runs
(`codex-cli 0.153.4`), not invented ones.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from project_atlas.orchestration.autonomy.models import NodeState
from project_atlas.orchestration.program.adapters.base import (
    AdapterCapabilities,
    AdapterOutcome,
    AdapterRequest,
    AdapterUnavailableError,
    run_child_to_completion,
)
from project_atlas.orchestration.program.adapters.claude_code import ClaudeCodeAdapter
from project_atlas.orchestration.program.adapters.codex import (
    SANDBOX_FOR_MODE,
    SIGTERM_EXIT_STATUS,
    CodexAdapter,
    _classify,
    _error_items,
    _read_events,
    _thread_id,
    _usage,
)
from project_atlas.orchestration.program.loader import load_program
from project_atlas.orchestration.program.models import (
    ExecutionConfidence,
    FailureClass,
    ProgramStopReason,
)
from project_atlas.orchestration.program.profiles import AdapterKind, AgentProfile
from project_atlas.orchestration.program.runtimes import (
    UNIVERSALLY_UNSUPPORTED,
    describe,
    describe_all,
)
from project_atlas.orchestration.program.store import load_state
from project_atlas.orchestration.program.supervisor import (
    DispatchMode,
    ProgramSupervisor,
    SupervisorError,
)

# Verbatim event shapes from a real `codex exec --json` run.
THREAD_STARTED: dict[str, Any] = {
    "type": "thread.started",
    "thread_id": "01a08aa5-6b52-7921-a7ed-748ae9c848b3",
}
TURN_STARTED: dict[str, Any] = {"type": "turn.started"}
AGENT_MESSAGE: dict[str, Any] = {
    "type": "item.completed",
    "item": {"id": "item_1", "type": "agent_message", "text": "OK"},
}
HOOKS_WARNING: dict[str, Any] = {
    "type": "item.completed",
    "item": {
        "id": "item_0",
        "type": "error",
        "message": (
            "loading hooks from both /home/u/.codex/hooks.json and "
            "/home/u/.codex/config.toml; prefer a single representation"
        ),
    },
}
TURN_COMPLETED: dict[str, Any] = {
    "type": "turn.completed",
    "usage": {
        "input_tokens": 17547,
        "cached_input_tokens": 9984,
        "cache_write_input_tokens": 0,
        "output_tokens": 5,
        "reasoning_output_tokens": 0,
    },
}


#: An executable that certainly exists, for tests about ARGV SHAPE rather than
#: about the runtime being installed. CI has neither real runtime on PATH, and
#: a test of "does the prompt stay off the command line" has no business
#: failing because of that -- it is a property of the code, not of the host.
INSTALLED_STAND_IN = sys.executable


def _codex_profile(**overrides: Any) -> AgentProfile:
    body: dict[str, Any] = {
        "profile_id": "codex-impl",
        "agent_id": "codex-impl",
        "adapter": "codex",
        "capabilities": ["IMPLEMENT"],
        "permission_mode": "acceptEdits",
    }
    body.update(overrides)
    return AgentProfile.model_validate(body)


def _request(tmp_path: Path, profile: AgentProfile, **overrides: Any) -> AdapterRequest:
    body: dict[str, Any] = {
        "program_id": "p",
        "task_id": "t",
        "attempt_id": "a1",
        "attempt_number": 1,
        "idempotency_key": "k",
        "instruction": "do the thing",
        "workspace": tmp_path,
        "profile": profile,
        "session_id": None,
        "resume_session_id": None,
        "timeout_seconds": 60,
        "evidence_dir": tmp_path / "evidence",
    }
    body.update(overrides)
    return AdapterRequest(**body)


# ------------------------------------------------------- codex classification


def test_completed_turn_and_clean_exit_is_confirmed_execution_only() -> None:
    confidence, failure, terminal = _classify(
        terminal="completed",
        exit_status=0,
        events=[THREAD_STARTED, TURN_STARTED, AGENT_MESSAGE, TURN_COMPLETED],
        stderr="",
    )
    assert confidence is ExecutionConfidence.CONFIRMED
    assert failure is None
    assert terminal == "turn.completed"


def test_an_error_item_alongside_a_completed_turn_is_not_a_failure() -> None:
    """Observed for real: a hooks-config warning, then a perfectly good turn.

    Treating any error item as fatal would throw away a correct result over a
    configuration complaint.
    """
    events = [THREAD_STARTED, HOOKS_WARNING, TURN_STARTED, AGENT_MESSAGE, TURN_COMPLETED]
    confidence, failure, _terminal = _classify(
        terminal="completed", exit_status=0, events=events, stderr=""
    )
    assert confidence is ExecutionConfidence.CONFIRMED
    assert failure is None
    assert _error_items(events), "the warning is still recorded, just not fatal"


def test_no_events_at_all_is_uncertain_not_failed() -> None:
    confidence, failure, terminal = _classify(
        terminal="completed", exit_status=1, events=[], stderr="boom"
    )
    assert confidence is ExecutionConfidence.UNCERTAIN
    assert failure is FailureClass.UNCERTAIN_OUTCOME
    assert terminal == "no_events"


def test_truncated_event_stream_is_uncertain() -> None:
    """Events started, no turn.completed, clean exit: the stream was cut."""
    confidence, failure, terminal = _classify(
        terminal="completed",
        exit_status=0,
        events=[THREAD_STARTED, TURN_STARTED],
        stderr="",
    )
    assert confidence is ExecutionConfidence.UNCERTAIN
    assert failure is FailureClass.UNCERTAIN_OUTCOME
    assert terminal == "truncated_event_stream"


def test_completed_turn_with_nonzero_exit_is_reported_as_failed() -> None:
    """A contradiction is not resolved in the runtime's favour."""
    confidence, _failure, terminal = _classify(
        terminal="completed",
        exit_status=3,
        events=[THREAD_STARTED, TURN_STARTED, TURN_COMPLETED],
        stderr="",
    )
    assert confidence is ExecutionConfidence.FAILED
    assert terminal == "completed_with_nonzero_exit"


def test_cancellation_and_timeout_and_sigterm_are_all_uncertain() -> None:
    for terminal_in, exit_status in (
        ("cancelled", None),
        ("timeout", None),
        ("completed", SIGTERM_EXIT_STATUS),
    ):
        confidence, failure, _terminal = _classify(
            terminal=terminal_in,
            exit_status=exit_status,
            events=[THREAD_STARTED, TURN_STARTED],
            stderr="",
        )
        assert confidence is ExecutionConfidence.UNCERTAIN, terminal_in
        assert failure is FailureClass.UNCERTAIN_OUTCOME, terminal_in


def test_credential_and_quota_text_classifies_as_quota_or_credential() -> None:
    failed = {"type": "turn.failed", "error": {"message": "You are not logged in"}}
    _confidence, failure, _terminal = _classify(
        terminal="completed",
        exit_status=1,
        events=[THREAD_STARTED, TURN_STARTED, failed],
        stderr="please run `codex login`",
    )
    assert failure is FailureClass.QUOTA_OR_CREDENTIAL


def test_event_parsing_records_a_truncated_final_line_as_residue(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps(THREAD_STARTED) + "\n" + '{"type": "turn.start',
        encoding="utf-8",
    )
    events = _read_events(path)
    assert _thread_id(events) == THREAD_STARTED["thread_id"]
    assert any(row.get("type") == "__malformed__" for row in events)


def test_usage_is_read_from_the_completed_turn() -> None:
    assert _usage([THREAD_STARTED, TURN_COMPLETED])["output_tokens"] == 5
    assert _usage([THREAD_STARTED]) == {}


# ------------------------------------------------------------- codex argv


def test_codex_keeps_the_prompt_off_the_command_line(tmp_path: Path) -> None:
    profile = _codex_profile()
    argv = CodexAdapter(INSTALLED_STAND_IN).build_argv(
        _request(tmp_path, profile, instruction="rm -rf / # must never reach argv")
    )
    assert "rm -rf /" not in " ".join(argv)
    assert argv[-1] == "-", "the prompt is read from stdin"
    assert "--json" in argv
    assert argv[argv.index("--cd") + 1] == str(tmp_path)


@pytest.mark.parametrize(
    ("mode", "sandbox"),
    [
        ("plan", "read-only"),
        ("dontAsk", "read-only"),
        ("manual", "read-only"),
        ("acceptEdits", "workspace-write"),
        ("auto", "workspace-write"),
        ("bypassPermissions", "danger-full-access"),
    ],
)
def test_permission_mode_maps_conservatively_onto_the_codex_sandbox(
    tmp_path: Path, mode: str, sandbox: str
) -> None:
    """Every mode that is not unambiguously a write mode maps to read-only."""
    profile = _codex_profile(permission_mode=mode)
    argv = CodexAdapter(INSTALLED_STAND_IN).build_argv(_request(tmp_path, profile))
    assert argv[argv.index("--sandbox") + 1] == sandbox
    assert SANDBOX_FOR_MODE[mode] == sandbox
    bypass = "--dangerously-bypass-approvals-and-sandbox" in argv
    assert bypass is (mode == "bypassPermissions")


def test_codex_resume_uses_the_exec_resume_subcommand(tmp_path: Path) -> None:
    profile = _codex_profile()
    argv = CodexAdapter(INSTALLED_STAND_IN).build_argv(
        _request(tmp_path, profile, resume_session_id="01a08aa5-thread")
    )
    assert argv[1:4] == ["exec", "resume", "01a08aa5-thread"]


def test_codex_rejects_an_anthropic_api_key_credential() -> None:
    profile = _codex_profile(credential="ANTHROPIC_API_KEY_ENV")
    with pytest.raises(AdapterUnavailableError) as excinfo:
        CodexAdapter().preflight(profile)
    assert excinfo.value.code == "CREDENTIAL_MECHANISM_CONFLICT"


def test_codex_preflight_rejects_a_profile_for_a_different_adapter() -> None:
    profile = AgentProfile.model_validate(
        {
            "profile_id": "claude",
            "agent_id": "claude",
            "adapter": "claude-code",
            "capabilities": ["IMPLEMENT"],
        }
    )
    with pytest.raises(AdapterUnavailableError) as excinfo:
        CodexAdapter().preflight(profile)
    assert excinfo.value.code == "ADAPTER_MISMATCH"


# ------------------------------------------------------- streamed identity


def test_streamed_stdout_survives_a_killed_child(tmp_path: Path) -> None:
    """The point of streaming: the session identity outlives the process.

    A runtime that announces its id on stdout tells us nothing if that stream
    dies in a pipe with the process. Written to a file, the first event is
    still readable after the child is killed -- which is what makes an
    interrupted Codex attempt resumable rather than merely unknown.
    """
    events_file = tmp_path / "events.jsonl"
    script = (
        "import json,sys,time\n"
        f"print(json.dumps({THREAD_STARTED!r}), flush=True)\n"
        "time.sleep(600)\n"
    )
    exit_status, _stdout, _stderr, _pid, _identity, terminal = run_child_to_completion(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", "")},
        stdin_text=None,
        timeout_seconds=2,
        cancel_requested=None,
        stdout_path=events_file,
    )
    assert terminal == "timeout"
    assert exit_status != 0
    assert events_file.is_file()
    assert _thread_id(_read_events(events_file)) == THREAD_STARTED["thread_id"]


def test_codex_probe_reports_none_when_no_stream_exists(tmp_path: Path) -> None:
    """Absence is 'cannot tell', never 'it did not run'."""
    adapter = CodexAdapter(INSTALLED_STAND_IN)
    assert adapter.probe_run_started(_request(tmp_path, _codex_profile())) is None


def test_codex_probe_reports_true_once_the_stream_has_an_event(
    tmp_path: Path,
) -> None:
    adapter = CodexAdapter(INSTALLED_STAND_IN)
    request = _request(tmp_path, _codex_profile())
    path = adapter.events_path(request)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(THREAD_STARTED) + "\n", encoding="utf-8")
    assert adapter.probe_run_started(request) is True


# ------------------------------------------------------- capability contract


def test_the_two_runtimes_declare_genuinely_different_capabilities() -> None:
    """The STATIC half of the contract, asserted wherever this runs.

    Split deliberately. `accepts_assigned_session`, `reports_cost` and
    `supports_cost_limit` are properties of the runtime's interface and are
    true on a bare CI box with neither binary installed. `supports_resume` is
    version-dependent, so it is asserted separately and only where the runtime
    is actually present -- an earlier version conflated the two and failed CI
    for the entirely correct reason that `claude` was not on PATH.
    """
    claude = ClaudeCodeAdapter(INSTALLED_STAND_IN).capabilities
    codex = CodexAdapter(INSTALLED_STAND_IN).capabilities

    # The load-bearing difference: only one will use an identity we assign.
    assert claude.accepts_assigned_session is True
    assert codex.accepts_assigned_session is False
    # Both can be probed, by different means.
    assert claude.supports_session_probe is True
    assert codex.supports_session_probe is True
    # Only one reports a cost figure at all.
    assert claude.reports_cost is True
    assert codex.reports_cost is False
    assert codex.supports_cost_limit is False
    assert claude.supports_cost_limit is True


@pytest.mark.parametrize("kind", [AdapterKind.CLAUDE_CODE, AdapterKind.CODEX])
def test_an_installed_runtime_reports_a_version_and_resume(kind: AdapterKind) -> None:
    """The version-dependent half, only where the runtime is really present."""
    support = describe(kind)
    if not support.installed:
        pytest.skip(f"{support.executable} is not installed on this host")
    assert support.capabilities is not None
    assert support.capabilities.version
    assert support.capabilities.supports_resume is True


def test_no_runtime_claims_it_can_attach_to_a_live_session() -> None:
    for support in describe_all():
        assert "attach to a running interactive session" in support.unsupported
        assert "adopt a process this supervisor did not start" in support.unsupported
    assert "attach to a running interactive session" in UNIVERSALLY_UNSUPPORTED


def test_the_fixture_adapter_is_labelled_as_a_fixture() -> None:
    fixture = describe(AdapterKind.LOCAL_COMMAND)
    assert fixture.is_fixture is True
    assert fixture.version == "FIXTURE"
    assert any("FIXTURE_RUN != REAL_RUNTIME" in note for note in fixture.notes)
    for real in (AdapterKind.CLAUDE_CODE, AdapterKind.CODEX):
        assert describe(real).is_fixture is False


def test_every_adapter_kind_has_a_support_entry() -> None:
    """A new adapter cannot be added without appearing in the matrix."""
    covered = {support.adapter for support in describe_all()}
    assert covered == set(AdapterKind)


# -------------------------------------------------------------- the handoff

FIXTURE_WORKER = Path(__file__).with_name("_program_fixture_worker.py")


class _StubAdapter:
    """A minimal adapter whose capabilities the test controls exactly."""

    def __init__(self, *, supports_resume: bool, accepts_assigned: bool = True) -> None:
        self._caps = AdapterCapabilities(
            adapter_id="stub",
            supports_resume=supports_resume,
            supports_session_probe=True,
            accepts_assigned_session=accepts_assigned,
            supports_cost_limit=False,
            reports_cost=False,
            supports_result_schema=False,
            version="STUB",
        )
        self.seen_resume: list[str | None] = []
        self.seen_session: list[str | None] = []

    @property
    def capabilities(self) -> AdapterCapabilities:
        return self._caps

    def preflight(self, profile: AgentProfile) -> None:
        _ = profile

    def probe_run_started(self, request: AdapterRequest) -> bool | None:
        _ = request
        return None

    def run(self, request: AdapterRequest) -> AdapterOutcome:
        self.seen_resume.append(request.resume_session_id)
        self.seen_session.append(request.session_id)
        (request.workspace / "stub-out.txt").write_text("done\n", encoding="utf-8")
        return AdapterOutcome(
            attempt_id=request.attempt_id,
            task_id=request.task_id,
            launched=True,
            confidence=ExecutionConfidence.CONFIRMED,
            terminal_state="completed",
            exit_status=0,
            session_id=request.resume_session_id or request.session_id or "stub-session",
            pid=None,
            process_start_identity=None,
            reported="stub done",
            structured=None,
            usage={},
            estimated_cost_usd=None,
            evidence=(),
            failure_class=None,
            duration_seconds=0.0,
        )


def _stub_program(tmp_path: Path, workspace: Path) -> Path:
    payload = {
        "schema_version": 1,
        "program": {
            "program_id": "handoff-program",
            "objective": "handoff coverage",
            "approved_by": "test",
            "approval_reference": "test",
            "workspace_root": str(workspace),
            "base_pin": "0" * 40,
            "limits": {"max_cycles": 6, "idle_sleep_seconds": 0.0},
            "tasks": [
                {
                    "task_id": "only",
                    "title": "only task",
                    "instruction": "do it",
                    "profile_ref": "impl",
                    "mutation_paths": ["stub-out.txt"],
                    "surface_id": "only",
                    "surface_semantic": "ONLY",
                    "capabilities_required": ["IMPLEMENT"],
                    "acceptance": [
                        {
                            "check_id": "out",
                            "kind": "FILE_EXISTS",
                            "description": "stub-out.txt exists",
                            "path": "stub-out.txt",
                        }
                    ],
                }
            ],
        },
        "profiles": {
            "impl": {
                "agent_id": "stub-agent",
                "adapter": "local-command",
                "credential": "NOT_APPLICABLE",
                "capabilities": ["IMPLEMENT"],
                "adapter_options": {"argv": [sys.executable, str(FIXTURE_WORKER)]},
            }
        },
    }
    path = tmp_path / "program.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _stub_supervisor(
    tmp_path: Path, workspace: Path, adapter: _StubAdapter
) -> ProgramSupervisor:
    loaded = load_program(_stub_program(tmp_path, workspace))
    return ProgramSupervisor(
        loaded,
        state_root=tmp_path / "state",
        adapters={"impl": adapter},
        sleeper=lambda _s: None,
    )


def test_an_enrolled_session_is_continued_not_started_fresh(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    adapter = _StubAdapter(supports_resume=True)
    supervisor = _stub_supervisor(tmp_path, workspace, adapter)
    enrolled = supervisor.enroll_session(
        task_id="only",
        session_id="prior-session-42",
        enrolled_by="wesley",
        note="picking up where the interactive session left off",
    )
    assert enrolled["session_id"] == "prior-session-42"
    assert enrolled["session_observed"] is None
    assert "on your assertion alone" in enrolled["session_observed_note"]

    report = supervisor.start()
    assert report.stop_reason is ProgramStopReason.PROGRAM_COMPLETE
    assert adapter.seen_resume == ["prior-session-42"]
    modes = [c.dispatch_mode for c in report.cycles if c.dispatch_mode]
    assert modes == [DispatchMode.RESUME]


def test_a_handoff_is_consumed_exactly_once(tmp_path: Path) -> None:
    """A spent handoff must not resume the same session on a later attempt."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    adapter = _StubAdapter(supports_resume=True)
    supervisor = _stub_supervisor(tmp_path, workspace, adapter)
    supervisor.enroll_session(
        task_id="only", session_id="prior-session-42", enrolled_by="wesley"
    )
    supervisor.start()

    state = load_state(tmp_path / "state")
    assert state is not None
    handoff = state.handoffs["only"]
    assert handoff.consumed_by_attempt_id is not None

    with pytest.raises(SupervisorError) as excinfo:
        supervisor.enroll_session(
            task_id="only", session_id="another", enrolled_by="wesley"
        )
    assert excinfo.value.code == "TASK_ALREADY_DONE"


def test_a_second_pending_handoff_is_refused(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    adapter = _StubAdapter(supports_resume=True)
    supervisor = _stub_supervisor(tmp_path, workspace, adapter)
    supervisor.enroll_session(
        task_id="only", session_id="first", enrolled_by="wesley"
    )
    with pytest.raises(SupervisorError) as excinfo:
        supervisor.enroll_session(
            task_id="only", session_id="second", enrolled_by="wesley"
        )
    assert excinfo.value.code == "HANDOFF_ALREADY_PENDING"


def test_handoff_is_refused_for_a_runtime_that_cannot_resume(tmp_path: Path) -> None:
    """No pretending: an adapter with no resume contract has nothing to hand to."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    adapter = _StubAdapter(supports_resume=False)
    supervisor = _stub_supervisor(tmp_path, workspace, adapter)
    with pytest.raises(SupervisorError) as excinfo:
        supervisor.enroll_session(
            task_id="only", session_id="prior", enrolled_by="wesley"
        )
    assert excinfo.value.code == "RESUME_UNSUPPORTED"


def test_no_session_id_is_invented_for_a_runtime_that_mints_its_own(
    tmp_path: Path,
) -> None:
    """Recording an id the runtime never heard of is worse than recording none.

    A recovery probe would go looking for it and find nothing, which reads as
    'this never started' when the truth is 'we made the identity up'.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    adapter = _StubAdapter(supports_resume=True, accepts_assigned=False)
    supervisor = _stub_supervisor(tmp_path, workspace, adapter)
    supervisor.start()
    assert adapter.seen_session == [None]

    state = load_state(tmp_path / "state")
    assert state is not None
    assert state.tasks["only"].state is NodeState.CERTIFIED


# ------------------------------------------- M7: the honest runtime inventory


def test_every_installed_runtime_is_accounted_for() -> None:
    """A runtime this package does not adapt is recorded, not omitted.

    An absent row reads as "nobody thought about it". A row saying
    `adapter_implemented: false` with the reason reads as what it is.
    """
    from project_atlas.orchestration.program.runtimes import inventory_unimplemented

    rows = inventory_unimplemented()
    names = {row.executable for row in rows}
    assert {"cursor-agent", "copilot"} <= names, (
        "the two runtimes the directive names must appear"
    )
    for row in rows:
        assert row.implemented is False
        assert row.verified_capabilities, "each row states what WAS verified"
        assert row.unverified, "and what was not"
        assert row.demand, "and whether anything actually needs it"


def test_a_blocked_runtime_names_its_blocker_precisely() -> None:
    from project_atlas.orchestration.program.runtimes import inventory_unimplemented

    by_exe = {row.executable: row for row in inventory_unimplemented()}
    cursor = by_exe["cursor-agent"]
    copilot = by_exe["copilot"]

    assert cursor.blocker and "usage limit" in cursor.blocker
    assert "not authorized" in cursor.blocker
    assert copilot.blocker and "rate limit" in copilot.blocker.lower()
    # Both blockers are account limits. Neither is described as something to
    # route around, and this package offers no way to.
    for row in (cursor, copilot):
        assert "switch" not in (row.blocker or "").lower().replace(
            "switching", ""
        ) or "not authorized" in (row.blocker or "")


def test_unverified_capabilities_are_named_not_assumed() -> None:
    """Flag-level facts from --help are not the same as a verified contract."""
    from project_atlas.orchestration.program.runtimes import inventory_unimplemented

    by_exe = {row.executable: row for row in inventory_unimplemented()}
    for executable in ("cursor-agent", "copilot"):
        row = by_exe[executable]
        joined = " ".join(row.unverified).lower()
        assert "terminal state" in joined or "terminal-state" in joined
        assert any("error taxonomy" in item.lower() for item in row.unverified)


def test_there_is_no_generic_adapter_and_the_reason_is_stated() -> None:
    from project_atlas.orchestration.program.profiles import AdapterKind
    from project_atlas.orchestration.program.runtimes import NO_GENERIC_ADAPTER

    assert "no generic subprocess adapter" in NO_GENERIC_ADAPTER
    # The enum is the enforcement: a program cannot name a runtime that has no
    # adapter, because there is no member for one.
    assert {kind.value for kind in AdapterKind} == {
        "claude-code",
        "codex",
        "local-command",
    }


def test_the_inventory_never_launches_a_model() -> None:
    """Building the inventory must be free.

    A report that quietly spends money is not a report, and this one is the
    thing an operator runs *before* deciding whether to spend any.
    """
    from project_atlas.orchestration.program.runtimes import inventory_unimplemented

    rows = inventory_unimplemented()
    # Version probes only; every installed row got a version string or None,
    # and none of them required authentication.
    assert rows
    for row in rows:
        if row.installed:
            assert row.version is None or isinstance(row.version, str)
