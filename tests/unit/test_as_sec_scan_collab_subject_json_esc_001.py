"""AS-SEC-SCAN-COLLAB-SUBJECT-JSON-ESC-001 — decoded session subjects must not persist."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.collab_live import CollabError, append_collab_action
from project_atlas.secrets import scan_text

TOKEN = "AKIAAAAAAAAAAAAAAAAA"


def test_json_unicode_escape_subject_is_not_rewritten(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    plant = vault / "generated" / "ops" / "collab" / "review-queue-session.json"
    plant.parent.mkdir(parents=True)
    raw = (
        '{\n  "schema_version": 1,\n  "session_id": "review-queue",\n'
        '  "kind": "review-queue",\n  "subject": "\\u0041KIAAAAAAAAAAAAAAAAA",\n'
        '  "closed": false,\n  "actions": [],\n  "operator_id": "local-operator"\n}\n'
    )
    plant.write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    with pytest.raises(CollabError, match="secret-content"):
        append_collab_action(vault, session_id="review-queue", action_name="comment", detail="note")
    written = plant.read_text(encoding="utf-8")
    assert TOKEN not in written
    assert "\\u0041KIAAAAAAAAAAAAAAAAA" in written
    assert scan_text(written) == []
