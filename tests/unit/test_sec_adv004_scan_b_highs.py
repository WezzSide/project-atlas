"""SEC-ADV004-B-001 / SEC-ADV004-B-002 remedi probes (ADVANCE-004-B SCAN-B)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from project_atlas.authz import (
    API_TOKEN_FILE_ENV,
    CLI_ELEVATE_CAPS_ENV,
    AuthzError,
    default_operator,
    mint_api_session,
    publish_api_session_credentials,
    require_cli_elevated_operator,
)
from project_atlas.cli import main


def test_cli_elevation_refused_without_env_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SEC-ADV004-B-001: no ATLAS_CLI_ELEVATE_CAPS → fail-closed."""
    monkeypatch.delenv(CLI_ELEVATE_CAPS_ENV, raising=False)
    with pytest.raises(AuthzError, match=r"authz-cli-elevation-required"):
        require_cli_elevated_operator(
            "cli-op",
            required={"scheduler.dispatch"},
        )
    assert not default_operator().allows("scheduler.dispatch")
    assert not default_operator().allows("autonomy.l3")


def test_cli_elevation_incomplete_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CLI_ELEVATE_CAPS_ENV, "scheduler.dispatch")
    with pytest.raises(AuthzError, match=r"authz-cli-elevation-incomplete"):
        require_cli_elevated_operator(
            "cli-op",
            required={"autonomy.l3", "scheduler.dispatch"},
        )


def test_cli_elevation_explicit_gate_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        CLI_ELEVATE_CAPS_ENV,
        "scheduler.dispatch,autonomy.l3",
    )
    op = require_cli_elevated_operator(
        "cli-op",
        required={"scheduler.dispatch"},
    )
    assert op.allows("scheduler.dispatch")
    op_l3 = require_cli_elevated_operator(
        "cli-l3",
        required={"autonomy.l3", "scheduler.dispatch"},
    )
    assert op_l3.allows("autonomy.l3")
    assert op_l3.allows("scheduler.dispatch")


def test_live_sched_dispatch_cli_no_self_grant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI must not mint elevated_operator without env gate."""
    monkeypatch.delenv(CLI_ELEVATE_CAPS_ENV, raising=False)
    vault = tmp_path / "v"
    vault.mkdir()
    # Message text is covered by require_cli_elevated_operator unit tests;
    # logging StreamHandler bypasses pytest capsys/capfd/caplog under
    # configure_logging(propagate=False). Exit code proves CLI fail-closed.
    code = main(
        [
            "live",
            "sched-dispatch",
            "--vault",
            str(vault),
            "--arm-id",
            "arm-1",
            "--job",
            "version",
        ]
    )
    assert code == 1


def test_publish_token_redacted_when_not_tty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """SEC-ADV004-B-002: redirected stderr must not receive full bearer."""
    monkeypatch.delenv(API_TOKEN_FILE_ENV, raising=False)
    store = mint_api_session()
    token = store.credentials.read_token
    publish_api_session_credentials(store.credentials, stderr_isatty=False)
    err = capsys.readouterr().err
    assert token not in err
    assert "ATLAS_API_READ_TOKEN=[redacted" in err


def test_publish_token_file_sink_redacts_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """SEC-ADV004-B-002: ATLAS_API_TOKEN_FILE captures; stderr is redacted."""
    token_path = tmp_path / "live-api.read.token"
    monkeypatch.setenv(API_TOKEN_FILE_ENV, str(token_path))
    store = mint_api_session()
    token = store.credentials.read_token
    publish_api_session_credentials(store.credentials, stderr_isatty=False)
    err = capsys.readouterr().err
    assert token not in err
    assert "ATLAS_API_READ_TOKEN=[redacted]" in err
    assert token_path.read_text(encoding="ascii").strip() == token


def test_publish_token_tty_prints_full(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv(API_TOKEN_FILE_ENV, raising=False)
    store = mint_api_session()
    token = store.credentials.read_token
    publish_api_session_credentials(store.credentials, stderr_isatty=True)
    err = capsys.readouterr().err
    assert f"ATLAS_API_READ_TOKEN={token}" in err


def test_cli_source_has_no_inline_self_grant() -> None:
    """Static guard: live sched-dispatch / l3-loop must not call elevated_operator."""
    cli_path = Path(__file__).resolve().parents[2] / "src" / "project_atlas" / "cli.py"
    text = cli_path.read_text(encoding="utf-8")
    assert "require_cli_elevated_operator" in text
    # Bare elevated_operator( self-grant must not remain (exclude require_cli_*).
    assert re.search(r"(?<!require_cli_)elevated_operator\(", text) is None
    assert "from project_atlas.authz import AuthzError, elevated_operator" not in text


def test_start_script_hardens_stderr_acl() -> None:
    root = Path(__file__).resolve().parents[2]
    start = (root / "scripts" / "windows" / "atlas-start.ps1").read_text(encoding="utf-8")
    common = (root / "scripts" / "windows" / "_AtlasCommon.ps1").read_text(encoding="utf-8")
    assert "Protect-AtlasSensitiveFile" in common
    assert "Clear-AtlasSecretFromLog" in common
    assert "ATLAS_API_TOKEN_FILE" in start
    assert "Protect-AtlasSensitiveFile -Path $apiProc.log_stderr" in start
    assert "Clear-AtlasSecretFromLog" in start
