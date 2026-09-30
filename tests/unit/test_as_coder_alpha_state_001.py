"""AS-CODER-ALPHA-STATE-001 — Current State derived lens."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.ask_atlas_live import ask_atlas_live
from project_atlas.cli import EXIT_OK, main
from project_atlas.connect import connect_project
from project_atlas.project_state import build_state_lens, materialize_state_lenses
from project_atlas.web_api.knowledge import list_knowledge_answers


def _seed(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text(
        "# State Fixture\n\nCurrent-state dogfood seed.\n",
        encoding="utf-8",
    )
    return root


def test_state_materializes_after_connect(tmp_path: Path) -> None:
    project = _seed(tmp_path / "state-fixture")
    report = connect_project(project)
    vault = Path(report["vault"])
    project_id = str(report["bound_project_id"])
    answers = report.get("state_answers") or []
    assert any(f"ans-state-{project_id}.json" in path for path in answers)

    rows = list_knowledge_answers(vault)
    state_row = next(row for row in rows if row["answer_id"] == f"ans-state-{project_id}")
    assert state_row["title"] == "What is the current state?"
    assert state_row["summary"]
    assert "rollup=" in (state_row["summary"] or "")
    assert state_row["has_value"] is True

    live = ask_atlas_live(vault, query="current state")
    knowledge = live["matches"]["knowledge"]
    assert any(row.get("answer_id") == f"ans-state-{project_id}" for row in knowledge)


def test_state_rollup_attention_on_conflicts(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    project_id = "conflicted"
    note = vault / "projects" / project_id / "project.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "---\ntype: Project\ntitle: conflicted\n---\n\n# conflicted\n\n"
        "<!-- atlas:generated:start -->\n## Semantic record\n\n```json\n"
        '{"project_id":"conflicted","lifecycle":"active","sources":[],'
        '"coverage":[]}\n```\n',
        encoding="utf-8",
    )
    status = vault / "projects" / project_id / "knowledge-status.md"
    status.write_text(
        "# Knowledge status — conflicted\n\n"
        "| Signal | Count |\n|---|---:|\n"
        "| unresolved conflicts | 2 |\n"
        "| claims awaiting review | 1 |\n"
        "| stale claims | 0 |\n"
        "| sources complete | 4 |\n"
        "| sources failed | 0 |\n"
        "| verified claims | 3 |\n",
        encoding="utf-8",
    )
    pending = vault / "review" / "pending" / f"{project_id}.json"
    pending.parent.mkdir(parents=True)
    pending.write_text(
        json.dumps({"schema_version": 1, "project_id": project_id, "entries": [{"id": "r1"}]}),
        encoding="utf-8",
    )
    conflicts = vault / "review" / "conflicts" / f"{project_id}.json"
    conflicts.parent.mkdir(parents=True)
    conflicts.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_id": project_id,
                "entries": [{"id": "c1"}, {"id": "c2"}],
            }
        ),
        encoding="utf-8",
    )
    lens = build_state_lens(vault, project_id)
    assert lens["rollup"] == "attention"
    assert lens["signals"]["unresolved_conflicts"] == 2
    assert lens["signals"]["pending_reviews"] == 1
    assert lens["lifecycle"] == "active"


def test_cli_state_writes_lens(tmp_path: Path) -> None:
    project = _seed(tmp_path / "cli-state")
    connected = connect_project(project)
    vault = Path(connected["vault"])
    project_id = str(connected["bound_project_id"])
    answer = vault / "generated" / "answers" / f"ans-state-{project_id}.json"
    answer.unlink()
    assert main(["state", "--vault", str(vault), "--project", project_id, "--json"]) == EXIT_OK
    assert answer.is_file()
    payload = json.loads(answer.read_text(encoding="utf-8"))
    assert payload["package"] == "AS-CODER-ALPHA-STATE-001"
    assert "generated_at" not in payload
    assert payload["generated"]["by"] == "atlas-coder-alpha-state-001"


def test_materialize_state_idempotent(tmp_path: Path) -> None:
    project = _seed(tmp_path / "idem-state")
    vault = Path(connect_project(project)["vault"])
    first = materialize_state_lenses(vault)
    second = materialize_state_lenses(vault)
    assert first["answers_written"] == second["answers_written"]
    path = vault / first["answers_written"][0]
    assert path.read_text(encoding="utf-8") == (
        json.dumps(first["lenses"][0], indent=2, sort_keys=True) + "\n"
    )
