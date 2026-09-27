"""SQLite state store for the atlas-runner controller (AS-RUNNER-001).

Contract: durable execution state with WAL, schema migrations, lifecycle
transition enforcement, idempotent admission keyed on the immutable task
definition hash, and heartbeats for the health lens. RUNNING_ROW != WORKER
ALIVE: sqlite truth is reconciled against docker reality by reconcile.py.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from controller import lifecycle

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    definition_hash TEXT NOT NULL,
    definition_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'REQUESTED',
    reason TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS executions (
    execution_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    github_run_id INTEGER,
    github_run_attempt INTEGER,
    github_job_id INTEGER,
    worker_name TEXT,
    runner_name TEXT,
    status TEXT NOT NULL,
    terminal_status TEXT,
    failure_reason TEXT,
    cleanup_status TEXT NOT NULL DEFAULT 'unknown',
    definition_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    lease_owner TEXT,
    lease_expires REAL,
    evidence_path TEXT,
    verifier_verdict TEXT,
    reconciled INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_executions_status ON executions(status);

CREATE INDEX IF NOT EXISTS idx_executions_task ON executions(task_id);

CREATE TABLE IF NOT EXISTS audit_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    task_id TEXT,
    execution_id TEXT,
    event TEXT NOT NULL,
    detail_json TEXT
);

CREATE TABLE IF NOT EXISTS heartbeats (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    ts REAL NOT NULL,
    pid INTEGER NOT NULL
);
"""


def _migrate_columns(conn) -> None:
    """Idempotent column additions for databases created before a column existed."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(executions)")}
    additions = {
        "github_job_id": "INTEGER",
        "verifier_verdict": "TEXT",
        "reconciled": "INTEGER NOT NULL DEFAULT 0",
    }
    for name, ddl in additions.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE executions ADD COLUMN {name} {ddl}")


class StateError(RuntimeError):
    """Raised on state-store violations (conflicts, bad transitions)."""


class TransitionError(StateError):
    """Raised when a lifecycle transition is not permitted."""


class TaskConflictError(StateError):
    """Same task_id with a conflicting immutable definition."""


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def definition_hash(definition: dict) -> str:
    import hashlib

    return hashlib.sha256(canonical_json(definition).encode("utf-8")).hexdigest()


class StateStore:
    """WAL sqlite3 store; every mutator runs in an immediate transaction."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.db_path), timeout=30, isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._migrate()

    def close(self) -> None:
        self._conn.close()

    def _migrate(self) -> None:
        with self._conn:
            self._conn.executescript(_SCHEMA)
            _migrate_columns(self._conn)
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    # -- audit -------------------------------------------------------------
    def audit(
        self,
        event: str,
        *,
        task_id: str | None = None,
        execution_id: str | None = None,
        detail: dict | None = None,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO audit_log(ts, task_id, execution_id, event, detail_json)"
                " VALUES (?,?,?,?,?)",
                (
                    time.time(),
                    task_id,
                    execution_id,
                    event,
                    canonical_json(detail) if detail else None,
                ),
            )

    # -- heartbeats --------------------------------------------------------
    def heartbeat(self, pid: int) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO heartbeats(id, ts, pid) VALUES (1, ?, ?)",
                (time.time(), pid),
            )

    def last_heartbeat(self) -> float | None:
        row = self._conn.execute("SELECT ts FROM heartbeats WHERE id = 1").fetchone()
        return float(row["ts"]) if row else None

    # -- admission / idempotency --------------------------------------------
    def submit_task(
        self, task_id: str, definition: dict, *, now: float | None = None
    ) -> tuple[str, str]:
        """Idempotent admission.

        Returns (outcome, status) where outcome is one of
        'admitted' | 'existing_terminal' | 'existing_active'.
        Same task_id + same definition -> idempotent (existing_*).
        Same task_id + conflicting definition -> TaskConflictError (fail closed).
        """
        now = time.time() if now is None else now
        digest = definition_hash(definition)
        definition_json = canonical_json(definition)
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT definition_hash, status FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is not None:
                if row["definition_hash"] != digest:
                    raise TaskConflictError(
                        f"task_id {task_id!r} already exists with a conflicting definition"
                    )
                status = row["status"]
                outcome = (
                    "existing_terminal"
                    if lifecycle.is_terminal(status) or status == "REJECTED"
                    else "existing_active"
                )
                return outcome, status
            self._conn.execute(
                "INSERT INTO tasks(task_id, definition_hash, definition_json, status, created_at,"
                " updated_at) VALUES (?,?,?,?,?,?)",
                (task_id, digest, definition_json, lifecycle.REQUESTED, now, now),
            )
            self._conn.execute(
                "INSERT INTO audit_log(ts, task_id, event, detail_json) VALUES (?,?,?,?)",
                (now, task_id, "task_submitted", canonical_json({"definition_hash": digest})),
            )
        return "admitted", lifecycle.REQUESTED

    def get_task(self, task_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_task_status(self, task_id: str, status: str, *, reason: str | None = None) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE tasks SET status = ?, reason = ?, updated_at = ? WHERE task_id = ?",
                (status, reason, time.time(), task_id),
            )

    # -- executions ---------------------------------------------------------
    def create_execution(
        self,
        *,
        task_id: str,
        definition_hash: str,
        github_run_id: int | None = None,
        github_run_attempt: int | None = None,
        github_job_id: int | None = None,
        worker_name: str | None = None,
        execution_id: str | None = None,
    ) -> str:
        # Canonical execution identity: Atlas-issued tasks carry an explicit
        # execution_id which must be unique; internal/transport tasks get a
        # generated id from the single authoritative allocator (this store).
        if execution_id is None:
            execution_id = f"ex-{uuid.uuid4().hex[:16]}"
        elif self.get_execution(execution_id) is not None:
            raise StateError(f"execution_id {execution_id!r} already exists")
        now = time.time()
        with self._conn:
            self._conn.execute(
                "INSERT INTO executions(execution_id, task_id, github_run_id,"
                " github_run_attempt, github_job_id, worker_name, status, definition_hash,"
                " created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    execution_id,
                    task_id,
                    github_run_id,
                    github_run_attempt,
                    github_job_id,
                    worker_name,
                    lifecycle.REQUESTED,
                    definition_hash,
                    now,
                    now,
                ),
            )
            self._conn.execute(
                "INSERT INTO audit_log(ts, task_id, execution_id, event) VALUES (?,?,?,?)",
                (now, task_id, execution_id, "execution_created"),
            )
        return execution_id

    def get_execution(self, execution_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
        ).fetchone()
        return dict(row) if row else None

    def find_execution_by_job(
        self, run_id: int, run_attempt: int, job_id: int
    ) -> dict | None:
        """Dedupe identity for queued GitHub jobs: (run, attempt, job).

        Distinct jobs within one run/attempt have distinct identities; a rerun
        attempt is a separate identity by design.
        """
        row = self._conn.execute(
            "SELECT * FROM executions WHERE github_run_id = ? AND github_run_attempt = ?"
            " AND github_job_id = ? ORDER BY created_at DESC LIMIT 1",
            (run_id, run_attempt, job_id),
        ).fetchone()
        return dict(row) if row else None

    def record_verifier_verdict(self, execution_id: str, verdict: str) -> None:
        if verdict not in {"PENDING", "VERIFIED", "REJECTED", "UNESTABLISHED"}:
            raise ValueError(f"unknown verifier verdict {verdict!r}")
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE executions SET verifier_verdict = ?, updated_at = ?"
                " WHERE execution_id = ?",
                (verdict, time.time(), execution_id),
            )

    def record_reconciled(self, execution_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE executions SET reconciled = 1, updated_at = ? WHERE execution_id = ?",
                (time.time(), execution_id),
            )

    def is_terminal_task_status(self, status: str) -> bool:
        return status == "REJECTED" or lifecycle.is_terminal(status)

    def active_executions(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM executions WHERE status NOT IN"
            f" ({','.join('?' * len(lifecycle.TERMINAL_STATES))})"
            " ORDER BY created_at",
            tuple(sorted(lifecycle.TERMINAL_STATES)),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_active_workers(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM executions WHERE status IN"
            " ('PROVISIONING','REGISTERING','READY','ASSIGNED','RUNNING')"
        ).fetchone()
        return int(row["n"])

    def transition(
        self,
        execution_id: str,
        to_state: str,
        *,
        terminal_status: str | None = None,
        failure_reason: str | None = None,
        cleanup_status: str | None = None,
        evidence_path: str | None = None,
        now: float | None = None,
    ) -> None:
        """Enforce the lifecycle graph; invalid transitions raise TransitionError."""
        if to_state not in lifecycle.ALL_STATES:
            raise TransitionError(f"unknown state: {to_state!r}")
        now = time.time() if now is None else now
        with self._conn:
            row = self._conn.execute(
                "SELECT status, task_id FROM executions WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
            if row is None:
                raise StateError(f"unknown execution: {execution_id}")
            from_state = row["status"]
            if not lifecycle.can_transition(from_state, to_state):
                raise TransitionError(f"invalid transition {from_state} -> {to_state}")
            finished = now if lifecycle.is_terminal(to_state) else None
            started = now if to_state == lifecycle.RUNNING else None
            self._conn.execute(
                "UPDATE executions SET status = ?, terminal_status = COALESCE(?, terminal_status),"
                " failure_reason = COALESCE(?, failure_reason),"
                " cleanup_status = COALESCE(?, cleanup_status),"
                " evidence_path = COALESCE(?, evidence_path),"
                " updated_at = ?, started_at = COALESCE(?, started_at),"
                " finished_at = COALESCE(?, finished_at)"
                " WHERE execution_id = ?",
                (
                    to_state,
                    terminal_status,
                    failure_reason,
                    cleanup_status,
                    evidence_path,
                    now,
                    started,
                    finished,
                    execution_id,
                ),
            )
            self._conn.execute(
                "INSERT INTO audit_log(ts, task_id, execution_id, event, detail_json)"
                " VALUES (?,?,?,?,?)",
                (
                    now,
                    row["task_id"],
                    execution_id,
                    "transition",
                    canonical_json({"from": from_state, "to": to_state}),
                ),
            )

    def set_execution_fields(self, execution_id: str, **fields: object) -> None:
        allowed = {
            "worker_name",
            "runner_name",
            "github_run_id",
            "github_run_attempt",
            "lease_owner",
            "lease_expires",
            "evidence_path",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise StateError(f"cannot set execution fields: {sorted(unknown)}")
        if not fields:
            return
        columns = ", ".join(f"{k} = ?" for k in fields)
        with self._conn:
            self._conn.execute(
                f"UPDATE executions SET {columns}, updated_at = ? WHERE execution_id = ?",
                (*fields.values(), time.time(), execution_id),
            )

    # -- leases -------------------------------------------------------------
    def acquire_lease(
        self, execution_id: str, *, ttl_seconds: float, owner: str | None = None
    ) -> str:
        """Take a lease row; returns lease owner token. Fails if already leased live."""
        now = time.time()
        owner = owner or uuid.uuid4().hex
        with self._conn:
            row = self._conn.execute(
                "SELECT lease_owner, lease_expires FROM executions WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
            if row is None:
                raise StateError(f"unknown execution: {execution_id}")
            if (
                row["lease_owner"]
                and row["lease_expires"]
                and row["lease_expires"] > now
                and row["lease_owner"] != owner
            ):
                raise StateError(f"execution {execution_id} is leased by another owner")
            self._conn.execute(
                "UPDATE executions SET lease_owner = ?, lease_expires = ?, updated_at = ?"
                " WHERE execution_id = ?",
                (owner, now + ttl_seconds, now, execution_id),
            )
        return owner

    def release_lease(self, execution_id: str, owner: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE executions SET lease_owner = NULL, lease_expires = NULL, updated_at = ?"
                " WHERE execution_id = ? AND lease_owner = ?",
                (time.time(), execution_id, owner),
            )
