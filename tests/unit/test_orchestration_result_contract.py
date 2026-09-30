"""AS-ORCH-001A agent result contract, schema parity, and CLI surface."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from atlas_contracts import AgentEvent, EventType, PipelineState
from atlas_contracts.event_package import AgentEventEnvelope
from atlas_contracts.provenance import ProvenanceRecord
from atlas_contracts.receipts import ReceiptReference
from project_atlas.cli import EXIT_ERROR, EXIT_OK, main
from project_atlas.orchestration import (
    PACKAGE_ID,
    AgentResultEnvelope,
    OrchestrationDecision,
    parse_envelope,
    validate_and_classify,
)
from project_atlas.orchestration.models import ResultReceiptBinding
from project_atlas.orchestration.validator import (
    MAX_RESULT_BYTES,
    read_result_source,
    run_validate_result,
)
from project_atlas.schema import SchemaValidationError, available_schemas, validate_record

ORCH_DIR = Path(__file__).resolve().parents[2] / "src" / "project_atlas" / "orchestration"


def _valid_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "producer": {"role": "local", "agent_id": "local-windows"},
        "task": {"id": "D-137", "attempt": 1},
        "outcome": "PASS",
        "state": "CERTIFIED",
        "observations": {"target_moved": False, "unauthorized_mutations": 0},
        "receipt": {
            "receipt_id": "ASR-1234567890abcdef",
            "status": "valid",
            "event_id": "AE-orch-001a",
        },
        "blockers": [],
        "requested_transition": None,
    }
    payload.update(overrides)
    return payload


def test_schema_kind_is_registered() -> None:
    assert "agent-result-envelope" in available_schemas()


def test_valid_payload_accepted_by_model_and_schema() -> None:
    payload = _valid_payload()
    envelope = AgentResultEnvelope.model_validate(payload)
    validate_record(envelope, "agent-result-envelope")
    parsed = parse_envelope(payload)
    assert parsed.producer.role == "local"
    assert parsed.task.id == "D-137"
    assert parsed.task.attempt == 1
    assert parsed.receipt_is_valid_evidence()


def test_schema_valid_payloads_are_accepted_by_model() -> None:
    samples = [
        _valid_payload(),
        _valid_payload(producer={"role": "integration", "agent_id": "iv-linux"}),
        _valid_payload(receipt=None),
        _valid_payload(
            observations={
                "target_moved": False,
                "unauthorized_mutations": 0,
                "extras": {"fixture_name": "harbor-api", "retry_count": 2},
            }
        ),
        _valid_payload(requested_transition="MERGE", state="MERGE_ELIGIBLE"),
    ]
    for payload in samples:
        validate_record(payload, "agent-result-envelope")
        AgentResultEnvelope.model_validate(payload)


def test_schema_invalid_payloads_are_rejected_by_model() -> None:
    invalids = [
        _valid_payload(producer={"role": "chatgpt", "agent_id": "x"}),
        _valid_payload(outcome="SUCCESS"),
        _valid_payload(task={"id": "", "attempt": 1}),
        _valid_payload(task={"id": "D-137", "attempt": 0}),
        _valid_payload(observations={"target_moved": False, "unauthorized_mutations": -1}),
        _valid_payload(execution_authorized=True),
        _valid_payload(merge_authorized=True),
        _valid_payload(task={"id": "../escape", "attempt": 1}),
        _valid_payload(task={"id": "path/like", "attempt": 1}),
        _valid_payload(state="certified"),
        _valid_payload(receipt={"receipt_id": "ASR-1", "status": "ok"}),
    ]
    for payload in invalids:
        with pytest.raises((SchemaValidationError, ValidationError, ValueError)):
            validate_record(payload, "agent-result-envelope")
        with pytest.raises((ValidationError, ValueError)):
            AgentResultEnvelope.model_validate(payload)


def test_unknown_producer_rejected() -> None:
    payload = _valid_payload(producer={"role": "unknown-plane", "agent_id": "x"})
    decision = validate_and_classify(payload)
    assert decision.valid is False
    assert decision.next_transition == "REJECTED"
    assert decision.execution_authorized is False


def test_unknown_outcome_rejected() -> None:
    payload = _valid_payload(outcome="MAYBE")
    decision = validate_and_classify(payload)
    assert decision.valid is False
    assert decision.next_transition == "REJECTED"


def test_malformed_task_and_attempt_rejected() -> None:
    blank = validate_and_classify(_valid_payload(task={"id": "", "attempt": 1}))
    assert blank.valid is False
    assert blank.next_transition == "REJECTED"
    low = validate_and_classify(_valid_payload(task={"id": "D-137", "attempt": 0}))
    assert low.valid is False
    path_like = validate_and_classify(_valid_payload(task={"id": "foo/bar", "attempt": 1}))
    assert path_like.valid is False


def test_negative_unauthorized_mutations_rejected() -> None:
    decision = validate_and_classify(
        _valid_payload(observations={"target_moved": False, "unauthorized_mutations": -1})
    )
    assert decision.valid is False
    assert decision.next_transition == "REJECTED"


def test_unexpected_authority_fields_rejected() -> None:
    payload = _valid_payload()
    payload["execution_authorized"] = True
    payload["owner_approval"] = True
    decision = validate_and_classify(payload)
    assert decision.valid is False
    assert decision.execution_authorized is False
    assert decision.merge_authorized is False


def test_coerced_observation_types_rejected() -> None:
    """JSON Schema must see the raw payload; bool/int swaps must not pass."""
    confused = _valid_payload(observations={"target_moved": 0, "unauthorized_mutations": False})
    with pytest.raises(SchemaValidationError):
        validate_record(confused, "agent-result-envelope")
    decision = validate_and_classify(confused)
    assert decision.valid is False
    assert decision.next_transition == "REJECTED"
    assert decision.execution_authorized is False
    assert decision.merge_authorized is False
    assert decision.reasons[0] == "malformed_or_schema_invalid"
    assert decision.reasons[1].startswith("schema_invalid:")


def test_rejected_values_are_not_echoed_in_decision() -> None:
    """Untrusted extra fields must not leak into classifier/CLI reasons."""
    secret = "sk-secret-SHOULD-NOT-LEAK"
    payload = _valid_payload()
    payload["api_key"] = secret
    decision = validate_and_classify(payload)
    assert decision.valid is False
    assert decision.execution_authorized is False
    public = json.dumps(decision.to_public_dict())
    assert secret not in public
    assert "input_value" not in public
    joined = " ".join(decision.reasons)
    assert secret not in joined
    assert joined.startswith("malformed_or_schema_invalid")


def test_file_read_is_capped_before_full_materialization(tmp_path: Path) -> None:
    path = tmp_path / "huge.json"
    path.write_bytes(b"x" * (MAX_RESULT_BYTES + 50))
    data = read_result_source(path=path, from_stdin=False, stdin=io.StringIO())
    assert len(data) == MAX_RESULT_BYTES + 1
    decision, code = run_validate_result(path=path, from_stdin=False, stdin=io.StringIO())
    assert code == 1
    assert decision.valid is False
    assert decision.next_transition == "REJECTED"
    assert decision.execution_authorized is False
    assert "size limit" in decision.reasons[1]


def test_receipt_binding_composes_receipt_reference() -> None:
    binding = ResultReceiptBinding(
        receipt_id="ASR-1234567890abcdef",
        status="valid",
        event_id="AE-orch-001a",
    )
    ref = binding.to_receipt_reference()
    assert ref == ReceiptReference(
        receipt_id="ASR-1234567890abcdef",
        status="valid",
        event_id="AE-orch-001a",
    )


def test_receipt_without_event_id_does_not_mint_reference() -> None:
    binding = ResultReceiptBinding(receipt_id="ASR-1234567890abcdef", status="valid")
    assert binding.to_receipt_reference() is None
    assert binding.is_valid_evidence() is True


def test_decision_cannot_enable_execution_or_merge() -> None:
    with pytest.raises(ValidationError):
        OrchestrationDecision(
            valid=True,
            producer="local",
            task="D-137",
            outcome="PASS",
            workflow_state="LOCAL_ACCEPTED",
            next_transition="INTEGRATION_VERIFY",
            execution_authorized=True,  # type: ignore[arg-type]
            owner_required=False,
        )
    with pytest.raises(ValidationError):
        OrchestrationDecision(
            valid=True,
            producer="local",
            task="D-137",
            outcome="PASS",
            workflow_state="OWNER_REQUIRED",
            next_transition="OWNER_REQUIRED",
            owner_required=True,
            merge_authorized=True,  # type: ignore[arg-type]
        )


def test_canonical_contracts_remain_intact() -> None:
    event = AgentEvent(
        event_id="AE-contract-test",
        event_type=EventType.IMPLEMENTATION,
        project_id="project-atlas",
        session_id="AS-contract-session",
        agent_id="agent-one",
        adapter_id="generic-cli-v1",
        timestamp="2026-08-01T18:00:00Z",
        summary="contract test",
    )
    pipeline = PipelineState(captured=True, normalized=True, verified=True, routed=True)
    receipt = ReceiptReference(receipt_id="ASR-intact", status="valid", event_id="AE-contract-test")
    assert event.project_id == "project-atlas"
    assert pipeline.is_verified()
    assert receipt.status == "valid"
    envelope = AgentEventEnvelope(
        schema_version=1,
        event=event,
        skill={"id": "atlas-governed-work", "version": "1.0.0", "sha256": "a" * 64},
        vault={"schema_version": 1, "vault_id": "vault-1", "vault_uuid": "u-1"},
        provenance=ProvenanceRecord(
            content_sha256="b" * 64,
            normalized_sha256="c" * 64,
            source_receipt_id="ASR-intact",
        ),
        pipeline=pipeline,
        receipt=receipt,
    )
    assert envelope.receipt.status == "valid"
    assert envelope.pipeline.is_verified()


def test_no_prose_parsing_in_orchestration_package() -> None:
    forbidden = (
        'if "PASS" in',
        "if 'PASS' in",
        'if "TARGET_MOVED" in',
        "if 'TARGET_MOVED' in",
        'if "blocked because"',
        "if 'blocked because'",
    )
    for path in sorted(ORCH_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, f"{path.name} must not parse prose via {needle!r}"


def test_no_dispatch_or_merge_implemented() -> None:
    """001A/001B/001C modules still must not spawn or merge.

    AS-ORCH-001D owns subprocess transport and dispatch-once. Multi-hop
    auto-dispatch and merge remain forbidden everywhere.

    ``local_process_transport.py`` (AS-ORCH-LOCAL-PROC-001, PR #661) is a
    second, deliberate subprocess-transport owner alongside
    ``agent_transport.py``/``dispatcher.py`` -- a disabled-by-default,
    provider-neutral LOCAL PROCESS execution backend, explicitly scoped
    by directive as its own independently-reviewable primitive (never
    Cursor/OpenAI/Anthropic/network/billing). It still never dispatches
    multi-hop or merges -- ``merge_authorized``/``execution_authorized``
    are hard-coded ``Literal[False]`` on every result it can produce, and
    it is checked by the same ``forbidden_names``/``def merge_pull_
    request`` scan below as every other module, dispatch-allowlisted or
    not.
    """
    dispatch_modules = {"agent_transport.py", "dispatcher.py", "local_process_transport.py"}
    forbidden_imports = (
        "import subprocess",
        "from subprocess",
        "import socket",
        "urllib.request",
        "http.client",
    )
    forbidden_names = (
        "def dispatch",
        "def spawn_agent",
        "def merge_pull_request",
    )
    hook_only_names = ("followup_message", "stop_hook")
    for path in sorted(ORCH_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "def merge_pull_request" not in text
        assert "next_handoff_autodispatched: Literal[True]" not in text
        if path.name in dispatch_modules:
            continue
        for needle in forbidden_imports + forbidden_names:
            assert needle not in text, f"{path.name} must not implement {needle!r}"
        if path.name != "cursor_bridge.py":
            for needle in hook_only_names:
                assert needle not in text, f"{path.name} must not implement {needle!r}"


def test_cli_valid_input(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "result.json"
    path.write_text(json.dumps(_valid_payload(), sort_keys=True), encoding="utf-8")
    code = main(["orchestrator", "validate-result", str(path)])
    assert code == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["valid"] is True
    assert report["producer_role"] == "local"
    assert report["task_id"] == "D-137"
    assert report["outcome"] == "PASS"
    assert report["workflow_state"] == "LOCAL_ACCEPTED"
    assert report["next_transition"] == "INTEGRATION_VERIFY"
    assert report["execution_authorized"] is False
    assert report["merge_authorized"] is False
    assert report["package_id"] == PACKAGE_ID


def test_cli_invalid_input(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{", encoding="utf-8")
    code = main(["orchestrator", "validate-result", str(path)])
    assert code == EXIT_ERROR
    report = json.loads(capsys.readouterr().out)
    assert report["valid"] is False
    assert report["next_transition"] == "REJECTED"
    assert report["execution_authorized"] is False
    assert report["reasons"][0] == "malformed_or_schema_invalid"


def test_cli_no_side_effects(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "result.json"
    path.write_text(json.dumps(_valid_payload(), sort_keys=True), encoding="utf-8")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert main(["orchestrator", "validate-result", str(path)]) == EXIT_OK
    capsys.readouterr()
    after = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert after == before
    assert {p.name for p in tmp_path.iterdir()} == {path.name}


def test_cli_help_includes_examples(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["orchestrator", "validate-result", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "atlas orchestrator validate-result result.json" in out
    assert "Examples:" in out
