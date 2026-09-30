"""AS-SEC-SCAN-CONNECT-YAML-001 — scan decoded YAML marker ids before bind persist."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.connect import (
    RECEIPT_RELATIVE,
    _finish_no_change_reconnect,
    _marker_project_id,
    _write_bind,
    _write_receipt,
    connect_project,
)
from project_atlas.incremental_connect import ActiveDelta, IncrementalDecision
from project_atlas.secrets import scan_text

TOKEN = "AKIAAAAAAAAAAAAAAAAA"  # AKIA + 16 A — matches cloud-access-key


def _project_with_id(tmp_path: Path, raw_id_line: str) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".atlas-project.yaml").write_text(
        "schema_version: 1\n"
        "project:\n"
        f"  id: {raw_id_line}\n",
        encoding="utf-8",
    )
    return root


def test_quoted_unicode_escape_akia_is_not_bound(tmp_path: Path) -> None:
    raw_id = '"\\u0041KIAAAAAAAAAAAAAAAAA"'
    root = _project_with_id(tmp_path, raw_id)
    raw = (root / ".atlas-project.yaml").read_text(encoding="utf-8")
    assert scan_text(raw) == []
    assert TOKEN not in raw
    assert _marker_project_id(root) is None

    vault = tmp_path / "vault"
    vault.mkdir()
    path = _write_bind(
        root,
        vault,
        "vault-1",
        project_ids=[TOKEN],
        primary_project_id=TOKEN,
    )
    persisted = path.read_text(encoding="utf-8")
    loaded = json.loads(persisted)
    assert TOKEN not in persisted
    assert loaded["project_id"] is None
    assert loaded["project_ids"] == []


def test_quoted_hex_escape_akia_is_not_bound(tmp_path: Path) -> None:
    raw_id = '"\\x41KIAAAAAAAAAAAAAAAAA"'
    root = _project_with_id(tmp_path, raw_id)
    raw = (root / ".atlas-project.yaml").read_text(encoding="utf-8")
    assert scan_text(raw) == []
    assert _marker_project_id(root) is None


def test_clean_marker_id_still_binds(tmp_path: Path) -> None:
    root = _project_with_id(tmp_path, "sample-estate")
    assert _marker_project_id(root) == "sample-estate"
    vault = tmp_path / "vault"
    vault.mkdir()
    path = _write_bind(
        root,
        vault,
        "vault-1",
        project_ids=["sample-estate"],
        primary_project_id="sample-estate",
    )
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["project_id"] == "sample-estate"
    assert loaded["project_ids"] == ["sample-estate"]
    assert TOKEN not in json.dumps(loaded)


def _skip_decision() -> IncrementalDecision:
    return IncrementalDecision(
        disposition="no_change_skip",
        reason="active_sources_unchanged",
        files_inspected=1,
        content_changed=0,
        semantic_records_changed=0,
        physical_writes=0,
        projections_regenerated=0,
        ingest_invocations=0,
        discover_invocations=1,
        fingerprint_digest="0" * 64,
        prior_receipt_complete=True,
        delta=ActiveDelta(
            added=(),
            removed=(),
            modified=(),
            renamed=(),
            unknown_moves=(),
            content_changed=0,
            semantic_records_changed=0,
            lineage_proven=True,
        ),
    )


def test_write_receipt_drops_secret_bound_project_id(tmp_path: Path) -> None:
    """P1-RECEIPT-BOUND: receipt persist must not echo a decoded secret id."""
    vault = tmp_path / "vault"
    (vault / "generated" / "ops").mkdir(parents=True)
    report = {
        "schema_version": 1,
        "schema": "atlas.connect.receipt.v1",
        "bound_project_id": TOKEN,
        "projects": [TOKEN, "sample-estate"],
        "status": "connected",
    }
    path = _write_receipt(vault, report)
    persisted = path.read_text(encoding="utf-8")
    loaded = json.loads(persisted)
    assert TOKEN not in persisted
    assert loaded.get("bound_project_id") is None
    assert loaded.get("projects") == ["sample-estate"]
    assert report["bound_project_id"] is None
    assert report["projects"] == ["sample-estate"]


def test_reconnect_does_not_copy_secret_vault_or_prior_id(tmp_path: Path) -> None:
    """P1-RECEIPT-BOUND: vault dir / prior receipt must not become bound_project_id."""
    root = _project_with_id(tmp_path, '"\\u0041KIAAAAAAAAAAAAAAAAA"')
    (root / "README.md").write_text("# seed\n", encoding="utf-8")
    vault = tmp_path / "vault"
    (vault / "projects" / TOKEN).mkdir(parents=True)
    (vault / "generated" / "ops").mkdir(parents=True)
    (vault / ".atlas").mkdir(parents=True)
    report = {
        "schema_version": 1,
        "schema": "atlas.connect.receipt.v1",
        "vault_id": "vault-1",
        "projects": [TOKEN],
        "status": "connected",
    }
    prior = {"bound_project_id": TOKEN, "projects": [TOKEN]}
    out = _finish_no_change_reconnect(
        project_root=root,
        vault_path=vault,
        report=report,
        decision=_skip_decision(),
        prior_receipt=prior,
    )
    receipt = (vault / RECEIPT_RELATIVE).read_text(encoding="utf-8")
    bind = (root / ".atlas" / "connect.json").read_text(encoding="utf-8")
    assert TOKEN not in receipt
    assert TOKEN not in bind
    assert out.get("bound_project_id") is None
    loaded = json.loads(receipt)
    assert loaded.get("bound_project_id") is None


def test_reconnect_clean_marker_overwrites_planted_secret(tmp_path: Path) -> None:
    root = _project_with_id(tmp_path, "sample-estate")
    (root / "README.md").write_text("# seed\n", encoding="utf-8")
    vault = tmp_path / "vault"
    (vault / "projects" / "sample-estate").mkdir(parents=True)
    (vault / "generated" / "ops").mkdir(parents=True)
    (vault / ".atlas").mkdir(parents=True)
    report = {
        "schema_version": 1,
        "schema": "atlas.connect.receipt.v1",
        "vault_id": "vault-1",
        "projects": ["sample-estate"],
        "status": "connected",
    }
    prior = {"bound_project_id": TOKEN, "projects": [TOKEN]}
    out = _finish_no_change_reconnect(
        project_root=root,
        vault_path=vault,
        report=report,
        decision=_skip_decision(),
        prior_receipt=prior,
    )
    receipt = (vault / RECEIPT_RELATIVE).read_text(encoding="utf-8")
    assert TOKEN not in receipt
    assert out.get("bound_project_id") == "sample-estate"
    assert json.loads(receipt)["bound_project_id"] == "sample-estate"


def test_connect_project_does_not_persist_decoded_secret_in_receipt(
    tmp_path: Path,
) -> None:
    """End-to-end: ingest may allocate projects/TOKEN/ (F5-B); receipt must not."""
    root = _project_with_id(tmp_path, '"\\u0041KIAAAAAAAAAAAAAAAAA"')
    (root / "README.md").write_text("# Coder Alpha Fixture\n\nSeed.\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "DECISIONS.md").write_text("# Decisions\n\nKeep.\n", encoding="utf-8")
    raw = (root / ".atlas-project.yaml").read_text(encoding="utf-8")
    assert scan_text(raw) == []
    report = connect_project(root)
    vault = Path(report["vault"])
    receipt = (vault / RECEIPT_RELATIVE).read_text(encoding="utf-8")
    bind = (root / ".atlas" / "connect.json").read_text(encoding="utf-8")
    assert TOKEN not in receipt
    assert TOKEN not in bind
    assert report.get("bound_project_id") is None
    loaded = json.loads(receipt)
    assert loaded.get("bound_project_id") is None
    assert TOKEN not in json.dumps(loaded)
