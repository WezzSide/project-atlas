"""Doc presence gates for ADV/SEC fixture matrices — no certification claims."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADV = ROOT / "docs" / "AS-ADV-RELEASE-MATRIX.md"
SEC = ROOT / "docs" / "AS-SEC-CONT-MATRIX.md"


def test_adv_matrix_exists_and_release_no() -> None:
    text = ADV.read_text(encoding="utf-8")
    assert ADV.is_file()
    assert "RELEASE CERTIFIED" in text
    assert "**NO**" in text
    assert "RELEASE CERTIFIED** | **YES" not in text


def test_sec_matrix_exists_and_pilot_release_no() -> None:
    text = SEC.read_text(encoding="utf-8")
    assert SEC.is_file()
    assert "PILOT" in text
    assert "**NO**" in text
    assert "ESTATE PILOT PASSED** | **YES" not in text
