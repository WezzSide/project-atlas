"""AT3-014 — Universal event ledger.

Append-only derived JSONL under generated/ops/atlas3/ledger/.
Does not write generated/ops/events/ (ops_events dual-writer forbidden).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from project_atlas.atlas3.contracts import (
    OPS_RELATIVE,
    Atlas3Error,
    require_project,
    require_vault,
    write_json_atomic,
)
from project_atlas.atlas3.events import normalize_engineering_event, verify_engineering_event
from project_atlas.secrets import scan_text

PACKAGE_ID: Final[str] = "AT3-014"
LEDGER_RELATIVE: Final[Path] = OPS_RELATIVE / "ledger"


def _ledger_path(vault: Path, project_id: str) -> Path:
    return vault / LEDGER_RELATIVE / f"{project_id}.jsonl"


def _reject_secret_record(record: dict[str, Any], *, line_number: int | None = None) -> None:
    """Fail closed if a decoded ledger row contains secret-shaped text.

    AS-SEC-SCAN-ATLAS3-LEDGER-PAYLOAD-JSON-ESC-001: json.loads can decode
    ``\\u`` payload fields that scan_text misses on raw bytes. The ledger
    is evidence substrate — do not persist or silently accept those rows.
    """
    blob = json.dumps(record, sort_keys=True, separators=(",", ":"))
    findings = scan_text(blob)
    if not findings:
        return
    codes = sorted({item.pattern for item in findings})
    where = f" at line {line_number}" if line_number is not None else ""
    raise Atlas3Error(
        "LEDGER_SECRET",
        f"NFR-004 secret patterns in ledger record{where}: {','.join(codes)}",
    )


def append_event(
    vault: Path,
    project_id: str,
    event: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Append a normalized event. Replay of the same event_id is idempotent."""
    root = require_vault(vault)
    pid = require_project(root, project_id)
    record = event or normalize_engineering_event(project_id=pid, **kwargs)
    if record.get("project_id") != pid:
        raise Atlas3Error("PROJECT_MISMATCH", "event project_id does not match ledger project")
    _reject_secret_record(record)
    path = _ledger_path(root, pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = list_events(root, pid)
    for prior in existing:
        if prior.get("event_id") == record.get("event_id"):
            return {
                "status": "ok",
                "package": PACKAGE_ID,
                "idempotency": "replay",
                "event_id": record["event_id"],
                "path": str(LEDGER_RELATIVE / f"{pid}.jsonl"),
            }
    line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)
    return {
        "status": "ok",
        "package": PACKAGE_ID,
        "idempotency": "appended",
        "event_id": record["event_id"],
        "path": str(LEDGER_RELATIVE / f"{pid}.jsonl"),
        "kind": record.get("kind"),
        "event_type": record.get("event_type"),
    }


def list_events(
    vault: Path,
    project_id: str,
    *,
    kind: str | None = None,
    event_type: str | None = None,
) -> list[dict[str, Any]]:
    return query_events(vault, project_id=project_id, kind=kind, event_type=event_type)


def query_events(
    vault: Path,
    *,
    project_id: str,
    event_type: str | None = None,
    kind: str | None = None,
    observed_from: str | None = None,
    observed_to: str | None = None,
) -> list[dict[str, Any]]:
    """Project-scoped temporal query. Fail-closed if the JSONL is malformed."""
    root = require_vault(vault)
    pid = require_project(root, project_id)
    path = _ledger_path(root, pid)
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            item = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise Atlas3Error("LEDGER_CORRUPT", f"malformed ledger line: {exc}") from exc
        _reject_secret_record(item, line_number=line_number)
        try:
            verify_engineering_event(item, expected_project_id=pid)
        except Atlas3Error as exc:
            if exc.code == "PROJECT_MISMATCH":
                raise Atlas3Error(
                    "PROJECT_MISMATCH",
                    f"foreign project row at line {line_number}",
                ) from exc
            raise
        event_id = str(item["event_id"])
        content_hash = str(item["content_hash"])
        prior = seen.get(event_id)
        if prior is not None:
            if prior == content_hash:
                continue
            raise Atlas3Error(
                "EVENT_ID_COLLISION",
                f"event_id {event_id!r} reused with altered payload at line {line_number}",
            )
        seen[event_id] = content_hash
        if kind is not None and item.get("kind") != kind:
            continue
        if event_type is not None and item.get("event_type") != event_type:
            continue
        observed = str(item.get("observed_at") or "")
        if observed_from and observed < observed_from:
            continue
        if observed_to and observed > observed_to:
            continue
        rows.append(item)
    return rows


def ledger_status(vault: Path, project_id: str) -> dict[str, Any]:
    rows = list_events(vault, project_id)
    kinds: dict[str, int] = {}
    types: dict[str, int] = {}
    for row in rows:
        kind_key = str(row.get("kind") or "unknown")
        type_key = str(row.get("event_type") or "unknown")
        kinds[kind_key] = kinds.get(kind_key, 0) + 1
        types[type_key] = types.get(type_key, 0) + 1
    payload = {
        "package": PACKAGE_ID,
        "project_id": project_id,
        "event_count": len(rows),
        "kinds": dict(sorted(kinds.items())),
        "event_types": dict(sorted(types.items())),
        "store": str(LEDGER_RELATIVE / f"{project_id}.jsonl"),
        "dual_writes_ops_events": False,
        "truth_core": False,
    }
    write_json_atomic(
        require_vault(vault) / OPS_RELATIVE / "ledger" / f"{project_id}.status.json",
        payload,
    )
    return payload
