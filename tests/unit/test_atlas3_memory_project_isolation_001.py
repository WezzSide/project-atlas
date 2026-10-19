"""P1-A — Cross-project memory routing (D-196)."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from project_atlas.atlas3.cli import dispatch_atlas3, register_atlas3_parsers
from project_atlas.atlas3.contracts import OPS_RELATIVE, Atlas3Error, write_json_atomic
from project_atlas.atlas3.memory.pipeline import ingest_provider_turns, run_memory_vertical
from project_atlas.atlas3.memory.routing import (
    assert_items_project_scope,
    assert_turns_project_scope,
)
from project_atlas.atlas3.memory.search import search_memory


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    (vault / "projects" / "other-api").mkdir(parents=True)
    return vault


def _items(project_id: str, text: str = "Project uses PostgreSQL 16") -> list[dict]:
    return [
        {
            "item_type": "claim_candidate",
            "text": text,
            "provider": "chatgpt",
            "conversation_id": "c1",
            "message_id": "m1",
            "source_content_hash": "sha256:" + "a" * 64,
            "project_id": project_id,
        }
    ]


def test_same_project_passes(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    items = _items("harbor-api")
    report = run_memory_vertical(
        vault,
        "harbor-api",
        provider_items=items,
        stronger_evidence=[],
        current_state_text="PostgreSQL 15",
    )
    assert report["project_id"] == "harbor-api"
    reconcile_path = (
        vault / "generated" / "ops" / "atlas3" / "memory" / "harbor-api" / "reconcile.json"
    )
    assert reconcile_path.is_file()


def test_foreign_project_rejected(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    with pytest.raises(Atlas3Error) as exc:
        run_memory_vertical(
            vault,
            "harbor-api",
            provider_items=_items("other-api"),
            stronger_evidence=[],
            current_state_text="PostgreSQL 15",
        )
    assert exc.value.code == "PROJECT_MISMATCH"
    assert not (
        vault / "generated" / "ops" / "atlas3" / "memory" / "harbor-api" / "reconcile.json"
    ).exists()


def test_mixed_batch_rejected_atomically(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    mixed = _items("harbor-api") + _items("other-api", text="Foreign claim")
    with pytest.raises(Atlas3Error) as exc:
        run_memory_vertical(
            vault,
            "harbor-api",
            provider_items=mixed,
            stronger_evidence=[],
            current_state_text="PostgreSQL 15",
        )
    assert exc.value.code == "PROJECT_MISMATCH"
    assert not (
        vault / "generated" / "ops" / "atlas3" / "memory" / "harbor-api" / "reconcile.json"
    ).exists()


def test_unsafe_project_id_rejected() -> None:
    with pytest.raises(Atlas3Error) as exc:
        assert_items_project_scope([], project_id="../evil")
    assert exc.value.code == "UNSAFE_PROJECT_ID"


def test_unknown_project_rejected(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(Atlas3Error) as exc:
        run_memory_vertical(
            vault,
            "missing-project",
            provider_items=_items("missing-project"),
            stronger_evidence=[],
            current_state_text="PostgreSQL 15",
        )
    assert exc.value.code == "UNKNOWN_PROJECT"


def test_missing_project_id_rejected() -> None:
    with pytest.raises(Atlas3Error) as exc:
        assert_items_project_scope(
            [{"item_type": "claim_candidate", "text": "no project binding"}],
            project_id="harbor-api",
        )
    assert exc.value.code == "PROJECT_MISMATCH"


def test_forged_provider_metadata_cannot_override_routing() -> None:
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


def test_search_and_cli_reject_leaked_mixed_reconcile(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    leaked = {
        "reconciliation": {
            "items": _items("harbor-api") + _items("other-api", text="FOREIGN-MARKER"),
        }
    }
    write_json_atomic(
        vault / OPS_RELATIVE / "memory" / "harbor-api" / "reconcile.json",
        leaked,
    )
    with pytest.raises(Atlas3Error) as search_exc:
        search_memory(leaked["reconciliation"]["items"], "FOREIGN")
    assert search_exc.value.code == "PROJECT_MISMATCH"
    parser = argparse.ArgumentParser()
    register_atlas3_parsers(parser.add_subparsers(dest="command"))
    args = parser.parse_args(
        ["memory", "search", "FOREIGN", "--vault", str(vault), "--project", "harbor-api"]
    )
    rc = dispatch_atlas3(args)
    assert rc == 1


def test_foreign_stronger_evidence_rejected_before_persist(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    with pytest.raises(Atlas3Error) as exc:
        run_memory_vertical(
            vault,
            "harbor-api",
            provider_items=_items("harbor-api"),
            stronger_evidence=[
                {
                    "kind": "deployment",
                    "text": "PostgreSQL 15",
                    "project_id": "other-api",
                }
            ],
            current_state_text="unknown",
        )
    assert exc.value.code == "PROJECT_MISMATCH"
    assert not (
        vault / "generated" / "ops" / "atlas3" / "memory" / "harbor-api" / "reconcile.json"
    ).exists()


def test_unlabeled_stronger_evidence_still_allowed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    report = run_memory_vertical(
        vault,
        "harbor-api",
        provider_items=_items("harbor-api"),
        stronger_evidence=[{"kind": "deployment", "text": "PostgreSQL 15"}],
        current_state_text="PostgreSQL 15",
    )
    assert report["project_id"] == "harbor-api"
    assert (
        vault / "generated" / "ops" / "atlas3" / "memory" / "harbor-api" / "reconcile.json"
    ).is_file()


def test_ingest_turn_project_mismatch() -> None:
    with pytest.raises(Atlas3Error) as exc:
        ingest_provider_turns(
            [{"role": "assistant", "text": "x", "project_id": "other-api"}],
            provider="chatgpt",
            conversation_id="c1",
            project_id="harbor-api",
        )
    assert exc.value.code == "PROJECT_MISMATCH"
