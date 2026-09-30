"""Health lens for the atlas-runner controller (AS-RUNNER-FABRIC-001).

Contract: `health` prints a JSON report {status, checks} and exits
0 (healthy) / 1 (degraded) / 2 (blocked). HEALTH != VERIFIED: this command
never executes an agent job; it is an observability lens over host + state +
docker + GitHub reachability only.
"""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path

from controller import CONFIG_SCHEMA_VERSION, CONTROLLER_VERSION
from controller.config import ConfigError, ControllerConfig, parse_config
from controller.controller import free_disk_mb, free_memory_mb
from controller.dockerctl import DockerCtl
from controller.github import GitHubClient
from controller.state import StateStore

HEARTBEAT_STALE_SECONDS = 120.0


def run_health(
    *,
    config: ControllerConfig,
    store: StateStore,
    docker: DockerCtl,
    github: GitHubClient | None,
    config_text: str | None = None,
    now: float | None = None,
) -> tuple[dict, int]:
    """Compute the health report. Returns (report, exit_code)."""
    now = time.time() if now is None else now
    checks: dict[str, object] = {}

    # -- state DB reachable + fresh heartbeat --------------------------------
    heartbeat_ok = False
    heartbeat_detail = "no heartbeat row"
    try:
        last = store.last_heartbeat()
        if last is not None and now - last <= HEARTBEAT_STALE_SECONDS:
            heartbeat_ok = True
            heartbeat_detail = f"age={int(now - last)}s"
        elif last is not None:
            heartbeat_detail = f"stale age={int(now - last)}s"
    except Exception as exc:  # pragma: no cover - defensive
        heartbeat_detail = f"error: {type(exc).__name__}"
    checks["state_db"] = {"ok": heartbeat_ok, "detail": heartbeat_detail}

    # -- docker daemon ----------------------------------------------------------
    try:
        docker_ok, version = docker.info()
        docker_detail = version or "unreachable"
    except Exception as exc:  # pragma: no cover - defensive
        docker_ok, docker_detail = False, type(exc).__name__
    checks["docker"] = {"ok": docker_ok, "detail": docker_detail}

    # -- worker image: the image actually selected must be the release-bound one ----
    # (EXECUTOR_PY312_DEPLOYMENT_CLOSURE: a stale or moved tag is a hard failure so
    # the deploy gate rolls back instead of serving jobs from an older image.)
    bound_id = config.worker.image_id
    if bound_id is None:
        image_ok = True
        image_detail = f"unbound (config image {config.worker.image})"
    else:
        try:
            actual_id = docker.image_id(config.worker.image)
        except Exception as exc:  # pragma: no cover - defensive
            actual_id = None
            image_detail = f"error: {type(exc).__name__}"
        image_ok = actual_id == bound_id
        image_detail = (
            f"{config.worker.image} id={actual_id} revision={config.worker.image_revision}"
            if image_ok
            else f"MISMATCH {config.worker.image}: host={actual_id} bound={bound_id}"
        )
    checks["worker_image"] = {"ok": image_ok, "detail": image_detail}

    # -- state dir writable ------------------------------------------------------
    state_dir = Path(config.paths.state_dir)
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        probe = state_dir / ".health-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        writable_ok = True
        writable_detail = "ok"
    except OSError as exc:
        writable_ok, writable_detail = False, str(exc)[:120]
    checks["state_dir_writable"] = {"ok": writable_ok, "detail": writable_detail}

    # -- config parse (re-validate the raw text if provided) ----------------------
    if config_text is not None:
        try:
            import tomllib

            parse_config(tomllib.loads(config_text))
            config_ok, config_detail = True, "ok"
        except (ConfigError, tomllib.TOMLDecodeError) as exc:
            config_ok, config_detail = False, str(exc)[:120]
    else:
        config_ok, config_detail = True, "not re-validated"
    checks["config"] = {"ok": config_ok, "detail": config_detail}

    # -- capacity ---------------------------------------------------------------
    mem_free = free_memory_mb()
    jobs_root = Path(config.paths.jobs_dir)
    with contextlib.suppress(OSError):
        jobs_root.mkdir(parents=True, exist_ok=True)
    disk_free = free_disk_mb(jobs_root)
    mem_ok = mem_free >= config.min_free_memory_mb
    disk_ok = disk_free >= config.min_free_disk_mb
    checks["free_memory_mb"] = {
        "ok": mem_ok,
        "detail": f"{mem_free} free, {config.min_free_memory_mb} required",
    }
    checks["free_disk_mb"] = {
        "ok": disk_ok,
        "detail": f"{disk_free} free, {config.min_free_disk_mb} required",
    }

    # -- stale workers ------------------------------------------------------------
    try:
        active = store.count_active_workers()
        stale_ok = True
        stale_detail = f"{active} active"
    except Exception as exc:  # pragma: no cover - defensive
        stale_ok, stale_detail = False, type(exc).__name__
    checks["workers"] = {"ok": stale_ok, "detail": stale_detail}

    # -- GitHub reachability ---------------------------------------------------------
    github_ok: bool | None
    if github is None:
        github_ok, github_detail = None, "no client configured"
    else:
        try:
            github_ok, status = github.check_reachable()
            github_detail = f"status={status}"
        except Exception as exc:  # pragma: no cover - defensive
            github_ok, github_detail = False, type(exc).__name__
    checks["github_api"] = {"ok": github_ok, "detail": github_detail}

    # -- verdict -----------------------------------------------------------------------
    hard_failures = [
        name
        for name, result in checks.items()
        if result["ok"] is False and name != "github_api"
    ]
    if hard_failures:
        status, exit_code = "blocked", 2
    else:
        degraded = [
            name
            for name, result in checks.items()
            if result["ok"] is False or (name == "github_api" and result["ok"] is not True)
        ]
        status = "degraded" if degraded else "healthy"
        exit_code = 1 if degraded else 0
    report = {
        "status": status,
        "controller_version": CONTROLLER_VERSION,
        "config_schema_version": CONFIG_SCHEMA_VERSION,
        "checks": checks,
    }
    return report, exit_code


def health_json(report: dict) -> str:
    return json.dumps(report, indent=2, sort_keys=True)
