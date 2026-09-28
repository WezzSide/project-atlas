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
from collections.abc import Callable
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
CREATE TABLE IF NOT EXISTS grant_refunds (
    grant_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    refunded_at REAL NOT NULL,
    PRIMARY KEY (grant_id, execution_id)
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


class ExecutionConflictError(StateError):
    """Supplied execution id is already bound to another execution."""


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

    def admit_atlas_task(
        self,
        *,
        grants,
        task_id: str,
        definition: dict,
        fault_hook: Callable[[str], None] | None = None,
    ) -> tuple[str, str]:
        """Atomically consume authority and persist the task plus execution.

        The grant registry must share this SQLite database. Fault hooks exist
        solely for deterministic transaction rollback tests.
        """
        from controller.grants import GrantConsumedError, GrantStore
        from controller.schemas import validate_atlas_task_binding

        if not isinstance(grants, GrantStore) or grants.db_path.resolve() != self.db_path.resolve():
            raise StateError("Atlas admission requires a shared grant/state database")
        schema_errors = validate_atlas_task_binding(definition)
        if schema_errors:
            raise StateError("invalid Atlas task binding: " + "; ".join(schema_errors))
        grant_id = definition.get("authority_reference")
        execution_id = definition.get("execution_id")
        if not isinstance(execution_id, str) or not execution_id:
            raise StateError("Atlas admission requires the supplied execution_id")
        digest = definition_hash(definition)
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._conn.execute(
                    "SELECT definition_hash, status FROM tasks WHERE task_id = ?", (task_id,)
                ).fetchone()
                if existing is not None:
                    if existing["definition_hash"] != digest:
                        raise TaskConflictError(
                            f"task_id {task_id!r} already exists with a conflicting definition"
                        )
                    execution = self._conn.execute(
                        "SELECT execution_id FROM executions WHERE task_id = ?", (task_id,)
                    ).fetchone()
                    if execution is None or execution["execution_id"] != execution_id:
                        raise StateError("existing Atlas task has no matching execution")
                    self._conn.commit()
                    outcome = (
                        "existing_terminal"
                        if lifecycle.is_terminal(existing["status"])
                        or existing["status"] == "REJECTED"
                        else "existing_active"
                    )
                    return outcome, execution_id

                row = self._conn.execute(
                    "SELECT * FROM grants WHERE grant_id = ?", (grant_id,)
                ).fetchone()
                GrantStore.validate_row(
                    row,
                    grant_id=grant_id,
                    task_id=task_id,
                    repository=definition.get("repository"),
                    base_revision=definition.get("base_revision"),
                    executor_type=definition.get("executor_type"),
                    action_type="command" if "command" in definition["execution"] else "prompt",
                    execution_hash=definition_hash(definition["execution"]),
                    now=now,
                    require_complete_bindings=True,
                )
                if fault_hook:
                    fault_hook("after_grant_validation")
                updated = self._conn.execute(
                    "UPDATE grants SET consumed = consumed + 1, updated_at = ?"
                    " WHERE grant_id = ? AND status = 'active' AND consumed < budget"
                    " AND (expires_at IS NULL OR expires_at > ?)",
                    (now, grant_id, now),
                )
                if updated.rowcount != 1:
                    raise GrantConsumedError("grant changed or expired during admission")
                if fault_hook:
                    fault_hook("after_grant_reservation")

                self._conn.execute(
                    "INSERT INTO tasks(task_id, definition_hash, definition_json, status,"
                    " created_at, updated_at) VALUES (?,?,?,?,?,?)",
                    (task_id, digest, canonical_json(definition), lifecycle.REQUESTED, now, now),
                )
                self._conn.execute(
                    "INSERT INTO audit_log(ts, task_id, event, detail_json) VALUES (?,?,?,?)",
                    (now, task_id, "task_submitted", canonical_json({"definition_hash": digest})),
                )
                if fault_hook:
                    fault_hook("after_task_persistence")

                if self._conn.execute(
                    "SELECT 1 FROM executions WHERE execution_id = ?", (execution_id,)
                ).fetchone():
                    raise ExecutionConflictError(
                        f"execution_id {execution_id!r} is already in use"
                    )
                if fault_hook:
                    fault_hook("before_execution_creation")
                self._conn.execute(
                    "INSERT INTO executions(execution_id, task_id, status, definition_hash,"
                    " created_at, updated_at) VALUES (?,?,?,?,?,?)",
                    (execution_id, task_id, lifecycle.REQUESTED, digest, now, now),
                )
                self._conn.execute(
                    "INSERT INTO audit_log(ts, task_id, execution_id, event) VALUES (?,?,?,?)",
                    (now, task_id, execution_id, "execution_created"),
                )
                if fault_hook:
                    fault_hook("after_execution_creation")
                self._conn.commit()
                return "admitted", execution_id
            except Exception:
                self._conn.rollback()
                raise

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
        execution_id: str | None = None,
        github_run_id: int | None = None,
        github_run_attempt: int | None = None,
        github_job_id: int | None = None,
        worker_name: str | None = None,
    ) -> str:
        execution_id = execution_id or f"ex-{uuid.uuid4().hex[:16]}"
        now = time.time()
        with self._conn:
            existing = self._conn.execute(
                "SELECT task_id, definition_hash FROM executions WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
            if existing is not None:
                raise ExecutionConflictError(
                    f"execution_id {execution_id!r} is already in use"
                )
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

    def refund_grant_once(
        self, grant_id: str, execution_id: str, *, now: float | None = None
    ) -> bool:
        """Idempotent compensation: refund one unit of grant budget exactly once
        per (grant, execution). Returns True if THIS call performed the refund.
        Safe under repeated invocation, restart, and concurrent recovery.
        Requires the grants table in this shared database."""
        now = time.time() if now is None else now
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO grant_refunds(grant_id, execution_id, refunded_at)"
                    " VALUES (?,?,?)",
                    (grant_id, execution_id, now),
                )
                if cur.rowcount == 0:
                    self._conn.commit()
                    return False
                self._conn.execute(
                    "UPDATE grants SET consumed = MAX(0, consumed - 1), updated_at = ?"
                    " WHERE grant_id = ?",
                    (now, grant_id),
                )
                self._conn.commit()
                return True
            except Exception:
                self._conn.rollback()
                raise

    def fail_admitted_start(
        self,
        *,
        task_id: str,
        execution_id: str,
        reason: str,
        now: float | None = None,
    ) -> None:
        """P1-2: a committed admission whose execution could not start.

        Exactly-once refund against the task's authority grant (if any),
        task -> REJECTED, execution -> FAILED with the reason recorded. If the
        refund itself fails, the execution is marked refund_pending so
        reconciliation can retry; the state is never silently lost.
        """
        now = time.time() if now is None else now
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                execution = self._conn.execute(
                    "SELECT status, task_id FROM executions WHERE execution_id = ?",
                    (execution_id,),
                ).fetchone()
                if execution is None:
                    self._conn.commit()
                    return
                # Never touch an execution that already started.
                if execution["status"] not in {lifecycle.REQUESTED, lifecycle.ADMITTED}:
                    self._conn.commit()
                    return
                task = self._conn.execute(
                    "SELECT definition_json FROM tasks WHERE task_id = ?",
                    (execution["task_id"],),
                ).fetchone()
                import json as _json

                definition = _json.loads(task["definition_json"]) if task else {}
                grant_id = definition.get("authority_reference")
                refund_state = "not_applicable"
                if grant_id:
                    cur = self._conn.execute(
                        "INSERT OR IGNORE INTO grant_refunds(grant_id, execution_id, refunded_at)"
                        " VALUES (?,?,?)",
                        (grant_id, execution_id, now),
                    )
                    if cur.rowcount == 1:
                        self._conn.execute(
                            "UPDATE grants SET consumed = MAX(0, consumed - 1), updated_at = ?"
                            " WHERE grant_id = ?",
                            (now, grant_id),
                        )
                        refund_state = "refunded"
                    else:
                        refund_state = "already_refunded"
                marker = reason if refund_state != "refund_failed" else reason + ":refund_pending"
                self._conn.execute(
                    "UPDATE executions SET status = ?, terminal_status = 'failed',"
                    " failure_reason = ?, updated_at = ?, finished_at = ?"
                    " WHERE execution_id = ? AND status IN (?, ?)",
                    (lifecycle.FAILED, marker, now, now, execution_id,
                     lifecycle.REQUESTED, lifecycle.ADMITTED),
                )
                self._conn.execute(
                    "UPDATE tasks SET status = 'REJECTED', reason = ?, updated_at = ?"
                    " WHERE task_id = ? AND status NOT IN ('COMPLETE', 'FAILED',"
                    " 'TIMED_OUT', 'CLEANUP_REQUIRED', 'BLOCKED', 'REJECTED')",
                    (marker, now, execution["task_id"]),
                )
                self._conn.execute(
                    "INSERT INTO audit_log(ts, task_id, execution_id, event, detail_json)"
                    " VALUES (?,?,?,?,?)",
                    (now, execution["task_id"], execution_id, "start_failed_compensated",
                     _json.dumps({"reason": marker, "refund": refund_state})),
                )
                self._conn.commit()
            except Exception as exc:
                self._conn.rollback()
                # Compensation failure: durable retryable state, not silent loss.
                try:
                    self._conn.execute(
                        "UPDATE executions SET failure_reason = ?, updated_at = ?"
                        " WHERE execution_id = ?",
                        (f"{reason}:refund_pending:{str(exc)[:120]}", now, execution_id),
                    )
                finally:
                    raise

    def is_terminal_task_status(self, status: str) -> bool:
        return status == "REJECTED" or lifecycle.is_terminal(status)

    def all_executions(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM executions ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

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
            "cleanup_status",
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
