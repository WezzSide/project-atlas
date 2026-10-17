"""AS-INT-010 — Removed-package / deletion-state tombstone projections.

When an agent-event package/receipt unit is removed (retention eviction or
explicit operator deletion), record a deterministic tombstone so the unit does
not silently vanish from operational inventory projections.

Hard rules (INT10-FR-002..007):
- Own this helper module; thin hooks only into retention / callers.
- Never rewrite Layer B concept notes; never invent authority.
- Deterministic JSON under ``generated/ops/`` (``sort_keys=True``, no wall-clock).
- Do not redesign INT-009 retention caps / ``event_retention`` core.
- Do not dual-own ``apps/web``, invent PILOT, open REL-001, or ship Atlas 2.0
  production semantics.
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

GENERATOR_ID = "atlas-int-010"
INDEX_SCHEMA = "event-tombstone-index"
INDEX_RELATIVE = Path("generated") / "ops" / "event-tombstones.json"

Reason = Literal["retention", "explicit"]


class TombstoneError(ValueError):
    """Raised when tombstone projection cannot proceed safely."""


@dataclass(frozen=True)
class TombstoneUnit:
    """One removed package/receipt unit projected as deleted-state."""

    project_id: str
    event_id: str
    reason: Reason
    deleted_paths: tuple[str, ...]

    @property
    def unit_key(self) -> str:
        return f"{self.project_id}/{self.event_id}"

    def to_record(self) -> dict[str, Any]:
        return {
            "unit_key": self.unit_key,
            "project_id": self.project_id,
            "event_id": self.event_id,
            "reason": self.reason,
            "deleted_paths": sorted(self.deleted_paths),
            "state": "deleted",
        }


def _inside(vault: Path, path: Path) -> Path:
    resolved_vault = vault.expanduser().resolve()
    resolved = path.expanduser().resolve()
    if not resolved.is_relative_to(resolved_vault):
        raise TombstoneError(f"path escapes vault root: {path}")
    return resolved


def _posix_rel(vault: Path, path: Path) -> str:
    return path.resolve().relative_to(vault.resolve()).as_posix()


def _safe_component(value: str, *, label: str) -> str:
    if not value or "/" in value or "\\" in value or value in {".", ".."}:
        raise TombstoneError(f"unsafe {label}: {value!r}")
    if PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts:
        raise TombstoneError(f"unsafe {label}: {value!r}")
    if scan_text(value):
        raise TombstoneError(f"secret-content {label}")
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


def empty_index() -> dict[str, Any]:
    """Return a schema-valid empty tombstone index."""
    index = {
        "schema_version": 1,
        "schema": "atlas.event_tombstone.index.v1",
        "truth_plane": "operational",
        "authority_plane": "none",
        "note": "REMOVED PACKAGE / RECEIPT TOMBSTONE ≠ PROJECT AUTHORITY",
        "tombstones": [],
        "generated": {"by": GENERATOR_ID},
    }
    validate_record(index, INDEX_SCHEMA)
    return index


def load_index(vault: Path) -> dict[str, Any]:
    """Load the vault tombstone index; missing file → empty index.

    Malformed content fails closed.
    """
    vault = vault.expanduser().resolve()
    path = _inside(vault, vault / INDEX_RELATIVE)
    if not path.is_file():
        return empty_index()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TombstoneError(f"malformed tombstone index: {exc}") from exc
    if not isinstance(loaded, dict):
        raise TombstoneError("tombstone index must be a JSON object")
    loaded.setdefault("schema_version", 1)
    loaded.setdefault("schema", "atlas.event_tombstone.index.v1")
    loaded.setdefault("truth_plane", "operational")
    loaded.setdefault("authority_plane", "none")
    loaded.setdefault(
        "note", "REMOVED PACKAGE / RECEIPT TOMBSTONE ≠ PROJECT AUTHORITY"
    )
    loaded.setdefault("tombstones", [])
    loaded.setdefault("generated", {"by": GENERATOR_ID})
    try:
        validate_record(loaded, INDEX_SCHEMA)
    except Exception as exc:
        raise TombstoneError(f"malformed tombstone index: {exc}") from exc
    rows = loaded.get("tombstones")
    if isinstance(rows, list):
        cleaned: list[Any] = []
        for row in rows:
            if not isinstance(row, dict):
                raise TombstoneError("tombstone entry must be an object")
            fields = [
                str(row.get("unit_key") or ""),
                str(row.get("event_id") or ""),
                str(row.get("project_id") or ""),
            ]
            paths = row.get("deleted_paths")
            if isinstance(paths, list):
                fields.extend(str(item) for item in paths)
            # AS-SEC-SCAN-TOMBSTONE-JSON-ESC-001: drop decoded secret-shaped
            # identity rows so index rewrite cannot persist them.
            if any(scan_text(field) for field in fields if field):
                continue
            cleaned.append(row)
        loaded["tombstones"] = cleaned
    return loaded


def write_index(vault: Path, index: dict[str, Any]) -> Path:
    """Persist a schema-valid tombstone index under ``generated/ops/`` only."""
    vault = vault.expanduser().resolve()
    validate_record(index, INDEX_SCHEMA)
    target = _inside(vault, vault / INDEX_RELATIVE)
    rel = _posix_rel(vault, target)
    if not rel.startswith("generated/ops/"):
        raise TombstoneError(f"refusing non-ops tombstone path: {rel}")
    # Defence: never write under Layer B / authority surfaces.
    forbidden_prefixes = (
        "projects/",
        "01-portfolio/",
        "00-system/",
        "concepts/",
        "relationships/",
        "apps/",
    )
    for prefix in forbidden_prefixes:
        if rel == prefix.rstrip("/") or rel.startswith(prefix):
            raise TombstoneError(f"refusing Layer B / forbidden write: {rel}")
    payload = json.dumps(index, indent=2, sort_keys=True) + "\n"
    _write_atomic(target, payload.encode("utf-8"))
    return target


def _paths_for_unit(
    unit_key: str,
    deleted_paths: Sequence[str],
) -> tuple[str, ...]:
    """Select deleted paths belonging to ``project_id/event_id``."""
    project_id, _, event_id = unit_key.partition("/")
    if not project_id or not event_id:
        raise TombstoneError(f"invalid unit_key: {unit_key!r}")
    package_prefix = f"sources/agent-events/{project_id}/{event_id}"
    receipt_path = f"receipts/agent-events/{project_id}/{event_id}.yaml"
    matched = [
        path
        for path in deleted_paths
        if path in (package_prefix, receipt_path)
        or path.startswith(package_prefix + "/")
    ]
    return tuple(sorted(matched))


def _merge_units(
    existing: Sequence[Mapping[str, Any]],
    incoming: Sequence[TombstoneUnit],
) -> list[dict[str, Any]]:
    """Merge by unit_key; later reason/paths win deterministically."""
    by_key: dict[str, dict[str, Any]] = {}
    for raw in existing:
        if not isinstance(raw, Mapping):
            raise TombstoneError("tombstone entry must be an object")
        key = str(raw.get("unit_key", ""))
        if not key:
            raise TombstoneError("tombstone entry missing unit_key")
        by_key[key] = dict(raw)
    for unit in incoming:
        by_key[unit.unit_key] = unit.to_record()
    return [by_key[key] for key in sorted(by_key)]


def record_tombstones(
    vault: Path,
    units: Sequence[TombstoneUnit],
) -> dict[str, Any]:
    """Merge tombstone units into the vault projection and write atomically."""
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise TombstoneError(f"vault is not a directory: {vault}")
    if not units:
        return load_index(vault)

    normalized: list[TombstoneUnit] = []
    for unit in units:
        project_id = _safe_component(unit.project_id, label="project_id")
        event_id = _safe_component(unit.event_id, label="event_id")
        if unit.reason not in {"retention", "explicit"}:
            raise TombstoneError(f"unsupported tombstone reason: {unit.reason!r}")
        paths = tuple(sorted({str(p) for p in unit.deleted_paths if str(p)}))
        for path in paths:
            if "\\" in path or path.startswith("/") or ".." in PurePosixPath(path).parts:
                raise TombstoneError(f"unsafe deleted_path: {path!r}")
            allowed = path.startswith("sources/agent-events/") or path.startswith(
                "receipts/agent-events/"
            )
            if not allowed:
                raise TombstoneError(
                    f"refusing tombstone path outside retention roots: {path}"
                )
        normalized.append(
            TombstoneUnit(
                project_id=project_id,
                event_id=event_id,
                reason=unit.reason,
                deleted_paths=paths,
            )
        )

    current = load_index(vault)
    merged = _merge_units(current.get("tombstones", []), normalized)
    index = {
        "schema_version": 1,
        "schema": "atlas.event_tombstone.index.v1",
        "truth_plane": "operational",
        "authority_plane": "none",
        "note": "REMOVED PACKAGE / RECEIPT TOMBSTONE ≠ PROJECT AUTHORITY",
        "tombstones": merged,
        "generated": {"by": GENERATOR_ID},
    }
    validate_record(index, INDEX_SCHEMA)
    write_index(vault, index)
    return index


def record_retention_tombstones(
    vault: Path,
    *,
    removed_unit_keys: Sequence[str],
    deleted_paths: Sequence[str],
) -> dict[str, Any] | None:
    """Thin retention hook: project deleted units after INT-009 apply.

    No-op when nothing was removed. Does not alter retention caps or delete
    files — retention already performed the deletes.
    """
    if not removed_unit_keys:
        return None
    for path in deleted_paths:
        text = str(path)
        if "\\" in text or text.startswith("/") or ".." in PurePosixPath(text).parts:
            raise TombstoneError(f"unsafe deleted_path: {text!r}")
        allowed = text.startswith("sources/agent-events/") or text.startswith(
            "receipts/agent-events/"
        )
        if not allowed:
            raise TombstoneError(
                f"refusing tombstone path outside retention roots: {text}"
            )
    units: list[TombstoneUnit] = []
    for key in removed_unit_keys:
        project_id, sep, event_id = str(key).partition("/")
        if not sep or not project_id or not event_id:
            raise TombstoneError(f"invalid removed unit_key: {key!r}")
        units.append(
            TombstoneUnit(
                project_id=project_id,
                event_id=event_id,
                reason="retention",
                deleted_paths=_paths_for_unit(str(key), deleted_paths),
            )
        )
    return record_tombstones(vault, units)


def record_explicit_tombstone(
    vault: Path,
    *,
    project_id: str,
    event_id: str,
    deleted_paths: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Record an operator-driven deletion tombstone (explicit reason)."""
    project_id = _safe_component(project_id, label="project_id")
    event_id = _safe_component(event_id, label="event_id")
    paths = list(deleted_paths) if deleted_paths is not None else [
        f"sources/agent-events/{project_id}/{event_id}",
        f"receipts/agent-events/{project_id}/{event_id}.yaml",
    ]
    unit = TombstoneUnit(
        project_id=project_id,
        event_id=event_id,
        reason="explicit",
        deleted_paths=tuple(paths),
    )
    return record_tombstones(vault, [unit])


def list_tombstones(vault: Path) -> list[dict[str, Any]]:
    """Return sorted tombstone records from the vault projection."""
    index = load_index(vault)
    return list(index.get("tombstones", []))


def projection_inventory(
    vault: Path,
    *,
    live_unit_keys: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Merge live units with tombstones so removals do not silently vanish.

    Each entry carries ``state`` of ``present`` or ``deleted``. Live keys that
    also appear as tombstones are reported as ``present`` (restoration wins).
    """
    vault = vault.expanduser().resolve()
    live = {str(key) for key in (live_unit_keys or ())}
    if live_unit_keys is None:
        # Optional lazy inventory via retention helper — import locally to keep
        # tombstone module free of retention policy ownership.
        from project_atlas.event_retention import inventory_units

        live = {unit.unit_key for unit in inventory_units(vault)}

    entries: dict[str, dict[str, Any]] = {}
    for key in sorted(live):
        project_id, sep, event_id = key.partition("/")
        if not sep:
            raise TombstoneError(f"invalid live unit_key: {key!r}")
        entries[key] = {
            "unit_key": key,
            "project_id": project_id,
            "event_id": event_id,
            "state": "present",
        }
    for tomb in list_tombstones(vault):
        key = str(tomb["unit_key"])
        if key in entries:
            continue
        entries[key] = {
            "unit_key": key,
            "project_id": tomb["project_id"],
            "event_id": tomb["event_id"],
            "state": "deleted",
            "reason": tomb["reason"],
            "deleted_paths": list(tomb["deleted_paths"]),
        }
    return [entries[key] for key in sorted(entries)]
