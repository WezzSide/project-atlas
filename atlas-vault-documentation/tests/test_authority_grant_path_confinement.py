"""AS-CTRL-GRANT-F1 — grant_id must not escape the authority store."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_control import authority

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "atlas_agent.py"


def test_revoke_traversal_does_not_write_outside_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATLAS_AUTHORITY_ISSUER_KEY", "test-authority-issuer-key-32chars-min!!")
    outside = tmp_path / "outside"
    outside.mkdir()
    vault = tmp_path / "vault"
    store = vault / ".atlas" / "authority"
    (store / "grants").mkdir(parents=True)
    key = authority.resolve_issuer_key()
    payload = {
        "schema_version": 1,
        "grant_type": "atlas-authority-grant",
        "grant_id": "AAG-deadbeef",
        "purpose": authority.PURPOSE_PROMOTE_READINESS,
        "subject": {
            "adapter_id": "x",
            "skill_id": "y",
            "skill_version": "1",
            "skill_sha256": "a" * 64,
        },
        "issuer_id": "issuer",
        "requester_id": "req",
        "revoked": False,
        "authority_role": "grant",
        "receipt_is_authority": False,
    }
    payload["mac"] = authority.mac_for(payload, key)
    planted = outside / "evil-grant.json"
    planted.write_text(json.dumps(payload), encoding="utf-8")
    before = planted.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="unsafe grant id"):
        authority.revoke_grant(
            store=store,
            grant_id="../../../../outside/evil-grant",
        )
    assert planted.read_text(encoding="utf-8") == before
    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "revoke-authority",
            "--store",
            str(store),
            "--grant-id",
            "../../../../outside/evil-grant",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "ATLAS_AUTHORITY_ISSUER_KEY": "test-authority-issuer-key-32chars-min!!"},
    )
    assert result.returncode != 0
    assert planted.read_text(encoding="utf-8") == before


def test_honest_revoke_still_works(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_AUTHORITY_ISSUER_KEY", "test-authority-issuer-key-32chars-min!!")
    store = tmp_path / "authority"
    grant = authority.issue_grant(
        store=store,
        purpose=authority.PURPOSE_PROMOTE_READINESS,
        adapter_id="generic-cli-v1",
        skill_id="atlas-governed-work",
        skill_version="1.0.0",
        skill_sha256="a" * 64,
        issuer_id="owner",
        requester_id="agent",
    )
    revoked = authority.revoke_grant(store=store, grant_id=str(grant["grant_id"]))
    assert revoked["revoked"] is True
