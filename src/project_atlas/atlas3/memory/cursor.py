"""AT3-057 — Cursor fixture / local-session ingest.

Structured-submission and local-session JSON ingest only. Does not claim a
Cursor Cloud history API. AGENTS.md and .cursorrules are bootstrap, not
ingestion. Does not replace Core conversation capture or the 2.x ChatGPT
live surface.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.memory.normalize import normalize_turns

PACKAGE_ID: Final[str] = "AT3-057"
LIVE_FULL_HISTORY_SYNC: Final[bool] = False
BOOTSTRAP_NAMES: Final[frozenset[str]] = frozenset(
    {"AGENTS.md", ".cursorrules", "CURSOR.md"}
)


def _turns_from_payload(payload: object) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        if payload.get("live_full_history_sync") is True:
            raise Atlas3Error(
                "CURSOR_CLOUD_HISTORY_CLAIMED",
                "Cursor fixture must not claim live_full_history_sync",
            )
        if payload.get("cursor_cloud_history") is True:
            raise Atlas3Error(
                "CURSOR_CLOUD_HISTORY_CLAIMED",
                "Cursor fixture must not claim cursor_cloud_history",
            )
        raw = payload.get("messages")
        if raw is None:
            raw = payload.get("turns")
        if not isinstance(raw, list):
            raise Atlas3Error("CURSOR_EXPORT_INVALID", "messages/turns must be a list")
    elif isinstance(payload, list):
        raw = payload
    else:
        raise Atlas3Error("CURSOR_EXPORT_INVALID", "export must be an object or list")
    turns: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            raise Atlas3Error("CURSOR_EXPORT_INVALID", "turn is not an object")
        text = str(row.get("content") or row.get("text") or "").strip()
        role = str(row.get("role") or "assistant").strip()
        if not text:
            continue
        turns.append({"role": role, "content": text, "text": text})
    return turns


def import_cursor_export(
    source: Path | str,
    *,
    conversation_id: str,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Parse a Cursor-style JSON session fixture into canonical envelopes."""
    if isinstance(source, Path):
        if source.name in BOOTSTRAP_NAMES:
            raise Atlas3Error(
                "AGENTS_MD_IS_NOT_INGESTION",
                "AGENTS.md / Cursor rules are bootstrap adapters, not ingestion sources",
            )
        if not source.is_file():
            raise Atlas3Error("EXPORT_NOT_FOUND", f"export file not found: {source}")
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise Atlas3Error("CURSOR_EXPORT_INVALID", "export is not readable JSON") from exc
    else:
        try:
            payload = json.loads(source)
        except json.JSONDecodeError as exc:
            raise Atlas3Error("CURSOR_EXPORT_INVALID", "export is not readable JSON") from exc
    if isinstance(payload, dict) and project_id is not None:
        explicit = payload.get("project_id")
        if explicit is not None and str(explicit) != project_id:
            raise Atlas3Error(
                "PROJECT_MISMATCH",
                f"export project_id {explicit!r} != requested {project_id!r}",
            )
    turns = _turns_from_payload(payload)
    return normalize_turns(
        turns,
        provider="cursor",
        conversation_id=conversation_id,
        import_mode="LOCAL_SESSION",
        project_id=project_id,
    )


def cursor_capability() -> dict[str, Any]:
    return {
        "package": PACKAGE_ID,
        "export_import": "IMPLEMENTED",
        "conversation_sync": "NOT_IMPLEMENTED",
        "live_full_history_sync": LIVE_FULL_HISTORY_SYNC,
        "cursor_cloud_history": False,
        "bootstrap_adapter": "AGENTS.md",
        "bootstrap_is_ingestion": False,
        "replaces_conversation_capture": False,
        "native_history_api": False,
        "import_mode": "LOCAL_SESSION",
        "merge_authorization": "NOT_GRANTED",
    }
