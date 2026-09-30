"""AT3-072 — isolated provider-register / capabilities CLI design."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from project_atlas.atlas3.cli import dispatch_atlas3, register_atlas3_parsers
from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.provider_register import (
    ALLOWED_CLI,
    PACKAGE_ID,
    assert_cli_design,
    compile_provider_register,
)


def test_design_catalog_is_not_live_register() -> None:
    report = compile_provider_register()
    assert report["package_id"] == PACKAGE_ID
    assert report["design_only"] is True
    assert report["cli_proliferation"] is False
    assert report["live_provider_register"] is False
    assert report["live_full_history_sync"] is False
    assert report["register_is_authority"] is False
    assert report["merge_authorization"] == "NOT_GRANTED"
    assert report["write_applied"] is False
    assert set(report["allowed_cli"]) == set(ALLOWED_CLI)


def test_allowlist_accepts_existing_commands() -> None:
    report = assert_cli_design(["pulse", "capabilities", "provider-register"])
    assert report["accepted"] is True
    assert report["cli_proliferation"] is False


def test_cli_proliferation_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        assert_cli_design(["pulse", "mission-command-center"])
    assert exc.value.code == "CLI_PROLIFERATION"


def test_query_read_is_forbidden() -> None:
    with pytest.raises(Atlas3Error) as exc:
        assert_cli_design(["atlas.query.read"])
    assert exc.value.code == "FORBIDDEN_CLI"


def test_live_history_sync_is_forbidden() -> None:
    with pytest.raises(Atlas3Error) as exc:
        assert_cli_design(["chatgpt-sync-live"])
    assert exc.value.code == "FORBIDDEN_CLI"


def test_empty_proposal_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        assert_cli_design(["", "  "])
    assert exc.value.code == "CLI_DESIGN_REQUIRED"


def test_cli_catalog(capsys: pytest.CaptureFixture[str]) -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_atlas3_parsers(sub)
    args = parser.parse_args(["provider-register"])
    assert dispatch_atlas3(args) == 0
    rendered = capsys.readouterr().out
    payload = json.loads(rendered)
    assert payload["design_only"] is True
    assert payload["live_provider_register"] is False
    assert all(ord(char) < 128 for char in rendered)


def test_cli_help_is_ascii(capsys: pytest.CaptureFixture[str]) -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_atlas3_parsers(sub)
    with pytest.raises(SystemExit) as info:
        parser.parse_args(["provider-register", "--help"])
    assert info.value.code == 0
    help_text = capsys.readouterr().out
    assert "no CLI proliferation" in help_text
    assert all(ord(char) < 128 for char in help_text)


def test_module_does_not_write() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/provider_register.py").read_text(encoding="utf-8")
    for name in (
        "write_json_atomic",
        "write_text(",
        "chatgpt_bridge",
        "from project_atlas.ingestion",
        "from project_atlas.api_server",
    ):
        assert name not in source
