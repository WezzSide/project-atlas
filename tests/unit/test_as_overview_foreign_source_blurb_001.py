"""AS-OVERVIEW-FOREIGN-SOURCE-BLURB-001 — foreign source_id is not purpose.

``_readme_blurb`` opened ``sources/imported-documents/{source_id}.md`` for
any ``source_id`` listed on ``project.md``. A harbor overview could quote
a portal README when connect-manifest owned that id.
"""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.overview import build_overview_lens


def test_foreign_source_id_is_not_overview_purpose(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    pid = "harbor-api"
    note = vault / "projects" / pid / "project.md"
    note.parent.mkdir(parents=True)
    sources = [{"path": "README.md", "source_id": "portal-readme"}]
    note.write_text(
        "---\ntype: Project\ntitle: harbor-api\n---\n\n# harbor-api\n\n"
        "<!-- atlas:generated:start -->\n## Semantic record\n\n```json\n"
        + json.dumps({"project_id": pid, "sources": sources, "coverage": []})
        + "\n```\n",
        encoding="utf-8",
    )
    imported = vault / "sources" / "imported-documents"
    imported.mkdir(parents=True)
    (imported / "portal-readme.md").write_text(
        "# Harbor Portal\n\nSIBLING SECRET PURPOSE for the portal estate.\n",
        encoding="utf-8",
    )
    manifest = vault / "generated" / "ops" / "connect-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_id": "portal-readme",
                        "path": "README.md",
                        "likely_project": "portal",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    lens = build_overview_lens(vault, pid)
    text = f"{lens.get('summary')} {lens.get('value')}"
    assert "SIBLING SECRET PURPOSE" not in text
    assert "Harbor Portal" not in text


def test_owned_source_id_still_supplies_blurb(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    pid = "harbor-api"
    note = vault / "projects" / pid / "project.md"
    note.parent.mkdir(parents=True)
    sources = [{"path": "README.md", "source_id": "harbor-readme"}]
    note.write_text(
        "---\ntype: Project\ntitle: harbor-api\n---\n\n# harbor-api\n\n"
        "<!-- atlas:generated:start -->\n## Semantic record\n\n```json\n"
        + json.dumps({"project_id": pid, "sources": sources, "coverage": []})
        + "\n```\n",
        encoding="utf-8",
    )
    imported = vault / "sources" / "imported-documents"
    imported.mkdir(parents=True)
    (imported / "harbor-readme.md").write_text(
        "# Harbor API\n\nOwned purpose blurb.\n",
        encoding="utf-8",
    )
    manifest = vault / "generated" / "ops" / "connect-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_id": "harbor-readme",
                        "path": "README.md",
                        "likely_project": "harbor-api",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    lens = build_overview_lens(vault, pid)
    assert "Owned purpose blurb" in (lens.get("summary") or "")
