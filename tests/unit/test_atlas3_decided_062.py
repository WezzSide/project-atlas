"""AT3-062 — isolated DECIDED_BY provenance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from project_atlas.atlas3.cli import dispatch_atlas3, register_atlas3_parsers
from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.decided import PACKAGE_ID, compile_decided_by


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    return vault


def _write_declared(vault: Path, payload: dict[str, object]) -> None:
    path = vault / "generated" / "ops" / "atlas3" / "decided-by" / "harbor-api" / "declared.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _owner_origin() -> dict[str, str]:
    return {
        "evidence_kind": "explicit_owner_statement",
        "origin": "owner",
        "statement": "Keep production on PostgreSQL 15.",
    }


def test_unknown_project_fails_closed(tmp_path: Path) -> None:
    vault = tmp_path / "empty"
    vault.mkdir()
    with pytest.raises(Atlas3Error) as exc:
        compile_decided_by(vault, "harbor-api")
    assert exc.value.code == "UNKNOWN_PROJECT"


def test_missing_declared_stays_unknown(tmp_path: Path) -> None:
    report = compile_decided_by(_vault(tmp_path), "harbor-api")
    assert report["package"] == PACKAGE_ID
    assert report["status"] == "UNKNOWN"
    assert report["model_is_owner"] is False
    assert report["graph_is_authority"] is False
    assert report["promoted_to_truth_core"] == 0


def test_declared_decided_by_requires_owner_origin(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "edges": [
                {
                    "from_id": "decision-pg15",
                    "to_id": "owner",
                    "evidence_refs": ["doc:owner.md#pg15"],
                    "owner_origin": _owner_origin(),
                }
            ],
        },
    )
    report = compile_decided_by(vault, "harbor-api")
    assert report["status"] == "derived"
    assert report["counts"] == {"edges": 1}
    assert report["edges"][0]["relationship"] == "DECIDED_BY"
    assert report["edges"][0]["owner_origin"]["origin"] == "owner"
    assert report["model_is_owner"] is False


def test_foreign_edge_project_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "edges": [
                {
                    "from_id": "decision-pg15",
                    "to_id": "owner",
                    "evidence_refs": ["doc:owner.md#pg15"],
                    "owner_origin": _owner_origin(),
                    "project_id": "other-api",
                }
            ],
        },
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_decided_by(vault, "harbor-api")
    assert exc.value.code == "CROSS_PROJECT"


def test_unlabeled_edge_still_compiles(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "edges": [
                {
                    "from_id": "decision-pg15",
                    "to_id": "owner",
                    "evidence_refs": ["doc:owner.md#pg15"],
                    "owner_origin": _owner_origin(),
                }
            ],
        },
    )
    report = compile_decided_by(vault, "harbor-api")
    assert report["status"] == "derived"
    assert report["edges"][0]["project_id"] == "harbor-api"


def test_model_claim_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "edges": [
                {
                    "from_id": "decision-pg16",
                    "to_id": "assistant",
                    "evidence_refs": ["doc:chat.md#claim"],
                    "owner_origin": {
                        "evidence_kind": "model_claim",
                        "origin": "assistant",
                        "statement": "owner decided postgres 16",
                    },
                }
            ],
        },
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_decided_by(vault, "harbor-api")
    assert exc.value.code == "FALSE_OWNER_DECISION"


def test_missing_owner_origin_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "edges": [
                {
                    "from_id": "decision-pg15",
                    "to_id": "owner",
                    "evidence_refs": ["doc:owner.md#pg15"],
                }
            ],
        },
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_decided_by(vault, "harbor-api")
    assert exc.value.code == "FALSE_OWNER_DECISION"


def test_cross_project_and_authority_fail_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(vault, {"project_id": "foreign"})
    with pytest.raises(Atlas3Error) as cross:
        compile_decided_by(vault, "harbor-api")
    assert cross.value.code == "CROSS_PROJECT"
    _write_declared(vault, {"project_id": "harbor-api", "graph_is_authority": True})
    with pytest.raises(Atlas3Error) as claimed:
        compile_decided_by(vault, "harbor-api")
    assert claimed.value.code == "GRAPH_AUTHORITY_CLAIMED"


def test_cli_decided_by_unknown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = _vault(tmp_path)
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_atlas3_parsers(sub)
    args = parser.parse_args(["decided-by", "--vault", str(vault), "--project", "harbor-api"])
    assert dispatch_atlas3(args) == 0
    rendered = capsys.readouterr().out
    payload = json.loads(rendered)
    assert payload["status"] == "UNKNOWN"
    assert all(ord(char) < 128 for char in rendered)


def test_cli_help_is_ascii(capsys: pytest.CaptureFixture[str]) -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_atlas3_parsers(sub)
    with pytest.raises(SystemExit) as info:
        parser.parse_args(["decided-by", "--help"])
    assert info.value.code == 0
    help_text = capsys.readouterr().out
    assert "DECIDED_BY" in help_text
    assert all(ord(char) < 128 for char in help_text)


def test_module_does_not_write() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/decided.py").read_text(encoding="utf-8")
    for name in (
        "chatgpt_bridge",
        "from project_atlas.ingestion",
        "shutil",
        "write_text(",
        "write_json_atomic",
    ):
        assert name not in source
