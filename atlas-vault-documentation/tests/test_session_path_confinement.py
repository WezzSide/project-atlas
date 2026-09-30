"""AS-CTRL-SESSION-F1 — session_id must not escape .atlas/sessions/."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_control import agent_identity, receipt_gate, session

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "atlas_agent.py"


def _planted_state(session_id: str) -> dict[str, object]:
    return {
        "session": {"session_id": session_id, "project_id": "pwned", "task_id": "t"},
        "agent": {"agent_id": "a", "adapter_id": "generic"},
        "skill": {
            "id": "x",
            "version": "1",
            "sha256": "a" * 64,
        },
        "vault": {"vault_id": "v", "vault_uuid": "u"},
        "events": {
            "session-start": ["e1"],
            "validation": ["e2"],
            "completion": ["e3"],
        },
        "pipeline": {
            "captured": 3,
            "normalized": 3,
            "verified": 3,
            "routed": 3,
            "pending_spool": 0,
        },
        "preflight": {"strict": True},
    }


def test_traversal_session_id_rejected_on_load_and_save(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / ".atlas" / "sessions").mkdir(parents=True)
    outside = tmp_path / "evil-session.json"
    outside.write_text(json.dumps(_planted_state("../../../evil-session")), encoding="utf-8")
    with pytest.raises(ValueError, match="unsafe session id"):
        session.load(vault, "../../../evil-session")
    with pytest.raises(ValueError, match="unsafe session id"):
        session.save(vault, _planted_state("../../../escape-out"))
    assert not (tmp_path / "escape-out.json").exists()


def test_authentic_session_id_still_round_trips(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / ".atlas" / "sessions").mkdir(parents=True)
    sid = agent_identity.session_id("generic", "control-fixture")
    state = _planted_state(sid)
    written = session.save(vault, state)
    assert written.is_relative_to((vault / ".atlas" / "sessions").resolve())
    loaded = session.load(vault, sid)
    assert loaded["session"]["session_id"] == sid


def test_cli_receipt_traversal_does_not_write_outside_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / ".atlas" / "sessions").mkdir(parents=True)
    (vault / ".atlas" / "receipts").mkdir(parents=True)
    planted = tmp_path / "evil-session.json"
    planted.write_text(json.dumps(_planted_state("../../../escape-out")), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "receipt",
            "--vault-root",
            str(vault),
            "--session-id",
            "../../../evil-session",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert not (tmp_path / "escape-out.json").exists()
    assert list((vault / ".atlas" / "receipts").glob("ASR-*.json")) == []
    with pytest.raises(ValueError, match="unsafe session id"):
        receipt_gate.issue(vault, _planted_state("../../../escape-out"))
