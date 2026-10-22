"""AT3-060 — isolated causal graph (CAUSED_BY)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from project_atlas.atlas3.causal import PACKAGE_ID, compile_causal_graph
from project_atlas.atlas3.cli import dispatch_atlas3, register_atlas3_parsers
from project_atlas.atlas3.contracts import Atlas3Error


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    return vault


def _write_declared(vault: Path, payload: dict[str, object]) -> None:
    path = vault / "generated" / "ops" / "atlas3" / "causal-graph" / "harbor-api" / "declared.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_unknown_project_fails_closed(tmp_path: Path) -> None:
    vault = tmp_path / "empty"
    vault.mkdir()
    with pytest.raises(Atlas3Error) as exc:
        compile_causal_graph(vault, "harbor-api")
    assert exc.value.code == "UNKNOWN_PROJECT"


def test_missing_declared_stays_unknown(tmp_path: Path) -> None:
    report = compile_causal_graph(_vault(tmp_path), "harbor-api")
    assert report["package"] == PACKAGE_ID
    assert report["status"] == "UNKNOWN"
    assert report["graph_is_authority"] is False
    assert report["promoted_to_truth_core"] == 0
    assert report["relationship"] == "CAUSED_BY"


def test_declared_caused_by_edges(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "edges": [
                {
                    "from_id": "outage-1",
                    "to_id": "deploy-1",
                    "evidence_refs": ["doc:incident.md#cause"],
                }
            ],
        },
    )
    report = compile_causal_graph(vault, "harbor-api")
    assert report["status"] == "derived"
    assert report["counts"] == {"edges": 1}
    assert report["edges"][0]["relationship"] == "CAUSED_BY"
    assert report["edges"][0]["authority"] == "derived"
    assert report["edges"][0]["graph_is_authority"] is False
    assert report["honesty"]["graph_is_authority"] is False


def test_foreign_edge_project_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "edges": [
                {
                    "from_id": "outage-1",
                    "to_id": "deploy-1",
                    "evidence_refs": ["doc:incident.md#cause"],
                    "project_id": "other-api",
                }
            ],
        },
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_causal_graph(vault, "harbor-api")
    assert exc.value.code == "CROSS_PROJECT"


def test_unlabeled_edge_still_compiles(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "edges": [
                {
                    "from_id": "outage-1",
                    "to_id": "deploy-1",
                    "evidence_refs": ["doc:incident.md#cause"],
                }
            ],
        },
    )
    report = compile_causal_graph(vault, "harbor-api")
    assert report["status"] == "derived"
    assert report["edges"][0]["project_id"] == "harbor-api"


def test_cross_project_corrupt_and_authority_fail_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(vault, {"project_id": "foreign"})
    with pytest.raises(Atlas3Error) as cross:
        compile_causal_graph(vault, "harbor-api")
    assert cross.value.code == "CROSS_PROJECT"
    path = vault / "generated" / "ops" / "atlas3" / "causal-graph" / "harbor-api" / "declared.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(Atlas3Error) as corrupt:
        compile_causal_graph(vault, "harbor-api")
    assert corrupt.value.code == "CAUSAL_GRAPH_CORRUPT"
    _write_declared(vault, {"project_id": "harbor-api", "graph_is_authority": True})
    with pytest.raises(Atlas3Error) as claimed:
        compile_causal_graph(vault, "harbor-api")
    assert claimed.value.code == "GRAPH_AUTHORITY_CLAIMED"


def test_non_caused_by_and_missing_provenance_fail_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "edges": [
                {
                    "from_id": "a",
                    "to_id": "b",
                    "relationship": "DEPENDS_ON",
                    "evidence_refs": ["x"],
                }
            ],
        },
    )
    with pytest.raises(Atlas3Error) as rel:
        compile_causal_graph(vault, "harbor-api")
    assert rel.value.code == "CAUSAL_RELATIONSHIP_INVALID"
    _write_declared(
        vault,
        {"project_id": "harbor-api", "edges": [{"from_id": "a", "to_id": "b"}]},
    )
    with pytest.raises(Atlas3Error) as prov:
        compile_causal_graph(vault, "harbor-api")
    assert prov.value.code == "PROVENANCE_REQUIRED"


def test_cli_causal_graph_unknown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = _vault(tmp_path)
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_atlas3_parsers(sub)
    args = parser.parse_args(["causal-graph", "--vault", str(vault), "--project", "harbor-api"])
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
        parser.parse_args(["causal-graph", "--help"])
    assert info.value.code == 0
    help_text = capsys.readouterr().out
    assert "CAUSED_BY" in help_text
    assert all(ord(char) < 128 for char in help_text)


def test_module_does_not_write() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/causal.py").read_text(encoding="utf-8")
    for name in (
        "chatgpt_bridge",
        "from project_atlas.ingestion",
        "shutil",
        "write_text(",
        "write_json_atomic",
    ):
        assert name not in source
