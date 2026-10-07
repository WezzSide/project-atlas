"""Contract tests for the candidate task class DOCS_ONLY_IMPLEMENTATION.

Lane: FLEET-POLICY-CONTRACT-TESTS.

These tests pin the semantics of a *candidate policy fragment*
(tests/fixtures/fleet_policy_contract/docs_only_implementation.candidate.json)
against the task-policy engine in infra/fleet-runtime/node/atlas_task_policy.py.

They are not evidence about any operator policy: the operator policy document is
not in this repository, and nothing here shows that a deployed policy contains
this class. All identities, roles, authorities and missions are invented. An
ALLOW here is a test artifact, never an execution grant.

Each test is labelled CANDIDATE_CONTRACT (pins the fragment) or
CURRENT_BEHAVIOR (pins what the engine does today, including what it does not
enforce).
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "infra" / "fleet-runtime" / "node"))

import atlas_task_policy as policy_engine  # noqa: E402  # type: ignore

FRAGMENT_PATH = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "fleet_policy_contract"
    / "docs_only_implementation.candidate.json"
)

CLASS_NAME = "DOCS_ONLY_IMPLEMENTATION"
DECLARED_SEMANTICS = (
    "A bounded repository implementation task whose intended mutation is limited "
    "to explicitly declared documentation files and which performs no runtime, "
    "deployment, credential, service, policy or executable-code mutation."
)
CONTROL = "TEST-CONTROL-ROLE"
VERIFY = "TEST-VERIFY-ROLE"
FORGE = "TEST-FORGE-ROLE"
CANARY_ONLY = "TEST-CANARY-ONLY-ROLE"
NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def fragment() -> dict[str, Any]:
    return json.loads(FRAGMENT_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def policy(fragment: dict[str, Any]) -> dict[str, Any]:
    """An invented policy that contains the candidate class and nothing real."""
    return {
        "policy_id": "POLICY-CONTRACT-CANDIDATE",
        "policy_version": "0.0.0-candidate",
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
        "optional_fields": [],
        "denied_classes": ["TEST-DENIED-CLASS"],
        "allowed_classes": {
            fragment["class_name"]: fragment["class_spec"],
            "TEST-FORGE-CLASS": {"target_roles": [FORGE]},
            "TEST-CANARY-CLASS": {
                "target_roles": [CANARY_ONLY],
                "synthetic_only": True,
            },
        },
        "authority_registry": {
            "AUTH-TEST-ANY": {"status": "current"},
        },
        "mission_registry": {"MISSION-TEST-CONTRACT": {"status": "current"}},
        "hard_deny_patterns": [],
        "verdict_dictation_patterns": [],
        "dedupe": {
            "open_states": ["READY", "CLAIMED"],
            "deny_if_same_key_blocked": True,
            "deny_if_same_key_completed": True,
        },
        "limits": {
            "priority_min": 0,
            "priority_max": 100,
            "max_outcome_chars": 400,
            "max_open_tasks_total": 5,
            "max_open_tasks_per_mission": 5,
        },
    }


def make_task(target_role: str, **overrides: Any) -> dict[str, Any]:
    task: dict[str, Any] = {
        "schema_version": 1,
        "task_id": "TASK-CONTRACT-0001",
        "mission_id": "MISSION-TEST-CONTRACT",
        "target_role": target_role,
        "task_class": CLASS_NAME,
        "outcome": "Add one documentation file describing an invented example",
        "authority_reference": "AUTH-TEST-ANY",
        "idempotency_key": "IDEM-CONTRACT-0001",
        "success_condition": "The declared documentation file exists",
        "created_at": "2026-10-07T12:00:00Z",
        "author_node": "TEST-AUTHOR",
        "scope": "docs/example/INVENTED.md",
        "evidence_requirements": "patch of the declared file",
        "priority": 10,
    }
    task.update(overrides)
    return task


def decide(task: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    return policy_engine.evaluate(task, policy, [], now=NOW)


# --- CANDIDATE_CONTRACT: the fragment itself --------------------------------


def test_fragment_is_marked_candidate_not_adopted(fragment: dict[str, Any]) -> None:
    """CANDIDATE_CONTRACT: the fixture never presents itself as deployed policy."""
    assert fragment["fragment_status"] == "CANDIDATE_NOT_ADOPTED"


def test_class_name_is_exact(fragment: dict[str, Any]) -> None:
    """CANDIDATE_CONTRACT: the class name is pinned exactly."""
    assert fragment["class_name"] == CLASS_NAME
    assert fragment["class_name"] == "DOCS_ONLY_IMPLEMENTATION"


def test_declared_semantics_are_exact(fragment: dict[str, Any]) -> None:
    """CANDIDATE_CONTRACT: the semantic description is pinned word for word."""
    assert fragment["declared_semantics"] == DECLARED_SEMANTICS


def test_target_roles_are_exactly_control_and_verify(
    fragment: dict[str, Any],
) -> None:
    """CANDIDATE_CONTRACT: no role beyond control and verify, in this order."""
    assert fragment["class_spec"]["target_roles"] == [CONTROL, VERIFY]


def test_class_spec_has_no_other_keys(fragment: dict[str, Any]) -> None:
    """CANDIDATE_CONTRACT: the engine-facing spec is the role list and nothing else.

    No flag (such as a synthetic-only flag) rides along, and the semantic
    description stays outside the spec handed to the engine.
    """
    assert sorted(fragment["class_spec"]) == ["target_roles"]


def test_fragment_carries_its_disclaimer(fragment: dict[str, Any]) -> None:
    """CANDIDATE_CONTRACT: the fixture keeps its not-operator-policy note."""
    assert "Not the operator policy" in fragment["fragment_note"]


def test_write_scope_enforcement_is_declared_not_implemented(
    fragment: dict[str, Any],
) -> None:
    """CANDIDATE_CONTRACT: the fragment states that scope is not enforced."""
    assert fragment["write_scope_enforcement"] == "NOT_IMPLEMENTED"


# --- CANDIDATE_CONTRACT evaluated by the current engine ----------------------


@pytest.mark.parametrize("role", [CONTROL, VERIFY])
def test_class_accepted_for_control_and_verify(role: str, policy: dict[str, Any]) -> None:
    """CANDIDATE_CONTRACT: control and verify may be given this class."""
    decision = decide(make_task(role), policy)
    assert decision["decision"] == "ALLOW", decision["reason"]
    assert decision["task_class"] == CLASS_NAME


def test_class_refused_for_forge(policy: dict[str, Any]) -> None:
    """CANDIDATE_CONTRACT: forge is refused for this class by the role check."""
    decision = decide(make_task(FORGE), policy)
    assert decision["decision"] == "DENY"
    assert decision["reason"].startswith("ROLE_FOR_CLASS:")


def test_forge_role_is_otherwise_usable(policy: dict[str, Any]) -> None:
    """Control for the test above: the forge refusal is specific to the class."""
    decision = decide(make_task(FORGE, task_class="TEST-FORGE-CLASS"), policy)
    assert decision["decision"] == "ALLOW", decision["reason"]


def test_synthetic_only_role_does_not_gain_the_class(policy: dict[str, Any]) -> None:
    """CANDIDATE_CONTRACT: a role allowed only for a synthetic-only class is refused."""
    decision = decide(make_task(CANARY_ONLY), policy)
    assert decision["decision"] == "DENY"
    assert decision["reason"].startswith("ROLE_FOR_CLASS:")


def test_unrelated_role_does_not_gain_the_class(policy: dict[str, Any]) -> None:
    """CANDIDATE_CONTRACT: a role named nowhere in the policy is refused."""
    decision = decide(make_task("TEST-UNRELATED-ROLE"), policy)
    assert decision["decision"] == "DENY"
    assert decision["reason"].startswith("ROLE_FOR_CLASS:")


@pytest.mark.parametrize(
    "near_miss",
    ["docs_only_implementation", "DOCS_ONLY", "DOCS-ONLY-IMPLEMENTATION"],
)
def test_near_miss_class_names_are_unknown(near_miss: str, policy: dict[str, Any]) -> None:
    """CANDIDATE_CONTRACT: only the exact class name is recognised."""
    decision = decide(make_task(CONTROL, task_class=near_miss), policy)
    assert decision["decision"] == "DENY"
    assert decision["reason"].startswith("CLASS_UNKNOWN:")


# --- CURRENT_BEHAVIOR: what the engine does not enforce ----------------------


@pytest.mark.parametrize(
    "scope",
    [
        "src/invented_module.py",
        ["docs/example/INVENTED.md", "infra/invented/service.py"],
        "anything at all",
    ],
)
def test_engine_does_not_enforce_docs_only_scope(scope: Any, policy: dict[str, Any]) -> None:
    """CURRENT_BEHAVIOR: WRITE_SCOPE_ENFORCEMENT = NOT_IMPLEMENTED.

    The engine requires only a non-empty scope. A task of this class whose
    declared scope names non-documentation paths is still allowed. "Docs-only"
    is a declared meaning that must be checked outside the engine; nothing here
    may be read as the engine rejecting an out-of-scope write.
    """
    decision = decide(make_task(CONTROL, scope=scope), policy)
    assert decision["decision"] == "ALLOW", decision["reason"]
