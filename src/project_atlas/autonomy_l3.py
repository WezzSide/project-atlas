"""AS-2.1-AUTONOMY-L3-001 - bounded L3 autonomy enablement.

Enables autonomy level 3 only when operator has ``autonomy.l3`` and an
active supervised scheduler arm exists. Never enables L4/L5. Never
promotes Layer B authority. Hardened: job timeout bound + disable receipt
+ AS-2.1-L3-JOB-MATRIX-ADV fail-closed guards (scope/overlap/stale/receipt/
duplicate).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from project_atlas.authz import OperatorProfile, default_operator
from project_atlas.compat_anchor import SNAPSHOT_ID, require_compatibility_anchor
from project_atlas.scheduler_live import dispatch_supervised_job
from project_atlas.secrets import scan_text

PACKAGE_ID = "AS-2.1-AUTONOMY-L3-001"
TRUTH_BOUNDARY = (
    "L3_BOUNDED_AUTONOMY != L4/L5 / != UNSUPERVISED PROMOTE / != AUTHORITY"
)
_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
ALLOWED_L3_JOBS: frozenset[str] = frozenset(
    {"validate", "build-indexes", "version"}
)
DESTRUCTIVE_DENIED_JOBS: frozenset[str] = frozenset(
    {
        "ingest",
        "discover",
        "init",
        "promote",
        "migrate",
        "sync",
        "delete",
        "quarantine",
    }
)


class AutonomyL3Error(ValueError):
    """Fail-closed L3 autonomy error."""


def _omit_decoded_secrets(value: Any) -> Any:
    """Omit decoded secret keys/values after ``json.loads``.

    AS-SEC-SCAN-AUTONOMY-L3-POLICY-REWRITE-JSON-ESC-001: disable rewrites
    the loaded policy object. ``scan_text`` on raw ``\\u0041KI…`` escapes
    is empty; persist must not echo the decoded TOKEN as a key or value.
    """
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and scan_text(key):
                continue
            cleaned[key] = _omit_decoded_secrets(item)
        return cleaned
    if isinstance(value, list):
        return [_omit_decoded_secrets(item) for item in value]
    if isinstance(value, str) and scan_text(value):
        return "unknown"
    return value


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp.replace(path)


def _policy_dir(vault: Path) -> Path:
    return vault / "generated" / "ops" / "autonomy"


def _reject_arm_overlap(vault: Path, *, arm_id: str, policy_id: str) -> None:
    """Fail closed when another enabled L3 policy already binds the arm."""
    root = _policy_dir(vault)
    if not root.is_dir():
        return
    for path in sorted(root.glob("*-l3-policy.json")):
        other = json.loads(path.read_text(encoding="utf-8"))
        if other.get("policy_id") == policy_id:
            continue
        if (
            other.get("enabled") is True
            and other.get("l3_bounded_autonomy") is True
            and str(other.get("arm_id") or "") == arm_id
        ):
            raise AutonomyL3Error(f"autonomy-l3-arm-overlap:{arm_id}")


def _assert_l4_l5_disabled(policy: dict[str, Any]) -> None:
    levels = policy.get("levels_enabled")
    if not isinstance(levels, dict):
        raise AutonomyL3Error("autonomy-l3-levels-missing")
    if levels.get("4") is True or levels.get("5") is True:
        raise AutonomyL3Error("autonomy-l3-l4-l5-forbidden")
    level = int(policy.get("level") or 0)
    if level > 3:
        raise AutonomyL3Error("autonomy-l3-level-out-of-range")


def _assert_policy_scope(policy: dict[str, Any]) -> int:
    """Reject tampered max_jobs / allowed_jobs scope expansion."""
    max_jobs = int(policy.get("max_jobs_per_arm") or 0)
    if max_jobs < 1 or max_jobs > 10:
        raise AutonomyL3Error("autonomy-l3-scope-expansion:max-jobs")
    allowed = policy.get("allowed_jobs")
    if not isinstance(allowed, list) or not allowed:
        raise AutonomyL3Error("autonomy-l3-scope-expansion:allowed-jobs")
    for job in allowed:
        if not isinstance(job, str) or job not in ALLOWED_L3_JOBS:
            raise AutonomyL3Error(f"autonomy-l3-scope-expansion:{job}")
    return max_jobs


def _assert_arm_receipt(vault: Path, *, arm_id: str, policy: dict[str, Any]) -> None:
    """Reject stale arms and receipt mismatches before dispatch."""
    if policy.get("package_id") != PACKAGE_ID:
        raise AutonomyL3Error("autonomy-l3-receipt-mismatch:package")
    if policy.get("compat_snapshot_id") != SNAPSHOT_ID:
        raise AutonomyL3Error("autonomy-l3-receipt-mismatch:compat")
    if str(policy.get("arm_id") or "") != arm_id:
        raise AutonomyL3Error("autonomy-l3-receipt-mismatch:arm-id")
    arm_path = vault / "generated" / "ops" / "scheduler" / f"{arm_id}-arm.json"
    if not arm_path.is_file():
        raise AutonomyL3Error("autonomy-l3-stale-context:arm-missing")
    arm = json.loads(arm_path.read_text(encoding="utf-8"))
    if arm.get("arm_id") != arm_id:
        raise AutonomyL3Error("autonomy-l3-receipt-mismatch:arm-receipt")
    if arm.get("armed") is not True:
        raise AutonomyL3Error("autonomy-l3-stale-context:arm-inactive")


def enable_bounded_l3(
    vault: Path,
    *,
    policy_id: str,
    arm_id: str,
    operator: OperatorProfile | None = None,
    max_jobs: int = 3,
    job_timeout_s: int = 120,
) -> dict[str, Any]:
    """Enable L3 bounded autonomy under AUTHZ + armed scheduler receipt."""
    require_compatibility_anchor()
    op = operator or default_operator()
    op.require("autonomy.l3")
    pid = policy_id.strip()
    if not _ID_RE.fullmatch(pid):
        raise AutonomyL3Error("autonomy-l3-policy-id-invalid")
    if max_jobs < 1 or max_jobs > 10:
        raise AutonomyL3Error("autonomy-l3-max-jobs-out-of-range")
    if job_timeout_s < 1 or job_timeout_s > 600:
        raise AutonomyL3Error("autonomy-l3-timeout-out-of-range")
    arm_path = vault / "generated" / "ops" / "scheduler" / f"{arm_id}-arm.json"
    if not arm_path.is_file():
        raise AutonomyL3Error("autonomy-l3-scheduler-not-armed")
    arm = json.loads(arm_path.read_text(encoding="utf-8"))
    if arm.get("armed") is not True:
        raise AutonomyL3Error("autonomy-l3-scheduler-arm-inactive")
    if arm.get("arm_id") != arm_id:
        raise AutonomyL3Error("autonomy-l3-receipt-mismatch:arm-receipt")
    _reject_arm_overlap(vault, arm_id=arm_id, policy_id=pid)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "compat_snapshot_id": SNAPSHOT_ID,
        "policy_id": pid,
        "level": 3,
        "l3_bounded_autonomy": True,
        "enabled": True,
        "levels_enabled": {
            "0": True,
            "1": True,
            "2": True,
            "3": True,
            "4": False,
            "5": False,
        },
        "max_jobs_per_arm": max_jobs,
        "job_timeout_s": job_timeout_s,
        "allowed_jobs": ["validate", "build-indexes", "version"],
        "arm_id": arm_id,
        "operator_id": op.operator_id,
        "vault_write_enabled": False,
        "truth_boundary": TRUTH_BOUNDARY,
        "authority": {
            "level": "derived",
            "note": "L3 bounded ops jobs only; never Layer B promote",
        },
        "generated": {"by": "project-atlas"},
    }
    out = _policy_dir(vault) / f"{pid}-l3-policy.json"
    _atomic_write_json(out, payload)
    return payload


def disable_bounded_l3(
    vault: Path,
    *,
    policy_id: str,
    operator: OperatorProfile | None = None,
) -> dict[str, Any]:
    """Write a disable receipt for an existing L3 policy (fail-closed if missing)."""
    require_compatibility_anchor()
    op = operator or default_operator()
    op.require("autonomy.l3")
    pid = policy_id.strip()
    if not _ID_RE.fullmatch(pid):
        raise AutonomyL3Error("autonomy-l3-policy-id-invalid")
    path = _policy_dir(vault) / f"{pid}-l3-policy.json"
    if not path.is_file():
        raise AutonomyL3Error("autonomy-l3-policy-missing")
    prior = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(prior, dict):
        raise AutonomyL3Error("autonomy-l3-policy-invalid")
    # AS-SEC-SCAN-AUTONOMY-L3-POLICY-REWRITE-JSON-ESC-001: scrub the
    # entire loaded object before disable receipt + in-place rewrite.
    # AS-SEC-SCAN-AUTONOMY-L3-ARM-JSON-ESC-001 remains covered (arm_id).
    prior = _omit_decoded_secrets(prior)
    if not isinstance(prior, dict):
        raise AutonomyL3Error("autonomy-l3-policy-invalid")
    prior_arm = str(prior.get("arm_id") or "")
    if scan_text(prior_arm):
        prior_arm = "unknown"
        prior["arm_id"] = "unknown"
    payload: dict[str, Any] = {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "compat_snapshot_id": SNAPSHOT_ID,
        "policy_id": pid,
        "level": 3,
        "l3_bounded_autonomy": False,
        "enabled": False,
        "prior_enabled": bool(prior.get("enabled", True)),
        "arm_id": prior_arm or prior.get("arm_id"),
        "operator_id": op.operator_id,
        "vault_write_enabled": False,
        "truth_boundary": TRUTH_BOUNDARY,
        "authority": {
            "level": "derived",
            "note": "L3 disabled; never Layer B promote",
        },
        "generated": {"by": "project-atlas"},
    }
    out = _policy_dir(vault) / f"{pid}-l3-disabled.json"
    _atomic_write_json(out, payload)
    # Flip policy enabled flag in place for reconstructable state.
    prior["enabled"] = False
    prior["l3_bounded_autonomy"] = False
    _atomic_write_json(path, prior)
    return payload


def run_bounded_l3_loop(
    vault: Path,
    *,
    policy_id: str,
    jobs: list[str],
    operator: OperatorProfile | None = None,
) -> dict[str, Any]:
    """Run a bounded policy→dispatch loop under L3 + armed scheduler.

    Requires ``autonomy.l3`` and ``scheduler.dispatch``. Never promotes
    Layer B. Caps job count by policy ``max_jobs_per_arm``. Rejects
    scope expansion, L4/L5, stale arms, receipt mismatches, and duplicate
    job entries in one loop.
    """
    require_compatibility_anchor()
    op = operator or default_operator()
    op.require("autonomy.l3")
    op.require("scheduler.dispatch")
    pid = policy_id.strip()
    if not _ID_RE.fullmatch(pid):
        raise AutonomyL3Error("autonomy-l3-policy-id-invalid")
    path = _policy_dir(vault) / f"{pid}-l3-policy.json"
    if not path.is_file():
        raise AutonomyL3Error("autonomy-l3-policy-missing")
    policy = json.loads(path.read_text(encoding="utf-8"))
    if policy.get("enabled") is not True or policy.get("l3_bounded_autonomy") is not True:
        raise AutonomyL3Error("autonomy-l3-policy-disabled")
    _assert_l4_l5_disabled(policy)
    arm_id = str(policy.get("arm_id") or "")
    if not arm_id:
        raise AutonomyL3Error("autonomy-l3-arm-id-missing")
    if not _ID_RE.fullmatch(arm_id):
        # Fail-closed: arm_id is interpolated into a scheduler receipt path.
        raise AutonomyL3Error("autonomy-l3-arm-id-invalid")
    _assert_arm_receipt(vault, arm_id=arm_id, policy=policy)
    max_jobs = _assert_policy_scope(policy)
    timeout_s = int(policy.get("job_timeout_s") or 120)
    if timeout_s < 1 or timeout_s > 600:
        raise AutonomyL3Error("autonomy-l3-scope-expansion:timeout")
    if not jobs:
        raise AutonomyL3Error("autonomy-l3-jobs-empty")
    if len(jobs) > max_jobs:
        raise AutonomyL3Error("autonomy-l3-jobs-exceed-max")
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for raw_job in jobs:
        job = raw_job.strip()
        if job in DESTRUCTIVE_DENIED_JOBS or job not in ALLOWED_L3_JOBS:
            raise AutonomyL3Error(f"autonomy-l3-job-forbidden:{job}")
        if job in seen:
            raise AutonomyL3Error(f"autonomy-l3-duplicate-dispatch:{job}")
        seen.add(job)
        dispatch = dispatch_supervised_job(
            vault,
            arm_id=arm_id,
            job=job,  # type: ignore[arg-type]
            operator=op,
            timeout_s=timeout_s,
        )
        results.append(
            {
                "job": job,
                "exit_code": dispatch.get("exit_code"),
                "timed_out": dispatch.get("timed_out"),
                "duration_ms": dispatch.get("duration_ms"),
            }
        )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "compat_snapshot_id": SNAPSHOT_ID,
        "policy_id": pid,
        "arm_id": arm_id,
        "l3_loop": True,
        "jobs_requested": list(jobs),
        "jobs_run": results,
        "vault_write_enabled": False,
        "promoted": False,
        "levels_enabled": {
            "3": True,
            "4": False,
            "5": False,
        },
        "operator_id": op.operator_id,
        "truth_boundary": TRUTH_BOUNDARY,
        "authority": {
            "level": "derived",
            "note": "L3 loop dispatches supervised jobs only; never Layer B",
        },
        "generated": {"by": "project-atlas"},
    }
    out = _policy_dir(vault) / f"{pid}-l3-loop.json"
    _atomic_write_json(out, payload)
    return payload
