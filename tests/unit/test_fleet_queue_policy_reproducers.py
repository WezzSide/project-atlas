"""Reproducer tests for the fleet queue/policy trust boundary (lane KIMI-FLEET-REPRO-001).

Synthetic, local-only characterisation of ``infra/fleet-runtime`` at base 2deff6de.
Every policy, token, node, mission and task identifier below is invented for these
tests. The queue service runs only as a subprocess bound to 127.0.0.1 on an
ephemeral port with a temporary database and is terminated by the fixture; every
file touched lives under ``tmp_path``. Each test carries exactly one label on the
first line of its docstring: CURRENT_BEHAVIOR (passes at base) or
EXPECTED_FUTURE_CONTRACT (strict xfail for the owner-adopted target contract).
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


# ---------------------------------------------------------------------------
# Service-level reproducers 1-27
# ---------------------------------------------------------------------------


def test_r01_missing_policy_enforced_submit_503(svc: QueueService) -> None:
    """CURRENT_BEHAVIOR
    R-MISSING: deleted policy file -> 503 with 'fails closed' text, rows +0/+0."""
    svc.policy_path.unlink()
    before = svc.counts()
    code, body = svc.post("/tasks", task_t0(), TOK_ENF)
    assert code == 503
    assert "fails closed" in body["error"]
    assert svc.counts() == before


def test_r02_badjson_policy_drops_connection(svc: QueueService) -> None:
    """CURRENT_BEHAVIOR
    R-BADJSON: invalid JSON policy -> no HTTP response, rows +0/+0, /health 200,
    stderr JSONDecodeError."""
    svc.policy_path.write_bytes(b'{"policy_id": ')
    before = svc.counts()
    svc.drop("POST", "/tasks", TOK_ENF, task_t0())
    assert svc.counts() == before
    code, _ = svc.get("/health")
    assert code == 200
    assert svc.wait_stderr("JSONDecodeError")


@pytest.mark.parametrize("payload", [b"[]", b'"x"', b"7", b"null"])
def test_r03_nonobject_policy_drops_connection(svc: QueueService, payload: bytes) -> None:
    """CURRENT_BEHAVIOR
    R-NONOBJECT: non-object JSON policy -> no HTTP response, TypeError, rows +0/+0."""
    svc.policy_path.write_bytes(payload)
    before = svc.counts()
    svc.drop("POST", "/tasks", TOK_ENF, task_t0())
    assert svc.counts() == before
    assert svc.wait_stderr("TypeError")


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="reproducer needs an unprivileged uid so a mode-000 policy file is unreadable",
)
def test_r04_unreadable_policy_drops_connection(svc: QueueService) -> None:
    """CURRENT_BEHAVIOR
    R-UNREADABLE: mode-000 policy file -> no HTTP response, PermissionError, rows +0/+0."""
    svc.policy_path.chmod(0)
    try:
        before = svc.counts()
        svc.drop("POST", "/tasks", TOK_ENF, task_t0())
        assert svc.counts() == before
        assert svc.wait_stderr("PermissionError")
    finally:
        svc.policy_path.chmod(0o600)


@pytest.mark.parametrize("key", REQUIRED_POLICY_KEYS)
def test_r05_missing_policy_key_drops_connection(svc: QueueService, key: str) -> None:
    """CURRENT_BEHAVIOR
    R-MISSINGKEY: P0 minus one top-level key -> no HTTP response, KeyError '<key>', rows +0/+0."""
    svc.write_policy(without(policy_p0(), key))
    before = svc.counts()
    svc.drop("POST", "/tasks", TOK_ENF, task_t0())
    assert svc.counts() == before
    assert svc.wait_stderr(f"KeyError: '{key}'")


def test_r06_lazy_limits_schema_version_denied_first(svc: QueueService) -> None:
    """CURRENT_BEHAVIOR
    R-LAZY: P0 minus limits + schema_version 2 -> 422 'schema_version must be 1',
    one policy_decision row."""
    svc.write_policy(without(policy_p0(), "limits"))
    code, body = svc.post("/tasks", task_t0(schema_version=2), TOK_ENF)
    assert code == 422
    assert "schema_version must be 1" in body["decision"]["reason"]
    rows = svc.audit_rows()
    assert len(rows) == 1
    assert rows[0]["action"] == "policy_decision"
    assert svc.counts()[0] == 0


@pytest.mark.parametrize("field", ["policy_id", "policy_version"])
def test_r07_missing_provenance_field_allows_with_null(svc: QueueService, field: str) -> None:
    """CURRENT_BEHAVIOR
    R-PROVENANCE-NULL: P0 minus policy_id/policy_version -> 201 ALLOW with that field null."""
    svc.write_policy(without(policy_p0(), field))
    code, body = svc.post("/tasks", task_t0(), TOK_ENF)
    assert code == 201
    assert body["policy_decision"]["decision"] == "ALLOW"
    assert body["policy_decision"][field] is None


def test_r08_target_roles_string_substring_widening(svc: QueueService) -> None:
    """CURRENT_BEHAVIOR
    R-SUBSTRING-WIDEN: target_roles as bare string + subject-less authority ->
    role 'ROLE' gets 201 ALLOW."""
    policy = policy_p0()
    policy["allowed_classes"]["VERIFY"]["target_roles"] = "TEST-ROLE-1"
    policy["authority_registry"]["AUTH-TEST-001"] = {"status": "current"}
    svc.write_policy(policy)
    code, body = svc.post("/tasks", task_t0(target_role="ROLE"), TOK_ENF)
    assert code == 201
    assert body["policy_decision"]["decision"] == "ALLOW"


def test_r09_badregex_order_dependent(svc: QueueService) -> None:
    """CURRENT_BEHAVIOR
    R-BADREGEX: (a) clean task drops on PatternError; (b) earlier match -> 422 HD-OK;
    (c) earlier mission check -> 422."""
    svc.write_policy(
        policy_p0(
            hard_deny_patterns=[
                {"id": "HD-OK", "re": "password"},
                {"id": "HD-BAD", "re": "(unclosed"},
            ]
        )
    )
    before = svc.counts()
    svc.drop("POST", "/tasks", TOK_ENF, task_t0())
    assert svc.counts() == before
    code, body = svc.post("/tasks", task_t0(outcome="print the password"), TOK_ENF)
    assert code == 422
    assert "HARD_DENY: HD-OK matched" in body["decision"]["reason"]
    code, body = svc.post("/tasks", task_t0(mission_id="NOPE"), TOK_ENF)
    assert code == 422
    assert "MISSION_UNKNOWN" in body["decision"]["reason"]
    assert svc.wait_stderr("PatternError") or "re.error" in svc.stderr()


def test_r10_badregex_verdict_patterns_class_dependent(svc: QueueService) -> None:
    """CURRENT_BEHAVIOR
    R-BADREGEX-CLASS: bad verdict_dictation pattern crashes VERIFY but not CANARY (201 ALLOW)."""
    svc.write_policy(policy_p0(verdict_dictation_patterns=["[bad"]))
    svc.drop("POST", "/tasks", TOK_ENF, task_t0())
    code, body = svc.post(
        "/tasks", task_t0(task_class="CANARY", scope="synthetic fixture"), TOK_ENF
    )
    assert code == 201
    assert body["policy_decision"]["decision"] == "ALLOW"


def test_r11_hard_deny_entry_without_id(svc: QueueService) -> None:
    """CURRENT_BEHAVIOR
    R-DENY-ID-MISSING: id-less hard_deny entry passes a clean task (201) but drops a match
    with KeyError 'id'."""
    svc.write_policy(policy_p0(hard_deny_patterns=[{"re": "password"}]))
    code, _ = svc.post("/tasks", task_t0(), TOK_ENF)
    assert code == 201
    before = svc.counts()
    svc.drop("POST", "/tasks", TOK_ENF, task_t0(outcome="rotate the password"))
    assert svc.counts() == before
    assert svc.wait_stderr("KeyError: 'id'")


def test_r12_owner_path_never_reads_policy(svc: QueueService) -> None:
    """CURRENT_BEHAVIOR
    R-OWNER-PATH: owner-form envelope as OWN -> 201, policy_decision null, one submit row
    (autonomous false), invalid policy never read."""
    svc.policy_path.write_bytes(b'{"policy_id": ')
    code, body = svc.post("/tasks", owner_body(), TOK_OWN)
    assert code == 201
    assert body["policy_decision"] is None
    row = svc.task_row("O-1")
    assert row is not None
    assert row["policy_decision"] is None
    rows = svc.audit_rows()
    assert len(rows) == 1
    assert rows[0]["action"] == "submit"
    assert json.loads(rows[0]["detail"])["autonomous"] is False


def test_r13_enforced_identity_without_author_node_403(svc: QueueService) -> None:
    """CURRENT_BEHAVIOR
    R-ENFORCED-NO-AUTHOR: item-12 body as ENF -> 403, rows +0/+0."""
    before = svc.counts()
    code, _ = svc.post("/tasks", owner_body(), TOK_ENF)
    assert code == 403
    assert svc.counts() == before


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
    """CURRENT_BEHAVIOR
    R-TASKID-PATH: traversal task_id -> 201 as ENF; same id as OWN -> 422 'bad task_id'."""
    traversal_id = "../state/qp-traversal-proof"
    code, _ = svc.post("/tasks", task_t0(task_id=traversal_id), TOK_ENF)
    assert code == 201
    assert svc.task_row(traversal_id) is not None
    code, body = svc.post("/tasks", owner_body(traversal_id), TOK_OWN)
    assert code == 422
    assert "bad task_id" in body["error"]


def test_r16_created_at_not_scanned_by_hard_deny(svc: QueueService) -> None:
    """CURRENT_BEHAVIOR
    R-UNSCANNED: hard-deny words in created_at -> 201; same words in outcome -> 422 HARD_DENY."""
    code, _ = svc.post(
        "/tasks",
        task_t0(created_at="deploy to production and print the password"),
        TOK_ENF,
    )
    assert code == 201
    code, body = svc.post("/tasks", task_t0(outcome="deploy to production"), TOK_ENF)
    assert code == 422
    assert "HARD_DENY" in body["decision"]["reason"]


@pytest.mark.parametrize("created_at", ["banana", "2026-10-06T12:00:00", " x "])
def test_r17_created_at_any_string_accepted(svc: QueueService, created_at: str) -> None:
    """CURRENT_BEHAVIOR
    R-CREATED-AT: arbitrary non-empty created_at strings -> 201 ALLOW."""
    code, body = svc.post("/tasks", task_t0(created_at=created_at), TOK_ENF)
    assert code == 201
    assert body["policy_decision"]["decision"] == "ALLOW"


def test_r18_allow_decision_without_task_row(svc: QueueService) -> None:
    """CURRENT_BEHAVIOR
    R-ALLOW-NO-TASK-1: dict parent_task_id -> no response, audit +1 (policy_decision ALLOW),
    tasks +0."""
    before = svc.counts()
    svc.drop("POST", "/tasks", TOK_ENF, task_t0(parent_task_id={"free": ["form"]}))
    after_tasks, after_audit = svc.counts()
    assert after_tasks == before[0]
    assert after_audit == before[1] + 1
    row = svc.audit_rows()[-1]
    assert row["action"] == "policy_decision"
    assert json.loads(row["detail"])["decision"] == "ALLOW"


def test_r19_allow_decision_then_409_on_terminal_same_key(svc: QueueService) -> None:
    """CURRENT_BEHAVIOR
    R-ALLOW-NO-TASK-2: idempotency_key in FAILED state -> 409 after an ALLOW audit row; tasks +0."""
    first = task_t0(task_id="TASK-KFAILED-1", idempotency_key="K-FAILED")
    code, _ = svc.post("/tasks", first, TOK_ENF)
    assert code == 201
    svc.exec_sql("UPDATE tasks SET state='FAILED' WHERE task_id=?", (first["task_id"],))
    before = svc.counts()
    code, _ = svc.post(
        "/tasks",
        task_t0(task_id="TASK-KFAILED-2", idempotency_key="K-FAILED"),
        TOK_ENF,
    )
    assert code == 409
    after_tasks, after_audit = svc.counts()
    assert after_tasks == before[0]
    assert after_audit == before[1] + 1
    row = svc.audit_rows()[-1]
    assert row["action"] == "policy_decision"
    assert json.loads(row["detail"])["decision"] == "ALLOW"


def test_r20_deny_audit_lost_on_nonstring_task_id(svc: QueueService) -> None:
    """CURRENT_BEHAVIOR
    R-DENY-LOST: task_id as list -> no response, rows +0/+0, stderr sqlite3.ProgrammingError."""
    before = svc.counts()
    svc.drop("POST", "/tasks", TOK_ENF, task_t0(task_id=["x"]))
    assert svc.counts() == before
    assert svc.wait_stderr("ProgrammingError")


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
    """CURRENT_BEHAVIOR
    R-LIBDIR: service without the engine beside it ignores ATLAS_LIB_DIR: ENF -> 503,
    OWN -> 201, quiet stderr."""
    engine_home = tmp_path / "engine-only"
    engine_home.mkdir()
    shutil.copy(ENGINE_SRC, engine_home / ENGINE_SRC.name)
    svc = make_service(engine=False, extra_env={"ATLAS_LIB_DIR": str(engine_home)})
    time.sleep(0.2)
    assert svc.stderr().strip() == ""
    code, body = svc.post("/tasks", task_t0(), TOK_ENF)
    assert code == 503
    assert "policy engine unavailable" in body["error"]
    code, body = svc.post("/tasks", owner_body(), TOK_OWN)
    assert code == 201
    assert body["policy_decision"] is None


# ---------------------------------------------------------------------------
# Engine-level reproducers 28-30 (direct evaluate calls, pinned now)
# ---------------------------------------------------------------------------


def test_r28_synthetic_only_satisfied_by_free_text_word(loaded_policy: dict[str, Any]) -> None:
    """CURRENT_BEHAVIOR
    R-SYNTHETIC-WORD: CANARY with 'not synthetic' free text -> ALLOW;
    without marker words -> SYNTHETIC_REQUIRED."""
    allow_task = task_t0(
        task_class="CANARY",
        outcome="Restart the live production service; this is not synthetic",
        scope="production host",
    )
    decision = policy_engine.evaluate(allow_task, loaded_policy, [], NOW)
    assert decision["decision"] == "ALLOW"
    deny_task = task_t0(
        task_class="CANARY",
        outcome="Restart the live service",
        scope="production host",
    )
    decision = policy_engine.evaluate(deny_task, loaded_policy, [], NOW)
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
    """CURRENT_BEHAVIOR
    R-CLI-DROP: server policy invalid JSON -> author exits 1, RemoteDisconnected traceback,
    zero decision files."""
    svc.policy_path.write_bytes(b'{"policy_id": ')
    cp, decisions_dir = run_author_cli(tmp_path, port=svc.port, task=task_t0())
    assert cp.returncode == 1
    assert "RemoteDisconnected" in cp.stderr
    assert list(decisions_dir.glob("*.json")) == []


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
    """CURRENT_BEHAVIOR
    R-SUP-TRAVERSAL: claimed task ids containing '..' write inbox files into <VAR>/state/,
    overwrite state.json, and next_task sees nothing."""
    first = task_t0(
        task_id="../state/qp-traversal-proof", idempotency_key="IDEM-TRAV-1", priority=100
    )
    second = task_t0(task_id="../state/state", idempotency_key="IDEM-TRAV-2", priority=100)
    for task in (first, second):
        code, body = svc.post("/tasks", task, TOK_ENF)
        assert code == 201, body
    etc = tmp_path / "etc"
    var = tmp_path / "var"
    (var / "state").mkdir(parents=True)
    etc.mkdir()
    (var / "state" / "state.json").write_text(
        json.dumps({"sentinel": "TEST-SENTINEL", "status": "OBSERVE_ONLY"})
    )
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
    proof = var / "state" / "qp-traversal-proof.json"
    assert proof.exists()
    assert json.loads(proof.read_text())["task_id"] == "../state/qp-traversal-proof"
    assert (var / "state" / "qp-traversal-proof.lease.json").exists()
    state_now = json.loads((var / "state" / "state.json").read_text())
    assert state_now.get("task_id") == "../state/state"
    assert state_now.get("sentinel") != "TEST-SENTINEL"
    assert (var / "state" / "state.lease.json").exists()
    assert supervisor.next_task("TEST-ROLE-1") is None
    assert svc.task_row("../state/qp-traversal-proof")["state"] == "CLAIMED"
    assert svc.task_row("../state/state")["state"] == "CLAIMED"


# ---------------------------------------------------------------------------
# Owner-adopted future contract (strict xfail at base)
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
    RuntimeError, never AssertionError, so under xfail(raises=AssertionError) a broken
    submit path fails/errors the test instead of reporting XFAIL without the id being
    evaluated, and an unrelated blanket refusal cannot satisfy the refusal assertions.
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

_XFAIL_ID_REASON = (
    "EXPECTED_FUTURE_CONTRACT: item 1 unsafe task ids fail closed on every submit path"
)

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
@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_XFAIL_ID_REASON)
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
@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_XFAIL_ID_REASON)
def test_fc1_owner_path_rejects_unsafe_task_ids(
    svc: QueueService, id_class: str, form: str, bad_id: str
) -> None:
    """EXPECTED_FUTURE_CONTRACT
    Item 1: an unsafe task id is rejected (never normalised) on the owner submit path
    before any state write and before any ALLOW decision is recorded."""
    control_id = _CONTROL_IDS_OWNER[(id_class, form)]
    require_canonical_control(
        svc, owner_body(control_id, target_role="TEST-ROLE-1"), TOK_OWN, control_id
    )
    tasks_before, _ = svc.counts()
    allow_before = allow_audit_rows(svc)
    code, _ = svc.post("/tasks", owner_body(bad_id, target_role="TEST-ROLE-1"), TOK_OWN)
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
    The owner submit path already rejects any task id containing '/' with 422
    'bad task_id' before any state write; pinned as passing so these contract-item-1
    forms are not double-counted as xfails."""
    code, body = svc.post("/tasks", owner_body(bad_id, target_role="TEST-ROLE-1"), TOK_OWN)
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
        code, body = svc.post("/tasks", owner_body(task_id, target_role="TEST-ROLE-1"), TOK_OWN)
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
@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_XFAIL_ID_REASON)
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
            svc, owner_body(control_id, target_role="TEST-ROLE-1"), TOK_OWN, control_id
        )
        tasks_before, _ = svc.counts()
        allow_before = allow_audit_rows(svc)
        code, _ = svc.post("/tasks", owner_body(bad_id, target_role="TEST-ROLE-1"), TOK_OWN)
    assert code not in (200, 201), (
        f"unsafe task id {bad_id!r} ({gen_label}) must not be accepted"
    )
    assert svc.counts()[0] == tasks_before, "no task row may be written"
    assert allow_audit_rows(svc) == allow_before, (
        "rejection must happen before any ALLOW decision is recorded"
    )


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="EXPECTED_FUTURE_CONTRACT: item 2 submit path decided by the identity record",
)
def test_fc2_submit_path_decided_by_identity_record(svc: QueueService) -> None:
    """EXPECTED_FUTURE_CONTRACT
    Item 2: an identity whose record carries no explicit exemption is policy-enforced;
    omitting author_node must not bypass policy."""
    code, _ = svc.post("/tasks", owner_body("O-FC2-1"), TOK_OWN)
    tasks, _ = svc.counts()
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
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="EXPECTED_FUTURE_CONTRACT: item 3 invalid policy fails closed with audit evidence",
)
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
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="EXPECTED_FUTURE_CONTRACT: item 4 created_at must be timezone-aware RFC 3339",
)
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
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="EXPECTED_FUTURE_CONTRACT: item 5 free text alone must not satisfy synthetic-only",
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
