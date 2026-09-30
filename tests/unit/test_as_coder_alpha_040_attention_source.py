"""D-040 attention hygiene + source-health explainability tests."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.attention_hygiene import classify_attention
from project_atlas.cli import EXIT_OK, main
from project_atlas.connect import connect_project
from project_atlas.project_decisions import _classify_decision_status
from project_atlas.source_health import explain_source_health


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, (dict, list)):
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        path.write_text(str(payload), encoding="utf-8")


def test_classify_decision_status_labels() -> None:
    """DECISIONS-003: ACTIVE_GOVERNING requires authority evidence."""
    assert (
        _classify_decision_status("ADR-001 Ship package data", kind="claim") == "ACTIVE_GOVERNING"
    )
    assert (
        _classify_decision_status(
            "Ship package data as package resources",
            kind="claim",
            verification="verified",
        )
        == "ACTIVE_GOVERNING"
    )
    # Bare claim titles without formal shape are proposed, not governing.
    assert _classify_decision_status("Ship package data", kind="claim") == "OPEN_PROPOSED"
    assert (
        _classify_decision_status("Context", kind="imported-heading", path="docs/adr/001.md")
        == "NON_DECISION"
    )
    assert (
        _classify_decision_status("Consequences", kind="adr-heading", path="docs/adr/ADR-001.md")
        == "NON_DECISION"
    )
    assert (
        _classify_decision_status("Migration and validation", kind="project-note") == "NON_DECISION"
    )
    assert (
        _classify_decision_status(
            "Accepted disposition for review-1",
            kind="human-disposition",
            authority="human-disposition",
        )
        == "ACTIVE_GOVERNING"
    )
    assert _classify_decision_status("Superseded: old vault layout", kind="claim") == "SUPERSEDED"
    assert _classify_decision_status("Rejected proposal for cloud sync", kind="claim") == "REJECTED"
    assert (
        _classify_decision_status("Proposed: optional MCP surface", kind="imported-heading")
        == "OPEN_PROPOSED"
    )
    assert (
        _classify_decision_status("Implementation note: wire CLI", kind="project-note")
        == "NON_DECISION"
    )
    assert (
        _classify_decision_status(
            "Historical decision on hashing",
            kind="imported-heading",
            path="docs/archive/old.md",
        )
        == "HISTORICAL"
    )
    assert (
        _classify_decision_status(
            "Keep three-layer vault",
            kind="claim",
            lifecycle="superseded",
        )
        == "SUPERSEDED"
    )
    assert (
        _classify_decision_status(
            "YES — RELEASE CERTIFIED",
            kind="imported-heading",
            path="docs/RECEIPT.md",
        )
        == "NON_DECISION"
    )


def test_attention_action_required_for_authority_pending(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write(
        vault / "review" / "pending" / "proj.json",
        {
            "entries": [
                {
                    "review_id": "r-auth",
                    "status": "pending",
                    "category": "competing-authority",
                    "reason": "competing authority claims",
                }
            ]
        },
    )
    report = classify_attention(vault, "proj")
    pending_levels = {item["level"] for item in report["items"] if item["kind"] == "pending_review"}
    assert pending_levels == {"ACTION_REQUIRED"}


def test_attention_action_required_for_promotion_failed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    # Real promotion failures land in quarantine after canonical rollback.
    _write(
        vault / "quarantine" / "promotion-failures" / "index.json",
        {
            "schema_version": 1,
            "receipt_type": "promotion-failure",
            "projects": [
                {
                    "project_id": "proj",
                    "candidates": [
                        {
                            "source_path": "docs/plan.md",
                            "outcome": "PROMOTION_FAILED",
                        }
                    ],
                }
            ],
        },
    )
    report = classify_attention(vault, "proj")
    promo_items = [item for item in report["items"] if item["kind"] == "promotion_failure"]
    assert promo_items
    assert promo_items[0]["level"] == "ACTION_REQUIRED"
    assert promo_items[0]["subject_id"] == "docs/plan.md"


def test_attention_unreadable_artifact_not_false_clear(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    path = vault / "review" / "conflicts" / "proj.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")
    report = classify_attention(vault, "proj")
    assert report["rollup"] != "CLEAR"
    assert any(item["reason_code"] == "ARTIFACT_UNREADABLE" for item in report["items"])


def test_attention_care_about_collapses_source_failures(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write(
        vault / "review" / "conflicts" / "proj.json",
        {
            "entries": [
                {
                    "conflict_id": "c1",
                    "field": "architecture",
                    "conflict_type": "materially-incompatible",
                }
            ]
        },
    )
    _write(
        vault / "state" / "compilation-outcomes" / "proj.json",
        {
            "candidates": [
                {"source_path": f"docs/f{index}.md", "outcome": "FAILED"} for index in range(12)
            ]
        },
    )
    report = classify_attention(vault, "proj")
    assert report["source_failure_total"] == 12
    assert report["honesty"]["failures_hidden"] is False
    care_levels = {item["level"] for item in report["care_about"]}
    assert "BLOCKING" in care_levels
    assert len(report["care_about"]) <= 10
    # Collapsed rollup present; not 12 individual SOURCE_FAILURE rows dominating care_about.
    assert any(item.get("kind") == "compile_failure_rollup" for item in report["items"])
    care_source_failures = [
        item for item in report["care_about"] if item.get("level") == "SOURCE_FAILURE"
    ]
    assert len(care_source_failures) <= 2


def test_attention_rollup_preserves_action_required(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    entries = [
        {
            "review_id": f"r-{index}",
            "status": "pending",
            "category": "pending-claim",
            "reason": "noise",
        }
        for index in range(22)
    ]
    entries.append(
        {
            "review_id": "r-auth-late",
            "status": "pending",
            "category": "competing-authority",
            "reason": "competing authority claims",
        }
    )
    _write(vault / "review" / "pending" / "proj.json", {"entries": entries})
    report = classify_attention(vault, "proj")
    levels = {item["level"] for item in report["items"]}
    assert "LOW_VALUE_NOISE" in levels
    assert "ACTION_REQUIRED" in levels
    assert any(
        item["subject_id"] == "r-auth-late" and item["level"] == "ACTION_REQUIRED"
        for item in report["items"]
    )


def test_attention_classifies_conflict_and_pending(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write(
        vault / "review" / "conflicts" / "proj.json",
        {
            "entries": [
                {
                    "conflict_id": "c1",
                    "field": "architecture",
                    "conflict_type": "materially-incompatible",
                }
            ]
        },
    )
    _write(
        vault / "review" / "pending" / "proj.json",
        {
            "entries": [
                {
                    "review_id": "r1",
                    "status": "pending",
                    "category": "pending-claim",
                    "reason": "claim requires human verification",
                }
            ]
        },
    )
    report = classify_attention(vault, "proj")
    assert report["rollup"] == "BLOCKING"
    levels = {item["level"] for item in report["items"]}
    assert "BLOCKING" in levels
    assert "NEEDS_HUMAN_REVIEW" in levels
    assert report["honesty"]["confidence_theatre"] is False
    assert all("why_seeing_this" in item for item in report["items"])


def test_source_health_explains_exclusions(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    sources = {
        "sources": [
            {
                "path": "fixtures/demo/README.md",
                "source_id": "source-x",
                "likely_project": "proj",
                "exclusion_reason": "default-excluded-directory",
            },
            {
                "path": "README.md",
                "source_id": "source-y",
                "likely_project": "proj",
                "exclusion_reason": None,
            },
        ]
    }
    _write(vault / "generated" / "ops" / "connect-manifest.json", sources)
    _write(vault / "sources" / "manifests" / "source-manifest.json", sources)
    report = explain_source_health(vault, "proj")
    assert report["source_count"] == 1
    row = report["sources"][0]
    assert row["reason_code"] == "default-excluded-directory"
    assert row["pipeline_stage"] == "discover"
    assert "excluded directory" in row["human_explanation"].lower()
    assert report["honesty"]["secrets_echoed"] is False


def test_source_health_scopes_quarantine_and_reads_source_path(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    ownership = {
        "sources": [
            {
                "path": "docs/a.md",
                "source_id": "src-a",
                "likely_project": "proj-a",
            },
            {
                "path": "docs/b.md",
                "source_id": "src-b",
                "likely_project": "proj-b",
            },
        ]
    }
    _write(vault / "generated" / "ops" / "connect-manifest.json", ownership)
    _write(vault / "sources" / "manifests" / "source-manifest.json", ownership)
    _write(
        vault / "generated" / "reports" / "secret-findings.json",
        {
            "findings": [
                {"path": "docs/a.md", "source_id": "src-a"},
                {"path": "docs/b.md", "source_id": "src-b"},
            ]
        },
    )
    _write(
        vault / "state" / "compilation-outcomes" / "proj-a.json",
        {
            "candidates": [
                {
                    "source_path": "docs/a.md",
                    "outcome": "PARTIAL_CANDIDATE",
                }
            ]
        },
    )
    _write(
        vault / "quarantine" / "promotion-failures" / "index.json",
        {
            "projects": [
                {
                    "project_id": "proj-a",
                    "candidates": [
                        {
                            "source_path": "docs/a.md",
                            "outcome": "PROMOTION_FAILED",
                        }
                    ],
                }
            ]
        },
    )
    report = explain_source_health(vault, "proj-a")
    sources = {row["source"] for row in report["sources"]}
    assert "docs/b.md" not in sources
    assert "docs/a.md" in sources
    codes = {row["reason_code"] for row in report["sources"]}
    assert "SECRET_QUARANTINE" in codes
    assert "PARTIAL_CANDIDATE" in codes
    assert "PROMOTION_FAILED" in codes
    partial = next(row for row in report["sources"] if row["reason_code"] == "PARTIAL_CANDIDATE")
    assert "partial candidate" in partial["human_explanation"].lower()
    assert partial["source"] == "docs/a.md"


def test_cli_attention_and_source_health(tmp_path: Path) -> None:
    project = tmp_path / "cli-att"
    project.mkdir()
    (project / "README.md").write_text("# CLI Att\n\nbody\n", encoding="utf-8")
    connected = connect_project(project)
    vault = Path(connected["vault"])
    project_id = str(connected["bound_project_id"])
    assert main(["attention", "--vault", str(vault), "--project", project_id, "--json"]) == EXIT_OK
    assert (
        main(
            [
                "source-health",
                "--vault",
                str(vault),
                "--project",
                project_id,
                "--json",
            ]
        )
        == EXIT_OK
    )
