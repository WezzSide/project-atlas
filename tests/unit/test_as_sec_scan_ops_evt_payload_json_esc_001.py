"""AS-SEC-SCAN-OPS-EVT-PAYLOAD-JSON-ESC-001 — decoded payload refs must not persist."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.ops_events import OpsEventError, apply_retention
from project_atlas.secrets import scan_text

TOKEN = "AKIAAAAAAAAAAAAAAAAA"
ESC = r"\u0041KIAAAAAAAAAAAAAAAAA"


def test_json_unicode_escape_payload_ref_is_not_rewritten(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    stream = vault / "generated" / "ops" / "events" / "stream.jsonl"
    raw_line = (
        "{"
        '"schema":"atlas.ops.event.v1",'
        '"truth_plane":"operational",'
        '"authority_plane":"none",'
        '"note":"OPERATIONAL EVENT \\u2260 PROJECT AUTHORITY",'
        '"event_id":"OPS-EVT-SYNC-PLANNED",'
        '"sequence":1,'
        '"event_uid":"aaaaaaaaaaaaaaaa",'
        '"severity":"INFO",'
        f'"payload":{{"ref":"{ESC}"}},'
        '"evidence_refs":["ref-1"],'
        '"generated":{"by":"probe"}'
        "}\n"
    )
    manifest = {
        "schema": "atlas.ops.event_stream.v1",
        "truth_plane": "operational",
        "authority_plane": "none",
        "note": "OPERATIONAL EVENT STREAM \u2260 PROJECT AUTHORITY",
        "stream_path": "generated/ops/events/stream.jsonl",
        "next_sequence": 2,
        "event_count": 1,
        "retention": {"max_events": 10000, "max_bytes": 8388608},
        "last_event_uid": "aaaaaaaaaaaaaaaa",
        "generated": {"by": "probe"},
    }
    stream.parent.mkdir(parents=True)
    stream.write_text(raw_line, encoding="utf-8")
    (stream.parent / "stream-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    assert scan_text(raw_line) == []
    assert json.loads(raw_line)["payload"]["ref"] == TOKEN
    with pytest.raises(OpsEventError, match="secret"):
        apply_retention(vault)
    written = stream.read_text(encoding="utf-8")
    assert TOKEN not in written
    assert scan_text(written) == []


def test_json_unicode_escape_evidence_ref_is_not_rewritten(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    stream = vault / "generated" / "ops" / "events" / "stream.jsonl"
    raw_line = (
        "{"
        '"schema":"atlas.ops.event.v1",'
        '"truth_plane":"operational",'
        '"authority_plane":"none",'
        '"note":"OPERATIONAL EVENT \\u2260 PROJECT AUTHORITY",'
        '"event_id":"OPS-EVT-SYNC-PLANNED",'
        '"sequence":1,'
        '"event_uid":"aaaaaaaaaaaaaaaa",'
        '"severity":"INFO",'
        '"payload":{},'
        f'"evidence_refs":["{ESC}"],'
        '"generated":{"by":"probe"}'
        "}\n"
    )
    manifest = {
        "schema": "atlas.ops.event_stream.v1",
        "truth_plane": "operational",
        "authority_plane": "none",
        "note": "OPERATIONAL EVENT STREAM \u2260 PROJECT AUTHORITY",
        "stream_path": "generated/ops/events/stream.jsonl",
        "next_sequence": 2,
        "event_count": 1,
        "retention": {"max_events": 10000, "max_bytes": 8388608},
        "last_event_uid": "aaaaaaaaaaaaaaaa",
        "generated": {"by": "probe"},
    }
    stream.parent.mkdir(parents=True)
    stream.write_text(raw_line, encoding="utf-8")
    (stream.parent / "stream-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    assert scan_text(raw_line) == []
    assert json.loads(raw_line)["evidence_refs"][0] == TOKEN
    with pytest.raises(OpsEventError, match="secret"):
        apply_retention(vault)
    written = stream.read_text(encoding="utf-8")
    assert TOKEN not in written
    assert scan_text(written) == []
