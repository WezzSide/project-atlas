"""AS-2.2-COMPAT-PIN-PREP-001 — docs/fixtures/ADR presence + invariants."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
PREP = ROOT / "docs" / "atlas-2.2" / "compat-pin"
FIXTURES = PREP / "fixtures"
CONTRACTS = PREP / "contracts"
ADR = PREP / "adr" / "ADR-2.2-COMPAT-PIN-001-2.1-anchor-prep.md"

CONSUMER_PACKAGES = (
    "AS-2.0-COMPAT-001",
    "AS-2.2-COMPAT-PIN-001",
    "AS-2.2-KCI-001",
    "AS-2.2-RET-CTX-001",
    "AS-2.2-CTX-COMPILER-001",
)


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def test_compat_pin_prep_docs_present() -> None:
    required = [
        "AS-2.2-COMPAT-PIN-PREP-001.md",
        "README.md",
        "ARCHITECTURE.md",
        "CONTRACT.md",
        "INVARIANTS.md",
        "FIXTURE-PLAN.md",
    ]
    for name in required:
        assert (PREP / name).is_file(), name
    assert ADR.is_file()
    assert (CONTRACTS / "compat-pin-expectation.schema.json").is_file()
    assert (CONTRACTS / "compat-pin-scenario.schema.json").is_file()


def test_compat_pin_prep_invariants_documented() -> None:
    text = (PREP / "INVARIANTS.md").read_text(encoding="utf-8")
    assert "PREP ≠ ANCHOR" in text
    assert "NO 2.1 RELEASE STAMP" in text
    assert "FUTURE PIN ONLY" in text
    assert "NO PILOT INVENT" in text
    assert "NO RUNTIME MUTATION" in text
    assert "ATLAS_2_1_RELEASE_CERTIFIED = NO" in text
    assert "ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED" in text


def test_compat_pin_prep_package_card_non_claims() -> None:
    text = (PREP / "AS-2.2-COMPAT-PIN-PREP-001.md").read_text(encoding="utf-8")
    assert "ATLAS_2_1_RELEASE_CERTIFIED" in text and "**NO**" in text
    assert "ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED" in text
    assert "AS-2.0-COMPAT-001" in text
    assert "atlas-2.1.0-compat" in text
    assert "AS-2.2-COMPAT-PIN-001" in text
    assert "Production mutation" in text and "NONE" in text
    assert "without mutating" in text.lower() or "do not mutate" in text.lower()
    assert "docs/atlas-2.2/README.md" in text
    assert "Not `v2.1.0` released" in text or "not yet certified" in text.lower()


def test_compat_pin_contract_stubs_are_prep_only_not_package_data() -> None:
    package_schemas = ROOT / "src" / "project_atlas" / "schemas"
    for filename in (
        "compat-pin-expectation.schema.json",
        "compat-pin-scenario.schema.json",
    ):
        stub = CONTRACTS / filename
        body = stub.read_text(encoding="utf-8")
        assert "PREP STUB" in body
        assert not (package_schemas / filename).exists()


def test_compat_pin_expectation_fixture_invariants() -> None:
    payload = _load_json(FIXTURES / "compat-expectation.fixture.json")
    assert isinstance(payload, dict)
    assert payload["package_id"] == "AS-2.2-COMPAT-PIN-PREP-001"
    assert payload["live_compat_snapshot_id"] == "atlas-1.0.0-compat"
    assert payload["future_compat_snapshot_id"] == "atlas-2.1.0-compat"
    assert payload["future_tag"] == "v2.1.0"
    assert payload["pilot_roots"] == 0
    assert payload["authentic_estate_pilot_passed"] is False
    assert payload["atlas_2_1_release_certified"] is False
    assert payload["atlas_2_2_intelligence_unlocked"] is False
    assert payload["invariants"] == {
        "prep_ne_anchor": True,
        "no_2_1_release_stamp": True,
        "future_pin_only": True,
        "no_pilot_invent": True,
        "no_runtime_mutation": True,
    }
    assert payload["scenario_count"] == len(payload["scenarios"]) == 5
    consumers = {row["consumer_package"] for row in payload["scenarios"]}
    assert consumers == set(CONSUMER_PACKAGES)
    scenario_schema = _load_json(CONTRACTS / "compat-pin-scenario.schema.json")
    assert isinstance(scenario_schema, dict)
    for row in payload["scenarios"]:
        assert row["evidence_class"] == "fixture-only"
        assert row["authentic_estate"] is False
        assert row["invent_pilot_roots"] is False
        assert row["release_certified"] is False
        jsonschema.validate(instance=row, schema=scenario_schema)
    inventory_schema = _load_json(CONTRACTS / "compat-pin-expectation.schema.json")
    assert isinstance(inventory_schema, dict)
    assert inventory_schema["properties"]["package_id"]["const"] == ("AS-2.2-COMPAT-PIN-PREP-001")
    assert inventory_schema["properties"]["atlas_2_1_release_certified"]["const"] is False
    assert (
        inventory_schema["properties"]["future_compat_snapshot_id"]["const"] == "atlas-2.1.0-compat"
    )


def test_compat_pin_negative_fixtures_present() -> None:
    release = _load_json(FIXTURES / "negative-release-certified.expect.json")
    pilot = _load_json(FIXTURES / "negative-pilot-invent.expect.json")
    assert isinstance(release, dict)
    assert isinstance(pilot, dict)
    assert release["expected_error"] == "compat-pin-prep-release-certified-forbidden"
    assert pilot["expected_error"] == "compat-pin-prep-pilot-invent-forbidden"
    assert release["atlas_2_1_release_certified"] is False
    assert pilot["atlas_2_1_release_certified"] is False
    assert release["forbidden_inventory"]["atlas_2_1_release_certified"] is True
    assert pilot["forbidden_inventory"]["invent_pilot_roots"] is True


def test_compat_pin_prep_adr_non_claims() -> None:
    text = ADR.read_text(encoding="utf-8")
    assert "ATLAS_2_1_RELEASE_CERTIFIED = NO" in text
    assert "do **not** mutate" in text.lower() or "do not mutate" in text.lower()
    assert "atlas-2.1.0-compat" in text
    assert "docs/atlas-2.2/README.md" in text
    assert "ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED" in text


def test_compat_pin_prep_does_not_touch_runtime_modules() -> None:
    """Guardrail: this PREP package must not land new runtime modules under src/."""
    src = ROOT / "src" / "project_atlas"
    forbidden_names = (
        "compat_pin.py",
        "compat_pin_prep.py",
        "compat_anchor_2_1.py",
    )
    for name in forbidden_names:
        assert not (src / name).exists()


def test_compat_pin_prep_does_not_publish_2_1_release_anchor() -> None:
    anchor_path = ROOT / "docs" / "releases" / "2.1.0" / "compatibility-anchor.json"
    assert not anchor_path.is_file()
