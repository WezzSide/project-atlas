"""AS-CODER-ALPHA-NEXT-001 — daily What Next derived lens."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.agent_handoff import export_agent_context
from project_atlas.cli import EXIT_ERROR, EXIT_OK, main
from project_atlas.connect import connect_project
from project_atlas.project_brief import build_project_brief
from project_atlas.project_next import (
    PACKAGE_ID,
    ProjectNextError,
    build_next_lens,
    derive_next_lenses,
    materialize_next_lenses,
)


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, (dict, list)):
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        path.write_text(str(payload), encoding="utf-8")


def _write_roadmap(vault: Path, project_id: str, record: dict[str, object]) -> None:
    note = vault / "projects" / project_id / "roadmap.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "---\ntype: Roadmap\n---\n\n# Roadmap\n\n## Roadmap record\n\n```json\n"
        + json.dumps(record)
        + "\n```\n",
        encoding="utf-8",
    )
    (vault / "projects" / project_id / "project.md").write_text(
        f"---\ntype: Project\ntitle: {project_id}\n---\n\n# {project_id}\n",
        encoding="utf-8",
    )


def _unlock_record() -> dict[str, object]:
    return {
        "items": [
            {
                "id": "pkg-done",
                "title": "Already verified",
                "status": "VERIFIED_COMPLETION",
                "depends_on": [],
                "evidence": ["generated/ops/receipts/done.json"],
            },
            {
                "id": "pkg-next",
                "title": "Ship daily next lens",
                "status": "IN_PROGRESS",
                "depends_on": ["pkg-done"],
                "evidence": [],
            },
        ]
    }


def test_unknown_project_and_unsafe_id_fail_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "projects").mkdir()
    with pytest.raises(ProjectNextError, match="unknown project"):
        build_next_lens(vault, "missing-project")
    with pytest.raises(ProjectNextError):
        build_next_lens(vault, "../escape")


def test_empty_project_is_unknown_not_invented(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_roadmap(vault, "empty-proj", {"items": []})
    lens = build_next_lens(vault, "empty-proj")
    assert lens["package"] == PACKAGE_ID
    assert lens["primary"]["kind"] == "unknown"
    assert lens["primary"]["title"] == "UNKNOWN"
    assert lens["honesty"]["next_is_authority"] is False
    assert lens["honesty"]["next_is_command"] is False
    assert lens["honesty"]["not_as_2_0_next_001"] is True
    assert lens["why_cannot_advance"] is None
    assert "generated_at" not in lens


def test_roadmap_unlock_when_no_blockers(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_roadmap(vault, "road-proj", _unlock_record())
    lens = build_next_lens(vault, "road-proj")
    assert lens["primary"]["kind"] == "roadmap_unlock"
    assert lens["primary"]["subject_id"] == "pkg-next"
    assert "Ship daily next lens" in str(lens["primary"]["title"])
    assert lens["status"] == "derived"
    assert lens["honesty"]["auto_execution"] is False


def test_blocking_attention_outranks_roadmap(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_roadmap(vault, "blocked-proj", _unlock_record())
    _write(
        vault / "review" / "conflicts" / "blocked-proj.json",
        {
            "entries": [
                {
                    "conflict_id": "c-datastore",
                    "conflict_type": "competing-claim",
                    "field": "datastore",
                    "project_id": "blocked-proj",
                }
            ]
        },
    )
    lens = build_next_lens(vault, "blocked-proj")
    assert lens["primary"]["kind"] == "blocking_attention"
    assert lens["why_cannot_advance"]
    assert any(item["kind"] == "roadmap_unlock" for item in lens["queue"])


def test_project_isolation_no_cross_leak(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_roadmap(vault, "proj-a", _unlock_record())
    _write_roadmap(vault, "proj-b", _unlock_record())
    _write(
        vault / "review" / "conflicts" / "proj-a.json",
        {
            "entries": [
                {
                    "conflict_id": "secret-a",
                    "conflict_type": "competing-claim",
                    "field": "SECRET-PORTAL-VALUE",
                    "project_id": "proj-a",
                }
            ]
        },
    )
    lens_b = build_next_lens(vault, "proj-b")
    blob = json.dumps(lens_b, sort_keys=True)
    assert "SECRET-PORTAL-VALUE" not in blob
    assert "secret-a" not in blob
    assert lens_b["primary"]["kind"] == "roadmap_unlock"


def test_missing_decisions_signal_preserved(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    project_id = "sparse-decisions"
    (vault / "projects" / project_id).mkdir(parents=True)
    _write(
        vault / "generated" / "answers" / f"ans-decisions-{project_id}.json",
        {"status": "unknown", "answer_id": f"ans-decisions-{project_id}"},
    )
    lens = build_next_lens(vault, project_id)
    assert any(item["kind"] == "missing_decisions" for item in lens["queue"])
    assert any(
        "DECISIONS" in line or "decision" in line.lower() for line in lens["suggested_next_work"]
    )


def test_materialize_is_deterministic_and_answers_only(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_roadmap(vault, "det-proj", _unlock_record())
    first = materialize_next_lenses(vault, project_ids=["det-proj"])
    second = materialize_next_lenses(vault, project_ids=["det-proj"])
    path = vault / "generated" / "answers" / "ans-next-det-proj.json"
    assert path.is_file()
    assert first["lenses"][0] == second["lenses"][0]
    assert path.read_bytes() == (
        json.dumps(first["lenses"][0], indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    assert not (vault / "projects" / "det-proj" / "truth-core.json").exists()
    read_only = derive_next_lenses(vault, project_ids=["det-proj"])
    assert read_only["answers_written"] == []
    assert read_only["honesty"]["read_only"] is True


def test_module_does_not_import_frozen_intelligence() -> None:
    source = Path("src/project_atlas/project_next.py").read_text(encoding="utf-8")
    assert "project_atlas.intelligence" not in source
    assert "web_api.intelligence" not in source
    assert "/v1/intelligence" not in source


def test_cli_next_read_only_and_help(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = tmp_path / "vault"
    _write_roadmap(vault, "cli-proj", _unlock_record())
    with pytest.raises(SystemExit) as excinfo:
        main(["next", "--help"])
    assert excinfo.value.code == 0
    assert main(["next", "--vault", str(vault), "--project", "cli-proj", "--read-only"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "NEXT!=AUTHORITY" in out
    assert "Ship daily next lens" in out
    assert not (vault / "generated" / "answers" / "ans-next-cli-proj.json").exists()
    assert main(["next", "--vault", str(vault), "--project", "missing-project"]) == EXIT_ERROR


def test_connect_materializes_next_and_brief_uses_it(tmp_path: Path) -> None:
    root = tmp_path / "daily"
    root.mkdir()
    (root / "README.md").write_text("# Daily next\n\nPersistent brain.\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "DECISIONS.md").write_text(
        "# Decisions\n\n## Keep next derived\nNext is not a command.\n",
        encoding="utf-8",
    )
    report = connect_project(root)
    vault = Path(report["vault"])
    project_id = str(report["bound_project_id"])
    assert any(f"ans-next-{project_id}.json" in path for path in report.get("next_answers") or [])
    lens = json.loads(
        (vault / "generated" / "answers" / f"ans-next-{project_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert lens["package"] == PACKAGE_ID
    assert lens["honesty"]["next_is_command"] is False
    brief = build_project_brief(vault, project_id, refresh=False)
    assert brief["lenses"]["next"] == f"ans-next-{project_id}"
    assert brief["suggested_next_work"]
    ctx = export_agent_context(vault, project_id, refresh_brief=False)
    markdown = (vault / ctx["markdown_path"]).read_text(encoding="utf-8")
    payload = json.loads((vault / ctx["json_path"]).read_text(encoding="utf-8"))
    assert "## What next (derived)" in markdown
    assert payload["next"]["package"] == PACKAGE_ID
    assert "next_is_command: false" in markdown
