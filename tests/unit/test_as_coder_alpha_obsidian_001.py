"""AS-CODER-ALPHA-OBSIDIAN-001 coverage."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.cli import EXIT_OK, main
from project_atlas.connect import connect_project
from project_atlas.obsidian_projection import (
    ObsidianProjectionError,
    materialize_obsidian_projection,
    project_note_path,
)


def _seed(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text(
        "# Obsidian Fixture\n\nLiving knowledge seed.\n\n## Stack\n\nPython.\n",
        encoding="utf-8",
    )
    return root


def test_obsidian_living_note_and_human_preserve(tmp_path: Path) -> None:
    project = _seed(tmp_path / "obs-fixture")
    connected = connect_project(project)
    vault = Path(connected["vault"])
    project_id = str(connected["bound_project_id"])
    note = project_note_path(vault, project_id)
    assert note.is_file()
    text = note.read_text(encoding="utf-8")
    assert "Living knowledge" in text
    assert "plugin_shipped: false" in text
    assert "UNKNOWN stays UNKNOWN" in text
    assert "<!-- BEGIN HUMAN: notes -->" in text

    humanized = text.replace(
        "<!-- BEGIN HUMAN: notes -->\n<!-- END HUMAN: notes -->",
        "<!-- BEGIN HUMAN: notes -->\nOwner: keep this sentence.\n<!-- END HUMAN: notes -->",
    )
    note.write_text(humanized, encoding="utf-8")
    report = materialize_obsidian_projection(vault, project_id=project_id, refresh_brief=False)
    assert report["status"] == "ok"
    refreshed = note.read_text(encoding="utf-8")
    assert "Owner: keep this sentence." in refreshed
    assert "atlas:generated:start" in refreshed


def test_obsidian_fail_closed_malformed_markers(tmp_path: Path) -> None:
    project = _seed(tmp_path / "obs-bad")
    connected = connect_project(project)
    vault = Path(connected["vault"])
    project_id = str(connected["bound_project_id"])
    note = project_note_path(vault, project_id)
    note.write_text(
        note.read_text(encoding="utf-8") + "\n<!-- BEGIN HUMAN: notes -->\n",
        encoding="utf-8",
    )
    with pytest.raises(ObsidianProjectionError):
        materialize_obsidian_projection(vault, project_id=project_id, refresh_brief=False)


def test_cli_obsidian_project(tmp_path: Path) -> None:
    project = _seed(tmp_path / "cli-obs")
    connected = connect_project(project)
    vault = Path(connected["vault"])
    project_id = str(connected["bound_project_id"])
    assert (
        main(
            [
                "obsidian",
                "project",
                "--vault",
                str(vault),
                "--project",
                project_id,
                "--no-refresh",
                "--json",
            ]
        )
        == EXIT_OK
    )
