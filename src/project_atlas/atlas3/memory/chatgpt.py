"""AT3-036 — ChatGPT export adapter.

Wraps landed parse_chat_export. Does not import or replace chatgpt_bridge.
Live full-history sync is not implemented.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.memory.normalize import normalize_turns
from project_atlas.openai_importer_fixtures import (
    OpenAIImportFixtureError,
    parse_chat_export,
)

PACKAGE_ID: Final[str] = "AT3-036"
LIVE_FULL_HISTORY_SYNC: Final[bool] = False


def _preflight_json_payload(payload: object) -> None:
    """Reject live-history claims and mixed valid+corrupt JSON exports."""
    if isinstance(payload, dict):
        if payload.get("live_full_history_sync") is True:
            raise Atlas3Error(
                "CHATGPT_HISTORY_API_CLAIMED",
                "ChatGPT fixture must not claim live_full_history_sync",
            )
        raw = payload.get("messages")
        if raw is None:
            raw = payload.get("turns")
        if raw is None:
            return
        if not isinstance(raw, list):
            raise Atlas3Error("CHATGPT_EXPORT_INVALID", "messages/turns must be a list")
    elif isinstance(payload, list):
        raw = payload
    else:
        return
    for row in raw:
        if not isinstance(row, dict):
            raise Atlas3Error("CHATGPT_EXPORT_INVALID", "turn is not an object")


def _load_export_text(source: Path | str) -> str:
    if isinstance(source, Path):
        if not source.is_file():
            raise Atlas3Error("EXPORT_NOT_FOUND", f"export file not found: {source}")
        try:
            return source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise Atlas3Error("CHATGPT_EXPORT_INVALID", "export is not readable") from exc
    return source


def import_chatgpt_export(
    source: Path | str,
    *,
    conversation_id: str,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Parse a ChatGPT-style export into canonical envelopes."""
    text = _load_export_text(source)
    stripped = text.strip()
    if stripped[:1] in {"{", "["}:
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise Atlas3Error("CHATGPT_EXPORT_INVALID", "export is not readable JSON") from exc
        _preflight_json_payload(payload)
        if isinstance(payload, dict) and project_id is not None:
            explicit = payload.get("project_id")
            if explicit is not None and str(explicit) != project_id:
                raise Atlas3Error(
                    "PROJECT_MISMATCH",
                    f"export project_id {explicit!r} != requested {project_id!r}",
                )
    try:
        turns = parse_chat_export(text)
    except OpenAIImportFixtureError as exc:
        raise Atlas3Error("CHATGPT_EXPORT_INVALID", str(exc)) from exc
    payload = [turn.as_dict() for turn in turns]
    return normalize_turns(
        payload,
        provider="chatgpt",
        conversation_id=conversation_id,
        import_mode="EXPORT",
        project_id=project_id,
    )


def chatgpt_capability() -> dict[str, Any]:
    return {
        "package": PACKAGE_ID,
        "export_import": "IMPLEMENTED",
        "conversation_sync": "NOT_IMPLEMENTED",
        "atlas_readonly_from_chatgpt": "IMPLEMENTED_AS_DEMO_MCP",
        "live_full_history_sync": LIVE_FULL_HISTORY_SYNC,
        "replaces_chatgpt_bridge": False,
        "uses_parse_chat_export": True,
        "native_history_api": False,
    }
