"""AS-CODER-ALPHA-OVERVIEW-001 — Project Overview derived lens."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.ask_atlas_live import ask_atlas_live
from project_atlas.cli import EXIT_OK, main
from project_atlas.connect import connect_project
from project_atlas.overview import build_overview_lens, materialize_overview_lenses
from project_atlas.web_api.knowledge import list_knowledge_answers


def _seed(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text(
        "# Harbor Portal\n\nPersistent brain for the harbor estate.\n",
        encoding="utf-8",
    )
    return root


def test_overview_materializes_after_connect_and_matches_ask_live(tmp_path: Path) -> None:
    project = _seed(tmp_path / "harbor-portal")
    report = connect_project(project)
    vault = Path(report["vault"])
    project_id = str(report["bound_project_id"])
    answers = report.get("overview_answers") or []
    assert answers
    assert any(f"ans-overview-{project_id}.json" in path for path in answers)

    rows = list_knowledge_answers(vault)
    assert rows
    overview = next(row for row in rows if row["answer_id"] == f"ans-overview-{project_id}")
    assert overview["title"] == "What is this project?"
    assert overview["summary"]
    assert "Harbor Portal" in (overview["summary"] or "")
    assert overview["has_value"] is True

    live = ask_atlas_live(vault, query="What is this project?")
    knowledge = live["matches"]["knowledge"]
    assert any(row.get("answer_id") == f"ans-overview-{project_id}" for row in knowledge)


def test_overview_prefers_root_readme_over_nested(tmp_path: Path) -> None:
    """D-038 dogfood: nested package READMEs must not win purpose/overview."""
    vault = tmp_path / "vault"
    project_id = "project-atlas"
    note = vault / "projects" / project_id / "project.md"
    note.parent.mkdir(parents=True)
    sources = [
        {
            "path": "deps/README.md",
            "source_id": "source-deps",
        },
        {
            "path": "apps/web/README.md",
            "source_id": "source-web",
        },
        {
            "path": "README.md",
            "source_id": "source-root",
        },
    ]
    note.write_text(
        "---\ntype: Project\ntitle: project-atlas\n---\n\n# project-atlas\n\n"
        "<!-- atlas:generated:start -->\n## Semantic record\n\n```json\n"
        + json.dumps({"project_id": project_id, "sources": sources, "coverage": []})
        + "\n```\n",
        encoding="utf-8",
    )
    imported = vault / "sources" / "imported-documents"
    imported.mkdir(parents=True)
    (imported / "source-deps.md").write_text(
        "# Research workspace\n\nNested deps package only.\n",
        encoding="utf-8",
    )
    (imported / "source-web.md").write_text(
        "# Web shell\n\nFrontend package README.\n",
        encoding="utf-8",
    )
    (imported / "source-root.md").write_text(
        "# Project Atlas\n\nPersistent brain for AI-native projects.\n",
        encoding="utf-8",
    )
    lens = build_overview_lens(vault, project_id)
    assert lens["status"] == "derived"
    assert "Project Atlas" in (lens["value"] or "")
    assert "Research workspace" not in (lens["value"] or "")
    assert any(item.endswith("selected:README.md") for item in lens["inspected_artifacts"])


def test_overview_unknown_without_readme_prose(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    project_id = "empty-proj"
    note = vault / "projects" / project_id / "project.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "---\ntype: Project\ntitle: empty-proj\n---\n\n# empty-proj\n\n"
        "<!-- atlas:generated:start -->\n## Semantic record\n\n```json\n"
        '{"project_id":"empty-proj","sources":[],"coverage":[]}\n```\n',
        encoding="utf-8",
    )
    lens = build_overview_lens(vault, project_id)
    assert lens["status"] == "unknown"
    assert lens["value"] is None
    assert lens["summary"] is None


def test_cli_overview_writes_lens(tmp_path: Path) -> None:
    project = _seed(tmp_path / "cli-ov")
    connected = connect_project(project)
    vault = Path(connected["vault"])
    project_id = str(connected["bound_project_id"])
    # Remove auto-written answer then regenerate via CLI.
    answer = vault / "generated" / "answers" / f"ans-overview-{project_id}.json"
    answer.unlink()
    assert main(["overview", "--vault", str(vault), "--project", project_id, "--json"]) == EXIT_OK
    assert answer.is_file()
    payload = json.loads(answer.read_text(encoding="utf-8"))
    assert payload["package"] == "AS-CODER-ALPHA-OVERVIEW-001"
    assert "generated_at" not in payload
    assert payload["generated"]["by"] == "atlas-coder-alpha-overview-001"


def test_materialize_is_idempotent(tmp_path: Path) -> None:
    project = _seed(tmp_path / "idem")
    vault = Path(connect_project(project)["vault"])
    first = materialize_overview_lenses(vault)
    second = materialize_overview_lenses(vault)
    assert first["answers_written"] == second["answers_written"]
    path = vault / first["answers_written"][0]
    assert path.read_text(encoding="utf-8") == (
        json.dumps(first["lenses"][0], indent=2, sort_keys=True) + "\n"
    )
