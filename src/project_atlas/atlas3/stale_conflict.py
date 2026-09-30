"""AT3-081 — Isolated stale / conflict intelligence.

Composes Pulse stale/conflict questions and memory reconcile artifacts.
Does not add a CLI command. Does not pick a graph winner. Stale is not
current. Ledger integrity failures fail closed. MERGE_AUTHORIZATION
remains NOT_GRANTED.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from project_atlas.atlas3.contracts import (
    OPS_RELATIVE,
    TRUTH_BOUNDARY,
    Atlas3Error,
    honesty_block,
    require_project,
    require_vault,
)
from project_atlas.atlas3.ledger import list_events
from project_atlas.atlas3.memory.conflicts import detect_conflicts
from project_atlas.atlas3.memory.routing import assert_items_project_scope

PACKAGE_ID: Final[str] = "AT3-081"
GENERATOR_ID: Final[str] = "atlas3-stale-conflict-081"
PULSE_PACKAGE_ID: Final[str] = "AT3-015"
MEMORY_CONFLICT_PACKAGE_ID: Final[str] = "AT3-042"


def _pulse_path(vault: Path, project_id: str) -> Path:
    return vault / OPS_RELATIVE / "pulse" / f"{project_id}.json"


def _reconcile_path(vault: Path, project_id: str) -> Path:
    return vault / OPS_RELATIVE / "memory" / project_id / "reconcile.json"


def _read_artifact_object(path: Path, *, corrupt_code: str) -> dict[str, Any] | None:
    """Missing file stays absent. Existing corrupt/non-object artifacts fail closed."""
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Atlas3Error(corrupt_code, f"{path.name} is not readable JSON") from exc
    if not isinstance(raw, dict):
        raise Atlas3Error(corrupt_code, f"{path.name} must be an object")
    return raw


def _require_object_list(raw: object, *, label: str, code: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise Atlas3Error(code, f"{label} must be a list")
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise Atlas3Error(code, f"{label}[{index}] must be an object")
        rows.append(item)
    return rows


def _assert_artifact_project_scope(
    payload: dict[str, Any], *, project_id: str, label: str
) -> None:
    """Explicit foreign project_id fails closed. Unlabeled / null / whitespace stay allowed."""
    explicit = payload.get("project_id")
    if explicit is None:
        return
    text = str(explicit).strip()
    if not text:
        return
    if text != project_id:
        raise Atlas3Error(
            "PROJECT_MISMATCH",
            f"{label} project_id {explicit!r} != requested {project_id!r}",
        )


def _reject_authority_claims(payload: dict[str, Any], *, label: str) -> None:
    if payload.get("trust_score") is not None:
        raise Atlas3Error("TRUST_SCORE_FORBIDDEN", f"{label} must not carry a trust score")
    if payload.get("graph_is_authority") is True or payload.get("graph_winner") is not None:
        raise Atlas3Error("GRAPH_WINNER_FORBIDDEN", f"{label} must not select a graph winner")
    if payload.get("winner") is not None or payload.get("resolved_winner") is not None:
        raise Atlas3Error("WINNER_SELECTED", f"{label} must not pick a conflict winner")
    if payload.get("stale_as_current") is True or payload.get("stale_is_current") is True:
        raise Atlas3Error("STALE_AS_CURRENT", f"{label} must not treat stale as current")


def _stale_ledger_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in events:
        if not isinstance(item, dict):
            raise Atlas3Error("LEDGER_SCHEMA_INVALID", "ledger row must be an object")
        _reject_authority_claims(item, label="ledger-event")
        payload = item.get("payload") or {}
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise Atlas3Error("LEDGER_SCHEMA_INVALID", "ledger payload must be an object")
        _reject_authority_claims(payload, label="ledger-payload")
        freshness = str(payload.get("freshness") or item.get("freshness") or "")
        if freshness == "CURRENT" and (
            item.get("event_type") == "CONTEXT_INVALIDATED" or payload.get("stale") is True
        ):
            raise Atlas3Error("STALE_AS_CURRENT", "invalidated evidence must not be CURRENT")
        if freshness == "STALE" or item.get("event_type") == "CONTEXT_INVALIDATED":
            rows.append(item)
    return rows


def compile_stale_conflict_intel(vault: Path | str, project_id: str) -> dict[str, Any]:
    """Compose stale/conflict intelligence. Missing evidence stays UNKNOWN."""
    root = require_vault(vault)
    pid = require_project(root, project_id)
    events = list_events(root, pid)
    stale_ledger = _stale_ledger_rows(events)

    pulse = _read_artifact_object(_pulse_path(root, pid), corrupt_code="PULSE_CORRUPT")
    if pulse is not None:
        _assert_artifact_project_scope(pulse, project_id=pid, label="pulse")
        _reject_authority_claims(pulse, label="pulse")
        questions = pulse.get("questions")
        if questions is not None and not isinstance(questions, dict):
            raise Atlas3Error("PULSE_CORRUPT", "pulse questions must be an object")

    recon = _read_artifact_object(
        _reconcile_path(root, pid), corrupt_code="RECONCILE_CORRUPT"
    )
    memory_items: list[dict[str, Any]] = []
    memory_stale: list[dict[str, Any]] = []
    memory_conflicts: dict[str, Any] | None = None
    if recon is not None:
        _assert_artifact_project_scope(recon, project_id=pid, label="reconcile")
        _reject_authority_claims(recon, label="reconcile")
        nested = recon.get("reconciliation")
        if nested is not None and not isinstance(nested, dict):
            raise Atlas3Error("RECONCILE_CORRUPT", "reconciliation must be an object")
        block = nested if isinstance(nested, dict) else recon
        _assert_artifact_project_scope(block, project_id=pid, label="reconciliation")
        _reject_authority_claims(block, label="reconciliation")
        raw_items = block.get("items") if "items" in block else []
        memory_items = _require_object_list(
            raw_items if raw_items is not None else [],
            label="reconciliation items",
            code="RECONCILE_CORRUPT",
        )
        assert_items_project_scope(memory_items, project_id=pid)
        if "stale_memories" in block:
            memory_stale = _require_object_list(
                block.get("stale_memories") if block.get("stale_memories") is not None else [],
                label="stale_memories",
                code="RECONCILE_CORRUPT",
            )
            assert_items_project_scope(memory_stale, project_id=pid)
            if any(str(item.get("freshness") or "") == "CURRENT" for item in memory_stale):
                raise Atlas3Error("STALE_AS_CURRENT", "stale_memories must not be CURRENT")
        raw_conflicts = block.get("conflicts")
        if raw_conflicts is not None and not isinstance(raw_conflicts, dict):
            raise Atlas3Error("RECONCILE_CORRUPT", "conflicts must be an object")
        if isinstance(raw_conflicts, dict):
            _reject_authority_claims(raw_conflicts, label="conflicts")
            memory_conflicts = raw_conflicts
        elif memory_items:
            memory_conflicts = detect_conflicts(memory_items)

    pulse_questions = (pulse or {}).get("questions") if isinstance(pulse, dict) else None
    pulse_stale = (
        pulse_questions.get("what_became_stale")
        if isinstance(pulse_questions, dict)
        else None
    )
    pulse_conflicts = (
        pulse_questions.get("what_conflicts") if isinstance(pulse_questions, dict) else None
    )

    derived = bool(stale_ledger or memory_stale or memory_conflicts or pulse)
    return {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "generated": {"by": GENERATOR_ID},
        "data_package_ids": [PULSE_PACKAGE_ID, MEMORY_CONFLICT_PACKAGE_ID],
        "project_id": pid,
        "status": "derived" if derived else "UNKNOWN",
        "reason": "COMPOSED_STALE_CONFLICT" if derived else "NO_STALE_OR_CONFLICT_EVIDENCE",
        "stale": {
            "ledger": stale_ledger,
            "memory": memory_stale,
            "pulse": pulse_stale,
        },
        "conflicts": {
            "memory": memory_conflicts,
            "pulse": pulse_conflicts,
        },
        "counts": {
            "stale_ledger": len(stale_ledger),
            "stale_memory": len(memory_stale),
            "memory_items": len(memory_items),
        },
        "winner_selected": False,
        "graph_is_authority": False,
        "stale_as_current": False,
        "new_cli_command": False,
        "certified_for_merge": False,
        "merge_authorization": "NOT_GRANTED",
        "promoted_to_truth_core": 0,
        "write_applied": False,
        "truth_boundary": TRUTH_BOUNDARY,
        "honesty": honesty_block(),
    }
