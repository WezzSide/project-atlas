"""Run decision-quality evaluation corpus (controlled transitions)."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.improvement_plane.compare import compare_reports
from project_atlas.improvement_plane.evaluate import evaluate_outcomes

CORPUS = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "improvement_plane"
    / "dq_corpus"
)


def test_dq_corpus_expected_classifications() -> None:
    expected = json.loads((CORPUS / "EXPECTED.json").read_text(encoding="utf-8"))
    for case in expected["cases"]:
        case_id = case["id"]
        before = json.loads(
            (CORPUS / case_id / "before" / "report.json").read_text(encoding="utf-8")
        )
        after = json.loads(
            (CORPUS / case_id / "after" / "report.json").read_text(encoding="utf-8")
        )
        cmp = compare_reports(before, after, before_label="before", after_label="after")
        want_cmp = case["expected_compare"]
        if want_cmp == "persistent":
            assert cmp["counts"]["persistent"] >= 1, case_id
            assert cmp["counts"]["resolved"] == 0
            assert cmp["counts"]["claimed_closure"] == 0
        else:
            assert cmp["counts"][want_cmp] >= 1, (case_id, cmp["counts"])

        fid = {
            "T01_planted_closure": "ENG-T01",
            "T02_path_continuous": "ENG-T02",
            "T03_coverage_reduced": "ENG-T03",
            "T04_persisted": "ENG-T04",
        }[case_id]
        ev = evaluate_outcomes(
            before_report=before,
            after_report=after,
            outcomes=[
                {
                    "recommendation_id": f"finding:{fid}",
                    "status": case["outcome_status"],
                    "evidence_refs": ["docs/evidence/open.json"],
                }
            ],
        )
        assert ev["evaluations"][0]["result"] == case["expected_evaluate"], case_id
        assert case["transition_kind"] == "controlled"
        assert case["provenance"] == "synthetic-fixture"
