"""AS-ADV-RELEASE-001 fixture certification tests."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.adv_release_cert import (
    MATRIX_CASE_IDS,
    run_fixture_adv_release_certification,
    write_report,
)
from project_atlas.cli import main
from project_atlas.schema import validate_record


def test_as_adv_release_001_full_matrix_certifies(tmp_path: Path) -> None:
    report = run_fixture_adv_release_certification(tmp_path / "work")
    validate_record(report, "adv-release-cert-report")
    assert report["release_certified"] is False
    assert report["estate_pilot_passed"] is False
    assert report["web_application_accepted"] is False
    assert report["package"] == "AS-ADV-RELEASE-001"
    assert "at" not in report["generated"]
    by_id = {row["case_id"]: row for row in report["cases"]}
    assert set(by_id) == set(MATRIX_CASE_IDS)
    failed = [row for row in report["cases"] if row["result"] == "fail"]
    assert failed == [], f"matrix failures: {failed}"
    assert report["status"] == "certified"


def test_as_adv_release_001_report_write_ops_only(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    report = run_fixture_adv_release_certification(
        tmp_path / "work",
        case_ids=("recovery_promote_noop",),
        report_vault=vault,
    )
    path = vault / "generated" / "ops" / "adv-release-cert-report.json"
    assert path.is_file()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["release_certified"] is False
    write_report(vault, report)
    assert json.loads(path.read_text(encoding="utf-8"))["package"] == "AS-ADV-RELEASE-001"


def test_as_adv_release_001_cli_certify(tmp_path: Path) -> None:
    work = tmp_path / "work"
    vault = tmp_path / "vault"
    vault.mkdir()
    code = main(
        [
            "adv",
            "certify",
            "--work-root",
            str(work),
            "--report-vault",
            str(vault),
        ]
    )
    assert code == 0
    assert (vault / "generated" / "ops" / "adv-release-cert-report.json").is_file()
    loaded = json.loads(
        (vault / "generated" / "ops" / "adv-release-cert-report.json").read_text(encoding="utf-8")
    )
    assert "clean_clone_replay" in {row["case_id"] for row in loaded["cases"]}
    assert loaded["release_certified"] is False
