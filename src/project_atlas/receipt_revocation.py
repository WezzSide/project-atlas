"""AS-INT-011 — Receipt revocation / invalidation semantics (operational).

Marks agent-event receipts as revoked or invalidated without deleting files
and without inventing project authority. Distinct from AS-INT-010 tombstones
(which project *deleted* package/receipt units).

Hard rules (INT11-FR-001..007):
- Own this helper module; thin hooks only into CLI / callers.
- Never rewrite Layer B concept notes; never invent authority.
- Deterministic JSON under ``generated/ops/`` (``sort_keys=True``, no wall-clock).
- Do not rewrite ``event_tombstones`` core; do not dual-own ``apps/web``, invent
  PILOT, open REL-001, or ship Atlas 2.0 production semantics.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from project_atlas.schema import validate_record
from project_atlas.secrets import scan_text

GENERATOR_ID = "atlas-int-011"
INDEX_SCHEMA = "receipt-revocation-index"
INDEX_RELATIVE = Path("generated") / "ops" / "receipt-revocations.json"
RECEIPTS_ROOT = Path("receipts") / "agent-events"

Reason = Literal["operator", "skill_policy", "integrity"]
Status = Literal["revoked", "invalidated"]

_REASON_DEFAULT_STATUS: dict[Reason, Status] = {
    "operator": "revoked",
    "skill_policy": "revoked",
    "integrity": "invalidated",
}


class RevocationError(ValueError):
    """Raised when receipt revocation cannot proceed safely."""


@dataclass(frozen=True)
class RevocationUnit:
    """One receipt marked revoked or invalidated (file may still exist)."""

    project_id: str
    event_id: str
    reason: Reason
    status: Status
    receipt_path: str
    detail: str | None = None

    @property
    def unit_key(self) -> str:
        return f"{self.project_id}/{self.event_id}"

    def to_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "unit_key": self.unit_key,
            "project_id": self.project_id,
            "event_id": self.event_id,
            "receipt_path": self.receipt_path,
            "reason": self.reason,
            "status": self.status,
        }
        if self.detail:
            record["detail"] = self.detail
        return record


def _inside(vault: Path, path: Path) -> Path:
    resolved_vault = vault.expanduser().resolve()
    resolved = path.expanduser().resolve()
    if not resolved.is_relative_to(resolved_vault):
        raise RevocationError(f"path escapes vault root: {path}")
    return resolved


def _posix_rel(vault: Path, path: Path) -> str:
    return path.resolve().relative_to(vault.resolve()).as_posix()


def _safe_component(value: str, *, label: str) -> str:
    if not value or "/" in value or "\\" in value or value in {".", ".."}:
        raise RevocationError(f"unsafe {label}: {value!r}")
    if PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts:
        raise RevocationError(f"unsafe {label}: {value!r}")
    if scan_text(value):
        raise RevocationError(f"secret-content {label}")
    return value


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _default_receipt_path(project_id: str, event_id: str) -> str:
    return f"receipts/agent-events/{project_id}/{event_id}.yaml"


def _validate_receipt_path(path: str) -> str:
    if "\\" in path or path.startswith("/") or ".." in PurePosixPath(path).parts:
        raise RevocationError(f"unsafe receipt_path: {path!r}")
    if not path.startswith("receipts/agent-events/"):
        raise RevocationError(
            f"refusing receipt_path outside agent-event receipts: {path}"
        )
    if not path.endswith(".yaml"):
        raise RevocationError(f"receipt_path must end with .yaml: {path}")
    return path


def empty_index() -> dict[str, Any]:
    """Return a schema-valid empty revocation index."""
    index = {
        "schema_version": 1,
        "schema": "atlas.receipt_revocation.index.v1",
        "truth_plane": "operational",
        "authority_plane": "none",
        "note": "RECEIPT REVOCATION / INVALIDATION ≠ PROJECT AUTHORITY",
        "revocations": [],
        "generated": {"by": GENERATOR_ID},
    }
    validate_record(index, INDEX_SCHEMA)
    return index


def load_index(vault: Path) -> dict[str, Any]:
    """Load the vault revocation index; missing file → empty index.

    Malformed content fails closed.
    """
    vault = vault.expanduser().resolve()
    path = _inside(vault, vault / INDEX_RELATIVE)
    if not path.is_file():
        return empty_index()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RevocationError(f"malformed revocation index: {exc}") from exc
    if not isinstance(loaded, dict):
        raise RevocationError("revocation index must be a JSON object")
    loaded.setdefault("schema_version", 1)
    loaded.setdefault("schema", "atlas.receipt_revocation.index.v1")
    loaded.setdefault("truth_plane", "operational")
    loaded.setdefault("authority_plane", "none")
    loaded.setdefault(
        "note", "RECEIPT REVOCATION / INVALIDATION ≠ PROJECT AUTHORITY"
    )
    loaded.setdefault("revocations", [])
    loaded.setdefault("generated", {"by": GENERATOR_ID})
    try:
        validate_record(loaded, INDEX_SCHEMA)
    except Exception as exc:
        raise RevocationError(f"malformed revocation index: {exc}") from exc
    rows = loaded.get("revocations")
    if isinstance(rows, list):
        cleaned: list[Any] = []
        for row in rows:
            if not isinstance(row, dict):
                raise RevocationError("revocation entry must be an object")
            fields = [
                str(row.get("unit_key") or ""),
                str(row.get("event_id") or ""),
                str(row.get("project_id") or ""),
                str(row.get("receipt_path") or ""),
            ]
            # AS-SEC-SCAN-REVOCATION-JSON-ESC-001: drop decoded secret-shaped
            # identity rows so index rewrite cannot persist them.
            if any(scan_text(field) for field in fields if field):
                continue
            cleaned.append(row)
        loaded["revocations"] = cleaned
    return loaded


def write_index(vault: Path, index: dict[str, Any]) -> Path:
    """Persist a schema-valid revocation index under ``generated/ops/`` only."""
    vault = vault.expanduser().resolve()
    validate_record(index, INDEX_SCHEMA)
    target = _inside(vault, vault / INDEX_RELATIVE)
    rel = _posix_rel(vault, target)
    if not rel.startswith("generated/ops/"):
        raise RevocationError(f"refusing non-ops revocation path: {rel}")
    forbidden_prefixes = (
        "projects/",
        "01-portfolio/",
        "00-system/",
        "concepts/",
        "relationships/",
        "apps/",
        "sources/",
        "receipts/",
    )
    for prefix in forbidden_prefixes:
        if rel == prefix.rstrip("/") or rel.startswith(prefix):
            raise RevocationError(f"refusing Layer B / forbidden write: {rel}")
    payload = json.dumps(index, indent=2, sort_keys=True) + "\n"
    _write_atomic(target, payload.encode("utf-8"))
    return target


def _merge_units(
    existing: Sequence[Mapping[str, Any]],
    incoming: Sequence[RevocationUnit],
) -> list[dict[str, Any]]:
    """Merge by unit_key; later reason/status/detail win deterministically."""
    by_key: dict[str, dict[str, Any]] = {}
    for raw in existing:
        if not isinstance(raw, Mapping):
            raise RevocationError("revocation entry must be an object")
        key = str(raw.get("unit_key", ""))
        if not key:
            raise RevocationError("revocation entry missing unit_key")
        by_key[key] = dict(raw)
    for unit in incoming:
        by_key[unit.unit_key] = unit.to_record()
    return [by_key[key] for key in sorted(by_key)]


def record_revocations(
    vault: Path,
    units: Sequence[RevocationUnit],
) -> dict[str, Any]:
    """Merge revocation units into the vault projection and write atomically.

    Does **not** delete receipt files on disk.
    """
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise RevocationError(f"vault is not a directory: {vault}")
    if not units:
        return load_index(vault)

    normalized: list[RevocationUnit] = []
    for unit in units:
        project_id = _safe_component(unit.project_id, label="project_id")
        event_id = _safe_component(unit.event_id, label="event_id")
        if unit.reason not in {"operator", "skill_policy", "integrity"}:
            raise RevocationError(f"unsupported revocation reason: {unit.reason!r}")
        if unit.status not in {"revoked", "invalidated"}:
            raise RevocationError(f"unsupported revocation status: {unit.status!r}")
        receipt_path = _validate_receipt_path(unit.receipt_path)
        detail = unit.detail.strip() if unit.detail else None
        if detail is not None and not detail:
            detail = None
        normalized.append(
            RevocationUnit(
                project_id=project_id,
                event_id=event_id,
                reason=unit.reason,
                status=unit.status,
                receipt_path=receipt_path,
                detail=detail,
            )
        )

    current = load_index(vault)
    merged = _merge_units(current.get("revocations", []), normalized)
    index = {
        "schema_version": 1,
        "schema": "atlas.receipt_revocation.index.v1",
        "truth_plane": "operational",
        "authority_plane": "none",
        "note": "RECEIPT REVOCATION / INVALIDATION ≠ PROJECT AUTHORITY",
        "revocations": merged,
        "generated": {"by": GENERATOR_ID},
    }
    validate_record(index, INDEX_SCHEMA)
    write_index(vault, index)
    return index


def revoke_receipt(
    vault: Path,
    *,
    project_id: str,
    event_id: str,
    reason: Reason = "operator",
    status: Status | None = None,
    detail: str | None = None,
    receipt_path: str | None = None,
) -> dict[str, Any]:
    """Record a single receipt revocation / invalidation (INT11-FR-001).

    Default status: ``revoked`` for operator/skill_policy, ``invalidated`` for
    integrity. Never deletes the receipt file.
    """
    project_id = _safe_component(project_id, label="project_id")
    event_id = _safe_component(event_id, label="event_id")
    if reason not in _REASON_DEFAULT_STATUS:
        raise RevocationError(f"unsupported revocation reason: {reason!r}")
    resolved_status = status if status is not None else _REASON_DEFAULT_STATUS[reason]
    path = receipt_path if receipt_path is not None else _default_receipt_path(
        project_id, event_id
    )
    unit = RevocationUnit(
        project_id=project_id,
        event_id=event_id,
        reason=reason,
        status=resolved_status,
        receipt_path=path,
        detail=detail,
    )
    return record_revocations(vault, [unit])


def list_revocations(vault: Path) -> list[dict[str, Any]]:
    """Return sorted revocation records from the vault projection."""
    index = load_index(vault)
    return list(index.get("revocations", []))


def get_revocation(
    vault: Path,
    *,
    project_id: str,
    event_id: str,
) -> dict[str, Any] | None:
    """Return the revocation record for a unit, or ``None`` if active."""
    project_id = _safe_component(project_id, label="project_id")
    event_id = _safe_component(event_id, label="event_id")
    key = f"{project_id}/{event_id}"
    for entry in list_revocations(vault):
        if entry.get("unit_key") == key:
            return dict(entry)
    return None


def is_receipt_revoked(
    vault: Path,
    *,
    project_id: str,
    event_id: str,
) -> bool:
    """True when the unit appears in the revocation index (any status)."""
    return get_revocation(vault, project_id=project_id, event_id=event_id) is not None


def receipt_trust_disposition(
    vault: Path,
    *,
    project_id: str,
    event_id: str,
) -> Literal["active", "revoked", "invalidated"]:
    """Thin consumer helper: active | revoked | invalidated (never authority)."""
    entry = get_revocation(vault, project_id=project_id, event_id=event_id)
    if entry is None:
        return "active"
    status = entry.get("status")
    if status == "invalidated":
        return "invalidated"
    return "revoked"


def assert_receipt_active(
    vault: Path,
    *,
    project_id: str,
    event_id: str,
) -> None:
    """Fail closed when a receipt is revoked or invalidated (thin hook)."""
    entry = get_revocation(vault, project_id=project_id, event_id=event_id)
    if entry is None:
        return
    raise RevocationError(
        f"receipt {project_id}/{event_id} is {entry.get('status')} "
        f"(reason={entry.get('reason')})"
    )


def inventory_with_revocations(
    vault: Path,
    *,
    live_unit_keys: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Merge live receipt units with revocation disposition.

    Live keys without a revocation entry are ``active``. Revoked units remain
    visible even when the receipt file was later deleted elsewhere.
    """
    vault = vault.expanduser().resolve()
    live: set[str]
    if live_unit_keys is None:
        live = set()
        receipts_root = vault / RECEIPTS_ROOT
        if receipts_root.is_dir():
            for project_dir in sorted(receipts_root.iterdir(), key=lambda p: p.name):
                if not project_dir.is_dir() or project_dir.is_symlink():
                    continue
                project_id = project_dir.name
                for receipt in sorted(project_dir.iterdir(), key=lambda p: p.name):
                    if not receipt.is_file() or receipt.is_symlink():
                        continue
                    if not receipt.name.endswith(".yaml"):
                        continue
                    event_id = receipt.name[: -len(".yaml")]
                    live.add(f"{project_id}/{event_id}")
    else:
        live = {str(key) for key in live_unit_keys}

    entries: dict[str, dict[str, Any]] = {}
    for key in sorted(live):
        project_id, sep, event_id = key.partition("/")
        if not sep:
            raise RevocationError(f"invalid live unit_key: {key!r}")
        disposition = receipt_trust_disposition(
            vault, project_id=project_id, event_id=event_id
        )
        row: dict[str, Any] = {
            "unit_key": key,
            "project_id": project_id,
            "event_id": event_id,
            "disposition": disposition,
        }
        rev = get_revocation(vault, project_id=project_id, event_id=event_id)
        if rev is not None:
            row["reason"] = rev["reason"]
            row["status"] = rev["status"]
            row["receipt_path"] = rev["receipt_path"]
        else:
            row["receipt_path"] = _default_receipt_path(project_id, event_id)
        entries[key] = row

    for rev in list_revocations(vault):
        key = str(rev["unit_key"])
        if key in entries:
            continue
        entries[key] = {
            "unit_key": key,
            "project_id": rev["project_id"],
            "event_id": rev["event_id"],
            "disposition": rev["status"],
            "reason": rev["reason"],
            "status": rev["status"],
            "receipt_path": rev["receipt_path"],
        }
    return [entries[key] for key in sorted(entries)]
