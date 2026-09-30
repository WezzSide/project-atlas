"""AS-CODER-ALPHA-INDEX-STATUS-001 — vault-scoped lexical index REPORT READ."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.cli import EXIT_ERROR, EXIT_OK, main
from project_atlas.mcp_server import invoke_mcp_tool
from project_atlas.web_api.index_status import (
    PACKAGE_ID,
    WebIndexStatusError,
    read_index_status,
    render_index_status_text,
)


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


def _write_index_artifact(
    vault: Path,
    *,
    name: str = "claims.json",
    extra: dict[str, object] | None = None,
) -> None:
    directory = vault / "generated" / "indexes"
    directory.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "schema_version": 1,
        "ids": ["claim-1"],
        "by_claim_id": {"claim-1": ["claim-1"]},
        "MERGE_AUTHORIZATION": "NOT_GRANTED",
        "honesty": {
            "index_status_is_authority": False,
            "presence_is_validate": False,
        },
    }
    if extra:
        payload.update(extra)
    (directory / name).write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_empty_vault_is_empty_not_healthy(tmp_path: Path) -> None:
    report = read_index_status(_vault(tmp_path))
    assert report["package_id"] == PACKAGE_ID
    assert report["status"] == "EMPTY"
    assert report["available"] is False
    assert report["honesty"]["write_applied"] is False
    assert report["honesty"]["materialize_invoked"] is False
    assert report["honesty"]["build_indexes_invoked"] is False
    assert report["honesty"]["MERGE_AUTHORIZATION"] == "NOT_GRANTED"
    assert report["honesty"]["legacy_indexes_authoritative"] is False
    text = render_index_status_text(report)
    assert "EMPTY != HEALTHY" in text
    assert all(ord(char) < 128 for char in text)


def test_present_artifact_is_not_authority(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_index_artifact(vault, extra={"summary": "12 claims"})
    report = read_index_status(vault)
    assert report["status"] == "PRESENT"
    assert report["available"] is True
    assert report["view"]["artifact_count"] == 1
    assert report["view"]["artifacts"][0]["record"]["summary"] == "12 claims"
    assert report["honesty"]["index_status_is_authority"] is False
    assert report["honesty"]["presence_is_validate"] is False
    assert report["honesty"]["presence_is_fresh"] is False
    text = render_index_status_text(report)
    assert "12 claims" in text


def test_malformed_is_unknown_not_healthy(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    path = vault / "generated" / "indexes" / "claims.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{nope", encoding="utf-8")
    report = read_index_status(vault)
    assert report["status"] == "UNKNOWN"
    assert report["view"]["malformed_count"] == 1
    assert report["available"] is False


def test_mixed_valid_and_corrupt_is_unknown(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_index_artifact(vault, name="claims.json")
    bad = vault / "generated" / "indexes" / "sources.json"
    bad.write_text("{nope", encoding="utf-8")
    report = read_index_status(vault)
    assert report["status"] == "UNKNOWN"
    assert report["view"]["artifact_count"] == 2
    assert report["view"]["malformed_count"] == 1
    assert report["available"] is False


def test_legacy_indexes_are_not_consumed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    legacy = vault / "indexes"
    legacy.mkdir()
    (legacy / "claims.json").write_text(json.dumps({"ids": ["leaked"]}) + "\n", encoding="utf-8")
    report = read_index_status(vault)
    assert report["status"] == "EMPTY"
    assert report["view"]["legacy_indexes_present"] is True
    assert report["view"]["legacy_indexes_consumed"] is False
    assert report["honesty"]["legacy_indexes_authoritative"] is False
    assert report["view"]["artifact_count"] == 0


def test_merge_and_pilot_invention_fail_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_index_artifact(vault, extra={"MERGE_AUTHORIZATION": "GRANTED"})
    with pytest.raises(WebIndexStatusError, match="merge-authority-invented"):
        read_index_status(vault)
    _write_index_artifact(vault, extra={"authentic_pilot": True})
    with pytest.raises(WebIndexStatusError, match="authentic-pilot-invented"):
        read_index_status(vault)


def test_authority_invention_fail_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_index_artifact(vault, extra={"index_status_is_authority": True})
    with pytest.raises(WebIndexStatusError, match="authority-invented"):
        read_index_status(vault)


def test_render_is_ascii_even_with_unicode_summary(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_index_artifact(vault, extra={"summary": "índice léxico"})
    text = render_index_status_text(read_index_status(vault))
    assert all(ord(char) < 128 for char in text)
    assert "?ndice l?xico" in text


def test_symlink_artifact_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    foreign = tmp_path / "foreign.json"
    foreign.write_text(json.dumps({"summary": "leak"}) + "\n", encoding="utf-8")
    directory = vault / "generated" / "indexes"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "claims.json").symlink_to(foreign)
    with pytest.raises(WebIndexStatusError, match="index-status-symlink-forbidden"):
        read_index_status(vault)


def test_module_does_not_write_or_materialize() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/web_api/index_status.py").read_text(encoding="utf-8")
    for name in (
        "from project_atlas.indexes",
        "build_indexes(",
        "canonical_index_payloads(",
        "write_text(",
        "write_json_atomic",
        "from project_atlas.ingestion",
        "from project_atlas.atlas3",
        "atlas.query.read",
    ):
        assert name not in source


def test_cli_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = _vault(tmp_path)
    assert main(["index-status", "--vault", str(vault)]) == EXIT_OK
    rendered = capsys.readouterr().out
    assert "EMPTY" in rendered
    assert all(ord(char) < 128 for char in rendered)
    _write_index_artifact(vault)
    assert main(["index-status", "--vault", str(vault), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PRESENT"


def test_cli_help_is_ascii(capsys: pytest.CaptureFixture[str]) -> None:
    try:
        code = main(["index-status", "--help"])
    except SystemExit as exc:
        code = int(exc.code or 0)
    assert code == 0
    help_text = capsys.readouterr().out
    assert "never writes" in help_text or "read-only" in help_text or "INDEX_STATUS" in help_text
    assert all(ord(char) < 128 for char in help_text)
    help_text.encode("cp1252")


def test_mcp_index_status_empty(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    report = invoke_mcp_tool(vault, "atlas.indexes.read")
    assert report["tool_id"] == "atlas.indexes.read"
    assert report["live_mcp_read"] is True
    payload = report["result"]
    assert payload["package_id"] == PACKAGE_ID
    assert payload["status"] == "EMPTY"
    assert payload["honesty"]["write_applied"] is False
    assert payload["honesty"]["presence_is_validate"] is False


def test_unknown_vault_is_operational_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    assert main(["index-status", "--vault", str(missing)]) == EXIT_ERROR
