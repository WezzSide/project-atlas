"""AS-ADV-RELEASE-003 objective performance/determinism tests."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.adv_release_cert import (
    MATRIX_CASE_IDS,
    run_fixture_adv_release_certification,
)
from project_atlas.schema import validate_record


def test_as_adv_release_003_perf_budget_smoke_records_objective_signals(
    tmp_path: Path,
) -> None:
    report = run_fixture_adv_release_certification(
        tmp_path / "work", case_ids=("perf_budget_smoke",)
    )
    validate_record(report, "adv-release-cert-report")
    assert report["release_certified"] is False
    row = report["cases"][0]
    assert row["case_id"] == "perf_budget_smoke"
    assert row["result"] == "pass", row
    observed = json.loads(row["observed"])
    signals = observed["signals"]
    assert signals["operation_count"] == 4
    assert signals["source_file_count"] == 2
    assert signals["source_byte_count"] > 0
    assert signals["stable_file_count"] > 0
    assert signals["stable_byte_count"] > 0
    assert "timing" not in row["observed"].lower()


def test_as_adv_release_003_perf_signals_repeat_exactly(tmp_path: Path) -> None:
    first = run_fixture_adv_release_certification(
        tmp_path / "first", case_ids=("perf_budget_smoke",)
    )
    second = run_fixture_adv_release_certification(
        tmp_path / "second", case_ids=("perf_budget_smoke",)
    )
    assert first["cases"][0]["observed"] == second["cases"][0]["observed"]


def test_as_adv_release_003_determinism_reports_equal_digest_summaries(
    tmp_path: Path,
) -> None:
    report = run_fixture_adv_release_certification(
        tmp_path / "work", case_ids=("determinism_pipeline",)
    )
    row = report["cases"][0]
    assert row["result"] == "pass", row
    observed = json.loads(row["observed"])
    assert observed["drift"] == []
    assert observed["first"] == observed["second"]
    assert observed["first"]["file_count"] > 0
    assert observed["first"]["byte_count"] > 0
    assert len(observed["first"]["sha256"]) == 64


def test_as_adv_release_003_matrix_and_docs_keep_release_no() -> None:
    assert "perf_budget_smoke" in MATRIX_CASE_IDS
    root = Path(__file__).resolve().parents[2]
    doc = (root / "docs" / "AS-ADV-RELEASE-003-perf-determinism.md").read_text(encoding="utf-8")
    assert "RELEASE = **NO**" in doc
    assert "RELEASE CERTIFIED = **NO**" in doc
    assert "release_certified: false" in doc
    assert "perf_budget_smoke" in doc
    assert "wall-clock" in doc
