"""Runtime configuration for the atlas-runner controller (AS-RUNNER-001).

Contract: TOML config parsed with stdlib tomllib into validated dataclasses.
Unknown keys are rejected (fail closed). The JSON schema
schemas/controller-config.schema.json is the published contract; this module
enforces an equivalent ruleset with zero third-party runtime dependencies so
a broken/missing jsonschema install can never widen the surface.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_LABELS = ["self-hosted", "linux", "x64", "atlas", "executor"]
_LABEL_RE = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_ENV_KEY_RE = r"[A-Z_][A-Z0-9_]*"
_ENV_DENY_SUBSTRINGS = ("TOKEN", "SECRET", "KEY", "PASSWORD")

# Keys allowed inside a worker task env block when not denied by default.
# Deny-listed substrings win unless the key is explicitly listed here.
_ALLOWED_SECRET_ENV: frozenset[str] = frozenset()


class ConfigError(ValueError):
    """Raised when configuration is invalid; always fail closed."""


def _reject_unknown(section: str, data: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ConfigError(f"unknown config key(s) in [{section}]: {', '.join(unknown)}")


def _require_type(section: str, key: str, value: Any, types: type | tuple[type, ...]) -> Any:
    if not isinstance(value, types):
        raise ConfigError(f"[{section}] {key}: expected {types}, got {type(value).__name__}")
    return value


def _positive_int(section: str, key: str, value: int, *, minimum: int = 1) -> int:
    _require_type(section, key, value, int)
    if value < minimum:
        raise ConfigError(f"[{section}] {key}: must be >= {minimum}")
    return value


def _positive_float(section: str, key: str, value: float | int, *, minimum: float = 0.0) -> float:
    _require_type(section, key, value, (int, float))
    if isinstance(value, bool) or float(value) < minimum:
        raise ConfigError(f"[{section}] {key}: must be a number >= {minimum}")
    return float(value)


@dataclass(frozen=True)
class GitHubConfig:
    owner: str
    repo: str
    api_url: str = "https://api.github.com"


@dataclass(frozen=True)
class WorkerConfig:
    cpus: float = 2.0
    memory_mb: int = 4096
    pids: int = 512
    timeout_seconds: int = 1800
    storage_mb: int = 10240
    image: str = "atlas-runner-worker:latest"
    network: str = "bridge"
    env: dict[str, str] = field(default_factory=dict)
    # Release-bound identity (set only by controller.worker_image.bind_config from the
    # release's worker-image.json; deliberately NOT a TOML key).
    image_id: str | None = None
    image_revision: str | None = None


@dataclass(frozen=True)
class PathsConfig:
    state_dir: str = "/var/lib/atlas-runner/state"
    jobs_dir: str = "/var/lib/atlas-runner/jobs"
    log_dir: str = "/var/log/atlas-runner"


@dataclass(frozen=True)
class RetentionConfig:
    evidence_days: int = 30
    workspace_hours: int = 24


@dataclass(frozen=True)
class ControllerConfig:
    github: GitHubConfig
    worker: WorkerConfig = field(default_factory=WorkerConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    poll_interval_seconds: float = 10.0
    max_concurrent_jobs: int = 1
    labels: tuple[str, ...] = tuple(DEFAULT_LABELS)
    min_free_memory_mb: int = 1024
    min_free_disk_mb: int = 5120
    allow_secret_env: tuple[str, ...] = ()
    # Grant that authorizes the fabric itself to admit GitHub-queued transport
    # jobs (acceptance/executor/smoke). Queued jobs without a resolvable
    # transport grant are rejected — no admission path bypasses authority.
    transport_grant_id: str | None = None
    # Production enablement of the GitHub queued transport path. Fail-closed
    # default: queued jobs are rejected unless this is explicitly enabled AND
    # transport_grant_id resolves to a valid grant. Neither gate may bypass
    # the other (overnight admission mission, owner disposition P1-1).
    queued_transport_enabled: bool = False

    @property
    def label_set(self) -> frozenset[str]:
        return frozenset(self.labels)


def _opt_str(name: str, value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{name} must be a non-empty string or null")
    return value


def _parse_github(data: dict[str, Any]) -> GitHubConfig:
    _reject_unknown("github", data, {"owner", "repo", "api_url"})
    for key in ("owner", "repo"):
        if key not in data:
            raise ConfigError(f"[github] missing required key: {key}")
    owner = _require_type("github", "owner", data["owner"], str)
    repo = _require_type("github", "repo", data["repo"], str)
    if not owner or not repo or "/" in owner or "/" in repo:
        raise ConfigError("[github] owner/repo must be non-empty single path segments")
    api_url = data.get("api_url", "https://api.github.com")
    _require_type("github", "api_url", api_url, str)
    if not api_url.startswith("https://"):
        raise ConfigError("[github] api_url must be https://")
    return GitHubConfig(owner=owner, repo=repo, api_url=api_url.rstrip("/"))


def _parse_worker(data: dict[str, Any]) -> WorkerConfig:
    import re

    _reject_unknown(
        "worker",
        data,
        {"cpus", "memory_mb", "pids", "timeout_seconds", "storage_mb", "image", "network", "env"},
    )
    env: dict[str, str] = {}
    raw_env = data.get("env", {})
    _require_type("worker", "env", raw_env, dict)
    env_key_re = re.compile(rf"^{_ENV_KEY_RE}$")
    for key, value in raw_env.items():
        if not isinstance(key, str) or not env_key_re.match(key):
            raise ConfigError(f"[worker] env key {key!r} does not match {_ENV_KEY_RE!r}")
        if not isinstance(value, str):
            raise ConfigError(f"[worker] env value for {key!r} must be a string")
        env[key] = value
    network = data.get("network", "bridge")
    _require_type("worker", "network", network, str)
    if network in {"host", "none"}:
        raise ConfigError("[worker] network 'host' is not permitted; use 'bridge'")
    image = data.get("image", "atlas-runner-worker:latest")
    _require_type("worker", "image", image, str)
    if not image or any(c in image for c in " \t\n"):
        raise ConfigError("[worker] image must be a single token")
    return WorkerConfig(
        cpus=_positive_float("worker", "cpus", data.get("cpus", 2.0), minimum=0.1),
        memory_mb=_positive_int("worker", "memory_mb", data.get("memory_mb", 4096)),
        pids=_positive_int("worker", "pids", data.get("pids", 512)),
        timeout_seconds=_positive_int(
            "worker", "timeout_seconds", data.get("timeout_seconds", 1800)
        ),
        storage_mb=_positive_int("worker", "storage_mb", data.get("storage_mb", 10240)),
        image=image,
        network=network,
        env=env,
    )


def _parse_paths(data: dict[str, Any]) -> PathsConfig:
    _reject_unknown("paths", data, {"state_dir", "jobs_dir", "log_dir"})
    out = PathsConfig()
    for key in ("state_dir", "jobs_dir", "log_dir"):
        if key in data:
            value = _require_type("paths", key, data[key], str)
            if not value.startswith("/"):
                raise ConfigError(f"[paths] {key} must be an absolute path")
            out = PathsConfig(
                **{**out.__dict__, key: value}
            )
    return out


def _parse_retention(data: dict[str, Any]) -> RetentionConfig:
    _reject_unknown("retention", data, {"evidence_days", "workspace_hours"})
    return RetentionConfig(
        evidence_days=_positive_int("retention", "evidence_days", data.get("evidence_days", 30)),
        workspace_hours=_positive_int(
            "retention", "workspace_hours", data.get("workspace_hours", 24)
        ),
    )


def parse_config(data: dict[str, Any]) -> ControllerConfig:
    """Validate a raw TOML-decoded mapping into a ControllerConfig. Fail closed."""
    import re

    if not isinstance(data, dict):
        raise ConfigError("config root must be a TOML table")
    _reject_unknown(
        "root",
        data,
        {
            "github",
            "worker",
            "paths",
            "retention",
            "poll_interval_seconds",
            "max_concurrent_jobs",
            "labels",
            "min_free_memory_mb",
            "min_free_disk_mb",
            "allow_secret_env",
            "transport_grant_id",
            "queued_transport_enabled",
        },
    )
    if "github" not in data:
        raise ConfigError("missing required section [github]")
    github_raw = _require_type("root", "github", data["github"], dict)

    labels_raw = data.get("labels", list(DEFAULT_LABELS))
    _require_type("root", "labels", labels_raw, list)
    label_re = re.compile(rf"^{_LABEL_RE}$")
    labels: list[str] = []
    for label in labels_raw:
        if not isinstance(label, str) or not label_re.match(label):
            raise ConfigError(f"invalid runner label: {label!r}")
        if label not in labels:
            labels.append(label)

    allow_secret_env_raw = data.get("allow_secret_env", [])
    _require_type("root", "allow_secret_env", allow_secret_env_raw, list)
    transport_grant_id_raw = data.get("transport_grant_id")
    queued_transport_enabled_raw = data.get("queued_transport_enabled", False)
    env_key_re = re.compile(rf"^{_ENV_KEY_RE}$")
    allow_secret_env: list[str] = []
    for key in allow_secret_env_raw:
        if not isinstance(key, str) or not env_key_re.match(key):
            raise ConfigError(f"invalid allow_secret_env key: {key!r}")
        allow_secret_env.append(key)

    cfg = ControllerConfig(
        github=_parse_github(github_raw),
        worker=_parse_worker(_require_type("root", "worker", data.get("worker", {}), dict)),
        paths=_parse_paths(_require_type("root", "paths", data.get("paths", {}), dict)),
        retention=_parse_retention(
            _require_type("root", "retention", data.get("retention", {}), dict)
        ),
        poll_interval_seconds=_positive_float(
            "root", "poll_interval_seconds", data.get("poll_interval_seconds", 10.0), minimum=1.0
        ),
        max_concurrent_jobs=_positive_int(
            "root", "max_concurrent_jobs", data.get("max_concurrent_jobs", 1)
        ),
        labels=tuple(labels),
        min_free_memory_mb=_positive_int(
            "root", "min_free_memory_mb", data.get("min_free_memory_mb", 1024)
        ),
        min_free_disk_mb=_positive_int(
            "root", "min_free_disk_mb", data.get("min_free_disk_mb", 5120)
        ),
        allow_secret_env=tuple(allow_secret_env),
        transport_grant_id=_opt_str("transport_grant_id", transport_grant_id_raw),
        queued_transport_enabled=_require_type(
            "root", "queued_transport_enabled", queued_transport_enabled_raw, bool
        ),
    )
    # Permit explicitly allow-listed secret-shaped env keys (operator opt-in only).
    for key in cfg.worker.env:
        _validate_task_env_key(key, allow=cfg.allow_secret_env)
    return cfg


def _validate_task_env_key(key: str, *, allow: tuple[str, ...] = ()) -> None:
    import re

    if not re.match(rf"^{_ENV_KEY_RE}$", key):
        raise ConfigError(f"env key {key!r} does not match {_ENV_KEY_RE!r}")
    if key in _ALLOWED_SECRET_ENV or key in allow:
        return
    for denied in _ENV_DENY_SUBSTRINGS:
        if denied in key:
            raise ConfigError(f"env key {key!r} matches deny-list substring {denied!r}")


def validate_task_env(env: dict[str, str], *, allow: tuple[str, ...] = ()) -> dict[str, str]:
    """Validate a task-supplied env block against the key pattern + deny-list."""
    if not isinstance(env, dict):
        raise ConfigError("task env must be a mapping")
    for key, value in env.items():
        _validate_task_env_key(key, allow=allow)
        if not isinstance(value, str):
            raise ConfigError(f"env value for {key!r} must be a string")
    return env


def load_config(path: str | Path) -> ControllerConfig:
    config_path = Path(path)
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    return parse_config(raw)
