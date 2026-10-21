"""AT3-039 — Provider-neutral conversation normalization."""

from __future__ import annotations

from typing import Any, Final

from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.memory.envelope import SCHEMA_NAME, build_envelope
from project_atlas.atlas3.memory.privacy import apply_privacy

PACKAGE_ID: Final[str] = "AT3-039"

ROLE_ALIASES = {
    "human": "user",
    "user": "user",
    "assistant": "assistant",
    "ai": "assistant",
    "model": "assistant",
    "system": "system",
    "tool": "tool",
    "owner": "owner",
}


def normalize_capability() -> dict[str, Any]:
    return {
        "package": PACKAGE_ID,
        "schema": SCHEMA_NAME,
        "import_mode_required": True,
        "graph_is_authority": False,
        "raw_transcript_persisted": False,
        "partial_persist_on_corrupt": False,
    }


def normalize_turns(
    turns: list[dict[str, Any]],
    *,
    provider: str,
    conversation_id: str,
    import_mode: str,
    project_id: str | None = None,
    privacy_class: str = "include",
) -> list[dict[str, Any]]:
    if not isinstance(turns, list):
        raise Atlas3Error("NORMALIZE_INVALID", "turns must be a list")
    envelopes: list[dict[str, Any]] = []
    parent: str | None = None
    for index, turn in enumerate(turns):
        if not isinstance(turn, dict):
            raise Atlas3Error("NORMALIZE_INVALID", "turn is not an object")
        if project_id is not None:
            explicit = turn.get("project_id")
            if explicit is not None and str(explicit) != project_id:
                raise Atlas3Error(
                    "PROJECT_MISMATCH",
                    f"turn project_id {explicit!r} != requested {project_id!r}",
                )
            meta = turn.get("provider_metadata")
            if isinstance(meta, dict):
                forged = meta.get("project_id") or meta.get("bound_project_id")
                if forged is not None and str(forged) != project_id:
                    raise Atlas3Error(
                        "PROJECT_MISMATCH",
                        "provider_metadata cannot override governed project routing",
                    )
        raw_role = str(turn.get("role") or "assistant").strip().lower()
        role = ROLE_ALIASES.get(raw_role, raw_role)
        raw_text = str(turn.get("text") or turn.get("content") or "")
        text = apply_privacy(raw_text, privacy_class=privacy_class)
        message_id = str(turn.get("message_id") or f"msg-{index + 1}")
        envelopes.append(
            build_envelope(
                provider=provider,
                conversation_id=conversation_id,
                message_id=message_id,
                role=role,
                text=text,
                import_mode=import_mode,
                project_id=project_id,
                parent_message_id=parent,
                model_name=str(turn.get("model_name") or "") or None,
                source_timestamp=str(turn.get("source_timestamp") or "") or None,
                privacy_class=privacy_class,
                provider_metadata={"source_index": index},
            )
        )
        parent = message_id
    return envelopes
