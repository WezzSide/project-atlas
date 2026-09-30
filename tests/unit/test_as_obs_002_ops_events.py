"""AS-OBS-002 Operational Event Model (OPS-EVT-*) certification tests.

Contract: gen4-next-wave-parallel-001/AS-OBS-002-PACKAGE-CONTRACT.md
Parent: gen4-parallel-wave-006/AS-OBSERVABILITY-CONTRACT.md §5

Invariants:
- truth_plane operational / authority_plane none
- Append-only under generated/ops/events/** only
- No fabricated events (evidence_refs required; health seed ≠ transition)
- NFR-004 secret payloads rejected
- Consume-only OBS-001 snapshot; no ops_health rewrite / no truth-plane drift
- Retention count/size caps; deterministic replay
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.cli import main
from project_atlas.ops_events import (
    EVENT_CATALOG,
    OpsEventError,
    append_event,
    apply_retention,
    build_event,
    event_to_jsonl_line,
    read_events,
    record_health_transition,
)
from project_atlas.ops_health import emit_health_snapshot
from project_atlas.schema import validate_record


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, (dict, list)):
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        path.write_text(str(payload), encoding="utf-8")


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write(
        vault / ".atlas" / "vault.json",
        {"vault_id": "atlas-main", "vault_uuid": "fixture-vault-uuid"},
    )
    (vault / "state" / "authoritative-state").mkdir(parents=True)
    (vault / "state" / "current-state").mkdir(parents=True)
    (vault / "state" / "claims").mkdir(parents=True)
    _write(vault / "state" / "authoritative-state" / "probe.json", {"ok": True})
    _write(vault / "state" / "current-state" / "probe.json", {"ok": True})
    _write(vault / "state" / "claims" / "probe.json", {"ok": True})
    return vault


def test_catalog_matches_wave006_minimum() -> None:
    assert "OPS-EVT-HEALTH-TRANSITION" in EVENT_CATALOG
    assert "OPS-EVT-SYNC-FAILED" in EVENT_CATALOG
    assert "OPS-EVT-QUARANTINE-ADDED" in EVENT_CATALOG


def test_build_event_schema_and_planes() -> None:
    event = build_event(
        event_id="OPS-EVT-CI-FAILED",
        sequence=1,
        payload={"workflow": "ci", "commit": "abc"},
        evidence_refs=["generated/ops/evidence/ci-status.json"],
    )
    validate_record(event, "ops-event")
    assert event["truth_plane"] == "operational"
    assert event["authority_plane"] == "none"
    assert "generated.at" not in event
    assert (
        event["event_uid"]
        == build_event(
            event_id="OPS-EVT-CI-FAILED",
            sequence=1,
            payload={"workflow": "ci", "commit": "abc"},
            evidence_refs=["generated/ops/evidence/ci-status.json"],
        )["event_uid"]
    )


def test_no_fabricated_event_without_evidence() -> None:
    with pytest.raises(OpsEventError, match="evidence_refs"):
        build_event(event_id="OPS-EVT-SYNC-SUCCEEDED", sequence=1, payload={"project_id": "p"})


def test_unknown_event_id_rejected() -> None:
    with pytest.raises(OpsEventError, match="unknown OPS-EVT"):
        build_event(
            event_id="OPS-EVT-NOT-REAL",
            sequence=1,
            evidence_refs=["ref"],
        )


def test_nfr004_secret_payload_rejected() -> None:
    with pytest.raises(OpsEventError, match="NFR-004"):
        build_event(
            event_id="OPS-EVT-SYNC-FAILED",
            sequence=1,
            payload={"error_code": "X", "note": "password=supersecretvalue"},
            evidence_refs=["ref"],
        )


def test_append_and_replay_deterministic(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    first = append_event(
        vault,
        event_id="OPS-EVT-BACKUP-COMPLETED",
        payload={"backup_id": "b1", "domains": ["claims"]},
        evidence_refs=["generated/ops/evidence/backup-receipt.json"],
    )
    second = append_event(
        vault,
        event_id="OPS-EVT-SPOOL-DRAINED",
        payload={"count_drained": 2},
        evidence_refs=["generated/ops/evidence/spool.json"],
    )
    events = read_events(vault)
    assert [e["event_uid"] for e in events] == [first["event_uid"], second["event_uid"]]
    assert events[0]["sequence"] == 1
    assert events[1]["sequence"] == 2
    replay = read_events(vault)
    assert [event_to_jsonl_line(e) for e in events] == [event_to_jsonl_line(e) for e in replay]
    manifest = json.loads(
        (vault / "generated" / "ops" / "events" / "stream-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    validate_record(manifest, "ops-event-stream")
    assert manifest["authority_plane"] == "none"
    assert manifest["event_count"] == 2


def test_retention_keeps_newest(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    for idx in range(5):
        append_event(
            vault,
            event_id="OPS-EVT-SYNC-PLANNED",
            payload={"plan_id": f"p{idx}"},
            evidence_refs=[f"plan/{idx}"],
            apply_caps=False,
        )
    apply_retention(vault, max_events=2)
    events = read_events(vault)
    assert len(events) == 2
    assert events[0]["payload"]["plan_id"] == "p3"
    assert events[1]["payload"]["plan_id"] == "p4"
    # Receipts / truth planes untouched.
    assert (vault / "state" / "claims" / "probe.json").read_text(encoding="utf-8")


def test_health_transition_seed_then_emit(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    emit_health_snapshot(vault)  # unknown rollup on empty vault
    assert record_health_transition(vault) is None  # seed only
    assert read_events(vault) == []

    # Force a different estate via a second snapshot with sync failure evidence.
    _write(
        vault / "generated" / "ops" / "evidence" / "sync-failures.json",
        {"failures": [{"project_id": "proj-1", "error_code": "SYNC_FAILED"}]},
    )
    emit_health_snapshot(vault)
    event = record_health_transition(vault)
    assert event is not None
    assert event["event_id"] == "OPS-EVT-HEALTH-TRANSITION"
    assert event["payload"]["from"] == "unknown"
    assert event["payload"]["to"] in {"degraded", "unhealthy", "unknown"}
    assert event["payload"]["to"] != "unknown" or event["payload"]["from"] != event["payload"]["to"]
    # Idempotent when unchanged.
    assert record_health_transition(vault) is None
    assert len(read_events(vault)) == 1


def test_health_transition_requires_snapshot(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    with pytest.raises(OpsEventError, match="health snapshot missing"):
        record_health_transition(vault)


def test_consume_only_no_truth_plane_mtime_drift(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    emit_health_snapshot(vault)
    probes = [
        vault / "state" / "authoritative-state" / "probe.json",
        vault / "state" / "current-state" / "probe.json",
        vault / "state" / "claims" / "probe.json",
        vault / "generated" / "ops" / "health-snapshot.json",
    ]
    before = {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in probes}
    record_health_transition(vault)
    append_event(
        vault,
        event_id="OPS-EVT-ADAPTER-STALE",
        payload={"adapter_id": "generic"},
        evidence_refs=[".atlas/agent-readiness.yaml"],
    )
    for path, (mtime, content) in before.items():
        assert path.stat().st_mtime_ns == mtime
        assert path.read_bytes() == content


def test_cli_ops_events_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = _vault(tmp_path)
    emit_health_snapshot(vault)
    code = main(
        [
            "ops",
            "events",
            "--vault",
            str(vault),
            "--record-health-transitions",
            "--json",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == []  # seed only
    # Change health and record again.
    _write(
        vault / "generated" / "ops" / "evidence" / "sync-failures.json",
        {"failures": [{"project_id": "p", "error_code": "X"}]},
    )
    emit_health_snapshot(vault)
    code = main(
        [
            "ops",
            "events",
            "--vault",
            str(vault),
            "--record-health-transitions",
            "--json",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 1
    assert payload[0]["event_id"] == "OPS-EVT-HEALTH-TRANSITION"
    assert payload[0]["authority_plane"] == "none"
