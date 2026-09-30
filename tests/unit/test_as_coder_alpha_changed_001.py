"""AS-CODER-ALPHA-CHANGED-001 — What Changed derived lens."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.ask_atlas_live import ask_atlas_live
from project_atlas.cli import EXIT_OK, main
from project_atlas.connect import connect_project
from project_atlas.project_changed import materialize_changed_lenses
from project_atlas.web_api.knowledge import list_knowledge_answers


def _seed(root: Path, body: str = "v1") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text(f"# Changed Fixture\n\n{body}\n", encoding="utf-8")
    return root


def test_first_connect_establishes_baseline_unknown_history(tmp_path: Path) -> None:
    project = _seed(tmp_path / "chg-base")
    report = connect_project(project)
    vault = Path(report["vault"])
    project_id = str(report["bound_project_id"])
    answers = report.get("changed_answers") or []
    assert any(f"ans-changed-{project_id}.json" in path for path in answers)
    assert report.get("changed_delta", {}).get("prior_baseline") is False

    payload = json.loads(
        (vault / "generated" / "answers" / f"ans-changed-{project_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["rollup"] == "baseline"
    assert payload["status"] == "unknown"
    assert payload["value"] is None
    assert (vault / "generated" / "ops" / "connect-inventory.json").is_file()


def test_second_connect_reports_added_and_modified(tmp_path: Path) -> None:
    project = _seed(tmp_path / "chg-delta", body="v1")
    first = connect_project(project)
    project_id = str(first["bound_project_id"])
    (project / "README.md").write_text(
        "# Changed Fixture\n\nv2 modified\n",
        encoding="utf-8",
    )
    (project / "EXTRA.md").write_text("# Extra\n\nnew file\n", encoding="utf-8")
    second = connect_project(project)
    vault = Path(second["vault"])
    assert second.get("changed_delta", {}).get("prior_baseline") is True
    assert second.get("changed_delta", {}).get("added_count", 0) >= 1
    assert second.get("changed_delta", {}).get("modified_count", 0) >= 1

    payload = json.loads(
        (vault / "generated" / "answers" / f"ans-changed-{project_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["rollup"] == "changed"
    assert payload["delta"]["added_count"] >= 1
    assert payload["delta"]["modified_count"] >= 1
    assert "EXTRA.md" in payload["delta"]["added"]
    assert "README.md" in payload["delta"]["modified"]

    rows = list_knowledge_answers(vault)
    assert any(row["answer_id"] == f"ans-changed-{project_id}" for row in rows)
    live = ask_atlas_live(vault, query="What changed?")
    assert any(
        row.get("answer_id") == f"ans-changed-{project_id}" for row in live["matches"]["knowledge"]
    )


def test_second_connect_reports_removed_and_self_churn_not_dominating(
    tmp_path: Path,
) -> None:
    """D-040 positive-delta: add/mod/remove detected; .atlas* churn excluded."""
    project = _seed(tmp_path / "chg-remove", body="v1")
    (project / "KEEP.md").write_text("# Keep\n\nstay\n", encoding="utf-8")
    (project / "GONE.md").write_text("# Gone\n\nremove me\n", encoding="utf-8")
    first = connect_project(project)
    vault = Path(first["vault"])
    project_id = str(first["bound_project_id"])
    assert first.get("changed_delta", {}).get("prior_baseline") is False

    (project / "README.md").write_text("# Changed Fixture\n\nv2\n", encoding="utf-8")
    (project / "NEW.md").write_text("# New\n\nadded\n", encoding="utf-8")
    (project / "GONE.md").unlink()
    # Operational noise that must not dominate meaningful delta classification.
    (project / ".atlas-vault").mkdir(exist_ok=True)
    (project / ".atlas-vault" / "noise.md").write_text("vault noise\n", encoding="utf-8")

    second = connect_project(project)
    assert second.get("changed_delta", {}).get("prior_baseline") is True
    payload = json.loads(
        (vault / "generated" / "answers" / f"ans-changed-{project_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["rollup"] == "changed"
    assert "NEW.md" in payload["delta"]["added"]
    assert "README.md" in payload["delta"]["modified"]
    assert "GONE.md" in payload["delta"]["removed"]
    # Atlas vault outputs under DEFAULT_EXCLUDES must not appear as project changes.
    assert not any(
        path.startswith(".atlas-vault/")
        for path in (
            payload["delta"]["added"] + payload["delta"]["removed"] + payload["delta"]["modified"]
        )
    )


def test_cli_changed_reads_existing_inventory(tmp_path: Path) -> None:
    project = _seed(tmp_path / "cli-chg")
    connected = connect_project(project)
    vault = Path(connected["vault"])
    project_id = str(connected["bound_project_id"])
    assert main(["changed", "--vault", str(vault), "--project", project_id, "--json"]) == EXIT_OK
    report = materialize_changed_lenses(vault, project_ids=[project_id])
    assert report["package"] == "AS-CODER-ALPHA-CHANGED-001"
    assert "generated_at" not in report
