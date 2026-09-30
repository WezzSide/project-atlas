from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from project_atlas.schema import SchemaValidationError, validate_record
from project_atlas.sync_receipts import (
    SyncReceiptError,
    build_dry_run_sync_receipts,
    write_dry_run_sync_receipts,
)

UUID_A = "11111111-1111-4111-8111-111111111111"
UUID_B = "22222222-2222-4222-8222-222222222222"


def _queue() -> dict[str, object]:
    def entry(index: int, project_uuid: str, state: str) -> dict[str, object]:
        return {
            "queue_index": index,
            "project_uuid": project_uuid,
            "disposition": "eligible" if state == "pending" else "disabled",
            "queue_state": state,
            "retry_policy": {
                "max_attempts": None,
                "backoff_seconds": [],
                "retryable_errors": [],
            },
            "resume_checkpoint_key": None,
            "estate_receipt": {
                "receipt_id": None,
                "status": "not_issued",
                "evidence_hash": None,
            },
        }

    return {
        "schema_version": 1,
        "schema": "atlas.sync_queue.dry_run.v1",
        "truth_plane": "operational",
        "authority_plane": "none",
        "note": "DRY-RUN SYNC QUEUE SCAFFOLD - NOT PRODUCTION CERTIFIED / NOT PILOT PASS",
        "package": "AS-SYNC-003-SCAFFOLD",
        "production_sync_certified": False,
        "estate_pilot_passed": False,
        "registry_id": "registry-fixture",
        "vault_identity": "vault-fixture",
        "source_plan_package": "AS-SYNC-002-SCAFFOLD",
        "entries": [entry(0, UUID_A, "pending"), entry(1, UUID_B, "blocked")],
        "resume_cursor": {
            "next_queue_index": None,
            "next_project_uuid": None,
            "completed_project_uuids": [],
            "last_estate_receipt_id": None,
        },
        "generated": {"by": "atlas-sync-003-scaffold"},
    }


def test_receipt_and_trigger_stubs_are_deterministic() -> None:
    first = build_dry_run_sync_receipts(_queue())
    second = build_dry_run_sync_receipts(copy.deepcopy(_queue()))

    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert [row["project_uuid"] for row in first["estate_receipts"]] == [UUID_A, UUID_B]
    assert [row["trigger_type"] for row in first["triggers"]] == [
        "on_change",
        "on_schedule",
        "on_change",
        "on_schedule",
    ]


def test_stubs_are_inert_and_flags_are_false() -> None:
    report = build_dry_run_sync_receipts(_queue())
    validate_record(report, "sync-receipts-dry-run")

    assert report["production_sync_certified"] is False
    assert report["estate_pilot_passed"] is False
    assert all(row["status"] == "not_issued" for row in report["estate_receipts"])
    assert all(row["receipt_id"] is None for row in report["estate_receipts"])
    assert all(row["enabled"] is False for row in report["triggers"])
    assert all(row["registration_id"] is None for row in report["triggers"])

    report["triggers"][0]["enabled"] = True
    with pytest.raises(SchemaValidationError):
        validate_record(report, "sync-receipts-dry-run")


def test_invalid_or_certified_queue_fails_closed() -> None:
    with pytest.raises(SyncReceiptError):
        build_dry_run_sync_receipts({})

    queue = _queue()
    queue["production_sync_certified"] = True
    with pytest.raises(SyncReceiptError):
        build_dry_run_sync_receipts(queue)

    queue = _queue()
    queue["estate_pilot_passed"] = True
    with pytest.raises(SyncReceiptError):
        build_dry_run_sync_receipts(queue)


def test_malformed_queue_order_fails_closed() -> None:
    queue = _queue()
    queue["entries"][1]["queue_index"] = 3
    with pytest.raises(SyncReceiptError, match="contiguous and ordered"):
        build_dry_run_sync_receipts(queue)


def test_write_is_limited_to_generated_ops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report = build_dry_run_sync_receipts(_queue())
    path = write_dry_run_sync_receipts(tmp_path, report)
    expected = (tmp_path / "generated" / "ops" / "sync-receipts-dry-run.json").resolve()
    assert path == expected
    assert not (tmp_path / "00-system" / "sync").exists()

    monkeypatch.setattr(
        "project_atlas.sync_receipts.REPORT_RELATIVE",
        Path("00-system/sync/sync-receipts.json"),
    )
    with pytest.raises(SyncReceiptError, match="generated/ops/sync-receipts-dry-run"):
        write_dry_run_sync_receipts(tmp_path, report)
    assert not (tmp_path / "00-system" / "sync" / "sync-receipts.json").exists()


def test_write_rejects_generated_symlink_escape(tmp_path: Path) -> None:
    report = build_dry_run_sync_receipts(_queue())
    outside = tmp_path / "outside"
    outside.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "generated").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SyncReceiptError, match="escapes vault root"):
        write_dry_run_sync_receipts(vault, report)
    assert not (outside / "ops" / "sync-receipts-dry-run.json").exists()
