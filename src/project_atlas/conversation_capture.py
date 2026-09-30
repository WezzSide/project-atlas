"""AS-CODER-ALPHA-CONVERSATIONAL-CAPTURE-001 — provider-neutral capture.

Structured conversation envelopes land in Knowledge Inbox / quarantine.
Conversation != authority. Capture != Truth Core. Inbox != authority.
Composes with session-capture ops receipts; does not replace them.

D-042 / CAPTURE-002. Transcript extraction is not implemented in Core.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

from atlas_contracts.identity import safe_relative_component
from project_atlas.knowledge_inbox import (
    KnowledgeInboxError,
    build_knowledge_inbox_receipt,
)
from project_atlas.schema import SchemaValidationError, validate_record
from project_atlas.secrets import scan_text

PACKAGE_ID = "AS-CODER-ALPHA-CONVERSATIONAL-CAPTURE-001"
GENERATOR_ID = "atlas-coder-alpha-conversational-capture-001"
SCHEMA_NAME = "atlas.conversation-capture.v1"
SCHEMA_KIND = "conversation-capture"
CAPTURE_DIR = Path("generated") / "ops" / "conversation-captures"
TRUTH_BOUNDARY = (
    "CONVERSATION != AUTHORITY / CAPTURE != TRUTH CORE / INBOX != AUTHORITY"
)

ITEM_TYPES = frozenset(
    {
        "session_note",
        "idea",
        "observation",
        "research_finding",
        "action_item",
        "open_question",
        "proposed_decision",
        "confirmed_owner_decision",
        "claim_candidate",
        "constraint",
        "lesson_learned",
        "failed_approach",
        "next_step",
    }
)
REVIEW_STATES = frozenset({"captured", "reviewed", "rejected"})
INBOX_BY_REVIEW = {
    "captured": "quarantined",
    "reviewed": "accepted-review",
    "rejected": "rejected",
}
ALLOWED_PROVIDERS = frozenset({"chatgpt", "claude", "cursor", "codex", "other"})
PROVIDER_RE = r"^[a-z][a-z0-9-]{0,31}$"

MAX_ITEMS = 32
MAX_ITEM_CHARS = 4000
MAX_SUMMARY_CHARS = 2000
MAX_ENVELOPE_CHARS = 48_000
MAX_MESSAGE_REFS = 64

SEMANTIC_STATE = ("CAPTURED", "NON_CANONICAL", "SOURCE_BACKED", "REVIEWABLE")


class ConversationCaptureError(ValueError):
    """Fail-closed conversational capture error with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip()


def _safe_project_id(project_id: str) -> str:
    try:
        return safe_relative_component(project_id, label="project id")
    except ValueError as exc:
        raise ConversationCaptureError("PATH_SHAPED_PROJECT_ID", str(exc)) from exc


def _existing_project_ids(vault: Path) -> list[str]:
    root = vault / "projects"
    if not root.is_dir():
        return []
    return sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )


def _scan_secret_fields(*texts: str) -> None:
    for text in texts:
        if not text:
            continue
        findings = scan_text(text)
        if findings:
            names = ",".join(sorted({item.pattern for item in findings}))
            raise ConversationCaptureError(
                "SECRET_CONTENT",
                f"capture rejected: secret-shaped content ({names})",
            )


def _scan_decoded_record(record: dict[str, Any]) -> None:
    """Rescan decoded capture scalars after json.loads.

    AS-SEC-SCAN-CONVERSATION-CAPTURE-JSON-ESC-001: on-disk ``\\u`` escapes
    miss scan_text on raw bytes; review-state rewrite must not persist the
    decoded token.
    """
    texts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)

    walk(record)
    _scan_secret_fields(*texts)


def _normalize_items(raw_items: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list) or not raw_items:
        raise ConversationCaptureError(
            "MALFORMED_SCHEMA",
            "capture_items must be a non-empty list",
        )
    if len(raw_items) > MAX_ITEMS:
        raise ConversationCaptureError(
            "CAPTURE_INPUT_TOO_LARGE",
            f"capture_items exceeds {MAX_ITEMS}",
        )
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ConversationCaptureError("MALFORMED_SCHEMA", "capture item must be an object")
        item_type = _nfc(str(raw.get("item_type") or "")).lower()
        if item_type not in ITEM_TYPES:
            raise ConversationCaptureError(
                "UNSUPPORTED_ITEM_TYPE",
                f"unsupported item type {item_type!r}",
            )
        text = _nfc(str(raw.get("text") or ""))
        if not text:
            raise ConversationCaptureError("MALFORMED_SCHEMA", "capture item text is required")
        if len(text) > MAX_ITEM_CHARS:
            raise ConversationCaptureError(
                "CAPTURE_INPUT_TOO_LARGE",
                f"item text exceeds {MAX_ITEM_CHARS} characters",
            )
        _scan_secret_fields(text)
        item: dict[str, Any] = {"item_type": item_type, "text": text}
        owner_origin = raw.get("owner_origin")
        if item_type == "confirmed_owner_decision":
            if not isinstance(owner_origin, dict):
                raise ConversationCaptureError(
                    "FALSE_OWNER_DECISION",
                    "confirmed_owner_decision requires explicit owner_origin evidence",
                )
            evidence_kind = _nfc(str(owner_origin.get("evidence_kind") or ""))
            origin = _nfc(str(owner_origin.get("origin") or "")).lower()
            statement = _nfc(str(owner_origin.get("statement") or ""))
            if (
                evidence_kind != "explicit_owner_statement"
                or origin != "owner"
                or not statement
            ):
                raise ConversationCaptureError(
                    "FALSE_OWNER_DECISION",
                    "confirmed_owner_decision owner_origin contract is not satisfied",
                )
            if len(statement) > 2000:
                raise ConversationCaptureError(
                    "CAPTURE_INPUT_TOO_LARGE",
                    "owner_origin.statement exceeds 2000 characters",
                )
            _scan_secret_fields(statement)
            item["owner_origin"] = {
                "evidence_kind": "explicit_owner_statement",
                "statement": statement,
                "origin": "owner",
            }
        elif owner_origin is not None:
            raise ConversationCaptureError(
                "MALFORMED_SCHEMA",
                "owner_origin is only valid on confirmed_owner_decision",
            )
        items.append(item)
    return items


def _normalize_provider(raw: Any) -> str:
    provider = _nfc(str(raw or "")).lower()
    if not provider:
        raise ConversationCaptureError("MALFORMED_SCHEMA", "source_provider is required")
    if re.fullmatch(PROVIDER_RE, provider) is None:
        raise ConversationCaptureError(
            "MALFORMED_SCHEMA",
            f"source_provider {provider!r} is not an opaque provider token",
        )
    if provider not in ALLOWED_PROVIDERS and provider != "other":
        # Opaque extra tokens remain provider-neutral; no authority branch.
        pass
    return provider


def _normalize_refs(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConversationCaptureError("MALFORMED_SCHEMA", "source_message_refs must be a list")
    if len(raw) > MAX_MESSAGE_REFS:
        raise ConversationCaptureError(
            "CAPTURE_INPUT_TOO_LARGE",
            f"source_message_refs exceeds {MAX_MESSAGE_REFS}",
        )
    refs: list[str] = []
    for item in raw:
        text = _nfc(str(item))
        if not text:
            continue
        if len(text) > 256:
            raise ConversationCaptureError(
                "CAPTURE_INPUT_TOO_LARGE",
                "source_message_ref exceeds 256 characters",
            )
        if text not in refs:
            refs.append(text)
    return refs


def _route_project(
    vault: Path,
    envelope: dict[str, Any],
    requested_project_id: str | None,
) -> str:
    existing = _existing_project_ids(vault)
    declared = envelope.get("project_id")
    extras = envelope.get("project_ids")
    name_only = envelope.get("project_name")
    if name_only and not declared and not requested_project_id:
        raise ConversationCaptureError(
            "UNMATCHED_PROJECT",
            "project name is not a governed identity; pass an existing project_id",
        )
    candidates: list[str] = []
    if declared not in {None, ""}:
        candidates.append(_safe_project_id(str(declared)))
    if requested_project_id not in {None, ""}:
        candidates.append(_safe_project_id(str(requested_project_id)))
    if extras is not None:
        if not isinstance(extras, list):
            raise ConversationCaptureError("MALFORMED_SCHEMA", "project_ids must be a list")
        for item in extras:
            candidates.append(_safe_project_id(str(item)))
    unique = list(dict.fromkeys(candidates))
    if len(unique) > 1:
        raise ConversationCaptureError(
            "CONFLICTING_PROJECT",
            f"conflicting project references: {', '.join(unique)}",
        )
    if not unique:
        if not existing:
            raise ConversationCaptureError(
                "UNMATCHED_PROJECT",
                "no governed project identity exists in this vault",
            )
        if len(existing) > 1:
            raise ConversationCaptureError(
                "AMBIGUOUS_PROJECT",
                "ambiguous project routing; pass an explicit existing project_id "
                f"(candidates: {', '.join(existing)})",
            )
        return existing[0]
    project_id = unique[0]
    if project_id not in existing:
        raise ConversationCaptureError(
            "UNMATCHED_PROJECT",
            f"project {project_id!r} is not an existing governed identity",
        )
    return project_id


def _content_hash(
    *,
    project_id: str,
    provider: str,
    conversation_id: str,
    items: list[dict[str, Any]],
    summary: str,
    refs: list[str],
) -> str:
    canonical = {
        "schema": SCHEMA_NAME,
        "schema_version": 1,
        "project_id": project_id,
        "source_provider": provider,
        "source_conversation_id": conversation_id,
        "source_message_refs": refs,
        "summary": summary,
        "capture_items": items,
        "capture_mode": "structured_submission",
    }
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def _capture_id(content_hash: str) -> str:
    return "ccap-" + hashlib.sha256(content_hash.encode("utf-8")).hexdigest()[:16]


def _render_projection(record: dict[str, Any]) -> str:
    items = record.get("capture_items") or []
    grouped: dict[str, list[str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        grouped.setdefault(str(item.get("item_type")), []).append(str(item.get("text")))
    lines = [
        f"# Conversation capture — {record.get('capture_id')}",
        "",
        "Derived Knowledge Inbox projection. Markdown != Truth Core.",
        "CONVERSATION != AUTHORITY. CAPTURE != TRUTH CORE. REVIEW != PROMOTION.",
        "",
        f"- project: `{record.get('project_id')}`",
        f"- source_provider: `{record.get('source_provider')}`",
        f"- source_conversation_id: `{record.get('source_conversation_id') or ''}`",
        f"- capture_mode: `{record.get('capture_mode')}`",
        f"- review_state: `{record.get('review_state')}`",
        f"- authority: `{((record.get('authority') or {}).get('classification'))}`",
        f"- source_content_hash: `{record.get('source_content_hash')}`",
        "",
        "## Summary",
        str(record.get("summary") or "UNKNOWN"),
        "",
    ]
    labels = (
        ("confirmed_owner_decision", "Confirmed owner statements (still not Truth Core)"),
        ("proposed_decision", "Decisions proposed"),
        ("idea", "Ideas"),
        ("action_item", "Actions"),
        ("open_question", "Open questions"),
        ("research_finding", "Research findings"),
        ("constraint", "Constraints"),
        ("lesson_learned", "Lessons"),
        ("failed_approach", "Failed approaches"),
        ("next_step", "Next steps"),
        ("observation", "Observations"),
        ("session_note", "Session notes"),
        ("claim_candidate", "Claim candidates (not claims)"),
    )
    for key, title in labels:
        values = grouped.get(key) or []
        if not values:
            continue
        lines.append(f"## {title}")
        lines.extend(f"- {value}" for value in values)
        lines.append("")
    lines.extend(
        [
            "## Unknowns / authority warning",
            "- This capture is quarantined evidence.",
            "- REVIEWED != TRUTH CORE PROMOTED.",
            "- Do not mix these items with verified project claims.",
            "",
            "## Provenance",
            f"- receipt: `{(record.get('inbox') or {}).get('path') or 'UNKNOWN'}`",
            f"- capture: `{CAPTURE_DIR.as_posix()}/{record.get('capture_id')}.json`",
            f"- generated.by: `{GENERATOR_ID}`",
            "",
        ]
    )
    return "\n".join(lines)


def _public_receipt(record: dict[str, Any]) -> dict[str, Any]:
    items = record.get("capture_items") or []
    counts: dict[str, int] = {}
    for item in items:
        if isinstance(item, dict):
            kind = str(item.get("item_type") or "unknown")
            counts[kind] = counts.get(kind, 0) + 1
    return {
        "schema_version": 1,
        "schema": SCHEMA_NAME,
        "package": PACKAGE_ID,
        "status": "ok",
        "capture_id": record.get("capture_id"),
        "project_id": record.get("project_id"),
        "source_provider": record.get("source_provider"),
        "source_content_hash": record.get("source_content_hash"),
        "capture_schema": SCHEMA_NAME,
        "item_count": len(items) if isinstance(items, list) else 0,
        "item_kinds": counts,
        "review_state": record.get("review_state"),
        "authority": record.get("authority"),
        "semantic_state": record.get("semantic_state"),
        "artifact_paths": {
            "capture": f"{CAPTURE_DIR.as_posix()}/{record.get('capture_id')}.json",
            "projection": (record.get("projection") or {}).get("path"),
            "inbox": (record.get("inbox") or {}).get("path"),
        },
        "idempotency": record.get("idempotency"),
        "generated": record.get("generated"),
        "truth_boundary": TRUTH_BOUNDARY,
        "raw_transcript_persisted": False,
    }


def capture_conversation(
    vault: Path,
    envelope: dict[str, Any],
    *,
    requested_project_id: str | None = None,
) -> dict[str, Any]:
    """Accept a provider-neutral structured conversation envelope."""
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise ConversationCaptureError("VAULT_NOT_FOUND", f"vault is not a directory: {vault}")
    if not isinstance(envelope, dict):
        raise ConversationCaptureError("MALFORMED_SCHEMA", "envelope must be an object")
    encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
    if len(encoded) > MAX_ENVELOPE_CHARS:
        raise ConversationCaptureError(
            "CAPTURE_INPUT_TOO_LARGE",
            f"envelope exceeds {MAX_ENVELOPE_CHARS} characters",
        )
    if envelope.get("transcript") not in {None, ""}:
        raise ConversationCaptureError(
            "RAW_TRANSCRIPT_FORBIDDEN",
            "raw transcript is not persisted; submit structured capture_items",
        )
    mode = _nfc(str(envelope.get("capture_mode") or "structured_submission")).lower()
    if mode == "transcript_extraction":
        raise ConversationCaptureError(
            "TRANSCRIPT_EXTRACTION_NOT_IMPLEMENTED",
            "transcript_extraction is deferred; Core accepts structured_submission only",
        )
    if mode != "structured_submission":
        raise ConversationCaptureError("MALFORMED_SCHEMA", f"unsupported capture_mode {mode!r}")

    schema_name = _nfc(str(envelope.get("schema") or SCHEMA_NAME))
    if schema_name not in {SCHEMA_NAME, ""}:
        raise ConversationCaptureError(
            "MALFORMED_SCHEMA",
            f"unsupported schema {schema_name!r}",
        )
    if envelope.get("schema_version") not in {None, 1, "1"}:
        raise ConversationCaptureError("MALFORMED_SCHEMA", "schema_version must be 1")

    project_id = _route_project(vault, envelope, requested_project_id)
    provider = _normalize_provider(envelope.get("source_provider"))
    conversation_id = _nfc(str(envelope.get("source_conversation_id") or ""))
    if len(conversation_id) > 256:
        raise ConversationCaptureError(
            "CAPTURE_INPUT_TOO_LARGE",
            "source_conversation_id exceeds 256 characters",
        )
    refs = _normalize_refs(envelope.get("source_message_refs"))
    items = _normalize_items(envelope.get("capture_items"))
    summary = _nfc(str(envelope.get("summary") or ""))
    if not summary:
        raise ConversationCaptureError("MALFORMED_SCHEMA", "summary is required")
    if len(summary) > MAX_SUMMARY_CHARS:
        raise ConversationCaptureError(
            "CAPTURE_INPUT_TOO_LARGE",
            f"summary exceeds {MAX_SUMMARY_CHARS} characters",
        )
    _scan_secret_fields(summary, conversation_id, *refs)

    supplied_hash = envelope.get("source_content_hash")
    content_hash = _content_hash(
        project_id=project_id,
        provider=provider,
        conversation_id=conversation_id,
        items=items,
        summary=summary,
        refs=refs,
    )
    if supplied_hash not in {None, ""} and str(supplied_hash) != content_hash:
        raise ConversationCaptureError(
            "CONTENT_HASH_MISMATCH",
            "supplied source_content_hash does not match canonical capture inputs",
        )
    capture_id = _capture_id(content_hash)
    path = vault / CAPTURE_DIR / f"{capture_id}.json"
    projection_rel = f"{CAPTURE_DIR.as_posix()}/{capture_id}.md"
    inbox_rel = f"generated/ops/inbox/{capture_id}.json"

    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict) or existing.get("source_content_hash") != content_hash:
            raise ConversationCaptureError(
                "CAPTURE_ID_COLLISION",
                "existing capture id does not match this payload",
            )
        existing["idempotency"] = {
            "result": "replay",
            "same_input_same_capture_id": True,
        }
        return _public_receipt(existing)

    record: dict[str, Any] = {
        "schema_version": 1,
        "schema": SCHEMA_NAME,
        "package": PACKAGE_ID,
        "capture_id": capture_id,
        "project_id": project_id,
        "source_provider": provider,
        "source_conversation_id": conversation_id,
        "source_message_refs": refs,
        "source_content_hash": content_hash,
        "capture_mode": "structured_submission",
        "capture_items": items,
        "summary": summary,
        "provenance": {
            "source_content_hash": content_hash,
            "extraction_method": "structured_submission",
            "raw_transcript_persisted": False,
        },
        "extraction_method": "structured_submission",
        "authority": {
            "level": "quarantined-evidence",
            "classification": "NON_CANONICAL",
            "note": TRUTH_BOUNDARY,
        },
        "review_state": "captured",
        "semantic_state": list(SEMANTIC_STATE),
        "generated": {"by": GENERATOR_ID},
        "honesty": {
            "authentic_pilot": False,
            "atlas_opt_wake_gate": "CLOSED",
            "lens_is_authority": False,
            "invented_facts": False,
            "conversation_is_authority": False,
            "model_summary_is_owner_decision": False,
        },
        "truth_boundary": TRUTH_BOUNDARY,
        "inbox": {
            "receipt_id": capture_id,
            "path": inbox_rel,
            "status": "quarantined",
            "promoted_to_authority": False,
        },
        "projection": {"path": projection_rel, "canonical": False},
        "idempotency": {
            "result": "created",
            "same_input_same_capture_id": True,
        },
    }
    try:
        validate_record(record, SCHEMA_KIND)
    except SchemaValidationError as exc:
        raise ConversationCaptureError(
            "MALFORMED_SCHEMA",
            f"conversation-capture schema invalid: {exc}",
        ) from exc
    try:
        build_knowledge_inbox_receipt(
            vault,
            record_id=capture_id,
            status="quarantined",
            item_count=len(items),
        )
    except KnowledgeInboxError as exc:
        raise ConversationCaptureError("INBOX_WRITE_FAILED", str(exc)) from exc

    encoded_record = (json.dumps(record, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_atomic(path, encoded_record)
    _write_atomic(
        vault / CAPTURE_DIR / f"{capture_id}.md",
        _render_projection(record).encode("utf-8"),
    )
    _write_atomic(
        vault / CAPTURE_DIR / "latest.json",
        (
            json.dumps(
                {
                    "schema_version": 1,
                    "capture_id": capture_id,
                    "project_id": project_id,
                    "path": path.relative_to(vault).as_posix(),
                    "generated": {"by": GENERATOR_ID},
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8"),
    )
    return _public_receipt(record)


def list_conversation_captures(
    vault: Path,
    *,
    project_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List conversation captures in deterministic reverse capture_id order."""
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise ConversationCaptureError("VAULT_NOT_FOUND", f"vault is not a directory: {vault}")
    if limit < 1:
        raise ConversationCaptureError("MALFORMED_SCHEMA", "limit must be >= 1")
    if project_id is not None:
        project_id = _safe_project_id(project_id)
    root = vault / CAPTURE_DIR
    if not root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(root.glob("ccap-*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if project_id is not None and payload.get("project_id") != project_id:
            continue
        items.append(
            {
                "capture_id": payload.get("capture_id"),
                "project_id": payload.get("project_id"),
                "source_provider": payload.get("source_provider"),
                "summary": payload.get("summary"),
                "review_state": payload.get("review_state"),
                "authority": False,
                "classification": ((payload.get("authority") or {}).get("classification")),
                "item_count": len(payload.get("capture_items") or []),
                "path": path.relative_to(vault).as_posix(),
                "projection_path": ((payload.get("projection") or {}).get("path")),
                "status": "quarantined-evidence",
                "label": "Conversation capture — non-authoritative",
            }
        )
        if len(items) >= limit:
            break
    return items


def render_conversation_captures_markdown(captures: list[dict[str, Any]]) -> list[str]:
    """Render non-authoritative conversation-capture bullets for agent context."""
    lines = [
        "",
        "## Conversation capture — non-authoritative",
        "These items are quarantined conversation evidence. They are not Truth Core.",
        "Do not mix them with verified project claims.",
    ]
    if not captures:
        lines.append("- UNKNOWN (no conversation captures yet)")
        return lines
    for item in captures:
        lines.append(
            f"- [{item.get('review_state') or 'captured'}] {item.get('summary')} "
            f"(`{item.get('capture_id')}`; provider={item.get('source_provider')}; "
            "authority=false)"
        )
    return lines


def set_conversation_review_state(
    vault: Path,
    capture_id: str,
    review_state: str,
) -> dict[str, Any]:
    """Update capture/inbox review lifecycle. REVIEWED != Truth Core promotion."""
    vault = vault.expanduser().resolve()
    state = _nfc(review_state).lower()
    if state not in REVIEW_STATES:
        raise ConversationCaptureError("MALFORMED_SCHEMA", f"unsupported review_state {state!r}")
    cid = _nfc(capture_id).lower()
    if not cid.startswith("ccap-"):
        raise ConversationCaptureError("MALFORMED_SCHEMA", "capture_id is invalid")
    path = vault / CAPTURE_DIR / f"{cid}.json"
    if not path.is_file():
        raise ConversationCaptureError("UNMATCHED_CAPTURE", f"capture {cid} does not exist")
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ConversationCaptureError("MALFORMED_SCHEMA", "stored capture is not an object")
    _scan_decoded_record(record)
    record["review_state"] = state
    inbox = dict(record.get("inbox") or {})
    inbox["status"] = INBOX_BY_REVIEW[state]
    inbox["promoted_to_authority"] = False
    record["inbox"] = inbox
    try:
        validate_record(record, SCHEMA_KIND)
        build_knowledge_inbox_receipt(
            vault,
            record_id=cid,
            status=INBOX_BY_REVIEW[state],
            item_count=len(record.get("capture_items") or []),
        )
    except (SchemaValidationError, KnowledgeInboxError) as exc:
        raise ConversationCaptureError("REVIEW_UPDATE_FAILED", str(exc)) from exc
    _write_atomic(
        path,
        (json.dumps(record, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    _write_atomic(
        vault / CAPTURE_DIR / f"{cid}.md",
        _render_projection(record).encode("utf-8"),
    )
    return _public_receipt(record)


def envelope_from_cli_items(
    *,
    summary: str,
    provider: str,
    items: list[str],
    project_id: str | None = None,
    conversation_id: str = "",
) -> dict[str, Any]:
    """Build a structured envelope from compact ``type=text`` CLI items."""
    parsed: list[dict[str, str]] = []
    for raw in items:
        if "=" not in raw:
            raise ConversationCaptureError(
                "MALFORMED_SCHEMA",
                "CLI --item must be item_type=text",
            )
        item_type, text = raw.split("=", 1)
        parsed.append({"item_type": item_type, "text": text})
    envelope: dict[str, Any] = {
        "schema": SCHEMA_NAME,
        "schema_version": 1,
        "source_provider": provider,
        "source_conversation_id": conversation_id,
        "capture_mode": "structured_submission",
        "summary": summary,
        "capture_items": parsed,
    }
    if project_id:
        envelope["project_id"] = project_id
    return envelope
