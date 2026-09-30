"""AS-SEC-SCAN-DISCOVER-YAML-001 — scan decoded YAML marker ids before manifest persist."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.cli import EXIT_ERROR, EXIT_OK, main
from project_atlas.discovery import discover, write_manifest
from project_atlas.secrets import scan_text

TOKEN = "AKIAAAAAAAAAAAAAAAAA"  # AKIA + 16 A — matches cloud-access-key


def _project_with_id(tmp_path: Path, raw_id_line: str) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".atlas-project.yaml").write_text(
        "schema_version: 1\n"
        "project:\n"
        f"  id: {raw_id_line}\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("# hello\n", encoding="utf-8")
    return root


def test_quoted_unicode_escape_akia_is_not_inventoried(tmp_path: Path) -> None:
    raw_id = '"\\u0041KIAAAAAAAAAAAAAAAAA"'
    root = _project_with_id(tmp_path, raw_id)
    raw = (root / ".atlas-project.yaml").read_text(encoding="utf-8")
    assert scan_text(raw) == []
    assert TOKEN not in raw
    output = tmp_path / "manifest.json"
    with pytest.raises(ValueError, match="secret-shaped project identifier"):
        discover(root)
    assert not output.is_file()


def test_quoted_hex_escape_akia_is_not_inventoried(tmp_path: Path) -> None:
    raw_id = '"\\x41KIAAAAAAAAAAAAAAAAA"'
    root = _project_with_id(tmp_path, raw_id)
    raw = (root / ".atlas-project.yaml").read_text(encoding="utf-8")
    assert scan_text(raw) == []
    with pytest.raises(ValueError, match="secret-shaped project identifier"):
        discover(root)


def test_clean_marker_id_still_discovers(tmp_path: Path) -> None:
    root = _project_with_id(tmp_path, "sample-estate")
    manifest = discover(root)
    output = tmp_path / "manifest.json"
    write_manifest(manifest, output)
    owners = {
        row.get("likely_project")
        for row in manifest["sources"]
        if isinstance(row, dict)
    }
    assert "sample-estate" in owners
    assert TOKEN not in output.read_text(encoding="utf-8")
    assert TOKEN not in "".join(str(item) for item in owners)


def test_cli_unicode_escape_akia_writes_no_manifest(tmp_path: Path) -> None:
    root = _project_with_id(tmp_path, '"\\u0041KIAAAAAAAAAAAAAAAAA"')
    output = tmp_path / "manifest.json"
    assert main(["discover", "--source", str(root), "--output", str(output)]) == EXIT_ERROR
    assert not output.is_file()


def test_cli_clean_marker_still_writes_manifest(tmp_path: Path) -> None:
    root = _project_with_id(tmp_path, "sample-estate")
    output = tmp_path / "manifest.json"
    assert main(["discover", "--source", str(root), "--output", str(output)]) == EXIT_OK
    assert output.is_file()
    assert TOKEN not in output.read_text(encoding="utf-8")
    assert "sample-estate" in output.read_text(encoding="utf-8")
