"""AS-2.2-ROADMAP-CROSSWALK-DEEPEN-PREP-001 — docs/contracts/fixtures only."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
PREP = ROOT / "docs" / "atlas-2.2" / "roadmap-crosswalk"
CONTRACTS = PREP / "contracts"
FIXTURES = PREP / "fixtures"
BASE = PREP / "AS-2.2-ROADMAP-CROSSWALK-PREP-001.md"

FIXTURES_BY_KIND = {
    "unlock_claim": "negative-deepen-unlock-claim.expect.json",
    "production_ready_claim": "negative-deepen-production-ready-claim.expect.json",
    "release_cert_stamp": "negative-deepen-release-cert-stamp.expect.json",
    "pilot_invent": "negative-deepen-pilot-invent.expect.json",
    "runtime_mutation": "negative-deepen-runtime-mutation.expect.json",
    "llm_authority_stamp": "negative-deepen-llm-authority.expect.json",
    "fixture_as_certification": "negative-deepen-fixture-as-certification.expect.json",
}


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def test_crosswalk_deepen_docs_present() -> None:
    for name in (
        "AS-2.2-ROADMAP-CROSSWALK-DEEPEN-PREP-001.md",
        "INVARIANTS.md",
        "DEEPEN-FIXTURE-PLAN.md",
        "adr/ADR-2.2-ROADMAP-CROSSWALK-001-deepen-prep.md",
    ):
        path = PREP / name
        assert path.is_file(), name
        assert "PREP" in path.read_text(encoding="utf-8").upper()
    package = (PREP / "AS-2.2-ROADMAP-CROSSWALK-DEEPEN-PREP-001.md").read_text(encoding="utf-8")
    assert "ATLAS_2_1_RELEASE_CERTIFIED" in package
    assert "**NO**" in package
    assert "ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED" in package


def test_crosswalk_deepen_extends_base_without_relocation() -> None:
    assert BASE.is_file()
    assert (PREP / "CROSSWALK.md").is_file()
    assert (FIXTURES / "crosswalk.fixture.json").is_file()
    package = (PREP / "AS-2.2-ROADMAP-CROSSWALK-DEEPEN-PREP-001.md").read_text(encoding="utf-8")
    assert "do not dual-own" in package.lower() or "do not relocate" in package.lower()
    assert "roadmap-crosswalk/" in package


def test_crosswalk_forbidden_action_schema_not_package_data() -> None:
    stub = CONTRACTS / "roadmap-crosswalk-forbidden-action.schema.json"
    assert stub.is_file()
    body = stub.read_text(encoding="utf-8")
    assert "PREP STUB" in body
    schema = _load(stub)
    assert isinstance(schema, dict)
    assert schema["properties"]["status"]["const"] == "rejected_forbidden"  # type: ignore[index]
    package_schemas = ROOT / "src" / "project_atlas" / "schemas"
    assert not (package_schemas / stub.name).exists()


def test_crosswalk_forbidden_negatives() -> None:
    schema = _load(CONTRACTS / "roadmap-crosswalk-forbidden-action.schema.json")
    for kind, filename in FIXTURES_BY_KIND.items():
        payload = _load(FIXTURES / filename)
        assert isinstance(payload, dict)
        jsonschema.validate(instance=payload, schema=schema)  # type: ignore[arg-type]
        assert payload["kind"] == kind
        assert payload["status"] == "rejected_forbidden"
        assert payload["pilot_pass"] is False
        assert payload["release_certified"] is False
        assert payload["canonical_writes"] is False
        assert payload["authentic_estate"] is False
        assert payload["evidence_class"] == "fixture-only"
        assert payload["authority"]["level"] == "derived"


def test_crosswalk_invariants_document_fail_closed_walls() -> None:
    text = (PREP / "INVARIANTS.md").read_text(encoding="utf-8")
    assert "CROSSWALK ≠ UNLOCK" in text
    assert "PREP ≠ PRODUCTION" in text
    assert "ATLAS_2_1_RELEASE_CERTIFIED" in text
    assert "NO" in text
