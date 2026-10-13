"""AS-IMPR-PLANE decision-quality-003: closure trust, ranking, journal, safety."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.improvement_plane import compile_improvement_report
from project_atlas.improvement_plane.compare import compare_reports
from project_atlas.improvement_plane.errors import ImprovementPlaneError
from project_atlas.improvement_plane.evaluate import evaluate_outcomes
from project_atlas.improvement_plane.outcomes import load_outcomes, record_outcome
from project_atlas.improvement_plane.quality import (
    capped_occurrence_score_inputs,
    path_continuous_closure,
)
from project_atlas.improvement_plane.readers import load_evidence_records


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _minimal_panels(**kwargs: object) -> dict[str, object]:
    base: dict[str, object] = {
        "owner_action_backlog": {"items": []},
        "recurring_failures": {"items": []},
        "waiting_work": {"items": []},
        "closed_findings": {"items": []},
        "data_quality_risks": {"contradictory_status": []},
    }
    base.update(kwargs)
    return base


def test_planted_closed_on_new_path_is_claimed_closure_not_resolved() -> None:
    """Q1: operator/new-path CLOSED cannot certify improvement."""
    before = {
        "schema": "atlas.improvement-plane.report.v1",
        "coverage": {"provenance": {"accepted_count": 2, "accepted": [{"path": "a.json"}]}},
        "panels": _minimal_panels(
            recurring_failures={
                "items": [
                    {
                        "finding_id": "ENG-PLANT",
                        "failure_class": "CANDIDATE_DEFECT",
                        "open_occurrences": 1,
                        "sources": ["docs/evidence/open.json"],
                    }
                ]
            }
        ),
    }
    after = {
        "schema": "atlas.improvement-plane.report.v1",
        "coverage": {
            "provenance": {
                "accepted_count": 2,
                "accepted": [{"path": "a.json"}, {"path": "planted.json"}],
            }
        },
        "panels": _minimal_panels(
            closed_findings={
                "items": [
                    {
                        "finding_id": "ENG-PLANT",
                        "statuses": ["CLOSED"],
                        "sources": ["docs/evidence/planted-closed.json"],
                    }
                ]
            }
        ),
    }
    cmp = compare_reports(before, after, before_label="before", after_label="after")
    assert cmp["counts"]["resolved"] == 0
    assert cmp["counts"]["claimed_closure"] == 1
    assert cmp["honesty"]["planted_closure_ne_resolved"] is True

    outcomes = [
        {
            "recommendation_id": "finding:ENG-PLANT",
            "status": "completed",
            "evidence_refs": ["docs/evidence/planted-closed.json"],
        }
    ]
    ev = evaluate_outcomes(before_report=before, after_report=after, outcomes=outcomes)
    assert ev["evaluations"][0]["result"] == "inconclusive"
    assert ev["evaluations"][0]["annotation_certified_resolution"] is False
    assert ev["honesty"]["planted_closure_ne_improved"] is True


def test_path_continuous_closed_is_resolved_and_improved() -> None:
    before = {
        "schema": "atlas.improvement-plane.report.v1",
        "coverage": {"provenance": {"accepted_count": 1, "accepted": [{"path": "a.json"}]}},
        "panels": _minimal_panels(
            recurring_failures={
                "items": [
                    {
                        "finding_id": "ENG-OK",
                        "failure_class": "CANDIDATE_DEFECT",
                        "open_occurrences": 1,
                        "sources": ["docs/evidence/shared.json"],
                    }
                ]
            }
        ),
    }
    after = {
        "schema": "atlas.improvement-plane.report.v1",
        "coverage": {"provenance": {"accepted_count": 1, "accepted": [{"path": "a.json"}]}},
        "panels": _minimal_panels(
            closed_findings={
                "items": [
                    {
                        "finding_id": "ENG-OK",
                        "statuses": ["CLOSED"],
                        "sources": ["docs/evidence/shared.json"],
                    }
                ]
            }
        ),
    }
    assert path_continuous_closure(
        before_sources=["docs/evidence/shared.json"],
        closed_sources=["docs/evidence/shared.json"],
    )
    cmp = compare_reports(before, after, before_label="b", after_label="a")
    assert cmp["counts"]["resolved"] == 1
    ev = evaluate_outcomes(
        before_report=before,
        after_report=after,
        outcomes=[
            {
                "recommendation_id": "finding:ENG-OK",
                "status": "completed",
                "evidence_refs": ["docs/evidence/shared.json"],
            }
        ],
    )
    assert ev["evaluations"][0]["result"] == "improved"
    assert ev["evaluations"][0]["path_continuous_closure"] is True


def test_coverage_reduction_flags_comparability() -> None:
    """Q2: reduced accepted coverage must not look like improvement."""
    before = {
        "schema": "atlas.improvement-plane.report.v1",
        "repo_root": "/repo-a",
        "reference_utc": "2026-09-01T00:00:00Z",
        "coverage": {
            "provenance": {
                "accepted_count": 3,
                "accepted": [{"path": "a.json"}, {"path": "b.json"}, {"path": "c.json"}],
            }
        },
        "panels": _minimal_panels(
            recurring_failures={
                "items": [
                    {
                        "finding_id": "ENG-1",
                        "failure_class": "CANDIDATE_DEFECT",
                        "open_occurrences": 1,
                        "sources": ["a.json"],
                    }
                ]
            }
        ),
    }
    after = {
        "schema": "atlas.improvement-plane.report.v1",
        "repo_root": "/repo-a",
        "reference_utc": "2026-09-10T00:00:00Z",
        "coverage": {
            "provenance": {
                "accepted_count": 1,
                "accepted": [{"path": "a.json"}],
            }
        },
        "panels": _minimal_panels(),
    }
    cmp = compare_reports(before, after, before_label="b", after_label="a")
    assert cmp["coverage_reduced"] is True
    assert cmp["comparability"]["uncertain"] is True
    assert cmp["comparability"]["reference_utc_mismatch"] is True
    assert cmp["counts"]["resolved"] == 0
    assert cmp["counts"]["unobservable"] == 1
    assert "reduced accepted coverage" in cmp["unobservable"][0]["note"]


def test_duplicate_occurrences_do_not_inflate_rank_cap(tmp_path: Path) -> None:
    """Q3: duplicate rows in one corpus cannot dominate ranking inputs."""
    capped, sources, note = capped_occurrence_score_inputs(
        open_occurrences=50, source_count=1
    )
    assert sources == 1
    assert capped == 2
    assert "capped" in note

    findings = [
        {
            "finding_id": "DUP-1",
            "status": "OPEN",
            "failure_class": "CANDIDATE_DEFECT",
        }
        for _ in range(20)
    ]
    _write_json(
        tmp_path / "docs" / "evidence" / "dup.json",
        {"as_of_utc": "2026-09-10T00:00:00Z", "findings": findings},
    )
    _write_json(
        tmp_path / "docs" / "evidence" / "s1.json",
        {
            "as_of_utc": "2026-09-10T00:00:00Z",
            "findings": [
                {
                    "finding_id": "MULTI-1",
                    "status": "OPEN",
                    "failure_class": "CANDIDATE_DEFECT",
                }
            ],
        },
    )
    _write_json(
        tmp_path / "docs" / "evidence" / "s2.json",
        {
            "as_of_utc": "2026-09-10T00:00:00Z",
            "findings": [
                {
                    "finding_id": "MULTI-1",
                    "status": "OPEN",
                    "failure_class": "CANDIDATE_DEFECT",
                }
            ],
        },
    )
    report = compile_improvement_report(tmp_path, reference_utc="2026-09-10T00:00:00Z")
    eng = [r for r in report["recommendations"] if r["kind"] == "engineering"]
    by_id = {r["recommendation_id"]: r for r in eng}
    assert "finding:DUP-1" in by_id
    assert "finding:MULTI-1" in by_id
    # Unique sources outweigh duplicate inflation: MULTI-1 ranks above DUP-1.
    assert by_id["finding:MULTI-1"]["rank"] < by_id["finding:DUP-1"]["rank"]


def test_outcome_journal_rejects_self_refs_and_corrupt_store(tmp_path: Path) -> None:
    """Q4: journal integrity — no self-certifying refs; corrupt JSONL fails closed."""
    store = tmp_path / "outcomes.jsonl"
    with pytest.raises(ImprovementPlaneError) as exc:
        record_outcome(
            tmp_path,
            recommendation_id="finding:X",
            status="completed",
            evidence_refs=["docs/evidence/AS-IMPR-PLANE-001-DEMO-REPORT.json"],
            outcomes_file=store,
        )
    assert exc.value.code == "invalid-evidence-ref-self"

    with pytest.raises(ImprovementPlaneError) as exc2:
        record_outcome(
            tmp_path,
            recommendation_id="finding:X",
            status="completed",
            evidence_refs=[".atlas/improvement-plane/outcomes.jsonl"],
            outcomes_file=store,
        )
    assert exc2.value.code == "invalid-evidence-ref-self"

    record_outcome(
        tmp_path,
        recommendation_id="finding:X",
        status="attempted",
        evidence_refs=["docs/evidence/frontier.json"],
        outcomes_file=store,
    )
    record_outcome(
        tmp_path,
        recommendation_id="finding:X",
        status="completed",
        evidence_refs=["docs/evidence/frontier.json"],
        outcomes_file=store,
    )
    rows = load_outcomes(tmp_path, outcomes_file=store)
    assert rows[0]["sequence"] == 1
    assert rows[1]["sequence"] == 2
    assert rows[0]["certifies_resolution"] is False

    store.write_text("{not-json\n", encoding="utf-8")
    with pytest.raises(ImprovementPlaneError) as exc3:
        load_outcomes(tmp_path, outcomes_file=store)
    assert exc3.value.code == "outcomes-store-corrupt"


def test_hard_counter_merges_related_finding_ids(tmp_path: Path) -> None:
    """Self-lane improvement: near-duplicate counter must not inflate recs."""
    _write_json(
        tmp_path / "docs" / "evidence" / "packet.json",
        {
            "as_of_utc": "2026-09-10T00:00:00Z",
            "findings": [
                {
                    "code": "GIT_REMOTE_PASSWORD_ECHO",
                    "severity": "HIGH",
                    "detail": "password echoed",
                }
            ],
            "hard_counters": {
                "secrets.REMOTE_PASSWORD_ECHO": 1,
                "secrets.SECRET_LEAKS": 1,
            },
        },
    )
    report = compile_improvement_report(tmp_path, reference_utc="2026-09-10T00:00:00Z")
    eng_ids = [
        r["recommendation_id"]
        for r in report["recommendations"]
        if r["kind"] == "engineering"
    ]
    assert "finding:GIT_REMOTE_PASSWORD_ECHO" in eng_ids
    assert "finding:secrets.REMOTE_PASSWORD_ECHO" not in eng_ids
    assert "finding:secrets.SECRET_LEAKS" in eng_ids
    items = report["panels"]["recurring_failures"]["items"]
    git = next(i for i in items if i["finding_id"] == "GIT_REMOTE_PASSWORD_ECHO")
    assert "secrets.REMOTE_PASSWORD_ECHO" in git.get("related_ids", [])


def test_queued_opportunity_not_labeled_engineering(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "docs" / "evidence" / "frontier.json",
        {
            "as_of_utc": "2026-09-10T00:00:00Z",
            "successor_dag": {
                "nodes": [{"node": "Ready-item", "classification": "READY"}]
            },
        },
    )
    report = compile_improvement_report(tmp_path, reference_utc="2026-09-10T00:00:00Z")
    queued = [
        r
        for r in report["recommendations"]
        if str(r["recommendation_id"]).startswith("queue:")
    ]
    assert queued
    assert all(r["kind"] == "other" for r in queued)


def test_readers_exclude_secrets_and_respect_file_bound(tmp_path: Path) -> None:
    """Q5: evidence consumed as data; secrets excluded; oversized files skipped."""
    evidence = tmp_path / "docs" / "evidence"
    _write_json(
        evidence / "ok.json",
        {"as_of_utc": "2026-09-10T00:00:00Z", "findings": []},
    )
    (evidence / "secret.json").write_text(
        json.dumps(
            {
                "as_of_utc": "2026-09-10T00:00:00Z",
                "note": "api_key = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ012345'",
            }
        ),
        encoding="utf-8",
    )
    big = evidence / "big.json"
    big.write_bytes(b"{" + (b"a" * 2_100_000) + b"}")
    _write_json(
        evidence / "AS-IMPR-PLANE-001-REVIEW-CI-REPORT.json",
        {"schema": "atlas.improvement-plane.review-ci.v1", "status": "in_progress"},
    )

    records = load_evidence_records(tmp_path)
    by_path = {r["path"]: r for r in records}
    assert by_path["docs/evidence/ok.json"]["parse_status"] == "ok"
    assert by_path["docs/evidence/secret.json"]["parse_status"] == "excluded_secrets"
    assert "payload" in by_path["docs/evidence/secret.json"]
    assert by_path["docs/evidence/secret.json"]["payload"] is None
    assert by_path["docs/evidence/big.json"]["parse_status"] == "skipped_limit"
    assert (
        by_path["docs/evidence/AS-IMPR-PLANE-001-REVIEW-CI-REPORT.json"]["parse_status"]
        == "excluded_self_ingest"
    )
    # Matched secret content must not appear in the record.
    dumped = json.dumps(by_path["docs/evidence/secret.json"])
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345" not in dumped
