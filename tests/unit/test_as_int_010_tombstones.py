"""AS-INT-010 removed-package / deletion tombstone projection tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.event_retention import apply_event_retention, default_policy
from project_atlas.event_tombstones import (
    TombstoneError,
    list_tombstones,
    projection_inventory,
    record_explicit_tombstone,
    record_retention_tombstones,
)
from project_atlas.schema import validate_record


def _write_unit(vault: Path, project: str, event: str, *, payload: str = "x") -> None:
    package = vault / "sources" / "agent-events" / project / event
    package.mkdir(parents=True, exist_ok=True)
    (package / "event.md").write_text(f"# {event}\n{payload}\n", encoding="utf-8")
    receipt_dir = vault / "receipts" / "agent-events" / project
    receipt_dir.mkdir(parents=True, exist_ok=True)
    (receipt_dir / f"{event}.yaml").write_text(
        f"receipt_id: {event}\nstatus: valid\nevent_id: {event}\n",
        encoding="utf-8",
    )


def _write_policy(vault: Path, *, max_packages: int, max_bytes: int = 10_000_000) -> None:
    policy = default_policy(max_packages=max_packages, max_bytes=max_bytes)
    path = vault / ".atlas" / "retention-policy.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_as_int_010_retention_records_tombstones(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    for event in ("AE-001", "AE-002", "AE-003"):
        _write_unit(vault, "proj-a", event)
    concept = vault / "projects" / "proj-a" / "concepts" / "note.md"
    concept.parent.mkdir(parents=True)
    concept.write_text("# keep\n", encoding="utf-8")
    _write_policy(vault, max_packages=1)

    report = apply_event_retention(vault)
    assert report["status"] == "applied"
    tombs = list_tombstones(vault)
    assert {t["unit_key"] for t in tombs} == {"proj-a/AE-001", "proj-a/AE-002"}
    assert all(t["state"] == "deleted" and t["reason"] == "retention" for t in tombs)
    assert concept.is_file()
    index_path = vault / "generated" / "ops" / "event-tombstones.json"
    assert index_path.is_file()
    loaded = json.loads(index_path.read_text(encoding="utf-8"))
    validate_record(loaded, "event-tombstone-index")
    assert "at" not in loaded["generated"]


def test_as_int_010_projection_inventory_keeps_deleted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    for event in ("AE-001", "AE-002"):
        _write_unit(vault, "proj-a", event)
    _write_policy(vault, max_packages=1)
    apply_event_retention(vault)
    inventory = projection_inventory(vault)
    by_key = {row["unit_key"]: row for row in inventory}
    assert by_key["proj-a/AE-002"]["state"] == "present"
    assert by_key["proj-a/AE-001"]["state"] == "deleted"


def test_as_int_010_explicit_tombstone(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    index = record_explicit_tombstone(vault, project_id="proj-b", event_id="AE-x")
    validate_record(index, "event-tombstone-index")
    assert index["tombstones"][0]["reason"] == "explicit"
    assert index["tombstones"][0]["unit_key"] == "proj-b/AE-x"


def test_as_int_010_refuses_layer_b_path(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(TombstoneError, match="outside retention roots"):
        record_retention_tombstones(
            vault,
            removed_unit_keys=["proj-a/AE-001"],
            deleted_paths=["projects/proj-a/note.md"],
        )


def test_as_int_010_dry_run_retention_no_tombstones(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    for event in ("AE-001", "AE-002"):
        _write_unit(vault, "proj-a", event)
    report = apply_event_retention(vault, max_packages=1, max_bytes=10_000_000, dry_run=True)
    assert report["status"] == "dry-run"
    assert list_tombstones(vault) == []


def test_as_int_010_deterministic_merge(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    record_explicit_tombstone(vault, project_id="proj-a", event_id="AE-002")
    record_explicit_tombstone(vault, project_id="proj-a", event_id="AE-001")
    first = (vault / "generated" / "ops" / "event-tombstones.json").read_bytes()
    record_explicit_tombstone(vault, project_id="proj-a", event_id="AE-001")
    second = (vault / "generated" / "ops" / "event-tombstones.json").read_bytes()
    assert first == second
    keys = [t["unit_key"] for t in list_tombstones(vault)]
    assert keys == sorted(keys)
