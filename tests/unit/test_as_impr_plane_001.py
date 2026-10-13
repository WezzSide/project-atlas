"""AS-IMPR-PLANE-001 — read-only delivery-evidence improvement report."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.improvement_plane import (
    PACKAGE_ID,
    TRUTH_BOUNDARY,
    compile_improvement_report,
    render_markdown_summary,
)
from project_atlas.improvement_plane.__main__ import main as impr_main


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@pytest.fixture()
def evidence_repo(tmp_path: Path) -> Path:
    evidence = tmp_path / "docs" / "evidence"
    _write_json(
        evidence / "frontier.json",
        {
            "directive": "D-TEST-FRONTIER",
            "owner_gated_nodes": [
                {
                    "node_id": "OWNER-1",
                    "description": "Merge decision for PR #1",
                    "why_owner_only": "MERGE_AUTHORIZATION = NOT_GRANTED",
                    "autonomous_preparation_complete": True,
                    "next_required_owner_action": "Review and merge PR #1",
                    "dependents": ["OWNER-2"],
                },
                {
                    "node_id": "OWNER-2",
                    "description": "Retarget PR #2",
                    "why_owner_only": "Stacked behind OWNER-1",
                    "autonomous_preparation_complete": True,
                    "next_required_owner_action": "Retarget after #1 merges",
                    "dependents": [],
                },
            ],
            "disposition_counts": {"BLOCKED_BY_OWNER": 2},
            "as_of_utc": "2026-08-26T12:00:00Z",
        },
    )
    _write_json(
        evidence / "failures-a.json",
        {
            "package_id": "AS-TEST-FAIL",
            "findings": [
                {
                    "finding_id": "CI-FLAKE-001",
                    "status": "OPEN",
                    "failure_class": "INFRA_TRANSIENT",
                    "attack": "windows-self-interrupt",
                },
                {
                    "finding_id": "CI-FLAKE-001",
                    "status": "OPEN",
                    "failure_class": "INFRA_TRANSIENT",
                    "attack": "windows-self-interrupt",
                },
                {
                    "finding_id": "OWNER-GATE-9",
                    "status": "CLOSED",
                    "failure_class": "NONE",
                },
            ],
            "merge_authorized": False,
        },
    )
    _write_json(
        evidence / "failures-b.json",
        {
            "package_id": "AS-TEST-FAIL-B",
            "findings": [
                {
                    "finding_id": "CI-FLAKE-001",
                    "status": "OPEN",
                    "failure_class": "INFRA_TRANSIENT",
                }
            ],
            "hard_counters": {"secrets.SECRET_LEAKS": 1, "stale.cache_used_for_skip": 0},
        },
    )
    _write_json(
        evidence / "stale-iv.json",
        {
            "directive": "D-TEST-IV",
            "agent_a_semantic_iv": {
                "class": "VERIFIED_NOW",
                "missing_files": [],
            },
            "CLOUD_IV": {"status": "UNKNOWN"},
            "note": "IV packet without comparable timestamp",
        },
    )
    _write_json(
        evidence / "waiting-dag.json",
        {
            "successor_dag": {
                "nodes": [
                    {"node": "Windows packet", "classification": "BLOCKED_EXTERNAL"},
                    {"node": "Owner review", "classification": "BLOCKED_BY_OWNER"},
                    {"node": "Optional harden", "classification": "READY"},
                ],
                "blocked_by_owner_count": 1,
                "blocked_external_count": 1,
                "ready_count": 1,
            }
        },
    )
    return tmp_path


def test_report_answers_operator_questions(evidence_repo: Path) -> None:
    report = compile_improvement_report(evidence_repo, reference_utc="2026-09-10T06:00:00Z")

    assert report["package_id"] == PACKAGE_ID
    assert report["schema"] == "atlas.improvement-plane.report.v1"
    assert report["honesty"]["recommendation_ne_authority"] is True
    assert report["honesty"]["missing_timestamp_ne_zero"] is True
    assert report["honesty"]["missing_iv_ne_never_verified"] is True
    assert TRUTH_BOUNDARY.split("/")[0].strip() in report["truth_boundary"]

    waiting = report["panels"]["waiting_work"]
    assert waiting["count"] >= 3
    assert any(item["kind"] == "owner_gated" for item in waiting["items"])

    recurring = report["panels"]["recurring_failures"]
    assert recurring["count"] >= 1
    top = recurring["items"][0]
    assert top["finding_id"] == "CI-FLAKE-001"
    assert top["occurrences"] >= 3
    assert top["source_count"] >= 2

    freshness = report["panels"]["evidence_freshness"]
    assert freshness["with_timestamp"] >= 1
    assert freshness["timestamp_unknown"] >= 1
    aged = next(i for i in freshness["items"] if i["path"].endswith("frontier.json"))
    assert aged["waiting_elapsed_seconds"] == 1274400
    assert aged["active_engineering_seconds"] is None
    unknown = next(i for i in freshness["items"] if i["path"].endswith("stale-iv.json"))
    assert unknown["waiting_elapsed_seconds"] is None
    assert unknown["timestamp_status"] == "unknown"

    owner = report["panels"]["owner_action_backlog"]
    assert owner["count"] >= 2
    assert owner["items"][0]["node_id"] == "OWNER-1"
    assert "dependents" in owner["items"][0]

    recs = report["recommendations"]
    assert len(recs) >= 1
    assert all(r["authority"] == "none" for r in recs)
    assert all(r["source_records"] for r in recs)
    assert all("uncertainty" in r for r in recs)
    assert all("proposed_action" in r for r in recs)
    assert all("observed_problem" in r for r in recs)
    assert all("required_actor" in r for r in recs)
    assert all("ranking_rationale" in r for r in recs)
    assert all("scope" in r for r in recs)
    assert all("dependency" in r for r in recs)
    assert all("recommendation_id" in r for r in recs)
    assert all("kind" in r for r in recs)
    assert recs[0]["rank"] == 1
    assert "engineering" in report["recommendations_by_kind"]
    assert report["candidate_identity"]["package_id"] == PACKAGE_ID
    assert report["coverage"]["provenance"]["accepted_count"] >= 1
    assert report["measures_supported"]["owner_action_backlog"] == "supported"
    assert report["measures_supported"]["validation_coverage"] == (
        "not_instrumented_in_evidence_json_v1"
    )
    assert report["honesty"]["ci_status"] == "not_claimed"
    assert report["optional_adoption_proposal"]["required_now"] is False


def test_missing_evidence_dir_is_honest_unknown(tmp_path: Path) -> None:
    report = compile_improvement_report(tmp_path)
    assert report["coverage"]["evidence_files_scanned"] == 0
    assert report["panels"]["waiting_work"]["count"] == 0
    assert report["coverage"]["limits"]
    assert any("docs/evidence" in limit for limit in report["coverage"]["limits"])


def test_optional_vault_ops_inventory_is_read_only(evidence_repo: Path, tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    ops = vault / "generated" / "ops" / "obs"
    ops.mkdir(parents=True)
    _write_json(ops / "sample-receipt.json", {"ok": True})
    # Deliberately omit health-snapshot / workflow-metrics.
    report = compile_improvement_report(evidence_repo, vault_path=vault)
    ops_panel = report["panels"]["ops_receipt_coverage"]
    assert ops_panel["vault_provided"] is True
    assert ops_panel["kinds"]["obs"] == "present"
    assert ops_panel["kinds"]["scheduler"] == "absent"
    assert ops_panel["health_snapshot"] == "absent"
    assert ops_panel["workflow_metrics"] == "absent"
    assert ops_panel["note"].startswith("Absence stays unknown")


def test_markdown_summary_is_concise(evidence_repo: Path) -> None:
    report = compile_improvement_report(evidence_repo, reference_utc="2026-09-10T06:00:00Z")
    md = render_markdown_summary(report)
    assert "# AS-IMPR-PLANE-001" in md
    assert "Waiting work" in md
    assert "Recurring failures" in md
    assert "Owner decisions" in md
    assert "Ranked recommendations" in md
    assert "RECOMMENDATION ≠ AUTHORITY" in md
    assert "does not dispatch" in md.lower() or "no authority" in md.lower()


def test_cli_writes_json_and_md(
    evidence_repo: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out_json = tmp_path / "out" / "report.json"
    out_md = tmp_path / "out" / "report.md"
    rc = impr_main(
        [
            "--repo",
            str(evidence_repo),
            "--reference-utc",
            "2026-09-10T06:00:00Z",
            "--output-json",
            str(out_json),
            "--output-md",
            str(out_md),
        ]
    )
    assert rc == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["package_id"] == PACKAGE_ID
    assert out_md.read_text(encoding="utf-8").startswith("# AS-IMPR-PLANE-001")
    captured = capsys.readouterr()
    assert "recommendations" in captured.out.lower() or PACKAGE_ID in captured.out


def test_report_never_claims_productivity_or_merge(evidence_repo: Path) -> None:
    report = compile_improvement_report(evidence_repo)
    assert report["honesty"]["merge_authorized"] is False
    assert report["honesty"]["execution_authorized"] is False
    assert report["honesty"]["measured_productivity_gain"] == "not_demonstrated"
    assert report["candidate_identity"]["status"]["merge"] == "not_merged"
    assert report["candidate_identity"]["status"]["independent_verification"] == (
        "not_claimed"
    )


def test_labeled_risk_fixtures_duplicate_missing_contradictory_stale(
    tmp_path: Path,
) -> None:
    """Labeled fixtures for acceptance risk classes (AS-IMPR-PLANE-001)."""
    evidence = tmp_path / "docs" / "evidence"
    # duplicate_records + contradictory_status
    _write_json(
        evidence / "dup-a.json",
        {
            "findings": [
                {"finding_id": "DUP-1", "status": "OPEN", "failure_class": "CANDIDATE_DEFECT"}
            ]
        },
    )
    _write_json(
        evidence / "dup-b.json",
        {
            "findings": [
                {"finding_id": "DUP-1", "status": "CLOSED", "failure_class": "CANDIDATE_DEFECT"}
            ]
        },
    )
    # missing timestamps
    _write_json(evidence / "no-ts.json", {"directive": "D-NO-TS", "note": "undated"})
    # stale snapshot (parseable old timestamp)
    _write_json(
        evidence / "stale.json",
        {"as_of_utc": "2026-01-01T00:00:00Z", "directive": "D-STALE"},
    )

    before = {
        path: path.read_bytes()
        for path in evidence.rglob("*.json")
    }
    report = compile_improvement_report(tmp_path, reference_utc="2026-09-10T00:00:00Z")
    after = {
        path: path.read_bytes()
        for path in evidence.rglob("*.json")
    }
    assert before == after, "inspection must not mutate source evidence"

    quality = report["panels"]["data_quality_risks"]
    assert any(row["finding_id"] == "DUP-1" for row in quality["duplicate_records"])
    assert any(row["finding_id"] == "DUP-1" for row in quality["contradictory_status"])
    assert quality["incorrect_attribution"]["status"] == "not_inferred"

    freshness = report["panels"]["evidence_freshness"]
    assert freshness["timestamp_unknown"] >= 1
    stale = next(i for i in freshness["items"] if i["path"].endswith("stale.json"))
    assert stale["waiting_elapsed_seconds"] is not None
    assert stale["waiting_elapsed_seconds"] >= 7 * 24 * 3600
    assert stale["active_engineering_seconds"] is None

    # Missing timestamp stays unknown, not zero.
    missing = next(i for i in freshness["items"] if i["path"].endswith("no-ts.json"))
    assert missing["timestamp_status"] == "unknown"
    assert missing["waiting_elapsed_seconds"] is None

