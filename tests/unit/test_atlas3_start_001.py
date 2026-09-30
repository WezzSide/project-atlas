"""AT3-030 Atlas Start."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.ledger import append_event
from project_atlas.atlas3.start import compile_start


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    return vault


def test_start_requires_budget(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    with pytest.raises(Atlas3Error) as exc:
        compile_start(vault, "harbor-api", token_budget=0)
    assert exc.value.code == "TOKEN_BUDGET_REQUIRED"


def test_start_is_budgeted_and_not_rag(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    briefing = compile_start(
        vault, "harbor-api", token_budget=240, current_task="Prepare Pulse demo"
    )
    assert briefing["rag_dump"] is False
    assert briefing["token_budget"] == 240
    assert briefing["tokens_remaining"] >= 0
    assert briefing["sections"]["current_task"]["text"].startswith("Prepare Pulse")
    assert "UNKNOWN" in briefing["sections"]["current_verified_truth"]["text"]
    assert len(briefing["sections"]["project_identity"]["text"]) <= 240
    assert briefing["freshness_requirement"] == "UNKNOWN"
    assert briefing["stale_presented_as_current"] is False


def test_start_current_refuses_stale_as_truth(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    append_event(
        vault,
        "harbor-api",
        event_type="CONTEXT_INVALIDATED",
        source_plane="engineering",
        summary="memory stale",
        payload={"freshness": "STALE"},
    )
    briefing = compile_start(
        vault,
        "harbor-api",
        token_budget=2000,
        freshness_requirement="CURRENT",
    )
    assert briefing["freshness_requirement"] == "CURRENT"
    assert briefing["stale_presented_as_current"] is False
    assert briefing["sections"]["current_verified_truth"]["status"] == "UNKNOWN"
    assert "stale evidence refused" in briefing["sections"]["current_verified_truth"]["text"]


def test_foreign_state_answer_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    answers = vault / "generated" / "answers"
    answers.mkdir(parents=True)
    (answers / "ans-state-harbor-api.json").write_text(
        json.dumps(
            {
                "status": "derived",
                "summary": "FOREIGN-STATE",
                "project_id": "other-api",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_start(vault, "harbor-api", token_budget=240)
    assert exc.value.code == "PROJECT_MISMATCH"
    assert not (vault / "generated" / "ops" / "atlas3" / "start" / "harbor-api.json").exists()


def test_start_unknown_freshness_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    with pytest.raises(Atlas3Error) as exc:
        compile_start(
            vault,
            "harbor-api",
            token_budget=64,
            freshness_requirement="FRESH_ENOUGH",
        )
    assert exc.value.code == "UNKNOWN_FRESHNESS_REQUIREMENT"
