"""AS-CODER-ALPHA-ARCHITECTURE-READ-001 — vault-scoped Architecture REPORT READ."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.cli import EXIT_ERROR, EXIT_OK, main
from project_atlas.mcp_server import invoke_mcp_tool
from project_atlas.web_api.architecture_read import (
    PACKAGE_ID,
    WebArchitectureReadError,
    read_architecture_view,
    render_architecture_status_text,
)


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


def _write_architecture_answer(
    vault: Path,
    *,
    project_id: str = "harbor-api",
    summary: str = "local-first compiler",
    extra: dict[str, object] | None = None,
) -> None:
    directory = vault / "generated" / "answers"
    directory.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "schema": "atlas.coder-alpha.architecture-lens.v1",
        "project_id": project_id,
        "summary": summary,
        "status": "derived",
        "value": summary,
        "MERGE_AUTHORIZATION": "NOT_GRANTED",
        "honesty": {
            "lens_is_authority": False,
            "architecture_is_authority": False,
            "readme_is_architecture_authority": False,
        },
    }
    if extra:
        payload.update(extra)
    (directory / f"ans-architecture-{project_id}.json").write_text(
        json.dumps(payload) + "\n",
        encoding="utf-8",
    )


def test_empty_vault_is_empty_not_healthy(tmp_path: Path) -> None:
    report = read_architecture_view(_vault(tmp_path))
    assert report["package_id"] == PACKAGE_ID
    assert report["status"] == "EMPTY"
    assert report["available"] is False
    assert report["honesty"]["write_applied"] is False
    assert report["honesty"]["materialize_invoked"] is False
    assert report["honesty"]["architecture_is_authority"] is False
    assert report["honesty"]["readme_is_architecture_authority"] is False
    assert report["honesty"]["purpose_echo_is_architecture"] is False
    assert report["honesty"]["MERGE_AUTHORIZATION"] == "NOT_GRANTED"
    text = render_architecture_status_text(report)
    assert "EMPTY != HEALTHY" in text
    assert "ARCHITECTURE != AUTHORITY" in text
    assert report["honesty"]["purpose_echo_is_architecture"] is False
    assert all(ord(char) < 128 for char in text)


def test_present_answer_is_not_authority(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_architecture_answer(vault, summary="postgres 15 vs 16")
    report = read_architecture_view(vault)
    assert report["status"] == "PRESENT"
    assert report["available"] is True
    assert report["view"]["projects"][0]["record"]["summary"] == "postgres 15 vs 16"
    assert report["honesty"]["architecture_is_authority"] is False
    assert report["honesty"]["readme_is_architecture_authority"] is False
    text = render_architecture_status_text(report)
    assert "postgres 15 vs 16" in text
    assert "README != ARCHITECTURE AUTHORITY" in text


def test_malformed_is_unknown_not_healthy(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    path = vault / "generated" / "answers" / "ans-architecture-harbor-api.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{nope", encoding="utf-8")
    report = read_architecture_view(vault)
    assert report["status"] == "UNKNOWN"
    assert report["view"]["malformed_count"] == 1
    assert report["available"] is False


def test_mixed_valid_and_corrupt_is_unknown(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_architecture_answer(vault, project_id="harbor-api")
    bad = vault / "generated" / "answers" / "ans-architecture-decoy.json"
    bad.write_text("{nope", encoding="utf-8")
    report = read_architecture_view(vault)
    assert report["status"] == "UNKNOWN"
    assert report["view"]["artifact_count"] == 2
    assert report["view"]["malformed_count"] == 1


def test_merge_and_pilot_invention_fail_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_architecture_answer(vault, extra={"MERGE_AUTHORIZATION": "GRANTED"})
    with pytest.raises(WebArchitectureReadError, match="merge-authority-invented"):
        read_architecture_view(vault)
    _write_architecture_answer(vault, extra={"authentic_pilot": True})
    with pytest.raises(WebArchitectureReadError, match="authentic-pilot-invented"):
        read_architecture_view(vault)


def test_authority_invention_fail_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_architecture_answer(vault, extra={"architecture_is_authority": True})
    with pytest.raises(WebArchitectureReadError, match="authority-invented"):
        read_architecture_view(vault)


def test_readme_is_architecture_authority_invention_fail_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_architecture_answer(vault, extra={"readme_is_architecture_authority": True})
    with pytest.raises(WebArchitectureReadError, match="authority-invented"):
        read_architecture_view(vault)


def test_purpose_echo_is_architecture_invention_fail_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_architecture_answer(vault, extra={"purpose_echo_is_architecture": True})
    with pytest.raises(WebArchitectureReadError, match="authority-invented"):
        read_architecture_view(vault)


def test_project_mismatch_fail_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_architecture_answer(
        vault,
        project_id="harbor-api",
        extra={"project_id": "foreign-project"},
    )
    with pytest.raises(WebArchitectureReadError, match="project-mismatch"):
        read_architecture_view(vault)


def test_render_is_ascii_even_with_unicode_summary(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_architecture_answer(vault, summary="visión")
    text = render_architecture_status_text(read_architecture_view(vault))
    assert all(ord(char) < 128 for char in text)
    assert "visi?n" in text


def test_symlink_answer_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    foreign = tmp_path / "foreign.json"
    foreign.write_text(json.dumps({"summary": "leak"}) + "\n", encoding="utf-8")
    directory = vault / "generated" / "answers"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "ans-architecture-harbor-api.json").symlink_to(foreign)
    with pytest.raises(WebArchitectureReadError, match="architecture-read-symlink-forbidden"):
        read_architecture_view(vault)


def test_module_does_not_write_or_materialize() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/web_api/architecture_read.py").read_text(encoding="utf-8")
    for name in (
        "from project_atlas.project_architecture",
        "materialize_architecture_lenses(",
        "build_architecture_lens(",
        "write_text(",
        "write_json_atomic",
        "from project_atlas.ingestion",
        "from project_atlas.atlas3",
        "atlas.query.read",
    ):
        assert name not in source


def test_cli_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = _vault(tmp_path)
    assert main(["architecture-status", "--vault", str(vault)]) == EXIT_OK
    rendered = capsys.readouterr().out
    assert "EMPTY" in rendered
    assert all(ord(char) < 128 for char in rendered)
    _write_architecture_answer(vault)
    assert main(["architecture-status", "--vault", str(vault), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PRESENT"


def test_cli_help_is_ascii(capsys: pytest.CaptureFixture[str]) -> None:
    try:
        code = main(["architecture-status", "--help"])
    except SystemExit as exc:
        code = int(exc.code or 0)
    assert code == 0
    help_text = capsys.readouterr().out
    lowered = help_text.lower()
    assert "never writes" in lowered or "read-only" in lowered or "ARCHITECTURE" in help_text
    assert all(ord(char) < 128 for char in help_text)
    help_text.encode("cp1252")


def test_mcp_architecture_read_empty(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    report = invoke_mcp_tool(vault, "atlas.architecture.read")
    assert report["tool_id"] == "atlas.architecture.read"
    assert report["live_mcp_read"] is True
    payload = report["result"]
    assert payload["package_id"] == PACKAGE_ID
    assert payload["status"] == "EMPTY"
    assert payload["honesty"]["write_applied"] is False
    assert payload["honesty"]["readme_is_architecture_authority"] is False


def test_unknown_vault_is_operational_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    assert main(["architecture-status", "--vault", str(missing)]) == EXIT_ERROR
