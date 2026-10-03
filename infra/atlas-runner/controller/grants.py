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

# Stable, non-secret refusal discriminators (F-RUNNER-1, incident record
# docs/global/baseline/2026-10-03-RUNNER-AUTHORITY-INCIDENT.md section 4.1). Each
# GrantError carries one as ``.code``, set at the raise site. Callers must read
# ``exc.code`` and never parse the exception message: "revoked" and "exhausted"
# share an exception class and differ only by this discriminator.
GRANT_MISSING = "GRANT_MISSING"
GRANT_UNKNOWN = "GRANT_UNKNOWN"
GRANT_REVOKED = "GRANT_REVOKED"
GRANT_EXPIRED = "GRANT_EXPIRED"
GRANT_EXHAUSTED = "GRANT_EXHAUSTED"
GRANT_SCOPE_MISMATCH = "GRANT_SCOPE_MISMATCH"
GRANT_CONFLICT = "GRANT_CONFLICT"
GRANT_INVALID = "GRANT_INVALID"  # unclassified GrantError (defensive default)


class GrantError(Exception):
    """Base for all grant validation failures (fail closed)."""

    default_code = GRANT_INVALID

    def __init__(self, *args: object, code: str | None = None):
        super().__init__(*args)
        self.code = code or self.default_code


class GrantMissingError(GrantError):
    """No grant id was supplied where one is required."""

    default_code = GRANT_MISSING


class GrantUnknownError(GrantError):
    """Grant id does not resolve to a registry record."""

    default_code = GRANT_UNKNOWN


class GrantExpiredError(GrantError):
    """Grant validity window has passed."""

    default_code = GRANT_EXPIRED


class GrantScopeError(GrantError):
    """task/repository/revision/executor mismatch with the grant."""

    default_code = GRANT_SCOPE_MISMATCH


class GrantConsumedError(GrantError):
    """Grant execution budget is exhausted (one-shot semantics by default).

    Also raised for a non-active (revoked) status; the two are told apart by
    ``code`` (GRANT_REVOKED vs GRANT_EXHAUSTED), never by message text.
    """

    default_code = GRANT_EXHAUSTED


class GrantConflictError(GrantError):
    """An immutable grant id was reissued with different content."""

    default_code = GRANT_CONFLICT


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
        scope_json = json.dumps(scope or {}, sort_keys=True, separators=(",", ":"))
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._conn.execute(
                    "SELECT scope_json, repository, base_revision, executor_type,"
                    " expires_at, budget FROM grants WHERE grant_id = ?",
                    (grant_id,),
                ).fetchone()
                requested = (
                    scope_json, repository, base_revision, executor_type, expires_at, budget
                )
                if existing is not None:
                    stored = tuple(existing[key] for key in (
                        "scope_json", "repository", "base_revision", "executor_type",
                        "expires_at", "budget",
                    ))
                    if stored != requested:
                        raise GrantConflictError(
                            f"grant id {grant_id!r} is immutable and already exists"
                        )
                    self._conn.commit()
                    return
                self._conn.execute(
                    "INSERT INTO grants(grant_id, scope_json, repository, base_revision,"
                    " executor_type, status, expires_at, budget, consumed, created_at,"
                    " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        grant_id, scope_json, repository, base_revision, executor_type,
                        "active", expires_at, budget, 0, now, now,
                    ),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

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
        action_type: str | None = None,
        execution_hash: str | None = None,
        now: float | None = None,
        require_complete_bindings: bool = False,
    ) -> dict:
        """Fail-closed validation. Returns the grant row on success."""
        now = time.time() if now is None else now
        if not grant_id:
            raise GrantMissingError(
                "authority_reference is required for Atlas tasks", code=GRANT_MISSING
            )
        row = self.get(grant_id)
        return self.validate_row(
            row,
            grant_id=grant_id,
            task_id=task_id,
            repository=repository,
            base_revision=base_revision,
            executor_type=executor_type,
            action_type=action_type,
            execution_hash=execution_hash,
            now=now,
            require_complete_bindings=require_complete_bindings,
        )

    @staticmethod
    def validate_row(
        row: dict | sqlite3.Row | None,
        *,
        grant_id: str | None,
        task_id: str | None = None,
        repository: str | None = None,
        base_revision: str | None = None,
        executor_type: str | None = None,
        action_type: str | None = None,
        execution_hash: str | None = None,
        now: float | None = None,
        require_complete_bindings: bool = False,
    ) -> dict:
        """Validate a locked grant row within the admission transaction."""
        now = time.time() if now is None else now
        if not grant_id:
            raise GrantMissingError(
                "authority_reference is required for Atlas tasks", code=GRANT_MISSING
            )
        if row is None:
            raise GrantUnknownError(f"unknown grant {grant_id!r}", code=GRANT_UNKNOWN)
        row = dict(row)
        if row["status"] != "active":
            raise GrantConsumedError(
                f"grant {grant_id!r} status={row['status']}", code=GRANT_REVOKED
            )
        if row["expires_at"] is not None and now >= row["expires_at"]:
            raise GrantExpiredError(f"grant {grant_id!r} expired", code=GRANT_EXPIRED)
        if row["consumed"] >= row["budget"]:
            raise GrantConsumedError(
                f"grant {grant_id!r} budget exhausted", code=GRANT_EXHAUSTED
            )
        for column, value in (
            ("repository", repository),
            ("base_revision", base_revision),
            ("executor_type", executor_type),
        ):
            bound = row[column]
            missing_required = require_complete_bindings and bound is None
            mismatch = bound is not None and bound != value
            if missing_required or mismatch:
                raise GrantScopeError(
                    f"grant {grant_id!r} binds {column}={bound!r}, task has {value!r}",
                    code=GRANT_SCOPE_MISMATCH,
                )
        scope = json.loads(row["scope_json"])
        scope_task = scope.get("task_id")
        if (require_complete_bindings and scope_task is None) or (
            scope_task is not None and scope_task != task_id
        ):
            raise GrantScopeError(
                f"grant {grant_id!r} scoped to task {scope_task!r}, not {task_id!r}",
                code=GRANT_SCOPE_MISMATCH,
            )
        for field, value in (
            ("action_type", action_type),
            ("execution_hash", execution_hash),
        ):
            bound = scope.get(field)
            if (require_complete_bindings and bound is None) or (
                bound is not None and bound != value
            ):
                raise GrantScopeError(
                    f"grant {grant_id!r} scope mismatch for {field}",
                    code=GRANT_SCOPE_MISMATCH,
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
                raise GrantConsumedError(
                    f"grant {grant_id!r} budget exhausted", code=GRANT_EXHAUSTED
                )

    def revoke(self, grant_id: str, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE grants SET status = 'revoked', updated_at = ? WHERE grant_id = ?",
                (now, grant_id),
            )


class ReadOnlyGrantReader:
    """Read-only lens over the grant registry for `health` / `status`.

    Opens the SQLite file with ``mode=ro``: it never creates the database, the
    table or a row, and never changes the journal mode. It exposes only
    ``get``, so it can validate (via ``GrantStore.validate_row``) but can never
    issue, consume or revoke. OBSERVABILITY != AUTHORITY.
    """

    def __init__(self, db_path: str | Path):
        from urllib.parse import quote

        self.db_path = Path(db_path)
        self._conn = sqlite3.connect(
            f"file:{quote(str(self.db_path))}?mode=ro", uri=True, timeout=5
        )
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self._conn.close()

    def get(self, grant_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM grants WHERE grant_id = ?", (grant_id,)
        ).fetchone()
        return dict(row) if row else None
