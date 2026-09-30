"""AT3-081 — isolated stale / conflict intelligence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.ledger import append_event
from project_atlas.atlas3.stale_conflict import PACKAGE_ID, compile_stale_conflict_intel


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    return vault


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_missing_evidence_stays_unknown(tmp_path: Path) -> None:
    report = compile_stale_conflict_intel(_vault(tmp_path), "harbor-api")
    assert report["package_id"] == PACKAGE_ID
    assert report["status"] == "UNKNOWN"
    assert report["winner_selected"] is False
    assert report["graph_is_authority"] is False
    assert report["stale_as_current"] is False
    assert report["new_cli_command"] is False
    assert report["merge_authorization"] == "NOT_GRANTED"
    assert report["write_applied"] is False


def test_composes_stale_ledger_without_selecting_winner(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    append_event(
        vault,
        "harbor-api",
        event_type="CONTEXT_INVALIDATED",
        source_plane="engineering",
        summary="context pack expired",
        payload={"freshness": "STALE"},
    )
    report = compile_stale_conflict_intel(vault, "harbor-api")
    assert report["status"] == "derived"
    assert report["counts"]["stale_ledger"] == 1
    assert report["winner_selected"] is False
    assert report["stale_as_current"] is False


def test_memory_conflict_does_not_collapse_to_winner(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_json(
        vault / "generated" / "ops" / "atlas3" / "memory" / "harbor-api" / "reconcile.json",
        {
            "reconciliation": {
                "items": [
                    {
                        "project_id": "harbor-api",
                        "provider": "chatgpt",
                        "text": "PostgreSQL 15 is deployed",
                    },
                    {
                        "project_id": "harbor-api",
                        "provider": "claude",
                        "text": "PostgreSQL 16 is deployed",
                    },
                ]
            }
        },
    )
    report = compile_stale_conflict_intel(vault, "harbor-api")
    assert report["status"] == "derived"
    conflicts = report["conflicts"]["memory"]
    assert conflicts is not None
    assert conflicts["conflicted_history"] is True
    assert conflicts["collapsed_to_scalar"] is False
    assert report["winner_selected"] is False


def test_winner_claim_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_json(
        vault / "generated" / "ops" / "atlas3" / "memory" / "harbor-api" / "reconcile.json",
        {"winner": "chatgpt", "items": []},
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_stale_conflict_intel(vault, "harbor-api")
    assert exc.value.code == "WINNER_SELECTED"


def test_stale_as_current_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_json(
        vault / "generated" / "ops" / "atlas3" / "memory" / "harbor-api" / "reconcile.json",
        {
            "reconciliation": {
                "items": [{"project_id": "harbor-api", "provider": "chatgpt", "text": "old"}],
                "stale_memories": [
                    {
                        "project_id": "harbor-api",
                        "provider": "chatgpt",
                        "text": "old",
                        "freshness": "CURRENT",
                    }
                ],
            }
        },
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_stale_conflict_intel(vault, "harbor-api")
    assert exc.value.code == "STALE_AS_CURRENT"


def test_ledger_graph_winner_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    append_event(
        vault,
        "harbor-api",
        event_type="CONTEXT_INVALIDATED",
        source_plane="engineering",
        summary="stale with invented winner",
        payload={"freshness": "STALE", "graph_winner": "chatgpt"},
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_stale_conflict_intel(vault, "harbor-api")
    assert exc.value.code == "GRAPH_WINNER_FORBIDDEN"


def test_foreign_memory_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_json(
        vault / "generated" / "ops" / "atlas3" / "memory" / "harbor-api" / "reconcile.json",
        {
            "reconciliation": {
                "items": [{"project_id": "foreign-api", "provider": "chatgpt", "text": "leak"}]
            }
        },
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_stale_conflict_intel(vault, "harbor-api")
    assert exc.value.code == "PROJECT_MISMATCH"


def test_corrupt_ledger_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    path = vault / "generated" / "ops" / "atlas3" / "ledger" / "harbor-api.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json\n", encoding="utf-8")
    with pytest.raises(Atlas3Error) as exc:
        compile_stale_conflict_intel(vault, "harbor-api")
    assert exc.value.code == "LEDGER_CORRUPT"


def test_module_does_not_write() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/stale_conflict.py").read_text(encoding="utf-8")
    for name in (
        "write_json_atomic",
        "write_text(",
        "chatgpt_bridge",
        "from project_atlas.ingestion",
        "add_parser",
    ):
        assert name not in source
