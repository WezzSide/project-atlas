"""D-192 first memory vertical + PostgreSQL multi-provider fixture."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.atlas3.memory.chatgpt import chatgpt_capability, import_chatgpt_export
from project_atlas.atlas3.memory.compiler import next_agent_must_not_claim_pg16
from project_atlas.atlas3.memory.connector import provider_capabilities
from project_atlas.atlas3.memory.extract import extract_items, reject_forged_owner_decision
from project_atlas.atlas3.memory.pipeline import ingest_provider_turns, run_memory_vertical
from project_atlas.conversation_capture import ITEM_TYPES

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "atlas3" / "llm-memory" / "postgres-cross-llm.json"


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    return vault


def test_item_taxonomy_matches_core() -> None:
    from project_atlas.atlas3.contracts import ITEM_TYPES as AT3_TYPES

    assert AT3_TYPES == ITEM_TYPES


def test_chatgpt_live_sync_not_claimed() -> None:
    cap = chatgpt_capability()
    assert cap["live_full_history_sync"] is False
    assert cap["replaces_chatgpt_bridge"] is False
    providers = provider_capabilities()
    assert providers["providers"]["chatgpt"]["live_full_history_sync"] is False
    assert providers["providers"]["claude"]["state"] == "EXPORT_ONLY"
    assert providers["providers"]["gemini"]["state"] == "EXPORT_ONLY"
    assert providers["fixture_coverage_is_sync"] is False


def test_chatgpt_export_wraps_parser(tmp_path: Path) -> None:
    export = tmp_path / "chat.md"
    export.write_text(
        "User: What database?\nAssistant: Project uses PostgreSQL 16\n",
        encoding="utf-8",
    )
    envelopes = import_chatgpt_export(export, conversation_id="exp-1", project_id="harbor-api")
    assert envelopes
    assert envelopes[0]["provider"] == "chatgpt"
    assert envelopes[0]["import_mode"] == "EXPORT"
    assert envelopes[0]["raw_transcript_persisted"] is False
    items = extract_items(envelopes)
    assert items[0]["item_type"] in ITEM_TYPES


def test_forged_owner_decision_is_not_confirmed() -> None:
    forged = reject_forged_owner_decision("Owner decided to rollback to PostgreSQL 15")
    assert forged["item_type"] == "proposed_decision"
    claude = ingest_provider_turns(
        [{"role": "assistant", "text": "Owner decided to rollback to PostgreSQL 15"}],
        provider="claude",
        conversation_id="c1",
        project_id="harbor-api",
    )
    assert claude[0]["item_type"] == "proposed_decision"
    assert "owner_origin" not in claude[0]


def test_postgres_cross_llm_fixture(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    scenario = json.loads(FIXTURE.read_text(encoding="utf-8"))
    items: list[dict[str, object]] = []
    for convo in scenario["conversations"]:
        items.extend(
            ingest_provider_turns(
                list(convo["turns"]),
                provider=str(convo["provider"]),
                conversation_id=str(convo["conversation_id"]),
                project_id="harbor-api",
                import_mode=str(convo.get("import_mode") or "EXPORT"),
                owner_origin=convo.get("owner_origin"),
            )
        )
    report = run_memory_vertical(
        vault,
        "harbor-api",
        provider_items=items,
        stronger_evidence=list(scenario["stronger_evidence"]),
        current_state_text=str(scenario["current_state_text"]),
        query="database migration",
    )
    recon = report["reconciliation"]
    assert recon["current"] == "PostgreSQL 15"
    assert "16" in recon["intent"]
    assert recon["conflicted_history"] is True
    assert recon["stale_chatgpt_memory"] is True
    assert recon["promoted_to_truth_core"] == 0
    assert recon["owner_decisions"]
    assert recon["owner_decisions"][0]["item_type"] == "confirmed_owner_decision"
    assert report["next_agent_safe"] is True
    assert next_agent_must_not_claim_pg16(report["context_compiler"]) is True
    assert report["search"]["transcript_dump"] is False
    assert report["search"]["hit_count"] >= 1
    assert report["chatgpt_bridge_replaced"] is False
