"""AT3-023 — isolated graph != authority prover."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from project_atlas.atlas3.cli import dispatch_atlas3, register_atlas3_parsers
from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.graph_authority import (
    PACKAGE_ID,
    compile_graph_authority,
    prove_graph_is_not_authority,
)


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    return vault


def _write_declared(vault: Path, payload: dict[str, object]) -> None:
    path = (
        vault / "generated" / "ops" / "atlas3" / "graph-authority" / "harbor-api" / "declared.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_missing_declared_stays_unknown(tmp_path: Path) -> None:
    report = compile_graph_authority(_vault(tmp_path), "harbor-api")
    assert report["package_id"] == PACKAGE_ID
    assert report["status"] == "UNKNOWN"
    assert report["graph_is_authority"] is False
    assert report["writes_as_graph_003"] is False
    assert report["merge_authorization"] == "NOT_GRANTED"


def test_declared_non_authority_is_derived(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(vault, {"project_id": "harbor-api", "note": "derived only"})
    report = compile_graph_authority(vault, "harbor-api")
    assert report["status"] == "derived"
    assert report["graph_is_authority"] is False
    assert report["accepted"] is True


def test_authority_winner_and_trust_fail_closed() -> None:
    with pytest.raises(Atlas3Error) as authority:
        prove_graph_is_not_authority({"graph_is_authority": True})
    assert authority.value.code == "GRAPH_AUTHORITY_CLAIMED"
    with pytest.raises(Atlas3Error) as winner:
        prove_graph_is_not_authority({"winner": "claim-a"})
    assert winner.value.code == "GRAPH_WINNER_CLAIMED"
    with pytest.raises(Atlas3Error) as trust:
        prove_graph_is_not_authority({"trust_score": 0.8})
    assert trust.value.code == "TRUST_SCORE_FORBIDDEN"


def test_cross_project_and_corrupt_fail_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(vault, {"project_id": "foreign"})
    with pytest.raises(Atlas3Error) as cross:
        compile_graph_authority(vault, "harbor-api")
    assert cross.value.code == "CROSS_PROJECT"
    path = (
        vault / "generated" / "ops" / "atlas3" / "graph-authority" / "harbor-api" / "declared.json"
    )
    path.write_text("{nope", encoding="utf-8")
    with pytest.raises(Atlas3Error) as corrupt:
        compile_graph_authority(vault, "harbor-api")
    assert corrupt.value.code == "GRAPH_AUTHORITY_CORRUPT"


def test_cli_graph_authority(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = _vault(tmp_path)
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_atlas3_parsers(sub)
    args = parser.parse_args(["graph-authority", "--vault", str(vault), "--project", "harbor-api"])
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
        parser.parse_args(["graph-authority", "--help"])
    assert info.value.code == 0
    help_text = capsys.readouterr().out
    collapsed = " ".join(help_text.split())
    assert "graph is not authority" in collapsed
    assert all(ord(char) < 128 for char in help_text)


def test_module_does_not_write() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/graph_authority.py").read_text(encoding="utf-8")
    for name in (
        "write_json_atomic",
        "write_text(",
        "chatgpt_bridge",
        "from project_atlas.ingestion",
        "from project_atlas.graph_relationships",
    ):
        assert name not in source
