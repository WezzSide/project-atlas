"""AS-CTRL-VAULT-ID-F1 — control-plane vault identity must not follow symlinks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_control.vault_identity import read, resolve


def test_read_refuses_symlinked_vault_json(tmp_path: Path) -> None:
    foreign = tmp_path / "foreign"
    local = tmp_path / "local"
    (foreign / ".atlas").mkdir(parents=True)
    (local / ".atlas").mkdir(parents=True)
    (foreign / ".atlas" / "vault.json").write_text(
        json.dumps(
            {
                "vault_id": "vault-OTHER",
                "vault_uuid": "11111111-1111-4111-8111-111111111111",
                "name": "Foreign",
            }
        ),
        encoding="utf-8",
    )
    (local / ".atlas" / "vault.json").symlink_to(foreign / ".atlas" / "vault.json")
    with pytest.raises(ValueError, match="symlink/reparse escape"):
        read(local)


def test_resolve_refuses_foreign_identity_via_symlink(tmp_path: Path) -> None:
    foreign = tmp_path / "foreign"
    local = tmp_path / "local"
    (foreign / ".atlas").mkdir(parents=True)
    (local / ".atlas").mkdir(parents=True)
    (foreign / ".atlas" / "vault.json").write_text(
        json.dumps(
            {
                "vault_id": "vault-OTHER",
                "vault_uuid": "11111111-1111-4111-8111-111111111111",
                "name": "Foreign",
            }
        ),
        encoding="utf-8",
    )
    (local / ".atlas" / "vault.json").symlink_to(foreign / ".atlas" / "vault.json")
    with pytest.raises(ValueError, match="symlink/reparse escape"):
        resolve(cli_root=local, required_id="vault-OTHER", required_uuid=None)
