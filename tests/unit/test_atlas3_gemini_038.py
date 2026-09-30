"""AT3-038 — Gemini fixture ingest (no history API)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from project_atlas.atlas3.cli import dispatch_atlas3, register_atlas3_parsers
from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.memory.gemini import PACKAGE_ID, gemini_capability, import_gemini_export
from project_atlas.atlas3.memory.pipeline import gemini_export_to_items
from project_atlas.atlas3.memory.providers import memory_providers


def test_capability_does_not_claim_history_api() -> None:
    cap = gemini_capability()
    assert cap["package"] == PACKAGE_ID
    assert cap["live_full_history_sync"] is False
    assert cap["conversation_sync"] == "NOT_IMPLEMENTED"
    assert cap["bootstrap_is_ingestion"] is False
    assert cap["native_history_api"] is False
    assert cap["export_import"] == "IMPLEMENTED"
    matrix = memory_providers()
    assert matrix["gemini_detail"]["conversation_sync"] == "NOT_IMPLEMENTED"
    assert matrix["gemini_current"]["export_import"] == "IMPLEMENTED"
    assert matrix["gemini_current"]["bootstrap_is_ingestion"] is False


def test_import_messages_fixture(tmp_path: Path) -> None:
    path = tmp_path / "gemini.json"
    path.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "which datastore?"},
                    {"role": "assistant", "content": "research says postgres 16 later"},
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )
    envelopes = import_gemini_export(path, conversation_id="c-gemini", project_id="harbor-api")
    assert len(envelopes) == 2
    assert envelopes[0]["provider"] == "gemini"
    assert envelopes[0]["import_mode"] == "EXPORT"
    assert envelopes[0]["project_id"] == "harbor-api"
    items = gemini_export_to_items(path, conversation_id="c-gemini", project_id="harbor-api")
    assert items
    assert all(item.get("promoted_to_truth_core") is not True for item in items)


def test_import_turns_list_and_inline_json() -> None:
    payload = json.dumps(
        {"turns": [{"role": "human", "text": "status?"}, {"role": "model", "text": "unknown"}]}
    )
    envelopes = import_gemini_export(payload, conversation_id="inline-1")
    assert len(envelopes) == 2
    assert envelopes[0]["role"] == "user"
    listed = import_gemini_export(
        json.dumps([{"role": "assistant", "content": "fixture only"}]),
        conversation_id="inline-2",
    )
    assert listed[0]["provider"] == "gemini"
    assert listed[0]["import_mode"] == "EXPORT"


def test_history_api_claim_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "claimed.json"
    path.write_text(
        json.dumps({"live_full_history_sync": True, "messages": []}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(Atlas3Error) as exc:
        import_gemini_export(path, conversation_id="c1", project_id="harbor-api")
    assert exc.value.code == "GEMINI_HISTORY_API_CLAIMED"


def test_mixed_valid_and_corrupt_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "mixed.json"
    path.write_text(
        json.dumps({"messages": [{"role": "user", "content": "ok"}, "corrupt"]}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(Atlas3Error) as exc:
        import_gemini_export(path, conversation_id="c1", project_id="harbor-api")
    assert exc.value.code == "GEMINI_EXPORT_INVALID"


def test_gemini_md_is_not_ingestion(tmp_path: Path) -> None:
    path = tmp_path / "GEMINI.md"
    path.write_text("# bootstrap only\n", encoding="utf-8")
    with pytest.raises(Atlas3Error) as exc:
        import_gemini_export(path, conversation_id="c1")
    assert exc.value.code == "GEMINI_MD_IS_NOT_INGESTION"


def test_missing_export_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(Atlas3Error) as exc:
        import_gemini_export(tmp_path / "absent.json", conversation_id="c1")
    assert exc.value.code == "EXPORT_NOT_FOUND"


def test_invalid_json_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(Atlas3Error) as exc:
        import_gemini_export(path, conversation_id="c1")
    assert exc.value.code == "GEMINI_EXPORT_INVALID"


def test_cli_gemini_export(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "gemini.json"
    path.write_text(
        json.dumps({"messages": [{"role": "user", "content": "ping"}]}) + "\n",
        encoding="utf-8",
    )
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_atlas3_parsers(sub)
    args = parser.parse_args(
        [
            "memory",
            "gemini",
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
    assert payload["envelope_count"] == 1
    assert payload["conversation_sync"] == "NOT_IMPLEMENTED"
    assert payload["promoted_to_truth_core"] == 0
    assert all(ord(char) < 128 for char in rendered)
    assert not (tmp_path / "projects").exists()


def test_cli_help_is_ascii(capsys: pytest.CaptureFixture[str]) -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_atlas3_parsers(sub)
    with pytest.raises(SystemExit) as info:
        parser.parse_args(["memory", "gemini", "--help"])
    assert info.value.code == 0
    help_text = capsys.readouterr().out
    assert "Gemini JSON export" in help_text
    assert all(ord(char) < 128 for char in help_text)


def test_module_does_not_touch_2x_bridges() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/memory/gemini.py").read_text(encoding="utf-8")
    for name in (
        "chatgpt_bridge",
        "chatgpt_capture",
        "knowledge_compiler",
        "from project_atlas.ingestion",
        "write_text(",
    ):
        assert name not in source
