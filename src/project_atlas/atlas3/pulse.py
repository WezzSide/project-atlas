"""AT3-015 — Atlas Pulse derived lens.

Answers: what changed / matters / became stale / conflicts / failed /
was decided / requires attention / should I look at next.

Composes landed Coder Alpha answers + the Atlas 3 ledger.
Does not invent history. UNKNOWN stays UNKNOWN. Stale ≠ changed.
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
from project_atlas.atlas3.ledger import list_events

PACKAGE_ID: Final[str] = "AT3-015"
PULSE_QUESTIONS: Final[tuple[str, ...]] = (
    "what_changed",
    "what_matters",
    "what_became_stale",
    "what_conflicts",
    "what_failed",
    "what_was_decided",
    "what_requires_attention",
    "what_should_i_look_at_next",
)
FAILURE_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {"AGENT_FAILED", "TEST_FAILED", "INCIDENT_OPENED"}
)
DECISION_EVENT_TYPES: Final[frozenset[str]] = frozenset({"DECISION_RECORDED"})


def _unknown(reason: str) -> dict[str, Any]:
    return {"status": "UNKNOWN", "reason": reason, "items": []}


def _bind_answer(
    answer: dict[str, Any] | None,
    *,
    project_id: str,
    label: str,
) -> dict[str, Any] | None:
    """Unlabeled answers stay allowed. Explicit foreign project_id fails closed."""
    if answer is None:
        return None
    if not isinstance(answer, dict):
        raise Atlas3Error("PULSE_ANSWER_CORRUPT", f"{label} must be an object")
    explicit = answer.get("project_id")
    if explicit is not None and str(explicit) != project_id:
        raise Atlas3Error(
            "PROJECT_MISMATCH",
            f"{label} project_id {explicit!r} != requested {project_id!r}",
        )
    return answer


def _from_answer(
    answer: dict[str, Any] | None,
    *,
    missing: str,
    project_id: str,
    label: str,
) -> dict[str, Any]:
    bound = _bind_answer(answer, project_id=project_id, label=label)
    if bound is None:
        return _unknown(missing)
    status = str(bound.get("status") or bound.get("disposition") or "derived")
    return {
        "status": status if status else "derived",
        "items": [bound],
        "authority": "derived",
    }


def compile_pulse(vault: Any, project_id: str) -> dict[str, Any]:
    root = require_vault(vault)
    pid = require_project(root, project_id)
    changed = _bind_answer(
        load_answer(root, f"ans-changed-{pid}"),
        project_id=pid,
        label="ans-changed",
    )
    unknown = _bind_answer(
        load_answer(root, f"ans-unknown-{pid}"),
        project_id=pid,
        label="ans-unknown",
    )
    state = _bind_answer(
        load_answer(root, f"ans-state-{pid}"),
        project_id=pid,
        label="ans-state",
    )
    decisions = _bind_answer(
        load_answer(root, f"ans-decisions-{pid}"),
        project_id=pid,
        label="ans-decisions",
    )
    nxt = _bind_answer(
        load_answer(root, f"ans-next-{pid}"),
        project_id=pid,
        label="ans-next",
    )
    attention = _bind_answer(
        load_answer(root, f"ans-attention-{pid}"),
        project_id=pid,
        label="ans-attention",
    )
    events = list_events(root, pid)
    failures = [
        item
        for item in events
        if item.get("kind") in {"failure", "incident"}
        or item.get("event_type") in FAILURE_EVENT_TYPES
    ]
    decisions_ev = [
        item
        for item in events
        if item.get("kind") == "decision" or item.get("event_type") in DECISION_EVENT_TYPES
    ]
    stale_events = [
        item
        for item in events
        if (item.get("payload") or {}).get("freshness") == "STALE"
        or item.get("event_type") == "CONTEXT_INVALIDATED"
    ]
    conflict_block = _from_answer(
        unknown,
        missing="unknown/conflict lens not materialized",
        project_id=pid,
        label="ans-unknown",
    )
    failed_block = (
        {"status": "derived", "items": failures, "authority": "derived"}
        if failures
        else _unknown("no failure events in atlas3 ledger")
    )
    attention_items: list[Any] = []
    if attention is not None:
        attention_items.append(attention)
    if unknown is not None:
        attention_items.append(unknown)
    attention_items.extend(failures)
    attention_block = (
        {"status": "derived", "items": attention_items, "authority": "derived"}
        if attention_items
        else _unknown("no attention, conflict, or failure evidence")
    )

    questions = {
        "what_changed": _from_answer(
            changed,
            missing="changed lens not materialized",
            project_id=pid,
            label="ans-changed",
        ),
        "what_matters": _from_answer(
            attention or nxt,
            missing="attention/next lens not materialized",
            project_id=pid,
            label="ans-matters",
        ),
        "what_became_stale": (
            {"status": "derived", "items": stale_events, "authority": "derived"}
            if stale_events
            else _unknown("no stale ledger evidence")
        ),
        "what_conflicts": conflict_block,
        "what_failed": failed_block,
        "what_was_decided": (
            {"status": "derived", "items": decisions_ev, "authority": "derived"}
            if decisions_ev
            else _from_answer(
                decisions,
                missing="decisions lens not materialized",
                project_id=pid,
                label="ans-decisions",
            )
        ),
        "what_requires_attention": attention_block,
        "what_should_i_look_at_next": _from_answer(
            nxt,
            missing="next lens not materialized",
            project_id=pid,
            label="ans-next",
        ),
    }

    missing = [
        key for key, value in questions.items() if value.get("status") == "UNKNOWN"
    ]
    report = {
        "schema": "atlas3.pulse.v1",
        "schema_version": 1,
        "package": PACKAGE_ID,
        "project_id": pid,
        "state": load_answer(root, f"ans-state-{pid}") is not None,
        "questions": questions,
        "unknown_questions": missing,
        "ledger_event_count": len(events),
        "authority": "derived",
        "truth_boundary": TRUTH_BOUNDARY,
        "honesty": honesty_block(),
        "generated": {"by": GENERATOR_ID},
        "current_state_lens_present": state is not None,
    }
    write_json_atomic(root / OPS_RELATIVE / "pulse" / f"{pid}.json", report)
    return report
