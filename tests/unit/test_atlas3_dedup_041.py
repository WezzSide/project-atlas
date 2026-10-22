"""AT3-041 — Cross-LLM deduplication."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.memory.dedup import (
    PACKAGE_ID,
    dedup_capability,
    deduplicate_items,
)


def test_capability_preserves_provenance() -> None:
    cap = dedup_capability()
    assert cap["package"] == PACKAGE_ID
    assert cap["original_provenance_erased"] is False
    assert cap["auto_promote_to_truth_core"] is False
    assert cap["collapses_state_intent_history"] is False


def test_exact_duplicates_keep_all_sources() -> None:
    report = deduplicate_items(
        [
            {
                "text": "uses PostgreSQL 16",
                "provider": "chatgpt",
                "conversation_id": "c1",
                "message_id": "m1",
                "source_content_hash": "sha256:a",
            },
            {
                "text": "Uses postgresql 16",
                "provider": "claude",
                "conversation_id": "c2",
                "message_id": "m2",
                "source_content_hash": "sha256:b",
            },
        ]
    )
    assert report["package"] == PACKAGE_ID
    assert report["input_count"] == 2
    assert report["collapsed_count"] == 1
    assert report["duplicates_collapsed"] == 1
    assert report["original_provenance_erased"] is False
    sources = report["items"][0]["evidence_sources"]
    assert {row["provider"] for row in sources} == {"chatgpt", "claude"}


def test_mixed_valid_and_corrupt_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        deduplicate_items([{"text": "ok"}, "corrupt"])  # type: ignore[list-item]
    assert exc.value.code == "DEDUP_INVALID"


def test_non_list_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        deduplicate_items({"text": "ok"})  # type: ignore[arg-type]
    assert exc.value.code == "DEDUP_INVALID"


def test_mixed_project_duplicates_fail_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        deduplicate_items(
            [
                {
                    "text": "production uses postgresql 15",
                    "project_id": "harbor-api",
                    "provider": "chatgpt",
                    "conversation_id": "c1",
                    "message_id": "m1",
                    "source_content_hash": "sha256:a",
                },
                {
                    "text": "production uses PostgreSQL 15",
                    "project_id": "other-api",
                    "provider": "claude",
                    "conversation_id": "c2",
                    "message_id": "m2",
                    "source_content_hash": "sha256:b",
                },
            ]
        )
    assert exc.value.code == "PROJECT_MISMATCH"


def test_same_project_duplicates_still_collapse() -> None:
    report = deduplicate_items(
        [
            {
                "text": "uses PostgreSQL 16",
                "project_id": "harbor-api",
                "provider": "chatgpt",
                "conversation_id": "c1",
                "message_id": "m1",
                "source_content_hash": "sha256:a",
            },
            {
                "text": "Uses postgresql 16",
                "project_id": "harbor-api",
                "provider": "claude",
                "conversation_id": "c2",
                "message_id": "m2",
                "source_content_hash": "sha256:b",
            },
        ]
    )
    assert report["collapsed_count"] == 1
    assert report["items"][0]["project_id"] == "harbor-api"


def test_module_does_not_touch_2x_bridges() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/memory/dedup.py").read_text(encoding="utf-8")
    for name in (
        "from project_atlas.chatgpt_bridge",
        "from project_atlas.knowledge_compiler",
        "write_text(",
    ):
        assert name not in source
