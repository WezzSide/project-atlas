"""Chronicle / Ambient Knowledge remains ROADMAP_HORIZON (D-193 §2)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_chronicle_is_design_notes_only() -> None:
    horizon = (ROOT / "docs" / "atlas-3" / "chronicle" / "HORIZON.md").read_text(encoding="utf-8")
    assert "ROADMAP_HORIZON" in horizon
    assert "Do not begin runtime implementation" in horizon
    assert not (ROOT / "src" / "project_atlas" / "atlas3" / "chronicle.py").exists()
    assert not (ROOT / "src" / "project_atlas" / "atlas3" / "chronicle").is_dir()
    runtime = ROOT / "src" / "project_atlas" / "atlas3"
    names = {path.name.lower() for path in runtime.iterdir()}
    assert "observe.py" not in names
    assert "moments.py" not in names
    assert "autojournal.py" not in names
