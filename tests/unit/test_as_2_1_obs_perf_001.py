"""AS-2.1-OBS-PERF-001 — observability + perf baseline deepen tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.obs_live import build_live_observability_receipt
from project_atlas.obs_perf import build_obs_perf_receipt
from project_atlas.perf_baselines import PerfBaselineError, run_perf_baselines


def test_perf_baselines_cover_api_mcp_query_sync(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    report = run_perf_baselines(vault, baseline_id="lanes", iterations=1)
    assert report["package_id"] == "AS-2.1-PERF-BASELINE-001"
    assert report["deepen_package_id"] == "AS-2.1-OBS-PERF-001"
    assert report["release_blocking"] is False
    assert report["authentic_pilot_substitute"] is False
    keys = set(report["measurements"])
    for required in (
        "api_health_read_ms",
        "api_projects_read_ms",
        "mcp_list_tools_ms",
        "mcp_invoke_health_ms",
        "ask_atlas_query_ms",
        "query_plan_build_ms",
        "sync_plan_dry_run_ms",
        "app_service_snapshot_ms",
    ):
        assert required in keys
        assert report["measurements"][required]["max_ms"] >= 0
    for lane in ("api", "mcp", "query", "sync"):
        assert lane in report["lanes_covered"]
    out = vault / "generated" / "ops" / "perf" / "lanes-baseline.json"
    assert out.is_file()


def test_obs_live_lanes_unknown_rollup(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    receipt = build_live_observability_receipt(vault, receipt_id="lane-obs")
    assert receipt["rollup"] == "unknown"
    assert receipt["deepen_package_id"] == "AS-2.1-OBS-PERF-001"
    lanes = receipt["lanes"]
    assert set(lanes) >= {"api", "mcp", "query", "sync", "perf"}
    assert lanes["sync"]["sync_plan_scaffold"] is True
    assert lanes["query"]["ask_atlas_module"] is True
    assert lanes["perf"]["baseline_receipt_count"] == 0
    # After a baseline, count increments without claiming healthy.
    run_perf_baselines(vault, baseline_id="c1", iterations=1)
    receipt2 = build_live_observability_receipt(vault, receipt_id="lane-obs-2")
    assert receipt2["lanes"]["perf"]["baseline_receipt_count"] == 1
    assert receipt2["rollup"] == "unknown"


def test_obs_perf_combined_receipt(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    payload = build_obs_perf_receipt(vault, receipt_id="combo", baseline_id="combo-b", iterations=1)
    assert payload["package_id"] == "AS-2.1-OBS-PERF-001"
    assert payload["shared_schema_mutated"] is False
    assert payload["rollup"] == "unknown"
    assert "api" in payload["lanes_present"]
    assert "ask_atlas_query_ms" in payload["measurement_keys"]
    assert (vault / "generated" / "ops" / "obs-perf" / "combo.json").is_file()


def test_perf_iterations_fail_closed(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(PerfBaselineError, match="perf-iterations-out-of-range"):
        run_perf_baselines(vault, iterations=0)


def test_obs_perf_docs_present() -> None:
    root = Path(__file__).resolve().parents[2]
    text = (root / "docs" / "atlas-2.1" / "OBS-PERF.md").read_text(encoding="utf-8")
    assert "AS-2.1-OBS-PERF-001" in text
    assert "shared JSON schemas" in text
    assert "Unknown ≠ healthy" in text or "UNKNOWN!=HEALTHY" in text
