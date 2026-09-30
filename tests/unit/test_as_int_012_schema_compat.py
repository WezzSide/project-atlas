"""AS-INT-012 schema compatibility / migration tooling tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.event_tombstones import record_explicit_tombstone
from project_atlas.receipt_revocation import revoke_receipt
from project_atlas.schema import validate_record
from project_atlas.schema_compat import (
    ScanTarget,
    SchemaCompatError,
    build_report,
    detect_schema_identity,
    migrate_dry_run,
    scan_compat,
)


def test_as_int_012_compat_ok_on_empty_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    report = scan_compat(vault)
    validate_record(report, "schema-compat-report")
    assert report["status"] == "ok"
    assert report["mode"] == "compat"
    assert report["counts"]["missing"] == len(report["findings"])
    assert "at" not in report["generated"]
    path = vault / "generated" / "ops" / "schema-compat-report.json"
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8")) == report


def test_as_int_012_compat_with_live_ops_indexes(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    record_explicit_tombstone(vault, project_id="proj-a", event_id="AE-001")
    revoke_receipt(vault, project_id="proj-a", event_id="AE-001")
    report = scan_compat(vault)
    by_path = {row["path"]: row for row in report["findings"]}
    assert by_path["generated/ops/event-tombstones.json"]["result"] == "compatible"
    assert by_path["generated/ops/receipt-revocations.json"]["result"] == "compatible"
    assert report["status"] == "ok"


def test_as_int_012_malformed_fail_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    target = vault / "generated" / "ops" / "event-tombstones.json"
    target.parent.mkdir(parents=True)
    target.write_text("{not-json", encoding="utf-8")
    report = scan_compat(vault)
    row = next(r for r in report["findings"] if r["path"].endswith("event-tombstones.json"))
    assert row["result"] == "malformed"
    assert report["status"] == "error"


def test_as_int_012_migrate_dry_run_never_mutates(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    record_explicit_tombstone(vault, project_id="proj-a", event_id="AE-001")
    tombs = vault / "generated" / "ops" / "event-tombstones.json"
    before = tombs.read_bytes()
    report = migrate_dry_run(vault)
    assert report["mode"] == "migrate-dry-run"
    assert report["status"] == "dry-run"
    assert tombs.read_bytes() == before
    validate_record(report, "schema-compat-report")


def test_as_int_012_migrate_candidate_on_drift(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    path = vault / "generated" / "ops" / "event-tombstones.json"
    path.parent.mkdir(parents=True)
    # Declares a schema id but fails shipped schema validation → migrate-candidate.
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "schema": "atlas.event_tombstone.index.v0-legacy",
                "tombstones": "not-an-array",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    report = build_report(vault, mode="migrate-dry-run")
    row = next(r for r in report["findings"] if r["path"].endswith("event-tombstones.json"))
    assert row["result"] == "migrate-candidate"
    assert "no auto-apply" in row["detail"]
    assert report["counts"]["migrate_candidate"] == 1


def test_as_int_012_unknown_schema_kind(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    report = build_report(
        vault,
        targets=[ScanTarget("generated/ops/x.json", "not-a-real-kind")],
        write=False,
    )
    assert report["findings"][0]["result"] == "unknown-schema"
    assert report["status"] == "error"


def test_as_int_012_detect_schema_identity() -> None:
    schema, version = detect_schema_identity({"schema": "atlas.x.v1", "schema_version": 1})
    assert schema == "atlas.x.v1"
    assert version == 1


def test_as_int_012_refuses_path_escape(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(SchemaCompatError, match="unsafe relative path"):
        build_report(
            vault,
            targets=[ScanTarget("../outside.json", "event-tombstone-index")],
            write=False,
        )


def test_as_int_012_deterministic_report(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    first = scan_compat(vault)
    second = scan_compat(vault)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
