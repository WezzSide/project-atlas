"""Logical Atlas Vault identity and root confinement."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class VaultIdentity:
    vault_id: str
    vault_uuid: str
    root: Path
    name: str


def _require_identity_path_contained(vault_root: Path, marker: Path) -> Path:
    """Refuse symlink / reparse escapes of ``.atlas/vault.json`` (SEC-ADV004B)."""
    atlas_dir = marker.parent
    if atlas_dir.is_symlink() or marker.is_symlink():
        raise ValueError(
            "refusing vault identity outside vault root (symlink/reparse escape): "
            f"{marker}"
        )
    real_root = Path(os.path.realpath(vault_root))
    real_marker = Path(os.path.realpath(marker))
    try:
        real_marker.relative_to(real_root)
    except ValueError as exc:
        raise ValueError(
            "refusing vault identity outside vault root (symlink/reparse escape): "
            f"{marker}"
        ) from exc
    return marker


def read(root: Path) -> VaultIdentity:
    resolved = root.expanduser().resolve()
    marker = resolved / ".atlas" / "vault.json"
    _require_identity_path_contained(resolved, marker)
    if not marker.is_file():
        raise ValueError(f"Atlas Vault identity is missing: {marker}")
    data = json.loads(marker.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data.get("vault_id") or not data.get("vault_uuid"):
        raise ValueError("invalid Atlas Vault identity")
    return VaultIdentity(str(data["vault_id"]), str(data["vault_uuid"]), resolved, str(data.get("name", "Atlas Vault")))


def resolve(*, cli_root: Path | None, required_id: str | None, required_uuid: str | None, config: dict[str, Any] | None = None) -> VaultIdentity:
    config = config or {}
    root_value = cli_root or (Path(os.environ["ATLAS_VAULT_ROOT"]) if os.environ.get("ATLAS_VAULT_ROOT") else None)
    if root_value is None:
        vaults = config.get("vaults", {}) if isinstance(config.get("vaults", {}), dict) else {}
        entry = vaults.get(required_id or os.environ.get("ATLAS_VAULT_ID", ""), {})
        root_value = Path(str(entry["root"])) if isinstance(entry, dict) and entry.get("root") else None
    if root_value is None:
        raise ValueError("Atlas Vault root is not configured")
    identity = read(root_value)
    expected_id = required_id or os.environ.get("ATLAS_VAULT_ID")
    if expected_id and identity.vault_id != expected_id:
        raise ValueError(f"wrong Atlas Vault ID: expected {expected_id}, found {identity.vault_id}")
    if required_uuid and identity.vault_uuid != required_uuid:
        raise ValueError("wrong Atlas Vault UUID")
    return identity
