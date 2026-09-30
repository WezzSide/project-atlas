"""First D-192 vertical: export/turns → envelope → extract → dedup → freshness → search."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from project_atlas.atlas3.contracts import (
    OPS_RELATIVE,
    require_project,
    require_vault,
    write_json_atomic,
)
from project_atlas.atlas3.memory.chatgpt import import_chatgpt_export
from project_atlas.atlas3.memory.claude import import_claude_export
from project_atlas.atlas3.memory.codex import import_codex_export
from project_atlas.atlas3.memory.compiler import next_agent_must_not_claim_pg16, rank_context_layers
from project_atlas.atlas3.memory.cursor import import_cursor_export
from project_atlas.atlas3.memory.extract import extract_items
from project_atlas.atlas3.memory.gemini import import_gemini_export
from project_atlas.atlas3.memory.normalize import normalize_turns
from project_atlas.atlas3.memory.reconcile import reconcile_memories
from project_atlas.atlas3.memory.routing import (
    assert_evidence_project_scope,
    assert_items_project_scope,
    assert_turns_project_scope,
)
from project_atlas.atlas3.memory.search import search_memory


def ingest_provider_turns(
    turns: list[dict[str, Any]],
    *,
    provider: str,
    conversation_id: str,
    project_id: str,
    import_mode: str = "EXPORT",
    owner_origin: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    assert_turns_project_scope(turns, project_id=project_id)
    envelopes = normalize_turns(
        turns,
        provider=provider,
        conversation_id=conversation_id,
        import_mode=import_mode,
        project_id=project_id,
    )
    return extract_items(envelopes, owner_origin=owner_origin)


def run_memory_vertical(
    vault: Path,
    project_id: str,
    *,
    provider_items: list[dict[str, Any]],
    stronger_evidence: list[dict[str, Any]],
    current_state_text: str,
    query: str = "database migration",
) -> dict[str, Any]:
    root = require_vault(vault)
    pid = require_project(root, project_id)
    assert_items_project_scope(provider_items, project_id=pid)
    assert_evidence_project_scope(stronger_evidence, project_id=pid)
    reconciled = reconcile_memories(
        provider_items,
        stronger_evidence=stronger_evidence,
        current_state_text=current_state_text,
        project_id=pid,
    )
    searched = search_memory(reconciled["items"], query, project_id=pid)
    ranked = rank_context_layers(
        project_evidence=[current_state_text],
        derived_truth=[],
        accepted_decisions=[
            str(item.get("text"))
            for item in reconciled["owner_decisions"]
        ],
        reconciled_items=reconciled["items"],
        include_stale_historical=False,
    )
    report = {
        "package": "AT3-D192-FIRST-VERTICAL",
        "project_id": pid,
        "reconciliation": reconciled,
        "search": searched,
        "context_compiler": ranked,
        "next_agent_safe": next_agent_must_not_claim_pg16(ranked),
        "promoted_to_truth_core": 0,
        "chatgpt_bridge_replaced": False,
    }
    write_json_atomic(root / OPS_RELATIVE / "memory" / pid / "reconcile.json", report)
    return report


def chatgpt_export_to_items(
    source: Path | str,
    *,
    conversation_id: str,
    project_id: str,
) -> list[dict[str, Any]]:
    envelopes = import_chatgpt_export(
        source, conversation_id=conversation_id, project_id=project_id
    )
    return extract_items(envelopes)


def claude_export_to_items(
    source: Path | str,
    *,
    conversation_id: str,
    project_id: str,
) -> list[dict[str, Any]]:
    envelopes = import_claude_export(
        source, conversation_id=conversation_id, project_id=project_id
    )
    return extract_items(envelopes)


def gemini_export_to_items(
    source: Path | str,
    *,
    conversation_id: str,
    project_id: str,
) -> list[dict[str, Any]]:
    envelopes = import_gemini_export(
        source, conversation_id=conversation_id, project_id=project_id
    )
    return extract_items(envelopes)


def cursor_export_to_items(
    source: Path | str,
    *,
    conversation_id: str,
    project_id: str,
) -> list[dict[str, Any]]:
    envelopes = import_cursor_export(
        source, conversation_id=conversation_id, project_id=project_id
    )
    return extract_items(envelopes)


def codex_export_to_items(
    source: Path | str,
    *,
    conversation_id: str,
    project_id: str,
) -> list[dict[str, Any]]:
    envelopes = import_codex_export(
        source, conversation_id=conversation_id, project_id=project_id
    )
    return extract_items(envelopes)
