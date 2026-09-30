"""AS-CODER-ALPHA-HUMAN-LOOP-F1 — invented conflict winners fail closed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.human_loop import (
    HumanLoopError,
    accepted_claim_ids,
    apply_review_decision,
    decisions_path,
)


def _seed_conflict(vault: Path, project_id: str) -> None:
    pending = vault / "review" / "pending" / f"{project_id}.json"
    pending.parent.mkdir(parents=True)
    pending.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_id": project_id,
                "entries": [
                    {
                        "review_id": "rev-1",
                        "status": "pending",
                        "category": "conflict",
                        "subject_id": "conflict-1",
                        "claim_ids": ["claim-a", "claim-b"],
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    conflicts = vault / "review" / "conflicts" / f"{project_id}.json"
    conflicts.parent.mkdir(parents=True)
    conflicts.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_id": project_id,
                "entries": [
                    {
                        "conflict_id": "conflict-1",
                        "claim_ids": ["claim-a", "claim-b"],
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    decisions_path(vault, project_id).parent.mkdir(parents=True, exist_ok=True)
    decisions_path(vault, project_id).write_text(
        json.dumps(
            {"schema_version": 1, "project_id": project_id, "decisions": []},
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def test_invented_winner_is_refused(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_conflict(vault, "harbor-api")
    with pytest.raises(HumanLoopError, match="not a claim on the pending conflict"):
        apply_review_decision(
            vault,
            project_id="harbor-api",
            review_id="rev-1",
            decision="accept",
            reason="owner said so",
            winner_claim_id="claim-not-in-conflict",
        )
    assert accepted_claim_ids(vault, "harbor-api") == set()


def test_bound_winner_is_accepted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_conflict(vault, "harbor-api")
    report = apply_review_decision(
        vault,
        project_id="harbor-api",
        review_id="rev-1",
        decision="accept",
        reason="owner chose claim-a",
        winner_claim_id="claim-a",
    )
    assert report["status"] == "ok"
    assert accepted_claim_ids(vault, "harbor-api") == {"claim-a"}
