"""AS-CODER-ALPHA-DECISIONS-READ-001 — vault-scoped Decisions REPORT READ."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.cli import EXIT_ERROR, EXIT_OK, main
from project_atlas.mcp_server import invoke_mcp_tool
from project_atlas.web_api.decisions_read import (
    PACKAGE_ID,
    WebDecisionsReadError,
    read_decisions_view,
    render_decisions_status_text,
)


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


def _write_decisions_answer(
    vault: Path,
    *,
    project_id: str = "harbor-api",
    summary: str = "use PostgreSQL 16",
    extra: dict[str, object] | None = None,
) -> None:
    directory = vault / "generated" / "answers"
    directory.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "schema": "atlas.coder-alpha.decisions-lens.v1",
        "project_id": project_id,
        "summary": summary,
        "status": "derived",
        "value": summary,
        "MERGE_AUTHORIZATION": "NOT_GRANTED",
        "honesty": {
            "lens_is_authority": False,
            "decisions_is_authority": False,
            "model_is_owner_decision": False,
        },
    }
    if extra:
        payload.update(extra)
    (directory / f"ans-decisions-{project_id}.json").write_text(
        json.dumps(payload) + "\n",
        encoding="utf-8",
    )


def test_empty_vault_is_empty_not_healthy(tmp_path: Path) -> None:
    report = read_decisions_view(_vault(tmp_path))
    assert report["package_id"] == PACKAGE_ID
    assert report["status"] == "EMPTY"
    assert report["available"] is False
    assert report["honesty"]["write_applied"] is False
    assert report["honesty"]["materialize_invoked"] is False
    assert report["honesty"]["decisions_is_authority"] is False
    assert report["honesty"]["model_is_owner_decision"] is False
    assert report["honesty"]["MERGE_AUTHORIZATION"] == "NOT_GRANTED"
    text = render_decisions_status_text(report)
    assert "EMPTY != HEALTHY" in text
    assert "DECISIONS != AUTHORITY" in text
    assert all(ord(char) < 128 for char in text)


def test_present_answer_is_not_authority(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_decisions_answer(vault, summary="harbor datastore")
    report = read_decisions_view(vault)
    assert report["status"] == "PRESENT"
    assert report["available"] is True
    assert report["view"]["projects"][0]["record"]["summary"] == "harbor datastore"
    assert report["honesty"]["decisions_is_authority"] is False
    assert report["honesty"]["model_is_owner_decision"] is False
    text = render_decisions_status_text(report)
    assert "harbor datastore" in text
    assert "MODEL PARAPHRASE != OWNER DECISION" in text


def test_malformed_is_unknown_not_healthy(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    path = vault / "generated" / "answers" / "ans-decisions-harbor-api.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{nope", encoding="utf-8")
    report = read_decisions_view(vault)
    assert report["status"] == "UNKNOWN"
    assert report["view"]["malformed_count"] == 1
    assert report["available"] is False


def test_mixed_valid_and_corrupt_is_unknown(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_decisions_answer(vault, project_id="harbor-api")
    bad = vault / "generated" / "answers" / "ans-decisions-decoy.json"
    bad.write_text("{nope", encoding="utf-8")
    report = read_decisions_view(vault)
    assert report["status"] == "UNKNOWN"
    assert report["view"]["artifact_count"] == 2
    assert report["view"]["malformed_count"] == 1


def test_merge_and_pilot_invention_fail_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_decisions_answer(vault, extra={"MERGE_AUTHORIZATION": "GRANTED"})
    with pytest.raises(WebDecisionsReadError, match="merge-authority-invented"):
        read_decisions_view(vault)
    _write_decisions_answer(vault, extra={"authentic_pilot": True})
    with pytest.raises(WebDecisionsReadError, match="authentic-pilot-invented"):
        read_decisions_view(vault)


def test_authority_invention_fail_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_decisions_answer(vault, extra={"decisions_is_authority": True})
    with pytest.raises(WebDecisionsReadError, match="authority-invented"):
        read_decisions_view(vault)


def test_model_is_owner_decision_invention_fail_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_decisions_answer(vault, extra={"model_is_owner_decision": True})
    with pytest.raises(WebDecisionsReadError, match="authority-invented"):
        read_decisions_view(vault)


def test_project_mismatch_fail_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_decisions_answer(
        vault,
        project_id="harbor-api",
        extra={"project_id": "foreign-project"},
    )
    with pytest.raises(WebDecisionsReadError, match="project-mismatch"):
        read_decisions_view(vault)


def test_render_is_ascii_even_with_unicode_summary(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_decisions_answer(vault, summary="visión")
    text = render_decisions_status_text(read_decisions_view(vault))
    assert all(ord(char) < 128 for char in text)
    assert "visi?n" in text


def test_symlink_answer_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    foreign = tmp_path / "foreign.json"
    foreign.write_text(json.dumps({"summary": "leak"}) + "\n", encoding="utf-8")
    directory = vault / "generated" / "answers"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "ans-decisions-harbor-api.json").symlink_to(foreign)
    with pytest.raises(WebDecisionsReadError, match="decisions-read-symlink-forbidden"):
        read_decisions_view(vault)


def test_module_does_not_write_or_materialize() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/web_api/decisions_read.py").read_text(encoding="utf-8")
    for name in (
        "from project_atlas.coder_alpha",
        "materialize_decisions_lenses(",
        "build_decisions_lens(",
        "write_text(",
        "write_json_atomic",
        "from project_atlas.ingestion",
        "from project_atlas.atlas3",
        "atlas.query.read",
    ):
        assert name not in source


def test_cli_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = _vault(tmp_path)
    assert main(["decisions-status", "--vault", str(vault)]) == EXIT_OK
    rendered = capsys.readouterr().out
    assert "EMPTY" in rendered
    assert all(ord(char) < 128 for char in rendered)
    _write_decisions_answer(vault)
    assert main(["decisions-status", "--vault", str(vault), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PRESENT"


def test_cli_help_is_ascii(capsys: pytest.CaptureFixture[str]) -> None:
    try:
        code = main(["decisions-status", "--help"])
    except SystemExit as exc:
        code = int(exc.code or 0)
    assert code == 0
    help_text = capsys.readouterr().out
    assert "never writes" in help_text or "read-only" in help_text or "DECISIONS" in help_text
    assert all(ord(char) < 128 for char in help_text)
    help_text.encode("cp1252")


def test_mcp_decisions_read_empty(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    report = invoke_mcp_tool(vault, "atlas.decisions.read")
    assert report["tool_id"] == "atlas.decisions.read"
    assert report["live_mcp_read"] is True
    payload = report["result"]
    assert payload["package_id"] == PACKAGE_ID
    assert payload["status"] == "EMPTY"
    assert payload["honesty"]["write_applied"] is False
    assert payload["honesty"]["model_is_owner_decision"] is False


def test_unknown_vault_is_operational_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    assert main(["decisions-status", "--vault", str(missing)]) == EXIT_ERROR
