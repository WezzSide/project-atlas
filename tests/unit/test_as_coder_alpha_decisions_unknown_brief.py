"""AS-CODER-ALPHA-DECISIONS/UNKNOWN/BRIEF-001 coverage."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.ask_atlas_live import ask_atlas_live
from project_atlas.cli import EXIT_OK, main
from project_atlas.connect import connect_project
from project_atlas.project_brief import materialize_project_briefs
from project_atlas.project_decisions import materialize_decisions_lenses
from project_atlas.secrets import scan_text
from project_atlas.web_api.knowledge import list_knowledge_answers


def _seed(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text(
        "# Brief Fixture\n\nPersistent brain dogfood.\n\n## Stack\n\nPython + Obsidian.\n",
        encoding="utf-8",
    )
    (root / "docs").mkdir()
    (root / "docs" / "DECISIONS.md").write_text(
        "# Decisions\n\n## Use OKF\nWe decided to use OKF.\n\n## Local-first\nOffline required.\n",
        encoding="utf-8",
    )
    return root


def test_connect_emits_decisions_unknown_and_brief(tmp_path: Path) -> None:
    project = _seed(tmp_path / "brief-fixture")
    report = connect_project(project)
    vault = Path(report["vault"])
    project_id = str(report["bound_project_id"])

    assert any(f"ans-decisions-{project_id}.json" in p for p in report["decisions_answers"])
    assert any(f"ans-unknown-{project_id}.json" in p for p in report["unknown_answers"])
    assert any(f"project-brief-{project_id}.json" in p for p in report["brief_paths"])

    decisions = json.loads(
        (vault / "generated" / "answers" / f"ans-decisions-{project_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert decisions["status"] == "derived"
    assert decisions["decision_count"] >= 2
    titles = {item["title"] for item in decisions["decisions"]}
    assert "Use OKF" in titles
    assert "Local-first" in titles

    unknown = json.loads(
        (vault / "generated" / "answers" / f"ans-unknown-{project_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert unknown["rollup"] in {"unknown", "review", "conflict", "clear"}
    assert "lifecycle=unknown" in " ".join(unknown["signals"]["unknown_items"])

    brief = json.loads(
        (vault / "generated" / "ops" / f"project-brief-{project_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert brief["project_identity"] == project_id
    assert brief["purpose"] != "UNKNOWN"
    assert "Python" in brief["tech_stack"]
    assert brief["important_decisions"] != "UNKNOWN"
    assert brief["unknown_or_conflicting"] != "UNKNOWN"
    assert brief["suggested_next_work"]
    assert brief["honesty"]["fabricated_fields"] is False

    ids = {row["answer_id"] for row in list_knowledge_answers(vault)}
    assert f"ans-decisions-{project_id}" in ids
    assert f"ans-unknown-{project_id}" in ids
    live = ask_atlas_live(vault, query="What decisions matter?")
    assert any(
        row.get("answer_id") == f"ans-decisions-{project_id}"
        for row in live["matches"]["knowledge"]
    )


def test_cli_brief_and_decisions(tmp_path: Path) -> None:
    project = _seed(tmp_path / "cli-brief")
    connected = connect_project(project)
    vault = Path(connected["vault"])
    project_id = str(connected["bound_project_id"])
    assert main(["decisions", "--vault", str(vault), "--json"]) == EXIT_OK
    assert main(["unknown", "--vault", str(vault), "--json"]) == EXIT_OK
    assert (
        main(
            [
                "brief",
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
    receipt = materialize_project_briefs(vault, project_ids=[project_id], refresh=False)
    assert receipt["package"] == "AS-CODER-ALPHA-BRIEF-001"
    assert "generated_at" not in receipt


def test_decisions_unknown_without_decision_docs(tmp_path: Path) -> None:
    root = tmp_path / "sparse"
    root.mkdir()
    (root / "README.md").write_text("# Sparse\n\nNo decisions file.\n", encoding="utf-8")
    report = connect_project(root)
    vault = Path(report["vault"])
    project_id = str(report["bound_project_id"])
    decisions = json.loads(
        (vault / "generated" / "answers" / f"ans-decisions-{project_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert decisions["status"] == "unknown"
    assert decisions["value"] is None
    brief = json.loads(
        (vault / "generated" / "ops" / f"project-brief-{project_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert brief["important_decisions"] == "UNKNOWN"
    next_work = brief["suggested_next_work"]
    assert any("DECISIONS" in item or "decision" in item.lower() for item in next_work)


def test_json_unicode_escape_claim_is_not_persisted(tmp_path: Path) -> None:
    """AS-SEC-SCAN-DECISIONS-JSON-ESC-001: decoded claim secrets must not persist."""
    token = "AKIAAAAAAAAAAAAAAAAA"
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    claims = vault / "state" / "claims"
    claims.mkdir(parents=True)
    raw = (
        '{"schema_version":1,"claims":[{"claim_id":"\\u0041KIAAAAAAAAAAAAAAAAA",'
        '"claim_type":"decision","value":"We will adopt \\u0041KIAAAAAAAAAAAAAAAAA",'
        '"project_id":"harbor-api","verification":"verified"}]}'
    )
    (claims / "harbor-api.json").write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    materialize_decisions_lenses(vault, project_ids=["harbor-api"])
    answer = (
        vault / "generated" / "answers" / "ans-decisions-harbor-api.json"
    ).read_text(encoding="utf-8")
    assert token not in answer
    assert scan_text(answer) == []
