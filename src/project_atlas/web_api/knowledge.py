"""Read-only knowledge / answer inventory for the web shell (AS-WEB-ACCEPT).

Lists schema-shaped answer JSON under ``generated/answers/`` when present.
Missing directory → empty list (unknown inventory, never invented claims).
Never compiles knowledge and never writes Layer B.

DEMO-FINDING-001 residual: expose title/summary/value_text so Ask live and
MCP NL matchers can use fields already present on answer lens files (listing
must not invent winners or Layer B claims).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypedDict

from project_atlas.secrets import scan_text

ANSWERS_RELATIVE = Path("generated") / "answers"
_LENS_PREFIXES = ("overview-", "state-", "changed-", "decisions-", "unknown-")
_REDACTED_VALUE = "[redacted: secret-shaped value]"


class KnowledgeAnswerSummary(TypedDict):
    """Non-authoritative answer listing row for UI display and live match."""

    answer_id: str
    path: str
    subject: str | None
    field: str | None
    title: str | None
    summary: str | None
    value_text: str | None
    has_value: bool


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _optional_str(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _value_text(payload: dict[str, Any]) -> str | None:
    """Bounded display/match text from value when it is a plain string."""
    value = payload.get("value")
    if isinstance(value, str) and value.strip():
        return _REDACTED_VALUE if scan_text(value) else value
    return None


def answer_filename_project(path: str | None) -> str | None:
    """Project token bound in ``ans-<lens>-<project_id>.json``.

    AS-WEB-BRIEF-F1: subject spoofing must not attach a victim filename to
    another project's brief/knowledge inventory.
    """
    name = Path(str(path or "")).name
    if not name.startswith("ans-") or not name.endswith(".json"):
        return None
    stem = name[len("ans-") : -len(".json")]
    for prefix in _LENS_PREFIXES:
        if stem.startswith(prefix):
            token = stem[len(prefix) :]
            return token or None
    return None


def _safe_display(value: str | None) -> str | None:
    if not value:
        return value
    return _REDACTED_VALUE if scan_text(value) else value


def list_knowledge_answers(vault: Path) -> list[KnowledgeAnswerSummary]:
    """Return sorted answer summaries from ``generated/answers/*.json`` only."""
    root = vault / ANSWERS_RELATIVE
    if not root.is_dir():
        return []
    rows: list[KnowledgeAnswerSummary] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if not entry.is_file() or entry.suffix.lower() != ".json":
            continue
        if entry.name.startswith("."):
            continue
        payload = _read_json(entry)
        if payload is None:
            continue
        answer_id = str(payload.get("answer_id") or entry.stem)
        rows.append(
            {
                "answer_id": answer_id,
                "path": f"generated/answers/{entry.name}",
                "subject": _optional_str(payload, "subject"),
                "field": _optional_str(payload, "field"),
                "title": _safe_display(_optional_str(payload, "title", "question")),
                "summary": _safe_display(_optional_str(payload, "summary", "notes")),
                "value_text": _value_text(payload),
                "has_value": payload.get("value") is not None,
            }
        )
    return rows
