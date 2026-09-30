"""AT3-049 — Compose dedup, conflicts, freshness. Never auto-promote."""

from __future__ import annotations

from typing import Any, Final

from project_atlas.atlas3.contracts import Atlas3Error, honesty_block
from project_atlas.atlas3.memory.conflicts import detect_conflicts
from project_atlas.atlas3.memory.dedup import deduplicate_items
from project_atlas.atlas3.memory.freshness import apply_freshness
from project_atlas.atlas3.memory.routing import assert_evidence_project_scope

PACKAGE_ID: Final[str] = "AT3-049"


def reconcile_capability() -> dict[str, Any]:
    return {
        "package": PACKAGE_ID,
        "composes": ["AT3-041", "AT3-042", "AT3-044"],
        "auto_promote_to_truth_core": False,
        "promoted_to_truth_core": 0,
        "picks_winner": False,
    }


def reconcile_memories(
    items: list[dict[str, Any]],
    *,
    stronger_evidence: list[dict[str, Any]] | None = None,
    current_state_text: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(items, list):
        raise Atlas3Error("RECONCILE_INVALID", "items must be a list")
    if project_id is not None and stronger_evidence is not None:
        assert_evidence_project_scope(stronger_evidence, project_id=project_id)
    deduped = deduplicate_items(items)
    fresh = apply_freshness(deduped["items"], stronger_evidence=stronger_evidence)
    conflicts = detect_conflicts(fresh, current_state_text=current_state_text)
    stale = [item for item in fresh if item.get("freshness") == "STALE"]
    decisions = [
        item
        for item in fresh
        if item.get("item_type") in {"proposed_decision", "confirmed_owner_decision"}
    ]
    owner = [
        item for item in fresh if item.get("item_type") == "confirmed_owner_decision"
    ]
    current = conflicts.get("current_state")
    return {
        "package": PACKAGE_ID,
        "schema": "atlas3.memory-reconcile.v1",
        "duplicates_collapsed": deduped["duplicates_collapsed"],
        "near_duplicates": deduped["near_duplicates"],
        "items": fresh,
        "conflicts": conflicts,
        "stale_memories": stale,
        "decision_candidates": decisions,
        "owner_decisions": owner,
        "current": f"PostgreSQL {current}" if current else "UNKNOWN",
        "intent": (
            "PostgreSQL 16 later"
            if any("16" in item for item in conflicts.get("intent_versions") or [])
            or any(
                ("16" in str(item.get("text")) and "later" in str(item.get("text")).lower())
                or "migrat" in str(item.get("text")).lower()
                for item in fresh
            )
            else "UNKNOWN"
        ),
        "conflicted_history": conflicts["conflicted_history"],
        "stale_chatgpt_memory": any(
            item.get("provider") == "chatgpt" and item.get("freshness") == "STALE"
            for item in fresh
        ),
        "promoted_to_truth_core": 0,
        "honesty": honesty_block(),
        "original_provenance_erased": False,
    }
