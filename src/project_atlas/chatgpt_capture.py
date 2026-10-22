"""AS-2.0-CHATGPT-CAPTURE-001 — ChatGPT capture receipt (fixture; no live API).

Bound to the Atlas 1.0 compatibility anchor. Never Layer B authority.
"""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path
from typing import Any

from project_atlas.compat_anchor import (
    SNAPSHOT_ID,
    CompatibilityAnchor,
    require_compatibility_anchor,
)
from project_atlas.schema import SchemaValidationError, validate_record

PACKAGE_ID = "AS-2.0-CHATGPT-CAPTURE-001"
_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
TRUTH_BOUNDARY = "CHATGPT CAPTURE ≠ LIVE API / ≠ AUTHORITY"
SCHEMA_KIND = "chatgpt-capture-receipt"


class ChatgptCaptureError(ValueError):
    """Fail-closed contract error."""


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
        raise ChatgptCaptureError(
            f"unwritable-capture-receipt:{type(exc).__name__}:{path}"
        ) from exc
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def build_chatgpt_capture_receipt(
    vault: Path,
    *,
    record_id: str,
    anchor: CompatibilityAnchor | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build a deterministic chatgpt-capture-receipt record."""
    _ = anchor or require_compatibility_anchor()
    rid = record_id.strip()
    if not _ID_RE.fullmatch(rid):
        raise ChatgptCaptureError("chatgpt-capture-id-invalid")

    if bool(kwargs.get("enable_live_api")):
        raise ChatgptCaptureError("chatgpt-live-api-forbidden")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "compat_snapshot_id": SNAPSHOT_ID,
        "receipt_id": rid,
        "live_api": False,
        "turn_count": int(kwargs.get("turn_count", 0)),
        "status": str(kwargs.get("status", "captured-fixture")),
        "authority": {
            "level": "derived",
            "note": "ChatGPT capture is fixture-safe; consumes OAI/PROV patterns",
        },
        "truth_boundary": TRUTH_BOUNDARY,
        "generated": {"by": "project-atlas"},
    }

    try:
        validate_record(payload, SCHEMA_KIND)
    except SchemaValidationError as exc:
        raise ChatgptCaptureError(f"chatgpt-capture-receipt-schema-invalid:{exc}") from exc
    out = vault / "generated" / "ops" / "chatgpt" / f"{rid}.json"
    _atomic_write_json(out, payload)
    return payload
