"""Reproducer tests for the fleet queue/policy trust boundary (lane KIMI-FLEET-REPRO-001).

Synthetic, local-only characterisation of ``infra/fleet-runtime`` at base 2deff6de.
Every policy, token, node, mission and task identifier below is invented for these
tests. The queue service runs only as a subprocess bound to 127.0.0.1 on an
ephemeral port with a temporary database and is terminated by the fixture; every
file touched lives under ``tmp_path``. Each test carries exactly one label on the
first line of its docstring: CURRENT_BEHAVIOR (held at base and still holds) or
EXPECTED_FUTURE_CONTRACT (the owner-adopted target contract).

Converted by lane H2 in a separate, labelled commit after the hardening landed. At
base 5aec1653 the EXPECTED_FUTURE_CONTRACT tests were strict xfail; their markers are
removed because the contract is now implemented. CURRENT_BEHAVIOR tests that pinned
behaviour the hardening removed are re-labelled EXPECTED_FUTURE_CONTRACT and assert
the fail-closed result; each docstring quotes the base behaviour it used to pin.
Owner-path tests submit with TOK_EXEMPT, an identity whose record carries the
explicit ``policy_exempt`` grant: an identity without it (TOK_OWN) is now
policy-checked.
"""

from __future__ import annotations

import copy
import hashlib
import http.client
import importlib.util
import itertools
import json
import os
import random
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NODE_DIR = REPO_ROOT / "infra" / "fleet-runtime" / "node"
QUEUE_DIR = REPO_ROOT / "infra" / "fleet-runtime" / "control" / "queue"
SERVICE_SRC = QUEUE_DIR / "atlas_queue_service.py"
ENGINE_SRC = NODE_DIR / "atlas_task_policy.py"
CLIENT_SRC = NODE_DIR / "atlas_queue_client.py"
SUPERVISOR_SRC = NODE_DIR / "atlas_mission_supervisor.py"

sys.path.insert(0, str(NODE_DIR))

import atlas_queue_client as queue_client  # noqa: E402
import atlas_task_policy as policy_engine  # noqa: E402

NOW = datetime(2026, 10, 6, 12, 0, 0, tzinfo=UTC)

TOK_ENF = "TEST-TOKEN-ENF-0001"
TOK_ENF2 = "TEST-TOKEN-ENF2-0005"
TOK_OWN = "TEST-TOKEN-OWN-0002"
TOK_WRK = "TEST-TOKEN-WRK-0003"
TOK_INTAKE = "TEST-TOKEN-INTAKE-0004"
TOK_EXEMPT = "TEST-TOKEN-EXEMPT-0006"

REQUIRED_POLICY_KEYS = [
    "required_fields",
    "limits",
    "author_role",
    "denied_classes",
    "allowed_classes",
    "authority_registry",
    "mission_registry",
    "hard_deny_patterns",
    "verdict_dictation_patterns",
    "dedupe",
]


def _digest(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode()).hexdigest()


def default_tokens() -> dict[str, Any]:
    return {
        _digest(TOK_ENF): {
            "node": "TEST-NODE-ENF",
            "roles": ["TEST-AUTHOR"],
            "submit": True,
            "policy_enforced": True,
        },
        _digest(TOK_OWN): {"node": "TEST-NODE-OWN", "roles": [], "submit": True},
        _digest(TOK_WRK): {"node": "TEST-NODE-WRK", "roles": ["TEST-ROLE-1"]},
        _digest(TOK_INTAKE): {"node": "TEST-NODE-INTAKE", "roles": [], "mission_intake": True},
        _digest(TOK_EXEMPT): {
            "node": "TEST-NODE-EXEMPT",
            "roles": [],
            "submit": True,
            "policy_exempt": True,
        },
    }


def policy_p0(**overrides: Any) -> dict[str, Any]:
    """The invented P0 policy from REPRODUCERS.md (14 required fields as in the synthetic suite)."""
    policy: dict[str, Any] = {
        "policy_id": "POLICY-TEST-P0",
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
        "optional_fields": ["parent_task_id"],
        "denied_classes": ["DEPLOY"],
        "allowed_classes": {
            "VERIFY": {"target_roles": ["TEST-ROLE-1"], "no_verdict_dictation": True},
            "CANARY": {"target_roles": ["TEST-ROLE-1"], "synthetic_only": True},
        },
        "authority_registry": {
            "AUTH-TEST-001": {"status": "current", "subject": "TEST-ROLE-1"},
        },
        "mission_registry": {
            "MISSION-TEST-001": {"status": "current"},
            "MISSION-TEST-SUPERSEDED": {"status": "superseded"},
        },
        "hard_deny_patterns": [{"id": "HD-SECRET", "re": "password|secret|deploy to production"}],
        "verdict_dictation_patterns": ["\\bPASS\\b"],
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
    }
    policy.update(overrides)
    return policy


_task_serial = itertools.count(1)


def task_t0(**overrides: Any) -> dict[str, Any]:
    """The invented T0 task envelope from REPRODUCERS.md with unique identity per call."""
    n = next(_task_serial)
    task: dict[str, Any] = {
        "schema_version": 1,
        "task_id": f"TASK-T0-{n:04d}",
        "idempotency_key": f"IDEM-T0-{n:04d}",
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


def owner_body(task_id: str = "O-1", **overrides: Any) -> dict[str, Any]:
    """The item-12 owner-form envelope: no author_node, invented authority."""
    body: dict[str, Any] = {
        "schema_version": 1,
        "task_id": task_id,
        "target_role": "ANY-ROLE-AT-ALL",
        "outcome": "x",
        "authority_reference": "INVENTED-NOT-IN-ANY-REGISTRY",
        "priority": 50,
    }
    body.update(overrides)
    return body


def without(mapping: dict[str, Any], key: str) -> dict[str, Any]:
    shrunk = copy.deepcopy(mapping)
    shrunk.pop(key, None)
    return shrunk


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class QueueService:
    """Handle for one queue-service subprocess and its temporary home directory."""

    def __init__(
        self,
        home: Path,
        port: int,
        proc: subprocess.Popen[bytes],
        out_f: Any,
        err_f: Any,
    ) -> None:
        self.home = home
        self.port = port
        self.proc = proc
        self._out_f = out_f
        self._err_f = err_f
        self.policy_path = home / "policy.json"
        self.tokens_path = home / "tokens.json"
        self.db_path = home / "db" / "queue.db"
        self._stopped = False

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def request(
        self,
        method: str,
        path: str,
        token: str | None = None,
        body: Any = None,
        timeout: float = 10.0,
    ) -> tuple[int, Any]:
        data = json.dumps(body, sort_keys=True).encode() if body is not None else None
        req = urllib.request.Request(self.url(path), data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token is not None:
            req.add_header("Authorization", "Bearer " + token)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")

    def call(
        self,
        method: str,
        path: str,
        token: str | None = None,
        body: Any = None,
        timeout: float = 10.0,
    ) -> tuple[int, Any]:
        try:
            return self.request(method, path, token, body, timeout)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def post(self, path: str, body: Any, token: str) -> tuple[int, Any]:
        return self.call("POST", path, token, body)

    def get(self, path: str, token: str | None = None) -> tuple[int, Any]:
        return self.call("GET", path, token)

    def drop(self, method: str, path: str, token: str | None = None, body: Any = None) -> Exception:
        """Run a request expected to get no HTTP response; return the transport exception.

        Only a connection closed without a response counts as a drop. A completed HTTP
        exchange of any status is not a drop, and neither is a refused connection, a
        timeout or an unparseable body: those exceptions escape to the test.
        """
        try:
            self.request(method, path, token, body)
        except urllib.error.HTTPError as e:
            raise AssertionError(
                f"expected no HTTP response from {method} {path}, got HTTP {e.code}"
            ) from e
        except (http.client.RemoteDisconnected, ConnectionResetError) as exc:
            return exc
        raise AssertionError(f"expected no HTTP response from {method} {path}, request completed")

    def write_policy(self, policy: Any) -> None:
        self.policy_path.write_text(json.dumps(policy, sort_keys=True))

    def write_tokens(self, tokens: Any) -> None:
        self.tokens_path.write_text(json.dumps(tokens, sort_keys=True))

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path), timeout=10)
        con.row_factory = sqlite3.Row
        return con

    def counts(self) -> tuple[int, int]:
        with self._connect() as con:
            tasks = con.execute("SELECT count(*) FROM tasks").fetchone()[0]
            audit = con.execute("SELECT count(*) FROM audit").fetchone()[0]
        return tasks, audit

    def audit_rows(self) -> list[dict[str, Any]]:
        with self._connect() as con:
            return [
                dict(r)
                for r in con.execute(
                    "SELECT id, at, node, action, task_id, detail FROM audit ORDER BY id"
                )
            ]

    def task_row(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def exec_sql(self, sql: str, args: tuple[Any, ...] = ()) -> None:
        with self._connect() as con:
            con.execute(sql, args)
            con.commit()

    def stderr(self) -> str:
        try:
            return (self.home / "stderr.log").read_text(errors="replace")
        except OSError:
            return ""

    def wait_stderr(self, needle: str, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if needle in self.stderr():
                return True
            time.sleep(0.05)
        return False

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        self._out_f.close()
        self._err_f.close()


def _spawn_service(home: Path, extra_env: dict[str, str] | None) -> QueueService:
    port = _free_port()
    out_f = open(home / "stdout.log", "wb")  # noqa: SIM115 - closed in QueueService.stop()
    err_f = open(home / "stderr.log", "wb")  # noqa: SIM115 - closed in QueueService.stop()
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
    if extra_env:
        env.update(extra_env)
    proc = subprocess.Popen(
        [sys.executable, str(home / SERVICE_SRC.name)],
        stdout=out_f,
        stderr=err_f,
        env=env,
        cwd=str(home),
    )
    svc = QueueService(home, port, proc, out_f, err_f)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            svc.stop()
            raise RuntimeError(
                f"queue service exited during startup (rc={proc.returncode}): {svc.stderr()}"
            )
        try:
            code, _ = svc.get("/health")
            if code == 200:
                return svc
        except Exception:
            pass
        time.sleep(0.05)
    svc.stop()
    raise RuntimeError("queue service did not become healthy on 127.0.0.1")


@pytest.fixture
def make_service(tmp_path: Path) -> Any:
    started: list[QueueService] = []
    serial = itertools.count()

    def _make(
        engine: bool = True,
        extra_env: dict[str, str] | None = None,
        policy: Any = None,
        tokens: Any = None,
    ) -> QueueService:
        home = tmp_path / f"svc-{next(serial)}"
        home.mkdir()
        shutil.copy(SERVICE_SRC, home / SERVICE_SRC.name)
        if engine:
            shutil.copy(ENGINE_SRC, home / ENGINE_SRC.name)
        (home / "policy.json").write_text(
            json.dumps(policy if policy is not None else policy_p0(), sort_keys=True)
        )
        (home / "tokens.json").write_text(
            json.dumps(tokens if tokens is not None else default_tokens(), sort_keys=True)
        )
        svc = _spawn_service(home, extra_env)
        started.append(svc)
        return svc

    yield _make
    for svc in started:
        svc.stop()


@pytest.fixture
def svc(make_service: Any) -> QueueService:
    return make_service()


@pytest.fixture
def loaded_policy(tmp_path: Path) -> dict[str, Any]:
    path = tmp_path / "engine-policy.json"
    path.write_text(json.dumps(policy_p0(), sort_keys=True))
    return policy_engine.load_policy(path)


def run_author_cli(
    tmp_path: Path,
    *,
    port: int,
    task: dict[str, Any],
    policy: dict[str, Any] | None = None,
    token: str = TOK_ENF,
    decisions_dir: Path | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    task_path = tmp_path / "author-task.json"
    task_path.write_text(json.dumps(task, sort_keys=True))
    policy_path = tmp_path / "author-policy.json"
    policy_path.write_text(
        json.dumps(policy if policy is not None else policy_p0(), sort_keys=True)
    )
    token_path = tmp_path / "author.token"
    token_path.write_text(token)
    if decisions_dir is None:
        decisions_dir = tmp_path / "decisions"
    env = {k: v for k, v in os.environ.items() if not k.startswith("ATLAS_")}
    env.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "ATLAS_QUEUE_URL": f"http://127.0.0.1:{port}",
            "ATLAS_QUEUE_TOKEN_FILE": str(token_path),
            "ATLAS_TASK_POLICY_FILE": str(policy_path),
            "ATLAS_AUTHORING_DECISIONS_DIR": str(decisions_dir),
        }
    )
    cp = subprocess.run(
        [sys.executable, str(CLIENT_SRC), "author", str(task_path)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return cp, decisions_dir


def assert_policy_refusal(
    svc: QueueService,
    before: tuple[int, int],
    code: int,
    body: Any,
    expect: str = "POLICY_INVALID",
) -> None:
    """An unusable policy is an explicit 503 with a distinct code, exactly one
    policy_decision audit row carrying that code, and no task row."""
    assert code == 503, body
    assert "fails closed" in body["error"]
    assert body["decision"]["code"] == expect
    assert svc.counts() == (before[0], before[1] + 1)
    row = svc.audit_rows()[-1]
    assert row["action"] == "policy_decision"
    detail = json.loads(row["detail"])
    assert detail["decision"] == "DENY"
    assert detail["code"] == expect


# ---------------------------------------------------------------------------
# Service-level reproducers 1-27
# ---------------------------------------------------------------------------


def test_r01_missing_policy_enforced_submit_503(svc: QueueService) -> None:
    """EXPECTED_FUTURE_CONTRACT
    R-MISSING (base: 503, rows +0/+0): deleted policy file -> 503 'fails closed',
    POLICY_UNAVAILABLE, one audit row, no task."""
    svc.policy_path.unlink()
    before = svc.counts()
    code, body = svc.post("/tasks", task_t0(), TOK_ENF)
    assert_policy_refusal(svc, before, code, body, "POLICY_UNAVAILABLE")


def test_r02_badjson_policy_drops_connection(svc: QueueService) -> None:
    """EXPECTED_FUTURE_CONTRACT
    R-BADJSON (base: no HTTP response, rows +0/+0, stderr JSONDecodeError): invalid JSON
    policy -> explicit 503 POLICY_INVALID with an audit row; /health 200."""
    svc.policy_path.write_bytes(b'{"policy_id": ')
    before = svc.counts()
    code, body = svc.post("/tasks", task_t0(), TOK_ENF)
    assert_policy_refusal(svc, before, code, body)
    code, _ = svc.get("/health")
    assert code == 200


@pytest.mark.parametrize("payload", [b"[]", b'"x"', b"7", b"null"])
def test_r03_nonobject_policy_drops_connection(svc: QueueService, payload: bytes) -> None:
    """EXPECTED_FUTURE_CONTRACT
    R-NONOBJECT (base: no HTTP response, TypeError): non-object JSON policy -> explicit
    503 POLICY_INVALID with an audit row."""
    svc.policy_path.write_bytes(payload)
    before = svc.counts()
    code, body = svc.post("/tasks", task_t0(), TOK_ENF)
    assert_policy_refusal(svc, before, code, body)


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="reproducer needs an unprivileged uid so a mode-000 policy file is unreadable",
)
def test_r04_unreadable_policy_drops_connection(svc: QueueService) -> None:
    """EXPECTED_FUTURE_CONTRACT
    R-UNREADABLE (base: no HTTP response, PermissionError): mode-000 policy file ->
    explicit 503 POLICY_UNAVAILABLE with an audit row."""
    svc.policy_path.chmod(0)
    try:
        before = svc.counts()
        code, body = svc.post("/tasks", task_t0(), TOK_ENF)
        assert_policy_refusal(svc, before, code, body, "POLICY_UNAVAILABLE")
    finally:
        svc.policy_path.chmod(0o600)


@pytest.mark.parametrize("key", REQUIRED_POLICY_KEYS)
def test_r05_missing_policy_key_drops_connection(svc: QueueService, key: str) -> None:
    """EXPECTED_FUTURE_CONTRACT
    R-MISSINGKEY (base: no HTTP response, KeyError '<key>'): P0 minus one top-level key ->
    explicit 503 POLICY_INVALID naming the key, with an audit row."""
    svc.write_policy(without(policy_p0(), key))
    before = svc.counts()
    code, body = svc.post("/tasks", task_t0(), TOK_ENF)
    assert_policy_refusal(svc, before, code, body)
    assert key in body["decision"]["reason"]


def test_r06_lazy_limits_schema_version_denied_first(svc: QueueService) -> None:
    """EXPECTED_FUTURE_CONTRACT
    R-LAZY (base: 422 'schema_version must be 1' before the missing limits were noticed):
    the policy is validated before any task check -> 503 POLICY_INVALID."""
    svc.write_policy(without(policy_p0(), "limits"))
    before = svc.counts()
    code, body = svc.post("/tasks", task_t0(schema_version=2), TOK_ENF)
    assert_policy_refusal(svc, before, code, body)
    assert "limits" in body["decision"]["reason"]


@pytest.mark.parametrize("field", ["policy_id", "policy_version"])
def test_r07_missing_provenance_field_allows_with_null(svc: QueueService, field: str) -> None:
    """EXPECTED_FUTURE_CONTRACT
    R-PROVENANCE-NULL (base: 201 ALLOW with that field null): P0 minus
    policy_id/policy_version -> 503 POLICY_INVALID."""
    svc.write_policy(without(policy_p0(), field))
    before = svc.counts()
    code, body = svc.post("/tasks", task_t0(), TOK_ENF)
    assert_policy_refusal(svc, before, code, body)
    assert field in body["decision"]["reason"]


def test_r08_target_roles_string_substring_widening(svc: QueueService) -> None:
    """EXPECTED_FUTURE_CONTRACT
    R-SUBSTRING-WIDEN (base: role 'ROLE' got 201 ALLOW): target_roles as bare string ->
    503 POLICY_INVALID; a wrong type never widens."""
    policy = policy_p0()
    policy["allowed_classes"]["VERIFY"]["target_roles"] = "TEST-ROLE-1"
    policy["authority_registry"]["AUTH-TEST-001"] = {"status": "current"}
    svc.write_policy(policy)
    before = svc.counts()
    code, body = svc.post("/tasks", task_t0(target_role="ROLE"), TOK_ENF)
    assert_policy_refusal(svc, before, code, body)


def test_r09_badregex_order_dependent(svc: QueueService) -> None:
    """EXPECTED_FUTURE_CONTRACT
    R-BADREGEX (base: order-dependent drop / 422 HD-OK / 422 MISSION_UNKNOWN): a
    non-compiling pattern makes every submission 503 POLICY_INVALID, whatever the task."""
    svc.write_policy(
        policy_p0(
            hard_deny_patterns=[
                {"id": "HD-OK", "re": "password"},
                {"id": "HD-BAD", "re": "(unclosed"},
            ]
        )
    )
    for task in (task_t0(), task_t0(outcome="print the password"), task_t0(mission_id="NOPE")):
        before = svc.counts()
        code, body = svc.post("/tasks", task, TOK_ENF)
        assert_policy_refusal(svc, before, code, body)


def test_r10_badregex_verdict_patterns_class_dependent(svc: QueueService) -> None:
    """EXPECTED_FUTURE_CONTRACT
    R-BADREGEX-CLASS (base: VERIFY dropped, CANARY 201 ALLOW): a bad verdict_dictation
    pattern is 503 POLICY_INVALID for every class."""
    svc.write_policy(policy_p0(verdict_dictation_patterns=["[bad"]))
    for task in (task_t0(), task_t0(task_class="CANARY", scope="synthetic fixture")):
        before = svc.counts()
        code, body = svc.post("/tasks", task, TOK_ENF)
        assert_policy_refusal(svc, before, code, body)


def test_r11_hard_deny_entry_without_id(svc: QueueService) -> None:
    """EXPECTED_FUTURE_CONTRACT
    R-DENY-ID-MISSING (base: clean task 201, matching task dropped with KeyError 'id'):
    an id-less hard_deny entry is 503 POLICY_INVALID for every task."""
    svc.write_policy(policy_p0(hard_deny_patterns=[{"re": "password"}]))
    for task in (task_t0(), task_t0(outcome="rotate the password")):
        before = svc.counts()
        code, body = svc.post("/tasks", task, TOK_ENF)
        assert_policy_refusal(svc, before, code, body)


def test_r12_owner_path_never_reads_policy(svc: QueueService) -> None:
    """EXPECTED_FUTURE_CONTRACT
    R-OWNER-PATH (base: OWN without any flag got 201 unchecked): an identity without the
    explicit grant is policy-checked (here: 503 on the invalid policy, no task); only the
    explicitly exempt identity takes the owner path, which does not read the policy."""
    svc.policy_path.write_bytes(b'{"policy_id": ')
    before = svc.counts()
    code, body = svc.post("/tasks", owner_body(), TOK_OWN)
    assert_policy_refusal(svc, before, code, body)
    code, body = svc.post("/tasks", owner_body(), TOK_EXEMPT)
    assert code == 201
    assert body["policy_decision"] is None
    row = svc.task_row("O-1")
    assert row is not None
    assert row["policy_decision"] is None
    detail = json.loads(svc.audit_rows()[-1]["detail"])
    assert svc.audit_rows()[-1]["action"] == "submit"
    assert detail["autonomous"] is False
    assert detail["policy_enforcement"] == "EXEMPT"


def test_r13_enforced_identity_without_author_node_403(svc: QueueService) -> None:
    """EXPECTED_FUTURE_CONTRACT
    R-ENFORCED-NO-AUTHOR (base: 403, rows +0/+0): item-12 body as ENF is evaluated by the
    policy -> 422 SCHEMA DENY with one audit row, no task."""
    before = svc.counts()
    code, body = svc.post("/tasks", owner_body(), TOK_ENF)
    assert code == 422
    assert body["decision"]["code"] == "SCHEMA"
    assert svc.counts() == (before[0], before[1] + 1)


def test_r14_author_node_not_bound_to_identity(svc: QueueService) -> None:
    """CURRENT_BEHAVIOR
    R-AUTHOR-UNBOUND: second enforced identity, different node -> 201 ALLOW; submitted_by
    differs from author_node."""
    tokens = default_tokens()
    tokens[_digest(TOK_ENF2)] = {
        "node": "TEST-NODE-ENF2",
        "roles": ["TEST-AUTHOR"],
        "submit": True,
        "policy_enforced": True,
    }
    svc.write_tokens(tokens)
    task = task_t0()
    code, _ = svc.post("/tasks", task, TOK_ENF2)
    assert code == 201
    row = svc.task_row(task["task_id"])
    assert row is not None
    assert row["submitted_by"] == "TEST-NODE-ENF2"
    assert row["submitted_by"] != task["author_node"]


def test_r15_task_id_traversal_enforced_accepts_owner_rejects(svc: QueueService) -> None:
    """EXPECTED_FUTURE_CONTRACT
    R-TASKID-PATH (base: traversal id 201 as ENF): traversal task_id -> 422 on the
    policy path and 422 'bad task_id' on the exempt path; no task row."""
    traversal_id = "../state/qp-traversal-proof"
    code, body = svc.post("/tasks", task_t0(task_id=traversal_id), TOK_ENF)
    assert code == 422
    assert body["decision"]["code"] == "SCHEMA"
    assert svc.task_row(traversal_id) is None
    code, body = svc.post("/tasks", owner_body(traversal_id), TOK_EXEMPT)
    assert code == 422
    assert "bad task_id" in body["error"]
    assert svc.counts()[0] == 0


def test_r16_created_at_not_scanned_by_hard_deny(svc: QueueService) -> None:
    """EXPECTED_FUTURE_CONTRACT
    R-UNSCANNED (base: hard-deny words in created_at got 201): created_at must be RFC 3339
    -> 422 SCHEMA; the same words in outcome -> 422 HARD_DENY."""
    code, body = svc.post(
        "/tasks",
        task_t0(created_at="deploy to production and print the password"),
        TOK_ENF,
    )
    assert code == 422
    assert body["decision"]["code"] == "SCHEMA"
    code, body = svc.post("/tasks", task_t0(outcome="deploy to production"), TOK_ENF)
    assert code == 422
    assert "HARD_DENY" in body["decision"]["reason"]
    assert svc.counts()[0] == 0


@pytest.mark.parametrize("created_at", ["banana", "2026-10-06T12:00:00", " x "])
def test_r17_created_at_any_string_accepted(svc: QueueService, created_at: str) -> None:
    """EXPECTED_FUTURE_CONTRACT
    R-CREATED-AT (base: 201 ALLOW): non-RFC-3339 created_at strings -> 422 SCHEMA."""
    code, body = svc.post("/tasks", task_t0(created_at=created_at), TOK_ENF)
    assert code == 422
    assert body["decision"]["code"] == "SCHEMA"
    assert "created_at" in body["decision"]["reason"]
    assert svc.counts()[0] == 0


def test_r18_allow_decision_without_task_row(svc: QueueService) -> None:
    """EXPECTED_FUTURE_CONTRACT
    R-ALLOW-NO-TASK-1 (base: no response, an ALLOW audit row with no task): dict
    parent_task_id -> 422 SCHEMA DENY, one DENY audit row, no ALLOW row, no task."""
    before = svc.counts()
    code, body = svc.post("/tasks", task_t0(parent_task_id={"free": ["form"]}), TOK_ENF)
    assert code == 422
    assert body["decision"]["code"] == "SCHEMA"
    assert svc.counts() == (before[0], before[1] + 1)
    assert allow_audit_rows(svc) == []


def test_r19_allow_decision_then_409_on_terminal_same_key(svc: QueueService) -> None:
    """EXPECTED_FUTURE_CONTRACT
    R-ALLOW-NO-TASK-2 (base: 409 after an ALLOW audit row): idempotency_key in FAILED
    state -> 409 with a submit_rejected row and no new ALLOW row; tasks +0."""
    first = task_t0(task_id="TASK-KFAILED-1", idempotency_key="K-FAILED")
    code, _ = svc.post("/tasks", first, TOK_ENF)
    assert code == 201
    svc.exec_sql("UPDATE tasks SET state='FAILED' WHERE task_id=?", (first["task_id"],))
    before = svc.counts()
    allow_before = allow_audit_rows(svc)
    code, _ = svc.post(
        "/tasks",
        task_t0(task_id="TASK-KFAILED-2", idempotency_key="K-FAILED"),
        TOK_ENF,
    )
    assert code == 409
    assert svc.counts() == (before[0], before[1] + 1)
    assert allow_audit_rows(svc) == allow_before
    row = svc.audit_rows()[-1]
    assert row["action"] == "submit_rejected"


def test_r20_deny_audit_lost_on_nonstring_task_id(svc: QueueService) -> None:
    """EXPECTED_FUTURE_CONTRACT
    R-DENY-LOST (base: no response, rows +0/+0, ProgrammingError): task_id as list ->
    422 DENY recorded in one audit row with a NULL task_id."""
    before = svc.counts()
    code, body = svc.post("/tasks", task_t0(task_id=["x"]), TOK_ENF)
    assert code == 422
    assert body["decision"]["decision"] == "DENY"
    assert svc.counts() == (before[0], before[1] + 1)
    row = svc.audit_rows()[-1]
    assert row["task_id"] is None
    assert json.loads(row["detail"])["decision"] == "DENY"


def test_r21_byte_identical_resubmit_denied_by_dedupe(svc: QueueService) -> None:
    """CURRENT_BEHAVIOR
    R-RESUBMIT: byte-identical second submit -> 422 DUPLICATE_OPEN instead of an idempotent 200."""
    task = task_t0()
    code_first, _ = svc.post("/tasks", task, TOK_ENF)
    code_second, body = svc.post("/tasks", task, TOK_ENF)
    assert code_first == 201
    assert code_second == 422
    assert "DUPLICATE_OPEN" in body["decision"]["reason"]


def test_r22_mission_registry_merged_from_db(svc: QueueService) -> None:
    """CURRENT_BEHAVIOR
    R-MISSION-OVERRIDE: owner-intake mission becomes authorable after /missions,
    closed after cancel."""
    today = datetime.now(UTC).strftime("%Y%m%d")
    expected_mid = f"OWNER-M-{today}-001"
    code, body = svc.post("/tasks", task_t0(mission_id=expected_mid), TOK_ENF)
    assert code == 422
    assert "MISSION_UNKNOWN" in body["decision"]["reason"]
    code, body = svc.post(
        "/missions", {"text": "synthetic goal", "envelope_id": "ENV-SYN-0001"}, TOK_INTAKE
    )
    assert code == 201
    assert body["mission_id"] == expected_mid
    code, body = svc.post("/tasks", task_t0(mission_id=expected_mid), TOK_ENF)
    assert code == 201
    assert body["policy_decision"]["decision"] == "ALLOW"
    code, _ = svc.post(f"/missions/{expected_mid}/cancel", {}, TOK_INTAKE)
    assert code == 200
    code, body = svc.post("/tasks", task_t0(mission_id=expected_mid), TOK_ENF)
    assert code == 422
    assert "MISSION_SUPERSEDED: mission status closed" in body["decision"]["reason"]


def test_r23_db_mission_row_reactivates_superseded_mission(svc: QueueService) -> None:
    """CURRENT_BEHAVIOR
    R-MISSION-COLLISION: a direct missions row with a superseded policy mission id -> 201 ALLOW."""
    svc.exec_sql(
        "INSERT INTO missions(mission_id,source,submitted_by,text,text_sha256,state,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
        (
            "MISSION-TEST-SUPERSEDED",
            "owner-interface",
            "TEST-NODE-INTAKE",
            "synthetic",
            "0" * 64,
            "ACCEPTED",
            "2026-10-06T00:00:00Z",
            "2026-10-06T00:00:00Z",
        ),
    )
    code, body = svc.post("/tasks", task_t0(mission_id="MISSION-TEST-SUPERSEDED"), TOK_ENF)
    assert code == 201
    assert body["policy_decision"]["decision"] == "ALLOW"


def test_r24_claim_does_not_recheck_policy(svc: QueueService) -> None:
    """CURRENT_BEHAVIOR
    R-CLAIM-NO-RECHECK: policy corrupted after ALLOW -> /claim still 200 with task and lease,
    no policy_decision key."""
    code, _ = svc.post("/tasks", task_t0(), TOK_ENF)
    assert code == 201
    svc.policy_path.write_bytes(b'{"policy_id": ')
    code, body = svc.post("/claim", {"role": "TEST-ROLE-1"}, TOK_WRK)
    assert code == 200
    assert body.get("task") is not None
    assert body.get("lease_id")
    assert "policy_decision" not in body


_VALID_UNTIL_CASES = [
    ("2099-01-01T00:00:00Z", 201, None),
    ("2020-01-01T00:00:00Z", 422, "AUTHORITY_EXPIRED"),
    ("2099-01-01T00:00:00", 422, "unparseable valid_until"),
    ("2099-01-01", 422, "unparseable valid_until"),
    ("soon", 422, "unparseable valid_until"),
    (4102444800, 422, "unparseable valid_until"),
    ("", 201, None),
]


@pytest.mark.parametrize("valid_until,expected_status,fragment", _VALID_UNTIL_CASES)
def test_r25_authority_valid_until_variants(
    svc: QueueService, valid_until: Any, expected_status: int, fragment: str | None
) -> None:
    """CURRENT_BEHAVIOR
    R-VALID-UNTIL: aware future and empty -> 201; past -> 422 expired;
    naive/garbage/numeric -> 422 'unparseable'."""
    policy = policy_p0()
    policy["authority_registry"]["AUTH-TEST-001"]["valid_until"] = valid_until
    svc.write_policy(policy)
    code, body = svc.post("/tasks", task_t0(), TOK_ENF)
    assert code == expected_status
    if fragment is None:
        assert body["policy_decision"]["decision"] == "ALLOW"
    else:
        assert fragment in body["decision"]["reason"]


def test_r26_tokens_file_missing_and_nonobject(svc: QueueService) -> None:
    """CURRENT_BEHAVIOR
    R-TOKENS: renamed tokens file -> 401; tokens content [] -> no response; service stays up."""
    svc.tokens_path.rename(svc.home / "tokens-renamed.json")
    code, _ = svc.post("/tasks", task_t0(), TOK_ENF)
    assert code == 401
    svc.write_tokens([])
    svc.drop("POST", "/tasks", TOK_ENF, task_t0())
    code, _ = svc.get("/health")
    assert code == 200


def test_r27_libdir_not_honoured_by_service(make_service: Any, tmp_path: Path) -> None:
    """EXPECTED_FUTURE_CONTRACT
    R-LIBDIR (base: ENF 503 without audit, OWN 201): service without the engine beside it
    ignores ATLAS_LIB_DIR: ENF and OWN (no grant) -> audited 503; only the explicitly
    exempt identity -> 201; quiet stderr."""
    engine_home = tmp_path / "engine-only"
    engine_home.mkdir()
    shutil.copy(ENGINE_SRC, engine_home / ENGINE_SRC.name)
    svc = make_service(engine=False, extra_env={"ATLAS_LIB_DIR": str(engine_home)})
    time.sleep(0.2)
    assert svc.stderr().strip() == ""
    for token in (TOK_ENF, TOK_OWN):
        code, body = svc.post("/tasks", task_t0(), token)
        assert code == 503
        assert "policy engine unavailable" in body["error"]
        assert json.loads(svc.audit_rows()[-1]["detail"])["code"] == "POLICY_ENGINE_UNAVAILABLE"
    assert svc.counts()[0] == 0
    code, body = svc.post("/tasks", owner_body(), TOK_EXEMPT)
    assert code == 201
    assert body["policy_decision"] is None


# ---------------------------------------------------------------------------
# Engine-level reproducers 28-30 (direct evaluate calls, pinned now)
# ---------------------------------------------------------------------------


def test_r28_synthetic_only_satisfied_by_free_text_word(loaded_policy: dict[str, Any]) -> None:
    """EXPECTED_FUTURE_CONTRACT
    R-SYNTHETIC-WORD (base: 'not synthetic' free text -> ALLOW): task text is not
    consulted; without execution_subject SYNTHETIC both tasks -> SYNTHETIC_REQUIRED."""
    for outcome in ("Restart the live production service; this is not synthetic",
                    "Restart the live service"):
        task = task_t0(task_class="CANARY", outcome=outcome, scope="production host")
        decision = policy_engine.evaluate(task, loaded_policy, [], NOW)
        assert decision["decision"] == "DENY"
        assert "SYNTHETIC_REQUIRED" in decision["reason"]


def test_r29_naive_now_makes_valid_until_unparseable(loaded_policy: dict[str, Any]) -> None:
    """CURRENT_BEHAVIOR
    R-NAIVE-NOW: aware future valid_until + naive now -> DENY 'unparseable valid_until'."""
    loaded_policy["authority_registry"]["AUTH-TEST-001"]["valid_until"] = "2099-01-01T00:00:00Z"
    decision = policy_engine.evaluate(task_t0(), loaded_policy, [], datetime(2026, 1, 1))
    assert decision["decision"] == "DENY"
    assert "unparseable valid_until" in decision["reason"]


def test_r30_open_tasks_shape_tolerated_or_crashes(loaded_policy: dict[str, Any]) -> None:
    """CURRENT_BEHAVIOR
    R-OPEN-SHAPE: list entry -> AttributeError; dict entry with state None and same key -> ALLOW."""
    with pytest.raises(AttributeError):
        policy_engine.evaluate(task_t0(), loaded_policy, [["row"]], NOW)
    task = task_t0()
    open_tasks = [
        {"task_id": "a", "idempotency_key": task["idempotency_key"], "state": None}
    ]
    decision = policy_engine.evaluate(task, loaded_policy, open_tasks, NOW)
    assert decision["decision"] == "ALLOW"


# ---------------------------------------------------------------------------
# Client reproducers 31-37
# ---------------------------------------------------------------------------


def test_r31_cli_author_server_drop_exit1(svc: QueueService, tmp_path: Path) -> None:
    """EXPECTED_FUTURE_CONTRACT
    R-CLI-DROP (base: exit 1, RemoteDisconnected, zero decision files): server policy
    invalid JSON -> explicit 503, author exits 3 with one recorded decision."""
    svc.policy_path.write_bytes(b'{"policy_id": ')
    cp, decisions_dir = run_author_cli(tmp_path, port=svc.port, task=task_t0())
    assert cp.returncode == 3, cp.stderr
    out = json.loads(cp.stdout)
    assert out["reason"].startswith("server rejected: policy engine unavailable")
    assert len(list(decisions_dir.glob("*.json"))) == 1


def test_r32_cli_author_server_503_recorded(svc: QueueService, tmp_path: Path) -> None:
    """CURRENT_BEHAVIOR
    R-CLI-503: server policy missing -> exit 3, reason starts
    'server rejected: policy engine unavailable', client_decision reduced to two keys."""
    svc.policy_path.unlink()
    cp, decisions_dir = run_author_cli(tmp_path, port=svc.port, task=task_t0())
    assert cp.returncode == 3
    out = json.loads(cp.stdout)
    assert out["decision"] == "DENY"
    assert out["reason"].startswith("server rejected: policy engine unavailable")
    files = list(decisions_dir.glob("*.json"))
    assert len(files) == 1
    record = json.loads(files[0].read_text())
    assert set(record["client_decision"].keys()) == {"decision", "reason"}


def test_r33_cli_author_local_deny_never_reaches_server(
    svc: QueueService, tmp_path: Path
) -> None:
    """CURRENT_BEHAVIOR
    R-CLI-STRICT: client policy denies VERIFY -> exit 3, server response null, server audit +0."""
    cp, _ = run_author_cli(
        tmp_path,
        port=svc.port,
        task=task_t0(),
        policy=policy_p0(denied_classes=["VERIFY"]),
    )
    assert cp.returncode == 3
    out = json.loads(cp.stdout)
    assert out["server"] is None
    assert "CLASS_DENIED" in out["reason"]
    assert svc.counts()[1] == 0


def test_r34_cli_author_local_policy_not_merged_with_server_missions(
    svc: QueueService, tmp_path: Path
) -> None:
    """CURRENT_BEHAVIOR
    R-CLI-MISSION: server-side ACTIVE owner mission is invisible to the client policy ->
    local MISSION_UNKNOWN, server audit +0."""
    today = datetime.now(UTC).strftime("%Y%m%d")
    mid = f"OWNER-M-{today}-007"
    svc.exec_sql(
        "INSERT INTO missions(mission_id,source,submitted_by,text,text_sha256,state,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
        (mid, "owner-interface", "TEST-NODE-INTAKE", "synthetic", "0" * 64, "ACTIVE",
         "2026-10-06T00:00:00Z", "2026-10-06T00:00:00Z"),
    )
    before = svc.counts()
    cp, _ = run_author_cli(tmp_path, port=svc.port, task=task_t0(mission_id=mid))
    assert cp.returncode == 3
    out = json.loads(cp.stdout)
    assert "MISSION_UNKNOWN" in out["reason"]
    assert svc.counts() == before


def test_r35_cli_author_get_tasks_failure_hides_duplicate(
    svc: QueueService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CURRENT_BEHAVIOR
    R-CLI-GETFAIL: GET /tasks wrapped to (500, {}) -> local ALLOW, server DENY
    DUPLICATE_OPEN reported as 'server rejected', client_decision two keys."""
    seed = task_t0(task_id="TASK-SEED-DUPKEY", idempotency_key="DUPKEY")
    code, _ = svc.post("/tasks", seed, TOK_ENF)
    assert code == 201
    token_path = tmp_path / "client.token"
    token_path.write_text(TOK_ENF)
    monkeypatch.setenv("ATLAS_QUEUE_URL", f"http://127.0.0.1:{svc.port}")
    monkeypatch.setenv("ATLAS_QUEUE_TOKEN_FILE", str(token_path))
    task_path = tmp_path / "task.json"
    task_path.write_text(
        json.dumps(task_t0(task_id="TASK-AUTH-DUPKEY", idempotency_key="DUPKEY"))
    )
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy_p0()))
    decisions_dir = tmp_path / "decisions"
    original_call = queue_client.call

    def wrapped(method: str, path: str, body: Any = None, timeout: int = 15) -> Any:
        if method == "GET" and path.startswith("/tasks"):
            return 500, {}
        return original_call(method, path, body, timeout)

    monkeypatch.setattr(queue_client, "call", wrapped)
    decision, record = queue_client.author(
        str(task_path), str(policy_path), str(decisions_dir)
    )
    assert decision["decision"] == "DENY"
    assert decision["reason"].startswith("server rejected: policy DENY DUPLICATE_OPEN")
    assert set(record["client_decision"].keys()) == {"decision", "reason"}
    assert len(list(decisions_dir.glob("*.json"))) == 1


def test_r36_cli_author_connection_refused(tmp_path: Path) -> None:
    """CURRENT_BEHAVIOR
    R-CLI-REFUSED: no listener -> exit 1, URLError traceback, zero decision files."""
    port = _free_port()
    cp, decisions_dir = run_author_cli(tmp_path, port=port, task=task_t0())
    assert cp.returncode == 1
    assert "URLError" in cp.stderr
    assert list(decisions_dir.glob("*.json")) == []


def test_r37_cli_author_decision_record_best_effort(
    svc: QueueService, tmp_path: Path
) -> None:
    """CURRENT_BEHAVIOR
    R-CLI-NOREC: decisions dir under a regular file -> exit 0 with 201 server-side,
    stderr warning, zero files."""
    blocker = tmp_path / "plain-file"
    blocker.write_text("not a directory")
    decisions_dir = blocker / "decisions"
    task = task_t0()
    cp, _ = run_author_cli(tmp_path, port=svc.port, task=task, decisions_dir=decisions_dir)
    assert cp.returncode == 0
    out = json.loads(cp.stdout)
    assert out["decision"] == "ALLOW"
    assert "warning: could not record decision" in cp.stderr
    assert not decisions_dir.exists()
    assert svc.task_row(task["task_id"]) is not None


# ---------------------------------------------------------------------------
# Supervisor reproducer 38 (import; writes only under tmp_path)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="the mission supervisor needs fcntl (POSIX-only), so it cannot run on Windows",
)
def test_r38_supervisor_queue_pull_task_id_traversal(
    svc: QueueService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EXPECTED_FUTURE_CONTRACT
    R-SUP-TRAVERSAL (base: claimed '..' ids wrote inbox files into <VAR>/state/ and
    overwrote state.json): such ids are refused at queue admission, so the supervisor has
    nothing to claim and <VAR>/state/ is untouched. The supervisor itself is unchanged."""
    first = task_t0(
        task_id="../state/qp-traversal-proof", idempotency_key="IDEM-TRAV-1", priority=100
    )
    second = task_t0(task_id="../state/state", idempotency_key="IDEM-TRAV-2", priority=100)
    for task in (first, second):
        code, body = svc.post("/tasks", task, TOK_ENF)
        assert code == 422, body
    assert svc.counts()[0] == 0
    etc = tmp_path / "etc"
    var = tmp_path / "var"
    (var / "state").mkdir(parents=True)
    etc.mkdir()
    sentinel = json.dumps({"sentinel": "TEST-SENTINEL", "status": "OBSERVE_ONLY"})
    (var / "state" / "state.json").write_text(sentinel)
    wrk_token_path = tmp_path / "wrk.token"
    wrk_token_path.write_text(TOK_WRK)
    monkeypatch.setenv("ATLAS_AUTONOMY_ETC", str(etc))
    monkeypatch.setenv("ATLAS_AUTONOMY_VAR", str(var))
    monkeypatch.setenv("ATLAS_LIB_DIR", str(NODE_DIR))
    monkeypatch.setenv("ATLAS_QUEUE_URL", f"http://127.0.0.1:{svc.port}")
    monkeypatch.setenv("ATLAS_QUEUE_TOKEN_FILE", str(wrk_token_path))
    spec = importlib.util.spec_from_file_location(
        "atlas_mission_supervisor_repro", SUPERVISOR_SRC
    )
    assert spec is not None and spec.loader is not None
    supervisor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(supervisor)
    while sys.path.count(str(NODE_DIR)) > 1:
        sys.path.remove(str(NODE_DIR))
    supervisor.queue_pull("TEST-ROLE-1")
    supervisor.queue_pull("TEST-ROLE-1")
    assert sorted(x.name for x in (var / "state").iterdir()) == ["state.json"]
    assert (var / "state" / "state.json").read_text() == sentinel


# ---------------------------------------------------------------------------
# Owner-adopted future contract (strict xfail at base 5aec1653; implemented by H2,
# markers removed). The owner submit path is the explicitly exempt identity.
#
# Canonical task-id contract (owner decision 2026-10-06): a task id is canonical only
# if its first character is [A-Za-z0-9] and every remaining character is
# [A-Za-z0-9._-]. Everything else must be rejected, fail closed, on every submit path,
# before any state write and before any ALLOW decision is recorded; no decoding,
# trimming or Unicode normalisation may turn a rejected representation into an
# accepted id. Owner decision 2026-10-07: the substring ".." is NOT canonical anywhere
# in a task id; a single "." stays allowed.
# ---------------------------------------------------------------------------


def allow_audit_rows(svc: QueueService) -> list[dict[str, Any]]:
    """Audit rows that record an ALLOW policy decision."""
    rows = []
    for row in svc.audit_rows():
        if row["action"] != "policy_decision":
            continue
        try:
            detail = json.loads(row["detail"] or "")
        except ValueError:
            continue
        if isinstance(detail, dict) and detail.get("decision") == "ALLOW":
            rows.append(row)
    return rows


def require_canonical_control(
    svc: QueueService, body: Any, token: str, control_id: str
) -> None:
    """Positive control for the item-1 refusal tests (review findings P2-1/P2-2).

    The same envelope with an explicitly canonical task id must first be accepted on the
    same request path (201 and a task row); only then may the case id be submitted. A
    dropped connection escapes as a transport error and a rejected control raises
    RuntimeError, so a broken submit path errors the test instead of passing without the
    id being evaluated, and an unrelated blanket refusal cannot satisfy the refusal
    assertions.
    """
    code, _ = svc.post("/tasks", body, token)
    if code != 201 or svc.task_row(control_id) is None:
        raise RuntimeError(
            f"control envelope with canonical task id {control_id!r} was not accepted "
            f"(HTTP {code}); the submit path itself is broken, so no refusal of the "
            "case id can be attributed to the id"
        )


# Contract-item-1 unsafe task-id corpus, one form per parametrized case. Base behaviour
# was probed before labelling: at base the enforced path accepts every form below with
# 201 + ALLOW, while the owner path already rejects exactly the forms containing '/'
# (422 'bad task_id', no state write) and accepts all the others. The
# path:dotdot-substring form pins the 2026-10-07 owner decision: interior ".." is not
# canonical even in an id whose characters are all otherwise allowed.
_UNSAFE_ID_FORMS = [
    ("path", "dot", "."),
    ("path", "dotdot", ".."),
    ("path", "traversal", "../state/qp-traversal-proof"),
    ("path", "embedded-dotdot", "a/../b"),
    ("path", "dotdot-substring", "a..b"),
    ("path", "embedded-dot", "a/./b"),
    ("path", "separator", "a/b"),
    ("path", "absolute", "/abs"),
    ("path", "trailing-separator", "a/"),
    ("path", "mixed-separators", "a/b\\c"),
    ("path", "backslash", "a\\b"),
    ("path", "windows-drive", "C:\\tasks\\x"),
    ("path", "unc", "\\\\server\\share\\x"),
    ("percent", "dotdot-encoded", "%2e%2e"),
    ("percent", "slash-encoded", "%2F"),
    ("percent", "nul-encoded", "a%00b"),
    ("percent", "lone-percent", "%"),
    ("percent", "harmless-encoded", "a%41"),
    ("non-ascii", "fullwidth-solidus", "a\uff0fb"),
    ("non-ascii", "division-slash", "a\u2215b"),
    ("non-ascii", "fullwidth-stop", "a\uff0eb"),
    ("non-ascii", "cyrillic-lookalike", "T\u0430sk1"),
    ("non-ascii", "combining-mark", "Ta\u0301sk1"),
    ("non-ascii", "nbsp", "a\u00a0b"),
    ("whitespace", "leading-space", " TaskA"),
    ("whitespace", "trailing-space", "TaskA "),
    ("whitespace", "leading-tab", "\tTaskA"),
    ("whitespace", "trailing-newline", "TaskA\n"),
    ("whitespace", "embedded-space", "Task A"),
    ("control", "nul", "a\x00b"),
    ("control", "c0-control", "a\x01b"),
    ("control", "del", "a\x7fb"),
    ("format", "zero-width-space", "a\u200bb"),
    ("format", "rtl-override", "a\u202eb"),
    ("first-char", "dot", ".TaskA"),
    ("first-char", "dash", "-TaskA"),
    ("first-char", "underscore", "_TaskA"),
] + [("ascii-symbol", f"symbol-{ch!r}", f"a{ch}b") for ch in ":~*?\"<|$`;&#+=@,({[!'"]

_UNSAFE_ID_FORMS_OWNER_XFAIL = [f for f in _UNSAFE_ID_FORMS if "/" not in f[2]]
_UNSAFE_ID_FORMS_OWNER_REJECTED = [f for f in _UNSAFE_ID_FORMS if "/" in f[2]]

# One explicitly canonical control id per case, unique per test (canonical alphabet, no
# ".."); submitted on the same request path before the case id (see
# require_canonical_control). The control envelope carries its own fresh idempotency
# key, so control and case never collide and stay well under the open-task limits.
_CONTROL_IDS_ENFORCED = {
    (id_class, form): f"CTRL-E{i:03d}"
    for i, (id_class, form, _) in enumerate(_UNSAFE_ID_FORMS)
}
_CONTROL_IDS_OWNER = {
    (id_class, form): f"CTRL-O{i:03d}"
    for i, (id_class, form, _) in enumerate(_UNSAFE_ID_FORMS_OWNER_XFAIL)
}


@pytest.mark.parametrize(
    "id_class,form,bad_id",
    _UNSAFE_ID_FORMS,
    ids=[f"{id_class}:{form}" for id_class, form, _ in _UNSAFE_ID_FORMS],
)
def test_fc1_enforced_path_rejects_unsafe_task_ids(
    svc: QueueService, id_class: str, form: str, bad_id: str
) -> None:
    """EXPECTED_FUTURE_CONTRACT
    Item 1: an unsafe task id is rejected (never normalised) on the enforced submit path
    before any state write and before any ALLOW decision is recorded."""
    control_id = _CONTROL_IDS_ENFORCED[(id_class, form)]
    require_canonical_control(svc, task_t0(task_id=control_id), TOK_ENF, control_id)
    tasks_before, _ = svc.counts()
    allow_before = allow_audit_rows(svc)
    code, _ = svc.post("/tasks", task_t0(task_id=bad_id), TOK_ENF)
    assert code not in (200, 201), (
        f"unsafe task id {bad_id!r} ({id_class}:{form}) must not be accepted"
    )
    assert svc.counts()[0] == tasks_before, "no task row may be written"
    assert allow_audit_rows(svc) == allow_before, (
        "rejection must happen before any ALLOW decision is recorded"
    )


@pytest.mark.parametrize(
    "id_class,form,bad_id",
    _UNSAFE_ID_FORMS_OWNER_XFAIL,
    ids=[f"{id_class}:{form}" for id_class, form, _ in _UNSAFE_ID_FORMS_OWNER_XFAIL],
)
def test_fc1_owner_path_rejects_unsafe_task_ids(
    svc: QueueService, id_class: str, form: str, bad_id: str
) -> None:
    """EXPECTED_FUTURE_CONTRACT
    Item 1: an unsafe task id is rejected (never normalised) on the owner submit path
    before any state write and before any ALLOW decision is recorded."""
    control_id = _CONTROL_IDS_OWNER[(id_class, form)]
    require_canonical_control(
        svc, owner_body(control_id, target_role="TEST-ROLE-1"), TOK_EXEMPT, control_id
    )
    tasks_before, _ = svc.counts()
    allow_before = allow_audit_rows(svc)
    code, _ = svc.post("/tasks", owner_body(bad_id, target_role="TEST-ROLE-1"), TOK_EXEMPT)
    assert code not in (200, 201), (
        f"unsafe task id {bad_id!r} ({id_class}:{form}) must not be accepted"
    )
    assert svc.counts()[0] == tasks_before, "no task row may be written"
    assert allow_audit_rows(svc) == allow_before, (
        "rejection must happen before any ALLOW decision is recorded"
    )


@pytest.mark.parametrize(
    "id_class,form,bad_id",
    _UNSAFE_ID_FORMS_OWNER_REJECTED,
    ids=[f"{id_class}:{form}" for id_class, form, _ in _UNSAFE_ID_FORMS_OWNER_REJECTED],
)
def test_fc1_owner_path_rejects_separator_task_ids_today(
    svc: QueueService, id_class: str, form: str, bad_id: str
) -> None:
    """CURRENT_BEHAVIOR
    The owner submit path (now: the explicitly exempt identity) rejects any task id
    containing '/' with 422 'bad task_id' before any state write, as it did at base."""
    code, body = svc.post("/tasks", owner_body(bad_id, target_role="TEST-ROLE-1"), TOK_EXEMPT)
    assert code == 422
    assert "bad task_id" in body["error"]
    assert svc.counts() == (0, 0), (
        f"rejected id {bad_id!r} ({id_class}:{form}) wrote state"
    )


# "a.b.c" pins the 2026-10-07 owner decision that single dots stay allowed.
_CANONICAL_TASK_IDS = ["A", "a1", "Task-1.v2_x", "a.b.c"]


@pytest.mark.parametrize("submit_path", ["enforced", "owner"])
@pytest.mark.parametrize("task_id", _CANONICAL_TASK_IDS)
def test_fc1_canonical_task_ids_stay_accepted(
    svc: QueueService, submit_path: str, task_id: str
) -> None:
    """CURRENT_BEHAVIOR
    Positive control: canonical task ids are accepted on both submit paths today and
    must stay accepted, so a fix that rejects everything cannot pass unnoticed."""
    if submit_path == "enforced":
        code, body = svc.post("/tasks", task_t0(task_id=task_id), TOK_ENF)
        assert code == 201
        assert body["policy_decision"]["decision"] == "ALLOW"
    else:
        code, body = svc.post(
            "/tasks", owner_body(task_id, target_role="TEST-ROLE-1"), TOK_EXEMPT
        )
        assert code == 201
        assert body["policy_decision"] is None
    assert svc.task_row(task_id) is not None


# Property-style corpus: printable non-alphabet characters (no '/', '.', '_', '-')
# sampled with a fixed seed at the start, middle and end of an otherwise canonical id.
# Deterministic; stdlib random only. All generated ids are accepted at base on both
# submit paths (verified by probing), so every case is a valid strict xfail.
_PROPERTY_POOL = [*"!\"#$%&'()*+,:;<=>?@[\\]^`{|}~", " "]


def _generated_unsafe_ids() -> list[tuple[str, str]]:
    rng = random.Random(20261007)
    generated = []
    for pos, make in (
        ("start", lambda ch: ch + "Task9"),
        ("middle", lambda ch: "Ta" + ch + "sk9"),
        ("end", lambda ch: "Task9" + ch),
    ):
        for ch in rng.sample(_PROPERTY_POOL, 8):
            generated.append((f"{pos}:{ch!r}", make(ch)))
    return generated


_GENERATED_UNSAFE_IDS = _generated_unsafe_ids()

# One explicitly canonical control id per (submit path, generated id), unique per test.
_CONTROL_IDS_GENERATED = {
    (submit_path, label): f"CTRL-G{submit_path[0].upper()}{i:03d}"
    for submit_path in ("enforced", "owner")
    for i, (label, _) in enumerate(_GENERATED_UNSAFE_IDS)
}


@pytest.mark.parametrize("submit_path", ["enforced", "owner"])
@pytest.mark.parametrize(
    "gen_label,bad_id",
    _GENERATED_UNSAFE_IDS,
    ids=[label for label, _ in _GENERATED_UNSAFE_IDS],
)
def test_fc1_generated_non_alphabet_task_ids_fail_closed(
    svc: QueueService, submit_path: str, gen_label: str, bad_id: str
) -> None:
    """EXPECTED_FUTURE_CONTRACT
    Item 1, property-style: a fixed seeded sample of non-alphabet characters at start,
    middle and end positions must fail closed on every submit path (deterministic, no
    new dependencies)."""
    control_id = _CONTROL_IDS_GENERATED[(submit_path, gen_label)]
    if submit_path == "enforced":
        require_canonical_control(svc, task_t0(task_id=control_id), TOK_ENF, control_id)
        tasks_before, _ = svc.counts()
        allow_before = allow_audit_rows(svc)
        code, _ = svc.post("/tasks", task_t0(task_id=bad_id), TOK_ENF)
    else:
        require_canonical_control(
            svc, owner_body(control_id, target_role="TEST-ROLE-1"), TOK_EXEMPT, control_id
        )
        tasks_before, _ = svc.counts()
        allow_before = allow_audit_rows(svc)
        code, _ = svc.post("/tasks", owner_body(bad_id, target_role="TEST-ROLE-1"), TOK_EXEMPT)
    assert code not in (200, 201), (
        f"unsafe task id {bad_id!r} ({gen_label}) must not be accepted"
    )
    assert svc.counts()[0] == tasks_before, "no task row may be written"
    assert allow_audit_rows(svc) == allow_before, (
        "rejection must happen before any ALLOW decision is recorded"
    )


def test_fc2_submit_path_decided_by_identity_record(svc: QueueService) -> None:
    """EXPECTED_FUTURE_CONTRACT
    Item 2: an identity whose record carries no explicit exemption is policy-enforced;
    omitting author_node must not bypass policy."""
    code, _ = svc.post("/tasks", owner_body("O-FC2-1"), TOK_OWN)
    tasks, _ = svc.counts()
    code_exempt, _ = svc.post("/tasks", owner_body("O-FC2-2"), TOK_EXEMPT)
    assert code_exempt == 201, "control: the explicit grant still selects the owner path"
    assert code not in (200, 201), "policy-evading envelope must not be accepted"
    assert tasks == 0, "no task row may be written"


_FC3_CASES = [
    "missing-file",
    "bad-json",
    "non-object",
    "missing-limits",
    "missing-mission-registry",
    "bad-regex",
]


@pytest.mark.parametrize("case", _FC3_CASES)
def test_fc3_policy_failure_explicit_fail_closed(svc: QueueService, case: str) -> None:
    """EXPECTED_FUTURE_CONTRACT
    Item 3: invalid or incomplete policy -> explicit fail-closed result with durable audit
    evidence and no task acceptance (not a dropped connection)."""
    if case == "missing-file":
        svc.policy_path.unlink()
    elif case == "bad-json":
        svc.policy_path.write_bytes(b'{"policy_id": ')
    elif case == "non-object":
        svc.policy_path.write_bytes(b"[]")
    elif case == "missing-limits":
        svc.write_policy(without(policy_p0(), "limits"))
    elif case == "missing-mission-registry":
        svc.write_policy(without(policy_p0(), "mission_registry"))
    elif case == "bad-regex":
        svc.write_policy(policy_p0(hard_deny_patterns=[{"id": "HD-BAD", "re": "(unclosed"}]))
    try:
        code, body = svc.post("/tasks", task_t0(), TOK_ENF)
    except (http.client.RemoteDisconnected, ConnectionResetError) as exc:
        raise AssertionError(
            "policy failure must return an explicit fail-closed response, not a dropped "
            f"connection ({exc!r})"
        ) from exc
    tasks, audit = svc.counts()
    assert tasks == 0, "no task acceptance"
    assert audit >= 1, "durable audit evidence of the refusal"
    assert not allow_audit_rows(svc), "no audit row may record an ALLOW decision"
    assert not (
        code in (200, 201)
        and isinstance(body, dict)
        and isinstance(body.get("policy_decision"), dict)
        and body["policy_decision"].get("decision") == "ALLOW"
    ), "no ALLOW"


@pytest.mark.parametrize("created_at", ["banana", "2026-10-06T12:00:00", " x "])
def test_fc4_created_at_must_be_timezone_aware_rfc3339(
    svc: QueueService, created_at: str
) -> None:
    """EXPECTED_FUTURE_CONTRACT
    Item 4: created_at that is not a timezone-aware RFC 3339 timestamp -> schema denial, no task
    acceptance."""
    code, _ = svc.post("/tasks", task_t0(created_at=created_at), TOK_ENF)
    tasks, _ = svc.counts()
    assert code not in (200, 201), "schema denial expected"
    assert tasks == 0, "no task row may be written"


@pytest.mark.parametrize(
    "outcome",
    [
        "Restart the live production service; this is not synthetic",
        "Restart the live production service in a synthetic change-window drill",
    ],
    ids=["negated", "non-negated"],
)
def test_fc5_synthetic_free_text_alone_not_allow(
    loaded_policy: dict[str, Any], outcome: str
) -> None:
    """EXPECTED_FUTURE_CONTRACT
    Item 5: free text alone must not produce ALLOW for a synthetic-only class (no field name
    asserted); a negated and a non-negated mention are both covered, so an implementation
    that only special-cases negation still fails."""
    task = task_t0(
        task_class="CANARY",
        outcome=outcome,
        scope="production host",
    )
    decision = policy_engine.evaluate(task, loaded_policy, [], NOW)
    assert decision["decision"] != "ALLOW", "free text alone must not produce ALLOW"
