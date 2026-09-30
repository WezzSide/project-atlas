"""AS-2.2-REALITY-GAP-PREP-001 — docs/fixtures/ADR presence + invariants."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREP = ROOT / "docs" / "atlas-2.2" / "reality-gap"
FIXTURES = PREP / "fixtures"
CONTRACTS = PREP / "contracts"
ADR = ROOT / "docs" / "adr" / "ADR-028-reality-gap-prep.md"

CANONICAL_GAP_IDS = (
    "estate-twin",
    "agent-os-in-core",
    "federation",
    "advanced-ux",
    "production-sync",
    "provider-mcp",
)


def test_reality_gap_prep_docs_present() -> None:
    required = [
        "AS-2.2-REALITY-GAP-PREP-001.md",
        "README.md",
        "ARCHITECTURE.md",
        "CONTRACT.md",
        "INVARIANTS.md",
        "FIXTURE-PLAN.md",
    ]
    for name in required:
        assert (PREP / name).is_file(), name
    assert ADR.is_file()
    assert (CONTRACTS / "reality-gap-prep-inventory.schema.json").is_file()
    assert (CONTRACTS / "reality-gap-prep-scenario.schema.json").is_file()


def test_reality_gap_prep_invariants_documented() -> None:
    text = (PREP / "INVARIANTS.md").read_text(encoding="utf-8")
    assert "unknown ≠ healthy" in text or "unknown≠healthy" in text
    assert "UI ≠ canonical" in text or "UI≠canonical" in text
    assert "no PILOT invent" in text or "PILOT invent" in text
    assert "ATLAS_2_1_RELEASE_CERTIFIED = NO" in text


def test_reality_gap_prep_package_card_non_claims() -> None:
    text = (PREP / "AS-2.2-REALITY-GAP-PREP-001.md").read_text(encoding="utf-8")
    assert "ATLAS_2_1_RELEASE_CERTIFIED` | **NO**" in text or (
        "ATLAS_2_1_RELEASE_CERTIFIED" in text and "**NO**" in text
    )
    assert "ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED" in text
    assert "reality_gap" in text
    assert (
        "do not mutate" in text.lower()
        or "Do not mutate" in text
        or ("NONE" in text and "Production mutation" in text)
    )


def test_reality_gap_prep_inventory_fixture_invariants() -> None:
    payload = json.loads((FIXTURES / "inventory.fixture.json").read_text(encoding="utf-8"))
    assert payload["package_id"] == "AS-2.2-REALITY-GAP-PREP-001"
    assert payload["pilot_roots"] == 0
    assert payload["authentic_estate_pilot_passed"] is False
    assert payload["atlas_2_1_release_certified"] is False
    assert payload["invariants"] == {
        "unknown_ne_healthy": True,
        "ui_ne_canonical": True,
        "no_pilot_invent": True,
    }
    assert payload["scenario_count"] == len(payload["scenarios"]) == 6
    gap_ids = {row["gap_id"] for row in payload["scenarios"]}
    assert gap_ids == set(CANONICAL_GAP_IDS)
    for row in payload["scenarios"]:
        assert row["evidence_class"] == "fixture-only"
        assert row["authentic_estate"] is False
        assert row["invent_pilot_roots"] is False
        assert row["healthy"] is False


def test_reality_gap_prep_negative_fixtures_present() -> None:
    unknown = json.loads(
        (FIXTURES / "negative-unknown-as-healthy.fixture.json").read_text(encoding="utf-8")
    )
    ui = json.loads((FIXTURES / "negative-ui-canonical.fixture.json").read_text(encoding="utf-8"))
    pilot = json.loads(
        (FIXTURES / "negative-pilot-invent.fixture.json").read_text(encoding="utf-8")
    )
    assert unknown["expected_error"] == ("reality-gap-prep-unknown-as-healthy-forbidden")
    assert ui["expected_error"] == "reality-gap-prep-ui-canonical-writes-forbidden"
    assert pilot["expected_error"] == "reality-gap-prep-pilot-invent-forbidden"
    assert ui["forbidden_catalog"]["canonical_writes"] is True
    assert pilot["forbidden_inventory"]["invent_pilot_roots"] is True


def test_reality_gap_prep_does_not_claim_release_certified_in_adr() -> None:
    text = ADR.read_text(encoding="utf-8")
    assert "ATLAS_2_1_RELEASE_CERTIFIED = NO" in text
    assert "unknown≠healthy" in text or "unknown ≠ healthy" in text
    assert "UI≠canonical" in text or "UI ≠ canonical" in text
    assert "no PILOT invent" in text
    assert "do **not** mutate" in text.lower() or "Do **not** mutate" in text
