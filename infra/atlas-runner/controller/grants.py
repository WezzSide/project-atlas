"""Atlas grant/authority registry (ATLAS RUNNER FABRIC integration hardening).

Closes the authority-by-string gap: an `authority_reference` in a task binding
must resolve to a durable grant record with scope, bindings, validity window
and consumption budget. Admission fails closed on every mismatch.

TRANSPORT RECORD != AUTHORITY: a workflow_dispatch event, run id, or any
transport artifact can never satisfy a grant check.

The registry shares the controller's SQLite database so grant state is as
durable as execution state and survives controller restarts.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path


class GrantError(Exception):
    """Base for all grant validation failures (fail closed)."""


class GrantMissingError(GrantError):
    """No grant id was supplied where one is required."""


class GrantUnknownError(GrantError):
    """Grant id does not resolve to a registry record."""


class GrantExpiredError(GrantError):
    """Grant validity window has passed."""


class GrantScopeError(GrantError):
    """task/repository/revision/executor mismatch with the grant."""


class GrantConsumedError(GrantError):
    """Grant execution budget is exhausted (one-shot semantics by default)."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS grants (
    grant_id TEXT PRIMARY KEY,
    scope_json TEXT NOT NULL,
    repository TEXT,
    base_revision TEXT,
    executor_type TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    expires_at REAL,
    budget INTEGER NOT NULL DEFAULT 1,
    consumed INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""


class GrantStore:
    """Durable grant registry with fail-closed validation and consumption."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.db_path), timeout=30, isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        with self._conn:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def issue(
        self,
        grant_id: str,
        *,
        scope: dict | None = None,
        repository: str | None = None,
        base_revision: str | None = None,
        executor_type: str | None = None,
        expires_at: float | None = None,
        budget: int = 1,
        now: float | None = None,
    ) -> None:
        now = time.time() if now is None else now
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO grants(grant_id, scope_json, repository,"
                " base_revision, executor_type, status, expires_at, budget, consumed,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    grant_id,
                    json.dumps(scope or {}, sort_keys=True),
                    repository,
                    base_revision,
                    executor_type,
                    "active",
                    expires_at,
                    budget,
                    0,
                    now,
                    now,
                ),
            )

    def get(self, grant_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM grants WHERE grant_id = ?", (grant_id,)
        ).fetchone()
        return dict(row) if row else None

    def validate(
        self,
        grant_id: str | None,
        *,
        task_id: str | None = None,
        repository: str | None = None,
        base_revision: str | None = None,
        executor_type: str | None = None,
        now: float | None = None,
    ) -> dict:
        """Fail-closed validation. Returns the grant row on success."""
        now = time.time() if now is None else now
        if not grant_id:
            raise GrantMissingError("authority_reference is required for Atlas tasks")
        row = self.get(grant_id)
        if row is None:
            raise GrantUnknownError(f"unknown grant {grant_id!r}")
        if row["status"] != "active":
            raise GrantConsumedError(f"grant {grant_id!r} status={row['status']}")
        if row["expires_at"] is not None and now > row["expires_at"]:
            raise GrantExpiredError(f"grant {grant_id!r} expired")
        if row["consumed"] >= row["budget"]:
            raise GrantConsumedError(f"grant {grant_id!r} budget exhausted")
        for column, value in (
            ("repository", repository),
            ("base_revision", base_revision),
            ("executor_type", executor_type),
        ):
            bound = row[column]
            if bound is not None and value is not None and bound != value:
                raise GrantScopeError(
                    f"grant {grant_id!r} binds {column}={bound!r}, task has {value!r}"
                )
        scope = json.loads(row["scope_json"])
        scope_task = scope.get("task_id")
        if scope_task is not None and task_id is not None and scope_task != task_id:
            raise GrantScopeError(
                f"grant {grant_id!r} scoped to task {scope_task!r}, not {task_id!r}"
            )
        return row

    def consume(self, grant_id: str, *, now: float | None = None) -> None:
        """Record one execution against the grant budget (terminal consumption)."""
        now = time.time() if now is None else now
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE grants SET consumed = consumed + 1, updated_at = ?"
                " WHERE grant_id = ? AND consumed < budget",
                (now, grant_id),
            )
            if cur.rowcount == 0:
                raise GrantConsumedError(f"grant {grant_id!r} budget exhausted")

    def revoke(self, grant_id: str, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE grants SET status = 'revoked', updated_at = ? WHERE grant_id = ?",
                (now, grant_id),
            )
