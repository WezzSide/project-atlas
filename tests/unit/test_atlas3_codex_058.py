"""AT3-058 — Codex fixture / structured-submission ingest (no history API)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from project_atlas.atlas3.cli import dispatch_atlas3, register_atlas3_parsers
from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.memory.codex import PACKAGE_ID, codex_capability, import_codex_export
from project_atlas.atlas3.memory.pipeline import codex_export_to_items
from project_atlas.atlas3.memory.providers import memory_providers


def test_capability_does_not_claim_history_api() -> None:
    cap = codex_capability()
    assert cap["package"] == PACKAGE_ID
    assert cap["live_full_history_sync"] is False
    assert cap["conversation_sync"] == "NOT_IMPLEMENTED"
    assert cap["bootstrap_is_ingestion"] is False
    assert cap["native_history_api"] is False
    assert cap["export_import"] == "IMPLEMENTED"
    assert cap["import_mode"] == "STRUCTURED_SUBMISSION"
    assert cap["merge_authorization"] == "NOT_GRANTED"
    matrix = memory_providers()
    assert matrix["codex_detail"]["conversation_sync"] == "NOT_IMPLEMENTED"
    assert matrix["codex_current"]["export_import"] == "IMPLEMENTED"
    assert matrix["codex_current"]["bootstrap_is_ingestion"] is False


def test_import_messages_fixture(tmp_path: Path) -> None:
    path = tmp_path / "codex.json"
    path.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "which datastore?"},
                    {"role": "assistant", "content": "I would use postgres 16"},
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )
    envelopes = import_codex_export(path, conversation_id="c-codex", project_id="harbor-api")
    assert len(envelopes) == 2
    assert envelopes[0]["provider"] == "codex"
    assert envelopes[0]["import_mode"] == "STRUCTURED_SUBMISSION"
    assert envelopes[0]["project_id"] == "harbor-api"
    items = codex_export_to_items(path, conversation_id="c-codex", project_id="harbor-api")
    assert items
    assert all(item.get("promoted_to_truth_core") is not True for item in items)


def test_import_turns_list_and_inline_json() -> None:
    payload = json.dumps(
        {"turns": [{"role": "human", "text": "status?"}, {"role": "ai", "text": "unknown"}]}
    )
    envelopes = import_codex_export(payload, conversation_id="inline-1")
    assert len(envelopes) == 2
    assert envelopes[0]["role"] == "user"
    listed = import_codex_export(
        json.dumps([{"role": "assistant", "content": "fixture only"}]),
        conversation_id="inline-2",
    )
    assert listed[0]["provider"] == "codex"
    assert listed[0]["import_mode"] == "STRUCTURED_SUBMISSION"


def test_history_api_claim_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "claimed.json"
    path.write_text(
        json.dumps({"live_full_history_sync": True, "messages": []}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(Atlas3Error) as exc:
        import_codex_export(path, conversation_id="c1", project_id="harbor-api")
    assert exc.value.code == "CODEX_HISTORY_API_CLAIMED"


def test_mixed_valid_and_corrupt_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "mixed.json"
    path.write_text(
        json.dumps({"messages": [{"role": "user", "content": "ok"}, "corrupt"]}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(Atlas3Error) as exc:
        import_codex_export(path, conversation_id="c1", project_id="harbor-api")
    assert exc.value.code == "CODEX_EXPORT_INVALID"


def test_codex_md_is_not_ingestion(tmp_path: Path) -> None:
    path = tmp_path / "CODEX.md"
    path.write_text("# bootstrap only\n", encoding="utf-8")
    with pytest.raises(Atlas3Error) as exc:
        import_codex_export(path, conversation_id="c1")
    assert exc.value.code == "CODEX_MD_IS_NOT_INGESTION"


def test_missing_export_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(Atlas3Error) as exc:
        import_codex_export(tmp_path / "absent.json", conversation_id="c1")
    assert exc.value.code == "EXPORT_NOT_FOUND"


def test_invalid_json_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(Atlas3Error) as exc:
        import_codex_export(path, conversation_id="c1")
    assert exc.value.code == "CODEX_EXPORT_INVALID"


def test_cli_codex_export(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "codex.json"
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
            "codex",
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
    assert payload["import_mode"] == "STRUCTURED_SUBMISSION"
    assert payload["promoted_to_truth_core"] == 0
    assert all(ord(char) < 128 for char in rendered)
    assert not (tmp_path / "projects").exists()


def test_cli_help_is_ascii(capsys: pytest.CaptureFixture[str]) -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_atlas3_parsers(sub)
    with pytest.raises(SystemExit) as info:
        parser.parse_args(["memory", "codex", "--help"])
    assert info.value.code == 0
    help_text = capsys.readouterr().out
    assert "Codex JSON" in help_text
    assert all(ord(char) < 128 for char in help_text)


def test_module_does_not_touch_2x_bridges() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/memory/codex.py").read_text(encoding="utf-8")
    for name in (
        "chatgpt_bridge",
        "chatgpt_capture",
        "knowledge_compiler",
        "from project_atlas.ingestion",
        "from project_atlas.ask2",
        "write_text(",
    ):
        assert name not in source
