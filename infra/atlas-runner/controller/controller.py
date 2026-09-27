"""Main controller loop (AS-RUNNER-FABRIC-001).

Contract: poll GitHub for queued jobs (no inbound ports), admit by label
subset + capacity, orchestrate one worker per admitted job, reconcile on
startup and periodically. POLLING != AUTHORITY: a queued job is a request,
never an instruction; admission control decides. HEARTBEAT != HEALTH: the
heartbeat row is a liveness signal only, cross-checked by health.py.
"""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path

from controller import lifecycle
from controller.config import ControllerConfig, validate_task_env
from controller.dockerctl import DockerCtl
from controller.github import GitHubClient, GitHubError
from controller.state import StateStore, definition_hash
from controller.worker import WorkerManager


class Capacity:
    """Host capacity snapshot used for admission control."""

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
    def __init__(
        self,
        *,
        config: ControllerConfig,
        store: StateStore,
        docker: DockerCtl,
        github: GitHubClient | None,
        worker_manager: WorkerManager | None = None,
        clock=time.time,
        sleeper=time.sleep,
    ):
        self.config = config
        self.store = store
        self.docker = docker
        self.github = github
        self.worker_manager = worker_manager or WorkerManager(
            config=config, store=store, docker=docker, github=github,
            token_provider=github.token_provider if github else None,
            source_revision=source_revision(), clock=clock,
        )
        self.clock = clock
        self.sleeper = sleeper
        self._stop = False

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
            existing = self.store.find_execution_by_run(job.run_id, job.run_attempt)
            if existing is not None:
                continue  # duplicate suppression: one execution per (run, attempt)
            reason = self.admission_reason(self.capacity())
            task_id = f"gh-{job.run_id}-{job.run_attempt}-{job.job_id}"
            definition = {
                "task_id": task_id,
                "github_run_id": job.run_id,
                "github_run_attempt": job.run_attempt,
                "github_job_id": job.job_id,
                "job_name": job.job_name,
                "labels": list(job.labels),
            }
            outcome, _status = self.store.submit_task(task_id, definition)
            if outcome != "admitted":
                continue
            if reason is not None:
                # Stay REQUESTED with reason; re-evaluated next poll. No crash loop.
                self.store.audit("admission_deferred", task_id=task_id, detail={"reason": reason})
                continue
            self._start_execution(
                task_id, definition, run_id=job.run_id, run_attempt=job.run_attempt
            )
            admitted.append(task_id)
        return admitted

    def submit_task(self, definition: dict, *, validate: bool = True) -> tuple[str, str]:
        """Direct admission path used by `submit`. Returns (outcome, status_or_id)."""
        task_id = definition.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task definition requires a non-empty 'task_id'")
        if validate:
            validate_task_env(definition.get("env") or {}, allow=self.config.allow_secret_env)
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

    def _start_execution(
        self,
        task_id: str,
        definition: dict,
        *,
        run_id: int | None,
        run_attempt: int | None,
    ) -> str:
        digest = definition_hash(definition)
        execution_id = self.store.create_execution(
            task_id=task_id,
            definition_hash=digest,
            github_run_id=run_id,
            github_run_attempt=run_attempt,
        )
        execution = self.store.get_execution(execution_id)
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
        return self.admit_queued_jobs()

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        while not self._stop:
            self.poll_once()
            # reconcile is run by the CLI on startup and periodically here
            self.sleeper(self.config.poll_interval_seconds)
