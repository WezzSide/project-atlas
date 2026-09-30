"""AT3-042 — Cross-LLM conflict detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.memory.conflicts import (
    PACKAGE_ID,
    conflict_capability,
    detect_conflicts,
)


def test_capability_does_not_pick_winner() -> None:
    cap = conflict_capability()
    assert cap["package"] == PACKAGE_ID
    assert cap["collapsed_to_scalar"] is False
    assert cap["picks_winner"] is False
    assert cap["collapses_state_intent_history"] is False


def test_pg15_vs_pg16_stays_conflicted() -> None:
    report = detect_conflicts(
        [
            {"text": "production uses PostgreSQL 15", "provider": "chatgpt"},
            {"text": "we use PostgreSQL 16", "provider": "claude"},
            {"text": "migrate to PostgreSQL 16 later", "provider": "gemini"},
        ],
        current_state_text="repository pins PostgreSQL 15",
    )
    assert report["conflicted_history"] is True
    assert report["collapsed_to_scalar"] is False
    assert report["winner"] is None
    assert report["current_state"] == "15"
    assert "15" in report["observed_versions"]
    assert "16" in report["observed_versions"]
    assert report["intent_versions"]


def test_mixed_valid_and_corrupt_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        detect_conflicts([{"text": "PostgreSQL 15"}, "corrupt"])  # type: ignore[list-item]
    assert exc.value.code == "CONFLICT_INVALID"


def test_module_does_not_touch_2x_bridges() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/memory/conflicts.py").read_text(encoding="utf-8")
    for name in (
        "from project_atlas.chatgpt_bridge",
        "from project_atlas.knowledge_compiler",
        "write_text(",
    ):
        assert name not in source
