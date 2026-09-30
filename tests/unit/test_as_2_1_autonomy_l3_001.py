"""AS-2.1-AUTONOMY-L3-001 tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.authz import AuthzError, elevated_operator
from project_atlas.autonomy_l3 import AutonomyL3Error, enable_bounded_l3
from project_atlas.scheduler_live import arm_scheduler


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
    report = enable_bounded_l3(vault, policy_id="pol-a", arm_id="arm-l3", operator=op)
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
