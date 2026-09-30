"""AS-WEB-ACCEPT-003 — governor sign-off pack presence tests (ACCEPTED=YES)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_as_web_accept_003_governor_signoff_approved() -> None:
    path = ROOT / "docs" / "AS-WEB-ACCEPT-GOVERNOR-SIGNOFF.md"
    text = path.read_text(encoding="utf-8")
    assert "WEB APPLICATION ACCEPTED** | **YES**" in text
    assert "APPROVED" in text
    assert "GOVERNOR:" in text


def test_as_web_accept_003_readme_documents_smoke() -> None:
    readme = (ROOT / "apps" / "web" / "README.md").read_text(encoding="utf-8")
    assert "scripts/smoke.mjs" in readme
    assert "WEB APPLICATION ACCEPTED = YES" in readme
    assert "AS-WEB-ACCEPT-GOVERNOR-SIGNOFF.md" in readme


def test_as_web_accept_003_checklist_item_11_documented() -> None:
    checklist = (ROOT / "docs" / "AS-WEB-ACCEPT-001-checklist.md").read_text(encoding="utf-8")
    assert "documented" in checklist
    assert "**WEB APPLICATION ACCEPTED** | **YES**" in checklist
