"""ChatGPT capture and Knowledge Inbox writers must not leak raw OSError.

Reproduced on live main ``b87b4a22``: a file where ``generated/`` must be a
directory leaked ``NotADirectoryError`` past ``ChatgptCaptureError`` /
``KnowledgeInboxError``. Same class as AS-2.0-OBS-UX-WRITE-BOUNDARY (#811),
different writers -- not mixed into that PR.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.chatgpt_capture import (
    ChatgptCaptureError,
    build_chatgpt_capture_receipt,
)
from project_atlas.conversation_capture import (
    ConversationCaptureError,
    _write_atomic,
)
from project_atlas.knowledge_inbox import (
    KnowledgeInboxError,
    build_knowledge_inbox_receipt,
)


def _blocked_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "generated").write_text("not-a-directory", encoding="utf-8")
    return vault


def test_chatgpt_capture_blocked_parent_is_domain_error(tmp_path: Path) -> None:
    vault = _blocked_vault(tmp_path)
    with pytest.raises(ChatgptCaptureError, match="unwritable-capture-receipt:"):
        build_chatgpt_capture_receipt(vault, record_id="cap-1")


def test_knowledge_inbox_blocked_parent_is_domain_error(tmp_path: Path) -> None:
    vault = _blocked_vault(tmp_path)
    with pytest.raises(KnowledgeInboxError, match="unwritable-inbox-receipt:"):
        build_knowledge_inbox_receipt(vault, record_id="in-1")


def test_chatgpt_capture_happy_path_still_writes(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    report = build_chatgpt_capture_receipt(vault, record_id="cap-1", turn_count=2)
    assert report["live_api"] is False
    assert (vault / "generated" / "ops" / "chatgpt" / "cap-1.json").is_file()


def test_conversation_capture_write_atomic_blocked_parent_is_domain_error(
    tmp_path: Path,
) -> None:
    """Inbox is now contained; this pins the sibling capture writer."""
    vault = _blocked_vault(tmp_path)
    target = vault / "generated" / "ops" / "conversation-captures" / "x.json"
    with pytest.raises(ConversationCaptureError, match="unwritable-conversation-capture:"):
        _write_atomic(target, b"{}\n")


def test_knowledge_inbox_happy_path_still_writes(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    report = build_knowledge_inbox_receipt(vault, record_id="in-1", item_count=2)
    assert report["promoted_to_authority"] is False
    assert (vault / "generated" / "ops" / "inbox" / "in-1.json").is_file()
