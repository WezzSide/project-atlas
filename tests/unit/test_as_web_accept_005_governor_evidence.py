"""AS-WEB-ACCEPT-005 governor evidence guards (acceptance APPROVED under owner gates)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAIN = "8ee65b91871bc04039ffe401a9da3743e4800a8b"
TREE = "a2e592a797056935fbec0d8c54033aa3c25a5b06"


def test_as_web_accept_005_evidence_is_pinned_and_accepted() -> None:
    evidence = ROOT / "docs" / "AS-WEB-ACCEPT-005-governor-evidence.md"
    assert evidence.is_file()
    text = evidence.read_text(encoding="utf-8")
    assert MAIN in text
    assert TREE in text
    assert "WEB APPLICATION ACCEPTED = **YES**" in text
    assert "Governor decision = **APPROVED**" in text


def test_as_web_accept_005_governor_decision_is_approved() -> None:
    signoff = (ROOT / "docs" / "AS-WEB-ACCEPT-GOVERNOR-SIGNOFF.md").read_text(encoding="utf-8")
    assert MAIN in signoff
    assert TREE in signoff
    assert "Governor decision | **APPROVED**" in signoff
    governor_checks = [line for line in signoff.splitlines() if line.startswith("- [")]
    assert governor_checks, "governor checklist must be present"
    assert all(line.startswith("- [x]") for line in governor_checks)
    assert "WEB APPLICATION ACCEPTED** | **YES**" in signoff


def test_as_web_accept_005_checklist_pin_and_item_10_closed() -> None:
    checklist = (ROOT / "docs" / "AS-WEB-ACCEPT-001-checklist.md").read_text(encoding="utf-8")
    assert MAIN in checklist
    assert TREE in checklist
    assert "| 10 | Governor sign-off artifact + tip pin recorded" in checklist
    assert "closed — APPROVED" in checklist
    assert "**WEB APPLICATION ACCEPTED** | **YES**" in checklist
