"""AT3-080 — isolated impact explorer data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from project_atlas.atlas3.cli import dispatch_atlas3, register_atlas3_parsers
from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.impact import PACKAGE_ID, compile_impact_explorer


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    return vault


def _write_declared(vault: Path, payload: dict[str, object]) -> None:
    path = vault / "generated" / "ops" / "atlas3" / "impact" / "harbor-api" / "declared.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_missing_declared_stays_unknown(tmp_path: Path) -> None:
    report = compile_impact_explorer(_vault(tmp_path), "harbor-api")
    assert report["package_id"] == PACKAGE_ID
    assert report["status"] == "UNKNOWN"
    assert report["graph_is_authority"] is False
    assert report["trust_score_used"] is False
    assert report["merge_authorization"] == "NOT_GRANTED"


def test_declared_impacts(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "impacts": [
                {
                    "impact_kind": "blocks",
                    "from_id": "conflict-1",
                    "to_id": "release-1",
                    "evidence_refs": ["doc:conflicts.md#pg"],
                }
            ],
        },
    )
    report = compile_impact_explorer(vault, "harbor-api")
    assert report["status"] == "derived"
    assert report["counts"] == {"impacts": 1}
    assert report["impacts"][0]["impact_kind"] == "blocks"
    assert report["impacts"][0]["trust_score"] is None


def test_trust_score_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(vault, {"project_id": "harbor-api", "trust_score": 0.9, "impacts": []})
    with pytest.raises(Atlas3Error) as exc:
        compile_impact_explorer(vault, "harbor-api")
    assert exc.value.code == "TRUST_SCORE_FORBIDDEN"


def test_row_trust_score_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "impacts": [
                {
                    "impact_kind": "depends_on",
                    "from_id": "a",
                    "to_id": "b",
                    "evidence_refs": ["doc:a"],
                    "trust_score": 12,
                }
            ],
        },
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_impact_explorer(vault, "harbor-api")
    assert exc.value.code == "TRUST_SCORE_FORBIDDEN"


def test_cross_project_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(vault, {"project_id": "foreign", "impacts": []})
    with pytest.raises(Atlas3Error) as exc:
        compile_impact_explorer(vault, "harbor-api")
    assert exc.value.code == "CROSS_PROJECT"


def test_cli_impact_explorer(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = _vault(tmp_path)
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_atlas3_parsers(sub)
    args = parser.parse_args(["impact-explorer", "--vault", str(vault), "--project", "harbor-api"])
    assert dispatch_atlas3(args) == 0
    rendered = capsys.readouterr().out
    payload = json.loads(rendered)
    assert payload["status"] == "UNKNOWN"
    assert payload["graph_is_authority"] is False
    assert all(ord(char) < 128 for char in rendered)


def test_cli_help_is_ascii(capsys: pytest.CaptureFixture[str]) -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_atlas3_parsers(sub)
    with pytest.raises(SystemExit) as info:
        parser.parse_args(["impact-explorer", "--help"])
    assert info.value.code == 0
    help_text = capsys.readouterr().out
    assert "graph is not authority" in help_text
    assert all(ord(char) < 128 for char in help_text)


def test_module_does_not_write() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/impact.py").read_text(encoding="utf-8")
    for name in (
        "write_json_atomic",
        "write_text(",
        "chatgpt_bridge",
        "from project_atlas.ingestion",
        "from project_atlas.graph_relationships",
    ):
        assert name not in source
