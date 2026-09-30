"""AS-2.1-L3-JOB-MATRIX-ADV — adversarial L3 autonomy job-matrix (Track A).

Fail-closed coverage: scope expansion, arm overlap, destructive deny,
stale context, receipt mismatch, duplicate dispatch. L4/L5 stay disabled.
Does not unlock authentic PILOT or RELEASE CERTIFIED.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.authz import elevated_operator
from project_atlas.autonomy_l3 import (
    AutonomyL3Error,
    enable_bounded_l3,
    run_bounded_l3_loop,
)
from project_atlas.scheduler_live import arm_scheduler


def _op():
    return elevated_operator("l3-adv-op", extra={"autonomy.l3", "scheduler.dispatch"})


def _armed_policy(
    vault: Path,
    *,
    arm_id: str = "arm-adv",
    policy_id: str = "pol-adv",
    max_jobs: int = 3,
) -> None:
    arm_scheduler(vault, arm_id=arm_id)
    enable_bounded_l3(
        vault,
        policy_id=policy_id,
        arm_id=arm_id,
        operator=_op(),
        max_jobs=max_jobs,
    )


def _policy_path(vault: Path, policy_id: str = "pol-adv") -> Path:
    return vault / "generated" / "ops" / "autonomy" / f"{policy_id}-l3-policy.json"


def _rewrite_policy(vault: Path, **updates: object) -> None:
    path = _policy_path(vault)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(updates)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def test_adv_scope_expansion_tampered_max_jobs(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    _armed_policy(vault, max_jobs=2)
    _rewrite_policy(vault, max_jobs_per_arm=99)
    with pytest.raises(AutonomyL3Error, match="scope-expansion:max-jobs"):
        run_bounded_l3_loop(vault, policy_id="pol-adv", jobs=["version"], operator=_op())


def test_adv_scope_expansion_tampered_allowed_jobs(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    _armed_policy(vault)
    _rewrite_policy(
        vault,
        allowed_jobs=["validate", "build-indexes", "version", "ingest"],
    )
    with pytest.raises(AutonomyL3Error, match="scope-expansion:ingest"):
        run_bounded_l3_loop(vault, policy_id="pol-adv", jobs=["version"], operator=_op())


def test_adv_scope_expansion_l4_l5_forbidden(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    _armed_policy(vault)
    _rewrite_policy(
        vault,
        levels_enabled={
            "0": True,
            "1": True,
            "2": True,
            "3": True,
            "4": True,
            "5": False,
        },
    )
    with pytest.raises(AutonomyL3Error, match="l4-l5-forbidden"):
        run_bounded_l3_loop(vault, policy_id="pol-adv", jobs=["version"], operator=_op())
    _rewrite_policy(
        vault,
        level=5,
        levels_enabled={
            "0": True,
            "1": True,
            "2": True,
            "3": True,
            "4": False,
            "5": True,
        },
    )
    with pytest.raises(AutonomyL3Error, match="l4-l5-forbidden"):
        run_bounded_l3_loop(vault, policy_id="pol-adv", jobs=["version"], operator=_op())


def test_adv_arm_overlap_second_policy_denied(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    arm_scheduler(vault, arm_id="arm-shared")
    op = _op()
    enable_bounded_l3(vault, policy_id="pol-a", arm_id="arm-shared", operator=op)
    with pytest.raises(AutonomyL3Error, match="arm-overlap"):
        enable_bounded_l3(vault, policy_id="pol-b", arm_id="arm-shared", operator=op)


def test_adv_destructive_jobs_denied(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    _armed_policy(vault)
    for job in ("ingest", "discover", "init", "promote", "migrate", "sync"):
        with pytest.raises(AutonomyL3Error, match=f"job-forbidden:{job}"):
            run_bounded_l3_loop(vault, policy_id="pol-adv", jobs=[job], operator=_op())


def test_adv_stale_context_disarmed_arm(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    _armed_policy(vault, arm_id="arm-stale")
    arm_path = vault / "generated" / "ops" / "scheduler" / "arm-stale-arm.json"
    arm = json.loads(arm_path.read_text(encoding="utf-8"))
    arm["armed"] = False
    arm_path.write_text(
        json.dumps(arm, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(AutonomyL3Error, match="stale-context"):
        run_bounded_l3_loop(vault, policy_id="pol-adv", jobs=["version"], operator=_op())


def test_adv_receipt_mismatch_package_and_arm(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    _armed_policy(vault, arm_id="arm-rx")
    _rewrite_policy(vault, package_id="AS-EVIL-PACKAGE")
    with pytest.raises(AutonomyL3Error, match="receipt-mismatch:package"):
        run_bounded_l3_loop(vault, policy_id="pol-adv", jobs=["version"], operator=_op())
    _rewrite_policy(
        vault,
        package_id="AS-2.1-AUTONOMY-L3-001",
        arm_id="arm-rx",
    )
    arm_path = vault / "generated" / "ops" / "scheduler" / "arm-rx-arm.json"
    arm = json.loads(arm_path.read_text(encoding="utf-8"))
    arm["arm_id"] = "arm-other"
    arm_path.write_text(
        json.dumps(arm, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(AutonomyL3Error, match="receipt-mismatch:arm-receipt"):
        run_bounded_l3_loop(vault, policy_id="pol-adv", jobs=["version"], operator=_op())


def test_adv_duplicate_dispatch_denied(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    _armed_policy(vault)
    with pytest.raises(AutonomyL3Error, match="duplicate-dispatch:version"):
        run_bounded_l3_loop(
            vault,
            policy_id="pol-adv",
            jobs=["version", "version"],
            operator=_op(),
        )


def test_adv_happy_path_keeps_l4_l5_disabled(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    _armed_policy(vault)
    report = run_bounded_l3_loop(vault, policy_id="pol-adv", jobs=["version"], operator=_op())
    assert report["promoted"] is False
    assert report["vault_write_enabled"] is False
    assert report["levels_enabled"]["4"] is False
    assert report["levels_enabled"]["5"] is False
    policy = json.loads(_policy_path(vault).read_text(encoding="utf-8"))
    assert policy["levels_enabled"]["4"] is False
    assert policy["levels_enabled"]["5"] is False
