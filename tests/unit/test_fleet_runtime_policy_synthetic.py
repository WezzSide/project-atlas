"""Synthetic policy-engine tests for infra/fleet-runtime/node/atlas_task_policy.py.

These tests exercise the decision boundaries of ``evaluate()`` using an explicit,
invented policy document. They do not depend on the real task-authoring policy or
operator configuration. An ALLOW result here is a test artifact only, never an
operational execution grant.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

# The fleet-runtime node code is intentionally outside the main src/ tree.
POLICY_DIR = Path(__file__).resolve().parents[2] / "infra" / "fleet-runtime" / "node"
sys.path.insert(0, str(POLICY_DIR))

import atlas_task_policy as policy_engine  # noqa: E402  # type: ignore


@pytest.fixture
def synthetic_policy() -> dict[str, Any]:
    """A fully explicit, self-contained policy for the engine decision tests."""
    policy: dict[str, Any] = {
        "policy_id": "POLICY-SYNTHETIC-001",
        "policy_version": "1.0.0-test",
        "author_role": "TEST-AUTHOR",
        "required_fields": [
            "schema_version",
            "task_id",
            "mission_id",
            "target_role",
            "task_class",
            "outcome",
            "authority_reference",
            "idempotency_key",
            "success_condition",
            "created_at",
            "author_node",
            "scope",
            "evidence_requirements",
            "priority",
        ],
        "optional_fields": ["parent_task_id"],
        "denied_classes": ["DEPLOY", "MERGE", "CREDENTIALS"],
        "allowed_classes": {
            "VERIFY": {
                "target_roles": ["TEST-ROLE-1", "TEST-ROLE-2"],
                "no_verdict_dictation": True,
            },
            "CANARY": {
                "target_roles": ["TEST-ROLE-1"],
                "synthetic_only": True,
            },
            "RECONCILE": {
                "target_roles": ["TEST-ROLE-2"],
            },
        },
        "authority_registry": {
            "AUTH-TEST-001": {"status": "current", "subject": "TEST-ROLE-1"},
            "AUTH-TEST-002": {
                "status": "current",
                "valid_until": "2099-12-31T23:59:59Z",
                "subject": "TEST-ROLE-2",
            },
            "AUTH-TEST-EXPIRED": {
                "status": "current",
                "valid_until": "2020-01-01T00:00:00Z",
                "subject": "TEST-ROLE-1",
            },
            "AUTH-TEST-SUPERSEDED": {"status": "superseded", "subject": "TEST-ROLE-1"},
            "AUTH-VPS3-TASK-AUTHORING-001": {"status": "current"},
        },
        "mission_registry": {
            "MISSION-TEST-001": {"status": "current"},
            "MISSION-TEST-SUPERSEDED": {"status": "superseded"},
        },
        "hard_deny_patterns": [
            {"id": "HD-SECRET", "re": r"password|secret|private_key"},
            {"id": "HD-PUBLIC", "re": r"expose.*internet|public.*endpoint"},
        ],
        "verdict_dictation_patterns": [r"\bPASS\b|\bAPPROVE\b"],
        "dedupe": {
            "open_states": ["READY", "CLAIMED"],
            "deny_if_same_key_blocked": True,
            "deny_if_same_key_completed": True,
        },
        "limits": {
            "priority_min": 0,
            "priority_max": 100,
            "max_outcome_chars": 200,
            "max_open_tasks_total": 5,
            "max_open_tasks_per_mission": 2,
        },
    }
    policy["_sha256"] = hashlib.sha256(
        json.dumps(policy, sort_keys=True).encode()
    ).hexdigest()
    return policy


@pytest.fixture
def base_task() -> dict[str, Any]:
    """A minimal task envelope that satisfies the synthetic policy."""
    return {
        "schema_version": 1,
        "task_id": "TASK-TEST-0001",
        "mission_id": "MISSION-TEST-001",
        "target_role": "TEST-ROLE-1",
        "task_class": "VERIFY",
        "outcome": "Verify the synthetic canary fixture behaves deterministically",
        "authority_reference": "AUTH-TEST-001",
        "idempotency_key": "IDEM-TEST-0001",
        "success_condition": "Test completes with deterministic result",
        "created_at": "2026-10-06T12:00:00Z",
        "author_node": "TEST-AUTHOR",
        "scope": "synthetic fixture in isolated test directory",
        "evidence_requirements": "reproducible test log",
        "priority": 50,
    }


@pytest.fixture
def pinned_now() -> datetime:
    """A controlled evaluation time between the fixture validity windows."""
    return datetime(2026, 10, 6, 12, 0, 0, tzinfo=UTC)


def _evaluate(
    task: dict[str, Any],
    policy: dict[str, Any],
    open_tasks: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run the engine with a deep-copied task so mutating fixtures does not leak."""
    return policy_engine.evaluate(copy.deepcopy(task), policy, open_tasks or [], now)


def _first_denial_check(decision: dict[str, Any]) -> str:
    """Return the code of the first failed check, or '' if every check passed."""
    for check in decision.get("checks", []):
        if not check.get("ok"):
            return str(check.get("check", ""))
    return ""


class TestSchemaAndEnvelope:
    """Decision boundaries for envelope shape and required fields."""

    def test_minimal_valid_task_is_allowed(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        decision = _evaluate(base_task, synthetic_policy, now=pinned_now)
        assert decision["decision"] == "ALLOW"
        assert decision["task_class"] == "VERIFY"
        assert decision["authority_reference"] == "AUTH-TEST-001"
        assert decision["policy_id"] == synthetic_policy["policy_id"]
        assert decision["policy_version"] == synthetic_policy["policy_version"]
        assert decision["policy_sha256"] == synthetic_policy["_sha256"]

    def test_task_must_be_an_object(
        self,
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        decision = policy_engine.evaluate("not-a-dict", synthetic_policy, [], pinned_now)
        assert decision["decision"] == "DENY"
        assert decision["reason"].startswith("SCHEMA:")
        assert _first_denial_check(decision) == "SCHEMA"

    def test_missing_required_field_is_schema_deny(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        for field in synthetic_policy["required_fields"]:
            task = copy.deepcopy(base_task)
            task.pop(field)
            decision = _evaluate(task, synthetic_policy, now=pinned_now)
            assert decision["decision"] == "DENY", field
            assert "missing fields" in decision["reason"], field

    def test_unexpected_field_is_schema_deny(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        base_task["extra_field"] = "surprise"
        decision = _evaluate(base_task, synthetic_policy, now=pinned_now)
        assert decision["decision"] == "DENY"
        assert "unexpected fields" in decision["reason"]

    def test_schema_version_must_be_one(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        base_task["schema_version"] = 2
        decision = _evaluate(base_task, synthetic_policy, now=pinned_now)
        assert decision["decision"] == "DENY"
        assert "schema_version must be 1" in decision["reason"]

    def test_string_fields_may_not_be_empty(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        for field in (
            "task_id",
            "mission_id",
            "target_role",
            "task_class",
            "outcome",
            "authority_reference",
            "idempotency_key",
            "success_condition",
            "created_at",
        ):
            task = copy.deepcopy(base_task)
            task[field] = "   "
            decision = _evaluate(task, synthetic_policy, now=pinned_now)
            assert decision["decision"] == "DENY", field
            assert decision["reason"].startswith("SCHEMA:")

    def test_scope_must_be_non_empty(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        scope_values: tuple[Any, ...] = ("", [], {})
        for value in scope_values:
            task = copy.deepcopy(base_task)
            task["scope"] = value
            decision = _evaluate(task, synthetic_policy, now=pinned_now)
            assert decision["decision"] == "DENY", value
            assert "scope must be non-empty" in decision["reason"]

    def test_evidence_requirements_must_be_non_empty(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        evidence_values: tuple[Any, ...] = ("", [])
        for value in evidence_values:
            task = copy.deepcopy(base_task)
            task["evidence_requirements"] = value
            decision = _evaluate(task, synthetic_policy, now=pinned_now)
            assert decision["decision"] == "DENY", value

    def test_priority_must_be_within_limits(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        for value in (-1, 101, "fifty"):
            task = copy.deepcopy(base_task)
            task["priority"] = value
            decision = _evaluate(task, synthetic_policy, now=pinned_now)
            assert decision["decision"] == "DENY", value
            assert "priority" in decision["reason"]

    def test_outcome_length_cap_is_enforced(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        base_task["outcome"] = "x" * (synthetic_policy["limits"]["max_outcome_chars"] + 1)
        decision = _evaluate(base_task, synthetic_policy, now=pinned_now)
        assert decision["decision"] == "DENY"
        assert "outcome too long" in decision["reason"]


class TestAuthorAndClass:
    """Author role, class allow/deny and role-for-class boundaries."""

    def test_author_node_must_match_policy_role(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        base_task["author_node"] = "WRONG-AUTHOR"
        decision = _evaluate(base_task, synthetic_policy, now=pinned_now)
        assert decision["decision"] == "DENY"
        assert "author_node must be" in decision["reason"]

    def test_denied_class_is_never_allowed(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        for cls in synthetic_policy["denied_classes"]:
            task = copy.deepcopy(base_task)
            task["task_class"] = cls
            task["target_role"] = "TEST-ROLE-2"  # CLASS_DENIED fires before the role check
            decision = _evaluate(task, synthetic_policy, now=pinned_now)
            assert decision["decision"] == "DENY", cls
            assert "CLASS_DENIED" in decision["reason"], cls
            assert "AUTHORITY_BOUNDARY" in decision["reason"], cls

    def test_unknown_class_is_denied(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        base_task["task_class"] = "UNKNOWN-CLASS"
        decision = _evaluate(base_task, synthetic_policy, now=pinned_now)
        assert decision["decision"] == "DENY"
        assert "CLASS_UNKNOWN" in decision["reason"]

    def test_role_must_be_allowed_for_class(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        base_task["target_role"] = "TEST-ROLE-2"  # VERIFY allows this, but we switch class below
        base_task["task_class"] = "CANARY"
        base_task["authority_reference"] = "AUTH-VPS3-TASK-AUTHORING-001"
        base_task["scope"] = "synthetic isolated fixture"
        decision = _evaluate(base_task, synthetic_policy, now=pinned_now)
        assert decision["decision"] == "DENY"
        assert "ROLE_FOR_CLASS" in decision["reason"]


class TestAuthorityAndMission:
    """Authority registry and mission registry boundaries."""

    def test_authority_must_exist(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        base_task["authority_reference"] = "AUTH-TEST-MISSING"
        decision = _evaluate(base_task, synthetic_policy, now=pinned_now)
        assert decision["decision"] == "DENY"
        assert "AUTHORITY_MISSING" in decision["reason"]

    def test_authority_must_be_current(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        base_task["authority_reference"] = "AUTH-TEST-SUPERSEDED"
        decision = _evaluate(base_task, synthetic_policy, now=pinned_now)
        assert decision["decision"] == "DENY"
        assert "AUTHORITY_NOT_CURRENT" in decision["reason"]

    def test_authority_valid_until_is_enforced(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        base_task["authority_reference"] = "AUTH-TEST-EXPIRED"
        decision = _evaluate(base_task, synthetic_policy, now=pinned_now)
        assert decision["decision"] == "DENY"
        assert "AUTHORITY_EXPIRED" in decision["reason"]

    def test_authority_subject_must_match_target_role(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        base_task["target_role"] = "TEST-ROLE-2"
        base_task["authority_reference"] = "AUTH-TEST-001"  # subject TEST-ROLE-1
        decision = _evaluate(base_task, synthetic_policy, now=pinned_now)
        assert decision["decision"] == "DENY"
        assert "AUTHORITY_SCOPE" in decision["reason"]

    def test_authoring_authority_is_exempt_from_subject_match(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        base_task["task_class"] = "CANARY"
        base_task["scope"] = "synthetic isolated fixture"
        base_task["authority_reference"] = "AUTH-VPS3-TASK-AUTHORING-001"
        # A mismatching subject, so the exemption itself is what allows the task.
        synthetic_policy["authority_registry"]["AUTH-VPS3-TASK-AUTHORING-001"]["subject"] = (
            "TEST-ROLE-2"
        )
        decision = _evaluate(base_task, synthetic_policy, now=pinned_now)
        assert decision["decision"] == "ALLOW"

    def test_mission_must_exist(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        base_task["mission_id"] = "MISSION-TEST-MISSING"
        decision = _evaluate(base_task, synthetic_policy, now=pinned_now)
        assert decision["decision"] == "DENY"
        assert "MISSION_UNKNOWN" in decision["reason"]

    def test_mission_must_be_current(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        base_task["mission_id"] = "MISSION-TEST-SUPERSEDED"
        decision = _evaluate(base_task, synthetic_policy, now=pinned_now)
        assert decision["decision"] == "DENY"
        assert "MISSION_SUPERSEDED" in decision["reason"]


class TestSemanticGuards:
    """Hard-deny, verdict-dictation and synthetic-only boundaries."""

    def test_hard_deny_pattern_blocks_task(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        base_task["outcome"] = "Verify the password rotation works"
        decision = _evaluate(base_task, synthetic_policy, now=pinned_now)
        assert decision["decision"] == "DENY"
        assert "HARD_DENY" in decision["reason"]
        assert "HD-SECRET" in decision["reason"]
        assert "AUTHORITY_BOUNDARY" in decision["reason"]

    def test_hard_deny_scans_combined_text(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        base_task["success_condition"] = "expose the service to the internet"
        decision = _evaluate(base_task, synthetic_policy, now=pinned_now)
        assert decision["decision"] == "DENY"
        assert "HARD_DENY" in decision["reason"]
        assert "HD-PUBLIC" in decision["reason"]

    def test_verdict_dictation_is_blocked_for_verify_class(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        base_task["outcome"] = "Make the auditor return PASS"
        decision = _evaluate(base_task, synthetic_policy, now=pinned_now)
        assert decision["decision"] == "DENY"
        assert "VERDICT_DICTATION" in decision["reason"]

    def test_verdict_dictation_only_applies_with_no_verdict_dictation_flag(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        base_task["task_class"] = "RECONCILE"
        base_task["target_role"] = "TEST-ROLE-2"
        base_task["authority_reference"] = "AUTH-TEST-002"
        base_task["outcome"] = "Make the auditor return PASS"
        decision = _evaluate(base_task, synthetic_policy, now=pinned_now)
        assert decision["decision"] == "ALLOW"

    def test_synthetic_only_class_requires_synthetic_marker(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        base_task["task_class"] = "CANARY"
        base_task["authority_reference"] = "AUTH-VPS3-TASK-AUTHORING-001"
        base_task["outcome"] = "Run the production database check"
        base_task["scope"] = "production database"
        base_task["evidence_requirements"] = "query log"
        decision = _evaluate(base_task, synthetic_policy, now=pinned_now)
        assert decision["decision"] == "DENY"
        assert "SYNTHETIC_REQUIRED" in decision["reason"]

    @pytest.mark.parametrize("marker", ["synthetic", "isolated", "fixture", "canary"])
    def test_synthetic_only_class_accepts_synthetic_markers(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
        marker: str,
    ) -> None:
        base_task["task_class"] = "CANARY"
        base_task["authority_reference"] = "AUTH-VPS3-TASK-AUTHORING-001"
        base_task["outcome"] = f"Run the {marker} check"
        base_task["scope"] = f"{marker} environment"
        decision = _evaluate(base_task, synthetic_policy, now=pinned_now)
        assert decision["decision"] == "ALLOW", marker


class TestDedupeAndLimits:
    """Idempotency, duplicate identity and open-task limit boundaries."""

    def test_open_duplicate_idempotency_key_is_denied(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        open_tasks = [
            {
                "task_id": "TASK-EXISTING",
                "idempotency_key": base_task["idempotency_key"],
                "state": "READY",
                "mission_id": base_task["mission_id"],
            }
        ]
        decision = _evaluate(base_task, synthetic_policy, open_tasks, now=pinned_now)
        assert decision["decision"] == "DENY"
        assert "DUPLICATE_OPEN" in decision["reason"]

    def test_blocked_duplicate_idempotency_key_is_denied_when_configured(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        open_tasks = [
            {
                "task_id": "TASK-BLOCKED",
                "idempotency_key": base_task["idempotency_key"],
                "state": "BLOCKED",
                "mission_id": base_task["mission_id"],
            }
        ]
        decision = _evaluate(base_task, synthetic_policy, open_tasks, now=pinned_now)
        assert decision["decision"] == "DENY"
        assert "DUPLICATE_BLOCKED" in decision["reason"]

    def test_completed_duplicate_idempotency_key_is_denied_when_configured(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        open_tasks = [
            {
                "task_id": "TASK-DONE",
                "idempotency_key": base_task["idempotency_key"],
                "state": "COMPLETED",
                "mission_id": base_task["mission_id"],
            }
        ]
        decision = _evaluate(base_task, synthetic_policy, open_tasks, now=pinned_now)
        assert decision["decision"] == "DENY"
        assert "ALREADY_DONE" in decision["reason"]

    def test_duplicate_task_id_is_denied(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        open_tasks = [
            {
                "task_id": base_task["task_id"],
                "idempotency_key": "OTHER-KEY",
                "state": "READY",
                "mission_id": base_task["mission_id"],
            }
        ]
        decision = _evaluate(base_task, synthetic_policy, open_tasks, now=pinned_now)
        assert decision["decision"] == "DENY"
        assert "TASK_ID_EXISTS" in decision["reason"]

    def test_total_open_task_limit_is_enforced(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        cap = synthetic_policy["limits"]["max_open_tasks_total"]
        open_tasks = [
            {
                "task_id": f"TASK-OPEN-{i}",
                "idempotency_key": f"IDEM-OPEN-{i}",
                "state": "READY",
                "mission_id": "MISSION-TEST-001",
            }
            for i in range(cap)
        ]
        decision = _evaluate(base_task, synthetic_policy, open_tasks, now=pinned_now)
        assert decision["decision"] == "DENY"
        assert "LIMIT_TOTAL" in decision["reason"]

    def test_per_mission_open_task_limit_is_enforced(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        cap = synthetic_policy["limits"]["max_open_tasks_per_mission"]
        open_tasks = [
            {
                "task_id": f"TASK-MISSION-{i}",
                "idempotency_key": f"IDEM-MISSION-{i}",
                "state": "READY",
                "mission_id": base_task["mission_id"],
            }
            for i in range(cap)
        ]
        decision = _evaluate(base_task, synthetic_policy, open_tasks, now=pinned_now)
        assert decision["decision"] == "DENY"
        assert "LIMIT_MISSION" in decision["reason"]


class TestReproducibilityAndProvenance:
    """Determinism, policy hash and load_policy behavior."""

    def test_evaluation_is_deterministic_for_fixed_inputs(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        decision1 = _evaluate(base_task, synthetic_policy, [], pinned_now)
        decision2 = _evaluate(base_task, synthetic_policy, [], pinned_now)
        assert decision1 == decision2

    def test_load_policy_attaches_sha256(
        self,
        tmp_path: Path,
        synthetic_policy: dict[str, Any],
    ) -> None:
        path = tmp_path / "policy.json"
        raw = json.dumps(synthetic_policy, sort_keys=True).encode()
        path.write_bytes(raw)
        loaded = policy_engine.load_policy(path)
        assert loaded["_sha256"] == hashlib.sha256(raw).hexdigest()

    def test_loaded_policy_used_by_evaluate(
        self,
        tmp_path: Path,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        path = tmp_path / "policy.json"
        path.write_text(json.dumps(synthetic_policy, sort_keys=True))
        loaded = policy_engine.load_policy(path)
        decision = policy_engine.evaluate(copy.deepcopy(base_task), loaded, [], pinned_now)
        assert decision["decision"] == "ALLOW"
        assert decision["policy_sha256"] == loaded["_sha256"]

    def test_evaluated_at_is_present_and_stable(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        decision = _evaluate(base_task, synthetic_policy, [], pinned_now)
        assert decision["evaluated_at"] == pinned_now.isoformat(timespec="seconds")


class TestObservedCurrentBehavior:
    """Characterisation of the current engine; not a documented guarantee."""

    def test_open_tasks_entry_without_mission_id_is_tolerated(
        self,
        base_task: dict[str, Any],
        synthetic_policy: dict[str, Any],
        pinned_now: datetime,
    ) -> None:
        """The limit count reads mission_id from open entries that may lack it."""
        open_tasks = [
            {
                "task_id": "TASK-EXISTING",
                "idempotency_key": "IDEM-TEST-OTHER",
                "state": "READY",
            }
        ]
        decision = _evaluate(base_task, synthetic_policy, open_tasks, now=pinned_now)
        assert decision["decision"] == "ALLOW"
