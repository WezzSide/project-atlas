"""AS-SEC-SCAN-ATLAS3-LEDGER-PAYLOAD-JSON-ESC-001 — decoded ledger payload must not persist."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.atlas3.contracts import OPS_RELATIVE, Atlas3Error
from project_atlas.atlas3.ledger import append_event
from project_atlas.secrets import scan_text

TOKEN = "AKIAAAAAAAAAAAAAAAAA"
ESC = r"\u0041KIAAAAAAAAAAAAAAAAA"


def test_json_unicode_escape_ledger_payload_ref_is_not_persisted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    raw = (
        '{"event_id":"evt-h0308-001","project_id":"harbor-api","kind":"observation",'
        '"event_type":"CONTEXT_INVALIDATED","observed_at":"2024-01-01T00:00:00Z",'
        f'"content_hash":"sha256:{"b" * 64}","payload":{{"ref":"{ESC}"}}}}'
    )
    event = json.loads(raw)
    assert scan_text(raw) == []
    assert event["payload"]["ref"] == TOKEN
    with pytest.raises(Atlas3Error) as exc:
        append_event(vault, "harbor-api", event=event)
    assert exc.value.code == "LEDGER_SECRET"
    path = vault / OPS_RELATIVE / "ledger" / "harbor-api.jsonl"
    if path.is_file():
        written = path.read_text(encoding="utf-8")
        assert TOKEN not in written
        assert scan_text(written) == []
    else:
        assert not path.exists()
