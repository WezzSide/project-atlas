"""AS-2.2-TIME-MACHINE-DEEPEN-PREP-001 — docs/contracts/fixtures only (no src mutation)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from _atlas_2_2_maturity import assert_prep_branch_scope

ROOT = Path(__file__).resolve().parents[2]
PREP = ROOT / "docs" / "atlas-2.2" / "time-machine"
CONTRACTS = PREP / "contracts"
FIXTURES = PREP / "fixtures"

DOCS = {
    "package": PREP / "AS-2.2-TIME-MACHINE-DEEPEN-PREP-001.md",
    "readme": PREP / "README.md",
    "architecture": PREP / "ARCHITECTURE.md",
    "contract": PREP / "CONTRACT.md",
    "invariants": PREP / "INVARIANTS.md",
    "fixture_plan": PREP / "FIXTURE-PLAN.md",
    "adr": PREP / "adr" / "ADR-2.2-TIME-MACHINE-001-time-machine-deepen-prep.md",
}

SCHEMA_FILES = {
    "action": "time-machine-forbidden-action.schema.json",
}

FIXTURE_SCHEMA = {
    "negative-layer-b-promotion.expect.json": "action",
    "negative-llm-authority.expect.json": "action",
    "negative-silent-overlap-winner.expect.json": "action",
    "negative-pilot-invent.expect.json": "action",
    "negative-release-cert-stamp.expect.json": "action",
}


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema(name: str) -> dict:
    return _load_json(CONTRACTS / SCHEMA_FILES[name])  # type: ignore[return-value]


def test_prep_docs_exist() -> None:
    for path in DOCS.values():
        assert path.is_file(), path
        text = path.read_text(encoding="utf-8")
        assert "TIME-MACHINE" in text or "time-machine" in text or "Time Machine" in text
        assert "PREP" in text.upper() or "prep" in text


def test_deepen_path_extends_base_time_machine_without_relocation() -> None:
    """Deepen tree must reference but not relocate base stub paths."""
    assert (CONTRACTS / "as-of-snapshot.schema.json").is_file()
    assert (FIXTURES / "as-of-selected.sample.json").is_file()
    assert (FIXTURES / "diff-t1-t2.sample.json").is_file()
    package = DOCS["package"].read_text(encoding="utf-8")
    assert "time-machine/" in package
    assert "deepen" in package.lower()
    assert "do not dual-own" in package.lower() or "do not relocate" in package.lower()
    assert not (ROOT / "docs" / "atlas-2.2" / "contracts" / "time-machine").exists()


def test_contract_stub_exists_and_is_not_package_data() -> None:
    package_schemas = ROOT / "src" / "project_atlas" / "schemas"
    filename = SCHEMA_FILES["action"]
    stub = CONTRACTS / filename
    assert stub.is_file(), stub
    body = stub.read_text(encoding="utf-8")
    assert "PREP STUB" in body
    assert "time-machine" in body.lower()
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
    assert "Layer B" in text or "bitemporal" in text


def test_invariants_document_fail_closed_walls() -> None:
    text = DOCS["invariants"].read_text(encoding="utf-8")
    assert "AS-OF ≠ LAYER B" in text or "As-of ≠ Layer B" in text
    assert "DIFF ≠ MUTATION" in text or "Diff ≠ mutation" in text
    assert "SILENT OVERLAP" in text.upper() or "silent overlap" in text
    assert "WALL-CLOCK" in text.upper() or "wall-clock" in text
    assert "GRAPH ≠ AUTHORITY" in text or "Graph ≠ authority" in text
    assert "LLM ≠ authority" in text or "LLM ≠ AUTHORITY" in text
    assert "ATLAS_2_1_RELEASE_CERTIFIED" in text
    assert "NO" in text


def test_negative_actions_are_rejected_forbidden() -> None:
    cases = {
        "negative-layer-b-promotion.expect.json": (
            "layer_b_promotion",
            "time-machine-layer-b-promotion-forbidden",
        ),
        "negative-llm-authority.expect.json": (
            "llm_authority_stamp",
            "time-machine-llm-authority-forbidden",
        ),
        "negative-silent-overlap-winner.expect.json": (
            "silent_overlap_winner",
            "time-machine-silent-overlap-forbidden",
        ),
        "negative-pilot-invent.expect.json": (
            "fixture_as_authentic_pilot",
            "time-machine-fixture-as-pilot-forbidden",
        ),
        "negative-release-cert-stamp.expect.json": (
            "release_cert_from_fixture",
            "time-machine-release-cert-from-fixture-forbidden",
        ),
    }
    for name, (kind, error) in cases.items():
        payload = _load_json(FIXTURES / name)
        assert isinstance(payload, dict)
        assert payload["kind"] == kind
        assert payload["status"] == "rejected_forbidden"
        assert payload["expected_error"] == error
        assert payload["authority"]["level"] == "derived"
        assert payload["consume_only"] is True
        assert payload["pilot_roots"] == 0


def test_base_positive_fixtures_remain_peer() -> None:
    overlap = _load_json(FIXTURES / "as-of-overlap.expect.json")
    assert isinstance(overlap, dict)
    assert overlap["package_id"] == "AS-2.2-TIME-MACHINE-001"
    assert overlap["status"] != "selected"
    wall_clock = _load_json(FIXTURES / "rejected-wall-clock.expect.json")
    assert isinstance(wall_clock, dict)
    assert wall_clock["as_of_valid_time"] == "now"
    assert wall_clock["status"] == "rejected_malformed"


def test_forbidden_action_schema_rejects_missing_required_fields() -> None:
    schema = _schema("action")
    bad = _load_json(FIXTURES / "negative-llm-authority.expect.json")
    assert isinstance(bad, dict)
    del bad["truth_boundary"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=schema)


def test_no_production_mutation_paths_in_prep_tree() -> None:
    """Capability-maturity-scoped 2.2 prep guard (D-INTEGRATE-007A).

    Keyed on docs/atlas-2.2/PACKAGE-MATURITY.json: 'time-machine' must not
    mutate its production surface while prep-frozen; an implementation-
    unlocked capability may legitimately mutate its own surface.
    """
    assert_prep_branch_scope("time-machine")
