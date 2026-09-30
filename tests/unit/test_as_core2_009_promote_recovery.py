"""AS-CORE2-009 / CORE2-009: interrupted-write promote orphan recovery."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from project_atlas.backup import find_promote_orphans, parse_promote_orphan_name
from project_atlas.ingestion import (
    PromoteRecoveryResult,
    recover_promote_orphans,
)

TXN = "a" * 32
TXN_B = "b" * 32


def _stage_name(canonical: str, txn: str = TXN) -> str:
    return f".{canonical}.{txn}.atlas-stage"


def _backup_name(canonical: str, txn: str = TXN) -> str:
    return f".{canonical}.{txn}.atlas-backup"


def test_as_core2_009_parse_promote_orphan_name() -> None:
    assert parse_promote_orphan_name(_stage_name("project.md")) == (
        "project.md",
        TXN,
        "atlas-stage",
    )
    assert parse_promote_orphan_name(_backup_name("notes.v2.md")) == (
        "notes.v2.md",
        TXN,
        "atlas-backup",
    )
    assert parse_promote_orphan_name("project.md") is None
    assert parse_promote_orphan_name(".project.md.nothex.atlas-stage") is None
    assert parse_promote_orphan_name(f".project.md.{'g' * 32}.atlas-stage") is None


def test_as_core2_009_clean_vault_noop(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "projects").mkdir()
    result = recover_promote_orphans(vault)
    assert result == PromoteRecoveryResult(
        orphan_count=0, transactions_recovered=0, receipt_path=None
    )
    assert not (vault / "quarantine" / "promotion-recovery").exists()


def test_as_core2_009_stage_only_cleans_without_touching_canonical(
    tmp_path: Path,
) -> None:
    """C209-FR-003: stage-only abort clean leaves canonical bytes intact."""
    vault = tmp_path / "vault"
    target_dir = vault / "projects" / "demo"
    target_dir.mkdir(parents=True)
    canonical = target_dir / "project.md"
    original = b"canonical-pre-crash\n"
    canonical.write_bytes(original)
    before = canonical.stat().st_mtime_ns

    stage = target_dir / _stage_name("project.md")
    stage.write_bytes(b"staged-never-promoted\n")
    assert find_promote_orphans(vault)

    result = recover_promote_orphans(vault)

    assert result.orphan_count == 1
    assert result.transactions_recovered == 1
    assert result.receipt_path is not None
    assert canonical.read_bytes() == original
    assert canonical.stat().st_mtime_ns == before
    assert not stage.exists()
    assert find_promote_orphans(vault) == []

    receipt = json.loads(result.receipt_path.read_text(encoding="utf-8"))
    assert receipt["receipt_type"] == "promotion-recovery"
    assert receipt["generated"] == {"by": "atlas-core2-009"}
    assert "at" not in receipt["generated"]
    assert receipt["transactions"][0]["disposition"] == "abort_clean_stages"
    assert receipt["transactions"][0]["transaction_id"] == TXN


def test_as_core2_009_backup_restores_pre_txn_snapshot(tmp_path: Path) -> None:
    """C209-FR-004: backups restore pre-txn snapshot; stages removed."""
    vault = tmp_path / "vault"
    target_dir = vault / "projects" / "demo"
    target_dir.mkdir(parents=True)
    canonical = target_dir / "project.md"
    pre_txn = b"pre-txn-bytes\n"
    # Simulate crash after path->backup and staged->path for one file:
    # canonical holds new bytes; backup holds pre-txn; stage already consumed.
    canonical.write_bytes(b"partially-promoted-new\n")
    backup = target_dir / _backup_name("project.md")
    backup.write_bytes(pre_txn)
    # Second file still mid-flight: backup exists, path missing, stage present.
    other = target_dir / "claims.md"
    other_backup = target_dir / _backup_name("claims.md")
    other_stage = target_dir / _stage_name("claims.md")
    other_backup.write_bytes(b"claims-pre\n")
    other_stage.write_bytes(b"claims-new\n")
    # path absent (moved to backup, stage not yet applied)

    result = recover_promote_orphans(vault)

    assert result.orphan_count == 3
    assert canonical.read_bytes() == pre_txn
    assert other.read_bytes() == b"claims-pre\n"
    assert not backup.exists()
    assert not other_backup.exists()
    assert not other_stage.exists()
    assert find_promote_orphans(vault) == []

    receipt = json.loads(result.receipt_path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
    assert receipt["transactions"][0]["disposition"] == "abort_restore"
    assert sorted(receipt["transactions"][0]["restored"]) == [
        "projects/demo/claims.md",
        "projects/demo/project.md",
    ]


def test_as_core2_009_unparseable_orphan_fail_closed(tmp_path: Path) -> None:
    """C209-FR-002/005: unparseable names fail closed without mutating."""
    vault = tmp_path / "vault"
    target_dir = vault / "projects" / "demo"
    target_dir.mkdir(parents=True)
    canonical = target_dir / "project.md"
    canonical.write_bytes(b"keep\n")
    bad = target_dir / ".project.md.NOT_A_VALID_TX.atlas-stage"
    bad.write_bytes(b"junk\n")

    with pytest.raises(ValueError, match="unparseable promote orphan"):
        recover_promote_orphans(vault)

    assert bad.exists()
    assert canonical.read_bytes() == b"keep\n"
    assert not (vault / "quarantine" / "promotion-recovery" / "index.json").exists()


def test_as_core2_009_restore_failure_preserves_artifacts(tmp_path: Path) -> None:
    """C209-FR-005: restore OSError fail-closed preserves remaining artifacts."""
    vault = tmp_path / "vault"
    target_dir = vault / "projects" / "demo"
    target_dir.mkdir(parents=True)
    canonical = target_dir / "project.md"
    canonical.write_bytes(b"new\n")
    backup = target_dir / _backup_name("project.md")
    backup.write_bytes(b"old\n")

    def boom(source: Path, destination: Path) -> None:
        raise OSError("injected restore failure")

    with (
        patch("project_atlas.ingestion._replace_path", side_effect=boom),
        pytest.raises(RuntimeError, match="fail-closed"),
    ):
        recover_promote_orphans(vault)

    assert backup.exists()
    assert canonical.read_bytes() == b"new\n"
    assert find_promote_orphans(vault)


def test_as_core2_009_ingest_preflight_runs_recovery(tmp_path: Path) -> None:
    """C209-FR-001: ingest preflight invokes recover_promote_orphans."""
    from project_atlas import ingestion as ingestion_module

    vault = tmp_path / "vault"
    vault.mkdir()
    source = tmp_path / "src"
    source.mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_root": str(source),
                "duplicates": {},
                "inventory_sha256": "0" * 64,
                "sources": [],
            }
        ),
        encoding="utf-8",
    )
    called: list[Path] = []

    def fake_recover(path: Path) -> PromoteRecoveryResult:
        called.append(path)
        return PromoteRecoveryResult(0, 0, None)

    with (
        patch.object(ingestion_module, "recover_promote_orphans", side_effect=fake_recover),
        patch.object(
            ingestion_module,
            "_ingest",
            return_value={"schema_version": 1, "sources": []},
        ),
    ):
        result = ingestion_module.ingest(manifest, vault, authorized_source_root=source)

    assert called == [vault.resolve()]
    assert result["schema_version"] == 1


def test_as_core2_009_multi_txn_deterministic_receipt(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    d = vault / "projects" / "x"
    d.mkdir(parents=True)
    (d / _stage_name("a.md", TXN_B)).write_bytes(b"s\n")
    (d / _stage_name("b.md", TXN)).write_bytes(b"s\n")

    first = recover_promote_orphans(vault)
    assert first.receipt_path is not None
    text_a = first.receipt_path.read_text(encoding="utf-8")

    # Re-inject identical orphans and recover again — receipt bytes stable.
    (d / _stage_name("a.md", TXN_B)).write_bytes(b"s\n")
    (d / _stage_name("b.md", TXN)).write_bytes(b"s\n")
    second = recover_promote_orphans(vault)
    assert second.receipt_path is not None
    assert second.receipt_path.read_text(encoding="utf-8") == text_a

    payload = json.loads(text_a)
    assert [t["transaction_id"] for t in payload["transactions"]] == sorted([TXN, TXN_B])
