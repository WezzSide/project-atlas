"""AS-DEMO-2.2-RECOVERY-ID-001 — vault identity bootstrap for recovery.

D-PROJECT-ATLAS-CLOUD-DEMO-RECOVERY-019.

Fresh product bootstrap must mint a canonical ``.atlas/vault.json`` so the
normal stranger pipeline is recovery-capable. Snapshot remains non-minting;
malformed / wrong / escaped identities fail closed with no silent rotation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from project_atlas.backup import BackupError, create_snapshot, read_vault_logical_id
from project_atlas.scaffold import ScaffoldError, create_scaffold
from project_atlas.vault_identity import (
    DEFAULT_VAULT_ID,
    VaultIdentityError,
    ensure_vault_identity,
    read_vault_identity,
)


def test_ensure_mints_absent_identity(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    identity = ensure_vault_identity(vault)
    marker = vault / ".atlas" / "vault.json"
    assert marker.is_file()
    raw = json.loads(marker.read_text(encoding="utf-8"))
    assert raw["vault_id"] == DEFAULT_VAULT_ID
    assert raw["vault_uuid"] == identity.vault_uuid
    assert raw["schema_version"] == 1


def test_ensure_preserves_existing_matching_identity(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    first = ensure_vault_identity(vault, vault_id="atlas-main")
    before = (vault / ".atlas" / "vault.json").read_bytes()
    second = ensure_vault_identity(vault, vault_id="atlas-main")
    after = (vault / ".atlas" / "vault.json").read_bytes()
    assert before == after
    assert second.vault_uuid == first.vault_uuid
    assert second.vault_id == first.vault_id


def test_ensure_fails_closed_on_vault_id_mismatch(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    ensure_vault_identity(vault, vault_id="atlas-main")
    before = (vault / ".atlas" / "vault.json").read_bytes()
    with pytest.raises(VaultIdentityError, match="existing Vault ID differs"):
        ensure_vault_identity(vault, vault_id="other-vault")
    assert (vault / ".atlas" / "vault.json").read_bytes() == before


@pytest.mark.parametrize(
    "payload",
    [
        "{not-json",
        "[]",
        "{}",
        '{"vault_id": "atlas-main"}',
        '{"vault_uuid": "only-uuid"}',
        '{"vault_id": "", "vault_uuid": "x"}',
        '{"vault_id": "bad id!", "vault_uuid": "x"}',
    ],
)
def test_ensure_fails_closed_on_malformed_identity(tmp_path: Path, payload: str) -> None:
    vault = tmp_path / "vault"
    marker = vault / ".atlas" / "vault.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(payload + "\n", encoding="utf-8")
    before = marker.read_bytes()
    with pytest.raises(VaultIdentityError):
        ensure_vault_identity(vault, vault_id="atlas-main")
    assert marker.read_bytes() == before


@pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "O_DIRECTORY"),
    reason="VI-001 dirfd renameat TOCTOU containment is POSIX-only",
)
def test_write_atomic_survives_parent_symlink_swap(tmp_path: Path) -> None:
    """VI-001: concurrent .atlas → symlink swap must not escape the vault."""
    from unittest import mock

    from project_atlas import vault_identity as vi

    vault = tmp_path / "vault"
    evil = tmp_path / "evil"
    vault.mkdir()
    evil.mkdir()
    (vault / ".atlas").mkdir()

    real_mkstemp = vi.tempfile.mkstemp

    def _mkstemp_and_swap(*args: object, **kwargs: object) -> tuple[int, str]:
        fd, name = real_mkstemp(*args, **kwargs)
        atlas = vault / ".atlas"
        # Swap parent for an outside directory after temp creation.
        os.rename(atlas, tmp_path / "orphaned-atlas")
        atlas.symlink_to(evil, target_is_directory=True)
        return fd, name

    with (
        mock.patch.object(vi.tempfile, "mkstemp", side_effect=_mkstemp_and_swap),
        pytest.raises(VaultIdentityError, match=r"symlink/reparse escape"),
    ):
        ensure_vault_identity(vault, vault_id="atlas-main")

    assert not (evil / "vault.json").exists()
    # Either no marker under the symlink, or ensure failed before publish.
    assert not (vault / ".atlas" / "vault.json").is_file() or (vault / ".atlas").is_symlink()


def test_ensure_fails_closed_on_symlinked_identity(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    outside = tmp_path / "outside-identity.json"
    vault.mkdir()
    (vault / ".atlas").mkdir()
    outside.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "vault_id": "atlas-main",
                "vault_uuid": "escaped-uuid",
                "name": "Atlas Vault",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    marker = vault / ".atlas" / "vault.json"
    marker.symlink_to(outside)
    with pytest.raises(VaultIdentityError, match=r"symlink/reparse escape"):
        ensure_vault_identity(vault, vault_id="atlas-main")
    with pytest.raises(VaultIdentityError, match=r"symlink/reparse escape"):
        read_vault_identity(vault)


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX directory mode bits do not deny writes on Windows NTFS",
)
def test_ensure_fails_closed_on_unwritable_identity_path(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    atlas = vault / ".atlas"
    atlas.mkdir()
    os.chmod(atlas, 0o555)
    try:
        with pytest.raises(VaultIdentityError, match=r"unable to write|Permission"):
            ensure_vault_identity(vault, vault_id="atlas-main")
        assert not (atlas / "vault.json").exists()
    finally:
        os.chmod(atlas, 0o755)


def test_init_bootstrap_idempotency_preserves_identity_bytes(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    create_scaffold(vault)
    marker = vault / ".atlas" / "vault.json"
    first = marker.read_bytes()
    first_id = json.loads(first)["vault_uuid"]
    create_scaffold(vault)
    second = marker.read_bytes()
    assert second == first
    assert json.loads(second)["vault_uuid"] == first_id


def test_dry_run_does_not_mint_identity(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    create_scaffold(vault, dry_run=True)
    assert not (vault / ".atlas" / "vault.json").exists()


def test_scaffold_rejects_mismatched_existing_identity(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    create_scaffold(vault, vault_id="atlas-main")
    with pytest.raises(ScaffoldError, match="existing Vault ID differs"):
        create_scaffold(vault, vault_id="other-vault")


def test_snapshot_still_non_minting_without_identity(tmp_path: Path) -> None:
    """Snapshot must not invent identity (trust property preserved)."""
    vault = tmp_path / "vault"
    create_scaffold(vault)
    marker = vault / ".atlas" / "vault.json"
    marker.unlink()
    marker.parent.rmdir()
    with pytest.raises(BackupError, match="missing vault identity"):
        create_snapshot(vault, tmp_path / "snap.tar.gz")
    assert not (vault / ".atlas" / "vault.json").exists()


def test_read_vault_logical_id_after_init(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    create_scaffold(vault)
    logical = read_vault_logical_id(vault)
    assert logical  # uuid or vault_id — non-empty
    identity = read_vault_identity(vault)
    assert logical in {identity.vault_uuid, identity.vault_id}
