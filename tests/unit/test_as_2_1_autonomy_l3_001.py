"""AS-2.1-AUTONOMY-L3-001 tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.authz import AuthzError, elevated_operator
from project_atlas.autonomy_l3 import AutonomyL3Error, disable_bounded_l3, enable_bounded_l3
from project_atlas.scheduler_live import arm_scheduler
from project_atlas.secrets import scan_text


def test_l3_requires_capability(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    arm_scheduler(vault, arm_id="arm-l3")
    with pytest.raises(AuthzError):
        enable_bounded_l3(vault, policy_id="pol-a", arm_id="arm-l3")


def test_l3_enable_bounded(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    arm_scheduler(vault, arm_id="arm-l3")
    op = elevated_operator("l3-op", extra={"autonomy.l3"})
    report = enable_bounded_l3(
        vault, policy_id="pol-a", arm_id="arm-l3", operator=op
    )
    assert report["l3_bounded_autonomy"] is True
    assert report["levels_enabled"]["3"] is True
    assert report["levels_enabled"]["4"] is False
    assert report["vault_write_enabled"] is False


def test_l3_requires_arm(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    op = elevated_operator("l3-op", extra={"autonomy.l3"})
    with pytest.raises(AutonomyL3Error, match="scheduler-not-armed"):
        enable_bounded_l3(vault, policy_id="pol-a", arm_id="missing", operator=op)


def test_json_unicode_escape_arm_id_is_not_persisted(tmp_path: Path) -> None:
    """AS-SEC-SCAN-AUTONOMY-L3-ARM-JSON-ESC-001: decoded arm_id must not persist."""
    token = "AKIAAAAAAAAAAAAAAAAA"
    vault = tmp_path / "v"
    d = vault / "generated" / "ops" / "autonomy"
    d.mkdir(parents=True)
    raw = (
        '{"schema_version":1,"package_id":"AS-2.1-AUTONOMY-L3-001",'
        '"enabled":true,"l3_bounded_autonomy":true,'
        '"arm_id":"\\u0041KIAAAAAAAAAAAAAAAAA","policy_id":"pol-ok"}'
    )
    (d / "pol-ok-l3-policy.json").write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    op = elevated_operator("hunter", extra={"autonomy.l3"})
    rec = disable_bounded_l3(vault, policy_id="pol-ok", operator=op)
    written = (d / "pol-ok-l3-disabled.json").read_text(encoding="utf-8")
    policy_text = (d / "pol-ok-l3-policy.json").read_text(encoding="utf-8")
    assert rec.get("arm_id") != token
    assert token not in written
    assert token not in policy_text
    assert scan_text(written) == []
    assert scan_text(policy_text) == []


def test_json_unicode_escape_extra_policy_key_is_not_persisted(
    tmp_path: Path,
) -> None:
    """AS-SEC-SCAN-AUTONOMY-L3-POLICY-REWRITE-JSON-ESC-001.

    disable_bounded_l3 rewrites the loaded policy. A JSON-escaped extra
    key that scan_text misses on raw bytes must not persist decoded.
    """
    token = "AKIAAAAAAAAAAAAAAAAA"
    vault = tmp_path / "v"
    d = vault / "generated" / "ops" / "autonomy"
    d.mkdir(parents=True)
    raw = (
        '{"schema_version":1,"package_id":"AS-2.1-AUTONOMY-L3-001",'
        '"enabled":true,"l3_bounded_autonomy":true,'
        '"arm_id":"arm-l3","policy_id":"pol-ok",'
        '"\\u0041KIAAAAAAAAAAAAAAAAA":"kept"}'
    )
    (d / "pol-ok-l3-policy.json").write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    op = elevated_operator("hunter", extra={"autonomy.l3"})
    disable_bounded_l3(vault, policy_id="pol-ok", operator=op)
    written = (d / "pol-ok-l3-disabled.json").read_text(encoding="utf-8")
    policy_text = (d / "pol-ok-l3-policy.json").read_text(encoding="utf-8")
    assert token not in written
    assert token not in policy_text
    assert scan_text(written) == []
    assert scan_text(policy_text) == []
