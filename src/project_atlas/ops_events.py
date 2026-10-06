"""AS-OBS-002 Operational Event Model (OPS-EVT-* append-only stream).

Owns ``generated/ops/events/**`` only. Events are operational facts — never
project authority, temporal currentness, or claim/query answers
(``truth_plane: operational``, ``authority_plane: none``).

Hard rules:
- Health ≠ truth / Health ≠ authority (OBS-001 snapshot is consume-only).
- DO NOT REOPEN AS-OBS-001 producer semantics beyond consume.
- DO NOT dual-own AS-OBS-003 ops-report / dashboard writers.
- DO NOT dual-own incremental compiler cache emit trees.
- No fabricated events: emitters require evidence refs or observed transitions.
- NFR-004: payloads are codes/refs only — secret material rejected.
- NFR-001: no wall-clock timestamps in generated content; retention is
  count/size capped (calendar archive is operator-offline).
- Monitoring stacks and release-certification packages are out of scope.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

from project_atlas.schema import validate_record
from project_atlas.secrets import scan_text

SCHEMA_ID = "atlas.ops.event.v1"
STREAM_SCHEMA_ID = "atlas.ops.event_stream.v1"
GENERATOR_ID = "atlas-obs-002"
EVENTS_DIR = Path("generated") / "ops" / "events"
STREAM_RELATIVE = EVENTS_DIR / "stream.jsonl"
MANIFEST_RELATIVE = EVENTS_DIR / "stream-manifest.json"
HEALTH_STATE_RELATIVE = EVENTS_DIR / "health-state.json"
SNAPSHOT_RELATIVE = Path("generated") / "ops" / "health-snapshot.json"

DEFAULT_MAX_EVENTS = 10_000
DEFAULT_MAX_BYTES = 8 * 1024 * 1024

Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
HealthState = Literal["healthy", "degraded", "unhealthy", "unknown"]

# Wave-006 AS-OBSERVABILITY-CONTRACT.md §5 — normative catalog.
EVENT_CATALOG: tuple[str, ...] = (
    "OPS-EVT-SYNC-PLANNED",
    "OPS-EVT-SYNC-STARTED",
    "OPS-EVT-SYNC-SUCCEEDED",
    "OPS-EVT-SYNC-FAILED",
    "OPS-EVT-SPOOL-PENDING",
    "OPS-EVT-SPOOL-DRAINED",
    "OPS-EVT-QUARANTINE-ADDED",
    "OPS-EVT-GRAPH-ACCEPT",
    "OPS-EVT-QUERY-CORRUPTION",
    "OPS-EVT-ADAPTER-STALE",
    "OPS-EVT-SKILL-DRIFT",
    "OPS-EVT-CI-FAILED",
    "OPS-EVT-BACKUP-COMPLETED",
    "OPS-EVT-MIGRATION-PARTIAL",
    "OPS-EVT-HEALTH-TRANSITION",
)

_DEFAULT_SEVERITY: dict[str, Severity] = {
    "OPS-EVT-SYNC-PLANNED": "INFO",
    "OPS-EVT-SYNC-STARTED": "INFO",
    "OPS-EVT-SYNC-SUCCEEDED": "INFO",
    "OPS-EVT-SYNC-FAILED": "HIGH",
    "OPS-EVT-SPOOL-PENDING": "HIGH",
    "OPS-EVT-SPOOL-DRAINED": "INFO",
    "OPS-EVT-QUARANTINE-ADDED": "MEDIUM",
    "OPS-EVT-GRAPH-ACCEPT": "INFO",
    "OPS-EVT-QUERY-CORRUPTION": "CRITICAL",
    "OPS-EVT-ADAPTER-STALE": "MEDIUM",
    "OPS-EVT-SKILL-DRIFT": "CRITICAL",
    "OPS-EVT-CI-FAILED": "HIGH",
    "OPS-EVT-BACKUP-COMPLETED": "INFO",
    "OPS-EVT-MIGRATION-PARTIAL": "CRITICAL",
    "OPS-EVT-HEALTH-TRANSITION": "MEDIUM",
}


class OpsEventError(ValueError):
    """Raised when an operational event cannot be emitted or retained safely."""


def _events_root(vault: Path) -> Path:
    return _inside(vault, vault / EVENTS_DIR)


def _inside(vault: Path, path: Path) -> Path:
    resolved_vault = vault.expanduser().resolve()
    resolved = path.expanduser().resolve()
    if not resolved.is_relative_to(resolved_vault):
        raise OpsEventError(f"path escapes vault root: {path}")
    return resolved


def _assert_ops_events_path(vault: Path, path: Path) -> Path:
    target = _inside(vault, path)
    posix = target.as_posix().replace("\\", "/")
    if "generated/ops/events" not in posix:
        raise OpsEventError(f"refusing non-ops-events write path: {target}")
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


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _event_uid(sequence: int, event_id: str, payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        f"{sequence}|{event_id}|{_canonical_json(payload)}".encode()
    ).hexdigest()
    return digest[:32]


def _scan_payload_for_secrets(payload: dict[str, Any]) -> None:
    blob = _canonical_json(payload)
    findings = scan_text(blob)
    if findings:
        codes = sorted({item.pattern for item in findings})
        raise OpsEventError(f"NFR-004 secret patterns in payload: {','.join(codes)}")


def default_severity(event_id: str) -> Severity:
    if event_id not in EVENT_CATALOG:
        raise OpsEventError(f"unknown OPS-EVT id: {event_id}")
    return _DEFAULT_SEVERITY[event_id]


def build_event(
    *,
    event_id: str,
    sequence: int,
    payload: dict[str, Any] | None = None,
    evidence_refs: list[str] | None = None,
    severity: Severity | None = None,
) -> dict[str, Any]:
    """Build a schema-bound OPS-EVT-* envelope (no I/O)."""
    if event_id not in EVENT_CATALOG:
        raise OpsEventError(f"unknown OPS-EVT id: {event_id}")
    if sequence < 1:
        raise OpsEventError("sequence must be >= 1")
    body = dict(payload or {})
    _scan_payload_for_secrets(body)
    refs = list(evidence_refs or [])
    if not refs:
        raise OpsEventError("evidence_refs required — no fabricated events")
    for ref in refs:
        if not isinstance(ref, str) or not ref.strip():
            raise OpsEventError("evidence_refs must be non-empty strings")
    event: dict[str, Any] = {
        "schema": SCHEMA_ID,
        "truth_plane": "operational",
        "authority_plane": "none",
        "note": "OPERATIONAL EVENT ≠ PROJECT AUTHORITY",
        "event_id": event_id,
        "sequence": sequence,
        "event_uid": _event_uid(sequence, event_id, body),
        "severity": severity or default_severity(event_id),
        "payload": body,
        "evidence_refs": refs,
        "generated": {"by": GENERATOR_ID},
    }
    validate_record(event, "ops-event")
    return event


def event_to_jsonl_line(event: dict[str, Any]) -> str:
    """Single deterministic JSONL line (sort_keys, no trailing spaces)."""
    return json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _empty_manifest(*, max_events: int, max_bytes: int) -> dict[str, Any]:
    return {
        "schema": STREAM_SCHEMA_ID,
        "truth_plane": "operational",
        "authority_plane": "none",
        "note": "OPERATIONAL EVENT STREAM ≠ PROJECT AUTHORITY",
        "stream_path": STREAM_RELATIVE.as_posix(),
        "next_sequence": 1,
        "event_count": 0,
        "retention": {"max_events": max_events, "max_bytes": max_bytes},
        "last_event_uid": None,
        "generated": {"by": GENERATOR_ID},
    }


def load_stream_manifest(
    vault: Path,
    *,
    max_events: int = DEFAULT_MAX_EVENTS,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    """Load or synthesize the stream manifest (ops-events owned)."""
    vault = vault.expanduser().resolve()
    path = _assert_ops_events_path(vault, vault / MANIFEST_RELATIVE)
    if not path.is_file():
        manifest = _empty_manifest(max_events=max_events, max_bytes=max_bytes)
        validate_record(manifest, "ops-event-stream")
        return manifest
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OpsEventError(f"corrupt stream manifest: {exc}") from exc
    if not isinstance(raw, dict):
        raise OpsEventError("stream manifest must be an object")
    # Preserve operator retention caps when present.
    retention = raw.get("retention")
    if isinstance(retention, dict):
        me = retention.get("max_events", max_events)
        mb = retention.get("max_bytes", max_bytes)
        if isinstance(me, int) and me >= 1:
            max_events = me
        if isinstance(mb, int) and mb >= 1024:
            max_bytes = mb
    raw.setdefault("retention", {"max_events": max_events, "max_bytes": max_bytes})
    validate_record(raw, "ops-event-stream")
    return raw


def write_stream_manifest(vault: Path, manifest: dict[str, Any]) -> Path:
    vault = vault.expanduser().resolve()
    validate_record(manifest, "ops-event-stream")
    target = _assert_ops_events_path(vault, vault / MANIFEST_RELATIVE)
    payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    _write_atomic(target, payload.encode("utf-8"))
    return target


def read_events(vault: Path) -> list[dict[str, Any]]:
    """Replay the append-only JSONL stream (fail-closed on corrupt lines)."""
    vault = vault.expanduser().resolve()
    path = _assert_ops_events_path(vault, vault / STREAM_RELATIVE)
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise OpsEventError(f"unreadable event stream: {exc}") from exc
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise OpsEventError(f"corrupt JSONL at line {line_no}: {exc}") from exc
        if not isinstance(raw, dict):
            raise OpsEventError(f"JSONL line {line_no} is not an object")
        validate_record(raw, "ops-event")
        try:
            # Scan the whole decoded envelope (payload + evidence_refs +
            # other string fields). Payload-only scan left evidence_refs
            # rewrite-leaking decoded secrets.
            _scan_payload_for_secrets(raw)
        except OpsEventError as exc:
            # AS-SEC-SCAN-OPS-EVT-PAYLOAD-JSON-ESC-001: retention rewrite
            # must not json.dumps decoded secret-shaped fields that
            # scan_text missed on the escaped on-disk form.
            raise OpsEventError(
                f"NFR-004 secret patterns in stream line {line_no}: {exc}"
            ) from exc
        events.append(raw)
    return events


def _rewrite_stream(vault: Path, events: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    stream = _assert_ops_events_path(vault, vault / STREAM_RELATIVE)
    lines = [event_to_jsonl_line(event) for event in events]
    body = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
    _write_atomic(stream, body)
    next_seq = (events[-1]["sequence"] + 1) if events else 1
    manifest["next_sequence"] = next_seq
    manifest["event_count"] = len(events)
    manifest["last_event_uid"] = events[-1]["event_uid"] if events else None
    manifest["generated"] = {"by": GENERATOR_ID}
    write_stream_manifest(vault, manifest)


def apply_retention(
    vault: Path,
    *,
    max_events: int | None = None,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    """Trim oldest events to honor count/size caps. Never touches receipts."""
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise OpsEventError(f"vault is not a directory: {vault}")
    manifest = load_stream_manifest(vault)
    caps = dict(manifest["retention"])
    if max_events is not None:
        if max_events < 1:
            raise OpsEventError("max_events must be >= 1")
        caps["max_events"] = max_events
    if max_bytes is not None:
        if max_bytes < 1024:
            raise OpsEventError("max_bytes must be >= 1024")
        caps["max_bytes"] = max_bytes
    manifest["retention"] = caps

    events = read_events(vault)
    # Count cap (drop oldest).
    if len(events) > caps["max_events"]:
        events = events[-caps["max_events"] :]
    # Size cap (drop oldest until under budget).
    while events:
        encoded = ("\n".join(event_to_jsonl_line(e) for e in events) + "\n").encode("utf-8")
        if len(encoded) <= caps["max_bytes"]:
            break
        events = events[1:]
    _rewrite_stream(vault, events, manifest)
    return manifest


def append_event(
    vault: Path,
    *,
    event_id: str,
    payload: dict[str, Any] | None = None,
    evidence_refs: list[str] | None = None,
    severity: Severity | None = None,
    apply_caps: bool = True,
) -> dict[str, Any]:
    """Append one OPS-EVT-* record under ``generated/ops/events/`` only."""
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise OpsEventError(f"vault is not a directory: {vault}")
    _events_root(vault).mkdir(parents=True, exist_ok=True)
    manifest = load_stream_manifest(vault)
    sequence = int(manifest["next_sequence"])
    event = build_event(
        event_id=event_id,
        sequence=sequence,
        payload=payload,
        evidence_refs=evidence_refs,
        severity=severity,
    )
    stream = _assert_ops_events_path(vault, vault / STREAM_RELATIVE)
    line = event_to_jsonl_line(event) + "\n"
    stream.parent.mkdir(parents=True, exist_ok=True)
    with stream.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)
    manifest["next_sequence"] = sequence + 1
    manifest["event_count"] = int(manifest["event_count"]) + 1
    manifest["last_event_uid"] = event["event_uid"]
    manifest["generated"] = {"by": GENERATOR_ID}
    write_stream_manifest(vault, manifest)
    if apply_caps:
        apply_retention(vault)
    return event


def _load_health_snapshot(vault: Path) -> dict[str, Any]:
    path = _inside(vault, vault / SNAPSHOT_RELATIVE)
    if not path.is_file():
        raise OpsEventError(
            "health snapshot missing — run atlas ops health first "
            "(AS-OBS-001 consume-only; no fabricated transitions)"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OpsEventError(f"unreadable health snapshot: {exc}") from exc
    if not isinstance(raw, dict):
        raise OpsEventError("health snapshot must be an object")
    # Consume-only validation against OBS-001 schema — do not mutate OBS-001 product.
    validate_record(raw, "ops-health-snapshot")
    return raw


def _load_health_state(vault: Path) -> dict[str, Any] | None:
    path = _assert_ops_events_path(vault, vault / HEALTH_STATE_RELATIVE)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _write_health_state(vault: Path, state: dict[str, Any]) -> None:
    target = _assert_ops_events_path(vault, vault / HEALTH_STATE_RELATIVE)
    payload = json.dumps(state, indent=2, sort_keys=True) + "\n"
    _write_atomic(target, payload.encode("utf-8"))


def record_health_transition(
    vault: Path,
    *,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Emit OPS-EVT-HEALTH-TRANSITION when estate rollup changes.

    Consumes AS-OBS-001 ``health-snapshot.json`` (or an in-memory snapshot).
    Returns the appended event, or ``None`` when no transition occurred.
    Never fabricates a transition without an observed from→to change.
    """
    vault = vault.expanduser().resolve()
    snap = snapshot if snapshot is not None else _load_health_snapshot(vault)
    rollup = snap.get("rollup")
    if not isinstance(rollup, dict):
        raise OpsEventError("snapshot missing rollup")
    to_state = rollup.get("estate")
    if to_state not in {"healthy", "degraded", "unhealthy", "unknown"}:
        raise OpsEventError(f"invalid estate rollup: {to_state!r}")

    prior = _load_health_state(vault)
    from_state = prior.get("estate") if isinstance(prior, dict) else None
    if from_state is None:
        # First observation: seed state without fabricating a transition event.
        _write_health_state(
            vault,
            {
                "schema": "atlas.ops.health_state.v1",
                "truth_plane": "operational",
                "authority_plane": "none",
                "estate": to_state,
                "generated": {"by": GENERATOR_ID},
            },
        )
        return None
    if from_state == to_state:
        return None

    triggering: list[str] = []
    for signal in snap.get("signals") or []:
        if not isinstance(signal, dict):
            continue
        status = signal.get("status")
        if status in {"fail", "warn", "unknown"}:
            sid = signal.get("signal_id")
            if isinstance(sid, str) and sid:
                triggering.append(sid)
    triggering = sorted(set(triggering))

    severity: Severity
    if to_state == "unhealthy":
        severity = "HIGH"
    elif to_state in {"degraded", "unknown"}:
        severity = "MEDIUM"
    else:
        severity = "INFO"
    event = append_event(
        vault,
        event_id="OPS-EVT-HEALTH-TRANSITION",
        payload={
            "scope": "estate",
            "scope_id": snap.get("estate_id", "unknown"),
            "from": from_state,
            "to": to_state,
            "triggering_signal_ids": triggering,
        },
        evidence_refs=[SNAPSHOT_RELATIVE.as_posix()],
        severity=severity,
    )
    _write_health_state(
        vault,
        {
            "schema": "atlas.ops.health_state.v1",
            "truth_plane": "operational",
            "authority_plane": "none",
            "estate": to_state,
            "last_transition_uid": event["event_uid"],
            "generated": {"by": GENERATOR_ID},
        },
    )
    return event
