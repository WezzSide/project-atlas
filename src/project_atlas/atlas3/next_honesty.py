"""AT3-082 — Isolated next-action honesty.

Composes Pulse "what should I look at next" and the landed next-lens
answer without inventing a command. Does not invoke the Pulse compiler
(Pulse writes). NEXT != command. Stale/unverified stay honest. Missing
stays UNKNOWN. Graph != authority. MERGE_AUTHORIZATION = NOT_GRANTED.
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

PACKAGE_ID: Final[str] = "AT3-082"
GENERATOR_ID: Final[str] = "atlas3-next-honesty-082"
PULSE_PACKAGE_ID: Final[str] = "AT3-015"
NEXT_LENS_PACKAGE_ID: Final[str] = "CODER-ALPHA-NEXT"


def _pulse_path(vault: Path, project_id: str) -> Path:
    return vault / OPS_RELATIVE / "pulse" / f"{project_id}.json"


def _next_lens_path(vault: Path, project_id: str) -> Path:
    return vault / "generated" / "answers" / f"ans-next-{project_id}.json"


def _read_object(path: Path, *, corrupt_code: str, label: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise Atlas3Error(corrupt_code, f"{label} must be a regular file")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Atlas3Error(corrupt_code, f"{label} is not valid JSON") from exc
    if not isinstance(raw, dict):
        raise Atlas3Error(corrupt_code, f"{label} must be an object")
    return raw


def _reject_authority_claims(payload: dict[str, Any], *, label: str) -> None:
    if payload.get("trust_score") is not None:
        raise Atlas3Error("TRUST_SCORE_FORBIDDEN", f"{label} must not carry a trust score")
    if payload.get("graph_is_authority") is True or payload.get("graph_winner") is not None:
        raise Atlas3Error("GRAPH_WINNER_FORBIDDEN", f"{label} must not select a graph winner")
    if payload.get("winner") is not None or payload.get("resolved_winner") is not None:
        raise Atlas3Error("WINNER_SELECTED", f"{label} must not pick a winner")
    if payload.get("merge_authorization") in {"GRANTED", "granted", True}:
        raise Atlas3Error("MERGE_CLAIM_FORBIDDEN", f"{label} must not grant merge")
    if payload.get("command") is not None or payload.get("cli_command") is not None:
        raise Atlas3Error("NEXT_IS_COMMAND", f"{label} must not emit a command")
    if payload.get("execute") is True or payload.get("auto_execute") is True:
        raise Atlas3Error("NEXT_IS_COMMAND", f"{label} must not auto-execute")
    freshness = str(payload.get("freshness") or "")
    status = str(payload.get("status") or "")
    if freshness == "STALE" and status in {"CURRENT", "current", "verified"}:
        raise Atlas3Error("STALE_AS_CURRENT", f"{label} must not treat stale as current")
    if payload.get("stale_as_current") is True or payload.get("stale_is_current") is True:
        raise Atlas3Error("STALE_AS_CURRENT", f"{label} must not treat stale as current")
    if payload.get("unverified") is True and status in {"verified", "CURRENT", "current"}:
        raise Atlas3Error("UNVERIFIED_AS_CURRENT", f"{label} must not treat unverified as current")


def _walk_reject(payload: Any, *, label: str) -> None:
    if isinstance(payload, dict):
        _reject_authority_claims(payload, label=label)
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                _walk_reject(value, label=f"{label}.{key}")
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            if isinstance(item, (dict, list)):
                _walk_reject(item, label=f"{label}[{index}]")


def _extract_next_text(block: dict[str, Any] | None) -> str | None:
    if not isinstance(block, dict):
        return None
    for key in ("value", "text", "next", "summary"):
        raw = block.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    items = block.get("items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                text = _extract_next_text(item)
                if text:
                    return text
            elif isinstance(item, str) and item.strip():
                return item.strip()
    return None


def _bind_project(
    payload: dict[str, Any] | None,
    *,
    project_id: str,
    label: str,
) -> dict[str, Any] | None:
    """Unlabeled artifacts stay allowed. Explicit foreign project_id fails closed."""
    if payload is None:
        return None
    explicit = payload.get("project_id")
    if explicit is not None and str(explicit) != project_id:
        raise Atlas3Error(
            "PROJECT_MISMATCH",
            f"{label} project_id {explicit!r} != requested {project_id!r}",
        )
    return payload


def compile_next_action_honesty(vault: Path | str, project_id: str) -> dict[str, Any]:
    """Project next-action honesty from Pulse / next-lens. Does not invent a command."""
    root = require_vault(vault)
    pid = require_project(root, project_id)

    pulse = _bind_project(
        _read_object(_pulse_path(root, pid), corrupt_code="PULSE_CORRUPT", label="pulse"),
        project_id=pid,
        label="pulse",
    )
    if pulse is not None:
        _walk_reject(pulse, label="pulse")
        questions = pulse.get("questions")
        if questions is not None and not isinstance(questions, dict):
            raise Atlas3Error("PULSE_CORRUPT", "pulse questions must be an object")

    next_lens = _bind_project(
        _read_object(
            _next_lens_path(root, pid),
            corrupt_code="NEXT_LENS_CORRUPT",
            label="next-lens",
        ),
        project_id=pid,
        label="next-lens",
    )
    if next_lens is not None:
        _walk_reject(next_lens, label="next-lens")

    pulse_questions = pulse.get("questions") if isinstance(pulse, dict) else None
    pulse_next = (
        pulse_questions.get("what_should_i_look_at_next")
        if isinstance(pulse_questions, dict)
        else None
    )
    if pulse_next is not None and not isinstance(pulse_next, dict):
        raise Atlas3Error("PULSE_CORRUPT", "pulse next-action must be an object")
    if isinstance(pulse_next, dict):
        _bind_project(pulse_next, project_id=pid, label="pulse.next")
        raw_items = pulse_next.get("items")
        if isinstance(raw_items, list):
            for index, item in enumerate(raw_items):
                if isinstance(item, dict):
                    _bind_project(item, project_id=pid, label=f"pulse.next.items[{index}]")
        _walk_reject(pulse_next, label="pulse.next")

    next_text = _extract_next_text(pulse_next) or _extract_next_text(next_lens)
    pulse_status = ""
    if isinstance(pulse_next, dict):
        pulse_status = str(pulse_next.get("status") or "")
    lens_status = ""
    if next_lens is not None:
        lens_status = str(next_lens.get("status") or next_lens.get("disposition") or "")

    if pulse is None and next_lens is None:
        status = "UNKNOWN"
        reason = "NO_NEXT_EVIDENCE"
    elif pulse_status == "UNKNOWN" and not next_text and not next_lens:
        status = "UNKNOWN"
        reason = "PULSE_NEXT_UNKNOWN"
    elif next_text:
        status = "derived"
        reason = "COMPOSED_NEXT_ACTION"
    elif pulse_status == "UNKNOWN" or (not pulse_status and not lens_status):
        status = "UNKNOWN"
        reason = "NEXT_LENS_NOT_MATERIALIZED"
    else:
        status = "derived"
        reason = "COMPOSED_NEXT_ACTION"

    return {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "generated": {"by": GENERATOR_ID},
        "data_package_ids": [PULSE_PACKAGE_ID, NEXT_LENS_PACKAGE_ID],
        "project_id": pid,
        "next": next_text,
        "status": status,
        "reason": reason,
        "sources": {
            "pulse_artifact_present": pulse is not None,
            "next_lens_present": next_lens is not None,
            "pulse_next": pulse_next,
            "next_lens": next_lens,
        },
        "next_is_command": False,
        "auto_execute": False,
        "new_cli_command": False,
        "graph_is_authority": False,
        "stale_as_current": False,
        "certified_for_merge": False,
        "merge_authorization": "NOT_GRANTED",
        "promoted_to_truth_core": 0,
        "write_applied": False,
        "truth_boundary": TRUTH_BOUNDARY,
        "honesty": honesty_block(),
    }
