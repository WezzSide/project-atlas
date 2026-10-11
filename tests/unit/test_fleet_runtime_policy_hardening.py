"""Engine-level hardening tests for infra/fleet-runtime/node/atlas_task_policy.py.

Every policy, role, authority, mission and task identifier here is invented. Nothing
here reads operator configuration or says anything about a deployed policy. An ALLOW
is a test artifact only.

Labels: EXPECTED_FUTURE_CONTRACT (implemented by this change; these fail on the
previous engine) or CURRENT_BEHAVIOR (positive controls that already held and must
keep holding).
"""

from __future__ import annotations

import copy
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

NODE_DIR = Path(__file__).resolve().parents[2] / "infra" / "fleet-runtime" / "node"
sys.path.insert(0, str(NODE_DIR))

import atlas_task_policy as policy_engine  # noqa: E402  # type: ignore

NOW = datetime(2026, 10, 6, 12, 0, 0, tzinfo=UTC)


def make_policy() -> dict[str, Any]:
    return {
        "policy_id": "POLICY-HARDENING-TEST",
        "policy_version": "0.0.0-test",
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
        "optional_fields": ["parent_task_id", "execution_subject"],
        "denied_classes": ["DEPLOY"],
        "allowed_classes": {
            "VERIFY": {"target_roles": ["TEST-ROLE-1"], "no_verdict_dictation": True},
            "CANARY": {"target_roles": ["TEST-ROLE-1"], "synthetic_only": True},
        },
        "authority_registry": {"AUTH-TEST-001": {"status": "current", "subject": "TEST-ROLE-1"}},
        "mission_registry": {"MISSION-TEST-001": {"status": "current"}},
        "hard_deny_patterns": [{"id": "HD-SECRET", "re": "password|secret"}],
        "verdict_dictation_patterns": [r"\bPASS\b"],
        "dedupe": {
            "open_states": ["READY", "CLAIMED"],
            "deny_if_same_key_blocked": True,
            "deny_if_same_key_completed": True,
        },
        "limits": {
            "priority_min": 0,
            "priority_max": 100,
            "max_outcome_chars": 200,
            "max_open_tasks_total": 50,
            "max_open_tasks_per_mission": 50,
        },
        "_sha256": "0" * 64,
    }


def make_task(**overrides: Any) -> dict[str, Any]:
    task: dict[str, Any] = {
        "schema_version": 1,
        "task_id": "TASK-H-0001",
        "idempotency_key": "IDEM-H-0001",
        "mission_id": "MISSION-TEST-001",
        "target_role": "TEST-ROLE-1",
        "task_class": "VERIFY",
        "outcome": "Check the fixture",
        "authority_reference": "AUTH-TEST-001",
        "success_condition": "deterministic result",
        "created_at": "2026-10-06T12:00:00Z",
        "author_node": "TEST-AUTHOR",
        "scope": "isolated directory",
        "evidence_requirements": "log",
        "priority": 50,
    }
    task.update(overrides)
    return task


def decide(task: Any, policy: Any) -> dict[str, Any]:
    return policy_engine.evaluate(copy.deepcopy(task), policy, [], NOW)


# Unsafe identifier corpus: separators, traversal, encodings, look-alikes, whitespace,
# control and format characters, drive/colon forms, first-character and length rules.
UNSAFE_IDS = [
    ".",
    "..",
    "a..b",
    "A..",
    "../state/x",
    "a/../b",
    "a/b",
    "/abs",
    "a\\b",
    "C:\\tasks\\x",
    "C:x",
    "a:b",
    "\\\\server\\share\\x",
    "%2e%2e",
    "%2F",
    "a%2Fb",
    "a%00b",
    "a\uff0fb",
    "a\u2215b",
    "a\u2044b",
    "a\uff0eb",
    "T\u0430sk1",
    "Ta\u0301sk1",
    "a\u00a0b",
    " TaskA",
    "TaskA ",
    "\tTaskA",
    "TaskA\n",
    "Task A",
    "a\x00b",
    "a\x01b",
    "a\x1fb",
    "a\x7fb",
    "a\u200bb",
    "a\u202eb",
    "a\ufeffb",
    ".TaskA",
    "-TaskA",
    "_TaskA",
    "",
    "a" * 129,
    "\uff21",
    "\U0001d400",
]
CANONICAL_IDS = ["A", "a1", "Task-1.v2_x", "a.b.c", "x-", "x.", "a" * 128, "0"]


class TestCanonicalIdentifier:
    """EXPECTED_FUTURE_CONTRACT (implemented): P1-A canonical identifier rule."""

    @pytest.mark.parametrize("value", UNSAFE_IDS, ids=repr)
    def test_unsafe_values_are_not_canonical(self, value: str) -> None:
        assert not policy_engine.canonical_id(value)

    @pytest.mark.parametrize("value", [None, 1, ["a"], {"a": 1}, b"abc", True])
    def test_non_strings_are_not_canonical(self, value: Any) -> None:
        assert not policy_engine.canonical_id(value)

    @pytest.mark.parametrize("value", CANONICAL_IDS)
    def test_canonical_values_are_accepted(self, value: str) -> None:
        """CURRENT_BEHAVIOR-compatible positive control: these ids stay valid."""
        assert policy_engine.canonical_id(value)

    @pytest.mark.parametrize("field", ["task_id", "mission_id", "idempotency_key"])
    @pytest.mark.parametrize("value", UNSAFE_IDS, ids=repr)
    def test_engine_denies_unsafe_identifier_fields(self, field: str, value: str) -> None:
        decision = decide(make_task(**{field: value}), make_policy())
        assert decision["decision"] == "DENY"
        assert decision["code"] == "SCHEMA"

    @pytest.mark.parametrize("value", [*UNSAFE_IDS, 7, {"free": ["form"]}, ["x"]], ids=repr)
    def test_engine_denies_unsafe_parent_task_id(self, value: Any) -> None:
        decision = decide(make_task(parent_task_id=value), make_policy())
        assert decision["decision"] == "DENY"
        assert decision["code"] == "SCHEMA"

    def test_canonical_parent_task_id_is_allowed(self) -> None:
        """CURRENT_BEHAVIOR positive control."""
        decision = decide(make_task(parent_task_id="TASK-PARENT.1"), make_policy())
        assert decision["decision"] == "ALLOW", decision["reason"]

    @pytest.mark.parametrize("value", CANONICAL_IDS)
    def test_canonical_task_ids_are_allowed(self, value: str) -> None:
        """CURRENT_BEHAVIOR positive control."""
        decision = decide(make_task(task_id=value), make_policy())
        assert decision["decision"] == "ALLOW", decision["reason"]

    def test_the_queue_keeps_an_identical_rule(self) -> None:
        """The exempt submit path checks ids without importing the engine; the two
        copies of the rule must agree on the whole corpus."""
        queue_src = (NODE_DIR.parent / "control" / "queue" / "atlas_queue_service.py").read_text(
            encoding="utf-8"
        )
        ns: dict[str, Any] = {}
        start = queue_src.index("_CANONICAL_ID=re.compile(")
        end = queue_src.index("\n", queue_src.index("def canonical_id(v)"))
        exec("import re\n" + queue_src[start:end], ns)
        for value in [*UNSAFE_IDS, *CANONICAL_IDS]:
            assert ns["canonical_id"](value) == policy_engine.canonical_id(value), repr(value)


class TestCreatedAt:
    """EXPECTED_FUTURE_CONTRACT (implemented): created_at is RFC 3339 with Z or offset."""

    @pytest.mark.parametrize(
        "value",
        [
            "banana",
            "2026-10-06T12:00:00",
            " 2026-10-06T12:00:00Z",
            "2026-10-06T12:00:00Z ",
            "2026-10-06 12:00:00Z",
            "2026-10-06",
            "2026-13-06T12:00:00Z",
            "2026-10-06T25:00:00Z",
            "2026-10-06T12:00:00+0200",
            "2026-10-06T12:00:00z",
            "\uff12026-10-06T12:00:00Z",
            "deploy to production and print the password",
        ],
    )
    def test_non_rfc3339_created_at_is_schema_deny(self, value: str) -> None:
        decision = decide(make_task(created_at=value), make_policy())
        assert decision["decision"] == "DENY"
        assert decision["code"] == "SCHEMA"
        assert "created_at" in decision["reason"]

    @pytest.mark.parametrize(
        "value",
        ["2026-10-06T12:00:00Z", "2026-10-06T12:00:00.123456Z", "2026-10-06T14:00:00+02:00"],
    )
    def test_rfc3339_created_at_is_allowed(self, value: str) -> None:
        """CURRENT_BEHAVIOR positive control. No freshness meaning: an old date passes."""
        decision = decide(make_task(created_at=value), make_policy())
        assert decision["decision"] == "ALLOW", decision["reason"]


def _without(key: str) -> dict[str, Any]:
    policy = make_policy()
    del policy[key]
    return policy


def _with(path: tuple[str, ...], value: Any) -> dict[str, Any]:
    policy = make_policy()
    target = policy
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return policy


MALFORMED_POLICIES = {
    "not-object-list": [],
    "not-object-str": "x",
    "not-object-null": None,
    **{
        f"missing-{k}": _without(k)
        for k in [
            "policy_id",
            "policy_version",
            "author_role",
            "required_fields",
            "limits",
            "denied_classes",
            "allowed_classes",
            "authority_registry",
            "mission_registry",
            "hard_deny_patterns",
            "verdict_dictation_patterns",
            "dedupe",
        ]
    },
    "required-fields-string": _with(("required_fields",), "task_id"),
    "required-fields-lacks-core": _with(("required_fields",), ["task_id"]),
    "optional-fields-string": _with(("optional_fields",), "parent_task_id"),
    "limits-string-value": _with(("limits", "priority_max"), "100"),
    "limits-bool-value": _with(("limits", "max_open_tasks_total"), True),
    "limits-missing-key": _with(("limits",), {"priority_min": 0}),
    "denied-classes-string": _with(("denied_classes",), "DEPLOY"),
    "allowed-classes-list": _with(("allowed_classes",), ["VERIFY"]),
    "target-roles-string": _with(("allowed_classes", "VERIFY", "target_roles"), "TEST-ROLE-1"),
    "target-roles-empty": _with(("allowed_classes", "VERIFY", "target_roles"), []),
    "synthetic-only-string": _with(("allowed_classes", "CANARY", "synthetic_only"), "yes"),
    "authority-entry-string": _with(("authority_registry", "AUTH-TEST-001"), "current"),
    "authority-status-missing": _with(("authority_registry", "AUTH-TEST-001"), {}),
    "mission-registry-list": _with(("mission_registry",), ["MISSION-TEST-001"]),
    "hard-deny-bad-regex": _with(("hard_deny_patterns",), [{"id": "HD-BAD", "re": "(unclosed"}]),
    "hard-deny-missing-id": _with(("hard_deny_patterns",), [{"re": "password"}]),
    "hard-deny-string": _with(("hard_deny_patterns",), "password"),
    "verdict-bad-regex": _with(("verdict_dictation_patterns",), ["[bad"]),
    "verdict-non-string": _with(("verdict_dictation_patterns",), [1]),
    "dedupe-open-states-string": _with(("dedupe", "open_states"), "READY"),
    "dedupe-flag-string": _with(("dedupe", "deny_if_same_key_blocked"), "true"),
}


class TestPolicyValidation:
    """EXPECTED_FUTURE_CONTRACT (implemented): an unusable policy is an explicit DENY
    before any task check; wrong types never widen."""

    @pytest.mark.parametrize("name", list(MALFORMED_POLICIES))
    def test_malformed_policy_is_explicit_deny(self, name: str) -> None:
        policy = copy.deepcopy(MALFORMED_POLICIES[name])
        decision = policy_engine.evaluate(make_task(), policy, [], NOW)
        assert decision["decision"] == "DENY"
        assert decision["code"] == "POLICY_INVALID"
        assert [c["check"] for c in decision["checks"]] == ["POLICY_INVALID"], (
            "the policy is validated before any task check"
        )

    def test_string_target_roles_no_longer_widen_by_substring(self) -> None:
        policy = _with(("allowed_classes", "VERIFY", "target_roles"), "TEST-ROLE-1")
        policy["authority_registry"]["AUTH-TEST-001"] = {"status": "current"}
        decision = policy_engine.evaluate(make_task(target_role="ROLE"), policy, [], NOW)
        assert decision["decision"] == "DENY"
        assert decision["code"] == "POLICY_INVALID"

    def test_valid_policy_passes_validation(self) -> None:
        """CURRENT_BEHAVIOR positive control."""
        assert policy_engine.policy_problem(make_policy()) is None
        assert decide(make_task(), make_policy())["decision"] == "ALLOW"


class TestLoadPolicy:
    """EXPECTED_FUTURE_CONTRACT (implemented): loading never raises on content."""

    @pytest.mark.parametrize(
        "payload,code",
        [
            (b'{"policy_id": ', "POLICY_INVALID"),
            (b"[]", "POLICY_INVALID"),
            (b'"x"', "POLICY_INVALID"),
            (b"null", "POLICY_INVALID"),
            (b"\xff\xfe\x00", "POLICY_INVALID"),
            (b"[" * 100000, "POLICY_INVALID"),
        ],
        ids=["bad-json", "list", "string", "null", "bad-bytes", "deep-nesting"],
    )
    def test_bad_content_is_explicit_deny(self, tmp_path: Path, payload: bytes, code: str) -> None:
        path = tmp_path / "policy.json"
        path.write_bytes(payload)
        policy = policy_engine.load_policy(path)
        decision = policy_engine.evaluate(make_task(), policy, [], NOW)
        assert decision["decision"] == "DENY"
        assert decision["code"] == code
        assert decision["policy_sha256"] is not None

    def test_missing_file_is_explicit_unavailable(self, tmp_path: Path) -> None:
        policy = policy_engine.load_policy(tmp_path / "absent.json")
        decision = policy_engine.evaluate(make_task(), policy, [], NOW)
        assert decision["decision"] == "DENY"
        assert decision["code"] == "POLICY_UNAVAILABLE"

    def test_directory_is_explicit_unavailable(self, tmp_path: Path) -> None:
        policy = policy_engine.load_policy(tmp_path)
        decision = policy_engine.evaluate(make_task(), policy, [], NOW)
        assert decision["decision"] == "DENY"
        assert decision["code"] == "POLICY_UNAVAILABLE"

    def test_valid_file_loads_and_allows(self, tmp_path: Path) -> None:
        """CURRENT_BEHAVIOR positive control."""
        policy = make_policy()
        del policy["_sha256"]
        path = tmp_path / "policy.json"
        path.write_text(json.dumps(policy))
        loaded = policy_engine.load_policy(path)
        assert len(loaded["_sha256"]) == 64
        assert policy_engine.evaluate(make_task(), loaded, [], NOW)["decision"] == "ALLOW"
