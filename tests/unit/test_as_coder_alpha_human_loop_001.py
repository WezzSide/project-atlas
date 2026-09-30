"""AS-CODER-ALPHA-HUMAN-LOOP-001 coverage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.cli import EXIT_ERROR, EXIT_OK, main
from project_atlas.connect import connect_project
from project_atlas.human_loop import HumanLoopError, apply_review_decision


def _seed(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text(
        "# Human Loop Fixture\n\nPurpose: review decide.\n\n## Stack\n\nPython.\n",
        encoding="utf-8",
    )
    (root / "docs").mkdir()
    (root / "docs" / "DECISIONS.md").write_text(
        "# Decisions\n\n## Keep reviews human\nHumans decide accept/reject.\n",
        encoding="utf-8",
    )
    return root


def _first_pending_review_id(vault: Path, project_id: str) -> str:
    path = vault / "review" / "pending" / f"{project_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for entry in payload.get("entries") or []:
        if isinstance(entry, dict) and entry.get("status") == "pending":
            return str(entry["review_id"])
    raise AssertionError("expected at least one pending review")


def test_review_decide_accept_and_unknown_drop(tmp_path: Path) -> None:
    project = _seed(tmp_path / "human-loop")
    connected = connect_project(project)
    vault = Path(connected["vault"])
    project_id = str(connected["bound_project_id"])
    review_id = _first_pending_review_id(vault, project_id)
    report = apply_review_decision(
        vault,
        project_id=project_id,
        review_id=review_id,
        decision="accept",
        reason="Owner verified claim from DECISIONS.md",
    )
    assert report["status"] == "ok"
    disposition = json.loads((vault / report["disposition_path"]).read_text(encoding="utf-8"))
    assert disposition["decisions"]
    pending = json.loads(
        (vault / "review" / "pending" / f"{project_id}.json").read_text(encoding="utf-8")
    )
    decided = next(e for e in pending["entries"] if e["review_id"] == review_id)
    assert decided["status"] == "resolved"
    unknown = json.loads(
        (vault / "generated" / "answers" / f"ans-unknown-{project_id}.json").read_text(
            encoding="utf-8"
        )
    )
    pending_left = sum(
        1
        for entry in pending["entries"]
        if isinstance(entry, dict) and entry.get("status") == "pending"
    )
    assert unknown["signals"]["pending_reviews"] == pending_left

    with pytest.raises(HumanLoopError):
        apply_review_decision(
            vault,
            project_id=project_id,
            review_id=review_id,
            decision="accept",
            reason="duplicate",
        )


def test_review_decide_fail_closed(tmp_path: Path) -> None:
    project = _seed(tmp_path / "loop-safety")
    connected = connect_project(project)
    vault = Path(connected["vault"])
    project_id = str(connected["bound_project_id"])
    with pytest.raises(HumanLoopError):
        apply_review_decision(
            vault,
            project_id=project_id,
            review_id="review-does-not-exist",
            decision="accept",
            reason="nope",
        )
    with pytest.raises(HumanLoopError):
        apply_review_decision(
            vault,
            project_id="../escape",
            review_id="review-x",
            decision="reject",
            reason="nope",
        )


def test_cli_review_decide(tmp_path: Path) -> None:
    project = _seed(tmp_path / "cli-loop")
    connected = connect_project(project)
    vault = Path(connected["vault"])
    project_id = str(connected["bound_project_id"])
    review_id = _first_pending_review_id(vault, project_id)
    assert (
        main(
            [
                "review",
                "decide",
                "--vault",
                str(vault),
                "--project",
                project_id,
                "--review-id",
                review_id,
                "--decision",
                "reject",
                "--reason",
                "Not evidenced enough",
                "--json",
            ]
        )
        == EXIT_OK
    )
    assert (
        main(
            [
                "review",
                "decide",
                "--vault",
                str(vault),
                "--project",
                project_id,
                "--review-id",
                "missing-review",
                "--decision",
                "accept",
                "--reason",
                "x",
            ]
        )
        == EXIT_ERROR
    )
