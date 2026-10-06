"""AS-IMPR-PLANE continuation-002: compare, outcomes, evaluate, self-ingest."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.improvement_plane import compile_improvement_report
from project_atlas.improvement_plane.__main__ import main as impr_main
from project_atlas.improvement_plane.compare import compare_reports
from project_atlas.improvement_plane.evaluate import evaluate_outcomes
from project_atlas.improvement_plane.outcomes import load_outcomes, record_outcome


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _fixture_repo(tmp_path: Path) -> Path:
    evidence = tmp_path / "docs" / "evidence"
    _write_json(
        evidence / "frontier.json",
        {
            "as_of_utc": "2026-08-26T12:00:00Z",
            "owner_gated_nodes": [
                {
                    "node_id": "OWNER-1",
                    "description": "Merge PR #1",
                    "next_required_owner_action": "Review PR #1",
                    "dependents": ["OWNER-2"],
                }
            ],
            "findings": [
                {
                    "finding_id": "ENG-1",
                    "status": "OPEN",
                    "failure_class": "CANDIDATE_DEFECT",
                }
            ],
            "successor_dag": {
                "nodes": [
                    {"node": "Windows", "classification": "BLOCKED_EXTERNAL"},
                    {"node": "Optional", "classification": "READY"},
                ]
            },
        },
    )
    # Lane-generated report must not self-ingest.
    _write_json(
        evidence / "AS-IMPR-PLANE-001-DEMO-REPORT.json",
        {
            "schema": "atlas.improvement-plane.report.v1",
            "package_id": "AS-IMPR-PLANE-001",
            "generator": "atlas-impr-plane-001",
            "recommendations": [
                {
                    "recommendation_id": "fake:self",
                    "title": "should not appear",
                    "source_records": ["docs/evidence/AS-IMPR-PLANE-001-DEMO-REPORT.json"],
                }
            ],
            "panels": {
                "owner_action_backlog": {
                    "items": [
                        {
                            "node_id": "FAKE-SELF",
                            "description": "from self report",
                            "source": "docs/evidence/AS-IMPR-PLANE-001-DEMO-REPORT.json",
                        }
                    ]
                },
                "recurring_failures": {"items": []},
                "waiting_work": {"items": []},
            },
        },
    )
    return tmp_path


def test_self_ingest_excluded_from_recommendations(tmp_path: Path) -> None:
    repo = _fixture_repo(tmp_path)
    report = compile_improvement_report(repo, reference_utc="2026-09-10T00:00:00Z")
    assert report["coverage"]["evidence_files_excluded_self_ingest"] >= 1
    ids = [r["recommendation_id"] for r in report["recommendations"]]
    assert "fake:self" not in ids
    assert "owner:FAKE-SELF" not in ids
    assert any(r["recommendation_id"] == "owner:OWNER-1" for r in report["recommendations"])
    assert any(r["kind"] == "engineering" for r in report["recommendations"])
    assert any(r["kind"] == "external_dependency" for r in report["recommendations"])


def test_compare_reordered_inputs_stable_and_disappearance_unobservable(
    tmp_path: Path,
) -> None:
    before = {
        "panels": {
            "owner_action_backlog": {
                "items": [
                    {"node_id": "OWNER-1", "description": "A", "source": "a.json"},
                    {"node_id": "OWNER-2", "description": "B", "source": "b.json"},
                ]
            },
            "recurring_failures": {
                "items": [
                    {
                        "finding_id": "ENG-1",
                        "failure_class": "CANDIDATE_DEFECT",
                        "open_occurrences": 2,
                        "sources": ["x.json", "y.json"],
                    }
                ]
            },
            "waiting_work": {"items": []},
            "data_quality_risks": {"contradictory_status": []},
        }
    }
    # Reordered owner list + ENG-1 fingerprint change + OWNER-2 disappeared.
    after = {
        "panels": {
            "owner_action_backlog": {
                "items": [
                    {"node_id": "OWNER-1", "description": "A", "source": "a.json"},
                ]
            },
            "recurring_failures": {
                "items": [
                    {
                        "finding_id": "ENG-1",
                        "failure_class": "CANDIDATE_DEFECT",
                        "open_occurrences": 5,
                        "sources": ["y.json", "x.json"],
                    }
                ]
            },
            "waiting_work": {
                "items": [
                    {
                        "id": "NEW-1",
                        "classification": "READY",
                        "summary": "new queue",
                        "source": "z.json",
                    }
                ]
            },
            "data_quality_risks": {"contradictory_status": []},
        }
    }
    # Swap ordering of before items should not create false "new".
    before_reordered = {
        "panels": {
            "owner_action_backlog": {
                "items": list(reversed(before["panels"]["owner_action_backlog"]["items"]))
            },
            "recurring_failures": before["panels"]["recurring_failures"],
            "waiting_work": {"items": []},
            "data_quality_risks": {"contradictory_status": []},
        }
    }
    cmp_same = compare_reports(
        before, before_reordered, before_label="a", after_label="b"
    )
    assert cmp_same["counts"]["new"] == 0
    assert cmp_same["counts"]["unobservable"] == 0
    assert cmp_same["counts"]["persistent"] >= 2

    cmp = compare_reports(before, after, before_label="before", after_label="after")
    assert cmp["counts"]["resolved"] == 0
    assert cmp["honesty"]["disappearing_ne_resolved"] is True
    assert any(row["id"] == "owner:OWNER-2" for row in cmp["unobservable"])
    assert any(row["id"] == "finding:ENG-1" for row in cmp["changed"])
    assert any(row["id"].startswith("queue:READY:") for row in cmp["new"])


def test_compare_resolved_requires_closed_finding_evidence() -> None:
    before = {
        "schema": "atlas.improvement-plane.report.v1",
        "panels": {
            "owner_action_backlog": {"items": []},
            "recurring_failures": {
                "items": [
                    {
                        "finding_id": "ENG-1",
                        "failure_class": "CANDIDATE_DEFECT",
                        "open_occurrences": 1,
                        "sources": ["a.json"],
                    }
                ]
            },
            "waiting_work": {"items": []},
            "closed_findings": {"items": []},
        },
    }
    after_unobs = {
        "schema": "atlas.improvement-plane.report.v1",
        "panels": {
            "owner_action_backlog": {"items": []},
            "recurring_failures": {"items": []},
            "waiting_work": {"items": []},
            "closed_findings": {"items": []},
        },
    }
    # Planted CLOSED on a new path must NOT count as resolved.
    after_claimed = {
        "schema": "atlas.improvement-plane.report.v1",
        "panels": {
            "owner_action_backlog": {"items": []},
            "recurring_failures": {"items": []},
            "waiting_work": {"items": []},
            "closed_findings": {
                "items": [
                    {
                        "finding_id": "ENG-1",
                        "statuses": ["CLOSED"],
                        "sources": ["b.json"],
                    }
                ]
            },
        },
    }
    after_resolved = {
        "schema": "atlas.improvement-plane.report.v1",
        "panels": {
            "owner_action_backlog": {"items": []},
            "recurring_failures": {"items": []},
            "waiting_work": {"items": []},
            "closed_findings": {
                "items": [
                    {
                        "finding_id": "ENG-1",
                        "statuses": ["CLOSED"],
                        "sources": ["a.json"],
                    }
                ]
            },
        },
    }
    u = compare_reports(before, after_unobs, before_label="b", after_label="a")
    assert u["counts"]["unobservable"] == 1
    assert u["counts"]["resolved"] == 0
    claimed = compare_reports(before, after_claimed, before_label="b", after_label="a")
    assert claimed["counts"]["resolved"] == 0
    assert claimed["counts"]["claimed_closure"] == 1
    r = compare_reports(before, after_resolved, before_label="b", after_label="a")
    assert r["counts"]["resolved"] == 1
    assert r["counts"]["claimed_closure"] == 0
    assert r["counts"]["unobservable"] == 0


def test_compare_incomplete_and_incompatible() -> None:
    incomplete = compare_reports(
        {"schema": "atlas.improvement-plane.report.v1", "panels": {}},
        {
            "schema": "atlas.improvement-plane.report.v1",
            "panels": {
                "owner_action_backlog": {"items": []},
                "recurring_failures": {"items": []},
                "waiting_work": {"items": []},
            },
        },
        before_label="bad",
        after_label="ok",
    )
    assert incomplete["comparison_status"] == "incomplete"
    incompatible = compare_reports(
        {
            "schema": "atlas.ops.report.v1",
            "panels": {
                "owner_action_backlog": {"items": []},
                "recurring_failures": {"items": []},
                "waiting_work": {"items": []},
            },
        },
        {
            "schema": "atlas.improvement-plane.report.v1",
            "panels": {
                "owner_action_backlog": {"items": []},
                "recurring_failures": {"items": []},
                "waiting_work": {"items": []},
            },
        },
        before_label="ops",
        after_label="impr",
    )
    assert incompatible["comparison_status"] == "incompatible"


def test_outcome_record_and_invalid_attribution(tmp_path: Path) -> None:
    repo = _fixture_repo(tmp_path)
    store = tmp_path / "outcomes.jsonl"
    ok = record_outcome(
        repo,
        recommendation_id="finding:ENG-1",
        status="attempted",
        evidence_refs=["docs/evidence/frontier.json"],
        outcomes_file=store,
    )
    assert ok["ok"] is True
    assert ok["outcome"]["dag_gate_resolved"] is False
    rows = load_outcomes(repo, outcomes_file=store)
    assert len(rows) == 1

    rc = impr_main(
        [
            "outcome",
            "--repo",
            str(repo),
            "--recommendation-id",
            "finding:ENG-1",
            "--status",
            "completed",
            "--outcomes-file",
            str(store),
            "--json",
        ]
    )
    assert rc == 2  # missing evidence refs


def test_evaluate_association_not_causation(tmp_path: Path) -> None:
    before = compile_improvement_report(_fixture_repo(tmp_path))
    # After: ENG-1 still present (persisted despite completed annotation)
    after_repo = tmp_path / "after"
    evidence = after_repo / "docs" / "evidence"
    _write_json(
        evidence / "frontier.json",
        {
            "owner_gated_nodes": [
                {
                    "node_id": "OWNER-1",
                    "description": "Merge PR #1",
                    "next_required_owner_action": "Review PR #1",
                    "dependents": [],
                }
            ],
            "findings": [
                {
                    "finding_id": "ENG-1",
                    "status": "OPEN",
                    "failure_class": "CANDIDATE_DEFECT",
                }
            ],
        },
    )
    after = compile_improvement_report(after_repo)
    store = tmp_path / "out.jsonl"
    record_outcome(
        tmp_path,
        recommendation_id="finding:ENG-1",
        status="completed",
        evidence_refs=["docs/evidence/frontier.json"],
        outcomes_file=store,
    )
    # Ambiguous: completed annotation but observation disappeared without proof.
    after_gone = {
        "panels": {
            "owner_action_backlog": {"items": []},
            "recurring_failures": {"items": []},
            "waiting_work": {"items": []},
            "data_quality_risks": {"contradictory_status": []},
        }
    }
    ev = evaluate_outcomes(
        before_report=before,
        after_report=after,
        outcomes=load_outcomes(tmp_path, outcomes_file=store),
    )
    assert ev["honesty"]["association_ne_causation"] is True
    assert ev["honesty"]["time_saved_claimed"] is False
    assert ev["sample_size"] == 1
    assert ev["evaluations"][0]["result"] == "persisted"

    ev2 = evaluate_outcomes(
        before_report=before,
        after_report=after_gone,
        outcomes=load_outcomes(tmp_path, outcomes_file=store),
    )
    assert ev2["evaluations"][0]["result"] == "inconclusive"

    after_closed = {
        "panels": {
            "owner_action_backlog": {"items": []},
            "recurring_failures": {"items": []},
            "waiting_work": {"items": []},
            "closed_findings": {
                "items": [
                    {
                        "finding_id": "ENG-1",
                        "statuses": ["CLOSED"],
                        "sources": ["docs/evidence/frontier.json"],
                    }
                ]
            },
            "data_quality_risks": {"contradictory_status": []},
        }
    }
    ev3 = evaluate_outcomes(
        before_report=before,
        after_report=after_closed,
        outcomes=load_outcomes(tmp_path, outcomes_file=store),
    )
    assert ev3["evaluations"][0]["result"] == "improved"


def test_cli_compare_and_expected_input_error(tmp_path: Path, capsys) -> None:
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    repo = _fixture_repo(tmp_path / "repo")
    report = compile_improvement_report(repo)
    _write_json(before_path, report)
    _write_json(after_path, report)
    rc = impr_main(
        ["compare", "--before", str(before_path), "--after", str(after_path), "--json"]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["schema"] == "atlas.improvement-plane.compare.v1"

    rc_bad = impr_main(
        [
            "compare",
            "--before",
            str(tmp_path / "missing.json"),
            "--after",
            str(after_path),
        ]
    )
    assert rc_bad == 2
    err = capsys.readouterr()
    assert "input-not-found" in (err.out + err.err)
    assert "Traceback" not in (err.out + err.err)


def test_report_subcommand_write_vs_readonly(tmp_path: Path) -> None:
    repo = _fixture_repo(tmp_path)
    out = tmp_path / "out.json"
    evidence_before = {
        p: p.read_bytes() for p in (repo / "docs" / "evidence").rglob("*.json")
    }
    rc = impr_main(
        [
            "report",
            "--repo",
            str(repo),
            "--reference-utc",
            "2026-09-10T00:00:00Z",
            "--output-json",
            str(out),
            "--json",
        ]
    )
    assert rc == 0
    assert out.is_file()
    evidence_after = {
        p: p.read_bytes() for p in (repo / "docs" / "evidence").rglob("*.json")
    }
    assert evidence_before == evidence_after
