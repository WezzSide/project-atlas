"""Main controller loop (AS-RUNNER-FABRIC-001).

Contract: poll GitHub for queued jobs (no inbound ports), admit by label
subset + capacity, orchestrate one worker per admitted job, reconcile on
startup and periodically. POLLING != AUTHORITY: a queued job is a request,
never an instruction; admission control decides. HEARTBEAT != HEALTH: the
heartbeat row is a liveness signal only, cross-checked by health.py.
"""

from __future__ import annotations

import os
import re
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from controller import lifecycle
from controller.config import ControllerConfig, validate_task_env
from controller.dockerctl import DockerCtl
from controller.github import GitHubClient, GitHubError
from controller.grants import (
    GRANT_EXHAUSTED,
    GRANT_EXPIRED,
    GRANT_MISSING,
    GRANT_REVOKED,
    GRANT_SCOPE_MISMATCH,
    GRANT_UNKNOWN,
    GrantError,
    GrantMissingError,
    GrantStore,
)
from controller.state import StateStore, definition_hash
from controller.worker import WorkerManager

# -- transport admission reason codes (F-RUNNER-1 / F-RUNNER-4) -----------------
# Vocabulary: docs/global/baseline/2026-10-03-RUNNER-AUTHORITY-INCIDENT.md §4.1.
# Stable, non-secret identifiers. OBSERVABILITY != AUTHORITY: a code describes a
# refusal, it never grants, renews or widens anything.
TRANSPORT_DISABLED = "TRANSPORT_DISABLED"
TRANSPORT_GRANT_NOT_CONFIGURED = "TRANSPORT_GRANT_NOT_CONFIGURED"
TRANSPORT_GRANT_REGISTRY_UNAVAILABLE = "TRANSPORT_GRANT_REGISTRY_UNAVAILABLE"
TRANSPORT_GRANT_UNKNOWN = "TRANSPORT_GRANT_UNKNOWN"
TRANSPORT_GRANT_REVOKED = "TRANSPORT_GRANT_REVOKED"
TRANSPORT_GRANT_EXPIRED = "TRANSPORT_GRANT_EXPIRED"
TRANSPORT_GRANT_EXHAUSTED = "TRANSPORT_GRANT_EXHAUSTED"
TRANSPORT_GRANT_SCOPE_MISMATCH = "TRANSPORT_GRANT_SCOPE_MISMATCH"
TRANSPORT_GRANT_OK = "TRANSPORT_GRANT_OK"
# Not in the §4.1 table: defensive fallback for a GrantError whose discriminator
# is not one of the mapped codes. Unreachable from GrantStore.validate_row today.
TRANSPORT_GRANT_INVALID = "TRANSPORT_GRANT_INVALID"
# Warning only (F-RUNNER-4, §4.2); never a refusal code.
TRANSPORT_GRANT_EXPIRING = "TRANSPORT_GRANT_EXPIRING"

TRANSPORT_REFUSAL_CODES = frozenset(
    {
        TRANSPORT_DISABLED,
        TRANSPORT_GRANT_NOT_CONFIGURED,
        TRANSPORT_GRANT_REGISTRY_UNAVAILABLE,
        TRANSPORT_GRANT_UNKNOWN,
        TRANSPORT_GRANT_REVOKED,
        TRANSPORT_GRANT_EXPIRED,
        TRANSPORT_GRANT_EXHAUSTED,
        TRANSPORT_GRANT_SCOPE_MISMATCH,
        TRANSPORT_GRANT_INVALID,
    }
)

_GRANT_CODE_TO_TRANSPORT = {
    GRANT_MISSING: TRANSPORT_GRANT_NOT_CONFIGURED,
    GRANT_UNKNOWN: TRANSPORT_GRANT_UNKNOWN,
    GRANT_REVOKED: TRANSPORT_GRANT_REVOKED,
    GRANT_EXPIRED: TRANSPORT_GRANT_EXPIRED,
    GRANT_EXHAUSTED: TRANSPORT_GRANT_EXHAUSTED,
    GRANT_SCOPE_MISMATCH: TRANSPORT_GRANT_SCOPE_MISMATCH,
}

# Journal rate limit (see AdmissionJournal). A refusal repeated on every poll
# is written on a change of state and then at most once per this interval.
JOURNAL_REPEAT_SECONDS = 900.0
# The pre-expiry warning is advisory, so it repeats more slowly.
JOURNAL_WARNING_REPEAT_SECONDS = 3600.0

_JOURNAL_UNSAFE = re.compile(r"[^A-Za-z0-9._:@/+-]")


def transport_reason_code(exc: BaseException) -> str:
    """Map a validation failure to a transport reason code.

    Derived only from the exception *type* and its ``code`` discriminator set
    at the raise site, never from the message text. Anything that is not a
    GrantError is an infrastructure fault, not an authority refusal.
    """
    if not isinstance(exc, GrantError):
        return TRANSPORT_GRANT_REGISTRY_UNAVAILABLE
    return _GRANT_CODE_TO_TRANSPORT.get(getattr(exc, "code", ""), TRANSPORT_GRANT_INVALID)


def _iso_utc(epoch: float) -> str | None:
    try:
        return datetime.fromtimestamp(float(epoch), tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def transport_admission_state(
    config: ControllerConfig, grants, *, now: float | None = None
) -> dict:
    """Pure read of the queued-transport admission state. No side effects.

    Evaluates the same gates, in the same order, with the same validator
    (``GrantStore.validate_row``) as ``Controller.admit_queued_jobs``, so the
    reported state cannot drift from what admission does. It writes nothing,
    consumes nothing and exposes only the grant id and expiry of the grant.

    ``state`` is one of:
      - ``ok``        grant valid (``warning`` may be TRANSPORT_GRANT_EXPIRING)
      - ``disabled``  queued transport is off by configuration (not an error)
      - ``refusing``  enabled, and every queued job is refused for ``code``
      - ``unknown``   the registry could not be read; admission fails closed,
                      but whether the grant is valid is NOT known
    """
    now = time.time() if now is None else now
    grant_id = config.transport_grant_id
    out: dict = {
        "enabled": bool(config.queued_transport_enabled),
        "grant_id": grant_id,
        "code": TRANSPORT_DISABLED,
        "state": "disabled",
        "expires_at": None,
        "expires_in_seconds": None,
        "warning": None,
    }
    if not config.queued_transport_enabled:
        return out
    if not grant_id:
        out.update(code=TRANSPORT_GRANT_NOT_CONFIGURED, state="refusing")
        return out
    out.update(code=TRANSPORT_GRANT_REGISTRY_UNAVAILABLE, state="unknown")
    if grants is None:
        return out
    try:
        row = grants.get(grant_id)
    except Exception:
        return out
    expires_raw = None
    if row is not None:
        try:
            expires_raw = dict(row).get("expires_at")
        except Exception:
            return out
    repository = f"{config.github.owner}/{config.github.repo}"
    try:
        GrantStore.validate_row(row, grant_id=grant_id, repository=repository, now=now)
    except GrantError as exc:
        out.update(code=transport_reason_code(exc), state="refusing")
    except Exception:
        return out
    else:
        out.update(code=TRANSPORT_GRANT_OK, state="ok")
    if expires_raw is not None:
        out["expires_at"] = _iso_utc(expires_raw)
        try:
            remaining = int(float(expires_raw) - now)
        except (TypeError, ValueError, OverflowError):
            remaining = None
        out["expires_in_seconds"] = remaining
        warn = config.transport_grant_warn_seconds
        # Only a still-valid grant can be "expiring" (validate_row passed, so
        # expires_at > now); the boundary is inclusive: remaining == warn warns.
        if (
            out["code"] == TRANSPORT_GRANT_OK
            and remaining is not None
            and warn > 0
            and float(expires_raw) - now <= warn
        ):
            out["warning"] = TRANSPORT_GRANT_EXPIRING
    return out


class AdmissionJournal:
    """Rate-limited single-line stderr journal (captured by systemd).

    Rule, per event name: a line is written when its dedup key differs from
    the key of the last line written for that event (a change of state), and
    otherwise at most once per ``repeat_seconds`` measured with the injected
    clock. Suppressed occurrences are counted and reported as ``repeats=N`` on
    the next line. The state is in-memory and per process, so a restart (or
    each `once` invocation) starts from a clean slate and logs again.

    Lines carry only caller-supplied ``key=value`` fields; values are reduced
    to a conservative character set and length so a line can never span
    multiple journal entries. A failing stream never propagates: observability
    must not be able to break (or bypass) admission.
    """

    def __init__(self, *, clock=time.time, stream=None):
        self._clock = clock
        self._stream = stream
        self._last: dict[str, tuple[tuple, float, int]] = {}

    @staticmethod
    def _safe(value: object) -> str:
        if value is None or value == "":
            return "-"
        return _JOURNAL_UNSAFE.sub("?", str(value))[:128]

    def last_key(self, event: str) -> tuple | None:
        entry = self._last.get(event)
        return entry[0] if entry else None

    def forget(self, event: str) -> None:
        self._last.pop(event, None)

    def emit(
        self,
        event: str,
        *,
        key: tuple,
        fields: list[tuple[str, object]],
        repeat_seconds: float | None = JOURNAL_REPEAT_SECONDS,
    ) -> bool:
        """Write one line unless rate-limited. Returns True if written."""
        now = float(self._clock())
        previous = self._last.get(event)
        suppressed = 0
        if previous is not None and previous[0] == key:
            last_at, suppressed = previous[1], previous[2]
            if repeat_seconds is None or now - last_at < repeat_seconds:
                self._last[event] = (key, last_at, suppressed + 1)
                return False
        parts = [f"atlas-runner {event}"]
        parts.extend(f"{name}={self._safe(value)}" for name, value in fields)
        if suppressed:
            parts.append(f"repeats={suppressed}")
        self._last[event] = (key, now, 0)
        try:
            stream = self._stream if self._stream is not None else sys.stderr
            stream.write(" ".join(parts) + "\n")
            stream.flush()
        except (OSError, ValueError):
            pass
        return True


class Capacity:
    """Host capacity snapshot used for admission control."""

    grants = None  # optional GrantStore; set via attach_grants()

    def attach_grants(self, grants) -> None:
        self.grants = grants

    def __init__(self, *, free_memory_mb: int, free_disk_mb: int, active_workers: int):
        self.free_memory_mb = free_memory_mb
        self.free_disk_mb = free_disk_mb
        self.active_workers = active_workers


def free_memory_mb() -> int:
    """MemAvailable from /proc/meminfo in MiB."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


def free_disk_mb(path: Path) -> int:
    try:
        stats = os.statvfs(str(path))
    except OSError:
        return 0
    return (stats.f_bavail * stats.f_frsize) // (1024 * 1024)


def source_revision() -> str:
    """Best-effort git revision of the deployed tree; 'unknown' if unavailable."""
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10, check=False
        )
        revision = proc.stdout.strip()
        return revision if proc.returncode == 0 and revision else "unknown"
    except OSError:
        return "unknown"


class Controller:
    grants = None  # optional GrantStore; set via attach_grants()

    def attach_grants(self, grants) -> None:
        self.grants = grants

    def __init__(
        self,
        *,
        config: ControllerConfig,
        store: StateStore,
        docker: DockerCtl,
        github: GitHubClient | None,
        worker_manager: WorkerManager | None = None,
        allow_internal_queued_jobs: bool = False,
        allow_legacy_internal_tasks: bool = False,
        clock=time.time,
        sleeper=time.sleep,
    ):
        self.config = config
        self.store = store
        self.docker = docker
        self.github = github
        # Test/staged-release-only compatibility for untrusted transport jobs.
        # The production CLI never enables this switch.
        self.allow_internal_queued_jobs = allow_internal_queued_jobs
        self.allow_legacy_internal_tasks = allow_legacy_internal_tasks
        self.worker_manager = worker_manager or WorkerManager(
            config=config, store=store, docker=docker, github=github,
            token_provider=github.token_provider if github else None,
            source_revision=source_revision(), clock=clock,
        )
        self.clock = clock
        self.sleeper = sleeper
        self._stop = False
        self.journal = AdmissionJournal(clock=clock)

    # -- signals ---------------------------------------------------------------
    def request_stop(self, *_args: object) -> None:
        self._stop = True

    # -- capacity ----------------------------------------------------------------
    def capacity(self) -> Capacity:
        jobs_root = Path(self.config.paths.jobs_dir)
        jobs_root.mkdir(parents=True, exist_ok=True)
        reserved = self.config.worker.memory_mb * max(self.store.count_active_workers(), 0)
        return Capacity(
            free_memory_mb=free_memory_mb() - reserved,
            free_disk_mb=free_disk_mb(jobs_root),
            active_workers=self.store.count_active_workers(),
        )

    def admission_reason(self, cap: Capacity) -> str | None:
        """None if admissible, else a stable machine-readable reason."""
        if cap.active_workers >= self.config.max_concurrent_jobs:
            return "capacity:max_concurrent_jobs"
        if cap.free_memory_mb < self.config.min_free_memory_mb:
            return "capacity:min_free_memory_mb"
        if cap.free_disk_mb < self.config.min_free_disk_mb:
            return "capacity:min_free_disk_mb"
        return None

    # -- admission ---------------------------------------------------------------
    def admit_queued_jobs(self) -> list[str]:
        """Poll GitHub and admit eligible queued jobs; returns admitted task ids."""
        admitted: list[str] = []
        if self.github is None:
            return admitted
        try:
            jobs = self.github.queued_jobs(admitted_labels=self.config.label_set)
        except GitHubError:
            return admitted
        for job in jobs:
            if self._stop:
                break
            # Dedupe identity: (run_id, run_attempt, job_id) — distinct jobs in one
            # run/attempt are distinct admissions; reruns are separate identities.
            task_id = f"gh-{job.run_id}-{job.run_attempt}-{job.job_id}"
            # P1-1 (overnight admission mission): the GitHub queued transport is
            # SUPPORTED but executes only when BOTH gates hold:
            #   A. config.queued_transport_enabled (explicit production enablement)
            #   B. config.transport_grant_id resolves to a valid grant bound to
            #      this repository (never consumed per job: standing transport
            #      grant). Neither gate may bypass the other.
            # Explicit test/dev bypass (fixture/dedupe tests only): loudly
            # audited, never production authority; no grant validation implied.
            test_bypass = self.allow_internal_queued_jobs
            if not (self.config.queued_transport_enabled or test_bypass):
                self._refuse_transport(
                    task_id, reason="queued_transport_disabled", code=TRANSPORT_DISABLED
                )
                continue
            transport = None
            if test_bypass:
                self.store.audit(
                    "test_only_queue_bypass", task_id=task_id,
                    detail={"flag": "allow_internal_queued_jobs"},
                )
            else:
                transport = self.config.transport_grant_id
                if not transport:
                    self._refuse_transport(
                        task_id,
                        reason="no_transport_grant_configured",
                        code=TRANSPORT_GRANT_NOT_CONFIGURED,
                    )
                    continue
                if self.grants is None:
                    self._refuse_transport(
                        task_id,
                        reason="grant_registry_required",
                        code=TRANSPORT_GRANT_REGISTRY_UNAVAILABLE,
                        grant_id=transport,
                    )
                    continue
                repository = f"{self.config.github.owner}/{self.config.github.repo}"
                try:
                    self.grants.validate(transport, repository=repository)
                except GrantError as exc:
                    # Authority refusal. The code comes from the discriminator
                    # set at the raise site; `reason` keeps its historical
                    # free-text form for compatibility.
                    self._refuse_transport(
                        task_id,
                        reason=f"transport_grant_invalid: {exc}",
                        code=transport_reason_code(exc),
                        grant_id=transport,
                    )
                    continue
                except Exception as exc:
                    # Infrastructure fault (for example a SQLite error), NOT an
                    # authority refusal. Still fail closed. Only the exception
                    # type is recorded: its text is not under our control.
                    self._refuse_transport(
                        task_id,
                        reason=f"transport_grant_registry_unavailable: {type(exc).__name__}",
                        code=TRANSPORT_GRANT_REGISTRY_UNAVAILABLE,
                        grant_id=transport,
                    )
                    continue
            reason = self.admission_reason(self.capacity())
            definition = {
                "task_id": task_id,
                "execution_class": (
                    "internal_non_production"
                    if self.allow_internal_queued_jobs
                    else "github_transport"
                ),
                "github_run_id": job.run_id,
                "github_run_attempt": job.run_attempt,
                "github_job_id": job.job_id,
                "job_name": job.job_name,
                "labels": list(job.labels),
                "authority_reference": transport,
            }
            if transport:
                self.store.audit(
                    "transport_grant_validated",
                    task_id=task_id,
                    detail={"grant_id": transport},
                )
            outcome, _status = self.store.submit_task(task_id, definition)
            if outcome != "admitted":
                # existing_terminal / existing_active: duplicate suppression keyed on
                # (run_id, run_attempt, job_id) via the task identity above.
                self.store.audit(
                    "duplicate_suppressed",
                    task_id=task_id,
                    detail={"run_id": job.run_id, "run_attempt": job.run_attempt,
                            "job_id": job.job_id, "outcome": outcome},
                )
                continue
            if reason is not None:
                # Stay REQUESTED with reason; re-evaluated next poll. No crash loop.
                self.store.audit("admission_deferred", task_id=task_id, detail={"reason": reason})
                continue
            self._start_execution(
                task_id,
                definition,
                run_id=job.run_id,
                run_attempt=job.run_attempt,
                github_job_id=job.job_id,
            )
            admitted.append(task_id)
        return admitted

    # -- transport refusal observability (F-RUNNER-1 / F-RUNNER-4) -----------------
    def _refuse_transport(
        self, task_id: str, *, reason: str, code: str, grant_id: str | None = None
    ) -> None:
        """Record one fail-closed transport refusal. Never admits anything.

        Audit: unchanged cadence (one row per refused job per poll; de-duplication
        is F-RUNNER-6) with `reason` kept and `reason_code` added. Journal: one
        rate-limited line carrying only the code, the grant id and the job id.
        """
        detail: dict[str, object] = {"reason": reason, "reason_code": code}
        if grant_id:
            detail["grant_id"] = grant_id
        self.store.audit("blocked_authority", task_id=task_id, detail=detail)
        self.journal.emit(
            "admission_refused",
            key=(code, grant_id),
            fields=[("reason_code", code), ("grant_id", grant_id), ("task_id", task_id)],
        )

    def transport_admission_state(self, *, now: float | None = None) -> dict:
        """Pure read: {enabled, grant_id, code, state, expires_at, ...}."""
        return transport_admission_state(self.config, self.grants, now=now)

    def _observe_transport_admission(self) -> None:
        """Journal the transport admission state once per change (and then
        rate-limited), so an expired or soon-to-expire grant is visible even
        when no job is queued. Read-only; cannot affect admission."""
        if self.allow_internal_queued_jobs:
            return  # test-only bypass: no grant is evaluated, so none is reported
        try:
            state = self.transport_admission_state()
            code, grant_id = state["code"], state["grant_id"]
            if state["state"] == "disabled":
                return  # off by design: nothing to report
            if state["state"] == "ok" and state["warning"] is None:
                previous = self.journal.last_key("transport_admission")
                if previous is not None and previous[0] != "ok":
                    self.journal.emit(
                        "transport_admission",
                        key=("ok", code, grant_id),
                        fields=[("state", "ok"), ("reason_code", code), ("grant_id", grant_id)],
                        repeat_seconds=None,
                    )
                    self.journal.forget("admission_refused")
                return
            if state["state"] == "ok":
                self.journal.emit(
                    "transport_admission",
                    key=("expiring", state["warning"], grant_id),
                    fields=[
                        ("state", "expiring"),
                        ("reason_code", state["warning"]),
                        ("grant_id", grant_id),
                        ("expires_at", state["expires_at"]),
                        ("remaining_seconds", state["expires_in_seconds"]),
                    ],
                    repeat_seconds=JOURNAL_WARNING_REPEAT_SECONDS,
                )
                return
            self.journal.emit(
                "transport_admission",
                key=(state["state"], code, grant_id),
                fields=[
                    ("state", state["state"]),
                    ("reason_code", code),
                    ("grant_id", grant_id),
                    ("expires_at", state["expires_at"]),
                ],
            )
        except Exception:
            # Observability must never stop the poll loop or change admission.
            return

    def submit_task(self, definition: dict, *, validate: bool = True) -> tuple[str, str]:
        """Direct admission path used by `submit`. Returns (outcome, status_or_id)."""
        task_id = definition.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task definition requires a non-empty 'task_id'")
        if validate:
            validate_task_env(definition.get("env") or {}, allow=self.config.allow_secret_env)
        atlas_binding = any(
            key in definition
            for key in (
                "repository",
                "executor_type",
                "execution",
                "execution_id",
                "authority_reference",
            )
        )
        if not atlas_binding and not self.allow_legacy_internal_tasks:
            raise GrantMissingError("production submit requires an Atlas task binding")
        authority = definition.get("authority_reference")
        if atlas_binding and not authority:
            raise GrantMissingError("authority_reference is required for Atlas tasks")
        if atlas_binding:
            from controller.schemas import validate_atlas_task_binding

            errors = validate_atlas_task_binding(definition)
            if errors:
                raise ValueError("invalid Atlas task binding: " + "; ".join(errors))
            if self.grants is None:
                raise GrantMissingError("no durable grant registry is configured")
            # Authority consumption, task persistence and execution identity are
            # one database transaction. GitHub transport jobs never enter here.
            outcome, execution_id = self.store.admit_atlas_task(
                grants=self.grants,
                task_id=task_id,
                definition=definition,
            )
            if outcome == "admitted" or self._can_resume_execution(execution_id):
                try:
                    self._start_execution(
                        task_id,
                        definition,
                        run_id=definition.get("github_run_id"),
                        run_attempt=definition.get("github_run_attempt", 1),
                        execution_id=execution_id,
                    )
                except Exception as exc:
                    # P1-2 (overnight admission mission): the admission transaction
                    # already committed; a start failure must NOT strand consumed
                    # budget or an ambiguous REQUESTED task. Single idempotent
                    # refund + REJECTED, durable and reconciliation-safe.
                    self.store.fail_admitted_start(
                        task_id=task_id,
                        execution_id=execution_id,
                        reason=f"start_failed: {exc}"[:200],
                    )
                    raise
            return outcome, execution_id
        outcome, status = self.store.submit_task(task_id, definition)
        if outcome == "admitted":
            self._start_execution(
                task_id,
                definition,
                run_id=definition.get("github_run_id"),
                run_attempt=definition.get("github_run_attempt", 1),
            )
            return outcome, self.store.get_execution(
                self._latest_execution_id(task_id)
            )["execution_id"]
        return outcome, status

    def _latest_execution_id(self, task_id: str) -> str:
        row = self.store._conn.execute(
            "SELECT execution_id FROM executions WHERE task_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"no execution for task {task_id}")
        return str(row["execution_id"])

    def _can_resume_execution(self, execution_id: str) -> bool:
        row = self.store.get_execution(execution_id)
        return bool(row and row["status"] in {lifecycle.REQUESTED, lifecycle.ADMITTED})

    def _start_execution(
        self,
        task_id: str,
        definition: dict,
        *,
        run_id: int | None,
        run_attempt: int | None,
        github_job_id: int | None = None,
        execution_id: str | None = None,
    ) -> str:
        digest = definition_hash(definition)
        execution_id = execution_id or self.store.create_execution(
            task_id=task_id,
            definition_hash=digest,
            execution_id=definition.get("execution_id"),
            github_run_id=run_id,
            github_run_attempt=run_attempt,
            github_job_id=github_job_id,
        )
        execution = self.store.get_execution(execution_id)
        if execution["status"] == lifecycle.REQUESTED:
            self.store.transition(execution_id, lifecycle.ADMITTED)
            self.store.update_task_status(task_id, lifecycle.ADMITTED)
        self.worker_manager.run_execution(execution, definition=definition)
        row = self.store.get_execution(execution_id)
        self.store.update_task_status(task_id, row["status"], reason=row.get("failure_reason"))
        return execution_id

    # -- loops ------------------------------------------------------------------
    def poll_once(self) -> list[str]:
        """One poll cycle: reconcile leftovers, admit, heartbeat."""
        self.store.heartbeat(os.getpid())
        self._observe_transport_admission()
        return self.admit_queued_jobs()

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        while not self._stop:
            self.poll_once()
            # No reconcile here: cli.cmd_run reconciles once at startup; there is no periodic pass.
            self.sleeper(self.config.poll_interval_seconds)
