"""AS-CORE2-010 fixture-safe lifecycle certification tests."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.lifecycle_cert import (
    MATRIX_CASE_IDS,
    run_fixture_lifecycle_certification,
    write_report,
)
from project_atlas.schema import validate_record


def test_as_core2_010_full_matrix_certifies(tmp_path: Path) -> None:
    report = run_fixture_lifecycle_certification(tmp_path / "work")
    validate_record(report, "lifecycle-cert-report")
    assert report["estate_pilot_passed"] is False
    assert report["package"] == "AS-CORE2-010"
    assert "at" not in report["generated"]
    by_id = {row["case_id"]: row for row in report["cases"]}
    assert set(by_id) == set(MATRIX_CASE_IDS)
    failed = [row for row in report["cases"] if row["result"] == "fail"]
    assert failed == [], f"matrix failures: {failed}"
    assert report["status"] == "certified"
    assert report["counts"]["failed"] == 0


def test_as_core2_010_report_write_ops_only(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    report = run_fixture_lifecycle_certification(
        tmp_path / "work",
        case_ids=("corrupt",),
        report_vault=vault,
    )
    path = vault / "generated" / "ops" / "lifecycle-cert-report.json"
    assert path.is_file()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["estate_pilot_passed"] is False
    assert loaded["cases"][0]["case_id"] == "corrupt"
    # Round-trip write helper
    write_report(vault, report)
    assert json.loads(path.read_text(encoding="utf-8"))["package"] == "AS-CORE2-010"


def test_as_core2_010_never_claims_pilot_pass(tmp_path: Path) -> None:
    report = run_fixture_lifecycle_certification(tmp_path / "work", case_ids=("new", "unchanged"))
    assert report["estate_pilot_passed"] is False
    assert "ESTATE PILOT" in report["note"] or "PILOT PASS" in report["note"]
