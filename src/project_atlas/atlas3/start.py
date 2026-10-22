"""AT3-030 — Atlas Start bounded context briefing.

Requires an explicit token/context budget. No arbitrary RAG dump.
"""

from __future__ import annotations

from typing import Any, Final

from project_atlas.atlas3.contracts import (
    GENERATOR_ID,
    OPS_RELATIVE,
    TRUTH_BOUNDARY,
    Atlas3Error,
    honesty_block,
    load_answer,
    require_project,
    require_vault,
    write_json_atomic,
)
from project_atlas.atlas3.pulse import compile_pulse

PACKAGE_ID: Final[str] = "AT3-030"
FRESHNESS_REQUIREMENTS: Final[frozenset[str]] = frozenset(
    {"CURRENT", "ALLOW_STALE_HISTORICAL", "UNKNOWN"}
)
START_SECTIONS: Final[tuple[str, ...]] = (
    "project_identity",
    "current_verified_truth",
    "recent_material_changes",
    "relevant_decisions",
    "open_conflicts",
    "open_unknowns",
    "stale_context",
    "current_task",
    "owner_constraints",
    "recent_failures",
    "next_relevant_actions",
)


def _clip(text: str, budget: int) -> tuple[str, int]:
    if budget <= 0:
        return ("", 0)
    if len(text) <= budget:
        return (text, budget - len(text))
    return (text[: budget - 1] + "…", 0)


def _section(status: str, text: str, *, remaining: int) -> tuple[dict[str, Any], int]:
    clipped, left = _clip(text, remaining)
    return (
        {
            "status": status if clipped else "UNKNOWN",
            "text": clipped or "UNKNOWN",
            "authority": "derived",
        },
        left,
    )


def compile_start(
    vault: Any,
    project_id: str,
    *,
    token_budget: int,
    current_task: str | None = None,
    freshness_requirement: str = "UNKNOWN",
) -> dict[str, Any]:
    if token_budget <= 0:
        raise Atlas3Error(
            "TOKEN_BUDGET_REQUIRED",
            "atlas start requires an explicit positive --budget / token_budget",
        )
    freshness = freshness_requirement.strip().upper() or "UNKNOWN"
    if freshness not in FRESHNESS_REQUIREMENTS:
        raise Atlas3Error(
            "UNKNOWN_FRESHNESS_REQUIREMENT",
            f"unsupported freshness requirement {freshness_requirement!r}",
        )
    root = require_vault(vault)
    pid = require_project(root, project_id)
    pulse = compile_pulse(root, pid)
    remaining = token_budget
    per_section = max(8, token_budget // len(START_SECTIONS))
    sections: dict[str, Any] = {}

    def take(status: str, text: str) -> dict[str, Any]:
        nonlocal remaining
        allowance = min(per_section, remaining) if remaining else 0
        block, leftover_in_allowance = _section(status, text, remaining=allowance)
        unused = leftover_in_allowance
        remaining = max(0, remaining - (allowance - unused))
        return block

    identity_text = f"project_id={pid}"
    sections["project_identity"] = take("derived", identity_text)

    state = load_answer(root, f"ans-state-{pid}")
    stale_block = (pulse.get("questions") or {}).get("what_became_stale") or {}
    stale_items = stale_block.get("items") or []
    truth_text = "UNKNOWN — state lens not materialized"
    truth_status = "UNKNOWN"
    if freshness == "CURRENT" and stale_items and state is None:
        truth_text = "UNKNOWN — stale evidence refused as current truth"
        truth_status = "UNKNOWN"
    elif state is not None:
        state_status = str(state.get("status") or "").upper()
        summary = state.get("summary") or state.get("title")
        if state_status == "UNKNOWN" or not summary:
            truth_text = "UNKNOWN — state lens has no verified summary"
            truth_status = "UNKNOWN"
        else:
            truth_text = str(summary)
            truth_status = "derived"
    sections["current_verified_truth"] = take(truth_status, truth_text)

    mapping = (
        ("recent_material_changes", "what_changed"),
        ("relevant_decisions", "what_was_decided"),
        ("open_conflicts", "what_conflicts"),
        ("open_unknowns", "what_conflicts"),
        ("stale_context", "what_became_stale"),
        ("recent_failures", "what_failed"),
        ("next_relevant_actions", "what_should_i_look_at_next"),
    )
    questions = pulse.get("questions") or {}
    for section_key, pulse_key in mapping:
        block = questions.get(pulse_key) or {}
        status = str(block.get("status") or "UNKNOWN")
        items = block.get("items") or []
        if items:
            text = f"{status}: {len(items)} evidence item(s)"
        else:
            text = str(block.get("reason") or "UNKNOWN")
            status = "UNKNOWN"
        sections[section_key] = take(status, text)

    if current_task and current_task.strip():
        sections["current_task"] = take("derived", current_task.strip())
    else:
        sections["current_task"] = take(
            "UNKNOWN",
            "UNKNOWN — current task was not supplied",
        )

    constraints = load_answer(root, f"ans-decisions-{pid}")
    if constraints is None:
        sections["owner_constraints"] = take(
            "UNKNOWN",
            "UNKNOWN — owner constraints not materialized",
        )
    else:
        sections["owner_constraints"] = take(
            "derived",
            "decisions lens present; not automatically owner constraints",
        )

    briefing = {
        "schema": "atlas3.start.v1",
        "schema_version": 1,
        "package": PACKAGE_ID,
        "project_id": pid,
        "token_budget": token_budget,
        "tokens_remaining": remaining,
        "freshness_requirement": freshness,
        "stale_presented_as_current": False,
        "rag_dump": False,
        "sections": sections,
        "section_order": list(START_SECTIONS),
        "authority": "derived",
        "truth_boundary": TRUTH_BOUNDARY,
        "honesty": honesty_block(),
        "generated": {"by": GENERATOR_ID},
    }
    write_json_atomic(root / OPS_RELATIVE / "start" / f"{pid}.json", briefing)
    return briefing
