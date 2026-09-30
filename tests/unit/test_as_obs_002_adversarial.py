"""AS-OBS-002 adversarial / invariant defenses (OBS-002-ADV-*)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from project_atlas.ops_events import (
    OpsEventError,
    append_event,
    build_event,
)
from project_atlas.schema import SchemaValidationError, validate_record

_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _ROOT / "src" / "project_atlas" / "ops_events.py"
_HEALTH = _ROOT / "src" / "project_atlas" / "ops_health.py"
_EVENT_SCHEMA = _ROOT / "src" / "project_atlas" / "schemas" / "ops-event.schema.json"
_STREAM_SCHEMA = _ROOT / "src" / "project_atlas" / "schemas" / "ops-event-stream.schema.json"
_DOCS = _ROOT / "docs" / "AS-OBS-002-ops-events.md"


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".atlas").mkdir()
    (vault / ".atlas" / "vault.json").write_text(
        json.dumps({"vault_id": "v", "vault_uuid": "u"}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return vault


def test_obs002_adv001_ops_health_not_rewritten() -> None:
    """OBS-001 CLOSED — ops_events must consume snapshot schema only."""
    src = _MODULE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    assert "project_atlas.ops_health" not in imports
    assert "ops-health-snapshot" in src  # consume-only schema kind
    # Product module file must remain present and unowned here.
    assert _HEALTH.is_file()


def test_obs002_adv002_compile_cache_not_dual_owned() -> None:
    src = _MODULE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "compile_cache" not in node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert "compile_cache" not in alias.name
    # No runtime emit under INCR surface (docstring may name the exclusion).
    assert 'Path("generated") / "compile-cache"' not in src
    assert 'EVENTS_DIR = Path("generated") / "compile-cache"' not in src
    assert "compile-cache-receipt" not in src


def test_obs002_adv003_obs003_ops_report_not_owned() -> None:
    src = _MODULE.read_text(encoding="utf-8")
    assert "ops_report" not in src
    assert "AS-OBS-003" in src  # documented non-ownership only
    docs = _DOCS.read_text(encoding="utf-8")
    assert "AS-OBS-003" in docs
    # Must not emit ops-report product paths.
    assert "ops-report.json" not in src
    assert "ops-report.md" not in src


def test_obs002_adv004_monitoring_stack_forbidden() -> None:
    src = _MODULE.read_text(encoding="utf-8").lower()
    for needle in ("prometheus", "grafana", "datadog", "pagerduty", "opentelemetry"):
        assert needle not in src
    docs = _DOCS.read_text(encoding="utf-8").lower()
    assert "monitoring stack" in docs or "prometheus" in docs


def test_obs002_adv005_trust_and_authority_fields_forbidden() -> None:
    schema = json.loads(_EVENT_SCHEMA.read_text(encoding="utf-8"))
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


def test_obs002_adv006_rel001_must_not_open() -> None:
    src = _MODULE.read_text(encoding="utf-8")
    # Docstring may forbid REL-001 by name; product must not implement release APIs.
    assert "release_certified" not in src.lower()
    assert "open_rel_001" not in src.lower()
    assert "def certify_release" not in src
    docs = _DOCS.read_text(encoding="utf-8")
    assert "AS-REL-001 MUST NOT OPEN" in docs


def test_obs002_adv007_path_escape_and_non_events_writes_rejected(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    from project_atlas.ops_events import _assert_ops_events_path

    with pytest.raises(OpsEventError, match=r"refusing non-ops-events"):
        _assert_ops_events_path(vault, vault / "generated" / "ops" / "health-snapshot.json")
    with pytest.raises(OpsEventError, match=r"refusing non-ops-events"):
        _assert_ops_events_path(vault, vault / "generated" / "compile-cache" / "x.json")
    with pytest.raises(OpsEventError, match=r"escapes vault root"):
        _assert_ops_events_path(vault, tmp_path / "outside.json")
    # Legitimate path under owned tree is accepted.
    ok = _assert_ops_events_path(vault, vault / "generated" / "ops" / "events" / "stream.jsonl")
    assert "generated/ops/events" in ok.as_posix().replace("\\", "/")


def test_obs002_adv008_no_wall_clock_in_event_or_stream_schema() -> None:
    for path in (_EVENT_SCHEMA, _STREAM_SCHEMA):
        schema = json.loads(path.read_text(encoding="utf-8"))
        gen = schema["properties"]["generated"]["properties"]
        assert "at" not in gen
        assert "timestamp" not in schema["properties"]
        assert "generated_at" not in schema["properties"]


def test_obs002_adv009_schema_rejects_authority_smuggling() -> None:
    event = build_event(
        event_id="OPS-EVT-CI-FAILED",
        sequence=1,
        payload={"workflow": "ci.yml", "commit": "abc"},
        evidence_refs=["generated/ops/evidence/ci-status.json"],
    )
    event["claim_id"] = "smuggle"
    with pytest.raises(SchemaValidationError):
        validate_record(event, "ops-event")


def test_obs002_adv010_health_ne_truth_disclaimer_persisted(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    event = append_event(
        vault,
        event_id="OPS-EVT-QUERY-CORRUPTION",
        payload={"project_id": "p", "error_code": "CORRUPT"},
        evidence_refs=["generated/ops/evidence/query-diagnostics.json"],
        apply_caps=False,
    )
    assert "AUTHORITY" in event["note"]
    assert event["truth_plane"] == "operational"
    assert event["authority_plane"] == "none"


def test_obs002_adv011_docs_stop_condition_present() -> None:
    docs = _DOCS.read_text(encoding="utf-8")
    assert "IMPLEMENTATION COMPLETE — GOVERNOR REQUIRED" in docs
    assert "NO SELF-MERGE" in docs
    assert "HEALTH ≠ TRUTH" in docs or "HEALTH != TRUTH" in docs
