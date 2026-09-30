"""AS-INT-009 raw-package and receipt retention policy tests.

Contract: AS-INT-009-PACKAGE-CONTRACT.md (INT9-FR-001..007)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.cli import main
from project_atlas.event_retention import (
    RetentionError,
    apply_event_retention,
    default_policy,
    inventory_units,
    load_policy,
    maybe_apply_after_ingest,
)
from project_atlas.schema import validate_record


def _write_unit(vault: Path, project: str, event: str, *, payload: str = "x") -> None:
    package = vault / "sources" / "agent-events" / project / event
    package.mkdir(parents=True, exist_ok=True)
    (package / "event.md").write_text(f"# {event}\n{payload}\n", encoding="utf-8")
    (package / "event.json").write_text("{}", encoding="utf-8")
    (package / "provenance.json").write_text("{}", encoding="utf-8")
    (package / "receipt.yaml").write_text("status: valid\n", encoding="utf-8")
    receipt_dir = vault / "receipts" / "agent-events" / project
    receipt_dir.mkdir(parents=True, exist_ok=True)
    (receipt_dir / f"{event}.yaml").write_text(
        f"receipt_id: {event}\nstatus: valid\nevent_id: {event}\n",
        encoding="utf-8",
    )


def _write_policy(vault: Path, *, max_packages: int, max_bytes: int) -> None:
    policy = default_policy(max_packages=max_packages, max_bytes=max_bytes)
    path = vault / ".atlas" / "retention-policy.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_as_int_009_default_policy_schema_valid() -> None:
    policy = default_policy(max_packages=3, max_bytes=4096)
    validate_record(policy, "event-retention-policy")


def test_as_int_009_malformed_policy_fail_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    path = vault / ".atlas" / "retention-policy.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"max_packages": "nope"}\n', encoding="utf-8")
    with pytest.raises(RetentionError, match="malformed"):
        load_policy(vault)


def test_as_int_009_count_cap_keeps_lexicographic_tail(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    for event in ("AE-001", "AE-002", "AE-003", "AE-004"):
        _write_unit(vault, "proj-a", event)
    # Layer B decoy — must never be deleted.
    concept = vault / "projects" / "proj-a" / "concepts" / "note.md"
    concept.parent.mkdir(parents=True)
    concept.write_text("# keep me\n", encoding="utf-8")

    report = apply_event_retention(vault, max_packages=2, max_bytes=10_000_000)
    assert report["status"] == "applied"
    assert report["counts"]["units_removed"] == 2
    assert report["removed_units"] == ["proj-a/AE-001", "proj-a/AE-002"]
    assert not (vault / "sources" / "agent-events" / "proj-a" / "AE-001").exists()
    assert not (vault / "receipts" / "agent-events" / "proj-a" / "AE-001.yaml").exists()
    assert (vault / "sources" / "agent-events" / "proj-a" / "AE-003").is_dir()
    assert (vault / "sources" / "agent-events" / "proj-a" / "AE-004").is_dir()
    assert concept.is_file()
    report_path = vault / "generated" / "ops" / "retention-report.json"
    assert report_path.is_file()
    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    assert loaded == json.loads(json.dumps(loaded, sort_keys=True))
    assert "generated" in loaded and "at" not in loaded["generated"]
    validate_record(loaded, "event-retention-report")


def test_as_int_009_size_cap_drops_from_front(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_unit(vault, "proj-a", "AE-001", payload="small")
    _write_unit(vault, "proj-a", "AE-002", payload="x" * 4000)
    _write_unit(vault, "proj-a", "AE-003", payload="y" * 4000)
    units = inventory_units(vault)
    assert len(units) == 3
    # Cap just under the two large units so only the lexicographic tail that
    # fits remains (drop from front until under budget).
    total = sum(u.size_bytes for u in units)
    report = apply_event_retention(
        vault,
        max_packages=10,
        max_bytes=max(units[-1].size_bytes, total // 3),
    )
    assert report["status"] == "applied"
    assert report["counts"]["units_kept"] >= 1
    assert report["counts"]["units_removed"] >= 1
    assert report["counts"]["bytes_kept"] <= report["policy"]["max_bytes"]


def test_as_int_009_dry_run_does_not_delete(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    for event in ("AE-001", "AE-002", "AE-003"):
        _write_unit(vault, "proj-a", event)
    report = apply_event_retention(vault, max_packages=1, max_bytes=10_000_000, dry_run=True)
    assert report["status"] == "dry-run"
    assert report["applied"] is False
    assert report["counts"]["units_removed"] == 2
    assert (vault / "sources" / "agent-events" / "proj-a" / "AE-001").is_dir()
    assert report["deleted_paths"] == []


def test_as_int_009_skipped_without_policy(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_unit(vault, "proj-a", "AE-001")
    report = apply_event_retention(vault)
    assert report["status"] == "skipped-no-policy"
    assert (vault / "sources" / "agent-events" / "proj-a" / "AE-001").is_dir()


def test_as_int_009_policy_file_drives_apply(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    for event in ("AE-001", "AE-002", "AE-003"):
        _write_unit(vault, "proj-a", event)
    _write_policy(vault, max_packages=1, max_bytes=10_000_000)
    report = apply_event_retention(vault)
    assert report["status"] == "applied"
    assert report["counts"]["units_kept"] == 1
    assert (vault / "sources" / "agent-events" / "proj-a" / "AE-003").is_dir()


def test_as_int_009_maybe_apply_after_ingest_requires_policy(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_unit(vault, "proj-a", "AE-001")
    assert maybe_apply_after_ingest(vault) is None
    _write_policy(vault, max_packages=10, max_bytes=10_000_000)
    report = maybe_apply_after_ingest(vault)
    assert report is not None
    assert report["status"] in {"no-op", "applied"}


def test_as_int_009_refuses_layer_b_delete(tmp_path: Path) -> None:
    from project_atlas.event_retention import _assert_allowed_delete

    vault = tmp_path / "vault"
    vault.mkdir()
    concept = vault / "projects" / "proj-a" / "note.md"
    concept.parent.mkdir(parents=True)
    concept.write_text("x\n", encoding="utf-8")
    with pytest.raises(RetentionError, match="forbidden prefix"):
        _assert_allowed_delete(vault, concept)


def test_as_int_009_cli_apply_json(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    for event in ("AE-001", "AE-002"):
        _write_unit(vault, "proj-a", event)
    code = main(
        [
            "retention",
            "apply",
            "--vault",
            str(vault),
            "--max-packages",
            "1",
            "--max-bytes",
            "10000000",
            "--json",
        ]
    )
    assert code == 0
    assert not (vault / "sources" / "agent-events" / "proj-a" / "AE-001").exists()
    assert (vault / "sources" / "agent-events" / "proj-a" / "AE-002").is_dir()


def test_as_int_009_orphan_receipt_is_inventoryed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    receipt_dir = vault / "receipts" / "agent-events" / "proj-a"
    receipt_dir.mkdir(parents=True)
    (receipt_dir / "AE-orphan.yaml").write_text(
        "receipt_id: AE-orphan\nstatus: valid\nevent_id: AE-orphan\n",
        encoding="utf-8",
    )
    units = inventory_units(vault)
    assert len(units) == 1
    assert units[0].unit_key == "proj-a/AE-orphan"
    with pytest.raises(RetentionError):
        apply_event_retention(vault, max_packages=0, max_bytes=10_000_000)
    report = apply_event_retention(vault, max_packages=1, max_bytes=10_000_000)
    assert report["status"] == "no-op"
    assert (receipt_dir / "AE-orphan.yaml").is_file()


def test_as_int_009_deterministic_report_bytes(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    for event in ("AE-001", "AE-002", "AE-003"):
        _write_unit(vault, "proj-a", event)
    first = apply_event_retention(vault, max_packages=2, max_bytes=10_000_000, dry_run=True)
    second = apply_event_retention(vault, max_packages=2, max_bytes=10_000_000, dry_run=True)
    assert first == second
    raw = (vault / "generated" / "ops" / "retention-report.json").read_bytes()
    assert raw == (json.dumps(first, indent=2, sort_keys=True) + "\n").encode("utf-8")
