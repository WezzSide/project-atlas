"""AS-CODER-ALPHA-PORTFOLIO-READ-001 — vault-scoped portfolio REPORT READ."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.cli import EXIT_ERROR, EXIT_OK, main
from project_atlas.mcp_server import invoke_mcp_tool
from project_atlas.web_api.portfolio_read import (
    PACKAGE_ID,
    WebPortfolioReadError,
    read_portfolio_view,
    render_portfolio_status_text,
)


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


def _write_portfolio_artifact(
    vault: Path,
    *,
    name: str = "overview.json",
    extra: dict[str, object] | None = None,
) -> None:
    directory = vault / "generated" / "portfolio"
    directory.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "schema_version": 1,
        "title": "portfolio overview",
        "summary": "two projects",
        "projects": [{"project_id": "harbor-api"}],
        "MERGE_AUTHORIZATION": "NOT_GRANTED",
        "honesty": {
            "portfolio_is_authority": False,
            "layer_c_is_truth_core": False,
        },
    }
    if extra:
        payload.update(extra)
    (directory / name).write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_empty_vault_is_empty_not_healthy(tmp_path: Path) -> None:
    report = read_portfolio_view(_vault(tmp_path))
    assert report["package_id"] == PACKAGE_ID
    assert report["status"] == "EMPTY"
    assert report["available"] is False
    assert report["honesty"]["write_applied"] is False
    assert report["honesty"]["materialize_invoked"] is False
    assert report["honesty"]["build_portfolio_invoked"] is False
    assert report["honesty"]["MERGE_AUTHORIZATION"] == "NOT_GRANTED"
    text = render_portfolio_status_text(report)
    assert "EMPTY != HEALTHY" in text
    assert all(ord(char) < 128 for char in text)


def test_present_artifact_is_not_authority(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_portfolio_artifact(vault, extra={"summary": "coverage gaps"})
    report = read_portfolio_view(vault)
    assert report["status"] == "PRESENT"
    assert report["available"] is True
    assert report["view"]["artifact_count"] == 1
    assert report["view"]["artifacts"][0]["record"]["summary"] == "coverage gaps"
    assert report["honesty"]["portfolio_is_authority"] is False
    assert report["honesty"]["layer_c_is_truth_core"] is False
    text = render_portfolio_status_text(report)
    assert "coverage gaps" in text


def test_malformed_is_unknown_not_healthy(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    path = vault / "generated" / "portfolio" / "overview.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{nope", encoding="utf-8")
    report = read_portfolio_view(vault)
    assert report["status"] == "UNKNOWN"
    assert report["view"]["malformed_count"] == 1
    assert report["available"] is False


def test_mixed_valid_and_corrupt_is_unknown(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_portfolio_artifact(vault, name="overview.json")
    bad = vault / "generated" / "portfolio" / "stale-knowledge.json"
    bad.write_text("{nope", encoding="utf-8")
    report = read_portfolio_view(vault)
    assert report["status"] == "UNKNOWN"
    assert report["view"]["artifact_count"] == 2
    assert report["view"]["malformed_count"] == 1
    assert report["available"] is False


def test_merge_and_pilot_invention_fail_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_portfolio_artifact(vault, extra={"MERGE_AUTHORIZATION": "GRANTED"})
    with pytest.raises(WebPortfolioReadError, match="merge-authority-invented"):
        read_portfolio_view(vault)
    _write_portfolio_artifact(vault, extra={"authentic_pilot": True})
    with pytest.raises(WebPortfolioReadError, match="authentic-pilot-invented"):
        read_portfolio_view(vault)


def test_authority_invention_fail_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_portfolio_artifact(vault, extra={"portfolio_is_authority": True})
    with pytest.raises(WebPortfolioReadError, match="authority-invented"):
        read_portfolio_view(vault)


def test_render_is_ascii_even_with_unicode_summary(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_portfolio_artifact(vault, extra={"summary": "revisión de cobertura"})
    text = render_portfolio_status_text(read_portfolio_view(vault))
    assert all(ord(char) < 128 for char in text)
    assert "revisi?n de cobertura" in text


def test_symlink_artifact_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    foreign = tmp_path / "foreign.json"
    foreign.write_text(json.dumps({"summary": "leak"}) + "\n", encoding="utf-8")
    directory = vault / "generated" / "portfolio"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "overview.json").symlink_to(foreign)
    with pytest.raises(WebPortfolioReadError, match="portfolio-read-symlink-forbidden"):
        read_portfolio_view(vault)


def test_module_does_not_write_or_materialize() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/web_api/portfolio_read.py").read_text(encoding="utf-8")
    for name in (
        "from project_atlas.portfolio",
        "build_portfolio(",
        "build_portfolio_payloads(",
        "read_portfolio_state(",
        "write_text(",
        "write_json_atomic",
        "from project_atlas.ingestion",
        "from project_atlas.atlas3",
        "atlas.query.read",
    ):
        assert name not in source


def test_cli_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = _vault(tmp_path)
    assert main(["portfolio-status", "--vault", str(vault)]) == EXIT_OK
    rendered = capsys.readouterr().out
    assert "EMPTY" in rendered
    assert all(ord(char) < 128 for char in rendered)
    _write_portfolio_artifact(vault)
    assert main(["portfolio-status", "--vault", str(vault), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PRESENT"


def test_cli_help_is_ascii(capsys: pytest.CaptureFixture[str]) -> None:
    try:
        code = main(["portfolio-status", "--help"])
    except SystemExit as exc:
        code = int(exc.code or 0)
    assert code == 0
    help_text = capsys.readouterr().out
    assert "never writes" in help_text or "read-only" in help_text or "PORTFOLIO" in help_text
    assert all(ord(char) < 128 for char in help_text)
    help_text.encode("cp1252")


def test_mcp_portfolio_read_empty(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    report = invoke_mcp_tool(vault, "atlas.portfolio.read")
    assert report["tool_id"] == "atlas.portfolio.read"
    assert report["live_mcp_read"] is True
    payload = report["result"]
    assert payload["package_id"] == PACKAGE_ID
    assert payload["status"] == "EMPTY"
    assert payload["honesty"]["write_applied"] is False
    assert payload["honesty"]["layer_c_is_truth_core"] is False


def test_unknown_vault_is_operational_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    assert main(["portfolio-status", "--vault", str(missing)]) == EXIT_ERROR
