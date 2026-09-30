"""AS-ORCH-001C-R1 explicit completion transport, HandoffPacket, scenarios A-G."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from project_atlas.cli import EXIT_ERROR, EXIT_OK, main
from project_atlas.orchestration.cursor_bridge import (
    STATE_RELATIVE,
    CompletionTransport,
    CursorBridgeError,
    HandoffPacket,
    StagedStateTampered,
    acknowledge,
    build_handoff_packet,
    complete_staged_handoff,
    handle_stop_event,
    persist_state,
    stage_result,
    status_report,
    surface_pending_handoff,
)
from project_atlas.orchestration.router import route_payload
from project_atlas.schema import SchemaValidationError, validate_record


def _payload(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema_version": 1,
        "producer": {"role": "local", "agent_id": "local-agent"},
        "task": {"id": "D-137", "attempt": 1},
        "outcome": "PASS",
        "state": "CERTIFIED",
        "observations": {"target_moved": False, "unauthorized_mutations": 0},
        "receipt": {"receipt_id": "ASR-1234567890abcdef", "status": "valid"},
        "blockers": [],
        "requested_transition": None,
    }
    for key, value in overrides.items():
        if key == "observations" and isinstance(value, dict):
            merged = dict(data["observations"])
            merged.update(value)
            data["observations"] = merged
        else:
            data[key] = value
    return data


def _stop(*, status: str = "completed", loop_count: int = 0, **extra: Any) -> dict[str, Any]:
    event = {"conversation_id": "conv-1", "status": status, "loop_count": loop_count}
    event.update(extra)
    return event


def test_handoff_packet_model_and_schema() -> None:
    packet = HandoffPacket(
        state="HANDOFF_READY",
        transport="explicit",
        route_digest="a" * 64,
        source_task="D-137",
        target_role="integration",
        task_type="candidate_verification",
    )
    dumped = packet.model_dump(mode="json")
    validate_record(dumped, "handoff-packet")
    public = packet.to_public_dict()
    assert public["dispatch_performed"] is False
    assert public["execution_authorized"] is False
    assert public["transport"] == "explicit"
    with pytest.raises(ValidationError):
        HandoffPacket.model_validate({**dumped, "execution_authorized": True})
    with pytest.raises(ValidationError):
        HandoffPacket.model_validate({**dumped, "dispatch_performed": True})
    with pytest.raises(SchemaValidationError):
        validate_record({**dumped, "bash": "echo hi"}, "handoff-packet")


def test_scenario_a_task_explicit_completion(tmp_path: Path) -> None:
    state = stage_result(_payload(), root=tmp_path)
    packet = complete_staged_handoff(root=tmp_path)
    assert packet.state == "HANDOFF_READY"
    assert packet.transport == CompletionTransport.EXPLICIT
    assert packet.target_role == "integration"
    assert packet.task_type == "candidate_verification"
    assert packet.source_task == "D-137"
    assert packet.dispatch_performed is False
    assert packet.execution_authorized is False
    assert packet.route_digest == state.route_digest
    public = packet.to_public_dict()
    assert public["state"] == "HANDOFF_READY"
    assert public["transport"] == "explicit"
    assert public["target_role"] == "integration"
    assert public["task_type"] == "candidate_verification"


def test_scenario_b_recertify_explicit_completion(tmp_path: Path) -> None:
    stage_result(_payload(observations={"target_moved": True}), root=tmp_path)
    packet = complete_staged_handoff(root=tmp_path)
    assert packet.state == "HANDOFF_READY"
    assert packet.target_role == "integration"
    assert packet.task_type == "recertification"
    assert packet.dispatch_performed is False
    assert packet.execution_authorized is False


def test_scenario_c_owner_gate_explicit_completion(tmp_path: Path) -> None:
    state = stage_result(
        _payload(producer={"role": "integration", "agent_id": "iv"}, state="MERGE_ELIGIBLE"),
        root=tmp_path,
    )
    packet = complete_staged_handoff(root=tmp_path)
    assert packet.state == "OWNER_REQUIRED"
    assert packet.dispatch_performed is False
    assert packet.execution_authorized is False
    assert packet.target_role is None
    assert packet.task_type is None
    assert state.route.permissions.merge is False
    assert state.route.execution_authorized is False


def test_scenario_d_terminal_explicit_completion(tmp_path: Path) -> None:
    stage_result(_payload(receipt=None), root=tmp_path)
    packet = complete_staged_handoff(root=tmp_path)
    assert packet.state == "TERMINAL"
    assert packet.dispatch_performed is False
    assert packet.execution_authorized is False
    assert packet.target_role is None
    assert packet.task_type is None


def test_scenario_e_tampered_state_rejected(tmp_path: Path) -> None:
    state = stage_result(_payload(), root=tmp_path)
    path = tmp_path / STATE_RELATIVE
    broken = json.loads(path.read_text(encoding="utf-8"))
    broken["route"]["target"]["role"] = "autonomous"
    broken["route"]["task_type"] = "program_reconciliation"
    path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(StagedStateTampered):
        complete_staged_handoff(root=tmp_path)
    persist_state(tmp_path, state)
    persist_state(tmp_path, state.model_copy(update={"source_result_digest": "c" * 64}))
    with pytest.raises(StagedStateTampered):
        complete_staged_handoff(root=tmp_path)
    persist_state(tmp_path, state)
    persist_state(tmp_path, state.model_copy(update={"route_digest": "d" * 64}))
    with pytest.raises(StagedStateTampered):
        complete_staged_handoff(root=tmp_path)
    persist_state(tmp_path, state)
    dumped = json.loads(path.read_text(encoding="utf-8"))
    dumped["route"]["permissions"]["merge"] = True
    path.write_text(json.dumps(dumped), encoding="utf-8")
    with pytest.raises((StagedStateTampered, CursorBridgeError)):
        complete_staged_handoff(root=tmp_path)


def test_scenario_f_repeated_completion_idempotent(tmp_path: Path) -> None:
    first_state = stage_result(_payload(), root=tmp_path)
    first = complete_staged_handoff(root=tmp_path)
    second = complete_staged_handoff(root=tmp_path)
    alias = surface_pending_handoff(root=tmp_path)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.semantic_identity() == alias.semantic_identity()
    reloaded = json.loads((tmp_path / STATE_RELATIVE).read_text(encoding="utf-8"))
    assert reloaded["source_result_digest"] == first_state.source_result_digest
    assert reloaded["route_digest"] == first_state.route_digest
    assert reloaded["status"] == "pending"
    assert first.execution_authorized is False
    assert second.execution_authorized is False


def test_scenario_g_transport_equivalence(tmp_path: Path) -> None:
    payload = _payload()
    state = stage_result(payload, root=tmp_path)
    hook_packet = build_handoff_packet(state, transport=CompletionTransport.HOOK)
    explicit = complete_staged_handoff(
        {"status": "completed", "conversation_id": "aaa", "transport": "explicit"},
        root=tmp_path,
    )
    assert hook_packet.semantic_identity() == explicit.semantic_identity()
    routed = route_payload(payload)
    assert explicit.route_digest == state.route_digest
    assert explicit.target_role == routed.target.role
    assert explicit.task_type == routed.task_type
    assert explicit.execution_authorized is False
    assert routed.execution_authorized is False
    other = complete_staged_handoff(
        {
            "status": "error",
            "conversation_id": "bbb",
            "target_role": "autonomous",
            "execution_authorized": True,
            "task_type": "program_reconciliation",
        },
        root=tmp_path,
    )
    assert other.semantic_identity() == explicit.semantic_identity()
    assert other.target_role == "integration"
    assert other.task_type == "candidate_verification"
    hook_response = handle_stop_event(_stop(conversation_id="zzz"), root=tmp_path)
    assert "followup_message" in hook_response
    assert state.route_digest in hook_response["followup_message"]
    assert "integration" in hook_response["followup_message"]
    assert "candidate_verification" in hook_response["followup_message"]


def test_ack_independent_of_transport(tmp_path: Path) -> None:
    state = stage_result(_payload(), root=tmp_path)
    packet = complete_staged_handoff(root=tmp_path)
    assert packet.route_digest == state.route_digest
    acked = acknowledge(state.route_digest, root=tmp_path)
    assert acked.status == "acknowledged"
    again = acknowledge(state.route_digest, root=tmp_path)
    assert again.status == "acknowledged"
    with pytest.raises(CursorBridgeError) as exc:
        complete_staged_handoff(root=tmp_path)
    assert exc.value.code == "HANDOFF_ALREADY_ACKNOWLEDGED"
    with pytest.raises(Exception, match="acknowledgement"):
        acknowledge("e" * 64, root=tmp_path)


def test_cursor_status_reports_explicit_transport(tmp_path: Path) -> None:
    stage_result(_payload(), root=tmp_path)
    report = status_report(tmp_path)
    assert report["cursor_hook_adapter"] == "absent"
    assert report["cursor_hook_runtime_certified"] is False
    assert report["explicit_completion_transport"] == "available"
    assert report["active_state"] == "pending"
    assert report["state"] == "pending"
    assert isinstance(report["route_digest"], str)
    assert report["route_kind"] == "task"
    assert "CURSOR_RUNTIME_HOOK" not in report
    assert report.get("CURSOR_RUNTIME_HOOK") != "PASS"
    hook_dir = tmp_path / ".cursor" / "hooks"
    hook_dir.mkdir(parents=True)
    (tmp_path / ".cursor" / "hooks.json").write_text("{}", encoding="utf-8")
    (hook_dir / "atlas_stop.py").write_text("# adapter\n", encoding="utf-8")
    configured = status_report(tmp_path)
    assert configured["cursor_hook_adapter"] == "configured"
    assert configured["cursor_hook_runtime_certified"] is False


def test_cli_cursor_complete_and_ack(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    result = tmp_path / "result.json"
    result.write_text(json.dumps(_payload()), encoding="utf-8")
    assert (
        main(["orchestrator", "cursor-stage-result", str(result), "--root", str(tmp_path)])
        == EXIT_OK
    )
    capsys.readouterr()
    assert main(["orchestrator", "cursor-complete", "--root", str(tmp_path)]) == EXIT_OK
    packet = json.loads(capsys.readouterr().out)
    assert packet["state"] == "HANDOFF_READY"
    assert packet["transport"] == "explicit"
    assert packet["target_role"] == "integration"
    assert packet["task_type"] == "candidate_verification"
    assert packet["dispatch_performed"] is False
    assert packet["execution_authorized"] is False
    assert "followup_message" not in packet
    digest = packet["route_digest"]
    assert main(["orchestrator", "cursor-status", "--root", str(tmp_path)]) == EXIT_OK
    status = json.loads(capsys.readouterr().out)
    assert status["explicit_completion_transport"] == "available"
    assert status["cursor_hook_runtime_certified"] is False
    assert status["active_state"] == "pending"
    assert main(["orchestrator", "cursor-ack", digest, "--root", str(tmp_path)]) == EXIT_OK
    acked = json.loads(capsys.readouterr().out)
    assert acked["status"] == "acknowledged"
    assert acked["execution_authorized"] is False
    assert main(["orchestrator", "cursor-complete", "--root", str(tmp_path)]) == EXIT_ERROR
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["ok"] is False
    assert rejected["error"] == "HANDOFF_ALREADY_ACKNOWLEDGED"
    assert rejected["execution_authorized"] is False


def test_cli_complete_tamper_rejected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    result = tmp_path / "result.json"
    result.write_text(json.dumps(_payload()), encoding="utf-8")
    assert (
        main(["orchestrator", "cursor-stage-result", str(result), "--root", str(tmp_path)])
        == EXIT_OK
    )
    capsys.readouterr()
    path = tmp_path / STATE_RELATIVE
    broken = json.loads(path.read_text(encoding="utf-8"))
    broken["route"]["target"]["role"] = "autonomous"
    path.write_text(json.dumps(broken), encoding="utf-8")
    assert main(["orchestrator", "cursor-complete", "--root", str(tmp_path)]) == EXIT_ERROR
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert report["error"] == "STAGED_STATE_TAMPERED"
    assert report["execution_authorized"] is False


def test_missing_state_rejected(tmp_path: Path) -> None:
    with pytest.raises(CursorBridgeError) as exc:
        complete_staged_handoff(root=tmp_path)
    assert exc.value.code == "NO_STAGED_HANDOFF"


def test_privilege_invariants_explicit_transport(tmp_path: Path) -> None:
    stage_result(
        _payload(
            producer={"role": "integration", "agent_id": "iv"},
            state="MERGE_ELIGIBLE",
            requested_transition="MERGE",
        ),
        root=tmp_path,
    )
    packet = complete_staged_handoff(
        {"execution_authorized": True, "merge": True, "authority_grant": True},
        root=tmp_path,
    )
    assert packet.state == "OWNER_REQUIRED"
    assert packet.execution_authorized is False
    assert packet.dispatch_performed is False
    dumped = packet.model_dump(mode="json")
    assert dumped["execution_authorized"] is False
    assert "merge" not in dumped
    assert "permissions" not in dumped


def test_explicit_completion_source_has_no_dispatch() -> None:
    text = (
        Path(__file__)
        .resolve()
        .parents[2]
        .joinpath("src/project_atlas/orchestration/cursor_bridge.py")
        .read_text(encoding="utf-8")
    )
    for needle in (
        "cursor-agent",
        "spawn_agent",
        "subprocess",
        "shell=True",
        "def execute(",
        "def dispatch(",
        "def launch(",
    ):
        assert needle not in text
