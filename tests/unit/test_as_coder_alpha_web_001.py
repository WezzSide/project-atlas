"""AS-CODER-ALPHA-WEB-001 / TRUTH-UX-001 — Web brief + truth panel tests."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.app_service import open_app_service
from project_atlas.web_api import filter_knowledge_by_project, read_project_brief


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, (dict, list)):
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    else:
        path.write_text(str(payload), encoding="utf-8")


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write(
        vault / ".atlas" / "vault.json",
        {"vault_id": "atlas-web", "vault_uuid": "web-fixture-uuid"},
    )
    (vault / "projects" / "project-atlas").mkdir(parents=True)
    _write(
        vault / "generated" / "ops" / "project-brief-project-atlas.json",
        {
            "schema_version": 1,
            "schema": "atlas.coder-alpha.project-brief.v1",
            "package": "AS-CODER-ALPHA-BRIEF-001",
            "project_id": "project-atlas",
            "purpose": "Local-first project knowledge compiler",
            "tech_stack": "Python 3.12",
            "architecture_summary": "discover → ingest → indexes → validate",
            "current_state": "lifecycle=active; pending_reviews=1",
            "recent_meaningful_changes": "WEB-001 brief API",
            "important_decisions": "UI != canonical",
            "unknown_or_conflicting": "pending_reviews=1",
            "suggested_next_work": ["Triage pending reviews"],
            "evidence_links": [
                "projects/project-atlas/project.md",
                "review/pending/project-atlas.json",
                "state/claims/project-atlas.json",
            ],
            "honesty": {
                "authentic_pilot": False,
                "atlas_opt_wake_gate": "CLOSED",
                "lens_is_authority": False,
                "unknown_is_valid": True,
            },
            "generated": {"by": "test"},
        },
    )
    _write(
        vault / "generated" / "answers" / "ans-overview-project-atlas.json",
        {
            "answer_id": "ans-overview-project-atlas",
            "subject": "project-atlas",
            "field": "overview",
            "title": "Overview",
            "summary": "Atlas overview lens",
            "value": "Local-first project knowledge compiler",
        },
    )
    _write(
        vault / "generated" / "answers" / "ans-overview-nebula.json",
        {
            "answer_id": "ans-overview-nebula",
            "subject": "nebula",
            "field": "overview",
            "title": "Overview",
            "summary": "other project",
            "value": "fixture",
        },
    )
    _write(
        vault / "review" / "pending" / "project-atlas.json",
        {
            "schema_version": 1,
            "project_id": "project-atlas",
            "entries": [
                {
                    "review_id": "review-abc",
                    "status": "pending",
                    "reason": "claim requires human verification",
                }
            ],
        },
    )
    _write(
        vault / "review" / "conflicts" / "project-atlas.json",
        {"entries": []},
    )
    _write(
        vault / "generated" / "ops" / "session-captures" / "capture-1.json",
        {
            "capture_id": "capture-1",
            "project_id": "project-atlas",
            "kind": "milestone",
            "summary": "dogfood capture",
            "source": "manual",
        },
    )
    _write(
        vault / "state" / "human-decisions" / "project-atlas.json",
        {
            "schema_version": 1,
            "project_id": "project-atlas",
            "decisions": [
                {
                    "review_id": "review-old",
                    "decision": "accept",
                    "reason": "owner verified",
                }
            ],
        },
    )
    return vault


def test_read_project_brief_surfaces_core_sections(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    brief = read_project_brief(vault, "project-atlas")
    assert brief["available"] is True
    assert brief["purpose"] == "Local-first project knowledge compiler"
    assert brief["lens_sections"]["overview"]["answer_id"] == "ans-overview-project-atlas"
    assert brief["session_captures"][0]["capture_id"] == "capture-1"
    assert brief["honesty"]["ui_is_canonical"] is False
    assert brief["honesty"]["atlas_opt_wake_gate"] == "CLOSED"
    assert "WEB BRIEF READ != AUTHORITY" in brief["truth_boundary"]


def test_truth_panel_has_labels_not_scores(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    brief = read_project_brief(vault, "project-atlas")
    truth = brief["truth"]
    assert truth["package_id"] == "AS-CODER-ALPHA-TRUTH-UX-001"
    assert truth["confidence_theatre"] is False
    assert truth["pending_review_count"] == 1
    assert truth["pending_reviews"][0]["review_id"] == "review-abc"
    assert truth["human_decision_count"] == 1
    assert truth["human_decisions"][0]["decision"] == "accept"
    assert truth["unknown"]["healthy"] is False
    assert any(row["path"] == "projects/project-atlas/project.md" for row in truth["evidence"])
    assert "verified" in truth["labels"]
    assert truth["confidence_theatre"] is False
    assert "confidence_score" not in json.dumps(truth)


def test_missing_brief_is_unknown_not_fabricated(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    (vault / "generated" / "ops" / "project-brief-project-atlas.json").unlink()
    brief = read_project_brief(vault, "project-atlas")
    assert brief["available"] is False
    assert brief["purpose"] == "UNKNOWN"
    assert brief["honesty"]["unknown_is_valid"] is True


def test_filter_knowledge_by_project(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    service = open_app_service(vault)
    all_rows = service.knowledge()
    filtered = service.knowledge("project-atlas")
    assert len(all_rows) == 2
    assert len(filtered) == 1
    assert filtered[0]["subject"] == "project-atlas"
    assert filter_knowledge_by_project(all_rows, "nebula")[0]["subject"] == "nebula"


def test_brief_and_knowledge_ignore_subject_spoofed_filename(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write(
        vault / "generated" / "answers" / "ans-state-victim.json",
        {
            "answer_id": "ans-state-victim",
            "subject": "project-atlas",
            "field": "project_state",
            "summary": "VICTIM-ONLY-STATE-HELIX-991",
            "value": "postgresql-16 detail",
        },
    )
    brief = read_project_brief(vault, "project-atlas")
    state = brief["lens_sections"].get("state")
    assert state is None or state.get("summary") != "VICTIM-ONLY-STATE-HELIX-991"
    rows = open_app_service(vault).knowledge("project-atlas")
    assert all(row.get("path") != "generated/answers/ans-state-victim.json" for row in rows)


def test_brief_and_knowledge_redact_secret_shaped_values(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    secret = "aws_secret_access_key=AKIAAAAAAAAAAAAAAAAA"
    _write(
        vault / "generated" / "answers" / "ans-overview-project-atlas.json",
        {
            "answer_id": "ans-overview-project-atlas",
            "subject": "project-atlas",
            "field": "overview",
            "title": "Overview",
            "summary": secret,
            "value": secret,
        },
    )
    brief = read_project_brief(vault, "project-atlas")
    overview = brief["lens_sections"]["overview"]
    assert secret not in json.dumps(overview)
    rows = open_app_service(vault).knowledge("project-atlas")
    assert all(secret not in json.dumps(row) for row in rows)


def test_app_service_brief_read_only(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    probe = vault / "state" / "claims" / "probe.json"
    _write(probe, {"ok": True})
    before = probe.read_text(encoding="utf-8")
    mtime = probe.stat().st_mtime_ns
    brief = open_app_service(vault).brief("project-atlas")
    assert brief["project_id"] == "project-atlas"
    assert probe.read_text(encoding="utf-8") == before
    assert probe.stat().st_mtime_ns == mtime
