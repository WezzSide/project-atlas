"""AT3-043 — conversation decision + intent extraction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.atlas3.cli import dispatch_atlas3, register_atlas3_parsers
from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.memory.intent import PACKAGE_ID, TRUTH_BOUNDARY, extract_intent_report


def _item(
    *,
    item_type: str,
    project_id: str = "harbor-api",
    text: str = "sample",
    owner_origin: dict[str, object] | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "item_type": item_type,
        "project_id": project_id,
        "text": text,
        "authority": "NON_CANONICAL",
    }
    if owner_origin is not None:
        row["owner_origin"] = owner_origin
    return row


def test_missing_project_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        extract_intent_report([], requested_project_id="  ")
    assert exc.value.code == "PROJECT_REQUIRED"


def test_cross_project_item_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        extract_intent_report(
            [_item(item_type="observation", project_id="foreign")],
            requested_project_id="harbor-api",
        )
    assert exc.value.code == "CROSS_PROJECT"


def test_forged_owner_decision_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        extract_intent_report(
            [_item(item_type="confirmed_owner_decision", text="we decided postgres 16")],
            requested_project_id="harbor-api",
        )
    assert exc.value.code == "FALSE_OWNER_DECISION"


def test_owner_origin_required_fields() -> None:
    with pytest.raises(Atlas3Error) as exc:
        extract_intent_report(
            [
                _item(
                    item_type="confirmed_owner_decision",
                    owner_origin={"evidence_kind": "model_claim", "origin": "assistant"},
                )
            ],
            requested_project_id="harbor-api",
        )
    assert exc.value.code == "FALSE_OWNER_DECISION"


def test_intent_is_not_current_state() -> None:
    report = extract_intent_report(
        [
            _item(item_type="proposed_decision", text="we should migrate later"),
            _item(item_type="next_step", text="look at the migration"),
            _item(item_type="claim_candidate", text="production uses postgres 15"),
        ],
        requested_project_id="harbor-api",
    )
    assert report["package_id"] == PACKAGE_ID
    assert report["counts"]["intent"] == 2
    assert report["counts"]["current_state"] == 1
    assert report["counts"]["decision"] == 0
    intent_types = {row["item_type"] for row in report["layers"]["intent"]}
    assert intent_types == {"proposed_decision", "next_step"}
    assert all(row["layer"] != "current_state" for row in report["layers"]["intent"])
    assert report["honesty"]["intent_is_current_state"] is False
    assert report["honesty"]["model_is_owner"] is False
    assert report["honesty"]["promoted_to_truth_core"] is False
    assert "INTENT != CURRENT STATE" in report["truth_boundary"]


def test_confirmed_owner_decision_stays_decision() -> None:
    origin = {
        "evidence_kind": "explicit_owner_statement",
        "origin": "owner",
        "statement": "Use PostgreSQL 15 until the owner signs the cutover.",
    }
    report = extract_intent_report(
        [_item(item_type="confirmed_owner_decision", owner_origin=origin)],
        requested_project_id="harbor-api",
    )
    assert report["counts"]["decision"] == 1
    assert report["layers"]["decision"][0]["layer"] == "decision"
    assert report["layers"]["decision"][0]["promoted_to_truth_core"] is False


def test_open_question_stays_unknown() -> None:
    report = extract_intent_report(
        [_item(item_type="open_question", text="which datastore is current?")],
        requested_project_id="harbor-api",
    )
    assert report["counts"]["unknown"] == 1
    assert report["honesty"]["unknown_stays_unknown"] is True


def test_cli_intent_empty_reconcile(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import argparse

    vault = tmp_path / "vault"
    vault.mkdir()
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_atlas3_parsers(sub)
    args = parser.parse_args(["memory", "intent", "--vault", str(vault), "--project", "harbor-api"])
    assert dispatch_atlas3(args) == 0
    rendered = capsys.readouterr().out
    payload = json.loads(rendered)
    assert payload["package_id"] == PACKAGE_ID
    assert payload["counts"]["intent"] == 0
    assert payload["truth_boundary"] == TRUTH_BOUNDARY
    assert all(ord(char) < 128 for char in rendered)


def test_cli_help_is_ascii(capsys: pytest.CaptureFixture[str]) -> None:
    import argparse

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_atlas3_parsers(sub)
    with pytest.raises(SystemExit) as info:
        parser.parse_args(["memory", "intent", "--help"])
    assert info.value.code == 0
    assert all(ord(char) < 128 for char in capsys.readouterr().out)


def test_module_does_not_write_truth_core() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/memory/intent.py").read_text(encoding="utf-8")
    forbidden = (
        "knowledge_compiler",
        "from project_atlas.ingestion",
        "write_json_atomic",
        "write_text(",
        "ask_atlas_2(",
    )
    for name in forbidden:
        assert name not in source
