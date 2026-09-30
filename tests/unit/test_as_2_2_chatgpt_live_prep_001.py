"""AS-2.2-CHATGPT-LIVE-PREP-001 — docs/contracts/fixtures only (no src mutation)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from _atlas_2_2_maturity import assert_prep_branch_scope

ROOT = Path(__file__).resolve().parents[2]
PREP = ROOT / "docs" / "atlas-2.2" / "chatgpt-live"
CONTRACTS = PREP / "contracts"
FIXTURES = PREP / "fixtures"

DOCS = {
    "package": PREP / "AS-2.2-CHATGPT-LIVE-PREP-001.md",
    "architecture": PREP / "ARCHITECTURE.md",
    "contract": PREP / "CONTRACT.md",
    "invariants": PREP / "INVARIANTS.md",
    "fixture_plan": PREP / "FIXTURE-PLAN.md",
    "adr": PREP / "adr" / "ADR-2.2-CHATGPT-LIVE-001-quarantine-first-live-bridge-prep.md",
}

SCHEMA_FILES = {
    "request": "live-bridge-request.schema.json",
    "quarantine": "quarantine-envelope.schema.json",
    "receipt": "live-session-receipt.schema.json",
    "action": "forbidden-action.schema.json",
}

FIXTURE_SCHEMA = {
    "live-bridge-request.sample.json": "request",
    "quarantine-envelope.sample.json": "quarantine",
    "live-session-quarantined.sample.json": "receipt",
    "negative-bypass-quarantine.expect.json": "action",
    "negative-layer-b-write.expect.json": "action",
    "negative-llm-authority.expect.json": "action",
    "negative-default-on-live.expect.json": "action",
    "negative-pilot-invent.expect.json": "action",
}


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema(name: str) -> dict:
    return _load_json(CONTRACTS / SCHEMA_FILES[name])  # type: ignore[return-value]


def test_prep_docs_exist_without_readme() -> None:
    for path in DOCS.values():
        assert path.is_file(), path
        text = path.read_text(encoding="utf-8")
        assert "AS-2.2-CHATGPT-LIVE" in text or "ChatGPT Live" in text or "chatgpt" in text.lower()
        assert "PREP" in text.upper() or "prep" in text
    assert not (PREP / "README.md").exists()
    assert not any(PREP.rglob("README.md"))


def test_contract_stubs_exist_and_are_not_package_data() -> None:
    package_schemas = ROOT / "src" / "project_atlas" / "schemas"
    for filename in SCHEMA_FILES.values():
        stub = CONTRACTS / filename
        assert stub.is_file(), stub
        body = stub.read_text(encoding="utf-8")
        assert "PREP STUB" in body
        assert "chatgpt" in body.lower() or "quarantine" in body.lower()
        assert not (package_schemas / filename).exists()


def test_fixtures_validate_against_prep_stubs() -> None:
    for fixture_name, schema_key in FIXTURE_SCHEMA.items():
        fixture_path = FIXTURES / fixture_name
        assert fixture_path.is_file(), fixture_path
        instance = _load_json(fixture_path)
        schema = _schema(schema_key)
        jsonschema.validate(instance=instance, schema=schema)


def test_package_card_non_claims() -> None:
    text = DOCS["package"].read_text(encoding="utf-8")
    assert "ATLAS_2_1_RELEASE_CERTIFIED" in text
    assert "**NO**" in text
    assert "ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED" in text
    assert "chatgpt_bridge" in text
    assert "do not mutate" in text.lower() or "NONE" in text
    assert "quarantine-first" in text.lower() or "QUARANTINE-FIRST" in text
    assert "PILOT" in text


def test_invariants_document_fail_closed_walls() -> None:
    text = DOCS["invariants"].read_text(encoding="utf-8")
    assert "quarantine" in text.lower()
    assert "chatgpt_bridge" in text
    assert "UI ≠ canonical" in text or "UI≠canonical" in text
    assert "LLM ≠ authority" in text or "LLM≠authority" in text
    assert "ATLAS_2_1_RELEASE_CERTIFIED" in text
    assert "NO" in text
    assert "Never PILOT" in text or "never PILOT" in text.lower()


def test_live_session_is_quarantine_first_derived() -> None:
    receipt = _load_json(FIXTURES / "live-session-quarantined.sample.json")
    assert isinstance(receipt, dict)
    assert receipt["package_id"] == "AS-2.2-CHATGPT-LIVE-PREP-001"
    assert receipt["live_chatgpt_api"] is True
    assert receipt["llm_authority"] is False
    assert receipt["canonical_write"] is False
    assert receipt["bypass_quarantine"] is False
    assert receipt["chatgpt_bridge_mutated"] is False
    assert receipt["quarantine"]["status"] == "quarantined"
    assert receipt["authority"]["level"] == "derived"
    assert receipt["evidence_class"] == "fixture-only"
    assert receipt["pilot_roots"] == 0
    assert receipt["authentic_estate"] is False
    assert receipt["atlas_2_1_release_certified"] is False
    assert "generated.at" not in receipt.get("generated", {})


def test_quarantine_envelope_forbids_bypass_and_layer_b() -> None:
    envelope = _load_json(FIXTURES / "quarantine-envelope.sample.json")
    assert isinstance(envelope, dict)
    assert envelope["bypass_quarantine"] is False
    assert envelope["layer_b_write"] is False
    assert envelope["llm_authority"] is False
    assert envelope["status"] == "quarantined"
    assert envelope["authority"]["level"] == "derived"


def test_opt_in_request_keeps_export_default() -> None:
    request = _load_json(FIXTURES / "live-bridge-request.sample.json")
    assert isinstance(request, dict)
    assert request["opt_in"] is True
    assert request["live_chatgpt_api"] is True
    assert request["export_bridge_default"] is True
    assert request["status"] == "accepted_opt_in"
    assert request["pilot_roots"] == 0


def test_negative_actions_are_rejected_forbidden() -> None:
    cases = {
        "negative-bypass-quarantine.expect.json": (
            "bypass_quarantine",
            "chatgpt-live-bypass-quarantine-forbidden",
        ),
        "negative-layer-b-write.expect.json": (
            "layer_b_write",
            "chatgpt-live-layer-b-write-forbidden",
        ),
        "negative-llm-authority.expect.json": (
            "llm_authority_stamp",
            "chatgpt-live-llm-authority-forbidden",
        ),
        "negative-default-on-live.expect.json": (
            "default_on_live",
            "chatgpt-live-default-on-forbidden",
        ),
        "negative-pilot-invent.expect.json": (
            "pilot_invent",
            "chatgpt-live-pilot-invent-forbidden",
        ),
    }
    for name, (kind, error) in cases.items():
        payload = _load_json(FIXTURES / name)
        assert isinstance(payload, dict)
        assert payload["kind"] == kind
        assert payload["status"] == "rejected_forbidden"
        assert payload["expected_error"] == error
        assert payload["authority"]["level"] == "derived"


def test_request_schema_rejects_opt_in_false_with_live_true() -> None:
    schema = _schema("request")
    bad = _load_json(FIXTURES / "live-bridge-request.sample.json")
    assert isinstance(bad, dict)
    bad["opt_in"] = False
    bad["live_chatgpt_api"] = True
    bad["status"] = "accepted_opt_in"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=schema)


def test_receipt_schema_rejects_bypass_quarantine_true() -> None:
    schema = _schema("receipt")
    bad = _load_json(FIXTURES / "live-session-quarantined.sample.json")
    assert isinstance(bad, dict)
    bad["bypass_quarantine"] = True
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=schema)


def test_no_runtime_chatgpt_bridge_mutation() -> None:
    """Capability-maturity-scoped 2.2 prep guard (D-INTEGRATE-007A).

    Keyed on docs/atlas-2.2/PACKAGE-MATURITY.json: 'chatgpt-live' must not
    mutate its production surface while prep-frozen; an implementation-
    unlocked capability may legitimately mutate its own surface.
    """
    assert_prep_branch_scope("chatgpt-live")
