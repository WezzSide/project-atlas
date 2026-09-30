"""AS-ROADMAP-UNREADABLE-PENDING-FALLBACK-001.

A present-but-corrupt ``review/pending/<project>.json`` must not fall back
to ``review/pending.json``. That resurrection is the D-047 hole the state
and unknown lenses already refuse. Missing scoped files may still use the
global queue (entry-owner scoped).
"""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.project_roadmap import build_roadmap_lens


def _write_roadmap(vault: Path, project_id: str) -> None:
    note = vault / "projects" / project_id / "roadmap.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "---\ntype: Roadmap\n---\n\n```json\n"
        + json.dumps({"items": [{"id": "a", "title": "A", "status": "IN_PROGRESS"}]})
        + "\n```\n",
        encoding="utf-8",
    )
    (vault / "projects" / project_id / "project.md").write_text(
        f"# {project_id}\n",
        encoding="utf-8",
    )


def test_unreadable_scoped_pending_does_not_resurrect_global(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_roadmap(vault, "harbor-api")
    scoped = vault / "review" / "pending" / "harbor-api.json"
    scoped.parent.mkdir(parents=True)
    scoped.write_text("{broken", encoding="utf-8")
    (vault / "review" / "pending.json").write_text(
        json.dumps(
            {
                "entries": [
                    {"project_id": "harbor-api", "status": "pending", "review_id": "stale-1"},
                    {"project_id": "harbor-api", "status": "pending", "review_id": "stale-2"},
                    {"project_id": "other-api", "status": "pending", "review_id": "foreign"},
                ]
            }
        ),
        encoding="utf-8",
    )
    lens = build_roadmap_lens(vault, "harbor-api")
    assert lens["pending_reviews"] == 0
    assert "pending_queue=unreadable" in lens["unknowns"]
    assert "pending reviews" not in lens["unknowns"]
    assert lens["honesty"]["pending_queue_unreadable"] is True
    assert any("pending-queue-unreadable" in note for note in lens["notes"])
    assert not any(
        blocker.get("waiting_on") == "review/pending" for blocker in lens["blockers"]
    )


def test_missing_scoped_pending_still_uses_global_owner_scope(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_roadmap(vault, "harbor-api")
    (vault / "review").mkdir(parents=True)
    (vault / "review" / "pending.json").write_text(
        json.dumps(
            {
                "entries": [
                    {"project_id": "harbor-api", "status": "pending", "review_id": "live-1"},
                    {"project_id": "other-api", "status": "pending", "review_id": "foreign"},
                ]
            }
        ),
        encoding="utf-8",
    )
    lens = build_roadmap_lens(vault, "harbor-api")
    assert lens["pending_reviews"] == 1
    assert "pending_queue=unreadable" not in lens["unknowns"]
    assert lens["honesty"]["pending_queue_unreadable"] is False


def test_unreadable_scoped_conflicts_do_not_use_unknown_lens(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_roadmap(vault, "harbor-api")
    scoped = vault / "review" / "conflicts" / "harbor-api.json"
    scoped.parent.mkdir(parents=True)
    scoped.write_text("{broken", encoding="utf-8")
    answers = vault / "generated" / "answers"
    answers.mkdir(parents=True)
    (answers / "ans-unknown-harbor-api.json").write_text(
        json.dumps({"unresolved_conflicts": 9, "status": "unknown"}),
        encoding="utf-8",
    )
    lens = build_roadmap_lens(vault, "harbor-api")
    assert lens["unresolved_conflicts"] == 0
    assert "conflicts_queue=unreadable" in lens["unknowns"]
    assert lens["honesty"]["conflicts_queue_unreadable"] is True
    assert any("conflicts-queue-unreadable" in note for note in lens["notes"])
