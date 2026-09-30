"""AS-OBS-001 Operational Health Snapshot certification tests.

Contract: gen4-parallel-wave-007/AS-OBS-001-CONTRACT.md
Directive: D-PROJECT-ATLAS-FORWARD-PIPELINE-ACTIVATION-001

Invariants exercised:
- Unknown ≠ healthy (missing evidence → unknown / non-green rollup)
- Required signals present; deterministic rollup §5.2
- truth_plane / authority_plane disclaimer
- Consume-only: no mtime drift on authority / claims / temporal paths
- Replay → byte-identical snapshot JSON
- OPS-SIG-009 nonanswer ≠ outage; corruption = fail
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.cli import main
from project_atlas.ops_health import (
    REQUIRED_SIGNAL_IDS,
    build_health_snapshot,
    emit_health_snapshot,
    rollup_health,
    snapshot_to_json,
)
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
    # Truth-plane paths that must not be mutated by ops health.
    (vault / "state" / "authoritative-state").mkdir(parents=True)
    (vault / "state" / "current-state").mkdir(parents=True)
    (vault / "state" / "claims").mkdir(parents=True)
    _write(vault / "state" / "authoritative-state" / "probe.json", {"ok": True})
    _write(vault / "state" / "current-state" / "probe.json", {"ok": True})
    _write(vault / "state" / "claims" / "probe.json", {"ok": True})
    return vault


def _signal_map(snapshot: dict) -> dict[str, dict]:
    return {item["signal_id"]: item for item in snapshot["signals"]}


def test_empty_vault_required_signals_unknown_not_healthy(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    snapshot = build_health_snapshot(vault)
    validate_record(snapshot, "ops-health-snapshot")
    assert snapshot["truth_plane"] == "operational"
    assert snapshot["authority_plane"] == "none"
    assert snapshot["note"] == "OPERATIONAL HEALTH ≠ PROJECT AUTHORITY"
    assert snapshot["rollup"]["estate"] == "unknown"
    assert snapshot["rollup"]["estate"] != "healthy"
    signals = _signal_map(snapshot)
    for signal_id in REQUIRED_SIGNAL_IDS:
        assert signal_id in signals
    # Absent sync/backup/readiness/promotion/quarantine evidence must not fabricate ok.
    assert signals["OPS-SIG-002"]["status"] == "unknown"
    assert signals["OPS-SIG-005"]["status"] == "unknown"
    assert signals["OPS-SIG-005"]["evidence_refs"] == []
    assert signals["OPS-SIG-006"]["status"] == "unknown"
    assert signals["OPS-SIG-006"]["evidence_refs"] == []
    assert signals["OPS-SIG-013"]["status"] == "unknown"
    assert signals["OPS-SIG-014"]["status"] == "unknown"
    assert signals["OPS-SIG-010"]["status"] == "unknown"


def test_absent_promotion_and_quarantine_evidence_not_ok(tmp_path: Path) -> None:
    """OBS-001-FR-002 / governor FR-002: unavailable ≠ fabricated ok."""
    vault = _vault(tmp_path)
    snapshot = build_health_snapshot(vault)
    signals = _signal_map(snapshot)
    assert signals["OPS-SIG-005"]["status"] != "ok"
    assert signals["OPS-SIG-005"]["status"] == "unknown"
    assert signals["OPS-SIG-005"]["observed_value"] is None
    assert signals["OPS-SIG-006"]["status"] != "ok"
    assert signals["OPS-SIG-006"]["status"] == "unknown"
    assert signals["OPS-SIG-006"]["observed_value"] is None
    assert snapshot["rollup"]["estate"] != "healthy"


def test_present_empty_promotion_and_quarantine_indexes_are_ok(tmp_path: Path) -> None:
    """Observed empty evidence surfaces may report ok/0 with non-empty refs."""
    vault = _vault(tmp_path)
    _write(vault / "quarantine" / "promotion-failures" / "index.json", [])
    _write(vault / "generated" / "reports" / "secret-findings.json", [])
    snapshot = build_health_snapshot(vault)
    signals = _signal_map(snapshot)
    assert signals["OPS-SIG-005"]["status"] == "ok"
    assert signals["OPS-SIG-005"]["observed_value"] == 0
    assert signals["OPS-SIG-005"]["evidence_refs"]
    assert signals["OPS-SIG-006"]["status"] == "ok"
    assert signals["OPS-SIG-006"]["observed_value"] == 0
    assert signals["OPS-SIG-006"]["evidence_refs"]


def test_certification_fixture_failed_sync_quarantine_stale_adapter(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write(
        vault / "generated" / "ops" / "evidence" / "sync-failures.json",
        {"failures": [{"project_id": "proj-1", "error_code": "SYNC_FAILED"}]},
    )
    _write(
        vault / "generated" / "reports" / "secret-findings.json",
        [{"source_id": "s1", "rule": "aws-key"}],
    )
    _write(
        vault / "generated" / "reports" / "injection-findings.json",
        {"schema_version": 1, "findings": [{"source_id": "s2", "rule": "prompt-inject"}]},
    )
    _write(
        vault / ".atlas" / "agent-readiness.yaml",
        (
            "schema_version: 1\n"
            "adapters:\n"
            "  stale-adapter:\n"
            "    rehearsal_status: pending\n"
            "    skill_sha256: abc\n"
            "    observed_skill_sha256: abc\n"
            "    governed_work_ready: false\n"
        ),
    )
    snapshot = emit_health_snapshot(vault)
    signals = _signal_map(snapshot)
    assert signals["OPS-SIG-002"]["status"] == "fail"
    assert signals["OPS-SIG-006"]["status"] in {"warn", "fail"}
    assert signals["OPS-SIG-010"]["status"] == "fail"
    assert snapshot["rollup"]["estate"] in {"degraded", "unhealthy", "unknown"}
    assert snapshot["rollup"]["estate"] != "healthy"
    assert (vault / "generated" / "ops" / "health-snapshot.json").is_file()


def test_missing_backup_is_unknown_not_green(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    snapshot = build_health_snapshot(vault)
    assert _signal_map(snapshot)["OPS-SIG-013"]["status"] == "unknown"
    assert snapshot["rollup"]["estate"] != "healthy"


def test_query_nonanswer_ok_corruption_fail(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    evidence = vault / "generated" / "ops" / "evidence" / "query-diagnostics.json"
    _write(
        evidence,
        {"query_nonanswer_count": 3, "query_corruption_count": 0},
    )
    ok_snap = build_health_snapshot(vault)
    assert _signal_map(ok_snap)["OPS-SIG-009"]["status"] == "ok"

    _write(
        evidence,
        {"query_nonanswer_count": 1, "query_corruption_count": 2},
    )
    bad_snap = build_health_snapshot(vault)
    assert _signal_map(bad_snap)["OPS-SIG-009"]["status"] == "fail"
    assert _signal_map(bad_snap)["OPS-SIG-009"]["severity"] == "CRITICAL"
    assert bad_snap["rollup"]["estate"] == "unhealthy"


def test_consume_only_no_truth_plane_mtime_drift(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    probes = [
        vault / "state" / "authoritative-state" / "probe.json",
        vault / "state" / "current-state" / "probe.json",
        vault / "state" / "claims" / "probe.json",
    ]
    before = {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in probes}
    emit_health_snapshot(vault)
    for path, (mtime, content) in before.items():
        assert path.stat().st_mtime_ns == mtime
        assert path.read_bytes() == content


def test_replay_byte_identical(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write(
        vault / "generated" / "ops" / "evidence" / "sync-failures.json",
        {"failures": [{"project_id": "p", "error_code": "X"}]},
    )
    first = snapshot_to_json(build_health_snapshot(vault))
    second = snapshot_to_json(build_health_snapshot(vault))
    assert first == second
    assert "generated.at" not in first


def test_rollup_rules_critical_high_medium_unknown() -> None:
    assert (
        rollup_health(
            [{"status": "fail", "severity": "CRITICAL"}, {"status": "ok", "severity": None}]
        )
        == "unhealthy"
    )
    assert (
        rollup_health(
            [{"status": "fail", "severity": "HIGH"}, {"status": "unknown", "severity": None}]
        )
        == "unhealthy"
    )
    assert (
        rollup_health(
            [{"status": "warn", "severity": "MEDIUM"}, {"status": "ok", "severity": None}]
        )
        == "degraded"
    )
    assert (
        rollup_health([{"status": "unknown", "severity": None}, {"status": "ok", "severity": None}])
        == "unknown"
    )
    assert rollup_health([{"status": "ok", "severity": None}]) == "healthy"


def test_library_emit_persist_and_no_write(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    snapshot = emit_health_snapshot(vault, persist=True)
    assert snapshot["schema"] == "atlas.ops.health_snapshot.v1"
    assert snapshot["authority_plane"] == "none"
    assert (vault / "generated" / "ops" / "health-snapshot.json").is_file()
    again = emit_health_snapshot(vault, persist=False)
    assert again["schema"] == snapshot["schema"]


def test_cli_ops_health_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """AS-OBS-001 CLI wiring (SERIALIZE HOLD released after QUERY-DIAG COMPLETE)."""
    vault = _vault(tmp_path)
    code = main(["ops", "health", "--vault", str(vault), "--json", "--no-write"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "atlas.ops.health_snapshot.v1"
    assert payload["authority_plane"] == "none"
    assert payload["truth_plane"] == "operational"
    assert not (vault / "generated" / "ops" / "health-snapshot.json").exists()


def test_skill_drift_fails_ops_sig_011(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write(
        vault / ".atlas" / "agent-readiness.yaml",
        (
            "schema_version: 1\n"
            "adapters:\n"
            "  generic-cli-v1:\n"
            "    rehearsal_status: passed\n"
            "    skill_sha256: expectedhash\n"
            "    observed_skill_sha256: differenthash\n"
            "    governed_work_ready: true\n"
        ),
    )
    snapshot = build_health_snapshot(vault)
    signals = _signal_map(snapshot)
    assert signals["OPS-SIG-011"]["status"] == "fail"
    assert signals["OPS-SIG-011"]["severity"] == "CRITICAL"
