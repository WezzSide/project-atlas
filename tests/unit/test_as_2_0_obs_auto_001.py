"""AS-2.0-OBS-UX-002 / AS-2.0-AUTONOMY-001 tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.autonomy_levels import (
    AutonomyLevelError,
    build_autonomy_level_catalog,
)
from project_atlas.obsidian_workspace import (
    ObsidianWorkspaceError,
    build_obsidian_workspace_binding,
)
from project_atlas.schema import available_schemas, validate_record


def test_obsidian_binding(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    report = build_obsidian_workspace_binding(vault, record_id="bind-a")
    assert report["plugin_shipped"] is False
    validate_record(report, "obsidian-workspace-binding")


def test_obsidian_rejects_plugin(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(ObsidianWorkspaceError, match="plugin-ship-forbidden"):
        build_obsidian_workspace_binding(vault, record_id="bind-a", ship_plugin=True)


def test_autonomy_catalog(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    report = build_autonomy_level_catalog(vault, record_id="auto-a")
    assert report["live_autonomy"] is False
    assert len(report["levels"]) == 6
    validate_record(report, "autonomy-level-catalog")


def test_autonomy_rejects_live(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(AutonomyLevelError, match="live-forbidden"):
        build_autonomy_level_catalog(vault, record_id="auto-a", enable_live_autonomy=True)


def test_docs() -> None:
    root = Path(__file__).resolve().parents[2]
    assert (root / "docs" / "AS-2.0-OBS-UX-002.md").is_file()
    assert (root / "docs" / "AS-2.0-AUTONOMY-001.md").is_file()
    assert "obsidian-workspace-binding" in available_schemas()
    assert "autonomy-level-catalog" in available_schemas()
