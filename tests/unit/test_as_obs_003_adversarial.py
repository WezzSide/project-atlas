"""AS-OBS-003 adversarial / invariant defenses (OBS-003-ADV-*)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from project_atlas.ops_report import (
    OpsReportError,
    build_ops_report,
    emit_ops_report,
)
from project_atlas.schema import SchemaValidationError, validate_record

_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _ROOT / "src" / "project_atlas" / "ops_report.py"
_HEALTH = _ROOT / "src" / "project_atlas" / "ops_health.py"
_EVENTS = _ROOT / "src" / "project_atlas" / "ops_events.py"
_SCHEMA = _ROOT / "src" / "project_atlas" / "schemas" / "ops-report.schema.json"
_DOCS = _ROOT / "docs" / "AS-OBS-003-ops-report.md"


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".atlas").mkdir()
    (vault / ".atlas" / "vault.json").write_text(
        json.dumps({"vault_id": "v", "vault_uuid": "u"}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return vault


def test_obs003_adv001_ops_health_not_rewritten() -> None:
    """OBS-001 CLOSED — ops_report must consume snapshot schema only."""
    src = _MODULE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    assert "project_atlas.ops_health" not in imports
    assert "ops-health-snapshot" in src  # consume-only schema kind
    assert _HEALTH.is_file()
    # Must not mutate health-snapshot write helpers.
    assert "write_health_snapshot" not in src
    assert "emit_health_snapshot" not in src


def test_obs003_adv002_ops_events_writers_not_dual_owned() -> None:
    src = _MODULE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module != "project_atlas.ops_events"
    assert "append_event" not in src
    assert "record_health_transition" not in src
    assert _EVENTS.is_file()
    # May read stream path; must not claim events/ as write ownership.
    assert 'EVENTS_DIR = Path("generated") / "ops" / "events"' not in src


def test_obs003_adv003_compile_cache_not_dual_owned() -> None:
    src = _MODULE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "compile_cache" not in node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert "compile_cache" not in alias.name
    assert 'Path("generated") / "compile-cache"' not in src
    assert "compile-cache-receipt" not in src


def test_obs003_adv004_monitoring_stack_forbidden() -> None:
    src = _MODULE.read_text(encoding="utf-8").lower()
    for needle in ("prometheus", "grafana", "datadog", "pagerduty", "opentelemetry"):
        assert needle not in src
    docs = _DOCS.read_text(encoding="utf-8").lower()
    assert "monitoring" in docs


def test_obs003_adv005_trust_and_authority_fields_forbidden() -> None:
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    props = schema["properties"]
    for needle in (
        "trust",
        "trust_score",
        "confidence",
        "confidence_score",
        "authority_winner",
        "claim_id",
        "temporal_tip",
    ):
        assert needle not in props
    assert schema["properties"]["truth_plane"]["const"] == "operational"
    assert schema["properties"]["authority_plane"]["const"] == "none"
    assert "PROJECT AUTHORITY" in schema["properties"]["note"]["const"]


def test_obs003_adv006_rel001_must_not_open() -> None:
    src = _MODULE.read_text(encoding="utf-8")
    assert "release_certified" not in src.lower()
    assert "open_rel_001" not in src.lower()
    assert "def certify_release" not in src
    docs = _DOCS.read_text(encoding="utf-8")
    assert "AS-REL-001 MUST NOT OPEN" in docs


def test_obs003_adv007_path_writes_confined_to_ops_report(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    from project_atlas.ops_report import _assert_ops_report_path

    with pytest.raises(OpsReportError, match=r"refusing"):
        _assert_ops_report_path(vault, vault / "generated" / "ops" / "health-snapshot.json")
    with pytest.raises(OpsReportError, match=r"refusing"):
        _assert_ops_report_path(vault, vault / "generated" / "ops" / "events" / "stream.jsonl")
    with pytest.raises(OpsReportError, match=r"refusing"):
        _assert_ops_report_path(vault, vault / "generated" / "compile-cache" / "x.json")
    with pytest.raises(OpsReportError, match=r"escapes vault root"):
        _assert_ops_report_path(vault, tmp_path / "outside.json")
    ok = _assert_ops_report_path(vault, vault / "generated" / "ops" / "ops-report.json")
    assert "ops-report.json" in ok.as_posix().replace("\\", "/")


def test_obs003_adv008_no_wall_clock_in_schema() -> None:
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    gen = schema["properties"]["generated"]["properties"]
    assert "at" not in gen
    assert "timestamp" not in schema["properties"]
    assert "generated_at" not in schema["properties"]


def test_obs003_adv009_schema_rejects_authority_smuggling(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    report = build_ops_report(vault)
    report["claim_id"] = "smuggle"
    with pytest.raises(SchemaValidationError):
        validate_record(report, "ops-report")


def test_obs003_adv010_health_ne_truth_disclaimer_persisted(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    report = emit_ops_report(vault, include_events=False)
    assert "AUTHORITY" in report["note"]
    assert report["truth_plane"] == "operational"
    assert report["authority_plane"] == "none"
    md = (vault / "generated" / "ops" / "ops-report.md").read_text(encoding="utf-8")
    assert "HEALTH ≠ TRUTH" in md or "HEALTH != TRUTH" in md
    assert "AUTHORITY" in md


def test_obs003_adv011_docs_stop_condition_present() -> None:
    docs = _DOCS.read_text(encoding="utf-8")
    assert "IMPLEMENTATION COMPLETE — GOVERNOR REQUIRED" in docs
    assert "NO SELF-MERGE" in docs
    assert "HEALTH ≠ TRUTH" in docs or "HEALTH != TRUTH" in docs
    assert "SURF" in docs
    assert "event-enriched" in docs.lower() or "EVENT_ENRICHED" in docs


def test_obs003_adv012_surf_ui_not_owned() -> None:
    src = _MODULE.read_text(encoding="utf-8")
    for needle in ("obsidian", "react", "dashboard_ui", "surf_panel"):
        assert needle not in src.lower()
    docs = _DOCS.read_text(encoding="utf-8")
    assert "SURF" in docs
