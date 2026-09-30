"""AS-SEC-SCAN-ESTATE-DISCOVERY-YAML-001 — scan decoded marker/package ids.

Estate discovery writes ``fingerprint.atlas_project_id`` / ``package_name``
after YAML/JSON decode. Quoted ``\\u`` / ``\\x`` escapes miss raw
``scan_text`` and must not persist as identity in the discovery report.
Distinct persist sink from Core ``atlas discover`` (#944) and connect (#943).
"""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.cli import EXIT_OK, main
from project_atlas.estate_discovery import discover_estate, write_discovery_report
from project_atlas.secrets import scan_text

TOKEN = "AKIAAAAAAAAAAAAAAAAA"  # AKIA + 16 A — matches cloud-access-key


def _estate_with_marker(tmp_path: Path, raw_id_line: str) -> Path:
    estate = tmp_path / "estate"
    proj = estate / "sample-app"
    proj.mkdir(parents=True)
    (proj / "README.md").write_text("# sample\n", encoding="utf-8")
    (proj / ".atlas-project.yaml").write_text(
        "schema_version: 1\n"
        "project:\n"
        f"  id: {raw_id_line}\n",
        encoding="utf-8",
    )
    return estate


def _report_blob(report: dict[str, object]) -> str:
    return json.dumps(report, sort_keys=True)


def test_quoted_unicode_escape_akia_is_not_in_report(tmp_path: Path) -> None:
    raw_id = '"\\u0041KIAAAAAAAAAAAAAAAAA"'
    estate = _estate_with_marker(tmp_path, raw_id)
    raw = (estate / "sample-app" / ".atlas-project.yaml").read_text(encoding="utf-8")
    assert scan_text(raw) == []
    assert TOKEN not in raw

    report = discover_estate(estate, include_projects=True, include_knowledge=False)
    blob = _report_blob(report)
    assert TOKEN not in blob
    projects = report["candidates"]["projects"]
    assert projects
    fingerprint = projects[0]["fingerprint"]
    assert fingerprint.get("atlas_project_id") is None
    assert fingerprint.get("marker_status") == "invalid"

    output = tmp_path / "estate-discovery-report.json"
    write_discovery_report(report, output)
    written = output.read_text(encoding="utf-8")
    assert TOKEN not in written
    assert json.loads(written)["candidates"]["projects"][0]["fingerprint"][
        "atlas_project_id"
    ] is None


def test_quoted_hex_escape_akia_is_not_in_report(tmp_path: Path) -> None:
    estate = _estate_with_marker(tmp_path, '"\\x41KIAAAAAAAAAAAAAAAAA"')
    raw = (estate / "sample-app" / ".atlas-project.yaml").read_text(encoding="utf-8")
    assert scan_text(raw) == []
    report = discover_estate(estate, include_projects=True, include_knowledge=False)
    assert TOKEN not in _report_blob(report)
    assert report["candidates"]["projects"][0]["fingerprint"]["atlas_project_id"] is None


def test_json_unicode_package_name_is_not_in_report(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    proj = estate / "sample-app"
    proj.mkdir(parents=True)
    (proj / "README.md").write_text("# sample\n", encoding="utf-8")
    (proj / ".git").mkdir()
    (proj / "package.json").write_text(
        '{"name": "\\u0041KIAAAAAAAAAAAAAAAAA"}\n',
        encoding="utf-8",
    )
    raw = (proj / "package.json").read_text(encoding="utf-8")
    assert scan_text(raw) == []
    report = discover_estate(estate, include_projects=True, include_knowledge=False)
    assert TOKEN not in _report_blob(report)
    fingerprint = report["candidates"]["projects"][0]["fingerprint"]
    assert fingerprint.get("package_name") is None


def test_clean_marker_id_still_discovered(tmp_path: Path) -> None:
    estate = _estate_with_marker(tmp_path, "sample-estate")
    report = discover_estate(estate, include_projects=True, include_knowledge=False)
    fingerprint = report["candidates"]["projects"][0]["fingerprint"]
    assert fingerprint.get("atlas_project_id") == "sample-estate"
    assert fingerprint.get("marker_status") == "ok"
    assert TOKEN not in _report_blob(report)
    output = tmp_path / "estate-discovery-report.json"
    write_discovery_report(report, output)
    assert "sample-estate" in output.read_text(encoding="utf-8")
    assert TOKEN not in output.read_text(encoding="utf-8")


def test_cli_unicode_escape_akia_writes_no_token(tmp_path: Path) -> None:
    estate = _estate_with_marker(tmp_path, '"\\u0041KIAAAAAAAAAAAAAAAAA"')
    output = tmp_path / "estate-discovery-report.json"
    assert (
        main(["discover", "--root", str(estate), "--output", str(output)]) == EXIT_OK
    )
    assert output.is_file()
    written = output.read_text(encoding="utf-8")
    assert TOKEN not in written
    payload = json.loads(written)
    assert (
        payload["candidates"]["projects"][0]["fingerprint"]["atlas_project_id"] is None
    )
