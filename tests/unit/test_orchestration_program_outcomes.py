"""Five things that look alike from a distance, and must not be conflated.

  a clean process exit
  a success-SHAPED event from the runtime
  a refusal (the runtime denied the worker something)
  a provider failure (quota, credentials, the server)
  a VERIFIED task completion

Only the last one means the task is done. The other four are routinely
mistaken for it, and each mistake has a different cost: retrying a refusal
forever, giving up on a transient failure, or -- worst -- recording work as
complete because a process exited 0.

Also covers the credential/account selection policy, which is reported without
reading any credential value.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from project_atlas.orchestration.autonomy.models import NodeState
from project_atlas.orchestration.program.adapters.base import (
    AdapterCapabilities,
    AdapterOutcome,
    AdapterRequest,
)
from project_atlas.orchestration.program.adapters.claude_code import (
    _classify as claude_classify,
)
from project_atlas.orchestration.program.adapters.codex import _classify as codex_classify
from project_atlas.orchestration.program.credentials import (
    credential_report,
    effective_selection,
)
from project_atlas.orchestration.program.loader import load_program
from project_atlas.orchestration.program.models import (
    ExecutionConfidence,
    FailureClass,
)
from project_atlas.orchestration.program.profiles import AgentProfile
from project_atlas.orchestration.program.store import load_state
from project_atlas.orchestration.program.supervisor import ProgramSupervisor

FIXTURE_WORKER = Path(__file__).with_name("_program_fixture_worker.py")


# ------------------------------------------------- the five, kept apart


def test_a_clean_exit_is_not_a_success_shaped_event(tmp_path: Path) -> None:
    """Claude Code returned exit 1 and `"subtype": "success"` in one payload."""
    observed = {
        "subtype": "success",
        "is_error": True,
        "terminal_reason": "api_error",
        "api_error_status": 400,
        "result": "Credit balance is too low",
    }
    confidence, failure, _reason = claude_classify(
        terminal="completed", exit_status=1, parsed=observed, stderr=""
    )
    assert confidence is ExecutionConfidence.FAILED
    assert failure is FailureClass.QUOTA_OR_CREDENTIAL
    _ = tmp_path


def test_a_success_shaped_event_is_not_a_completed_task(tmp_path: Path) -> None:
    """Codex exits 0 with a completed turn while refusing to do the work.

    Observed under `--sandbox read-only`: exit 0, `turn.completed`, and
    "Unable to create `blocked.txt`: the current workspace is read-only".
    """
    events = [
        {"type": "thread.started", "thread_id": "t"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "Unable to create the file."},
        },
        {"type": "turn.completed", "usage": {"output_tokens": 3}},
    ]
    confidence, failure, reason = codex_classify(
        terminal="completed", exit_status=0, events=events, stderr=""
    )
    # The ADAPTER says execution was confirmed -- which is true and is all an
    # adapter can know. Whether the TASK is done is acceptance's answer.
    assert confidence is ExecutionConfidence.CONFIRMED
    assert failure is None
    assert reason == "turn.completed"
    _ = tmp_path


def test_the_five_outcomes_produce_five_distinct_task_states(
    tmp_path: Path,
) -> None:
    """One program, five workers, five different recorded outcomes."""
    workspace = tmp_path / "ws"
    workspace.mkdir()

    # Order matters and the reason is itself a property worth stating: a
    # QUOTA_OR_CREDENTIAL failure stops the whole program, because the next
    # dispatch would hit the same wall. So the provider failure goes last --
    # not to dodge it, but because putting it first would prove only that it
    # halts everything, which a separate assertion below checks anyway.
    scenarios = {
        "verified": ("CONFIRMED", 0, None, False),
        "clean-exit-no-work": ("CONFIRMED", 0, None, False),
        "refusal": ("CONFIRMED", 0, None, True),
        "transient": ("FAILED", 1, FailureClass.TRANSIENT_INFRASTRUCTURE, False),
        "provider-failure": ("FAILED", 1, FailureClass.QUOTA_OR_CREDENTIAL, False),
    }

    class _Scripted:
        @property
        def capabilities(self) -> AdapterCapabilities:
            return AdapterCapabilities(
                adapter_id="scripted",
                supports_resume=False,
                supports_session_probe=True,
                accepts_assigned_session=True,
                supports_cost_limit=False,
                reports_cost=False,
                supports_result_schema=False,
                version="FIXTURE",
            )

        def preflight(self, profile: AgentProfile) -> None:
            _ = profile

        def probe_run_started(self, request: AdapterRequest) -> bool | None:
            _ = request
            return None

        def run(self, request: AdapterRequest) -> AdapterOutcome:
            kind, exit_status, failure, denied = scenarios[request.task_id]
            if request.task_id == "verified":
                (request.workspace / "verified.txt").write_text("x\n", encoding="utf-8")
            return AdapterOutcome(
                attempt_id=request.attempt_id,
                task_id=request.task_id,
                launched=True,
                confidence=ExecutionConfidence(kind),
                terminal_state="completed",
                exit_status=exit_status,
                session_id=request.session_id,
                pid=None,
                process_start_identity=None,
                reported="Task complete." if exit_status == 0 else "failed",
                structured=None,
                usage={},
                estimated_cost_usd=None,
                evidence=(),
                failure_class=failure,
                duration_seconds=0.0,
                policy_denials=2 if denied else 0,
            )

    tasks = [
        {
            "task_id": name,
            "title": name,
            "instruction": "do it",
            "profile_ref": name,
            "mutation_paths": [f"{name}.txt"],
            "surface_id": name,
            "surface_semantic": name.upper().replace("-", "_"),
            "capabilities_required": ["IMPLEMENT"],
            "acceptance": [
                {
                    "check_id": f"{name}-out",
                    "kind": "FILE_EXISTS",
                    "description": f"{name}.txt exists",
                    "path": f"{name}.txt",
                }
            ],
        }
        for name in scenarios
    ]
    payload = {
        "schema_version": 1,
        "program": {
            "program_id": "outcome-program",
            "objective": "outcome distinctness",
            "approved_by": "test",
            "approval_reference": "test",
            "workspace_root": str(workspace),
            "base_pin": "0" * 40,
            "limits": {
                "max_cycles": 30,
                "idle_sleep_seconds": 0.0,
                "max_attempts_per_task": 2,
            },
            "tasks": tasks,
        },
        "profiles": {
            name: {
                "agent_id": f"agent-{name}",
                "adapter": "local-command",
                "credential": "NOT_APPLICABLE",
                "capabilities": ["IMPLEMENT"],
                "limits": {"max_attempts": 2},
                "adapter_options": {"argv": [sys.executable, str(FIXTURE_WORKER)]},
            }
            for name in scenarios
        },
    }
    program = tmp_path / "program.json"
    program.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    adapter = _Scripted()
    report = ProgramSupervisor(
        load_program(program),
        state_root=tmp_path / "state",
        adapters=dict.fromkeys(scenarios, adapter),
        sleeper=lambda _s: None,
    ).start()
    # The provider failure halted the program rather than carrying on into a
    # wall the next dispatch would hit too.
    assert report.stop_reason.value == "HARD_BLOCKER"

    state = load_state(tmp_path / "state")
    assert state is not None
    recorded = {
        task_id: (
            record.state.value,
            record.last_failure_class.value if record.last_failure_class else None,
        )
        for task_id, record in state.tasks.items()
    }

    # Only the verified one is done.
    assert recorded["verified"] == ("CERTIFIED", None)
    # A clean exit that did no work is an acceptance failure, retried then stopped.
    assert recorded["clean-exit-no-work"][0] == "BLOCKED"
    assert recorded["clean-exit-no-work"][1] in {"ACCEPTANCE_FAILED", "NO_PROGRESS"}
    # A refusal is its own class and is never retried.
    assert recorded["refusal"] == ("BLOCKED", "POLICY_REFUSAL")
    # A provider failure is distinct from both, and also never retried.
    assert recorded["provider-failure"] == ("BLOCKED", "QUOTA_OR_CREDENTIAL")
    # A transient failure is the only one that gets another attempt.
    assert recorded["transient"][1] == "TRANSIENT_INFRASTRUCTURE"

    # Five task_ids, and no two of them ended in the same (state, class) pair
    # for the wrong reason: the four distinct classes are all present.
    classes = {value[1] for value in recorded.values()}
    assert {"POLICY_REFUSAL", "QUOTA_OR_CREDENTIAL", "TRANSIENT_INFRASTRUCTURE"} <= classes
    assert None in classes  # the verified one

    # Launch counts encode the retry policy differences.
    assert state.tasks["refusal"].launches == 1
    assert state.tasks["provider-failure"].launches == 1
    assert state.tasks["transient"].launches == 2
    assert state.tasks["verified"].launches == 1


def test_a_worker_reporting_completion_is_recorded_but_never_believed(
    tmp_path: Path,
) -> None:
    """The claim is kept as evidence; it is not what decides the task."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    payload = {
        "schema_version": 1,
        "program": {
            "program_id": "claim-program",
            "objective": "claim vs acceptance",
            "approved_by": "test",
            "approval_reference": "test",
            "workspace_root": str(workspace),
            "base_pin": "0" * 40,
            "limits": {
                "max_cycles": 6,
                "idle_sleep_seconds": 0.0,
                "max_attempts_per_task": 1,
            },
            "tasks": [
                {
                    "task_id": "claims",
                    "title": "claims",
                    "instruction": "do it",
                    "profile_ref": "impl",
                    "mutation_paths": ["never.txt"],
                    "surface_id": "claims",
                    "surface_semantic": "CLAIMS",
                    "capabilities_required": ["IMPLEMENT"],
                    "acceptance": [
                        {
                            "check_id": "out",
                            "kind": "FILE_EXISTS",
                            "description": "never.txt exists",
                            "path": "never.txt",
                        }
                    ],
                }
            ],
        },
        "profiles": {
            "impl": {
                "agent_id": "claimer",
                "adapter": "local-command",
                "credential": "NOT_APPLICABLE",
                "capabilities": ["IMPLEMENT"],
                "limits": {"max_attempts": 1},
                "env_allowlist": ["ATLAS_FIXTURE_MODE", "ATLAS_PROGRAM_TASK"],
                "adapter_options": {"argv": [sys.executable, str(FIXTURE_WORKER)]},
            }
        },
    }
    program = tmp_path / "program.json"
    program.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    import os

    os.environ["ATLAS_FIXTURE_MODE"] = "claim-only"
    try:
        ProgramSupervisor(
            load_program(program),
            state_root=tmp_path / "state",
            sleeper=lambda _s: None,
        ).start()
    finally:
        os.environ["ATLAS_FIXTURE_MODE"] = "write"

    state = load_state(tmp_path / "state")
    assert state is not None
    attempt = next(iter(state.attempts.values()))
    assert "Task complete" in (attempt.worker_reported or "")
    assert attempt.exit_status == 0
    assert attempt.confidence is ExecutionConfidence.CONFIRMED
    assert attempt.acceptance_passed is False
    assert state.tasks["claims"].state is NodeState.BLOCKED

    # The evidence file keeps the claim next to the verdict that overrode it.
    evidence = json.loads(
        next(
            (tmp_path / "state" / ".atlas" / "orchestration" / "program" / "evidence")
            .glob("*.acceptance.json")
        ).read_text(encoding="utf-8")
    )
    assert evidence["worker_report_is_not_acceptance"] is True
    assert evidence["acceptance"]["passed"] is False


# ------------------------------------------------------------- credentials


def _profile(**overrides: Any) -> AgentProfile:
    body: dict[str, Any] = {
        "profile_id": "impl",
        "agent_id": "impl",
        "adapter": "claude-code",
        "capabilities": ["IMPLEMENT"],
        "credential": "SUBSCRIPTION_OAUTH",
    }
    body.update(overrides)
    return AgentProfile.model_validate(body)


def test_an_inherited_key_is_reported_present_but_withheld() -> None:
    report = effective_selection(
        _profile(), environment={"ANTHROPIC_API_KEY": "sk-secret-value"}
    )
    assert report["credential_names_present_in_environment"] == ["ANTHROPIC_API_KEY"]
    assert report["credential_names_forwarded_to_the_worker"] == []
    assert report["credential_names_present_but_withheld"] == ["ANTHROPIC_API_KEY"]
    assert report["values_read"] is False
    # The value must appear nowhere in the report, at any depth.
    assert "sk-secret-value" not in json.dumps(report)


def test_a_contradictory_profile_is_warned_about_not_resolved() -> None:
    report = effective_selection(
        _profile(env_allowlist=["ANTHROPIC_API_KEY"]),
        environment={"ANTHROPIC_API_KEY": "x"},
    )
    assert report["warnings"], "declaring OAuth while forwarding a key must warn"
    assert "would be a fiction" in report["warnings"][0]


def test_bare_mode_cannot_use_a_subscription() -> None:
    report = effective_selection(_profile(isolated_runtime=True), environment={})
    assert any("--bare" in warning for warning in report["warnings"])


def test_an_api_key_profile_that_forwards_nothing_is_warned_about() -> None:
    report = effective_selection(
        _profile(credential="ANTHROPIC_API_KEY_ENV"), environment={}
    )
    assert any("does not" in warning for warning in report["warnings"])


def test_the_policy_states_what_it_will_never_do(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    payload = {
        "schema_version": 1,
        "program": {
            "program_id": "cred-program",
            "objective": "credential policy",
            "approved_by": "test",
            "approval_reference": "test",
            "workspace_root": str(workspace),
            "base_pin": "0" * 40,
            "limits": {"max_cycles": 2},
            "tasks": [
                {
                    "task_id": "one",
                    "title": "one",
                    "instruction": "do it",
                    "profile_ref": "impl",
                    "mutation_paths": ["one.txt"],
                    "surface_id": "one",
                    "surface_semantic": "ONE",
                    "capabilities_required": ["IMPLEMENT"],
                    "acceptance": [
                        {
                            "check_id": "o",
                            "kind": "FILE_EXISTS",
                            "description": "one.txt",
                            "path": "one.txt",
                        }
                    ],
                }
            ],
        },
        "profiles": {
            "impl": {
                "agent_id": "impl",
                "adapter": "claude-code",
                "capabilities": ["IMPLEMENT"],
                "credential": "SUBSCRIPTION_OAUTH",
            }
        },
    }
    program = tmp_path / "program.json"
    program.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    report = credential_report(
        load_program(program), environment={"ANTHROPIC_API_KEY": "secret"}
    )
    joined = " ".join(report["policy"])
    assert "never inherited wholesale" in joined
    assert "no account is switched automatically" in joined
    assert "never discarded" in joined
    assert "no spending limit is raised" in joined
    assert "secret" not in json.dumps(report)
