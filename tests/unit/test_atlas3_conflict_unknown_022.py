"""AT3-022 — isolated conflict / UNKNOWN projection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from project_atlas.atlas3.cli import dispatch_atlas3, register_atlas3_parsers
from project_atlas.atlas3.conflict_unknown import PACKAGE_ID, compile_conflict_unknown
from project_atlas.atlas3.contracts import Atlas3Error


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    return vault


def _write_declared(vault: Path, payload: dict[str, object]) -> None:
    path = (
        vault / "generated" / "ops" / "atlas3" / "conflict-unknown" / "harbor-api" / "declared.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_missing_declared_stays_unknown(tmp_path: Path) -> None:
    report = compile_conflict_unknown(_vault(tmp_path), "harbor-api")
    assert report["package_id"] == PACKAGE_ID
    assert report["status"] == "UNKNOWN"
    assert report["conflicts"] == []
    assert report["unknowns"] == []
    assert report["unknown_collapsed"] is False
    assert report["filtered_corruption"] is False
    assert report["merge_authorization"] == "NOT_GRANTED"


def test_declared_conflicts_and_unknowns(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "conflicts": [
                {
                    "conflict_id": "pg-version",
                    "sides": ["PostgreSQL 15", "PostgreSQL 16"],
                    "evidence_refs": ["doc:adr.md#pg"],
                }
            ],
            "unknowns": [
                {
                    "unknown_id": "cache",
                    "text": "Which cache is current?",
                    "status": "UNKNOWN",
                }
            ],
        },
    )
    report = compile_conflict_unknown(vault, "harbor-api")
    assert report["status"] == "derived"
    assert report["counts"] == {"conflicts": 1, "unknowns": 1}
    assert report["conflicts"][0]["winner"] is None
    assert report["unknowns"][0]["status"] == "UNKNOWN"


def test_winner_and_unknown_collapse_fail_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "conflicts": [
                {
                    "conflict_id": "pg",
                    "sides": ["15", "16"],
                    "evidence_refs": ["doc:x"],
                    "winner": "16",
                }
            ],
        },
    )
    with pytest.raises(Atlas3Error) as winner:
        compile_conflict_unknown(vault, "harbor-api")
    assert winner.value.code == "GRAPH_WINNER_CLAIMED"
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "unknowns": [
                {
                    "unknown_id": "cache",
                    "text": "cache?",
                    "invented_answer": "redis",
                }
            ],
        },
    )
    with pytest.raises(Atlas3Error) as collapsed:
        compile_conflict_unknown(vault, "harbor-api")
    assert collapsed.value.code == "UNKNOWN_COLLAPSED"


def test_healthy_filter_and_cross_project_fail_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(vault, {"project_id": "harbor-api", "filter_corruption": True})
    with pytest.raises(Atlas3Error) as filtered:
        compile_conflict_unknown(vault, "harbor-api")
    assert filtered.value.code == "HEALTHY_FILTER_FORBIDDEN"
    _write_declared(vault, {"project_id": "foreign"})
    with pytest.raises(Atlas3Error) as cross:
        compile_conflict_unknown(vault, "harbor-api")
    assert cross.value.code == "CROSS_PROJECT"


def test_resolved_true_without_status_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "conflicts": [
                {
                    "conflict_id": "pg",
                    "sides": ["15", "16"],
                    "evidence_refs": ["doc:x"],
                    "resolved": True,
                }
            ],
        },
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_conflict_unknown(vault, "harbor-api")
    assert exc.value.code == "CONFLICT_STATE_INCOHERENT"


def test_whitespace_only_sides_fail_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "conflicts": [
                {
                    "conflict_id": "pg",
                    "sides": ["  ", "\t"],
                    "evidence_refs": ["doc:x"],
                }
            ],
        },
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_conflict_unknown(vault, "harbor-api")
    assert exc.value.code == "CONFLICT_SIDES_REQUIRED"


def test_corrupt_json_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    path = (
        vault / "generated" / "ops" / "atlas3" / "conflict-unknown" / "harbor-api" / "declared.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{nope", encoding="utf-8")
    with pytest.raises(Atlas3Error) as corrupt:
        compile_conflict_unknown(vault, "harbor-api")
    assert corrupt.value.code == "CONFLICT_UNKNOWN_CORRUPT"


def test_cli_conflict_unknown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = _vault(tmp_path)
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_atlas3_parsers(sub)
    args = parser.parse_args(["conflict-unknown", "--vault", str(vault), "--project", "harbor-api"])
    assert dispatch_atlas3(args) == 0
    rendered = capsys.readouterr().out
    payload = json.loads(rendered)
    assert payload["status"] == "UNKNOWN"
    assert payload["unknown_collapsed"] is False
    assert all(ord(char) < 128 for char in rendered)


def test_cli_help_is_ascii(capsys: pytest.CaptureFixture[str]) -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_atlas3_parsers(sub)
    with pytest.raises(SystemExit) as info:
        parser.parse_args(["conflict-unknown", "--help"])
    assert info.value.code == 0
    help_text = capsys.readouterr().out
    collapsed = " ".join(help_text.split())
    assert "UNKNOWN remains UNKNOWN" in collapsed
    assert all(ord(char) < 128 for char in help_text)


def test_module_does_not_write() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/conflict_unknown.py").read_text(encoding="utf-8")
    for name in (
        "write_json_atomic",
        "write_text(",
        "chatgpt_bridge",
        "from project_atlas.ingestion",
        "from project_atlas.knowledge_compiler",
    ):
        assert name not in source
