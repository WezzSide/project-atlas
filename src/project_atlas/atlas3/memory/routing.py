"""AT3 memory project routing — fail-closed cross-project guards."""

from __future__ import annotations

from typing import Any

from project_atlas.atlas3.contracts import Atlas3Error, safe_project_id


def require_memory_project(project_id: str) -> str:
    """Validate requested project token before any memory work."""
    return safe_project_id(project_id)


def assert_turns_project_scope(
    turns: list[dict[str, Any]],
    *,
    project_id: str,
) -> str:
    """Reject turns whose explicit project_id differs from the governed scope."""
    pid = require_memory_project(project_id)
    for index, turn in enumerate(turns):
        if not isinstance(turn, dict):
            raise Atlas3Error("MALFORMED_TURN", f"turn {index} must be an object")
        explicit = turn.get("project_id")
        if explicit is not None and str(explicit) != pid:
            raise Atlas3Error(
                "PROJECT_MISMATCH",
                f"turn project_id {explicit!r} != requested {pid!r}",
            )
        meta = turn.get("provider_metadata")
        if isinstance(meta, dict):
            forged = meta.get("project_id") or meta.get("bound_project_id")
            if forged is not None and str(forged) != pid:
                raise Atlas3Error(
                    "PROJECT_MISMATCH",
                    "provider_metadata cannot override governed project routing",
                )
    return pid


def assert_items_project_scope(
    items: list[dict[str, Any]],
    *,
    project_id: str,
) -> str:
    """Atomic batch guard: every extracted item must match requested project."""
    pid = require_memory_project(project_id)
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise Atlas3Error("MALFORMED_ITEM", f"item {index} must be an object")
        item_pid = item.get("project_id")
        if item_pid is None:
            raise Atlas3Error(
                "PROJECT_MISMATCH",
                f"item {index} missing project_id under governed routing",
            )
        if str(item_pid) != pid:
            raise Atlas3Error(
                "PROJECT_MISMATCH",
                f"item project_id {item_pid!r} != requested {pid!r}",
            )
    return pid


def assert_evidence_project_scope(
    evidence: object,
    *,
    project_id: str,
) -> str:
    """Reject evidence rows that name a foreign project. Unlabeled rows stay allowed."""
    pid = require_memory_project(project_id)
    if not isinstance(evidence, list):
        raise Atlas3Error("MALFORMED_ITEM", "evidence must be a list")
    for index, row in enumerate(evidence):
        if not isinstance(row, dict):
            raise Atlas3Error("MALFORMED_ITEM", f"evidence {index} must be an object")
        explicit = row.get("project_id")
        if explicit is not None and str(explicit) != pid:
            raise Atlas3Error(
                "PROJECT_MISMATCH",
                f"evidence project_id {explicit!r} != requested {pid!r}",
            )
    return pid
