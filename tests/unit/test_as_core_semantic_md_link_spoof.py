"""AS-CORE-002-F1 — project Sources lines must not be spoofable Markdown links.

Independently reproduced on main b87b4a22: path ``a](b.md`` rendered as
``[a](b.md](...)`` so Markdown display text and target diverged. Synthetic
filename only. Does not mutate ingestion.py (F5-B freeze).
"""

from __future__ import annotations

from project_atlas.domain.semantic import ProjectRecord
from project_atlas.semantic_compiler import render_project_record


def test_render_project_record_does_not_spoof_markdown_link() -> None:
    record = ProjectRecord(project_id="demo", name="Demo")
    text = render_project_record(
        record,
        [
            {
                "path": "a](b.md",
                "source": "../../sources/imported-documents/s1.md",
                "classification": "project-overview",
                "sha256": "ab" * 32,
            }
        ],
    )
    assert "- [a](b.md]" not in text
    assert "[a](b.md" not in text
    assert "`a](b.md`" in text
    assert "`../../sources/imported-documents/s1.md`" in text


def test_render_project_record_honest_path_stays_visible() -> None:
    record = ProjectRecord(project_id="demo", name="Demo")
    text = render_project_record(
        record,
        [
            {
                "path": "README.md",
                "source": "../../sources/imported-documents/readme.md",
                "classification": "project-overview",
                "sha256": "cd" * 32,
            }
        ],
    )
    assert "`README.md`" in text
    assert "`../../sources/imported-documents/readme.md`" in text
