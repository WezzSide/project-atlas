"""AS-2.2-DOC-CHARTER-PREP-001 — charter deepen + matrix draft (no src mutation)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
from _atlas_2_2_maturity import assert_prep_branch_scope

ROOT = Path(__file__).resolve().parents[2]
PREP = ROOT / "docs" / "atlas-2.2" / "doc-charter"
CHARTER = ROOT / "docs" / "atlas-2.2" / "CHARTER.md"
CONTRACTS = PREP / "contracts"
FIXTURES = PREP / "fixtures"
ADR = PREP / "adr" / "ADR-2.2-DOC-CHARTER-001-charter-maturity-prep.md"

DOCS = {
    "package": PREP / "AS-2.2-DOC-CHARTER-PREP-001.md",
    "architecture": PREP / "ARCHITECTURE.md",
    "contract": PREP / "CONTRACT.md",
    "invariants": PREP / "INVARIANTS.md",
    "fixture_plan": PREP / "FIXTURE-PLAN.md",
    "matrix": PREP / "FEATURE-MATURITY-MATRIX.md",
    "adr": ADR,
}

LANDED_PREP_PACKAGES = (
    "AS-2.2-RET-HYBRID-001",
    "AS-2.2-CTX-COMPILER-001",
    "AS-2.2-MEM-GOV-001",
    "AS-2.2-KCI-ENGINE-PREP-001",
    "AS-2.2-DOD-COMPILER-001",
    "AS-2.2-TIME-MACHINE-001",
    "AS-2.2-REALITY-LIVE-001",
    "AS-2.2-REALITY-GAP-PREP-001",
    "AS-2.2-RESEARCH-001",
    "AS-2.2-CONFLICT-UX-PREP-001",
    "AS-2.2-XPROJ-CONTRACT-PREP-001",
    "AS-2.2-KF2-FABRIC-PREP-001",
    "AS-2.2-ASK2-DEEPEN-PREP-001",
    "AS-2.2-INTEL-SLICE-PREP-001",
    "AS-2.2-CHATGPT-LIVE-PREP-001",
    "AS-2.2-TEMPORAL-UX-PREP-001",
    "AS-2.2-COMPAT-PIN-PREP-001",
    "AS-2.2-ESTATE-OPS-PREP-001",
    "AS-2.2-PREP-FIXTURE-ROLLUP-001",
    "AS-2.2-ADV-POOL-001",
    "AS-2.2-ROADMAP-CROSSWALK-SYNC-001",
    "AS-2.2-RET-SEMIDX-PREP-001",
)


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def test_prep_docs_exist_without_readme() -> None:
    for path in DOCS.values():
        assert path.is_file(), path
        text = path.read_text(encoding="utf-8")
        assert "AS-2.2-DOC-CHARTER" in text
        assert "PREP" in text.upper() or "prep" in text
    assert not (PREP / "README.md").exists()
    assert not any(PREP.rglob("README.md"))


def test_charter_deepened() -> None:
    text = CHARTER.read_text(encoding="utf-8")
    assert "Non-goals" in text
    assert "Maturity vocabulary" in text
    assert "Package DAG" in text
    assert "ATLAS_2_1_RELEASE_CERTIFIED" in text
    assert "**NO**" in text
    assert "doc-charter" in text
    assert len(text.splitlines()) >= 80


def test_contract_stubs_exist_and_are_not_package_data() -> None:
    package_schemas = ROOT / "src" / "project_atlas" / "schemas"
    for filename in (
        "charter-maturity-row.schema.json",
        "charter-maturity-matrix.schema.json",
    ):
        stub = CONTRACTS / filename
        assert stub.is_file(), stub
        body = stub.read_text(encoding="utf-8")
        assert "PREP STUB" in body
        assert "charter" in body.lower()
        assert not (package_schemas / filename).exists()


def test_maturity_matrix_fixture_invariants() -> None:
    payload = _load_json(FIXTURES / "maturity-matrix.fixture.json")
    assert isinstance(payload, dict)
    assert payload["package_id"] == "AS-2.2-DOC-CHARTER-PREP-001"
    assert payload["pilot_roots"] == 0
    assert payload["authentic_estate_pilot_passed"] is False
    assert payload["atlas_2_1_release_certified"] is False
    assert payload["atlas_2_2_intelligence_unlocked"] is False
    assert payload["invariants"] == {
        "prep_ne_cert": True,
        "no_2_1_release_stamp": True,
        "no_2_2_unlock_stamp": True,
        "no_pilot_invent": True,
        "no_runtime_mutation": True,
    }
    assert payload["row_count"] == len(payload["rows"]) == 23
    package_ids = {row["package_id"] for row in payload["rows"]}
    assert set(LANDED_PREP_PACKAGES).issubset(package_ids)
    assert "AS-2.2-DOC-CHARTER-001" in package_ids
    row_schema = _load_json(CONTRACTS / "charter-maturity-row.schema.json")
    matrix_schema = _load_json(CONTRACTS / "charter-maturity-matrix.schema.json")
    assert isinstance(row_schema, dict)
    assert isinstance(matrix_schema, dict)
    for row in payload["rows"]:
        assert row["evidence_class"] == "fixture-only"
        assert row["authentic_estate"] is False
        assert row["invent_pilot_roots"] is False
        assert row["release_certified"] is False
        # Validate rows offline against the row stub (avoid remote $ref fetch).
        jsonschema.validate(instance=row, schema=row_schema)
    assert matrix_schema["properties"]["package_id"]["const"] == ("AS-2.2-DOC-CHARTER-PREP-001")
    assert matrix_schema["properties"]["atlas_2_1_release_certified"]["const"] is False


def test_package_card_non_claims() -> None:
    text = DOCS["package"].read_text(encoding="utf-8")
    assert "ATLAS_2_1_RELEASE_CERTIFIED" in text
    assert "**NO**" in text
    assert "ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED" in text
    assert "Production mutation" in text and "NONE" in text
    assert "docs/atlas-2.2/README.md" in text


def test_invariants_document_fail_closed_walls() -> None:
    text = DOCS["invariants"].read_text(encoding="utf-8")
    assert "PREP ≠ CERT" in text
    assert "NO PILOT INVENT" in text
    assert "NO RUNTIME MUTATION" in text
    assert "ATLAS_2_1_RELEASE_CERTIFIED = NO" in text
    assert "ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED" in text


def test_negative_fixtures_present() -> None:
    release = _load_json(FIXTURES / "negative-release-certified.expect.json")
    pilot = _load_json(FIXTURES / "negative-pilot-invent.expect.json")
    assert isinstance(release, dict)
    assert isinstance(pilot, dict)
    assert release["expected_error"] == "doc-charter-prep-release-certified-forbidden"
    assert pilot["expected_error"] == "doc-charter-prep-pilot-invent-forbidden"
    assert release["atlas_2_1_release_certified"] is False
    assert pilot["pilot_roots"] == 0


def test_matrix_markdown_lists_landed_prep_packages() -> None:
    text = DOCS["matrix"].read_text(encoding="utf-8")
    for package_id in LANDED_PREP_PACKAGES:
        assert package_id in text
    assert "AS-2.2-DOC-CHARTER-001" in text
    assert "PREP ONLY" in text


def test_no_runtime_mutation() -> None:
    """Capability-maturity-scoped 2.2 prep guard (D-INTEGRATE-007A).

    Keyed on docs/atlas-2.2/PACKAGE-MATURITY.json: 'doc-charter' must not
    mutate its production surface while prep-frozen; an implementation-
    unlocked capability may legitimately mutate its own surface.
    """
    assert_prep_branch_scope("doc-charter")
