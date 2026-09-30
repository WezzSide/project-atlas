"""AT3-046 — isolated incremental sync honesty."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from project_atlas.atlas3.cli import dispatch_atlas3, register_atlas3_parsers
from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.memory.envelope import build_envelope
from project_atlas.atlas3.memory.incremental import (
    PACKAGE_ID,
    apply_local_incremental,
    envelope_cursor,
    incremental_capability,
)


def _env(
    *,
    message_id: str,
    text: str,
    project_id: str = "harbor-api",
    conversation_id: str = "c-inc",
) -> dict[str, object]:
    return build_envelope(
        provider="chatgpt",
        conversation_id=conversation_id,
        message_id=message_id,
        role="assistant",
        text=text,
        import_mode="EXPORT",
        project_id=project_id,
    )


def test_capability_keeps_live_incremental_blocked() -> None:
    cap = incremental_capability()
    assert cap["package"] == PACKAGE_ID
    assert cap["local_export_cursor"] == "IMPLEMENTED"
    assert cap["live_provider_incremental_sync"] == "EXTERNAL_BLOCKED"
    assert cap["live_full_history_sync"] is False
    assert cap["chatgpt_history_api"] is False
    assert cap["conversation_sync"] == "NOT_IMPLEMENTED"
    assert cap["writes_truth_core"] is False
    assert cap["merge_authorization"] == "NOT_GRANTED"


def test_local_cursor_applies_only_new_envelopes() -> None:
    first = _env(message_id="m1", text="pins PostgreSQL 15")
    second = _env(message_id="m2", text="later mentions 16")
    report = apply_local_incremental(
        [first],
        [first, second],
        cursor=envelope_cursor(first),
        conversation_id="c-inc",
        project_id="harbor-api",
    )
    assert report["package_id"] == PACKAGE_ID
    assert report["applied_count"] == 1
    assert report["skipped_already_accepted"] == 1
    assert report["applied"][0]["message_id"] == "m2"
    assert report["next_cursor"] == envelope_cursor(second)
    assert report["live_sync_used"] is False
    assert report["promoted_to_truth_core"] == 0
    assert report["write_applied"] is False


def test_empty_accepted_requires_empty_cursor() -> None:
    incoming = _env(message_id="m1", text="first export")
    report = apply_local_incremental(
        [],
        [incoming],
        cursor="",
        conversation_id="c-inc",
        project_id="harbor-api",
    )
    assert report["applied_count"] == 1
    assert report["next_cursor"] == envelope_cursor(incoming)


def test_cursor_mismatch_fails_closed() -> None:
    first = _env(message_id="m1", text="accepted")
    incoming = _env(message_id="m2", text="delta")
    with pytest.raises(Atlas3Error) as exc:
        apply_local_incremental(
            [first],
            [incoming],
            cursor="a3ce-not-the-last",
            conversation_id="c-inc",
            project_id="harbor-api",
        )
    assert exc.value.code == "INCREMENTAL_CURSOR_MISMATCH"


def test_missing_cursor_on_accepted_fails_closed() -> None:
    first = _env(message_id="m1", text="accepted")
    with pytest.raises(Atlas3Error) as exc:
        apply_local_incremental(
            [first],
            [],
            cursor=None,
            conversation_id="c-inc",
            project_id="harbor-api",
        )
    assert exc.value.code == "INCREMENTAL_CURSOR_REQUIRED"


def test_live_claim_fails_closed() -> None:
    incoming = _env(message_id="m1", text="claimed live")
    incoming["live_incremental_sync"] = True
    with pytest.raises(Atlas3Error) as exc:
        apply_local_incremental(
            [],
            [incoming],
            cursor="",
            conversation_id="c-inc",
            project_id="harbor-api",
        )
    assert exc.value.code == "INCREMENTAL_LIVE_CLAIMED"


def test_api_import_mode_fails_closed() -> None:
    incoming = _env(message_id="m1", text="api mode")
    incoming["import_mode"] = "API"
    with pytest.raises(Atlas3Error) as exc:
        apply_local_incremental(
            [],
            [incoming],
            cursor="",
            conversation_id="c-inc",
            project_id="harbor-api",
        )
    assert exc.value.code == "INCREMENTAL_LIVE_CLAIMED"


def test_credentials_fail_closed() -> None:
    incoming = _env(message_id="m1", text="secret")
    incoming["provider_metadata"] = {"api_key": "sk-test"}
    with pytest.raises(Atlas3Error) as exc:
        apply_local_incremental(
            [],
            [incoming],
            cursor="",
            conversation_id="c-inc",
            project_id="harbor-api",
        )
    assert exc.value.code == "INCREMENTAL_CREDENTIAL_REFUSED"


def test_mixed_valid_and_corrupt_fails_closed() -> None:
    first = _env(message_id="m1", text="ok")
    with pytest.raises(Atlas3Error) as exc:
        apply_local_incremental(
            [],
            [first, "corrupt"],
            cursor="",
            conversation_id="c-inc",
            project_id="harbor-api",
        )
    assert exc.value.code == "INCREMENTAL_INVALID"


def test_cross_project_fails_closed() -> None:
    foreign = _env(message_id="m1", text="other", project_id="other-api")
    with pytest.raises(Atlas3Error) as exc:
        apply_local_incremental(
            [],
            [foreign],
            cursor="",
            conversation_id="c-inc",
            project_id="harbor-api",
        )
    assert exc.value.code == "PROJECT_MISMATCH"


def test_conversation_mismatch_fails_closed() -> None:
    other = _env(message_id="m1", text="other thread", conversation_id="c-other")
    with pytest.raises(Atlas3Error) as exc:
        apply_local_incremental(
            [],
            [other],
            cursor="",
            conversation_id="c-inc",
            project_id="harbor-api",
        )
    assert exc.value.code == "INCREMENTAL_CONVERSATION_MISMATCH"


def test_cli_capability_and_apply(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_atlas3_parsers(sub)
    args = parser.parse_args(["memory", "incremental"])
    assert dispatch_atlas3(args) == 0
    cap = json.loads(capsys.readouterr().out)
    assert cap["package"] == PACKAGE_ID
    assert cap["live_provider_incremental_sync"] == "EXTERNAL_BLOCKED"

    first = _env(message_id="m1", text="accepted")
    second = _env(message_id="m2", text="delta")
    accepted = tmp_path / "accepted.json"
    incoming = tmp_path / "incoming.json"
    accepted.write_text(json.dumps([first]) + "\n", encoding="utf-8")
    incoming.write_text(json.dumps([second]) + "\n", encoding="utf-8")
    args = parser.parse_args(
        [
            "memory",
            "incremental",
            "--accepted",
            str(accepted),
            "--incoming",
            str(incoming),
            "--cursor",
            envelope_cursor(first),
            "--conversation-id",
            "c-inc",
            "--project",
            "harbor-api",
        ]
    )
    assert dispatch_atlas3(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["applied_count"] == 1
    assert report["live_sync_used"] is False
    assert report["promoted_to_truth_core"] == 0
    assert all(ord(char) < 128 for char in json.dumps(report))
    assert not (tmp_path / "projects").exists()


def test_cli_help_is_ascii(capsys: pytest.CaptureFixture[str]) -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_atlas3_parsers(sub)
    with pytest.raises(SystemExit) as info:
        parser.parse_args(["memory", "incremental", "--help"])
    assert info.value.code == 0
    help_text = capsys.readouterr().out
    assert "JSON list of incoming envelopes." in help_text
    assert "--incoming" in help_text
    assert all(ord(char) < 128 for char in help_text)


def test_module_does_not_touch_2x_bridges() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/memory/incremental.py").read_text(encoding="utf-8")
    for name in (
        "from project_atlas.chatgpt_bridge",
        "from project_atlas.chatgpt_capture",
        "knowledge_compiler",
        "from project_atlas.ingestion",
        "write_text(",
        "write_json_atomic",
    ):
        assert name not in source
