"""Session-bound, idempotent recovery for captured Vault events."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import capture_event
import check_documentation
from internal import event_reader

from agent_control import agent_identity, event_client, session, skill_loader, vault_identity

ROOT = Path(__file__).resolve().parents[1]
NORMALIZE_SCRIPT = ROOT / "scripts" / "normalize_event.py"
ROUTE_SCRIPT = ROOT / "scripts" / "route_event.py"
SKILL_DIR = ROOT / "skills" / "atlas-governed-work"
# The normalization directory must include the repository's companion
# MDA-STANDARD.md.  The installed governance skill above is still used to
# validate the session's acknowledged skill identity.
MDA_SKILL_DIR = ROOT
_RUN_COMMAND: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run


class EventReprocessError(ValueError):
    """A fail-closed error with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _run_command(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Isolated subprocess seam; production always uses the OS process runner."""
    return _RUN_COMMAND(args, **kwargs)


def _event_registry(state: dict[str, Any]) -> dict[str, str]:
    events = state.get("events")
    if not isinstance(events, dict):
        raise EventReprocessError(
            "SESSION_EVENT_ACCOUNTING_MISMATCH", "session events are malformed"
        )
    registry: dict[str, str] = {}
    for kind, values in events.items():
        if not isinstance(kind, str) or not isinstance(values, list):
            raise EventReprocessError(
                "SESSION_EVENT_ACCOUNTING_MISMATCH", "session event groups are malformed"
            )
        for event_id in values:
            if not isinstance(event_id, str) or not event_reader.EVENT_ID_PATTERN.fullmatch(
                event_id
            ):
                raise EventReprocessError(
                    "SESSION_EVENT_ACCOUNTING_MISMATCH", "session contains an unsafe event id"
                )
            previous = registry.get(event_id)
            if previous is not None and previous != kind:
                raise EventReprocessError(
                    "SESSION_EVENT_ACCOUNTING_MISMATCH",
                    "event id is registered under multiple event kinds",
                )
            registry[event_id] = kind
    return registry


def _validate_context(vault_root: Path, session_id: str, state: dict[str, Any]) -> dict[str, str]:
    for key in ("session", "agent", "skill", "vault", "preflight", "capability"):
        if not isinstance(state.get(key), dict):
            raise EventReprocessError(
                "SESSION_CONTEXT_INVALID", f"session {key} context is malformed"
            )
    if not isinstance(state.get("skill_acknowledgement", {}), dict):
        raise EventReprocessError(
            "SKILL_ACKNOWLEDGEMENT_INVALID", "skill acknowledgement is malformed"
        )
    identity = vault_identity.read(vault_root)
    if identity.vault_id == "" or identity.vault_uuid in {"", "unknown", "placeholder"}:
        raise EventReprocessError("VAULT_IDENTITY_INVALID", "Vault identity is incomplete")
    if state.get("session", {}).get("session_id") != session_id:
        raise EventReprocessError(
            "SESSION_BINDING_MISMATCH", "loaded session id does not match request"
        )
    if (
        state.get("status") == "superseded"
        or state.get("session", {}).get("status") == "superseded"
        or state.get("superseded_by")
        or state.get("session", {}).get("superseded_by")
    ):
        raise EventReprocessError("SESSION_SUPERSEDED", "superseded sessions cannot be reprocessed")
    bound_vault = state.get("vault", {})
    if (
        Path(str(bound_vault.get("root", ""))).expanduser().resolve() != identity.root
        or bound_vault.get("vault_id") != identity.vault_id
        or bound_vault.get("vault_uuid") != identity.vault_uuid
    ):
        raise EventReprocessError(
            "SESSION_VAULT_BINDING_MISMATCH", "session is not bound to this Vault"
        )
    if state.get("preflight", {}).get("spool", {}).get("mode") is True:
        raise EventReprocessError(
            "SPOOL_SESSION_UNSUPPORTED", "offline spool sessions must use sync-spool"
        )
    skill = state.get("skill", {})
    ack = state.get("skill_acknowledgement", {})
    try:
        current_skill = skill_loader.load(SKILL_DIR)
    except (OSError, ValueError) as exc:
        raise EventReprocessError(
            "SKILL_BINDING_INVALID", "current governed-work skill is invalid"
        ) from exc
    if not skill.get("id") or not skill.get("version") or not skill.get("sha256"):
        raise EventReprocessError("SKILL_BINDING_INVALID", "session skill binding is incomplete")
    if (skill.get("id"), skill.get("version"), skill.get("sha256")) != (
        current_skill.skill_id,
        current_skill.version,
        current_skill.sha256,
    ):
        raise EventReprocessError(
            "SKILL_BINDING_INVALID", "session skill does not match the current governed-work skill"
        )
    if any(
        ack.get(key) != value
        for key, value in (
            ("skill_id", skill.get("id")),
            ("version", skill.get("version")),
            ("sha256", skill.get("sha256")),
            ("session_id", session_id),
            ("agent_id", state.get("agent", {}).get("agent_id")),
            ("adapter_id", state.get("agent", {}).get("adapter_id", "unknown")),
        )
    ):
        raise EventReprocessError(
            "SKILL_ACKNOWLEDGEMENT_INVALID", "session skill acknowledgement is missing or stale"
        )
    try:
        acknowledged_at = datetime.fromisoformat(
            str(ack.get("acknowledged_at", "")).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise EventReprocessError(
            "SKILL_ACKNOWLEDGEMENT_INVALID", "skill acknowledgement timestamp is invalid"
        ) from exc
    if acknowledged_at.tzinfo is None:
        raise EventReprocessError(
            "SKILL_ACKNOWLEDGEMENT_INVALID",
            "skill acknowledgement timestamp must include a timezone",
        )
    preflight = state["preflight"]
    readiness = preflight.get("readiness")
    if not isinstance(readiness, dict):
        raise EventReprocessError(
            "PREFLIGHT_NOT_READY", "current adapter readiness context is malformed"
        )
    if preflight.get("ok") is not True or readiness.get("authorized") is not True:
        raise EventReprocessError(
            "PREFLIGHT_NOT_READY", "current preflight or adapter readiness is not authorized"
        )
    if state.get("capability", {}).get("ready") is not True:
        raise EventReprocessError("CAPABILITY_NOT_READY", "session capability is not ready")
    return {"vault_id": identity.vault_id, "vault_uuid": identity.vault_uuid}


def _event_paths(vault_root: Path, event_id: str) -> Path:
    sources = vault_root / "sources" / "agent-events"
    matches = list(sources.glob(f"*/*/*/{event_id}.md")) if sources.is_dir() else []
    if not matches:
        raise EventReprocessError(
            "RAW_EVENT_MISSING", f"no canonical raw event exists for {event_id}"
        )
    if len(matches) != 1:
        raise EventReprocessError(
            "RAW_EVENT_AMBIGUOUS", f"multiple canonical raw events exist for {event_id}"
        )
    raw_path = matches[0]
    if raw_path.is_symlink() or not raw_path.is_file():
        raise EventReprocessError(
            "RAW_EVENT_INVALID", f"canonical raw event is not a regular file: {event_id}"
        )
    relative_parts = raw_path.relative_to(sources).parts
    try:
        if len(relative_parts) != 4:
            raise ValueError("unexpected source depth")
        datetime.strptime("/".join(relative_parts[:3]), "%Y/%m/%d")
    except ValueError as exc:
        raise EventReprocessError(
            "RAW_EVENT_PATH_MISMATCH", f"raw event {event_id} is outside a canonical date path"
        ) from exc
    resolved = raw_path.resolve()
    try:
        resolved.relative_to(vault_root.resolve())
    except ValueError as exc:
        raise EventReprocessError(
            "RAW_EVENT_INVALID", f"raw event escapes the requested Vault: {event_id}"
        ) from exc
    return resolved


def _validate_raw(
    raw_path: Path, event_id: str, event_kind: str, state: dict[str, Any], session_id: str
) -> None:
    problems = check_documentation.validate_event(raw_path)
    if problems:
        raise EventReprocessError("RAW_EVENT_INVALID", "; ".join(problems[:3]))
    try:
        metadata = check_documentation.parse_frontmatter(raw_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise EventReprocessError(
            "RAW_EVENT_INVALID", f"raw frontmatter cannot be read: {type(exc).__name__}"
        ) from exc
    occurred_at = metadata.get("occurred_at", "")
    try:
        occurred = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise EventReprocessError(
            "RAW_EVENT_INVALID", f"raw event {event_id} has invalid occurred_at"
        ) from exc
    if raw_path.parent.relative_to(raw_path.parents[3]).as_posix() != occurred.strftime("%Y/%m/%d"):
        raise EventReprocessError(
            "RAW_EVENT_PATH_MISMATCH", f"raw event {event_id} is not under its occurred_at date"
        )
    expected: dict[str, str] = {
        "event_id": event_id,
        "session_id": session_id,
        "project_slug": str(state.get("session", {}).get("project_id", "")),
        "project_id": f"PRJ-{str(state.get('session', {}).get('project_id', '')).upper()}",
        "agent": str(state.get("agent", {}).get("agent_id", "")),
        "adapter_id": str(state.get("agent", {}).get("adapter_id", "unknown")),
        "skill_id": str(state.get("skill", {}).get("id", "")),
        "skill_version": str(state.get("skill", {}).get("version", "")),
        "skill_sha256": str(state.get("skill", {}).get("sha256", "")),
        "event_kind": event_kind,
    }
    for key, wanted in expected.items():
        if not wanted or metadata.get(key) != wanted:
            raise EventReprocessError(
                "SESSION_EVENT_BINDING_MISMATCH", f"raw event {event_id} has mismatched {key}"
            )
    work_package = metadata.get("work_package", "")
    if work_package != str(state.get("session", {}).get("task_id", "")):
        raise EventReprocessError(
            "SESSION_EVENT_BINDING_MISMATCH", f"raw event {event_id} has mismatched work_package"
        )


def _load_json(
    result: subprocess.CompletedProcess[str], stage: str, event_id: str
) -> dict[str, Any]:
    if result.returncode != 0:
        detail = capture_event.redact((result.stderr or result.stdout or "command failed").strip())[
            -500:
        ]
        code = "NORMALIZATION_FAILED" if stage == "normalize" else f"{stage.upper()}_FAILED"
        raise EventReprocessError(code, f"{event_id}: {detail}")
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise EventReprocessError(
            f"{stage.upper()}_INVALID_RESPONSE", f"{event_id}: command returned invalid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise EventReprocessError(
            f"{stage.upper()}_INVALID_RESPONSE", f"{event_id}: command response is not an object"
        )
    return payload


def _expected_output(vault_root: Path, raw_path: Path, event_id: str, mda_command: str) -> Path:
    command = [
        sys.executable,
        str(NORMALIZE_SCRIPT),
        "--event",
        str(raw_path),
        "--root",
        str(vault_root),
        "--mda-command",
        mda_command,
        "--skill-dir",
        str(MDA_SKILL_DIR),
        "--skill",
        "atlas-governed-work",
        "--dry-run",
        "--json",
    ]
    payload = _load_json(
        _run_command(command, capture_output=True, text=True, check=False), "dry_run", event_id
    )
    if (
        payload.get("ok") is not True
        or payload.get("status") != "dry-run"
        or payload.get("event_id") != event_id
    ):
        raise EventReprocessError(
            "NORMALIZATION_PLAN_INVALID",
            f"{event_id}: normalizer did not produce a valid output plan",
        )
    output = Path(str(payload.get("expected_output", ""))).expanduser()
    if not output.is_absolute():
        output = Path.cwd() / output
    output = output.resolve()
    try:
        output.relative_to(vault_root.resolve())
    except ValueError as exc:
        raise EventReprocessError(
            "NORMALIZATION_PLAN_INVALID", f"{event_id}: expected output escapes Vault"
        ) from exc
    return output


def _accepted_normalized(
    path: Path,
    raw_path: Path,
    raw_hash: str,
    vault_root: Path,
    state: dict[str, Any],
    event_id: str,
) -> None:
    event, problems = event_reader.read_event(path, vault_root=vault_root)
    if event is None:
        raise EventReprocessError(
            "NORMALIZED_EVENT_REJECTED", f"{event_id}: {'; '.join(problems[:3])}"
        )
    if (
        event.event_id != event_id
        or event.project_slug != state.get("session", {}).get("project_id")
        or event.work_package != state.get("session", {}).get("task_id")
        or event.raw_event_path.resolve() != raw_path.resolve()
        or event.raw_event_hash != f"sha256:{raw_hash}"
    ):
        raise EventReprocessError(
            "NORMALIZED_EVENT_BINDING_MISMATCH",
            f"normalized event {event_id} is not bound to requested session",
        )


def _quarantine_failed_output(
    output_path: Path, raw_path: Path, event_id: str
) -> Path | None:
    """Preserve a known rejected MDA artifact so its registered event can retry.

    Only a prior verification-failure record for this same event authorizes
    moving the derived output. Unknown, unregistered, or differently bound
    artifacts remain untouched and fail closed.
    """
    failure_path = raw_path.with_name(f"{raw_path.stem}.normalization-failed.json")
    if failure_path.is_symlink() or not failure_path.is_file():
        return None
    try:
        failure = json.loads(failure_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(failure, dict)
        or failure.get("event_id") != event_id
        or failure.get("category") != "verification-failed"
    ):
        return None
    digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
    quarantine_path = output_path.with_name(f"{output_path.name}.invalid-{digest}")
    if quarantine_path.exists() or quarantine_path.is_symlink():
        raise EventReprocessError(
            "NORMALIZED_EVENT_QUARANTINE_COLLISION",
            f"a preserved failed output already exists for {event_id}",
        )
    os.replace(output_path, quarantine_path)
    return quarantine_path


def _process_one(
    vault_root: Path,
    session_id: str,
    event_id: str,
    event_kind: str,
    state: dict[str, Any],
    mda_command: str,
) -> dict[str, Any]:
    raw_path = _event_paths(vault_root, event_id)
    _validate_raw(raw_path, event_id, event_kind, state, session_id)
    raw_hash = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    output_path = _expected_output(vault_root, raw_path, event_id, mda_command)
    reused = False
    if output_path.exists():
        if output_path.is_symlink() or not output_path.is_file():
            raise EventReprocessError(
                "NORMALIZED_EVENT_INVALID",
                f"existing normalized artifact is not a regular file: {event_id}",
            )
        try:
            _accepted_normalized(output_path, raw_path, raw_hash, vault_root, state, event_id)
        except EventReprocessError as exc:
            if exc.code != "NORMALIZED_EVENT_REJECTED":
                raise
            if _quarantine_failed_output(output_path, raw_path, event_id) is None:
                raise
        else:
            reused = True
    if not reused:
        command = [
            sys.executable,
            str(NORMALIZE_SCRIPT),
            "--event",
            str(raw_path),
            "--root",
            str(vault_root),
            "--mda-command",
            mda_command,
            "--skill-dir",
            str(MDA_SKILL_DIR),
            "--skill",
            "atlas-governed-work",
            "--json",
        ]
        payload = _load_json(
            _run_command(command, capture_output=True, text=True, check=False),
            "normalize",
            event_id,
        )
        output_value = Path(str(payload.get("normalized_event", ""))).expanduser()
        if (
            payload.get("ok") is not True
            or payload.get("status") != "normalized"
            or payload.get("event_id") != event_id
            or not output_value.is_absolute()
            or output_value.resolve() != output_path
        ):
            raise EventReprocessError(
                "NORMALIZATION_OUTPUT_MISMATCH",
                f"{event_id}: normalizer output did not match its dry-run plan",
            )
        if output_path.is_symlink() or not output_path.is_file():
            raise EventReprocessError(
                "NORMALIZATION_OUTPUT_MISSING",
                f"{event_id}: normalized output is not a regular file",
            )
    _accepted_normalized(output_path, raw_path, raw_hash, vault_root, state, event_id)
    route_command = [
        sys.executable,
        str(ROUTE_SCRIPT),
        "--normalized-event",
        str(output_path),
        "--vault",
        str(vault_root),
        "--json",
    ]
    route_payload = _load_json(
        _run_command(route_command, capture_output=True, text=True, check=False), "route", event_id
    )
    if route_payload.get("ok") is not True or route_payload.get("status") == "disabled":
        raise EventReprocessError("ROUTE_FAILED", f"{event_id}: router did not accept the event")
    return {
        "event_id": event_id,
        "raw_sha256": raw_hash,
        "normalized_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        "normalization": "reused" if reused else "new",
        "route_status": str(route_payload.get("status", "accepted")),
    }


def reprocess(*, vault_root: Path, session_id: str, mda_command: str) -> dict[str, Any]:
    """Reconcile all registered raw events for one managed Vault session.

    No event is captured, no raw event is modified, and this function never
    issues receipts or changes session completion authority.
    """
    root = vault_root.expanduser().resolve()
    if not mda_command or mda_command != mda_command.strip():
        raise EventReprocessError("MDA_COMMAND_INVALID", "a trusted mda command is required")
    if not agent_identity.SAFE.fullmatch(session_id):
        raise EventReprocessError(
            "SESSION_ID_INVALID", "session id contains unsafe path characters"
        )
    try:
        # Do not create the lock file (or its parent) for a path that is not
        # already a valid Vault. Recheck identity after acquiring the lock.
        vault_identity.read(root)
    except (OSError, ValueError) as exc:
        raise EventReprocessError(
            "VAULT_IDENTITY_INVALID", "requested path is not a readable Atlas Vault"
        ) from exc
    # Share document()'s same-Vault lock for the entire discover/validate/run/
    # route/accounting transaction; no second recovery lock is introduced.
    with event_client._normalization_lock(root):
        try:
            vault_identity.read(root)
            state = session.load(root, session_id)
        except OSError as exc:
            raise EventReprocessError(
                "SESSION_NOT_FOUND", f"cannot load requested session: {type(exc).__name__}"
            ) from exc
        except ValueError as exc:
            if "Vault identity" in str(exc) or "Vault ID" in str(exc):
                raise EventReprocessError("VAULT_IDENTITY_INVALID", str(exc)) from exc
            raise EventReprocessError(
                "SESSION_NOT_FOUND", f"cannot load requested session: {type(exc).__name__}"
            ) from exc
        _validate_context(root, session_id, state)
        pipeline = state.get("pipeline", {})
        if not isinstance(pipeline, dict):
            raise EventReprocessError("SESSION_PIPELINE_INVALID", "session pipeline is malformed")
        pending = pipeline.get("pending_spool", 0)
        if not isinstance(pending, int) or isinstance(pending, bool) or pending < 0:
            raise EventReprocessError(
                "SESSION_PIPELINE_INVALID", "pending_spool counter is malformed"
            )
        if pending > 0:
            raise EventReprocessError(
                "PENDING_SPOOL", "pending spool work must be processed with sync-spool"
            )
        registry = _event_registry(state)
        captured = pipeline.get("captured")
        if (
            not isinstance(captured, int)
            or isinstance(captured, bool)
            or captured < 0
            or captured != len(registry)
        ):
            raise EventReprocessError(
                "SESSION_EVENT_ACCOUNTING_MISMATCH",
                "captured count does not equal registered unique event IDs",
            )
        if any(
            not isinstance(pipeline.get(key, 0), int)
            or isinstance(pipeline.get(key, 0), bool)
            or pipeline.get(key, 0) < 0
            for key in ("normalized", "verified", "routed")
        ):
            raise EventReprocessError(
                "SESSION_PIPELINE_INVALID", "normalized/verified/routed counters are malformed"
            )
        before = {
            key: pipeline.get(key, 0)
            for key in ("captured", "normalized", "verified", "routed", "pending_spool")
        }
        outcomes = [
            _process_one(root, session_id, event_id, registry[event_id], state, mda_command)
            for event_id in sorted(registry)
        ]
        latest = session.load(root, session_id)
        if _event_registry(latest) != registry:
            raise EventReprocessError(
                "SESSION_CHANGED_DURING_REPROCESS", "registered event set changed during processing"
            )
        latest_pipeline = latest.get("pipeline", {})
        if any(latest_pipeline.get(key, 0) != value for key, value in before.items()):
            raise EventReprocessError(
                "SESSION_CHANGED_DURING_REPROCESS", "pipeline accounting changed during processing"
            )
        latest_pipeline["normalized"] = captured
        latest_pipeline["verified"] = captured
        latest_pipeline["routed"] = captured
        latest_pipeline["captured"] = captured
        latest_pipeline["pending_spool"] = 0
        latest["pipeline"] = latest_pipeline
        session.save(root, latest)
        return {
            "ok": True,
            "status": "reprocessed",
            "session_id": session_id,
            "events": outcomes,
            "pipeline": {
                key: latest_pipeline[key]
                for key in ("captured", "normalized", "verified", "routed", "pending_spool")
            },
            "receipt_issued": False,
            "session_completed": False,
        }
