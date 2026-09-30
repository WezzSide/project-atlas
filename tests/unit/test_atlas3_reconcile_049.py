"""AT3-049 — Cross-LLM memory reconciliation."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.memory.reconcile import (
    PACKAGE_ID,
    reconcile_capability,
    reconcile_memories,
)


def test_capability_never_auto_promotes() -> None:
    cap = reconcile_capability()
    assert cap["package"] == PACKAGE_ID
    assert cap["auto_promote_to_truth_core"] is False
    assert cap["promoted_to_truth_core"] == 0
    assert cap["picks_winner"] is False
    assert cap["composes"] == ["AT3-041", "AT3-042", "AT3-044"]


def test_reconcile_keeps_conflict_and_does_not_promote() -> None:
    report = reconcile_memories(
        [
            {
                "text": "production uses PostgreSQL 16",
                "provider": "chatgpt",
                "item_type": "claim_candidate",
            },
            {
                "text": "production uses PostgreSQL 15",
                "provider": "claude",
                "item_type": "claim_candidate",
            },
            {
                "text": "migrate to PostgreSQL 16 later",
                "provider": "gemini",
                "item_type": "claim_candidate",
            },
        ],
        stronger_evidence=[{"kind": "repository", "text": "pins PostgreSQL 15"}],
        current_state_text="repository pins PostgreSQL 15",
    )
    assert report["package"] == PACKAGE_ID
    assert report["promoted_to_truth_core"] == 0
    assert report["conflicted_history"] is True
    assert report["conflicts"]["winner"] is None
    assert report["stale_chatgpt_memory"] is True
    assert report["original_provenance_erased"] is False


def test_mixed_valid_and_corrupt_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        reconcile_memories([{"text": "ok"}, "corrupt"])  # type: ignore[list-item]
    assert exc.value.code in {"RECONCILE_INVALID", "DEDUP_INVALID"}


def test_module_does_not_touch_2x_bridges() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/memory/reconcile.py").read_text(encoding="utf-8")
    for name in (
        "from project_atlas.chatgpt_bridge",
        "from project_atlas.knowledge_compiler",
        "write_text(",
    ):
        assert name not in source
