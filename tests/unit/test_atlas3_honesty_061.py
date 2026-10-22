"""AT3-061 — intent vs current-state honesty wrapper."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from project_atlas.atlas3.cli import dispatch_atlas3, register_atlas3_parsers
from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.memory.honesty import (
    PACKAGE_ID,
    TRUTH_BOUNDARY,
    wrap_intent_state_honesty,
)


def _item(
    *,
    item_type: str,
    project_id: str = "harbor-api",
    text: str = "sample",
    **extra: object,
) -> dict[str, object]:
    row: dict[str, object] = {
        "item_type": item_type,
        "project_id": project_id,
        "text": text,
        "authority": "NON_CANONICAL",
    }
    row.update(extra)
    return row


def test_missing_project_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        wrap_intent_state_honesty([], requested_project_id="  ")
    assert exc.value.code == "PROJECT_REQUIRED"


def test_cross_project_item_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        wrap_intent_state_honesty(
            [_item(item_type="observation", project_id="foreign")],
            requested_project_id="harbor-api",
        )
    assert exc.value.code == "CROSS_PROJECT"


def test_intent_and_state_stay_disjoint() -> None:
    report = wrap_intent_state_honesty(
        [
            _item(item_type="proposed_decision", text="migrate to postgres 16 later"),
            _item(item_type="claim_candidate", text="production uses postgres 15"),
        ],
        requested_project_id="harbor-api",
    )
    assert report["package_id"] == PACKAGE_ID
    assert report["composed_from"] == "AT3-043"
    assert report["collapsed"] is False
    assert report["counts"]["intent"] == 1
    assert report["counts"]["current_state"] == 1
    assert report["honesty"]["intent_is_current_state"] is False
    assert report["honesty"]["current_state_is_intent"] is False
    assert report["honesty"]["layers_collapsed"] is False
    assert report["honesty"]["MERGE_AUTHORIZATION"] == "NOT_GRANTED"
    assert "INTENT != CURRENT STATE" in report["truth_boundary"]
    assert report["promoted_to_truth_core"] == 0


def test_declared_collapse_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        wrap_intent_state_honesty(
            [
                _item(
                    item_type="proposed_decision",
                    layer=["intent", "current_state"],
                )
            ],
            requested_project_id="harbor-api",
        )
    assert exc.value.code == "LAYER_COLLAPSE"


def test_intent_declared_as_state_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        wrap_intent_state_honesty(
            [_item(item_type="next_step", layer="current_state")],
            requested_project_id="harbor-api",
        )
    assert exc.value.code == "INTENT_COLLAPSED_TO_STATE"


def test_state_declared_as_intent_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        wrap_intent_state_honesty(
            [_item(item_type="observation", layer="intent")],
            requested_project_id="harbor-api",
        )
    assert exc.value.code == "STATE_PRESENTED_AS_INTENT"


def test_present_as_current_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        wrap_intent_state_honesty(
            [_item(item_type="idea", present_as_current=True)],
            requested_project_id="harbor-api",
        )
    assert exc.value.code == "INTENT_COLLAPSED_TO_STATE"


def test_truth_core_promotion_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        wrap_intent_state_honesty(
            [_item(item_type="observation", promoted_to_truth_core=True)],
            requested_project_id="harbor-api",
        )
    assert exc.value.code == "TRUTH_CORE_PROMOTION_ATTEMPT"


def _composed_report(
    *,
    project_id: str = "harbor-api",
    intent_rows: list[object] | None = None,
    honesty: object | None = None,
    extra_layers: dict[str, object] | None = None,
) -> dict[str, object]:
    layers: dict[str, object] = {
        "intent": intent_rows
        if intent_rows is not None
        else [
            {
                "item_type": "idea",
                "layer": "intent",
                "project_id": project_id,
                "text": "ok",
                "id": "a",
            }
        ],
        "current_state": [],
        "decision": [],
        "history": [],
        "unknown": [],
    }
    if extra_layers:
        layers.update(extra_layers)
    counts = {
        name: len(rows) if isinstance(rows, list) else 0 for name, rows in layers.items()
    }
    return {
        "project_id": project_id,
        "counts": counts,
        "layers": layers,
        "honesty": {"intent_is_current_state": False} if honesty is None else honesty,
    }


def test_composed_mixed_corrupt_layer_fails_closed() -> None:
    def fake_report(
        items: list[dict[str, object]],
        *,
        requested_project_id: str,
        owner_origin: dict[str, object] | None = None,
    ) -> dict[str, object]:
        del items, owner_origin
        return _composed_report(
            project_id=requested_project_id,
            intent_rows=[
                {
                    "item_type": "idea",
                    "layer": "intent",
                    "project_id": requested_project_id,
                    "text": "ok",
                    "id": "a",
                },
                "corrupt-row",
            ],
        )

    with (
        patch(
            "project_atlas.atlas3.memory.honesty.extract_intent_report",
            side_effect=fake_report,
        ),
        pytest.raises(Atlas3Error) as exc,
    ):
        wrap_intent_state_honesty(
            [_item(item_type="idea", text="ok")],
            requested_project_id="harbor-api",
        )
    assert exc.value.code == "INTENT_ITEM_INVALID"


def test_composed_non_list_layer_fails_closed() -> None:
    def fake_report(
        items: list[dict[str, object]],
        *,
        requested_project_id: str,
        owner_origin: dict[str, object] | None = None,
    ) -> dict[str, object]:
        del items, owner_origin
        return _composed_report(
            project_id=requested_project_id,
            extra_layers={"history": {"not": "a-list"}},
        )

    with (
        patch(
            "project_atlas.atlas3.memory.honesty.extract_intent_report",
            side_effect=fake_report,
        ),
        pytest.raises(Atlas3Error) as exc,
    ):
        wrap_intent_state_honesty(
            [_item(item_type="idea", text="ok")],
            requested_project_id="harbor-api",
        )
    assert exc.value.code == "LAYER_COLLAPSE"


def test_composed_foreign_layer_row_fails_closed() -> None:
    def fake_report(
        items: list[dict[str, object]],
        *,
        requested_project_id: str,
        owner_origin: dict[str, object] | None = None,
    ) -> dict[str, object]:
        del items, owner_origin
        return _composed_report(
            project_id=requested_project_id,
            intent_rows=[
                {
                    "item_type": "idea",
                    "layer": "intent",
                    "project_id": "other-api",
                    "text": "foreign",
                    "id": "x",
                }
            ],
        )

    with (
        patch(
            "project_atlas.atlas3.memory.honesty.extract_intent_report",
            side_effect=fake_report,
        ),
        pytest.raises(Atlas3Error) as exc,
    ):
        wrap_intent_state_honesty(
            [_item(item_type="idea", text="ok")],
            requested_project_id="harbor-api",
        )
    assert exc.value.code == "CROSS_PROJECT"


def test_same_identity_in_both_layers_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        wrap_intent_state_honesty(
            [
                _item(item_type="proposed_decision", text="shared-fact"),
                _item(item_type="observation", text="shared-fact"),
            ],
            requested_project_id="harbor-api",
        )
    assert exc.value.code == "LAYER_COLLAPSE"


def test_cli_honesty_empty_reconcile(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_atlas3_parsers(sub)
    args = parser.parse_args(
        ["memory", "honesty", "--vault", str(vault), "--project", "harbor-api"]
    )
    assert dispatch_atlas3(args) == 0
    rendered = capsys.readouterr().out
    payload = json.loads(rendered)
    assert payload["package_id"] == PACKAGE_ID
    assert payload["counts"]["intent"] == 0
    assert payload["truth_boundary"] == TRUTH_BOUNDARY
    assert all(ord(char) < 128 for char in rendered)


def test_cli_help_is_ascii(capsys: pytest.CaptureFixture[str]) -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_atlas3_parsers(sub)
    with pytest.raises(SystemExit) as info:
        parser.parse_args(["memory", "honesty", "--help"])
    assert info.value.code == 0
    help_text = capsys.readouterr().out
    assert "does not collapse layers" in help_text
    assert all(ord(char) < 128 for char in help_text)


def test_module_does_not_write_truth_core() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/memory/honesty.py").read_text(encoding="utf-8")
    forbidden = (
        "knowledge_compiler",
        "from project_atlas.ingestion",
        "write_json_atomic",
        "write_text(",
        "ask_atlas_2(",
        "chatgpt_bridge",
    )
    for name in forbidden:
        assert name not in source
