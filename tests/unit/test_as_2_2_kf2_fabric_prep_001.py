"""AS-2.2-KF2-FABRIC-PREP-001 — docs/fixtures/ADR presence + invariants."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
PREP = ROOT / "docs" / "atlas-2.2" / "kf2-fabric"
FIXTURES = PREP / "fixtures"
CONTRACTS = PREP / "contracts"
ADR = PREP / "adr" / "ADR-2.2-KF2-FABRIC-001-estate-fabric-prep.md"

SUBSTRATE_PACKAGES = (
    "AS-KF2-NS-001",
    "AS-KF2-ENTITY-001",
    "AS-KF2-REL-001",
    "AS-KF2-002",
)

PROJECTION_FIXTURES = (
    "namespace.sample.json",
    "entity.sample.json",
    "relationship.sample.json",
    "inventory-export.sample.json",
)


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def test_kf2_fabric_prep_docs_present() -> None:
    required = [
        "AS-2.2-KF2-FABRIC-PREP-001.md",
        "README.md",
        "ARCHITECTURE.md",
        "CONTRACT.md",
        "INVARIANTS.md",
        "FIXTURE-PLAN.md",
    ]
    for name in required:
        assert (PREP / name).is_file(), name
    assert ADR.is_file()
    assert (CONTRACTS / "kf2-estate-fabric-inventory.schema.json").is_file()
    assert (CONTRACTS / "kf2-estate-fabric-scenario.schema.json").is_file()
    assert (CONTRACTS / "kf2-estate-projection.schema.json").is_file()


def test_kf2_fabric_prep_invariants_documented() -> None:
    text = (PREP / "INVARIANTS.md").read_text(encoding="utf-8")
    assert "KF2 ≠ AUTHORITY" in text
    assert "NO CROSS PROMOTE" in text
    assert "PROJECTION ≠ MUTATION" in text
    assert "KF2 ≠ FED" in text
    assert "ATLAS_2_1_RELEASE_CERTIFIED = NO" in text
    assert "ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED" in text


def test_kf2_fabric_prep_package_card_non_claims() -> None:
    text = (PREP / "AS-2.2-KF2-FABRIC-PREP-001.md").read_text(encoding="utf-8")
    assert "ATLAS_2_1_RELEASE_CERTIFIED" in text and "**NO**" in text
    assert "ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED" in text
    assert "AS-KF2-NS-001" in text
    assert "AS-KF2-ENTITY-001" in text
    assert "AS-KF2-REL-001" in text
    assert "AS-KF2-002" in text
    assert "AS-2.2-KF2-FABRIC-001" in text
    assert "Production mutation" in text and "NONE" in text
    assert "without mutating" in text.lower() or "do not mutate" in text.lower()
    assert "docs/atlas-2.2/README.md" in text
    assert "after `v2.1.0`" in text or "after v2.1.0" in text


def test_kf2_fabric_contract_stubs_are_prep_only_not_package_data() -> None:
    package_schemas = ROOT / "src" / "project_atlas" / "schemas"
    for filename in (
        "kf2-estate-fabric-inventory.schema.json",
        "kf2-estate-fabric-scenario.schema.json",
        "kf2-estate-projection.schema.json",
    ):
        stub = CONTRACTS / filename
        body = stub.read_text(encoding="utf-8")
        assert "PREP STUB" in body
        assert not (package_schemas / filename).exists()


def test_kf2_fabric_inventory_fixture_invariants() -> None:
    payload = _load_json(FIXTURES / "fabric-inventory.fixture.json")
    assert isinstance(payload, dict)
    assert payload["package_id"] == "AS-2.2-KF2-FABRIC-PREP-001"
    assert payload["pilot_roots"] == 0
    assert payload["authentic_estate_pilot_passed"] is False
    assert payload["atlas_2_1_release_certified"] is False
    assert payload["cross_promote"] is False
    assert payload["invariants"] == {
        "kf2_ne_authority": True,
        "no_cross_promote": True,
        "projection_ne_mutation": True,
        "kf2_ne_fed": True,
    }
    assert payload["scenario_count"] == len(payload["scenarios"]) == 4
    substrates = {row["substrate_package"] for row in payload["scenarios"]}
    assert substrates == set(SUBSTRATE_PACKAGES)
    scenario_schema = _load_json(CONTRACTS / "kf2-estate-fabric-scenario.schema.json")
    assert isinstance(scenario_schema, dict)
    for row in payload["scenarios"]:
        assert row["evidence_class"] == "fixture-only"
        assert row["authentic_estate"] is False
        assert row["cross_promote"] is False
        assert row["authority_elevated"] is False
        jsonschema.validate(instance=row, schema=scenario_schema)
    inventory_schema = _load_json(CONTRACTS / "kf2-estate-fabric-inventory.schema.json")
    assert isinstance(inventory_schema, dict)
    assert inventory_schema["properties"]["package_id"]["const"] == ("AS-2.2-KF2-FABRIC-PREP-001")
    assert inventory_schema["properties"]["atlas_2_1_release_certified"]["const"] is False
    assert inventory_schema["properties"]["cross_promote"]["const"] is False


def test_kf2_projection_fixtures_validate_and_stay_non_authority() -> None:
    schema = _load_json(CONTRACTS / "kf2-estate-projection.schema.json")
    assert isinstance(schema, dict)
    for name in PROJECTION_FIXTURES:
        path = FIXTURES / name
        assert path.is_file(), name
        payload = _load_json(path)
        assert isinstance(payload, dict)
        jsonschema.validate(instance=payload, schema=schema)
        assert payload["package_id"] == "AS-2.2-KF2-FABRIC-PREP-001"
        assert payload["authority"]["level"] == "derived"
        assert payload["cross_promote"] is False
        assert payload["atlas_2_1_release_certified"] is False
        assert payload["evidence_class"] == "fixture-only"


def test_kf2_negative_fixtures_present() -> None:
    cross = _load_json(FIXTURES / "negative-cross-promote.expect.json")
    elevate = _load_json(FIXTURES / "negative-authority-elevate.expect.json")
    write = _load_json(FIXTURES / "negative-projection-write.expect.json")
    assert isinstance(cross, dict)
    assert isinstance(elevate, dict)
    assert isinstance(write, dict)
    assert cross["expected_error"] == "kf2-prep-cross-promote-forbidden"
    assert elevate["expected_error"] == "kf2-prep-authority-elevate-forbidden"
    assert write["expected_error"] == "kf2-prep-projection-write-forbidden"
    assert cross["atlas_2_1_release_certified"] is False
    assert elevate["atlas_2_1_release_certified"] is False
    assert write["atlas_2_1_release_certified"] is False


def test_kf2_fabric_prep_adr_non_claims() -> None:
    text = ADR.read_text(encoding="utf-8")
    assert "ATLAS_2_1_RELEASE_CERTIFIED = NO" in text
    assert "do **not** mutate" in text.lower() or "do not mutate" in text.lower()
    assert "AS-KF2" in text
    assert "docs/atlas-2.2/README.md" in text
    assert "ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED" in text


def test_kf2_prep_does_not_touch_runtime_modules() -> None:
    """Guardrail: this PREP package must not land new runtime modules under src/."""
    src = ROOT / "src" / "project_atlas"
    forbidden_names = (
        "kf2_estate_fabric.py",
        "kf2_estate_projection.py",
        "kf2_fabric_prep.py",
    )
    for name in forbidden_names:
        assert not (src / name).exists()
