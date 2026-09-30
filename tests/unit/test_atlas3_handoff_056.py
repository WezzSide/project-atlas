"""AT3-056 — fixture-level provider handoff."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.memory.handoff import (
    PACKAGE_ID,
    fixture_provider_handoff,
    handoff_capability,
)

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "atlas3"
    / "llm-memory"
    / "postgres-cross-llm.json"
)


def test_capability_keeps_live_multi_account_blocked() -> None:
    cap = handoff_capability()
    assert cap["package"] == PACKAGE_ID
    assert cap["fixture_handoff"] == "IMPLEMENTED"
    assert cap["live_multi_account_product"] == "EXTERNAL_BLOCKED"
    assert cap["new_cli_command"] is False
    assert cap["merge_authorization"] == "NOT_GRANTED"


def test_chatgpt_fixture_serves_to_claude() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    chatgpt = next(conv for conv in payload["conversations"] if conv["provider"] == "chatgpt")
    report = fixture_provider_handoff(
        chatgpt["turns"],
        source_provider="chatgpt",
        target_provider="claude",
        conversation_id=chatgpt["conversation_id"],
        project_id=payload["project_id"],
        freshness_requirement="CURRENT",
    )
    assert report["package_id"] == PACKAGE_ID
    assert report["source_provider"] == "chatgpt"
    assert report["target_provider"] == "claude"
    assert report["item_count"] >= 1
    assert report["live_handoff_used"] is False
    assert report["write_applied"] is False
    served = report["served"]
    assert served["target_provider"] == "claude"
    assert served["live_provider_serve"] == "EXTERNAL_BLOCKED"


def test_same_provider_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        fixture_provider_handoff(
            [{"role": "assistant", "text": "PostgreSQL 15"}],
            source_provider="chatgpt",
            target_provider="chatgpt",
            conversation_id="c1",
            project_id="harbor-api",
        )
    assert exc.value.code == "HANDOFF_SAME_PROVIDER"


def test_live_claim_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        fixture_provider_handoff(
            [{"role": "assistant", "text": "x", "live_full_history_sync": True}],
            source_provider="chatgpt",
            target_provider="gemini",
            conversation_id="c1",
            project_id="harbor-api",
        )
    assert exc.value.code == "HANDOFF_LIVE_CLAIMED"


def test_cross_project_turn_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        fixture_provider_handoff(
            [{"role": "assistant", "text": "x", "project_id": "other-api"}],
            source_provider="chatgpt",
            target_provider="claude",
            conversation_id="c1",
            project_id="harbor-api",
        )
    assert exc.value.code == "PROJECT_MISMATCH"


def test_module_does_not_touch_certified_surfaces() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/memory/handoff.py").read_text(encoding="utf-8")
    for name in (
        "from project_atlas.runtime_22",
        "from project_atlas.chatgpt_bridge",
        "from project_atlas.knowledge_compiler",
        "from project_atlas.ask2",
        "write_json_atomic",
    ):
        assert name not in source
