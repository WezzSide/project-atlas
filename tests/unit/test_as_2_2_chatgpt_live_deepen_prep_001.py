"""AS-2.2-CHATGPT-LIVE-DEEPEN-PREP-001 — docs/contracts/fixtures only (no src mutation)."""

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
ASK2 = ROOT / "docs" / "atlas-2.2" / "ask-atlas-2"

DOCS = {
    "package": PREP / "AS-2.2-CHATGPT-LIVE-DEEPEN-PREP-001.md",
    "base_package": PREP / "AS-2.2-CHATGPT-LIVE-PREP-001.md",
    "architecture": PREP / "ARCHITECTURE.md",
    "contract": PREP / "CONTRACT.md",
    "invariants": PREP / "INVARIANTS.md",
    "fixture_plan": PREP / "FIXTURE-PLAN.md",
    "adr": PREP / "adr" / "ADR-2.2-CHATGPT-LIVE-001-quarantine-first-live-bridge-deepen-prep.md",
}

SCHEMA_FILES = {
    "action": "chatgpt-live-deepen-forbidden-action.schema.json",
}

FIXTURE_SCHEMA = {
    "negative-env-force-live.expect.json": "action",
    "negative-billing-without-opt-in.expect.json": "action",
    "negative-bypass-quarantine-deepen.expect.json": "action",
    "negative-release-cert-stamp.expect.json": "action",
}

BASE_NEGATIVES = (
    "negative-layer-b-write.expect.json",
    "negative-llm-authority.expect.json",
    "negative-pilot-invent.expect.json",
)


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


def test_deepen_path_extends_base_without_relocation() -> None:
    """Deepen tree must reference but not relocate base stub paths."""
    assert (CONTRACTS / "live-bridge-request.schema.json").is_file()
    assert (CONTRACTS / "quarantine-envelope.schema.json").is_file()
    assert (CONTRACTS / "forbidden-action.schema.json").is_file()
    assert (FIXTURES / "live-session-quarantined.sample.json").is_file()
    for name in BASE_NEGATIVES:
        assert (FIXTURES / name).is_file(), name
    package = DOCS["package"].read_text(encoding="utf-8")
    assert "chatgpt-live/" in package
    assert "deepen" in package.lower()
    assert "do not dual-own" in package.lower() or "do not relocate" in package.lower()
    assert "ask-atlas-2" in package.lower()
    assert "env" in package.lower()
    assert "billing" in package.lower()
    assert "quarantine" in package.lower()


def test_contract_stub_exists_and_is_not_package_data() -> None:
    package_schemas = ROOT / "src" / "project_atlas" / "schemas"
    filename = SCHEMA_FILES["action"]
    stub = CONTRACTS / filename
    assert stub.is_file(), stub
    body = stub.read_text(encoding="utf-8")
    assert "PREP STUB" in body
    assert "chatgpt" in body.lower()
    assert "env_force_live" in body
    assert "billing_without_opt_in" in body
    assert "bypass_quarantine" in body
    assert "release_cert_stamp" in body
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
    assert "DEMO" in text.upper()
    assert "PILOT" in text


def test_invariants_document_fail_closed_walls() -> None:
    text = DOCS["invariants"].read_text(encoding="utf-8")
    assert "quarantine" in text.lower()
    assert "ENV ≠ OPT-IN" in text or "ENV≠OPT-IN" in text
    assert "BILLING ≠ SILENT" in text or "BILLING≠SILENT" in text
    assert "chatgpt_bridge" in text
    assert "ask-atlas-2" in text.lower()
    assert "LLM ≠ authority" in text or "LLM≠authority" in text
    assert "ATLAS_2_1_RELEASE_CERTIFIED" in text
    assert "NO" in text
    assert "DEMO ≠ RELEASE" in text or "DEMO≠RELEASE" in text


def test_negative_actions_are_rejected_forbidden() -> None:
    cases = {
        "negative-env-force-live.expect.json": (
            "env_force_live",
            "chatgpt-live-env-force-live-forbidden",
        ),
        "negative-billing-without-opt-in.expect.json": (
            "billing_without_opt_in",
            "chatgpt-live-billing-without-opt-in-forbidden",
        ),
        "negative-bypass-quarantine-deepen.expect.json": (
            "bypass_quarantine",
            "chatgpt-live-bypass-quarantine-forbidden",
        ),
        "negative-release-cert-stamp.expect.json": (
            "release_cert_stamp",
            "chatgpt-live-release-cert-stamp-forbidden",
        ),
    }
    for name, (kind, error) in cases.items():
        payload = _load_json(FIXTURES / name)
        assert isinstance(payload, dict)
        assert payload["package_id"] == "AS-2.2-CHATGPT-LIVE-DEEPEN-PREP-001"
        assert payload["kind"] == kind
        assert payload["status"] == "rejected_forbidden"
        assert payload["expected_error"] == error
        assert payload["authority"]["level"] == "derived"
        assert payload["pilot_roots"] == 0
        assert payload["invent_pilot_roots"] is False


def test_base_negatives_remain_peer() -> None:
    """Base release-adjacent negatives stay owned by base PREP package id."""
    for name in BASE_NEGATIVES:
        payload = _load_json(FIXTURES / name)
        assert isinstance(payload, dict)
        assert payload["package_id"] == "AS-2.2-CHATGPT-LIVE-PREP-001"
        assert payload["status"] == "rejected_forbidden"


def test_forbidden_action_schema_rejects_missing_required_fields() -> None:
    schema = _schema("action")
    bad = _load_json(FIXTURES / "negative-env-force-live.expect.json")
    assert isinstance(bad, dict)
    del bad["truth_boundary"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=schema)


def test_no_production_or_ask2_mutation_paths_in_prep_tree() -> None:
    """Capability-maturity-scoped 2.2 prep guard (D-INTEGRATE-007A).

    Keyed on docs/atlas-2.2/PACKAGE-MATURITY.json: 'chatgpt-live' must not
    mutate its production surface while prep-frozen; an implementation-
    unlocked capability may legitimately mutate its own surface.
    """
    assert_prep_branch_scope("chatgpt-live")
