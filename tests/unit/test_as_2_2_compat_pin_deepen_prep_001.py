"""AS-2.2-COMPAT-PIN-DEEPEN-PREP-001 — docs/contracts/fixtures only (no src mutation)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from _atlas_2_2_maturity import assert_prep_branch_scope

ROOT = Path(__file__).resolve().parents[2]
PREP = ROOT / "docs" / "atlas-2.2" / "compat-pin"
CONTRACTS = PREP / "contracts"
FIXTURES = PREP / "fixtures"

DOCS = {
    "package": PREP / "AS-2.2-COMPAT-PIN-DEEPEN-PREP-001.md",
    "architecture": PREP / "ARCHITECTURE.md",
    "contract": PREP / "CONTRACT.md",
    "invariants": PREP / "INVARIANTS.md",
    "fixture_plan": PREP / "FIXTURE-PLAN.md",
    "adr": PREP / "adr" / "ADR-2.2-COMPAT-PIN-001-2.1-anchor-deepen-prep.md",
}

SCHEMA_FILES = {
    "action": "compat-pin-forbidden-action.schema.json",
}

FIXTURE_SCHEMA = {
    "negative-deepen-release-cert-stamp.expect.json": "action",
    "negative-deepen-pilot-invent.expect.json": "action",
    "negative-deepen-anchor-publish.expect.json": "action",
    "negative-deepen-runtime-mutation.expect.json": "action",
    "negative-deepen-future-pin-as-live.expect.json": "action",
}

EVIDENCE_WALL = {
    "evidence_class": "fixture-only",
    "authentic_estate": False,
    "release_certified": False,
    "pilot_pass": False,
    "canonical_writes": False,
}


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema(name: str) -> dict:
    return _load_json(CONTRACTS / SCHEMA_FILES[name])  # type: ignore[return-value]


def test_prep_docs_exist() -> None:
    for path in DOCS.values():
        assert path.is_file(), path
        text = path.read_text(encoding="utf-8")
        assert "COMPAT-PIN" in text or "compat-pin" in text.lower()
        assert "PREP" in text.upper() or "prep" in text


def test_deepen_path_extends_base_compat_pin_without_relocation() -> None:
    """Deepen tree must reference but not relocate base stub paths."""
    assert (CONTRACTS / "compat-pin-expectation.schema.json").is_file()
    assert (CONTRACTS / "compat-pin-scenario.schema.json").is_file()
    assert (FIXTURES / "compat-expectation.fixture.json").is_file()
    assert (FIXTURES / "negative-release-certified.expect.json").is_file()
    assert (FIXTURES / "negative-pilot-invent.expect.json").is_file()
    package = DOCS["package"].read_text(encoding="utf-8")
    assert "compat-pin/" in package
    assert "deepen" in package.lower()
    assert "do not dual-own" in package.lower() or "do not relocate" in package.lower()
    assert (PREP / "AS-2.2-COMPAT-PIN-PREP-001.md").is_file()


def test_contract_stub_exists_and_is_not_package_data() -> None:
    package_schemas = ROOT / "src" / "project_atlas" / "schemas"
    filename = SCHEMA_FILES["action"]
    stub = CONTRACTS / filename
    assert stub.is_file(), stub
    body = stub.read_text(encoding="utf-8")
    assert "PREP STUB" in body
    assert "compat-pin" in body.lower()
    kind = _load_json(stub)
    assert isinstance(kind, dict)
    assert "enum" in kind["properties"]["kind"]
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
    assert (
        "compat_anchor" in text
        or "PREP ≠ ANCHOR" in text
        or "PREP ≠ ANCHOR" in (PREP / "INVARIANTS.md").read_text(encoding="utf-8")
    )


def test_invariants_document_fail_closed_walls() -> None:
    text = DOCS["invariants"].read_text(encoding="utf-8")
    assert "PREP ≠ ANCHOR" in text
    assert "NO 2.1 RELEASE STAMP" in text
    assert "NO PILOT INVENT" in text
    assert "NO RUNTIME MUTATION" in text
    assert "ATLAS_2_1_RELEASE_CERTIFIED" in text
    assert "NO" in text


def test_negative_actions_are_rejected_forbidden_with_evidence_wall() -> None:
    cases = {
        "negative-deepen-release-cert-stamp.expect.json": (
            "release_cert_stamp",
            "compat-pin-release-cert-stamp-forbidden",
        ),
        "negative-deepen-pilot-invent.expect.json": (
            "pilot_invent",
            "compat-pin-pilot-invent-forbidden",
        ),
        "negative-deepen-anchor-publish.expect.json": (
            "anchor_publish",
            "compat-pin-anchor-publish-forbidden",
        ),
        "negative-deepen-runtime-mutation.expect.json": (
            "runtime_mutation",
            "compat-pin-runtime-mutation-forbidden",
        ),
        "negative-deepen-future-pin-as-live.expect.json": (
            "future_pin_as_live",
            "compat-pin-future-pin-as-live-forbidden",
        ),
    }
    for name, (kind, error) in cases.items():
        payload = _load_json(FIXTURES / name)
        assert isinstance(payload, dict)
        assert payload["kind"] == kind
        assert payload["status"] == "rejected_forbidden"
        assert payload["expected_error"] == error
        assert payload["authority"]["level"] == "derived"
        for key, expected in EVIDENCE_WALL.items():
            assert payload[key] == expected, (name, key)


def test_forbidden_action_schema_rejects_missing_required_fields() -> None:
    schema = _schema("action")
    bad = _load_json(FIXTURES / "negative-deepen-pilot-invent.expect.json")
    assert isinstance(bad, dict)
    del bad["truth_boundary"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=schema)


def test_forbidden_action_schema_rejects_pilot_pass_true() -> None:
    schema = _schema("action")
    bad = _load_json(FIXTURES / "negative-deepen-pilot-invent.expect.json")
    assert isinstance(bad, dict)
    bad["pilot_pass"] = True
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=schema)


def test_no_production_mutation_paths_in_prep_tree() -> None:
    """Capability-maturity-scoped 2.2 prep guard (D-INTEGRATE-007A).

    Keyed on docs/atlas-2.2/PACKAGE-MATURITY.json: 'compat-pin' must not
    mutate its production surface while prep-frozen; an implementation-
    unlocked capability may legitimately mutate its own surface.
    """
    assert_prep_branch_scope("compat-pin")
