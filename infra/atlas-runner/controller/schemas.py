"""Stdlib validators for the internal worker and canonical Atlas task schemas.

Production submit accepts only ``atlas-task-binding.schema.json``. The legacy
``worker-task.schema.json`` remains limited to internal/transport tasks.
Tests cross-check each mirror against its published JSON schema.
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
ATLAS_REQUIRED = {
    "schema_version", "task_id", "repository", "base_revision", "executor_type",
    "execution", "authority_reference", "execution_id",
}
ATLAS_ALLOWED = ATLAS_REQUIRED | {
    "resource_class", "timeout_seconds", "platform", "evidence_requirements"
}
EXECUTOR_TYPES = {
    "claude", "codex", "gemini", "aider", "opencode", "qwen", "atlas-native",
    "deterministic",
}


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


def validate_atlas_task_binding(task: object) -> list[str]:
    """Validate the single supported production submission contract."""
    if not isinstance(task, dict):
        return ["Atlas binding is not an object"]
    errors = [f"unexpected field: {key}" for key in task if key not in ATLAS_ALLOWED]
    errors.extend(f"missing field: {key}" for key in sorted(ATLAS_REQUIRED.difference(task)))
    if errors:
        return errors
    if task["schema_version"] != 1:
        errors.append("schema_version must be 1")
    for key in ("task_id", "execution_id", "authority_reference"):
        value = task[key]
        if not isinstance(value, str) or not _ID_RE.fullmatch(value) or len(value) > 128:
            errors.append(f"{key} must be a valid non-empty identity (<=128 chars)")
    repository = task["repository"]
    if (
        not isinstance(repository, str)
        or len(repository) > 256
        or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
    ):
        errors.append("repository must use owner/repository form")
    if not isinstance(task["base_revision"], str) or not re.fullmatch(
        r"[0-9a-f]{40}", task["base_revision"]
    ):
        errors.append("base_revision must be a 40-character SHA-1 revision")
    if not isinstance(task["executor_type"], str) or task["executor_type"] not in EXECUTOR_TYPES:
        errors.append("executor_type is not supported")
    execution = task["execution"]
    valid_command = (
        isinstance(execution, dict)
        and set(execution) == {"command"}
        and isinstance(execution["command"], str)
        and 1 <= len(execution["command"]) <= 4096
    )
    valid_prompt = (
        isinstance(execution, dict)
        and set(execution).issubset({"prompt", "allowed_tools"})
        and isinstance(execution.get("prompt"), str)
        and 1 <= len(execution["prompt"]) <= 8000
        and (
            "allowed_tools" not in execution
            or (
                isinstance(execution["allowed_tools"], list)
                and all(
                    isinstance(tool, str) and 1 <= len(tool) <= 256
                    for tool in execution["allowed_tools"]
                )
            )
        )
    )
    if not (valid_command or valid_prompt):
        errors.append("execution must contain one bounded command or prompt payload")
    if "resource_class" in task and (
        not isinstance(task["resource_class"], str)
        or task["resource_class"] not in {"small", "standard", "large"}
    ):
        errors.append("resource_class is not supported")
    timeout = task.get("timeout_seconds", 1800)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 60 <= timeout <= 7200:
        errors.append("timeout_seconds must be an integer from 60 through 7200")
    if "platform" in task and (
        not isinstance(task["platform"], str)
        or task["platform"] not in {"linux", "windows"}
    ):
        errors.append("platform is not supported")
    evidence = task.get("evidence_requirements", {})
    if not isinstance(evidence, dict) or set(evidence).difference(
        {"require_tests", "require_artifacts", "fields"}
    ):
        errors.append("evidence_requirements has unsupported fields")
    elif (
        any(
            not isinstance(evidence[k], bool)
            for k in ("require_tests", "require_artifacts")
            if k in evidence
        )
        or (
            "fields" in evidence
            and (
                not isinstance(evidence["fields"], list)
                or not all(
                    isinstance(v, str) and 1 <= len(v) <= 128 for v in evidence["fields"]
                )
            )
        )
    ):
        errors.append("evidence_requirements values are invalid")
    return errors
