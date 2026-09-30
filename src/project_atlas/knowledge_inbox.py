"""AS-2.0-INBOX-001 — Knowledge Inbox quarantine intake (≠ authority promote).

Bound to the Atlas 1.0 compatibility anchor. Never Layer B authority.

``list_inbox_items`` (AS-CODER-ALPHA-INBOX-LIST-001) is a read-only
project-scoped lens. Inbox items are observations, not Truth Core facts.
INBOX_LISTING != INBOX_MUTATION != COMMAND_EXECUTION.
"""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path
from typing import Any

from atlas_contracts.identity import join_under_root, safe_relative_component
from project_atlas.compat_anchor import (
    SNAPSHOT_ID,
    CompatibilityAnchor,
    require_compatibility_anchor,
)
from project_atlas.schema import SchemaValidationError, validate_record
from project_atlas.secrets import scan_text

PACKAGE_ID = "AS-2.0-INBOX-001"
LIST_PACKAGE_ID = "AS-CODER-ALPHA-INBOX-LIST-001"
_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
TRUTH_BOUNDARY = "KNOWLEDGE INBOX ≠ AUTHORITY PROMOTE"
SCHEMA_KIND = "knowledge-inbox-receipt"
INBOX_DIR = Path("generated") / "ops" / "inbox"
CONVERSATION_DIR = Path("generated") / "ops" / "conversation-captures"
ALLOWED_STATUS = frozenset({"quarantined", "accepted-review", "rejected"})
UNKNOWN_PROJECT = "unknown-project"
_REDACTED = "[redacted: secret-shaped value]"


class KnowledgeInboxError(ValueError):
    """Fail-closed contract error."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        tmp.replace(path)
    except OSError as exc:
        raise KnowledgeInboxError(
            f"unwritable-inbox-receipt:{type(exc).__name__}:{path}"
        ) from exc
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def build_knowledge_inbox_receipt(
    vault: Path,
    *,
    record_id: str,
    anchor: CompatibilityAnchor | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build a deterministic knowledge-inbox-receipt record."""
    _ = anchor or require_compatibility_anchor()
    rid = record_id.strip()
    if not _ID_RE.fullmatch(rid):
        raise KnowledgeInboxError("inbox-receipt-id-invalid")

    status = str(kwargs.get("status", "quarantined"))
    if status not in {"quarantined", "accepted-review", "rejected"}:
        raise KnowledgeInboxError("inbox-status-invalid")
    if bool(kwargs.get("promote_authority")):
        raise KnowledgeInboxError("inbox-authority-promote-forbidden")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "compat_snapshot_id": SNAPSHOT_ID,
        "receipt_id": rid,
        "status": status,
        "promoted_to_authority": False,
        "item_count": int(kwargs.get("item_count", 0)),
        "authority": {
            "level": "derived",
            "note": "Inbox intake never promotes Layer B authority",
        },
        "truth_boundary": TRUTH_BOUNDARY,
        "generated": {"by": "project-atlas"},
    }

    try:
        validate_record(payload, SCHEMA_KIND)
    except SchemaValidationError as exc:
        raise KnowledgeInboxError(f"knowledge-inbox-receipt-schema-invalid:{exc}") from exc
    out = vault / INBOX_DIR / f"{rid}.json"
    _atomic_write_json(out, payload)
    return payload


def _safe_project_id(project_id: str | None) -> str:
    """Require an explicit project token. No implicit portfolio-all."""
    token = (project_id or "").strip()
    if not token:
        raise KnowledgeInboxError(
            "UNSUPPORTED_SCOPE",
            "inbox list requires --project (no implicit portfolio-all)",
        )
    if len(token) > 64 or not _ID_RE.fullmatch(token):
        raise KnowledgeInboxError("MALFORMED_INPUT", "inbox-project-id-invalid")
    try:
        return safe_relative_component(token, label="project id")
    except ValueError as exc:
        raise KnowledgeInboxError("MALFORMED_INPUT", str(exc)) from exc


def _safe_receipt_id(receipt_id: str) -> str:
    """Validate a receipt/capture id as one safe relative path component."""
    token = receipt_id.strip()
    if not token or not _ID_RE.fullmatch(token):
        raise KnowledgeInboxError("MALFORMED_INPUT", "inbox-receipt-id-invalid")
    try:
        return safe_relative_component(token, label="receipt id")
    except ValueError as exc:
        raise KnowledgeInboxError("MALFORMED_INPUT", str(exc)) from exc


def _inbox_receipt_path(vault: Path, receipt_id: str) -> Path:
    """Resolve ``generated/ops/inbox/<id>.json``; never follow traversal."""
    rid = _safe_receipt_id(receipt_id)
    return join_under_root(vault / INBOX_DIR, f"{rid}.json", label="inbox receipt")


def _unknown_project_report(
    *,
    limit: int,
    status_filter: str | None,
) -> dict[str, Any]:
    """Fail-closed: unknown-project cannot own authoritative inbox inventory."""
    return {
        "schema_version": 1,
        "package": LIST_PACKAGE_ID,
        "status": "UNKNOWN",
        "reason_code": "UNKNOWN_PROJECT",
        "project_id": UNKNOWN_PROJECT,
        "count": 0,
        "limit": limit,
        "status_filter": status_filter,
        "items": [],
        "unknown": "UNKNOWN (unknown-project is not an authoritative owner)",
        "promoted_to_authority": False,
        "layer_b_writes": 0,
        "truth_boundary": TRUTH_BOUNDARY,
        "generated": {"by": "project-atlas"},
    }


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _safe_summary(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        return "UNKNOWN"
    return _REDACTED if scan_text(text) else text


def _load_receipt(vault: Path, receipt_id: str) -> dict[str, Any] | None:
    """Load an inbox receipt. Unsafe ids never escape ``generated/ops/inbox``."""
    try:
        path = _inbox_receipt_path(vault, receipt_id)
    except (KnowledgeInboxError, ValueError):
        return None
    if path.is_symlink() or not path.is_file():
        return None
    payload = _read_json_object(path)
    if payload is None:
        return None
    if payload.get("promoted_to_authority") is True:
        return None
    status = str(payload.get("status") or "")
    if status not in ALLOWED_STATUS:
        return None
    return payload


def list_inbox_items(
    vault: Path,
    *,
    project_id: str,
    status: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """List project-scoped inbox observations (read-only).

    INBOX != AUTHORITY. Missing items stay UNKNOWN. Cross-project rows
    are never returned. Orphan receipts without a project-attributable
    conversation capture are excluded (cannot prove isolation).
    This function never writes Layer B and never executes inbox commands.
    """
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise KnowledgeInboxError("VAULT_NOT_FOUND", f"vault is not a directory: {vault}")
    token = _safe_project_id(project_id)
    if limit < 1:
        raise KnowledgeInboxError("MALFORMED_INPUT", "limit must be >= 1")
    status_filter: str | None = None
    if status is not None:
        status_filter = str(status).strip()
        if status_filter not in ALLOWED_STATUS:
            raise KnowledgeInboxError("MALFORMED_INPUT", "inbox-status-invalid")
    if token == UNKNOWN_PROJECT:
        return _unknown_project_report(limit=limit, status_filter=status_filter)

    inbox_root = vault / INBOX_DIR
    capture_root = vault / CONVERSATION_DIR
    if inbox_root.is_symlink() or capture_root.is_symlink():
        raise KnowledgeInboxError("PATH_UNSAFE", "inbox path must not be a symlink")

    items: list[dict[str, Any]] = []
    if capture_root.is_dir():
        for path in sorted(capture_root.glob("ccap-*.json")):
            if path.is_symlink() or not path.is_file():
                continue
            payload = _read_json_object(path)
            if payload is None:
                continue
            owner = payload.get("project_id")
            if owner == UNKNOWN_PROJECT or owner != token:
                continue
            raw_cid = payload.get("capture_id") or path.stem
            if not isinstance(raw_cid, str):
                continue
            try:
                cid = _safe_receipt_id(raw_cid)
            except KnowledgeInboxError:
                continue
            receipt = _load_receipt(vault, cid)
            raw_inbox = payload.get("inbox")
            inbox_meta = raw_inbox if isinstance(raw_inbox, dict) else {}
            receipt_status = receipt.get("status") if receipt is not None else None
            row_status = str(receipt_status or inbox_meta.get("status") or "quarantined")
            if row_status not in ALLOWED_STATUS:
                continue
            if status_filter is not None and row_status != status_filter:
                continue
            receipt_path = "UNKNOWN"
            if receipt is not None:
                try:
                    receipt_path = _inbox_receipt_path(vault, cid).relative_to(vault).as_posix()
                except (KnowledgeInboxError, ValueError):
                    receipt_path = "UNKNOWN"
            items.append(
                {
                    "receipt_id": cid,
                    "project_id": token,
                    "status": row_status,
                    "promoted_to_authority": False,
                    "item_count": int(
                        (receipt.get("item_count") if receipt is not None else None)
                        or len(payload.get("capture_items") or [])
                    ),
                    "source_kind": "conversation-capture",
                    "summary": _safe_summary(payload.get("summary")),
                    "review_state": payload.get("review_state") or "UNKNOWN",
                    "path": path.relative_to(vault).as_posix(),
                    "receipt_path": receipt_path,
                    "authority": "derived",
                    "truth_boundary": TRUTH_BOUNDARY,
                }
            )

    items.sort(key=lambda row: str(row.get("receipt_id") or ""))
    truncated = items[:limit]
    return {
        "schema_version": 1,
        "package": LIST_PACKAGE_ID,
        "status": "ok",
        "project_id": token,
        "count": len(truncated),
        "limit": limit,
        "status_filter": status_filter,
        "items": truncated,
        "unknown": "UNKNOWN (no inbox items for project)" if not truncated else None,
        "promoted_to_authority": False,
        "layer_b_writes": 0,
        "truth_boundary": TRUTH_BOUNDARY,
        "generated": {"by": "project-atlas"},
    }
