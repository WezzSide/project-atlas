"""AT3-036 — ChatGPT export ingest (no history API)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from project_atlas.atlas3.cli import dispatch_atlas3, register_atlas3_parsers
from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.memory.chatgpt import (
    PACKAGE_ID,
    chatgpt_capability,
    import_chatgpt_export,
)
from project_atlas.atlas3.memory.pipeline import chatgpt_export_to_items
from project_atlas.atlas3.memory.providers import memory_providers


def test_capability_does_not_claim_history_api() -> None:
    cap = chatgpt_capability()
    assert cap["package"] == PACKAGE_ID
    assert cap["live_full_history_sync"] is False
    assert cap["conversation_sync"] == "NOT_IMPLEMENTED"
    assert cap["replaces_chatgpt_bridge"] is False
    assert cap["uses_parse_chat_export"] is True
    assert cap["native_history_api"] is False
    assert cap["export_import"] == "IMPLEMENTED"
    matrix = memory_providers()
    assert matrix["chatgpt_detail"]["conversation_sync"] == "NOT_IMPLEMENTED"
    assert matrix["chatgpt_current"]["conversation_sync"] == "NOT_IMPLEMENTED"
    assert matrix["chatgpt_current"]["export_import"] == "IMPLEMENTED"
    assert matrix["chatgpt_current"]["replaces_chatgpt_bridge"] is False
    assert matrix["chatgpt_current"]["state"] == "EXPORT_ONLY"


def test_import_markdown_export(tmp_path: Path) -> None:
    path = tmp_path / "chat.md"
    path.write_text(
        "User: which datastore?\nAssistant: I would use postgres 16\n",
        encoding="utf-8",
    )
    envelopes = import_chatgpt_export(path, conversation_id="c-gpt", project_id="harbor-api")
    assert len(envelopes) == 2
    assert envelopes[0]["provider"] == "chatgpt"
    assert envelopes[0]["import_mode"] == "EXPORT"
    assert envelopes[0]["project_id"] == "harbor-api"
    assert envelopes[0]["raw_transcript_persisted"] is False
    items = chatgpt_export_to_items(path, conversation_id="c-gpt", project_id="harbor-api")
    assert items
    assert all(item.get("promoted_to_truth_core") is not True for item in items)


def test_import_json_turns_and_inline() -> None:
    payload = json.dumps(
        {
            "messages": [
                {"role": "user", "text": "status?"},
                {"role": "assistant", "text": "unknown"},
            ]
        }
    )
    envelopes = import_chatgpt_export(payload, conversation_id="inline-1")
    assert len(envelopes) == 2
    assert envelopes[0]["role"] == "user"
    listed = import_chatgpt_export(
        json.dumps([{"role": "assistant", "content": "fixture only"}]),
        conversation_id="inline-2",
    )
    assert listed[0]["provider"] == "chatgpt"
    assert listed[0]["import_mode"] == "EXPORT"


def test_history_api_claim_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "claimed.json"
    path.write_text(
        json.dumps({"live_full_history_sync": True, "messages": []}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(Atlas3Error) as exc:
        import_chatgpt_export(path, conversation_id="c1", project_id="harbor-api")
    assert exc.value.code == "CHATGPT_HISTORY_API_CLAIMED"


def test_mixed_valid_and_corrupt_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "mixed.json"
    path.write_text(
        json.dumps({"messages": [{"role": "user", "content": "ok"}, "corrupt"]}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(Atlas3Error) as exc:
        import_chatgpt_export(path, conversation_id="c1", project_id="harbor-api")
    assert exc.value.code == "CHATGPT_EXPORT_INVALID"


def test_missing_export_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(Atlas3Error) as exc:
        import_chatgpt_export(tmp_path / "absent.json", conversation_id="c1")
    assert exc.value.code == "EXPORT_NOT_FOUND"


def test_invalid_json_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(Atlas3Error) as exc:
        import_chatgpt_export(path, conversation_id="c1")
    assert exc.value.code == "CHATGPT_EXPORT_INVALID"


def test_cli_chatgpt_export(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "chat.md"
    path.write_text("User: ping\nAssistant: pong\n", encoding="utf-8")
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_atlas3_parsers(sub)
    args = parser.parse_args(
        [
            "memory",
            "chatgpt",
            "--export",
            str(path),
            "--conversation-id",
            "c-cli",
            "--project",
            "harbor-api",
        ]
    )
    assert dispatch_atlas3(args) == 0
    rendered = capsys.readouterr().out
    payload = json.loads(rendered)
    assert payload["package"] == PACKAGE_ID
    assert payload["envelope_count"] == 2
    assert payload["conversation_sync"] == "NOT_IMPLEMENTED"
    assert payload["replaces_chatgpt_bridge"] is False
    assert payload["promoted_to_truth_core"] == 0
    assert all(ord(char) < 128 for char in rendered)
    assert not (tmp_path / "projects").exists()


def test_cli_help_is_ascii(capsys: pytest.CaptureFixture[str]) -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_atlas3_parsers(sub)
    with pytest.raises(SystemExit) as info:
        parser.parse_args(["memory", "chatgpt", "--help"])
    assert info.value.code == 0
    help_text = capsys.readouterr().out
    assert "ChatGPT export path" in help_text
    assert all(ord(char) < 128 for char in help_text)


def test_module_does_not_touch_2x_bridges() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/memory/chatgpt.py").read_text(encoding="utf-8")
    for name in (
        "from project_atlas.chatgpt_bridge",
        "from project_atlas.chatgpt_capture",
        "knowledge_compiler",
        "from project_atlas.ingestion",
        "write_text(",
    ):
        assert name not in source
