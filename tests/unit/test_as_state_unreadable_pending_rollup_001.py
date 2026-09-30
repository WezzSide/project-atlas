"""AS-STATE-UNREADABLE-PENDING-ROLLUP-001 — unreadable pending is not stable.

D-047 already requires state/unknown pending *counts* to agree and forbids
stale knowledge-status fallback. That test used no declared lifecycle, so
rollup stayed unknown. With lifecycle=active the state lens still reported
rollup=stable while status=unknown — a healthy current-state rollup over a
corrupt review queue.
"""

from __future__ import annotations

from pathlib import Path

from project_atlas.project_state import build_state_lens
from project_atlas.project_unknown import build_unknown_lens


def _vault_with_unreadable_pending(tmp_path: Path, *, lifecycle: str) -> Path:
    vault = tmp_path / "vault"
    pending = vault / "review" / "pending" / "harbor-api.json"
    pending.parent.mkdir(parents=True)
    pending.write_text("{broken", encoding="utf-8")
    project = vault / "projects" / "harbor-api"
    project.mkdir(parents=True)
    project.joinpath("project.md").write_text(
        "## Semantic record\n"
        f'```json\n{{"lifecycle":"{lifecycle}"}}\n```\n',
        encoding="utf-8",
    )
    project.joinpath("knowledge-status.md").write_text(
        "| Signal | Count |\n| --- | --- |\n"
        "| claims awaiting review | 9 |\n"
        "| sources complete | 3 |\n",
        encoding="utf-8",
    )
    return vault


def test_unreadable_pending_active_lifecycle_is_not_stable(tmp_path: Path) -> None:
    vault = _vault_with_unreadable_pending(tmp_path, lifecycle="active")
    state = build_state_lens(vault, "harbor-api")
    unknown = build_unknown_lens(vault, "harbor-api")

    assert state["status"] == "unknown"
    assert state["rollup"] != "stable"
    assert state["rollup"] == "unknown"
    assert "pending_queue=unreadable" in str(state.get("summary") or "")
    assert "pending-queue-unreadable" in " ".join(state.get("notes") or [])
    assert int((state.get("signals") or {}).get("pending_reviews") or 0) == int(
        (unknown.get("signals") or {}).get("pending_reviews") or 0
    )
    assert int((unknown.get("signals") or {}).get("pending_reviews") or 0) != 9
    assert unknown["status"] == "unknown"
    assert unknown["rollup"] == "unknown"


def test_unreadable_pending_keeps_attention_when_conflicts_exist(tmp_path: Path) -> None:
    vault = _vault_with_unreadable_pending(tmp_path, lifecycle="active")
    conflicts = vault / "review" / "conflicts" / "harbor-api.json"
    conflicts.parent.mkdir(parents=True)
    conflicts.write_text(
        '{"entries":[{"id":"c1","status":"open"}]}\n',
        encoding="utf-8",
    )
    state = build_state_lens(vault, "harbor-api")
    assert state["status"] == "unknown"
    assert state["rollup"] == "attention"
    assert state["rollup"] != "stable"
