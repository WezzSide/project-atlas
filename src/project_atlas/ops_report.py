"""AS-OBS-003 Ops-report projection (tip-safe band).

Regenerable JSON/Markdown ops-report from the AS-OBS-001 health snapshot.
Optionally consumes AS-OBS-002 event stream when present (read-only; never
fabricates events). Writes only ``generated/ops/ops-report.*`` (+ optional
archive under ``generated/ops/archive/``).

Hard rules:
- truth_plane: operational / authority_plane: none
- HEALTH ≠ TRUTH / OPS REPORT ≠ PROJECT AUTHORITY
- DO NOT rewrite ops_health / ops_events writers
- DO NOT dual-own INCR compile-cache, GRAPH, XPROJ, claims, SURF UI
- Monitoring stacks FORBIDDEN
- AS-REL-001 MUST NOT OPEN
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from project_atlas.schema import validate_record
from project_atlas.secrets import scan_text

SCHEMA_ID = "atlas.ops.report.v1"
GENERATOR_ID = "atlas-obs-003"
SNAPSHOT_RELATIVE = Path("generated") / "ops" / "health-snapshot.json"
REPORT_JSON_RELATIVE = Path("generated") / "ops" / "ops-report.json"
REPORT_MD_RELATIVE = Path("generated") / "ops" / "ops-report.md"
ARCHIVE_DIR = Path("generated") / "ops" / "archive"
EVENTS_STREAM_RELATIVE = Path("generated") / "ops" / "events" / "stream.jsonl"

DEFAULT_EVENT_LIMIT = 20
DEFAULT_MAX_ARCHIVE = 50

HealthState = Literal["healthy", "degraded", "unhealthy", "unknown"]
SnapshotStatus = Literal["present", "missing", "invalid"]

NOTE = "OPERATIONAL METRIC ≠ PROJECT AUTHORITY"


class OpsReportError(ValueError):
    """Raised when an ops-report cannot be projected safely."""


def _inside(vault: Path, path: Path) -> Path:
    resolved_vault = vault.expanduser().resolve()
    resolved = path.expanduser().resolve()
    if not resolved.is_relative_to(resolved_vault):
        raise OpsReportError(f"path escapes vault root: {path}")
    return resolved


def _assert_ops_report_path(vault: Path, path: Path) -> Path:
    target = _inside(vault, path)
    posix = target.as_posix().replace("\\", "/")
    # Owned write surfaces only: ops-report.* and optional archive copies.
    is_report = posix.endswith("/generated/ops/ops-report.json") or posix.endswith(
        "/generated/ops/ops-report.md"
    )
    is_archive_root = posix.endswith("/generated/ops/archive")
    is_archive_file = "/generated/ops/archive/ops-report-" in posix and (
        posix.endswith(".json") or posix.endswith(".md")
    )
    if not (is_report or is_archive_root or is_archive_file):
        raise OpsReportError(f"refusing non-ops-report write path: {target}")
    # Defence in depth: never land under events/, compile-cache/, claims, etc.
    forbidden = (
        "/events/",
        "compile-cache",
        "/claims/",
        "authoritative-state",
        "current-state",
        "/xproj",
        "/graph/",
    )
    if any(token in posix for token in forbidden):
        raise OpsReportError(f"refusing forbidden write surface: {target}")
    return target


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_name.write_bytes(content)
        os.replace(tmp_name, path)
    finally:
        if tmp_name.exists():
            tmp_name.unlink(missing_ok=True)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OpsReportError(f"unreadable JSON at {path}: {exc}") from exc


def load_health_snapshot(vault: Path) -> tuple[dict[str, Any] | None, SnapshotStatus]:
    """Consume OBS-001 snapshot (read-only). Never invents a healthy snapshot."""
    vault = vault.expanduser().resolve()
    path = _inside(vault, vault / SNAPSHOT_RELATIVE)
    if not path.is_file():
        return None, "missing"
    try:
        raw = _read_json(path)
    except OpsReportError:
        return None, "invalid"
    if not isinstance(raw, dict):
        return None, "invalid"
    try:
        validate_record(raw, "ops-health-snapshot")
    except Exception:
        return None, "invalid"
    return raw, "present"


def _read_events_optional(
    vault: Path, *, limit: int = DEFAULT_EVENT_LIMIT
) -> tuple[bool, list[dict[str, Any]]]:
    """Read-only optional consume of OBS-002 stream. Missing → empty (no fabricate)."""
    vault = vault.expanduser().resolve()
    path = _inside(vault, vault / EVENTS_STREAM_RELATIVE)
    if not path.is_file():
        return False, []
    items: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False, []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            # Fail closed on corrupt stream: treat as absent (no fabricate).
            return False, []
        if not isinstance(raw, dict):
            return False, []
        items.append(raw)
    # Newest-last in stream; surface last-N newest first for report panels.
    newest = list(reversed(items[-limit:])) if limit > 0 else []
    summaries: list[dict[str, Any]] = []
    for event in newest:
        summaries.append(
            {
                "event_id": str(event.get("event_id") or "unknown"),
                "sequence": event.get("sequence"),
                "severity": event.get("severity"),
                "event_uid": event.get("event_uid"),
                "evidence_refs": sorted(
                    ref
                    for ref in (event.get("evidence_refs") or [])
                    if isinstance(ref, str)
                ),
            }
        )
    return True, summaries


def _unknown_report(*, estate_id: str, snapshot_status: SnapshotStatus) -> dict[str, Any]:
    return {
        "schema": SCHEMA_ID,
        "truth_plane": "operational",
        "authority_plane": "none",
        "note": NOTE,
        "estate_id": estate_id,
        "source_snapshot": SNAPSHOT_RELATIVE.as_posix(),
        "snapshot_status": snapshot_status,
        "rollup": {"estate": "unknown"},
        "scopes": [],
        "signals": [],
        "events": {"present": False, "count": 0, "items": []},
        "generated": {"by": GENERATOR_ID},
    }


def build_ops_report(
    vault: Path,
    *,
    include_events: bool = True,
    event_limit: int = DEFAULT_EVENT_LIMIT,
) -> dict[str, Any]:
    """Project a schema-bound ops-report from the OBS-001 snapshot (+ optional events).

    Missing/invalid snapshot → ``unknown`` report (never invents ``healthy``).
    """
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise OpsReportError(f"vault is not a directory: {vault}")
    if event_limit < 0:
        raise OpsReportError("event_limit must be >= 0")

    snapshot, status = load_health_snapshot(vault)
    if snapshot is None:
        estate_id = "unknown"
        identity_path = vault / ".atlas" / "vault.json"
        if identity_path.is_file():
            try:
                identity = _read_json(identity_path)
            except OpsReportError:
                identity = None
            if isinstance(identity, dict):
                for key in ("vault_uuid", "vault_id"):
                    value = identity.get(key)
                    if isinstance(value, str) and value.strip():
                        token = value.strip()
                        estate_id = "unknown" if scan_text(token) else token
                        break
        unknown = _unknown_report(estate_id=estate_id, snapshot_status=status)
        if include_events:
            present, items = _read_events_optional(vault, limit=event_limit)
            unknown["events"] = {
                "present": present,
                "count": len(items),
                "items": items,
            }
        validate_record(unknown, "ops-report")
        return unknown

    signals = snapshot.get("signals") or []
    if not isinstance(signals, list):
        signals = []
    # Stable signal ordering by (signal_id, scope_id).
    ordered_signals = sorted(
        [s for s in signals if isinstance(s, dict)],
        key=lambda item: (str(item.get("signal_id") or ""), str(item.get("scope_id") or "")),
    )
    projected_signals: list[dict[str, Any]] = []
    for signal in ordered_signals:
        projected_signals.append(
            {
                "signal_id": signal.get("signal_id"),
                "scope": signal.get("scope"),
                "scope_id": (
                    "unknown"
                    if scan_text(str(signal.get("scope_id") or ""))
                    else signal.get("scope_id")
                ),
                "status": signal.get("status"),
                "severity": signal.get("severity"),
                "observed_value": signal.get("observed_value"),
                "evidence_refs": sorted(
                    ref
                    for ref in (signal.get("evidence_refs") or [])
                    if isinstance(ref, str)
                ),
            }
        )

    scopes = snapshot.get("scopes") or []
    ordered_scopes = sorted(
        [s for s in scopes if isinstance(s, dict)],
        key=lambda item: (str(item.get("scope") or ""), str(item.get("scope_id") or "")),
    )

    raw_rollup = snapshot.get("rollup")
    rollup: dict[str, Any] = raw_rollup if isinstance(raw_rollup, dict) else {}
    estate = rollup.get("estate")
    if estate not in {"healthy", "degraded", "unhealthy", "unknown"}:
        estate = "unknown"

    events_panel: dict[str, Any] = {"present": False, "count": 0, "items": []}
    if include_events:
        present, items = _read_events_optional(vault, limit=event_limit)
        events_panel = {"present": present, "count": len(items), "items": items}

    report: dict[str, Any] = {
        "schema": SCHEMA_ID,
        "truth_plane": "operational",
        "authority_plane": "none",
        "note": NOTE,
        "estate_id": (
            "unknown"
            if scan_text(str(snapshot.get("estate_id") or ""))
            else str(snapshot.get("estate_id") or "unknown")
        ),
        "source_snapshot": SNAPSHOT_RELATIVE.as_posix(),
        "snapshot_status": "present",
        "rollup": {"estate": estate},
        "scopes": [
            {
                "scope": scope.get("scope"),
                "scope_id": (
                    "unknown"
                    if scan_text(str(scope.get("scope_id") or ""))
                    else scope.get("scope_id")
                ),
                "health": scope.get("health"),
            }
            for scope in ordered_scopes
        ],
        "signals": projected_signals,
        "events": events_panel,
        "generated": {"by": GENERATOR_ID},
    }
    validate_record(report, "ops-report")
    return report


def report_to_json(report: dict[str, Any]) -> str:
    """Deterministic JSON serialization (sort_keys, stable trailing newline)."""
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def report_to_markdown(report: dict[str, Any]) -> str:
    """Deterministic Markdown projection (human-readable; non-canonical)."""
    lines: list[str] = [
        "# Atlas Ops Report",
        "",
        f"- truth_plane: `{report['truth_plane']}`",
        f"- authority_plane: `{report['authority_plane']}`",
        f"- note: {report['note']}",
        f"- estate_id: `{report['estate_id']}`",
        f"- snapshot_status: `{report['snapshot_status']}`",
        f"- source_snapshot: `{report['source_snapshot']}`",
        f"- estate rollup: **{report['rollup']['estate']}**",
        "",
        "> HEALTH ≠ TRUTH · OPS REPORT ≠ PROJECT AUTHORITY",
        "",
        "## Signals",
        "",
    ]
    signals = report.get("signals") or []
    if not signals:
        lines.append("_No signals (snapshot missing or empty)._")
        lines.append("")
    else:
        lines.append("| signal_id | scope | scope_id | status | severity |")
        lines.append("|---|---|---|---|---|")
        for signal in signals:
            sev = signal.get("severity")
            sev_s = "" if sev is None else str(sev)
            lines.append(
                f"| {signal.get('signal_id')} | {signal.get('scope')} | "
                f"{signal.get('scope_id')} | {signal.get('status')} | {sev_s} |"
            )
        lines.append("")

    lines.extend(["## Scopes", ""])
    scopes = report.get("scopes") or []
    if not scopes:
        lines.append("_No scopes._")
        lines.append("")
    else:
        lines.append("| scope | scope_id | health |")
        lines.append("|---|---|---|")
        for scope in scopes:
            lines.append(
                f"| {scope.get('scope')} | {scope.get('scope_id')} | {scope.get('health')} |"
            )
        lines.append("")

    events = report.get("events") or {}
    lines.extend(
        [
            "## Events (optional OBS-002 consume)",
            "",
            f"- present: `{bool(events.get('present'))}`",
            f"- count (panel): `{int(events.get('count') or 0)}`",
            "",
        ]
    )
    items = events.get("items") or []
    if items:
        lines.append("| sequence | event_id | severity | event_uid |")
        lines.append("|---|---|---|---|")
        for item in items:
            lines.append(
                f"| {item.get('sequence')} | {item.get('event_id')} | "
                f"{item.get('severity')} | {item.get('event_uid')} |"
            )
        lines.append("")
    else:
        lines.append("_No events panel (stream absent or empty)._")
        lines.append("")

    lines.extend(
        [
            "---",
            f"generated.by: `{report['generated']['by']}`",
            "",
        ]
    )
    return "\n".join(lines)


def _archive_sequence(archive_dir: Path) -> int:
    """Next deterministic archive sequence from existing ops-report-*.json names."""
    if not archive_dir.is_dir():
        return 1
    highest = 0
    for path in archive_dir.glob("ops-report-*.json"):
        stem = path.stem  # ops-report-NNNN
        suffix = stem.rsplit("-", 1)[-1]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return highest + 1


def _apply_archive_retention(archive_dir: Path, *, max_archive: int) -> None:
    if max_archive < 1:
        raise OpsReportError("max_archive must be >= 1")
    files = sorted(archive_dir.glob("ops-report-*.json"), key=lambda p: p.name)
    md_files = {p.with_suffix(".md") for p in files}
    excess = len(files) - max_archive
    if excess <= 0:
        return
    for path in files[:excess]:
        path.unlink(missing_ok=True)
        md = path.with_suffix(".md")
        if md in md_files or md.is_file():
            md.unlink(missing_ok=True)


def write_ops_report(
    vault: Path,
    report: dict[str, Any],
    *,
    archive: bool = False,
    max_archive: int = DEFAULT_MAX_ARCHIVE,
) -> tuple[Path, Path]:
    """Persist regenerable ops-report under ``generated/ops/ops-report.*`` only."""
    vault = vault.expanduser().resolve()
    validate_record(report, "ops-report")
    json_path = _assert_ops_report_path(vault, vault / REPORT_JSON_RELATIVE)
    md_path = _assert_ops_report_path(vault, vault / REPORT_MD_RELATIVE)
    json_bytes = report_to_json(report).encode("utf-8")
    md_bytes = report_to_markdown(report).encode("utf-8")
    _write_atomic(json_path, json_bytes)
    _write_atomic(md_path, md_bytes)

    if archive:
        archive_dir = _assert_ops_report_path(vault, vault / ARCHIVE_DIR)
        archive_dir.mkdir(parents=True, exist_ok=True)
        seq = _archive_sequence(archive_dir)
        archived_json = _assert_ops_report_path(
            vault, archive_dir / f"ops-report-{seq:04d}.json"
        )
        archived_md = _assert_ops_report_path(
            vault, archive_dir / f"ops-report-{seq:04d}.md"
        )
        _write_atomic(archived_json, json_bytes)
        _write_atomic(archived_md, md_bytes)
        _apply_archive_retention(archive_dir, max_archive=max_archive)

    return json_path, md_path


def emit_ops_report(
    vault: Path,
    *,
    include_events: bool = True,
    event_limit: int = DEFAULT_EVENT_LIMIT,
    persist: bool = True,
    archive: bool = False,
    max_archive: int = DEFAULT_MAX_ARCHIVE,
) -> dict[str, Any]:
    """Build → optionally persist the tip-safe ops-report projection."""
    report = build_ops_report(
        vault, include_events=include_events, event_limit=event_limit
    )
    if persist:
        write_ops_report(vault, report, archive=archive, max_archive=max_archive)
    return report
