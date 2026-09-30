"""AT3-044 — Memory freshness / invalidation."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.memory.freshness import (
    PACKAGE_ID,
    apply_freshness,
    classify_freshness,
    freshness_capability,
)


def test_capability_keeps_unknown() -> None:
    cap = freshness_capability()
    assert cap["package"] == PACKAGE_ID
    assert cap["stale_is_not_current"] is True
    assert cap["unknown_stays_unknown"] is True
    assert cap["historical_memory_is_not_current_truth"] is True


def test_no_evidence_stays_unknown() -> None:
    state = classify_freshness({"text": "we discussed the datastore"})
    assert state == "UNKNOWN"


def test_contradicting_evidence_is_stale() -> None:
    state = classify_freshness(
        {"text": "production uses PostgreSQL 16"},
        stronger_evidence=[
            {"kind": "repository", "text": "pins PostgreSQL 15"},
        ],
    )
    assert state == "STALE"
    assert state != "CURRENT"


def test_matching_evidence_is_current() -> None:
    state = classify_freshness(
        {"text": "production uses PostgreSQL 15"},
        stronger_evidence=[
            {"kind": "current_state", "text": "PostgreSQL 15"},
        ],
    )
    assert state == "CURRENT"


def test_mixed_valid_and_corrupt_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        apply_freshness([{"text": "ok"}, "corrupt"])  # type: ignore[list-item]
    assert exc.value.code == "FRESHNESS_INVALID"


def test_module_does_not_touch_2x_bridges() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/memory/freshness.py").read_text(encoding="utf-8")
    for name in (
        "from project_atlas.chatgpt_bridge",
        "from project_atlas.knowledge_compiler",
        "write_text(",
    ):
        assert name not in source
