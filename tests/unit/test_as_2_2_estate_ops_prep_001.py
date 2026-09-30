"""AS-2.2-ESTATE-OPS-PREP-001 — docs/contracts/fixtures only (no src mutation)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
from _atlas_2_2_maturity import assert_prep_branch_scope

ROOT = Path(__file__).resolve().parents[2]
PREP = ROOT / "docs" / "atlas-2.2" / "estate-ops"
CONTRACTS = PREP / "contracts"
FIXTURES = PREP / "fixtures"

DOCS = {
    "package": PREP / "AS-2.2-ESTATE-OPS-PREP-001.md",
    "architecture": PREP / "ARCHITECTURE.md",
    "contract": PREP / "CONTRACT.md",
    "invariants": PREP / "INVARIANTS.md",
    "fixture_plan": PREP / "FIXTURE-PLAN.md",
    "adr": PREP / "adr" / "ADR-2.2-ESTATE-OPS-001-estate-ops-lens-prep.md",
}

SCHEMA_FILES = {
    "cockpit": "estate-ops-cockpit-view.schema.json",
    "mission_control": "mission-control-lens.schema.json",
    "ops_receipt": "ops-health-receipt.schema.json",
    "action": "estate-ops-action.schema.json",
}

FIXTURE_SCHEMA = {
    "cockpit-estate-selected.sample.json": "cockpit",
    "mission-control-lens.sample.json": "mission_control",
    "ops-health-receipt.sample.json": "ops_receipt",
    "negative-unknown-as-healthy.expect.json": "action",
    "negative-ui-canonical-write.expect.json": "action",
    "negative-pilot-invent.expect.json": "action",
}

HARBOR_PROJECTS = ("harbor-api", "harbor-ops", "harbor-portal")


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema(name: str) -> dict:
    return _load_json(CONTRACTS / SCHEMA_FILES[name])  # type: ignore[return-value]


def test_prep_docs_exist_without_readme() -> None:
    for path in DOCS.values():
        assert path.is_file(), path
        text = path.read_text(encoding="utf-8")
        assert "AS-2.2-ESTATE-OPS" in text or "ESTATE OPS" in text
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
        assert "estate" in body.lower() or "ops" in body.lower() or "mission" in body.lower()
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
    assert "ops_health" in text or "ops health" in text.lower()
    assert "do not mutate" in text.lower() or "NONE" in text or "NO OPS RUNTIME" in text


def test_invariants_document_fail_closed_walls() -> None:
    text = DOCS["invariants"].read_text(encoding="utf-8")
    assert "unknown" in text.lower()
    assert "healthy" in text.lower()
    assert "UI ≠ canonical" in text or "UI≠canonical" in text
    assert "ops_health" in text or "ops health" in text.lower()
    assert "ATLAS_2_1_RELEASE_CERTIFIED" in text
    assert "NO" in text


def test_cockpit_honest_unknown_rollup_and_harbor_scope() -> None:
    cockpit = _load_json(FIXTURES / "cockpit-estate-selected.sample.json")
    assert isinstance(cockpit, dict)
    assert cockpit["package_id"] == "AS-2.2-ESTATE-OPS-PREP-001"
    assert cockpit["authority"]["level"] == "derived"
    assert cockpit["evidence_class"] == "fixture-only"
    assert cockpit["pilot_roots"] == 0
    assert cockpit["atlas_2_1_release_certified"] is False
    assert cockpit["authentic_estate"] is False
    scope = cockpit["estate_scope"]
    assert scope["kind"] == "estate-slice"
    assert set(scope["project_ids"]) == set(HARBOR_PROJECTS)
    receipt = cockpit["ops_receipt"]
    assert receipt["rollup"]["estate"] == "unknown"
    assert receipt["rollup"]["estate"] != "healthy"
    assert receipt["note"] == "OPERATIONAL HEALTH ≠ PROJECT AUTHORITY"


def test_mission_control_lens_partial_with_unknown_chip() -> None:
    lens = _load_json(FIXTURES / "mission-control-lens.sample.json")
    assert isinstance(lens, dict)
    assert lens["lens_kind"] == "mission-control"
    assert lens["project_id"] == "harbor-ops"
    assert lens["status"] == "partial"
    states = {chip["state"] for chip in lens["queue_chips"]}
    assert "unknown" in states
    assert lens["authority"]["level"] == "derived"


def test_ops_health_receipt_unknown_signals() -> None:
    receipt = _load_json(FIXTURES / "ops-health-receipt.sample.json")
    assert isinstance(receipt, dict)
    assert receipt["rollup"]["estate"] == "unknown"
    for signal in receipt["signals"]:
        assert signal["status"] == "unknown"
        assert signal["evidence_refs"] == []


def test_negative_actions_are_rejected_forbidden() -> None:
    cases = {
        "negative-unknown-as-healthy.expect.json": (
            "unknown_as_healthy",
            "estate-ops-unknown-as-healthy-forbidden",
        ),
        "negative-ui-canonical-write.expect.json": (
            "canonical_write",
            "estate-ops-ui-canonical-write-forbidden",
        ),
        "negative-pilot-invent.expect.json": (
            "pilot_invent",
            "estate-ops-pilot-invent-forbidden",
        ),
    }
    for name, (kind, error) in cases.items():
        payload = _load_json(FIXTURES / name)
        assert isinstance(payload, dict)
        assert payload["kind"] == kind
        assert payload["status"] == "rejected_forbidden"
        assert payload["expected_error"] == error
        assert payload["authority"]["level"] == "derived"
        assert payload["canonical_write"] is False


def test_adr_documents_prep_boundary() -> None:
    text = DOCS["adr"].read_text(encoding="utf-8")
    assert "ATLAS_2_1_RELEASE_CERTIFIED = NO" in text or (
        "ATLAS_2_1_RELEASE_CERTIFIED" in text and "**NO**" in text
    )
    assert "unknown" in text.lower()
    assert "do **not** mutate" in text.lower() or "Do **not** mutate" in text


def test_no_runtime_ops_mutation() -> None:
    """Capability-maturity-scoped 2.2 prep guard (D-INTEGRATE-007A).

    Keyed on docs/atlas-2.2/PACKAGE-MATURITY.json: 'estate-ops' must not
    mutate its production surface while prep-frozen; an implementation-
    unlocked capability may legitimately mutate its own surface.
    """
    assert_prep_branch_scope("estate-ops")
