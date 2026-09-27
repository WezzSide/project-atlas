"""Stdlib validators mirroring the published JSON schemas.

Contract: the controller ships zero third-party runtime dependencies, so
task admission is validated here with the same rules as
schemas/worker-task.schema.json. Tests cross-check parity against the JSON
schema via the repo's jsonschema library. SCHEMA_MIRROR != SOURCE_OF_TRUTH:
the .schema.json file is the published contract; this module must be kept in
lockstep with it.
"""

from __future__ import annotations

import re

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")

TASK_REQUIRED = ("schema_version", "task_id")
TASK_ALLOWED = (
    *TASK_REQUIRED,
    "github_run_id",
    "github_run_attempt",
    "job_name",
    "labels",
    "env",
    "base_revision",
    "result_revision",
    "runner_name",
)


def validate_worker_task(task: object) -> list[str]:
    """Return validation errors (empty == valid). Fail closed."""
    errors: list[str] = []
    if not isinstance(task, dict):
        return ["task is not an object"]
    for key in task:
        if key not in TASK_ALLOWED:
            errors.append(f"unexpected field: {key}")
    for key in TASK_REQUIRED:
        if key not in task:
            errors.append(f"missing field: {key}")
    if errors:
        return errors
    if task["schema_version"] != 1:
        errors.append("schema_version must be 1")
    task_id = task["task_id"]
    if not isinstance(task_id, str) or not _ID_RE.match(task_id) or len(task_id) > 128:
        errors.append("task_id must match ^[A-Za-z0-9][A-Za-z0-9._-]*$ (<=128 chars)")
    if "github_run_id" in task:
        value = task["github_run_id"]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            errors.append("github_run_id must be a positive integer")
    if "github_run_attempt" in task:
        value = task["github_run_attempt"]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            errors.append("github_run_attempt must be a positive integer")
    if "job_name" in task and not isinstance(task["job_name"], str):
        errors.append("job_name must be a string")
    if "labels" in task:
        labels = task["labels"]
        if not isinstance(labels, list) or not all(
                isinstance(label, str) and _ID_RE.match(label) for label in labels
            ):
            errors.append("labels must be a list of valid label strings")
    if "env" in task:
        env = task["env"]
        if not isinstance(env, dict):
            errors.append("env must be an object")
        else:
            for key, value in env.items():
                if not isinstance(key, str) or not _ENV_KEY_RE.match(key):
                    errors.append(f"env key {key!r} must match ^[A-Z_][A-Z0-9_]*$")
                if not isinstance(value, str):
                    errors.append(f"env value for {key!r} must be a string")
    for key in ("base_revision", "result_revision", "runner_name"):
        if key in task and task[key] is not None and not isinstance(task[key], str):
            errors.append(f"{key} must be a string or null")
    return errors
