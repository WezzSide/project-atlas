"""AS-CODER-ALPHA-BITEMPORAL-READ-001 — vault-scoped bitemporal REPORT READ."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.cli import EXIT_ERROR, EXIT_OK, main
from project_atlas.mcp_server import invoke_mcp_tool
from project_atlas.web_api.bitemporal_read import (
    PACKAGE_ID,
    WebBitemporalReadError,
    read_bitemporal_view,
    render_bitemporal_status_text,
)


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


def _write_catalog(
    vault: Path,
    *,
    project_id: str = "harbor-api",
    extra: dict[str, object] | None = None,
) -> None:
    directory = vault / "generated" / "ops" / "bitemporal"
    directory.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "schema_version": 1,
        "catalog_id": project_id,
        "window_count": 2,
        "windows": [],
        "MERGE_AUTHORIZATION": "NOT_GRANTED",
        "honesty": {
            "catalog_is_authority": False,
            "graph_is_authority": False,
        },
    }
    if extra:
        payload.update(extra)
    (directory / f"{project_id}-validity-catalog.json").write_text(
        json.dumps(payload) + "\n",
        encoding="utf-8",
    )


def test_empty_vault_is_empty_not_healthy(tmp_path: Path) -> None:
    report = read_bitemporal_view(_vault(tmp_path))
    assert report["package_id"] == PACKAGE_ID
    assert report["status"] == "EMPTY"
    assert report["available"] is False
    assert report["honesty"]["write_applied"] is False
    assert report["honesty"]["write_catalog_invoked"] is False
    assert report["honesty"]["MERGE_AUTHORIZATION"] == "NOT_GRANTED"
    text = render_bitemporal_status_text(report)
    assert "EMPTY != HEALTHY" in text
    assert all(ord(char) < 128 for char in text)


def test_present_catalog_is_not_authority(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_catalog(vault)
    report = read_bitemporal_view(vault)
    assert report["status"] == "PRESENT"
    assert report["available"] is True
    assert report["view"]["artifact_count"] == 1
    assert report["view"]["projects"][0]["record"]["window_count"] == 2
    assert report["honesty"]["catalog_is_authority"] is False
    assert report["honesty"]["graph_is_authority"] is False
    text = render_bitemporal_status_text(report)
    assert "harbor-api" in text


def test_malformed_is_unknown_not_healthy(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    path = vault / "generated" / "ops" / "bitemporal" / "harbor-api-validity-catalog.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{nope", encoding="utf-8")
    report = read_bitemporal_view(vault)
    assert report["status"] == "UNKNOWN"
    assert report["view"]["malformed_count"] == 1
    assert report["available"] is False


def test_mixed_valid_and_corrupt_is_unknown(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_catalog(vault, project_id="harbor-api")
    bad = vault / "generated" / "ops" / "bitemporal" / "decoy-validity-catalog.json"
    bad.write_text("{nope", encoding="utf-8")
    report = read_bitemporal_view(vault)
    assert report["status"] == "UNKNOWN"
    assert report["view"]["artifact_count"] == 2
    assert report["view"]["malformed_count"] == 1
    assert report["available"] is False


def test_merge_and_pilot_invention_fail_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_catalog(vault, extra={"MERGE_AUTHORIZATION": "GRANTED"})
    with pytest.raises(WebBitemporalReadError, match="merge-authority-invented"):
        read_bitemporal_view(vault)
    _write_catalog(vault, extra={"authentic_pilot": True})
    with pytest.raises(WebBitemporalReadError, match="authentic-pilot-invented"):
        read_bitemporal_view(vault)


def test_project_mismatch_fail_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_catalog(vault, project_id="harbor-api", extra={"catalog_id": "foreign-project"})
    with pytest.raises(WebBitemporalReadError, match="project-mismatch"):
        read_bitemporal_view(vault)


def test_symlink_catalog_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    foreign = tmp_path / "foreign.json"
    foreign.write_text(json.dumps({"catalog_id": "harbor-api"}) + "\n", encoding="utf-8")
    directory = vault / "generated" / "ops" / "bitemporal"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "harbor-api-validity-catalog.json").symlink_to(foreign)
    with pytest.raises(WebBitemporalReadError, match="bitemporal-read-symlink-forbidden"):
        read_bitemporal_view(vault)


def test_module_does_not_write_or_materialize() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/web_api/bitemporal_read.py").read_text(encoding="utf-8")
    for name in (
        "from project_atlas.bitemporal",
        "from project_atlas.bitemporal_catalog",
        "write_validity_catalog(",
        "build_bitemporal_catalogs(",
        "write_project_validity_catalog(",
        "write_text(",
        "from project_atlas.ingestion",
        "from project_atlas.atlas3",
        "atlas.query.read",
    ):
        assert name not in source


def test_cli_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = _vault(tmp_path)
    assert main(["bitemporal-status", "--vault", str(vault)]) == EXIT_OK
    rendered = capsys.readouterr().out
    assert "EMPTY" in rendered
    assert all(ord(char) < 128 for char in rendered)
    _write_catalog(vault)
    assert main(["bitemporal-status", "--vault", str(vault), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PRESENT"


def test_cli_help_is_ascii(capsys: pytest.CaptureFixture[str]) -> None:
    try:
        code = main(["bitemporal-status", "--help"])
    except SystemExit as exc:
        code = int(exc.code or 0)
    assert code == 0
    help_text = capsys.readouterr().out
    assert "never writes" in help_text or "read-only" in help_text or "CATALOG" in help_text
    assert all(ord(char) < 128 for char in help_text)
    help_text.encode("cp1252")


def test_mcp_bitemporal_read_empty(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    report = invoke_mcp_tool(vault, "atlas.bitemporal.read")
    assert report["tool_id"] == "atlas.bitemporal.read"
    assert report["live_mcp_read"] is True
    payload = report["result"]
    assert payload["package_id"] == PACKAGE_ID
    assert payload["status"] == "EMPTY"
    assert payload["honesty"]["write_applied"] is False


def test_unknown_vault_is_operational_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    assert main(["bitemporal-status", "--vault", str(missing)]) == EXIT_ERROR
