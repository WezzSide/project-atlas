"""AS-OBS-003 Ops-report projection certification tests.

Contract: gen4-next-wave-parallel-001/AS-OBS-003-PACKAGE-CONTRACT.md
Parent: gen4-parallel-wave-006/AS-OBSERVABILITY-CONTRACT.md §8

Invariants:
- truth_plane operational / authority_plane none
- HEALTH ≠ TRUTH / OPS REPORT ≠ PROJECT AUTHORITY
- Consume OBS-001 snapshot; missing → unknown (never invent healthy)
- Optional OBS-002 events consume (no fabricate)
- Writes only generated/ops/ops-report.* (+ optional archive)
- Deterministic replay → byte-identical report
"""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.cli import main
from project_atlas.ops_events import append_event
from project_atlas.ops_health import emit_health_snapshot
from project_atlas.ops_report import (
    build_ops_report,
    emit_ops_report,
    report_to_json,
    report_to_markdown,
)
from project_atlas.schema import validate_record
from project_atlas.secrets import scan_text


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


def test_missing_snapshot_unknown_never_healthy(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    report = build_ops_report(vault)
    validate_record(report, "ops-report")
    assert report["truth_plane"] == "operational"
    assert report["authority_plane"] == "none"
    assert report["note"] == "OPERATIONAL METRIC ≠ PROJECT AUTHORITY"
    assert report["snapshot_status"] == "missing"
    assert report["rollup"]["estate"] == "unknown"
    assert report["rollup"]["estate"] != "healthy"
    assert report["signals"] == []
    assert report["events"]["present"] is False


def test_project_from_obs001_snapshot_deterministic(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    emit_health_snapshot(vault)
    first = build_ops_report(vault, include_events=False)
    second = build_ops_report(vault, include_events=False)
    validate_record(first, "ops-report")
    assert first["snapshot_status"] == "present"
    assert first["rollup"]["estate"] == "unknown"
    assert len(first["signals"]) >= 1
    signal_ids = [s["signal_id"] for s in first["signals"]]
    assert signal_ids == sorted(signal_ids)
    assert report_to_json(first) == report_to_json(second)
    assert report_to_markdown(first) == report_to_markdown(second)


def test_persist_ops_report_only(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    emit_health_snapshot(vault)
    probes = [
        vault / "state" / "authoritative-state" / "probe.json",
        vault / "state" / "current-state" / "probe.json",
        vault / "state" / "claims" / "probe.json",
        vault / "generated" / "ops" / "health-snapshot.json",
    ]
    before = {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in probes}
    report = emit_ops_report(vault, include_events=False)
    json_path = vault / "generated" / "ops" / "ops-report.json"
    md_path = vault / "generated" / "ops" / "ops-report.md"
    assert json_path.is_file()
    assert md_path.is_file()
    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert loaded == report
    assert "OPERATIONAL METRIC" in md_path.read_text(encoding="utf-8")
    for path, (mtime, content) in before.items():
        assert path.stat().st_mtime_ns == mtime
        assert path.read_bytes() == content
    # No dual-own of events/ or compile-cache/
    assert not (vault / "generated" / "ops" / "events").exists()
    assert not (vault / "generated" / "compile-cache").exists()


def test_optional_events_consume_no_fabricate(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    emit_health_snapshot(vault)
    # Absent stream → present False, empty items (no fabricate).
    bare = build_ops_report(vault, include_events=True)
    assert bare["events"]["present"] is False
    assert bare["events"]["items"] == []

    append_event(
        vault,
        event_id="OPS-EVT-CI-FAILED",
        payload={"workflow": "ci.yml", "commit": "abc"},
        evidence_refs=["generated/ops/evidence/ci-status.json"],
        apply_caps=False,
    )
    enriched = build_ops_report(vault, include_events=True)
    assert enriched["events"]["present"] is True
    assert enriched["events"]["count"] == 1
    assert enriched["events"]["items"][0]["event_id"] == "OPS-EVT-CI-FAILED"
    # Snapshot-only mode ignores events even when present.
    snapshot_only = build_ops_report(vault, include_events=False)
    assert snapshot_only["events"]["present"] is False
    assert snapshot_only["events"]["items"] == []


def test_replay_identical_snapshot_byte_identical_report(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    emit_health_snapshot(vault)
    a = emit_ops_report(vault, include_events=False)
    json_a = (vault / "generated" / "ops" / "ops-report.json").read_bytes()
    md_a = (vault / "generated" / "ops" / "ops-report.md").read_bytes()
    b = emit_ops_report(vault, include_events=False)
    json_b = (vault / "generated" / "ops" / "ops-report.json").read_bytes()
    md_b = (vault / "generated" / "ops" / "ops-report.md").read_bytes()
    assert report_to_json(a) == report_to_json(b)
    assert json_a == json_b
    assert md_a == md_b


def test_archive_last_n(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    emit_health_snapshot(vault)
    emit_ops_report(vault, include_events=False, archive=True, max_archive=2)
    emit_ops_report(vault, include_events=False, archive=True, max_archive=2)
    emit_ops_report(vault, include_events=False, archive=True, max_archive=2)
    archive = vault / "generated" / "ops" / "archive"
    jsons = sorted(archive.glob("ops-report-*.json"))
    assert len(jsons) == 2
    assert jsons[0].name == "ops-report-0002.json"
    assert jsons[1].name == "ops-report-0003.json"


def test_cli_ops_report(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    emit_health_snapshot(vault)
    code = main(["ops", "report", "--vault", str(vault), "--json"])
    assert code == 0
    assert (vault / "generated" / "ops" / "ops-report.json").is_file()
    assert (vault / "generated" / "ops" / "ops-report.md").is_file()


def test_json_unicode_escape_estate_id_is_not_persisted(tmp_path: Path) -> None:
    """AS-SEC-SCAN-OPS-REPORT-JSON-ESC-001: decoded estate_id must not persist."""
    token = "AKIAAAAAAAAAAAAAAAAA"
    vault = tmp_path / "vault"
    (vault / "generated" / "ops").mkdir(parents=True)
    snap = {
        "schema": "atlas.ops.health_snapshot.v1",
        "truth_plane": "operational",
        "authority_plane": "none",
        "note": "OPERATIONAL HEALTH ≠ PROJECT AUTHORITY",
        "estate_id": token,
        "project_filter": None,
        "rollup": {"estate": "healthy"},
        "scopes": [{"scope": "estate", "scope_id": token, "health": "unknown"}],
        "signals": [],
        "collector": "atlas.ops.health",
        "generated": {"by": "atlas-obs-001"},
    }
    raw = json.dumps(snap).replace('"' + token + '"', '"\\u0041KIAAAAAAAAAAAAAAAAA"')
    (vault / "generated" / "ops" / "health-snapshot.json").write_text(
        raw, encoding="utf-8"
    )
    assert scan_text(raw) == []
    rec = emit_ops_report(vault, persist=True, include_events=False)
    written = (vault / "generated" / "ops" / "ops-report.json").read_text(
        encoding="utf-8"
    )
    assert rec.get("estate_id") != token
    assert token not in written
    assert scan_text(written) == []
