"""AS-2.2-XPROJ-CONTRACT-PREP-001 — docs/fixtures/ADR presence + invariants."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
PREP = ROOT / "docs" / "atlas-2.2" / "xproj"
FIXTURES = PREP / "fixtures"
CONTRACTS = PREP / "contracts"
ADR = PREP / "adr" / "ADR-2.2-XPROJ-001-cross-project-fabric-prep.md"

SUBSTRATE_PACKAGES = (
    "AS-XPROJ-001",
    "AS-XPROJ-002",
    "AS-XPROJ-003",
    "AS-XPROJ-004",
)

LENS_FIXTURES = (
    "entity-join.sample.json",
    "cross-project-edge.sample.json",
    "duplicate-candidate.sample.json",
    "conflict-index.sample.json",
)


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def test_xproj_contract_prep_docs_present() -> None:
    required = [
        "AS-2.2-XPROJ-CONTRACT-PREP-001.md",
        "README.md",
        "ARCHITECTURE.md",
        "CONTRACT.md",
        "INVARIANTS.md",
        "FIXTURE-PLAN.md",
    ]
    for name in required:
        assert (PREP / name).is_file(), name
    assert ADR.is_file()
    assert (CONTRACTS / "xproj-fabric-inventory.schema.json").is_file()
    assert (CONTRACTS / "xproj-fabric-scenario.schema.json").is_file()
    assert (CONTRACTS / "xproj-estate-lens.schema.json").is_file()


def test_xproj_contract_prep_invariants_documented() -> None:
    text = (PREP / "INVARIANTS.md").read_text(encoding="utf-8")
    assert "CROSS-PROJECT ≠ AUTHORITY" in text or "CROSS-PROJECT IDENTITY" in text
    assert "NAME ≠ IDENTITY" in text or "NAME / STRING ≠ IDENTITY" in text
    assert "NO AUTOCOLLAPSE" in text or "no autocollapse" in text.lower()
    assert "INDEX ≠ RET-001" in text or "≠ AS-RET-001" in text
    assert "ATLAS_2_1_RELEASE_CERTIFIED = NO" in text


def test_xproj_contract_prep_package_card_non_claims() -> None:
    text = (PREP / "AS-2.2-XPROJ-CONTRACT-PREP-001.md").read_text(encoding="utf-8")
    assert "ATLAS_2_1_RELEASE_CERTIFIED" in text and "**NO**" in text
    assert "ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED" in text
    assert "AS-XPROJ-001" in text
    assert "AS-XPROJ-002" in text
    assert "AS-XPROJ-003" in text
    assert "AS-XPROJ-004" in text
    assert "Production mutation" in text and "NONE" in text
    assert "do not mutate" in text.lower() or "without mutating" in text.lower()
    assert "docs/atlas-2.2/README.md" in text


def test_xproj_contract_stubs_are_prep_only_not_package_data() -> None:
    package_schemas = ROOT / "src" / "project_atlas" / "schemas"
    for filename in (
        "xproj-fabric-inventory.schema.json",
        "xproj-fabric-scenario.schema.json",
        "xproj-estate-lens.schema.json",
    ):
        stub = CONTRACTS / filename
        body = stub.read_text(encoding="utf-8")
        assert "PREP STUB" in body
        assert not (package_schemas / filename).exists()


def test_xproj_fabric_inventory_fixture_invariants() -> None:
    payload = _load_json(FIXTURES / "fabric-inventory.fixture.json")
    assert isinstance(payload, dict)
    assert payload["package_id"] == "AS-2.2-XPROJ-CONTRACT-PREP-001"
    assert payload["pilot_roots"] == 0
    assert payload["authentic_estate_pilot_passed"] is False
    assert payload["atlas_2_1_release_certified"] is False
    assert payload["invariants"] == {
        "cross_project_ne_authority": True,
        "name_ne_identity": True,
        "no_autocollapse": True,
        "index_ne_ret001": True,
    }
    assert payload["scenario_count"] == len(payload["scenarios"]) == 4
    substrates = {row["substrate_package"] for row in payload["scenarios"]}
    assert substrates == set(SUBSTRATE_PACKAGES)
    scenario_schema = _load_json(CONTRACTS / "xproj-fabric-scenario.schema.json")
    assert isinstance(scenario_schema, dict)
    for row in payload["scenarios"]:
        assert row["evidence_class"] == "fixture-only"
        assert row["authentic_estate"] is False
        assert row["autocollapse"] is False
        assert row["authority_elevated"] is False
        # Validate rows offline against the scenario stub (avoid remote $ref fetch).
        jsonschema.validate(instance=row, schema=scenario_schema)
    inventory_schema = _load_json(CONTRACTS / "xproj-fabric-inventory.schema.json")
    assert isinstance(inventory_schema, dict)
    assert inventory_schema["properties"]["package_id"]["const"] == (
        "AS-2.2-XPROJ-CONTRACT-PREP-001"
    )
    assert inventory_schema["properties"]["atlas_2_1_release_certified"]["const"] is False


def test_xproj_lens_fixtures_validate_and_stay_non_authority() -> None:
    schema = _load_json(CONTRACTS / "xproj-estate-lens.schema.json")
    assert isinstance(schema, dict)
    for name in LENS_FIXTURES:
        path = FIXTURES / name
        assert path.is_file(), name
        payload = _load_json(path)
        assert isinstance(payload, dict)
        jsonschema.validate(instance=payload, schema=schema)
        assert payload["package_id"] == "AS-2.2-XPROJ-CONTRACT-PREP-001"
        assert payload["authority"]["level"] == "derived"
        assert payload["atlas_2_1_release_certified"] is False
        assert payload["evidence_class"] == "fixture-only"


def test_xproj_negative_fixtures_present() -> None:
    fuzzy = _load_json(FIXTURES / "negative-fuzzy-join.expect.json")
    auto = _load_json(FIXTURES / "negative-autocollapse.expect.json")
    elevate = _load_json(FIXTURES / "negative-authority-elevate.expect.json")
    assert isinstance(fuzzy, dict)
    assert isinstance(auto, dict)
    assert isinstance(elevate, dict)
    assert fuzzy["expected_error"] == "xproj-prep-fuzzy-join-forbidden"
    assert auto["expected_error"] == "xproj-prep-autocollapse-forbidden"
    assert elevate["expected_error"] == "xproj-prep-authority-elevate-forbidden"
    assert fuzzy["atlas_2_1_release_certified"] is False
    assert auto["atlas_2_1_release_certified"] is False
    assert elevate["atlas_2_1_release_certified"] is False


def test_xproj_contract_prep_adr_non_claims() -> None:
    text = ADR.read_text(encoding="utf-8")
    assert "ATLAS_2_1_RELEASE_CERTIFIED = NO" in text
    assert "do **not** mutate" in text.lower() or "do not mutate" in text.lower()
    assert "AS-XPROJ-001" in text
    assert "docs/atlas-2.2/README.md" in text


def test_xproj_prep_does_not_touch_runtime_modules() -> None:
    """Guardrail: this PREP package must not land under src/."""
    src_xproj = ROOT / "src" / "project_atlas"
    forbidden_names = (
        "xproj_fabric.py",
        "xproj_estate_lens.py",
        "xproj_contract_prep.py",
    )
    for name in forbidden_names:
        assert not (src_xproj / name).exists()
