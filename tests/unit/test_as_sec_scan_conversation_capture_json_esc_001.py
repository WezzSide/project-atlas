"""AS-SEC-SCAN-CONVERSATION-CAPTURE-JSON-ESC-001 — decoded capture text must not persist."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.conversation_capture import (
    CAPTURE_DIR,
    ConversationCaptureError,
    capture_conversation,
    set_conversation_review_state,
)
from project_atlas.secrets import scan_text

TOKEN = "AKIAAAAAAAAAAAAAAAAA"
ESC = r"\u0041KIAAAAAAAAAAAAAAAAA"


def test_json_unicode_escape_review_rewrite_does_not_persist(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    receipt = capture_conversation(
        vault,
        {
            "schema": "atlas.conversation-capture.v1",
            "schema_version": 1,
            "project_id": "harbor-api",
            "source_provider": "cursor",
            "source_conversation_id": "conv-1",
            "source_message_refs": ["msg-1"],
            "capture_mode": "structured_submission",
            "summary": "safe standup",
            "capture_items": [{"item_type": "observation", "text": "safe note"}],
        },
    )
    cid = str(receipt["capture_id"])
    path = vault / CAPTURE_DIR / f"{cid}.json"
    raw = path.read_text(encoding="utf-8").replace('"safe standup"', f'"{ESC}"')
    path.write_text(raw, encoding="utf-8")
    assert TOKEN not in raw
    assert scan_text(raw) == []
    assert json.loads(raw)["summary"] == TOKEN
    with pytest.raises(ConversationCaptureError) as exc:
        set_conversation_review_state(vault, cid, "reviewed")
    assert exc.value.code == "SECRET_CONTENT"
    written = path.read_text(encoding="utf-8")
    assert TOKEN not in written
    assert scan_text(written) == []
