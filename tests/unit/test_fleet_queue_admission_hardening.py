"""Queue admission hardening tests for infra/fleet-runtime/control/queue/atlas_queue_service.py.

The queue service runs as a subprocess bound to 127.0.0.1 on an ephemeral port, with a
temporary database, invented tokens and an invented policy, all under ``tmp_path``. No
real configuration is read. Nothing here says anything about a deployed queue.

Labels: EXPECTED_FUTURE_CONTRACT (implemented by this change; fails on the previous
service) or CURRENT_BEHAVIOR (positive controls that already held and must keep holding).
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NODE_DIR = REPO_ROOT / "infra" / "fleet-runtime" / "node"
SERVICE_SRC = REPO_ROOT / "infra" / "fleet-runtime" / "control" / "queue" / "atlas_queue_service.py"
ENGINE_SRC = NODE_DIR / "atlas_task_policy.py"
CLIENT_SRC = NODE_DIR / "atlas_queue_client.py"

TOK_ENF = "TEST-H-TOKEN-ENF"
TOK_ENF2 = "TEST-H-TOKEN-ENF2"
TOK_EXEMPT = "TEST-H-TOKEN-EXEMPT"
TOK_PLAIN = "TEST-H-TOKEN-PLAIN"


def _digest(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode()).hexdigest()


def make_tokens(plain_record: dict[str, Any] | None = None) -> dict[str, Any]:
    plain = {"node": "TEST-H-NODE-PLAIN", "roles": [], "submit": True}
    if plain_record is not None:
        plain.update(plain_record)
    return {
        _digest(TOK_ENF): {
            "node": "TEST-H-NODE-ENF",
            "roles": ["TEST-AUTHOR"],
            "submit": True,
            "policy_enforced": True,
        },
        _digest(TOK_ENF2): {"node": "TEST-H-NODE-ENF2", "roles": ["TEST-AUTHOR"], "submit": True},
        _digest(TOK_EXEMPT): {
            "node": "TEST-H-NODE-EXEMPT",
            "roles": [],
            "submit": True,
            "policy_exempt": True,
        },
        _digest(TOK_PLAIN): plain,
    }


def make_policy() -> dict[str, Any]:
    return {
        "policy_id": "POLICY-H-QUEUE",
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
            "max_open_tasks_total": 200,
            "max_open_tasks_per_mission": 200,
        },
    }


_serial = itertools.count(1)


def policy_task(**overrides: Any) -> dict[str, Any]:
    n = next(_serial)
    task: dict[str, Any] = {
        "schema_version": 1,
        "task_id": f"TASK-HQ-{n:04d}",
        "idempotency_key": f"IDEM-HQ-{n:04d}",
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


def owner_task(task_id: str | None = None, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": 1,
        "task_id": task_id or f"OWNER-HQ-{next(_serial):04d}",
        "target_role": "TEST-ROLE-1",
        "outcome": "x",
        "authority_reference": "AUTH-INVENTED",
        "priority": 50,
    }
    body.update(overrides)
    return body


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class Service:
    def __init__(self, home: Path, port: int, proc: subprocess.Popen[bytes], logs: list[Any]):
        self.home, self.port, self.proc, self._logs = home, port, proc, logs
        self.policy_path = home / "policy.json"
        self.db_path = home / "db" / "queue.db"

    def call(self, method: str, path: str, token: str | None, body: Any = None) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data, method=method
        )
        req.add_header("Content-Type", "application/json")
        if token is not None:
            req.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def submit(self, body: Any, token: str) -> tuple[int, Any]:
        return self.call("POST", "/tasks", token, body)

    def _rows(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        con = sqlite3.connect(str(self.db_path), timeout=10)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(sql, args)]
        finally:
            con.close()

    def exec_sql(self, sql: str, args: tuple[Any, ...] = ()) -> None:
        con = sqlite3.connect(str(self.db_path), timeout=10)
        try:
            con.execute(sql, args)
            con.commit()
        finally:
            con.close()

    def task_count(self) -> int:
        return self._rows("SELECT count(*) n FROM tasks")[0]["n"]

    def task_row(self, task_id: str) -> dict[str, Any] | None:
        rows = self._rows("SELECT * FROM tasks WHERE task_id=?", (task_id,))
        return rows[0] if rows else None

    def audit(self) -> list[dict[str, Any]]:
        rows = self._rows("SELECT id, node, action, task_id, detail FROM audit ORDER BY id")
        for row in rows:
            row["detail"] = json.loads(row["detail"]) if row["detail"] else None
        return rows

    def allow_rows(self) -> list[dict[str, Any]]:
        return [
            r
            for r in self.audit()
            if r["action"] == "policy_decision" and (r["detail"] or {}).get("decision") == "ALLOW"
        ]

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        for f in self._logs:
            f.close()


@pytest.fixture
def make_service(tmp_path: Path) -> Any:
    started: list[Service] = []
    serial = itertools.count()

    def _make(
        tokens: dict[str, Any] | None = None, policy: Any = None, engine: bool = True
    ) -> Service:
        home = tmp_path / f"svc-{next(serial)}"
        home.mkdir()
        shutil.copy(SERVICE_SRC, home / SERVICE_SRC.name)
        if engine:
            shutil.copy(ENGINE_SRC, home / ENGINE_SRC.name)
        (home / "policy.json").write_text(
            json.dumps(policy if policy is not None else make_policy())
        )
        (home / "tokens.json").write_text(
            json.dumps(tokens if tokens is not None else make_tokens())
        )
        port = _free_port()
        logs = [open(home / "stdout.log", "wb"), open(home / "stderr.log", "wb")]  # noqa: SIM115
        env = {k: v for k, v in os.environ.items() if not k.startswith("ATLAS_")}
        env.update(
            {
                "PYTHONDONTWRITEBYTECODE": "1",
                "ATLAS_QUEUE_BIND": "127.0.0.1",
                "ATLAS_QUEUE_PORT": str(port),
                "ATLAS_QUEUE_DB": str(home / "db" / "queue.db"),
                "ATLAS_QUEUE_POLICY_FILE": str(home / "policy.json"),
                "ATLAS_QUEUE_TOKENS_FILE": str(home / "tokens.json"),
            }
        )
        proc = subprocess.Popen(
            [sys.executable, str(home / SERVICE_SRC.name)],
            stdout=logs[0],
            stderr=logs[1],
            env=env,
            cwd=str(home),
        )
        svc = Service(home, port, proc, logs)
        started.append(svc)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError((home / "stderr.log").read_text(errors="replace"))
            try:
                if svc.call("GET", "/health", None)[0] == 200:
                    return svc
            except OSError:
                pass
            time.sleep(0.05)
        raise RuntimeError("queue service did not become healthy")

    yield _make
    for svc in started:
        svc.stop()


@pytest.fixture
def svc(make_service: Any) -> Service:
    return make_service()


# --- P1-B: the submit path is decided by the authenticated identity's record ---------


# Token records without an explicit, boolean, correctly spelled exemption grant.
NOT_EXEMPT_RECORDS = {
    "flag-absent": {},
    "enforced-false": {"policy_enforced": False},
    "exempt-false": {"policy_exempt": False},
    "exempt-string-true": {"policy_exempt": "true"},
    "exempt-int-one": {"policy_exempt": 1},
    "exempt-list": {"policy_exempt": [True]},
    "exempt-misspelled": {"policy_exmpt": True},
    "exempt-wrong-case": {"Policy_Exempt": True},
    "exempt-and-enforced": {"policy_exempt": True, "policy_enforced": True},
    "exempt-and-enforced-truthy": {"policy_exempt": True, "policy_enforced": "yes"},
}


class TestIdentityBoundEnforcement:
    """EXPECTED_FUTURE_CONTRACT (implemented): P1-B. Absent, false or misspelled
    exemption means enforced; no body field selects the path."""

    @pytest.mark.parametrize("record", list(NOT_EXEMPT_RECORDS))
    def test_owner_form_envelope_from_non_exempt_identity_is_policy_checked(
        self, make_service: Any, record: str
    ) -> None:
        svc = make_service(tokens=make_tokens(NOT_EXEMPT_RECORDS[record]))
        code, body = svc.submit(owner_task(), TOK_PLAIN)
        assert code == 422, body
        assert body["decision"]["code"] == "SCHEMA"
        assert svc.task_count() == 0
        (row,) = svc.audit()
        assert row["action"] == "policy_decision"
        assert row["detail"]["decision"] == "DENY"
        assert row["detail"]["policy_enforcement"] == "ENFORCED"
        assert row["detail"]["authenticated_identity"] == "TEST-H-NODE-PLAIN"

    @pytest.mark.parametrize("record", list(NOT_EXEMPT_RECORDS))
    def test_non_exempt_identity_still_gets_policy_checked_envelopes_through(
        self, make_service: Any, record: str
    ) -> None:
        """Control: enforcement is not a blanket refusal."""
        svc = make_service(tokens=make_tokens(NOT_EXEMPT_RECORDS[record]))
        task = policy_task()
        code, body = svc.submit(task, TOK_PLAIN)
        assert code == 201, body
        assert body["policy_decision"]["decision"] == "ALLOW"

    def test_omitted_selector_field_does_not_bypass_policy(self, svc: Service) -> None:
        """Dropping author_node from a policy envelope is a SCHEMA denial with an audit row."""
        task = policy_task()
        del task["author_node"]
        code, body = svc.submit(task, TOK_ENF)
        assert code == 422
        assert "missing fields ['author_node']" in body["decision"]["reason"]
        assert svc.task_count() == 0
        (row,) = svc.audit()
        assert row["detail"]["decision"] == "DENY"
        assert row["detail"]["declared_author"] is None

    def test_explicitly_exempt_identity_uses_owner_path(self, svc: Service) -> None:
        """CURRENT_BEHAVIOR for an explicit grant: owner envelopes are accepted unchecked."""
        task = owner_task()
        code, body = svc.submit(task, TOK_EXEMPT)
        assert code == 201, body
        assert body["policy_decision"] is None
        (row,) = svc.audit()
        assert row["action"] == "submit"
        assert row["detail"]["policy_enforcement"] == "EXEMPT"
        assert row["detail"]["authenticated_identity"] == "TEST-H-NODE-EXEMPT"
        assert row["detail"]["autonomous"] is False

    def test_body_cannot_select_the_policy_path_for_an_exempt_identity(self, svc: Service) -> None:
        """The exempt path validates the owner envelope; author_node is just an unexpected field."""
        code, body = svc.submit(policy_task(), TOK_EXEMPT)
        assert code == 422
        assert "unexpected fields" in body["error"]
        assert svc.task_count() == 0
        assert svc.audit() == []

    def test_declared_author_is_recorded_apart_from_authenticated_identity(
        self, svc: Service
    ) -> None:
        task = policy_task()
        code, _ = svc.submit(task, TOK_ENF2)
        assert code == 201
        assert svc.task_row(task["task_id"])["submitted_by"] == "TEST-H-NODE-ENF2"
        decision_row, submit_row = svc.audit()
        assert [decision_row["action"], submit_row["action"]] == ["policy_decision", "submit"]
        for row in (decision_row, submit_row):
            detail = row["detail"]
            assert row["node"] == "TEST-H-NODE-ENF2"
            assert detail["authenticated_identity"] == "TEST-H-NODE-ENF2"
            assert detail["declared_author"] == "TEST-AUTHOR"
            assert detail["roles"] == ["TEST-AUTHOR"]
            assert detail["policy_enforcement"] == "ENFORCED"
            assert detail["authority_reference"] == "AUTH-TEST-001"

    def test_declared_author_naming_another_identity_is_denied_and_recorded(
        self, svc: Service
    ) -> None:
        code, body = svc.submit(policy_task(author_node="TEST-H-NODE-EXEMPT"), TOK_ENF)
        assert code == 422
        assert body["decision"]["code"] == "AUTHOR"
        (row,) = svc.audit()
        assert row["detail"]["authenticated_identity"] == "TEST-H-NODE-ENF"
        assert row["detail"]["declared_author"] == "TEST-H-NODE-EXEMPT"


# --- P1-A: canonical task ids on every submit path, before any state write ----------

UNSAFE_TASK_IDS = [
    "..",
    "a..b",
    "../state/x",
    "a/b",
    "a\\b",
    "C:\\tasks\\x",
    "a:b",
    "%2e%2e",
    "a%2Fb",
    "a\uff0fb",
    "a\u2215b",
    "T\u0430sk1",
    " TaskA",
    "TaskA\n",
    "a\x00b",
    "a\x7fb",
    "a\u200bb",
    ".TaskA",
    "-TaskA",
    "a" * 129,
]


class TestCanonicalTaskIdAdmission:
    """EXPECTED_FUTURE_CONTRACT (implemented): P1-A on both submit paths."""

    @pytest.mark.parametrize("bad_id", UNSAFE_TASK_IDS, ids=repr)
    def test_enforced_path_rejects_before_any_task_write_or_allow(
        self, svc: Service, bad_id: str
    ) -> None:
        control = policy_task()
        assert svc.submit(control, TOK_ENF)[0] == 201
        tasks_before, allow_before = svc.task_count(), svc.allow_rows()
        code, body = svc.submit(policy_task(task_id=bad_id), TOK_ENF)
        assert code == 422, body
        assert body["decision"]["code"] == "SCHEMA"
        assert svc.task_count() == tasks_before
        assert svc.allow_rows() == allow_before
        deny = svc.audit()[-1]
        assert deny["detail"]["decision"] == "DENY"
        assert deny["task_id"] is None, "a non-canonical id is never used as audit key material"
        assert deny["detail"]["task_id_repr"] == repr(bad_id)[:200]

    @pytest.mark.parametrize("bad_id", UNSAFE_TASK_IDS, ids=repr)
    def test_exempt_path_rejects_before_any_state_write(self, svc: Service, bad_id: str) -> None:
        control = owner_task()
        assert svc.submit(control, TOK_EXEMPT)[0] == 201
        tasks_before, audit_before = svc.task_count(), svc.audit()
        code, body = svc.submit(owner_task(bad_id), TOK_EXEMPT)
        assert code == 422
        assert body["error"] == "bad task_id"
        assert svc.task_count() == tasks_before
        assert svc.audit() == audit_before

    @pytest.mark.parametrize("bad_parent", ["../x", "a/b", "a..b", {"free": ["form"]}, ["x"], 7])
    def test_exempt_path_rejects_unsafe_parent_task_id(self, svc: Service, bad_parent: Any) -> None:
        code, body = svc.submit(owner_task(parent_task_id=bad_parent), TOK_EXEMPT)
        assert code == 422
        assert body["error"] == "bad parent_task_id"
        assert svc.task_count() == 0

    @pytest.mark.parametrize("token", [TOK_ENF, TOK_EXEMPT])
    @pytest.mark.parametrize("good_id", ["A", "a1", "Task-1.v2_x", "a.b.c", "a" * 128])
    def test_canonical_ids_stay_accepted(self, svc: Service, token: str, good_id: str) -> None:
        """CURRENT_BEHAVIOR positive control on both paths."""
        body = policy_task(task_id=good_id) if token == TOK_ENF else owner_task(good_id)
        code, resp = svc.submit(body, token)
        assert code == 201, resp
        assert svc.task_row(good_id) is not None

    def test_client_submit_route_is_covered(self, svc: Service, tmp_path: Path) -> None:
        """The node client's submit() reaches the same admission check."""
        token_file = tmp_path / "exempt.token"
        token_file.write_text(TOK_EXEMPT)
        code = (
            "import json, sys; sys.path.insert(0, sys.argv[1]); import atlas_queue_client as q;"
            "print(json.dumps(q.submit(json.loads(sys.argv[2]))))"
        )
        env = {k: v for k, v in os.environ.items() if not k.startswith("ATLAS_")}
        env.update(
            {
                "ATLAS_QUEUE_URL": f"http://127.0.0.1:{svc.port}",
                "ATLAS_QUEUE_TOKEN_FILE": str(token_file),
            }
        )
        cp = subprocess.run(
            [sys.executable, "-c", code, str(NODE_DIR), json.dumps(owner_task("../state/x"))],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
            check=False,
        )
        status, body = json.loads(cp.stdout)
        assert status == 422 and body["error"] == "bad task_id"
        assert svc.task_count() == 0


# --- Adopted semantics 1: an unusable policy is an explicit, audited refusal --------


def _write_fault(svc: Service, case: str) -> None:
    policy = make_policy()
    if case == "missing-file":
        svc.policy_path.unlink()
    elif case == "directory":
        svc.policy_path.unlink()
        svc.policy_path.mkdir()
    elif case == "bad-json":
        svc.policy_path.write_bytes(b'{"policy_id": ')
    elif case == "non-object":
        svc.policy_path.write_bytes(b"[]")
    elif case == "missing-limits":
        del policy["limits"]
        svc.policy_path.write_text(json.dumps(policy))
    elif case == "missing-mission-registry":
        del policy["mission_registry"]
        svc.policy_path.write_text(json.dumps(policy))
    elif case == "bad-regex":
        policy["hard_deny_patterns"] = [{"id": "HD-BAD", "re": "(unclosed"}]
        svc.policy_path.write_text(json.dumps(policy))
    elif case == "string-target-roles":
        policy["allowed_classes"]["VERIFY"]["target_roles"] = "TEST-ROLE-1"
        svc.policy_path.write_text(json.dumps(policy))
    else:
        raise AssertionError(case)


POLICY_FAULTS = {
    "missing-file": "POLICY_UNAVAILABLE",
    "directory": "POLICY_UNAVAILABLE",
    "bad-json": "POLICY_INVALID",
    "non-object": "POLICY_INVALID",
    "missing-limits": "POLICY_INVALID",
    "missing-mission-registry": "POLICY_INVALID",
    "bad-regex": "POLICY_INVALID",
    "string-target-roles": "POLICY_INVALID",
}


class TestPolicyFaultAdmission:
    """EXPECTED_FUTURE_CONTRACT (implemented): explicit fail-closed result, durable audit
    row with a distinct code, no task acceptance, service keeps serving."""

    @pytest.mark.parametrize("case", list(POLICY_FAULTS))
    def test_policy_fault_is_explicit_and_audited(self, svc: Service, case: str) -> None:
        _write_fault(svc, case)
        code, body = svc.submit(policy_task(target_role="TEST-ROLE-1"), TOK_ENF)
        assert code == 503
        assert "fails closed" in body["error"]
        assert body["decision"]["code"] == POLICY_FAULTS[case]
        assert svc.task_count() == 0
        (row,) = svc.audit()
        assert row["action"] == "policy_decision"
        assert row["detail"]["decision"] == "DENY"
        assert row["detail"]["code"] == POLICY_FAULTS[case]
        assert row["detail"]["authenticated_identity"] == "TEST-H-NODE-ENF"
        assert svc.call("GET", "/health", None)[0] == 200

    @pytest.mark.skipif(
        not hasattr(os, "geteuid") or os.geteuid() == 0,
        reason="needs an unprivileged POSIX uid so a mode-000 file is unreadable",
    )
    def test_unreadable_policy_is_explicit_and_audited(self, svc: Service) -> None:
        svc.policy_path.chmod(0)
        try:
            code, body = svc.submit(policy_task(), TOK_ENF)
        finally:
            svc.policy_path.chmod(0o600)
        assert code == 503
        assert body["decision"]["code"] == "POLICY_UNAVAILABLE"
        assert svc.task_count() == 0
        assert svc.audit()[-1]["detail"]["code"] == "POLICY_UNAVAILABLE"

    def test_missing_engine_is_explicit_and_audited(self, make_service: Any) -> None:
        svc = make_service(engine=False)
        code, body = svc.submit(policy_task(), TOK_ENF)
        assert code == 503
        assert "policy engine unavailable" in body["error"]
        (row,) = svc.audit()
        assert row["detail"]["code"] == "POLICY_ENGINE_UNAVAILABLE"
        assert svc.task_count() == 0

    def test_exempt_path_does_not_read_the_policy(self, svc: Service) -> None:
        """CURRENT_BEHAVIOR for an explicit grant: the break-glass path stays usable."""
        _write_fault(svc, "bad-json")
        assert svc.submit(owner_task(), TOK_EXEMPT)[0] == 201


# --- Adjacent P2: an ALLOW is never recorded without its task, a DENY is never lost --


class TestAuditIntegrity:
    """EXPECTED_FUTURE_CONTRACT (implemented)."""

    def test_object_parent_task_id_is_denied_without_allow_row(self, svc: Service) -> None:
        code, body = svc.submit(policy_task(parent_task_id={"free": ["form"]}), TOK_ENF)
        assert code == 422
        assert body["decision"]["code"] == "SCHEMA"
        assert svc.allow_rows() == []
        assert svc.task_count() == 0

    def test_terminal_same_key_conflict_records_no_allow_row(self, svc: Service) -> None:
        first = policy_task(idempotency_key="K-FAILED")
        assert svc.submit(first, TOK_ENF)[0] == 201
        svc.exec_sql("UPDATE tasks SET state='FAILED' WHERE task_id=?", (first["task_id"],))
        allow_before = svc.allow_rows()
        code, _ = svc.submit(policy_task(idempotency_key="K-FAILED"), TOK_ENF)
        assert code == 409
        assert svc.allow_rows() == allow_before
        last = svc.audit()[-1]
        assert last["action"] == "submit_rejected"
        assert last["detail"]["rejected"] == "idempotency_key already used"

    @pytest.mark.parametrize("bad_id", [["x"], {"a": 1}, 7, None])
    def test_non_string_task_id_deny_is_recorded(self, svc: Service, bad_id: Any) -> None:
        code, body = svc.submit(policy_task(task_id=bad_id), TOK_ENF)
        assert code == 422
        assert body["decision"]["decision"] == "DENY"
        (row,) = svc.audit()
        assert row["task_id"] is None
        assert row["detail"]["decision"] == "DENY"

    def test_allow_is_committed_with_its_task(self, svc: Service) -> None:
        """CURRENT_BEHAVIOR positive control: a legitimate submission is accepted and
        its ALLOW and submit rows name the stored task."""
        task = policy_task()
        code, body = svc.submit(task, TOK_ENF)
        assert code == 201
        assert body["policy_decision"]["decision"] == "ALLOW"
        assert svc.task_row(task["task_id"])["state"] == "READY"
        assert [(r["action"], r["task_id"]) for r in svc.audit()] == [
            ("policy_decision", task["task_id"]),
            ("submit", task["task_id"]),
        ]


# --- Client: an invalid policy reaches the engine's explicit denial ------------------


def _run_author(
    tmp_path: Path, port: int, task: dict[str, Any], policy_bytes: bytes
) -> subprocess.CompletedProcess[str]:
    (tmp_path / "task.json").write_text(json.dumps(task))
    (tmp_path / "author-policy.json").write_bytes(policy_bytes)
    (tmp_path / "author.token").write_text(TOK_ENF)
    env = {k: v for k, v in os.environ.items() if not k.startswith("ATLAS_")}
    env.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "ATLAS_QUEUE_URL": f"http://127.0.0.1:{port}",
            "ATLAS_QUEUE_TOKEN_FILE": str(tmp_path / "author.token"),
            "ATLAS_TASK_POLICY_FILE": str(tmp_path / "author-policy.json"),
            "ATLAS_AUTHORING_DECISIONS_DIR": str(tmp_path / "decisions"),
        }
    )
    return subprocess.run(
        [sys.executable, str(CLIENT_SRC), "author", str(tmp_path / "task.json")],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


class TestClientAuthor:
    """EXPECTED_FUTURE_CONTRACT (implemented)."""

    @pytest.mark.parametrize(
        "payload", [b"{}", b"[]", b'{"policy_id": '], ids=["empty-object", "list", "bad-json"]
    )
    def test_invalid_local_policy_is_an_explicit_recorded_deny(
        self, svc: Service, tmp_path: Path, payload: bytes
    ) -> None:
        task = policy_task()
        cp = _run_author(tmp_path, svc.port, task, payload)
        assert cp.returncode == 3, cp.stderr
        out = json.loads(cp.stdout)
        assert out["decision"] == "DENY"
        assert out["reason"].startswith("POLICY_INVALID")
        assert out["server"] is None
        assert len(list((tmp_path / "decisions").glob("*.json"))) == 1
        assert svc.audit() == []

    def test_server_policy_fault_is_a_recorded_deny_not_a_drop(
        self, svc: Service, tmp_path: Path
    ) -> None:
        _write_fault(svc, "bad-json")
        cp = _run_author(tmp_path, svc.port, policy_task(), json.dumps(make_policy()).encode())
        assert cp.returncode == 3, cp.stderr
        out = json.loads(cp.stdout)
        assert out["reason"].startswith("server rejected: policy engine unavailable")
        assert out["server"]["http"] == 503
        assert len(list((tmp_path / "decisions").glob("*.json"))) == 1
