"""AS-CODER-ALPHA-DEMO-READINESS-001 — fixture journey, not AUTHENTIC_PILOT."""

from __future__ import annotations

from pathlib import Path

from project_atlas.demo_readiness import HONESTY, JOURNEY, STAMPS, run_demo_readiness

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "demo" / "estate" / "harbor-api"


def test_demo_readiness_harbor_journey_is_honest(tmp_path: Path) -> None:
    report = run_demo_readiness(FIXTURE, work_root=tmp_path / "demo")
    assert report["honesty"] == HONESTY
    assert report["honesty"]["commercial_ga"] is False
    assert report["honesty"]["authentic_pilot"] is False
    assert report["honesty"]["demo_readiness_is_derived"] is True
    assert report["honesty"]["demo_readiness_is_authority"] is False
    assert report["honesty"]["missing_is_pass"] is False
    assert report["demo_readiness"] in {"PASS", "PARTIAL", "BLOCKED"}
    assert report["demo_readiness"] != "PASS"
    assert report["pilot_readiness"] == "NOT_IMPLEMENTED"
    assert report["release_readiness"] == "NOT_IMPLEMENTED"
    assert report["checks"]["persistent_identity"] is True
    assert report["checks"]["second_session_continuity"] is True
    assert report["checks"]["cross_project_leak_count"] == 0
    assert report["checks"]["handoff_present"] is True
    assert report["checks"]["context_present"] is True
    assert report["checks"]["obsidian_present"] is True
    assert report["checks"]["drift_consumed"] is True
    assert report["checks"]["next_api_landed"] is False
    assert report["next_api"] == "PENDING_OWNER_HELD_406"
    assert report["inbox_list"] == "READY"
    by_name = {row["name"]: row for row in report["stages"]}
    assert tuple(row["name"] for row in report["stages"] if row["name"] in JOURNEY) == JOURNEY
    assert by_name["project_root"]["state"] == "READY"
    assert by_name["connect_discover"]["state"] == "READY"
    assert by_name["project_identity"]["state"] == "READY"
    assert by_name["source_inventory"]["state"] == "READY"
    assert by_name["drift_state"]["state"] in {"READY", "UNKNOWN"}
    assert by_name["overview"]["state"] == "READY"
    assert by_name["architecture"]["state"] in {"READY", "PARTIAL"}
    assert by_name["next"]["state"] == "PARTIAL"
    assert by_name["inbox"]["state"] == "READY"
    assert by_name["api_cli_web"]["state"] == "PARTIAL"
    assert "MISSING" not in {row["state"] for row in report["stages"]}
    for stamp in STAMPS:
        assert stamp in report["stamps"]
    assert "RELEASE" not in report["demo_readiness"]
    assert report.get("commercial_ga") is None


def test_demo_readiness_never_claims_ga_or_missing_pass(tmp_path: Path) -> None:
    report = run_demo_readiness(FIXTURE, work_root=tmp_path / "demo2")
    dumped = str(report)
    assert report["honesty"]["commercial_ga"] is False
    assert report["honesty"]["demo_is_release"] is False
    assert "MISSING == PASS" not in dumped
    assert report["demo_readiness"] != "PASS"
    assert report["inbox_list"] == "READY"
    assert report["checks"]["inbox_list_implemented"] is True
