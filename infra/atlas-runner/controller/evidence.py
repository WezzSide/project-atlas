"""Execution evidence build / validate / write (AS-RUNNER-001, spec section 31).

Contract: evidence.json is the durable record of one worker execution.
EVIDENCE != AUTHORITY and EVIDENCE != MERGE AUTHORIZATION: it records what
ran and what was observed, nothing more. Every evidence document is
validated against schemas/execution-evidence.schema.json at write time; a
validation failure is a terminal FAILED (evidence generation failure), never
a silent success. Validation uses a strict stdlib checker so the controller
keeps zero third-party runtime deps; tests cross-check parity with the
published JSON schema via the repo's jsonschema library.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE_SCHEMA_VERSION = 1

HEX64 = re.compile(r"^[0-9a-f]{64}$")
SHA1 = re.compile(r"^[0-9a-f]{40}$")
UTC_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

REQUIRED_FIELDS = (
    "schema_version",
    "task_id",
    "execution_id",
    "github_run_id",
    "github_run_attempt",
    "worker_id",
    "runner_name",
    "runner_labels",
    "runner_image_digest",
    "repository",
    "base_revision",
    "result_revision",
    "started_at",
    "finished_at",
    "duration_seconds",
    "terminal_status",
    "exit_code",
    "tests",
    "artifacts",
    "artifact_sha256",
    "cleanup_status",
    "controller_version",
    "source_revision",
)

TERMINAL_STATUS_ENUM = ("complete", "failed", "timed_out", "cleanup_required", "unknown")
CLEANUP_ENUM = ("ok", "failed", "unknown")


class EvidenceError(ValueError):
    """Evidence failed validation; must fail closed to FAILED."""


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_evidence(document: dict) -> list[str]:
    """Return a list of validation errors (empty == valid). Strict, stdlib-only."""
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["evidence is not an object"]
    allowed = set(REQUIRED_FIELDS)
    for key in document:
        if key not in allowed:
            errors.append(f"unexpected field: {key}")
    for key in REQUIRED_FIELDS:
        if key not in document:
            errors.append(f"missing field: {key}")
    if errors:
        return errors

    def _check(cond: bool, msg: str) -> None:
        if not cond:
            errors.append(msg)

    _check(document["schema_version"] == EVIDENCE_SCHEMA_VERSION, "schema_version must be 1")
    for key in ("task_id", "execution_id", "worker_id", "runner_name", "repository"):
        _check(
            isinstance(document[key], str) and bool(document[key]),
            f"{key} must be a non-empty string",
        )
    _check(
        document["runner_image_digest"] is None
        or isinstance(document["runner_image_digest"], str),
        "runner_image_digest must be string or null",
    )
    _check(
        isinstance(document["github_run_id"], int) and document["github_run_id"] > 0,
        "github_run_id must be a positive integer",
    )
    _check(
        isinstance(document["github_run_attempt"], int) and document["github_run_attempt"] > 0,
        "github_run_attempt must be a positive integer",
    )
    _check(
        isinstance(document["runner_labels"], list)
        and all(isinstance(label, str) for label in document["runner_labels"]),
        "runner_labels must be a list of strings",
    )
    for key in ("base_revision", "result_revision"):
        value = document[key]
        _check(
            value is None
            or (isinstance(value, str) and (SHA1.match(value) or HEX64.match(value))),
            f"{key} must be null or a hex revision",
        )
    for key in ("started_at", "finished_at"):
        _check(
            isinstance(document[key], str) and UTC_ISO.match(document[key]),
            f"{key} must be UTC ISO-8601",
        )
    _check(
        isinstance(document["duration_seconds"], (int, float))
        and document["duration_seconds"] >= 0,
        "duration_seconds must be a number >= 0",
    )
    _check(document["terminal_status"] in TERMINAL_STATUS_ENUM, "terminal_status not in enum")
    _check(
        document["exit_code"] is None or isinstance(document["exit_code"], int),
        "exit_code must be int or null",
    )
    _check(isinstance(document["tests"], dict), "tests must be an object")
    _check(isinstance(document["artifacts"], list), "artifacts must be a list")
    _check(isinstance(document["artifact_sha256"], dict), "artifact_sha256 must be an object")
    for artifact in document["artifacts"]:
        _check(isinstance(artifact, str) and artifact, "artifacts must be non-empty strings")
    for name, digest in document["artifact_sha256"].items():
        _check(
            isinstance(name, str) and HEX64.match(digest)
            if isinstance(digest, str)
            else False,
            f"artifact_sha256[{name!r}] must be a sha256 hex",
        )
    _check(document["cleanup_status"] in CLEANUP_ENUM, "cleanup_status not in enum")
    _check(
        isinstance(document["controller_version"], str) and bool(document["controller_version"]),
        "controller_version must be a non-empty string",
    )
    _check(
        isinstance(document["source_revision"], str) and bool(document["source_revision"]),
        "source_revision must be a non-empty string",
    )
    return errors


def build_evidence(
    *,
    task_id: str,
    execution_id: str,
    github_run_id: int,
    github_run_attempt: int,
    worker_id: str,
    runner_name: str,
    runner_labels: list[str],
    runner_image_digest: str | None,
    repository: str,
    base_revision: str | None,
    result_revision: str | None,
    started_at: str,
    finished_at: str,
    terminal_status: str,
    exit_code: int | None,
    tests: dict,
    artifacts: list[str],
    artifact_sha256: dict[str, str],
    cleanup_status: str,
    controller_version: str,
    source_revision: str,
) -> dict:
    started = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    finished = datetime.strptime(finished_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "task_id": task_id,
        "execution_id": execution_id,
        "github_run_id": github_run_id,
        "github_run_attempt": github_run_attempt,
        "worker_id": worker_id,
        "runner_name": runner_name,
        "runner_labels": list(runner_labels),
        "runner_image_digest": runner_image_digest,
        "repository": repository,
        "base_revision": base_revision,
        "result_revision": result_revision,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "terminal_status": terminal_status,
        "exit_code": exit_code,
        "tests": dict(tests),
        "artifacts": list(artifacts),
        "artifact_sha256": dict(artifact_sha256),
        "cleanup_status": cleanup_status,
        "controller_version": controller_version,
        "source_revision": source_revision,
    }


def write_evidence(document: dict, target: Path) -> Path:
    """Validate then atomically write evidence.json. Raises EvidenceError."""
    errors = validate_evidence(document)
    if errors:
        raise EvidenceError("evidence validation failed: " + "; ".join(errors))
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, sort_keys=True, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".evidence-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return target


def copy_evidence(document: dict, log_dir: Path, execution_id: str) -> Path | None:
    """Best-effort copy of evidence into the log dir; returns path or None."""
    try:
        target = Path(log_dir) / f"{execution_id}.evidence.json"
        return write_evidence(document, target)
    except (OSError, EvidenceError):
        return None


def sanitize_log(text: str, secrets: list[str]) -> str:
    """Redact known secrets from collected container logs before persisting."""
    from controller.github import redact_secrets

    return redact_secrets(text, secrets)


def collect_artifact_hashes(workspace: Path, artifact_names: list[str]) -> dict[str, str]:
    """Hash workspace artifacts named in the evidence fragment; missing -> skipped."""
    hashes: dict[str, str] = {}
    for name in artifact_names:
        path = Path(workspace) / name
        if path.is_file():
            hashes[name] = sha256_file(path)
    return hashes


def verify_artifact_hashes(workspace: Path, artifact_sha256: dict[str, str]) -> dict[str, bool]:
    """Re-hash recorded artifacts; False marks a tamper/mismatch. PASS != UNALTERED."""
    return {
        name: (Path(workspace) / name).is_file() and sha256_file(Path(workspace) / name) == digest
        for name, digest in artifact_sha256.items()
    }
