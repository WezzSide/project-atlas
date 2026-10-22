"""AS-L3-ARM-ID-PATH-001 — confine enable_bounded_l3 arm_id.

``enable_bounded_l3`` interpolated unsanitized ``arm_id`` into
``generated/ops/scheduler/{arm_id}-arm.json``. ``policy_id`` was already
``_ID_RE``-gated; ``run_bounded_l3_loop`` and ``scheduler_live`` already
reject unsafe arm tokens. Enable forgot the same guard.

With ``generated/ops/scheduler`` present (normal after any arm),
``arm_id=../../../../outside-l3/forged`` reads a forged arm JSON *outside
the vault* and persists ``enabled=True`` / ``l3_bounded_autonomy=True``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.authz import OperatorProfile
from project_atlas.autonomy_l3 import AutonomyL3Error, enable_bounded_l3
from project_atlas.scheduler_live import arm_scheduler

_TRAVERSAL = [
    "../../../../outside-l3/forged",
    "..",
    "a/b",
    "Upper",
    "",
    "  ",
    "x" * 65,
]


@pytest.mark.parametrize("bad", _TRAVERSAL)
def test_enable_l3_rejects_unsafe_arm_id(tmp_path: Path, bad: str) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "generated" / "ops" / "scheduler").mkdir(parents=True)
    outside = tmp_path / "outside-l3"
    outside.mkdir()
    (outside / "forged-arm.json").write_text(
        json.dumps({"arm_id": bad, "armed": True}),
        encoding="utf-8",
    )
    op = OperatorProfile("l3", frozenset({"autonomy.l3"}))
    with pytest.raises(AutonomyL3Error, match="autonomy-l3-arm-id-invalid"):
        enable_bounded_l3(vault, policy_id="trav-policy", arm_id=bad, operator=op)
    assert not (vault / "generated" / "ops" / "autonomy").exists()


def test_enable_l3_still_accepts_safe_arm_id(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    arm_scheduler(vault, arm_id="arm-l3")
    op = OperatorProfile("l3", frozenset({"autonomy.l3"}))
    report = enable_bounded_l3(
        vault, policy_id="pol-a", arm_id="arm-l3", operator=op
    )
    assert report["enabled"] is True
    assert report["arm_id"] == "arm-l3"
    assert report["l3_bounded_autonomy"] is True
    assert report["levels_enabled"]["4"] is False
