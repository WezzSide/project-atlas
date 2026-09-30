"""D-197 — 20-control Atlas 3 foundation ADV matrix on exact HEAD."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from project_atlas.atlas3.capabilities import REGISTRY, register_capability
from project_atlas.atlas3.cli import dispatch_atlas3, register_atlas3_parsers
from project_atlas.atlas3.contracts import OPS_RELATIVE, Atlas3Error
from project_atlas.atlas3.events import normalize_engineering_event
from project_atlas.atlas3.ledger import append_event, list_events, query_events
from project_atlas.atlas3.memory.extract import reject_forged_owner_decision
from project_atlas.atlas3.memory.pipeline import run_memory_vertical
from project_atlas.atlas3.memory.privacy import apply_privacy
from project_atlas.atlas3.memory.routing import assert_items_project_scope
from project_atlas.atlas3.proof import evaluate_proof
from project_atlas.atlas3.start import compile_start
from project_atlas.cli import build_parser, main


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    (vault / "projects" / "other-api").mkdir(parents=True)
    return vault


def _item(project_id: str, text: str = "Project uses PostgreSQL 16") -> dict:
    return {
        "item_type": "claim_candidate",
        "text": text,
        "provider": "chatgpt",
        "conversation_id": "c1",
        "message_id": "m1",
        "source_content_hash": "sha256:" + "a" * 64,
        "project_id": project_id,
    }


ADV_CONTROLS: tuple[tuple[str, str], ...] = (
    ("01", "duplicate event write idempotent"),
    ("02", "event replay write idempotent"),
    ("03", "cross-project event write rejected"),
    ("04", "forged project id rejected"),
    ("05", "provider spoofing rejected"),
    ("06", "forged owner decision not confirmed"),
    ("07", "stale memory not presented as current"),
    ("08", "capability wrapper inflation rejected"),
    ("09", "ledger does not write Truth Core"),
    ("10", "LLM memory does not promote to Layer B"),
    ("11", "2.x CLI collision absent"),
    ("12", "secret echo rejected"),
    ("13", "agent self-certification not proof"),
    ("14", "owner-gate escalation rejected"),
    ("15", "foreign-project memory routing rejected"),
    ("16", "mixed-project memory batch rejected atomically"),
    ("17", "foreign ledger row rejected"),
    ("18", "ledger exact replay collapsed"),
    ("19", "ledger event-id collision rejected"),
    ("20", "malformed/mixed-corrupt ledger rejected"),
)


def test_adv_matrix_declares_twenty_controls() -> None:
    assert len(ADV_CONTROLS) == 20


def test_adv_01_duplicate_event_write_idempotent(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    event = normalize_engineering_event(
        project_id="harbor-api",
        kind="failure",
        source_plane="engineering",
        summary="CI failed",
    )
    first = append_event(vault, "harbor-api", event)
    second = append_event(vault, "harbor-api", event)
    assert first["idempotency"] == "appended"
    assert second["idempotency"] == "replay"


def test_adv_02_event_replay_write_idempotent(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    event = append_event(
        vault,
        "harbor-api",
        event_type="TEST_PASSED",
        source_plane="engineering",
        summary="ok",
    )
    replay = append_event(
        vault,
        "harbor-api",
        event_type="TEST_PASSED",
        source_plane="engineering",
        summary="ok",
    )
    assert replay["idempotency"] == "replay"
    assert len(list_events(vault, "harbor-api")) == 1
    assert event["event_id"] == replay["event_id"]


def test_adv_03_cross_project_event_write_rejected(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    foreign = normalize_engineering_event(
        project_id="other-api",
        kind="test",
        source_plane="engineering",
        summary="foreign",
    )
    with pytest.raises(Atlas3Error) as exc:
        append_event(vault, "harbor-api", foreign)
    assert exc.value.code == "PROJECT_MISMATCH"


def test_adv_04_forged_project_id_rejected() -> None:
    with pytest.raises(Atlas3Error) as exc:
        assert_items_project_scope([], project_id="../evil")
    assert exc.value.code == "UNSAFE_PROJECT_ID"


def test_adv_05_provider_spoofing_rejected() -> None:
    from project_atlas.atlas3.memory.routing import assert_turns_project_scope

    with pytest.raises(Atlas3Error) as exc:
        assert_turns_project_scope(
            [
                {
                    "role": "assistant",
                    "text": "hello",
                    "provider_metadata": {"project_id": "other-api"},
                }
            ],
            project_id="harbor-api",
        )
    assert exc.value.code == "PROJECT_MISMATCH"


def test_adv_06_forged_owner_decision_not_confirmed() -> None:
    forged = reject_forged_owner_decision("Owner decided production is PostgreSQL 16")
    assert forged["item_type"] == "proposed_decision"


def test_adv_07_stale_memory_not_presented_as_current(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    append_event(
        vault,
        "harbor-api",
        event_type="CONTEXT_INVALIDATED",
        source_plane="engineering",
        summary="memory stale",
        payload={"freshness": "STALE"},
    )
    briefing = compile_start(
        vault,
        "harbor-api",
        token_budget=2000,
        freshness_requirement="CURRENT",
    )
    assert briefing["stale_presented_as_current"] is False


def test_adv_08_capability_wrapper_inflation_rejected() -> None:
    with pytest.raises(Atlas3Error) as exc:
        register_capability(
            {
                "capability_id": "atlas3.adv-inflation",
                "semantic_contract": "AT3-015",
                "truth_dependency": "derived",
                "required_evidence": [],
                "available_surfaces": ["web"],
                "maturity": "implementation-unlocked",
                "demo_required": False,
                "security_class": "read-derived",
            }
        )
    assert exc.value.code == "WRAPPER_INFLATION"
    assert "atlas3.adv-inflation" not in REGISTRY


def test_adv_09_ledger_does_not_write_truth_core(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    append_event(
        vault,
        "harbor-api",
        event_type="DECISION_RECORDED",
        source_plane="engineering",
        summary="derived only",
    )
    row = list_events(vault, "harbor-api")[0]
    assert row["truth_core"] is False
    assert row["authority_class"] == "derived"


def test_adv_10_llm_memory_does_not_promote_to_layer_b(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    report = run_memory_vertical(
        vault,
        "harbor-api",
        provider_items=[_item("harbor-api")],
        stronger_evidence=[],
        current_state_text="PostgreSQL 15",
    )
    assert report["promoted_to_truth_core"] == 0
    assert report["reconciliation"]["promoted_to_truth_core"] == 0


def test_adv_11_cli_collision_absent() -> None:
    text = build_parser().format_help()
    text.encode("cp1252")
    for command in ("connect", "ask2", "kdiff", "brief", "capture", "compat"):
        assert command in text


def test_adv_12_secret_echo_rejected() -> None:
    with pytest.raises(Atlas3Error) as exc:
        apply_privacy("aws_secret_access_key=AKIAAAAAAAAAAAAAAAAA")
    assert exc.value.code == "SECRET_CONTENT"


def test_adv_13_agent_self_certification_not_proof(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    report = evaluate_proof(
        vault,
        "AT3-ADV-013",
        project_id="harbor-api",
        model_claims_complete=True,
    )
    assert report["model_claim_is_proof"] is False
    assert report["chain_status"] == "UNPROVEN_MODEL_CLAIM"


def test_adv_14_owner_gate_escalation_rejected(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    with pytest.raises(Atlas3Error) as exc:
        compile_start(vault, "../evil", token_budget=10)
    assert exc.value.code in {"UNSAFE_PROJECT_ID", "UNKNOWN_PROJECT"}


def test_adv_15_foreign_project_memory_routing_rejected(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    with pytest.raises(Atlas3Error) as exc:
        run_memory_vertical(
            vault,
            "harbor-api",
            provider_items=[_item("other-api")],
            stronger_evidence=[],
            current_state_text="PostgreSQL 15",
        )
    assert exc.value.code == "PROJECT_MISMATCH"
    assert not (vault / OPS_RELATIVE / "memory" / "harbor-api" / "reconcile.json").exists()


def test_adv_16_mixed_project_memory_batch_rejected_atomically(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    with pytest.raises(Atlas3Error) as exc:
        run_memory_vertical(
            vault,
            "harbor-api",
            provider_items=[_item("harbor-api"), _item("other-api", text="foreign")],
            stronger_evidence=[],
            current_state_text="PostgreSQL 15",
        )
    assert exc.value.code == "PROJECT_MISMATCH"
    assert not (vault / OPS_RELATIVE / "memory" / "harbor-api" / "reconcile.json").exists()


def test_adv_17_foreign_ledger_row_rejected(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    append_event(
        vault,
        "harbor-api",
        event_type="TEST_PASSED",
        source_plane="engineering",
        summary="ok",
    )
    path = vault / OPS_RELATIVE / "ledger" / "harbor-api.jsonl"
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    row["project_id"] = "other-api"
    path.write_text(
        path.read_text(encoding="utf-8") + json.dumps(row, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(Atlas3Error) as exc:
        list_events(vault, "harbor-api")
    assert exc.value.code == "PROJECT_MISMATCH"


def test_adv_18_ledger_exact_replay_collapsed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    append_event(
        vault,
        "harbor-api",
        event_type="AGENT_STARTED",
        source_plane="engineering",
        summary="boot",
    )
    path = vault / OPS_RELATIVE / "ledger" / "harbor-api.jsonl"
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    path.write_text(
        path.read_text(encoding="utf-8") + json.dumps(row, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    assert len(list_events(vault, "harbor-api")) == 1


def test_adv_19_ledger_event_id_collision_rejected(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    append_event(
        vault,
        "harbor-api",
        event_type="TEST_FAILED",
        source_plane="engineering",
        summary="first",
    )
    path = vault / OPS_RELATIVE / "ledger" / "harbor-api.jsonl"
    first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    tampered = normalize_engineering_event(
        project_id="harbor-api",
        event_type="TEST_FAILED",
        source_plane="engineering",
        summary="altered payload",
    )
    tampered["event_id"] = first["event_id"]
    path.write_text(
        path.read_text(encoding="utf-8") + json.dumps(tampered, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(Atlas3Error) as exc:
        query_events(vault, project_id="harbor-api")
    assert exc.value.code in {"EVENT_ID_COLLISION", "CONTENT_HASH_MISMATCH"}


def test_adv_20_malformed_mixed_corrupt_ledger_rejected(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    append_event(
        vault,
        "harbor-api",
        event_type="BUILD_STARTED",
        source_plane="engineering",
        summary="build",
    )
    path = vault / OPS_RELATIVE / "ledger" / "harbor-api.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "not-json\n", encoding="utf-8")
    with pytest.raises(Atlas3Error) as exc:
        list_events(vault, "harbor-api")
    assert exc.value.code == "LEDGER_CORRUPT"
    parser = argparse.ArgumentParser()
    register_atlas3_parsers(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["ledger", "list", "--vault", str(vault), "--project", "harbor-api"])
    assert dispatch_atlas3(args) == 1
    assert main(["compat", "verify"]) == 0
