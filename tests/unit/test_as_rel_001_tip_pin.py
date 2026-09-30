"""AS-REL-001 tip pin guards — freeze tip + RELEASE CERTIFIED = YES."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = ROOT / "docs" / "releases" / "1.0.0"
MAIN = "f4079813025dd882e0e3608ab7ad5b3b17f95bd9"
TREE = "feb0441a13e391812ae07a1a8eb27b0de1061469"
DOCUMENTS = (
    "README.md",
    "CHECKLIST.md",
    "EVIDENCE-INDEX.md",
    "RECEIPT.md",
    "RECEIPT-TEMPLATE.md",
    "RELEASE-NOTES.md",
    "COMPATIBILITY-SNAPSHOT.md",
)


def test_release_documents_pin_freeze_tip_and_certified_yes() -> None:
    for name in DOCUMENTS:
        text = (RELEASE_DIR / name).read_text(encoding="utf-8")
        assert MAIN in text, f"{name} does not pin freeze MAIN"
        assert TREE in text, f"{name} does not pin freeze TREE"
        assert "RELEASE CERTIFIED = YES" in text
        assert "RELEASE CERTIFIED = NO" not in text


def test_release_owner_gate_state_and_fixture_pilot_waiver() -> None:
    combined = "\n".join((RELEASE_DIR / name).read_text(encoding="utf-8") for name in DOCUMENTS)
    assert "WEB APPLICATION ACCEPTED = YES" in combined
    assert "FIXTURE-ONLY CERT UNDER OWNER WAIVER = YES" in combined
    assert "Authentic estate PILOT = NO" in combined
    assert "FIXTURE_ONLY_OWNER_WAIVER" in combined
    assert "D-PROJECT-ATLAS-1.0-OWNER-GATES-PARALLEL-CLOSEOUT-001" in combined
    assert "ATLAS_1_0_RELEASE_CERTIFIED" in combined
    assert (RELEASE_DIR / "RECEIPT.md").is_file()
