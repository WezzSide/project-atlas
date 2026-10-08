"""AS-SEC-SCAN-WEBACT-JSON-001 — scan decoded JSON payload before ledger persist."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.authz import elevated_operator
from project_atlas.scaffold import create_scaffold
from project_atlas.secrets import scan_text
from project_atlas.web_actions import (
    WebActionError,
    _ledger_path,
    submit_web_action,
)

TOKEN = "bearer " + ("A" * 32)


def test_unicode_escape_payload_is_not_persisted(tmp_path: Path) -> None:
    raw = '{"q":"\\u0062earer ' + ("A" * 32) + '"}'
    assert scan_text(raw) == []
    body = json.loads(raw)
    assert body["q"] == TOKEN
    vault = tmp_path / "vault"
    create_scaffold(vault)
    with pytest.raises(WebActionError, match="web-action-secret-findings"):
        submit_web_action(
            vault,
            action_id="act-hunt",
            action_type="ask-query",
            payload=body,
            operator=elevated_operator("op", extra={"web.action"}),
        )
    assert not _ledger_path(vault).exists()


def test_nested_decoded_secret_is_not_persisted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    create_scaffold(vault)
    with pytest.raises(WebActionError, match="web-action-secret-findings"):
        submit_web_action(
            vault,
            action_id="act-nest",
            action_type="ask-query",
            payload={"items": [{"q": TOKEN}]},
            operator=elevated_operator("op", extra={"web.action"}),
        )
    assert not _ledger_path(vault).exists()


def test_json_unicode_escape_existing_ledger_row_is_not_rewritten(
    tmp_path: Path,
) -> None:
    """AS-SEC-SCAN-WEB-ACTIONS-LEDGER-JSON-ESC-001: decoded ledger payload rewrite."""
    token = "AKIAAAAAAAAAAAAAAAAA"
    vault = tmp_path / "vault"
    create_scaffold(vault)
    led = vault / "generated" / "ops" / "web-actions"
    led.mkdir(parents=True)
    raw = (
        '{"schema_version":1,"package_id":"AS-2.1-WEB-ACTIONS-001",'
        '"transactions":[{"action_id":"act-old","action_type":"ask-query",'
        '"payload":{"q":"\\u0041KIAAAAAAAAAAAAAAAAA"},"operator_id":"op",'
        '"canonical_write":false,"authority":false}],'
        '"truth_boundary":"x","generated":{"by":"project-atlas"}}'
    )
    (led / "action-ledger.json").write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    submit_web_action(
        vault,
        action_id="act-new",
        action_type="refresh-status",
        payload={"ok": True},
        operator=elevated_operator("op", extra={"web.action"}),
    )
    text = (led / "action-ledger.json").read_text(encoding="utf-8")
    assert token not in text
    assert scan_text(text) == []


def test_json_unicode_escape_existing_operator_id_is_not_rewritten(
    tmp_path: Path,
) -> None:
    """Existing ledger operator_id \\u decode must not rewrite plaintext."""
    token = "AKIAAAAAAAAAAAAAAAAA"
    vault = tmp_path / "vault"
    create_scaffold(vault)
    led = vault / "generated" / "ops" / "web-actions"
    led.mkdir(parents=True)
    raw = (
        '{"schema_version":1,"package_id":"AS-2.1-WEB-ACTIONS-001",'
        '"transactions":[{"action_id":"act-old","action_type":"ask-query",'
        '"payload":{"ok":true},"operator_id":"\\u0041KIAAAAAAAAAAAAAAAAA",'
        '"canonical_write":false,"authority":false}],'
        '"truth_boundary":"x","generated":{"by":"project-atlas"}}'
    )
    (led / "action-ledger.json").write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    submit_web_action(
        vault,
        action_id="act-new",
        action_type="refresh-status",
        payload={"ok": True},
        operator=elevated_operator("op", extra={"web.action"}),
    )
    text = (led / "action-ledger.json").read_text(encoding="utf-8")
    assert token not in text
    assert scan_text(text) == []


def test_json_unicode_escape_existing_action_type_is_not_rewritten(
    tmp_path: Path,
) -> None:
    """Existing ledger action_type \\u decode must not rewrite plaintext."""
    token = "AKIAAAAAAAAAAAAAAAAA"
    vault = tmp_path / "vault"
    create_scaffold(vault)
    led = vault / "generated" / "ops" / "web-actions"
    led.mkdir(parents=True)
    raw = (
        '{"schema_version":1,"package_id":"AS-2.1-WEB-ACTIONS-001",'
        '"transactions":[{"action_id":"act-old","action_type":'
        '"\\u0041KIAAAAAAAAAAAAAAAAA","payload":{"ok":true},'
        '"operator_id":"op","canonical_write":false,"authority":false}],'
        '"truth_boundary":"x","generated":{"by":"project-atlas"}}'
    )
    (led / "action-ledger.json").write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    submit_web_action(
        vault,
        action_id="act-new",
        action_type="refresh-status",
        payload={"ok": True},
        operator=elevated_operator("op", extra={"web.action"}),
    )
    text = (led / "action-ledger.json").read_text(encoding="utf-8")
    assert token not in text
    assert scan_text(text) == []
