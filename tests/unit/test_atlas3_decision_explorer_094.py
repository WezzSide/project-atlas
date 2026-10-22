"""AT3-094 — isolated Decision Explorer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from project_atlas.atlas3.cli import dispatch_atlas3, register_atlas3_parsers
from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.decision_explorer import PACKAGE_ID, compile_decision_explorer


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    return vault


def _write_declared(vault: Path, payload: dict[str, object]) -> None:
    path = (
        vault
        / "generated"
        / "ops"
        / "atlas3"
        / "decision-explorer"
        / "harbor-api"
        / "declared.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_missing_declared_stays_unknown(tmp_path: Path) -> None:
    report = compile_decision_explorer(_vault(tmp_path), "harbor-api")
    assert report["package_id"] == PACKAGE_ID
    assert report["status"] == "UNKNOWN"
    assert report["model_is_owner"] is False
    assert report["explorer_is_authority"] is False
    assert report["merge_authorization"] == "NOT_GRANTED"
    assert report["promoted_to_truth_core"] == 0


def test_confirmed_owner_decision(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "decisions": [
                {
                    "decision_id": "d-pg16",
                    "text": "Use PostgreSQL 16",
                    "status": "confirmed_owner",
                    "owner_origin": {
                        "evidence_kind": "explicit_owner_statement",
                        "origin": "owner",
                        "statement": "Use PostgreSQL 16",
                    },
                    "evidence_refs": ["adr-001"],
                }
            ],
        },
    )
    report = compile_decision_explorer(vault, "harbor-api")
    assert report["status"] == "derived"
    assert report["counts"] == {"decisions": 1, "confirmed_owner": 1}
    assert report["decisions"][0]["status"] == "confirmed_owner"
    assert report["decisions"][0]["owner_origin"]["origin"] == "owner"
    assert report["model_is_owner"] is False


def test_foreign_decision_project_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "decisions": [
                {
                    "decision_id": "d-pg16",
                    "text": "Use PostgreSQL 16",
                    "status": "confirmed_owner",
                    "project_id": "other-api",
                    "owner_origin": {
                        "evidence_kind": "explicit_owner_statement",
                        "origin": "owner",
                        "statement": "Use PostgreSQL 16",
                    },
                    "evidence_refs": ["adr-001"],
                }
            ],
        },
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_decision_explorer(vault, "harbor-api")
    assert exc.value.code == "CROSS_PROJECT"


def test_unlabeled_decision_still_compiles(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "decisions": [
                {
                    "decision_id": "d-pg16",
                    "text": "Use PostgreSQL 16",
                    "status": "confirmed_owner",
                    "owner_origin": {
                        "evidence_kind": "explicit_owner_statement",
                        "origin": "owner",
                        "statement": "Use PostgreSQL 16",
                    },
                    "evidence_refs": ["adr-001"],
                }
            ],
        },
    )
    report = compile_decision_explorer(vault, "harbor-api")
    assert report["status"] == "derived"
    assert report["decisions"][0]["project_id"] == "harbor-api"


def test_model_paraphrase_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "decisions": [
                {
                    "decision_id": "d-fake",
                    "text": "Ship it",
                    "status": "proposed",
                    "model_paraphrase": True,
                    "evidence_refs": ["chat:1"],
                }
            ],
        },
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_decision_explorer(vault, "harbor-api")
    assert exc.value.code == "FALSE_OWNER_DECISION"


def test_confirmed_without_owner_origin_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "decisions": [
                {
                    "decision_id": "d-bare",
                    "text": "Use Redis",
                    "status": "confirmed_owner",
                    "evidence_refs": ["chat:2"],
                }
            ],
        },
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_decision_explorer(vault, "harbor-api")
    assert exc.value.code == "FALSE_OWNER_DECISION"


def test_model_origin_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "decisions": [
                {
                    "decision_id": "d-llm",
                    "text": "Use Redis",
                    "status": "proposed",
                    "owner_origin": {
                        "evidence_kind": "explicit_owner_statement",
                        "origin": "model",
                        "statement": "Use Redis",
                    },
                    "evidence_refs": ["chat:3"],
                }
            ],
        },
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_decision_explorer(vault, "harbor-api")
    assert exc.value.code == "FALSE_OWNER_DECISION"


def test_cross_project_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(vault, {"project_id": "foreign", "decisions": []})
    with pytest.raises(Atlas3Error) as exc:
        compile_decision_explorer(vault, "harbor-api")
    assert exc.value.code == "CROSS_PROJECT"


def test_cli_decision_explorer(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = _vault(tmp_path)
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_atlas3_parsers(sub)
    args = parser.parse_args(
        ["decision-explorer", "--vault", str(vault), "--project", "harbor-api"]
    )
    assert dispatch_atlas3(args) == 0
    rendered = capsys.readouterr().out
    payload = json.loads(rendered)
    assert payload["status"] == "UNKNOWN"
    assert payload["explorer_is_authority"] is False
    assert payload["model_is_owner"] is False
    assert all(ord(char) < 128 for char in rendered)


def test_cli_help_is_ascii(capsys: pytest.CaptureFixture[str]) -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_atlas3_parsers(sub)
    with pytest.raises(SystemExit) as info:
        parser.parse_args(["decision-explorer", "--help"])
    assert info.value.code == 0
    help_text = capsys.readouterr().out
    collapsed = " ".join(help_text.split())
    assert "model paraphrase is not an owner decision" in collapsed
    assert all(ord(char) < 128 for char in help_text)


def test_module_does_not_write() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/decision_explorer.py").read_text(encoding="utf-8")
    for name in (
        "write_json_atomic",
        "write_text(",
        "chatgpt_bridge",
        "from project_atlas.ingestion",
        "from project_atlas.graph_relationships",
    ):
        assert name not in source
