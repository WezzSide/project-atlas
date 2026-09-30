"""AT3-010 — isolated repository / component inventory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from project_atlas.atlas3.cli import dispatch_atlas3, register_atlas3_parsers
from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.inventory import PACKAGE_ID, compile_inventory


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    return vault


def _write_declared(vault: Path, payload: dict[str, object]) -> None:
    path = vault / "generated" / "ops" / "atlas3" / "inventory" / "harbor-api" / "declared.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_unknown_project_fails_closed(tmp_path: Path) -> None:
    vault = tmp_path / "empty"
    vault.mkdir()
    with pytest.raises(Atlas3Error) as exc:
        compile_inventory(vault, "harbor-api")
    assert exc.value.code == "UNKNOWN_PROJECT"


def test_missing_declared_stays_unknown(tmp_path: Path) -> None:
    report = compile_inventory(_vault(tmp_path), "harbor-api")
    assert report["package"] == PACKAGE_ID
    assert report["status"] == "UNKNOWN"
    assert report["reason"] == "NO_DECLARED_INVENTORY"
    assert report["counts"] == {"repositories": 0, "components": 0}
    assert report["inventory_is_truth_core"] is False
    assert report["authentic_estate"] is False
    assert report["promoted_to_truth_core"] == 0


def test_declared_inventory_is_derived(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "repositories": [
                {"id": "harbor-api-repo", "evidence_refs": ["src:harbor-api/.atlas-project.yaml"]}
            ],
            "components": [{"id": "api", "evidence_refs": ["doc:architecture.md#api"]}],
        },
    )
    report = compile_inventory(vault, "harbor-api")
    assert report["status"] == "derived"
    assert report["counts"]["repositories"] == 1
    assert report["counts"]["components"] == 1
    assert report["repositories"][0]["authority"] == "derived"
    assert report["honesty"]["graph_is_authority"] is False


def test_cross_project_declared_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(vault, {"project_id": "foreign", "repositories": []})
    with pytest.raises(Atlas3Error) as exc:
        compile_inventory(vault, "harbor-api")
    assert exc.value.code == "CROSS_PROJECT"


def test_missing_provenance_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(vault, {"project_id": "harbor-api", "repositories": [{"id": "repo"}]})
    with pytest.raises(Atlas3Error) as exc:
        compile_inventory(vault, "harbor-api")
    assert exc.value.code == "PROVENANCE_REQUIRED"


def test_corrupt_and_mixed_rows_fail_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    path = vault / "generated" / "ops" / "atlas3" / "inventory" / "harbor-api" / "declared.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(Atlas3Error) as exc:
        compile_inventory(vault, "harbor-api")
    assert exc.value.code == "INVENTORY_CORRUPT"
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "components": [{"id": "api", "evidence_refs": ["doc:a"]}, "corrupt"],
        },
    )
    with pytest.raises(Atlas3Error) as mixed:
        compile_inventory(vault, "harbor-api")
    assert mixed.value.code == "INVENTORY_CORRUPT"


def test_authority_claims_fail_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(vault, {"project_id": "harbor-api", "authentic_estate": True})
    with pytest.raises(Atlas3Error) as exc:
        compile_inventory(vault, "harbor-api")
    assert exc.value.code == "INVENTORY_AUTHORITY_CLAIMED"
    _write_declared(vault, {"project_id": "harbor-api", "merge_authorization": "GRANTED"})
    with pytest.raises(Atlas3Error) as merge:
        compile_inventory(vault, "harbor-api")
    assert merge.value.code == "INVENTORY_AUTHORITY_CLAIMED"


def test_cli_inventory_unknown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = _vault(tmp_path)
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_atlas3_parsers(sub)
    args = parser.parse_args(["inventory", "--vault", str(vault), "--project", "harbor-api"])
    assert dispatch_atlas3(args) == 0
    rendered = capsys.readouterr().out
    payload = json.loads(rendered)
    assert payload["status"] == "UNKNOWN"
    assert payload["package"] == PACKAGE_ID
    assert all(ord(char) < 128 for char in rendered)


def test_cli_help_is_ascii(capsys: pytest.CaptureFixture[str]) -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_atlas3_parsers(sub)
    with pytest.raises(SystemExit) as info:
        parser.parse_args(["inventory", "--help"])
    assert info.value.code == 0
    help_text = capsys.readouterr().out
    assert "Truth Core" in help_text
    assert all(ord(char) < 128 for char in help_text)


def test_module_does_not_touch_2x_surfaces() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/inventory.py").read_text(encoding="utf-8")
    for name in (
        "chatgpt_bridge",
        "discovery",
        "ingestion",
        "knowledge_compiler",
        "project_architecture",
        "write_text(",
        "write_json_atomic",
    ):
        assert name not in source
