"""AS-2.1-OAI-IMPORT-REAL-001 - real OpenAI chat-export import path.

Reads an operator-supplied export file (markdown/text), scans secrets,
quarantines via PROV helper, and writes an import receipt. Never claims
live OpenAI API access; this is REAL_OPENAI_EXPORT_IMPORT (file export).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from project_atlas.authz import OperatorProfile, default_operator
from project_atlas.compat_anchor import SNAPSHOT_ID, require_compatibility_anchor
from project_atlas.openai_importer_fixtures import parse_chat_export
from project_atlas.provider_adapters import quarantine_provider_output
from project_atlas.secrets import scan_text

PACKAGE_ID = "AS-2.1-OAI-IMPORT-REAL-001"
TRUTH_BOUNDARY = (
    "REAL_OPENAI_EXPORT_IMPORT != LIVE OPENAI API / != AUTHORITY PROMOTE"
)
_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
MAX_EXPORT_BYTES = 2_000_000


class OpenAIRealImportError(ValueError):
    """Fail-closed real export import error."""


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp.replace(path)


def import_openai_export(
    vault: Path,
    export_path: Path,
    *,
    import_id: str,
    operator: OperatorProfile | None = None,
) -> dict[str, Any]:
    """Import a real on-disk OpenAI chat export into quarantined receipts."""
    require_compatibility_anchor()
    op = operator or default_operator()
    op.require("oai.import")
    iid = import_id.strip()
    if not _ID_RE.fullmatch(iid):
        raise OpenAIRealImportError("oai-import-id-invalid")
    src = export_path.resolve()
    if not src.is_file():
        raise OpenAIRealImportError("oai-export-missing")
    # Bytes, not ``read_text``: text mode applies universal-newline
    # translation, so a SHA-256 of the decoded string cannot verify
    # ``source_path`` (#726). Hash and ``source_bytes`` must describe
    # the same on-disk artifact. Decode only for parse/scan.
    try:
        raw = src.read_bytes()
    except OSError as exc:
        raise OpenAIRealImportError("oai-export-unreadable") from exc
    size = len(raw)
    if size <= 0 or size > MAX_EXPORT_BYTES:
        raise OpenAIRealImportError("oai-export-size-out-of-range")
    lowered = src.name.lower()
    if any(x in lowered for x in (".env", "credential", "secret", "apikey", "api_key")):
        raise OpenAIRealImportError("oai-export-filename-forbidden")
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise OpenAIRealImportError("oai-export-unreadable") from exc
    findings = scan_text(text)
    if findings:
        raise OpenAIRealImportError("oai-export-secret-findings")
    turns = parse_chat_export(text)
    if not turns:
        raise OpenAIRealImportError("oai-export-no-turns")
    digest = hashlib.sha256(raw).hexdigest()
    quarantine = quarantine_provider_output(
        vault,
        envelope_id=f"oai-real-{iid}",
        adapter_id="openai-export-file",
        payload_text=text,
        payload_kind="text",
        adapters_enabled=True,
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "compat_snapshot_id": SNAPSHOT_ID,
        "import_id": iid,
        "source_path": str(src),
        "source_sha256": digest,
        "source_bytes": size,
        "max_export_bytes": MAX_EXPORT_BYTES,
        "turn_count": len(turns),
        "turns": [t.as_dict() for t in turns],
        "quarantine": {
            "envelope_id": quarantine.get("envelope_id"),
            "status": quarantine.get("status"),
        },
        "real_openai_export_import": True,
        "live_openai_api": False,
        "operator_id": op.operator_id,
        "truth_boundary": TRUTH_BOUNDARY,
        "authority": {
            "level": "derived",
            "note": "export import quarantined; not Layer B promote",
        },
        "generated": {"by": "project-atlas"},
    }
    out = vault / "generated" / "ops" / "openai-import" / f"{iid}-real-import.json"
    _atomic_write_json(out, payload)
    return payload
