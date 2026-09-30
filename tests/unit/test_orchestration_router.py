"""AS-ORCH-001B router: composition, consistency, privilege, and CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from project_atlas.cli import EXIT_ERROR, EXIT_OK, main
from project_atlas.orchestration import (
    POLICY_ID,
    ROUTING_PACKAGE_ID,
    classify_envelope,
    parse_envelope,
    route,
    route_payload,
    source_result_digest,
    validate_and_classify,
)
from project_atlas.orchestration.models import (
    NextTransition,
    OrchestrationDecision,
    OrchestrationRoute,
    ProducerRole,
    ResultOutcome,
    RouteKind,
    TaskType,
    WorkflowState,
)
from project_atlas.orchestration.policy import POLICY
from project_atlas.orchestration.router import RouteConsistencyError
from project_atlas.schema import validate_record

ORCH_DIR = Path(__file__).resolve().parents[2] / "src" / "project_atlas" / "orchestration"


def _payload(
    *,
    role: str = "local",
    task_id: str = "D-137",
    attempt: int = 1,
    outcome: str = "PASS",
    state: str = "CERTIFIED",
    target_moved: bool = False,
    unauthorized_mutations: int = 0,
    receipt_status: str | None = "valid",
    include_receipt: bool = True,
    blockers: list[dict[str, str]] | None = None,
    requested_transition: str | None = None,
    extras: dict[str, bool | int | str] | None = None,
) -> dict[str, Any]:
    observations: dict[str, Any] = {
        "target_moved": target_moved,
        "unauthorized_mutations": unauthorized_mutations,
    }
    if extras is not None:
        observations["extras"] = extras
    payload: dict[str, Any] = {
        "schema_version": 1,
        "producer": {"role": role, "agent_id": f"{role}-agent"},
        "task": {"id": task_id, "attempt": attempt},
        "outcome": outcome,
        "state": state,
        "observations": observations,
        "blockers": blockers or [],
        "requested_transition": requested_transition,
    }
    if include_receipt:
        payload["receipt"] = {
            "receipt_id": "ASR-1234567890abcdef",
            "status": receipt_status,
        }
    else:
        payload["receipt"] = None
    return payload


def _pipeline(payload: dict[str, Any]) -> tuple[Any, OrchestrationDecision, OrchestrationRoute]:
    envelope = parse_envelope(payload)
    decision = classify_envelope(envelope)
    routed = route(decision, envelope)
    return envelope, decision, routed


def _assert_non_privileged(routed: OrchestrationRoute) -> None:
    assert routed.execution_authorized is False
    assert routed.permissions.merge is False
    assert routed.permissions.production_mutation is False
    assert routed.permissions.authority_grant is False
    assert routed.permissions.repository_write is False
    assert routed.permissions.branch_write is False
    assert routed.permissions.pull_request_write is False
    if routed.task is not None:
        assert routed.task.execution_authorized is False
        assert routed.task.permissions.merge is False
        assert routed.task.permissions.production_mutation is False
        assert routed.task.permissions.authority_grant is False


def test_scenario_a_local_pass_to_integration_verify() -> None:
    envelope, decision, routed = _pipeline(_payload())
    assert decision.next_transition == NextTransition.INTEGRATION_VERIFY
    assert routed.route_kind == RouteKind.TASK
    assert routed.target.role == ProducerRole.INTEGRATION
    assert routed.task_type == TaskType.CANDIDATE_VERIFICATION
    assert routed.owner_gate is False
    assert routed.dispatchable is True
    assert routed.execution_authorized is False
    assert routed.task is not None
    assert routed.task.target.role == ProducerRole.INTEGRATION
    _assert_non_privileged(routed)
    validate_record(routed, "orchestration-route")
    validate_record(routed.task, "task-directive")
    assert routed.source_result_digest == source_result_digest(envelope)


def test_scenario_b_target_moved_recertification() -> None:
    _envelope, decision, routed = _pipeline(_payload(target_moved=True))
    assert decision.next_transition == NextTransition.RECERTIFY_REQUIRED
    assert routed.route_kind == RouteKind.TASK
    assert routed.target.role == ProducerRole.INTEGRATION
    assert routed.task_type == TaskType.RECERTIFICATION
    assert routed.execution_authorized is False
    _assert_non_privileged(routed)


def test_scenario_c_merge_eligible_owner_gate() -> None:
    _envelope, decision, routed = _pipeline(_payload(role="integration", state="MERGE_ELIGIBLE"))
    assert decision.next_transition == NextTransition.OWNER_REQUIRED
    assert routed.route_kind == RouteKind.OWNER_GATE
    assert routed.owner_gate is True
    assert routed.dispatchable is False
    assert routed.execution_authorized is False
    assert routed.task is None
    assert routed.task_type is None
    assert routed.permissions.merge is False
    _assert_non_privileged(routed)


def test_scenario_d_rejected_terminal() -> None:
    routed = route_payload(_payload(include_receipt=False))
    assert routed.route_kind == RouteKind.TERMINAL
    assert routed.transition == NextTransition.REJECTED
    assert routed.dispatchable is False
    assert routed.execution_authorized is False
    assert routed.task is None
    _assert_non_privileged(routed)
    invalid = route_payload("{")
    assert invalid.route_kind == RouteKind.TERMINAL
    assert invalid.transition == NextTransition.REJECTED
    assert invalid.dispatchable is False
    assert invalid.execution_authorized is False


def test_acceptance_matrix_remaining_transitions() -> None:
    autonomous = route_payload(_payload(role="integration"))
    assert autonomous.transition == NextTransition.AUTONOMOUS_RECONCILE
    assert autonomous.target.role == ProducerRole.AUTONOMOUS
    assert autonomous.task_type == TaskType.PROGRAM_RECONCILIATION
    assert autonomous.execution_authorized is False

    remediation = route_payload(_payload(role="integration", outcome="FAIL"))
    assert remediation.transition == NextTransition.REMEDIATION_REQUIRED
    assert remediation.target.role == ProducerRole.LOCAL
    assert remediation.task_type == TaskType.REMEDIATION
    assert remediation.execution_authorized is False

    blocked = route_payload(_payload(outcome="FAIL"))
    assert blocked.transition == NextTransition.BLOCKED
    assert blocked.route_kind == RouteKind.TERMINAL
    assert blocked.dispatchable is False

    unknown = route_payload(_payload(role="autonomous"))
    assert unknown.transition == NextTransition.BLOCKED_UNKNOWN_STATE
    assert unknown.route_kind == RouteKind.TERMINAL
    assert unknown.dispatchable is False


def test_requested_transition_is_advisory_only() -> None:
    cases = [
        (_payload(requested_transition="MERGE"), NextTransition.INTEGRATION_VERIFY),
        (
            _payload(requested_transition="OWNER_REQUIRED"),
            NextTransition.INTEGRATION_VERIFY,
        ),
        (
            _payload(requested_transition="AUTONOMOUS_RECONCILE"),
            NextTransition.INTEGRATION_VERIFY,
        ),
        (
            _payload(outcome="FAIL", requested_transition="AUTONOMOUS_RECONCILE"),
            NextTransition.BLOCKED,
        ),
        (
            _payload(include_receipt=False, requested_transition="INTEGRATION_VERIFY"),
            NextTransition.REJECTED,
        ),
    ]
    for payload, expected in cases:
        decision = validate_and_classify(payload)
        routed = route_payload(payload)
        assert decision.next_transition == expected
        assert routed.transition == expected
        assert routed.execution_authorized is False
        if expected == NextTransition.INTEGRATION_VERIFY:
            assert routed.task_type == TaskType.CANDIDATE_VERIFICATION


def test_owner_required_with_malicious_extras_stays_non_privileged() -> None:
    routed = route_payload(
        _payload(
            role="integration",
            state="MERGE_ELIGIBLE",
            requested_transition="MERGE",
            extras={"merge": True, "authority_grant": True, "execute": "now"},
        )
    )
    assert routed.route_kind == RouteKind.OWNER_GATE
    assert routed.permissions.merge is False
    assert routed.permissions.authority_grant is False
    assert routed.execution_authorized is False
    assert routed.task is None


def test_decision_envelope_task_mismatch_fail_closed() -> None:
    envelope = parse_envelope(_payload(task_id="D-137"))
    other = parse_envelope(_payload(task_id="D-999"))
    decision = classify_envelope(envelope)
    with pytest.raises(RouteConsistencyError, match="task mismatch"):
        route(decision, other)


def test_decision_envelope_role_mismatch_fail_closed() -> None:
    local = parse_envelope(_payload(role="local"))
    integration = parse_envelope(_payload(role="integration", task_id="D-137"))
    decision = classify_envelope(local)
    with pytest.raises(RouteConsistencyError, match="producer role mismatch"):
        route(decision, integration)


def test_decision_envelope_outcome_mismatch_fail_closed() -> None:
    passed = parse_envelope(_payload(outcome="PASS"))
    failed = parse_envelope(_payload(outcome="FAIL"))
    decision = classify_envelope(passed)
    with pytest.raises(RouteConsistencyError, match="outcome mismatch"):
        route(decision, failed)


def test_forged_decision_transition_fail_closed() -> None:
    envelope = parse_envelope(_payload())
    decision = classify_envelope(envelope)
    forged = decision.model_copy(
        update={
            "next_transition": NextTransition.OWNER_REQUIRED,
            "owner_required": True,
        }
    )
    with pytest.raises(RouteConsistencyError, match="transition mismatch"):
        route(forged, envelope)


def test_source_binding_stable_and_input_sensitive() -> None:
    first = parse_envelope(_payload())
    second = parse_envelope(_payload())
    assert source_result_digest(first) == source_result_digest(second)
    moved = parse_envelope(_payload(target_moved=True))
    other_task = parse_envelope(_payload(task_id="D-999"))
    assert source_result_digest(first) != source_result_digest(moved)
    assert source_result_digest(first) != source_result_digest(other_task)
    routed_a = route(classify_envelope(first), first)
    routed_b = route(classify_envelope(second), second)
    assert routed_a == routed_b
    assert routed_a.source_result_digest == source_result_digest(first)


def test_deterministic_replay() -> None:
    payload = _payload(requested_transition="MERGE")
    first = route_payload(payload)
    second = route_payload(payload)
    assert first == second
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_route_schema_parity_for_all_policy_transitions() -> None:
    builders: dict[NextTransition, dict[str, Any]] = {
        NextTransition.INTEGRATION_VERIFY: _payload(),
        NextTransition.RECERTIFY_REQUIRED: _payload(target_moved=True),
        NextTransition.AUTONOMOUS_RECONCILE: _payload(role="integration"),
        NextTransition.REMEDIATION_REQUIRED: _payload(role="integration", outcome="FAIL"),
        NextTransition.OWNER_REQUIRED: _payload(role="integration", state="MERGE_ELIGIBLE"),
        NextTransition.BLOCKED: _payload(outcome="FAIL"),
        NextTransition.REJECTED: _payload(include_receipt=False),
        NextTransition.BLOCKED_UNKNOWN_STATE: _payload(role="autonomous"),
    }
    assert set(builders) == set(POLICY)
    for transition, payload in builders.items():
        routed = route_payload(payload)
        assert routed.transition == transition
        assert routed.policy_id == POLICY_ID
        assert routed.package_id == ROUTING_PACKAGE_ID
        _assert_non_privileged(routed)
        validate_record(routed, "orchestration-route")
        OrchestrationRoute.model_validate(routed.model_dump(mode="json"))


def test_privilege_invariants_global() -> None:
    payloads = [
        _payload(),
        _payload(target_moved=True),
        _payload(role="integration"),
        _payload(role="integration", outcome="FAIL"),
        _payload(role="integration", state="MERGE_ELIGIBLE"),
        _payload(outcome="FAIL"),
        _payload(include_receipt=False),
        _payload(role="autonomous"),
        _payload(requested_transition="MERGE"),
    ]
    for payload in payloads:
        routed = route_payload(payload)
        assert routed.execution_authorized is False
        assert routed.permissions.merge is False
        assert routed.permissions.production_mutation is False
        assert routed.permissions.authority_grant is False


def test_router_source_never_assigns_privileged_true() -> None:
    needles = (
        "merge=True",
        "production_mutation=True",
        "authority_grant=True",
        "execution_authorized=True",
        "repository_write=True",
    )
    for path in (ORCH_DIR / "router.py", ORCH_DIR / "policy.py"):
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            assert needle not in text, f"{path.name} must not assign {needle}"


def test_no_execute_or_dispatch_helpers() -> None:
    forbidden = (
        "def execute(",
        "def dispatch(",
        "def spawn_agent(",
        "shell_command",
        "cursor_prompt",
    )
    for path in sorted(ORCH_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, f"{path.name} must not contain {needle!r}"
        if path.name != "cursor_bridge.py":
            assert "followup_message" not in text


def test_cli_route_result_scenario_a(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "result.json"
    path.write_text(json.dumps(_payload(), sort_keys=True), encoding="utf-8")
    code = main(["orchestrator", "route-result", str(path)])
    assert code == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["route_kind"] == "task"
    assert report["transition"] == "INTEGRATION_VERIFY"
    assert report["target_role"] == "integration"
    assert report["task_type"] == "candidate_verification"
    assert report["owner_gate"] is False
    assert report["execution_authorized"] is False
    assert report["package_id"] == "AS-ORCH-001B"


def test_cli_route_result_owner_gate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "result.json"
    path.write_text(
        json.dumps(_payload(role="integration", state="MERGE_ELIGIBLE"), sort_keys=True),
        encoding="utf-8",
    )
    code = main(["orchestrator", "route-result", str(path)])
    assert code == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["route_kind"] == "owner_gate"
    assert report["transition"] == "OWNER_REQUIRED"
    assert report["owner_gate"] is True
    assert report["dispatchable"] is False
    assert report["execution_authorized"] is False
    assert report["permissions"]["merge"] is False


def test_cli_route_result_rejected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{", encoding="utf-8")
    code = main(["orchestrator", "route-result", str(path)])
    assert code == EXIT_ERROR
    report = json.loads(capsys.readouterr().out)
    assert report["route_kind"] == "terminal"
    assert report["transition"] == "REJECTED"
    assert report["dispatchable"] is False
    assert report["execution_authorized"] is False


def test_cli_route_result_no_side_effects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "result.json"
    path.write_text(json.dumps(_payload(), sort_keys=True), encoding="utf-8")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert main(["orchestrator", "route-result", str(path)]) == EXIT_OK
    capsys.readouterr()
    after = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert after == before


def test_cli_route_help_includes_examples(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["orchestrator", "route-result", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "atlas orchestrator route-result result.json" in out
    assert "Does not dispatch" in out or "does not dispatch" in out.lower()


def test_composition_json_to_typed_output() -> None:
    raw = json.dumps(_payload())
    payload = json.loads(raw)
    routed = route_payload(payload)
    assert isinstance(routed, OrchestrationRoute)
    assert routed.route_kind == RouteKind.TASK
    assert routed.execution_authorized is False


def test_decision_valid_flag_not_required_for_rejected_receipt() -> None:
    decision = validate_and_classify(_payload(include_receipt=False))
    assert decision.valid is True
    assert decision.next_transition == NextTransition.REJECTED
    assert decision.workflow_state == WorkflowState.REJECTED
    assert decision.outcome == ResultOutcome.PASS
