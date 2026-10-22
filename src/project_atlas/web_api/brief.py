"""AS-CODER-ALPHA-WEB-001 / TRUTH-UX-001 — read-only project brief for Web.

Loads Core-emitted brief/lens/capture/review artifacts. Never materializes
lenses, never imports knowledge_compiler, never writes Layer B.

Truth UX exposes provenance/status labels without confidence theatre:
evidence != interpretation; UNKNOWN != healthy; UI != canonical.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from atlas_contracts.identity import safe_relative_component
from project_atlas.conversation_capture import list_conversation_captures
from project_atlas.web_api.conflicts import list_project_conflicts
from project_atlas.web_api.knowledge import (
    answer_filename_project,
    list_knowledge_answers,
)

PACKAGE_ID = "AS-CODER-ALPHA-WEB-001"
TRUTH_PACKAGE_ID = "AS-CODER-ALPHA-TRUTH-UX-001"
BRIEF_OPS = Path("generated") / "ops"
CAPTURE_DIR = Path("generated") / "ops" / "session-captures"
HUMAN_DECISIONS = Path("state") / "human-decisions"
PENDING_REVIEWS = Path("review") / "pending"
LENS_FIELDS = ("overview", "state", "changed", "decisions", "unknown")


class WebBriefError(ValueError):
    """Fail-closed web brief read error."""


def _safe_project_id(project_id: str) -> str:
    try:
        return safe_relative_component(project_id, label="project id")
    except ValueError as exc:
        raise WebBriefError(str(exc)) from exc


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _unknown_brief(project_id: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "schema": "atlas.coder-alpha.project-brief.v1",
        "package": PACKAGE_ID,
        "project_id": project_id,
        "project_identity": project_id,
        "purpose": "UNKNOWN",
        "tech_stack": "UNKNOWN",
        "architecture_summary": "UNKNOWN",
        "current_state": "UNKNOWN",
        "recent_meaningful_changes": "UNKNOWN",
        "important_decisions": "UNKNOWN",
        "known_problems": "UNKNOWN",
        "unknown_or_conflicting": "UNKNOWN",
        "suggested_next_work": [],
        "evidence_links": [],
        "lenses": {},
        "session_captures": [],
        "conversation_captures": [],
        "available": False,
        "generated": {"by": "atlas-coder-alpha-web-001-read"},
        "honesty": {
            "authentic_pilot": False,
            "atlas_opt_wake_gate": "CLOSED",
            "lens_is_authority": False,
            "ui_is_canonical": False,
            "unknown_is_valid": True,
            "fabricated_fields": False,
            "confidence_theatre": False,
        },
        "notes": [
            "Read-only Web projection; brief file absent",
            "UI!=canonical",
            "UNKNOWN!=healthy",
        ],
    }


def _list_captures(vault: Path, project_id: str, *, limit: int = 8) -> list[dict[str, Any]]:
    root = vault / CAPTURE_DIR
    if not root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(root.glob("capture-*.json"), reverse=True):
        payload = _read_json(path)
        if not payload or payload.get("project_id") != project_id:
            continue
        items.append(
            {
                "capture_id": payload.get("capture_id"),
                "kind": payload.get("kind"),
                "summary": payload.get("summary"),
                "source": payload.get("source"),
                "path": path.relative_to(vault).as_posix(),
                "status": "ops_receipt",
                "authority": False,
            }
        )
        if len(items) >= limit:
            break
    return items


def _lens_rows(vault: Path, project_id: str) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for answer in list_knowledge_answers(vault):
        if answer.get("subject") != project_id:
            continue
        if answer_filename_project(str(answer.get("path") or "")) != project_id:
            continue
        field = answer.get("field")
        if not isinstance(field, str):
            continue
        key = field
        if field in {"unknown_conflicts", "unknown"}:
            key = "unknown"
        elif field.startswith("project_"):
            key = field.removeprefix("project_")
        if key in LENS_FIELDS and key not in rows:
            rows[key] = {
                "answer_id": answer.get("answer_id"),
                "title": answer.get("title"),
                "summary": answer.get("summary") or "UNKNOWN",
                "value_text": answer.get("value_text") or answer.get("summary") or "UNKNOWN",
                "path": answer.get("path"),
                "field": field,
                "status": "derived_lens",
                "verified": False,
                "authority": False,
            }
    return rows


def _pending_reviews(vault: Path, project_id: str, *, limit: int = 12) -> list[dict[str, Any]]:
    path = vault / PENDING_REVIEWS / f"{project_id}.json"
    payload = _read_json(path)
    if not payload:
        return []
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return []
    rows: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "pending")
        if status not in {"pending", "open", "needs_review", ""}:
            continue
        rows.append(
            {
                "review_id": entry.get("review_id"),
                "subject": entry.get("subject"),
                "field": entry.get("field"),
                "reason": entry.get("reason") or entry.get("summary") or "UNKNOWN",
                "status": status or "pending",
                "claim": entry.get("claim"),
                "verified": False,
                "human_disposition": None,
                "path": path.relative_to(vault).as_posix(),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _human_decisions(vault: Path, project_id: str, *, limit: int = 12) -> list[dict[str, Any]]:
    path = vault / HUMAN_DECISIONS / f"{project_id}.json"
    payload = _read_json(path)
    if not payload:
        return []
    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        return []
    rows: list[dict[str, Any]] = []
    for entry in decisions:
        if not isinstance(entry, dict):
            continue
        rows.append(
            {
                "review_id": entry.get("review_id"),
                "decision": entry.get("decision"),
                "reason": entry.get("reason") or "UNKNOWN",
                "status": "human_recorded",
                "verified": entry.get("decision") == "accept",
                "path": path.relative_to(vault).as_posix(),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _evidence_rows(brief: dict[str, Any]) -> list[dict[str, Any]]:
    links = brief.get("evidence_links")
    if not isinstance(links, list):
        return []
    rows: list[dict[str, Any]] = []
    for link in links:
        if not isinstance(link, str) or not link.strip():
            continue
        kind = "source"
        if link.startswith("review/"):
            kind = "review"
        elif link.startswith("state/"):
            kind = "state"
        elif link.startswith("projects/"):
            kind = "project_note"
        elif link.startswith("generated/"):
            kind = "generated"
        rows.append(
            {
                "path": link,
                "kind": kind,
                "role": "evidence",
                "authority": kind in {"source", "project_note", "state"},
                "interpretation": False,
            }
        )
    return rows


def _truth_panel(
    vault: Path,
    project_id: str,
    brief: dict[str, Any],
) -> dict[str, Any]:
    conflicts = list_project_conflicts(vault, project_id)
    pending = _pending_reviews(vault, project_id)
    human = _human_decisions(vault, project_id)
    evidence = _evidence_rows(brief)
    unknown_text = str(brief.get("unknown_or_conflicting") or "UNKNOWN")
    return {
        "package_id": TRUTH_PACKAGE_ID,
        "truth_boundary": (
            "EVIDENCE!=INTERPRETATION / GRAPH!=AUTHORITY / "
            "MODEL!=AUTHORITY / UNKNOWN!=HEALTHY / UI!=CANONICAL"
        ),
        "why_atlas_believes": [
            {
                "field": "purpose",
                "value": brief.get("purpose") or "UNKNOWN",
                "status": "derived_brief",
                "verified": False,
                "evidence_count": len(evidence),
            },
            {
                "field": "current_state",
                "value": brief.get("current_state") or "UNKNOWN",
                "status": "derived_brief",
                "verified": False,
                "evidence_count": len(evidence),
            },
        ],
        "evidence": evidence,
        "conflicts": conflicts.get("conflicts") or [],
        "conflict_count": int(conflicts.get("conflict_count") or 0),
        "pending_reviews": pending,
        "pending_review_count": len(pending),
        "human_decisions": human,
        "human_decision_count": len(human),
        "unknown": {
            "summary": unknown_text,
            "is_unknown": unknown_text.strip().upper() == "UNKNOWN"
            or "UNKNOWN" in unknown_text
            or "conflict" in unknown_text.lower()
            or "pending" in unknown_text.lower(),
            "healthy": False,
        },
        "labels": {
            "verified": "human accept or verified claim signal",
            "pending": "awaiting human review",
            "conflict": "unresolved competing claims",
            "unknown": "absent or unresolved — not a healthy green state",
            "derived": "lens/brief projection — not Layer B authority",
            "ops_receipt": "session/handoff capture — not authority",
        },
        "confidence_theatre": False,
    }


def read_project_brief(vault: Path, project_id: str) -> dict[str, Any]:
    """Read Core brief + lens/capture/truth projections for Web (read-only)."""
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise WebBriefError(f"vault is not a directory: {vault}")
    project_id = _safe_project_id(project_id)
    brief_path = vault / BRIEF_OPS / f"project-brief-{project_id}.json"
    brief = _read_json(brief_path)
    if brief is None:
        brief = _unknown_brief(project_id)
    else:
        brief = dict(brief)
        brief["available"] = True
        honesty = dict(brief.get("honesty") or {})
        honesty.setdefault("lens_is_authority", False)
        honesty.setdefault("ui_is_canonical", False)
        honesty.setdefault("atlas_opt_wake_gate", "CLOSED")
        honesty.setdefault("authentic_pilot", False)
        honesty.setdefault("confidence_theatre", False)
        brief["honesty"] = honesty
        brief["package_web"] = PACKAGE_ID

    lenses = _lens_rows(vault, project_id)
    brief["lens_sections"] = {key: lenses.get(key) for key in LENS_FIELDS}
    brief["session_captures"] = _list_captures(vault, project_id)
    brief["conversation_captures"] = list_conversation_captures(
        vault, project_id=project_id, limit=8
    )
    brief["brief_path"] = (
        brief_path.relative_to(vault).as_posix() if brief_path.is_file() else None
    )
    brief["truth"] = _truth_panel(vault, project_id, brief)
    brief["truth_boundary"] = "WEB BRIEF READ != AUTHORITY / UI != CANONICAL"
    brief["package_truth_ux"] = TRUTH_PACKAGE_ID
    return brief


def filter_knowledge_by_project(
    rows: list[dict[str, Any]],
    project_id: str | None,
) -> list[dict[str, Any]]:
    """Filter knowledge inventory rows by project subject (optional)."""
    if project_id is None:
        return rows
    project_id = _safe_project_id(project_id)
    return [
        row
        for row in rows
        if row.get("subject") == project_id
        and answer_filename_project(str(row.get("path") or "")) == project_id
    ]
