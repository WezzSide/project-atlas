"""AS-CODER-ALPHA-CONTEXT-001 + HANDOFF-001 — agent context export and handoff.

Builds paste-ready agent context and durable handoff packs from the Coder Alpha
project brief + evidence links. Does not invent estate facts; UNKNOWN remains.

AS-CODER-ALPHA-CONTEXT-FRESHNESS-ADV-001 binds a minimal non-authoritative
connect-manifest identity at write time so a pack that was valid then cannot
silently appear current after the live estate changes. Live comparison reuses
``inventory_drift``. No second source-hash / fingerprint engine. Historical
``#378`` is not retargeted. This is not a duplicate of owner-held ``#419``
(live inventory stamping only).

Outputs (under vault):
- ``generated/ops/agent-context/<project>.md``
- ``generated/ops/agent-context/<project>.json``
- ``generated/ops/handoffs/<handoff_id>.json``
- ``generated/ops/handoffs/latest.json`` pointer
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from atlas_contracts.identity import safe_relative_component
from project_atlas.conversation_capture import (
    list_conversation_captures,
    render_conversation_captures_markdown,
)
from project_atlas.inventory_drift import CONNECT_MANIFEST, attach_source_drift
from project_atlas.inventory_drift import PACKAGE_ID as DRIFT_PACKAGE
from project_atlas.project_brief import ProjectBriefError, build_project_brief
from project_atlas.secrets import scan_text
from project_atlas.session_capture import (
    SessionCaptureError,
    capture_session,
    list_captures,
    render_captures_markdown,
)

PACKAGE_CONTEXT = "AS-CODER-ALPHA-CONTEXT-001"
PACKAGE_HANDOFF = "AS-CODER-ALPHA-HANDOFF-001"
PACKAGE_FRESHNESS_ADV = "AS-CODER-ALPHA-CONTEXT-FRESHNESS-ADV-001"
GENERATOR_ID = "atlas-coder-alpha-context-handoff-001"
CONTEXT_DIR = Path("generated") / "ops" / "agent-context"
HANDOFF_DIR = Path("generated") / "ops" / "handoffs"
FROZEN_ESTATE_NOT_CURRENT = (
    "FROZEN CONTEXT AT WRITE != CURRENT LIVE ESTATE; reconnect first"
)
RESUME_STALE_WARNING = (
    "WARNING: frozen estate at write != live estate; "
    "do not treat this pack as current"
)
UNBOUND_FROZEN_ESTATE = (
    "MISSING OR MALFORMED ESTATE_BINDING != CURRENT; fail closed UNKNOWN"
)


class AgentHandoffError(ValueError):
    """Fail-closed agent context/handoff error."""


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _safe_project_id(project_id: str) -> str:
    try:
        return safe_relative_component(project_id, label="project id")
    except ValueError as exc:
        raise AgentHandoffError(str(exc)) from exc


def _estate_honesty(*, live_status: str, is_current: bool) -> dict[str, Any]:
    return {
        "authentic_pilot": False,
        "atlas_opt_wake_gate": "CLOSED",
        "lens_is_authority": False,
        "invented_facts": False,
        "fresh_is_authority": False,
        "context_is_authority": False,
        "handoff_is_authority": False,
        "frozen_binding_is_authority": False,
        "unknown_is_fresh": False,
        "unknown_is_healthy": False,
        "unknown_is_current": False,
        "stale_is_current": False,
        "source_inventory_stale": live_status == "STALE",
        "pack_is_current": is_current,
    }


def _live_inventory_drift(vault: Path, project_id: str) -> dict[str, Any]:
    """Reuse inventory_drift. Does not hash project sources here."""
    lens = attach_source_drift(
        {
            "summary": "estate-binding",
            "inspected_artifacts": [
                CONTEXT_DIR.as_posix(),
                CONNECT_MANIFEST.as_posix(),
            ],
            "notes": [],
            "honesty": _estate_honesty(live_status="UNKNOWN", is_current=False),
        },
        vault.expanduser().resolve(),
        project_id,
    )
    drift = lens.get("source_drift") if isinstance(lens.get("source_drift"), dict) else {}
    return drift if isinstance(drift, dict) else {}


def _row_owner(item: dict[str, Any]) -> str | None:
    raw = item.get("likely_project") or item.get("project_id")
    return raw if isinstance(raw, str) and raw else None


def connect_manifest_identity(vault: Path, project_id: str) -> dict[str, Any]:
    """Identity of the already-written connect-manifest. Not a source hasher.

    Copies sha256 values already stored in the manifest. Hashes only the
    manifest file bytes so reconnect can be distinguished from the frozen pack.
    """
    scoped = _safe_project_id(project_id) if project_id else ""
    path = vault.expanduser().resolve() / CONNECT_MANIFEST
    if not path.is_file():
        return {
            "present": False,
            "readable": False,
            "manifest_sha256": None,
            "source_digests": [],
        }
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {
            "present": True,
            "readable": False,
            "manifest_sha256": digest,
            "source_digests": [],
        }
    if not isinstance(payload, dict):
        return {
            "present": True,
            "readable": False,
            "manifest_sha256": digest,
            "source_digests": [],
        }
    rows: list[dict[str, str]] = []
    sources = payload.get("sources")
    if isinstance(sources, list):
        for item in sources:
            if not isinstance(item, dict) or item.get("exclusion_reason"):
                continue
            owner = _row_owner(item)
            if owner != scoped:
                continue
            rel = item.get("path")
            sha = item.get("sha256")
            if isinstance(rel, str) and rel and isinstance(sha, str) and sha:
                # AS-SEC-SCAN-HANDOFF-MANIFEST-PATH-JSON-ESC-001: json.loads of
                # connect-manifest path can decode ``\u`` escapes that
                # scan_text misses on raw bytes.
                if scan_text(rel):
                    continue
                rows.append({"path": rel, "sha256": sha})
    rows.sort(key=lambda row: row["path"])
    return {
        "present": True,
        "readable": True,
        "manifest_sha256": digest,
        "source_digests": rows,
    }


def bind_estate_at_write(vault: Path, project_id: str) -> dict[str, Any]:
    """Minimal non-authoritative frozen-at-write estate marker."""
    scoped = _safe_project_id(project_id)
    drift = _live_inventory_drift(vault, scoped)
    live_status = str(drift.get("status") or "UNKNOWN")
    identity = connect_manifest_identity(vault, scoped)
    return {
        "schema_version": 1,
        "schema": "atlas.coder-alpha.estate-binding.v1",
        "package": PACKAGE_FRESHNESS_ADV,
        "engine": DRIFT_PACKAGE,
        "project_id": scoped,
        "status_at_write": live_status,
        "manifest_sha256": identity.get("manifest_sha256"),
        "source_digests": identity.get("source_digests") or [],
        "manifest_present": bool(identity.get("present")),
        "authority": False,
        "generated": {"by": GENERATOR_ID},
        "honesty": _estate_honesty(live_status=live_status, is_current=False),
    }


def _usable_frozen_binding(written: dict[str, Any] | None) -> bool:
    """True when a write-time binding exists to compare against live estate.

    A non-empty ``manifest_sha256`` is sufficient identity. An explicit
    ``status_at_write`` without a digest still counts as a written record
    (UNKNOWN-at-write must stay UNKNOWN). Missing/empty objects do not.
    """
    if not isinstance(written, dict) or not written:
        return False
    sha = written.get("manifest_sha256")
    if isinstance(sha, str) and bool(sha.strip()):
        return True
    return written.get("status_at_write") in {"UNKNOWN", "STALE", "FRESH"}


def evaluate_estate_currentness(
    vault: Path,
    project_id: str,
    *,
    frozen_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare frozen-at-write identity to the current live estate.

    Live file-vs-manifest drift is delegated to ``inventory_drift``.
    UNKNOWN/STALE live status is never upgraded to FRESH. A later
    connect-manifest identity change keeps an older pack not-current
    even when live files match the new manifest.
    """
    scoped = _safe_project_id(project_id) if project_id else ""
    drift = _live_inventory_drift(vault, scoped or project_id)
    live_status = str(drift.get("status") or "UNKNOWN")
    current_identity = connect_manifest_identity(vault, scoped or project_id)
    written = frozen_binding if isinstance(frozen_binding, dict) else None
    written_status = str((written or {}).get("status_at_write") or "")
    frozen_manifest = (written or {}).get("manifest_sha256")
    frozen_digests = (written or {}).get("source_digests")
    if not isinstance(frozen_digests, list):
        frozen_digests = []
    current_manifest = current_identity.get("manifest_sha256")
    current_digests = current_identity.get("source_digests") or []
    manifest_identity_changed = bool(written) and frozen_manifest != current_manifest
    source_digests_changed = bool(written) and frozen_digests != current_digests
    notes: list[str] = []
    status = live_status
    reason_code = str(drift.get("reason_code") or "LIVE_INVENTORY")
    if not _usable_frozen_binding(written):
        # Legacy / missing / malformed bindings never inherit live FRESH.
        status = "UNKNOWN"
        reason_code = "UNBOUND_FROZEN_ESTATE"
        notes.append(UNBOUND_FROZEN_ESTATE)
    elif live_status in {"STALE", "UNKNOWN"}:
        status = live_status
    elif written_status == "UNKNOWN":
        status = "UNKNOWN"
        reason_code = "WRITTEN_UNKNOWN_NOT_FRESH"
        notes.append("CONTEXT WRITTEN UNKNOWN != FRESH")
    elif written_status == "STALE" and (
        manifest_identity_changed or source_digests_changed
    ):
        status = "STALE"
        reason_code = "WRITTEN_STALE_ESTATE_CHANGED"
        notes.append("CONTEXT WRITTEN STALE != CURRENT")
    elif manifest_identity_changed or source_digests_changed:
        status = "STALE"
        reason_code = "FROZEN_ESTATE_NOT_CURRENT"
        notes.append(FROZEN_ESTATE_NOT_CURRENT)
    is_current = status == "FRESH"
    if status == "STALE" and FROZEN_ESTATE_NOT_CURRENT not in notes and (
        manifest_identity_changed or source_digests_changed
    ):
        notes.append(FROZEN_ESTATE_NOT_CURRENT)
    return {
        "schema_version": 1,
        "schema": "atlas.coder-alpha.context-freshness-adv.v1",
        "package": PACKAGE_FRESHNESS_ADV,
        "engine": DRIFT_PACKAGE,
        "status": status,
        "is_current": is_current,
        "reason": (
            UNBOUND_FROZEN_ESTATE
            if reason_code == "UNBOUND_FROZEN_ESTATE"
            else (
                drift.get("reason")
                if status == live_status and live_status != "FRESH"
                else (
                    "frozen estate at write is not the current live estate"
                    if status == "STALE"
                    else drift.get("reason")
                )
            )
        ),
        "reason_code": reason_code,
        "changed_paths": [
            item for item in (drift.get("changed_paths") or []) if isinstance(item, str)
        ][:20],
        "notes": notes,
        "source_drift": drift,
        "estate_compare": {
            "frozen_manifest_sha256": frozen_manifest,
            "current_manifest_sha256": current_manifest,
            "manifest_identity_changed": manifest_identity_changed,
            "source_digests_changed": source_digests_changed,
            "written_status": written_status or None,
        },
        "generated": {"by": GENERATOR_ID},
        "honesty": _estate_honesty(live_status=live_status, is_current=is_current),
    }


def evaluate_written_context(
    vault: Path,
    project_id: str,
    *,
    written: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Re-evaluate a previously written context/handoff pack."""
    scoped = _safe_project_id(project_id)
    payload = written
    if payload is None:
        path = vault.expanduser().resolve() / CONTEXT_DIR / f"{scoped}.json"
        if not path.is_file():
            raise AgentHandoffError(f"written context missing: {path}")
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AgentHandoffError(f"unreadable written context: {exc}") from exc
        if not isinstance(loaded, dict):
            raise AgentHandoffError("written context must be a JSON object")
        payload = loaded
    binding = payload.get("estate_binding")
    if not isinstance(binding, dict):
        nested = payload.get("context")
        if isinstance(nested, dict) and isinstance(nested.get("estate_binding"), dict):
            binding = nested["estate_binding"]
        else:
            binding = None
    return evaluate_estate_currentness(vault, scoped, frozen_binding=binding)


def _render_attention_section(attention: dict[str, Any] | None) -> list[str]:
    lines = ["", "## Attention (what requires action)"]
    if not attention:
        lines.append("UNKNOWN")
        return lines
    lines.append(f"rollup={attention.get('rollup', 'UNKNOWN')}")
    lines.append(
        "package=`AS-CODER-ALPHA-ATTENTION-001` "
        "implementation=`src/project_atlas/attention_hygiene.py` "
        "CLI=`atlas attention`"
    )
    care = attention.get("care_about") if isinstance(attention, dict) else None
    items = care if isinstance(care, list) and care else (
        attention.get("items") if isinstance(attention, dict) else None
    )
    if not isinstance(items, list) or not items:
        lines.append("- CLEAR / no attention items")
        return lines
    lines.append(f"care_about_count={len(items)}")
    if attention.get("source_failure_total"):
        lines.append(
            f"source_failure_total={attention.get('source_failure_total')} "
            "(not hidden; collapsed for triage)"
        )
    for item in items[:10]:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"- [{item.get('level')}] {item.get('why_seeing_this')} "
            f"| matter={item.get('why_it_matters')} "
            f"| do={item.get('what_to_do')} "
            f"| evidence={', '.join(item.get('evidence') or [])}"
        )
    return lines


def _render_source_health_section(health: dict[str, Any] | None) -> list[str]:
    lines = ["", "## Source health (failures / exclusions)"]
    if not health:
        lines.append("UNKNOWN")
        return lines
    lines.append(
        "package=`AS-CODER-ALPHA-SOURCE-HEALTH-001` "
        "implementation=`src/project_atlas/source_health.py` "
        "CLI=`atlas source-health`"
    )
    raw_counts = health.get("counts")
    counts: dict[str, Any] = raw_counts if isinstance(raw_counts, dict) else {}
    lines.append(
        f"source_count={health.get('source_count', 0)}; "
        + ", ".join(f"{key}={counts[key]}" for key in sorted(counts))
    )
    raw_rows = health.get("sources")
    rows: list[Any] = raw_rows if isinstance(raw_rows, list) else []
    # Prefer actionable failures over exclusion noise.
    preferred = [
        row
        for row in rows
        if isinstance(row, dict)
        and str(row.get("status") or "")
        in {"compile_failed", "promotion_failed", "quarantined", "compile_partial"}
    ]
    sample = preferred[:6] or [row for row in rows if isinstance(row, dict)][:6]
    for row in sample:
        lines.append(
            f"- {row.get('source')} | {row.get('status')} | "
            f"{row.get('reason_code')} | {row.get('human_explanation')} | "
            f"next={row.get('suggested_next_action')}"
        )
    if not sample:
        lines.append("- no failed/excluded sources in scoped report")
    return lines


def _render_roadmap_section(roadmap: dict[str, Any] | None) -> list[str]:
    lines = ["", "## Current project position (derived roadmap)"]
    if not roadmap:
        lines.append("UNKNOWN — no roadmap lens")
        return lines
    here = roadmap.get("you_are_here") if isinstance(roadmap, dict) else None
    nxt = roadmap.get("next_unlock") if isinstance(roadmap, dict) else None
    here = here if isinstance(here, dict) else {}
    nxt = nxt if isinstance(nxt, dict) else {}
    lines.append("ROADMAP!=CANONICAL_TRUTH. DERIVED_STATUS!=AUTHORITY.")
    lines.append(
        f"you_are_here={here.get('title') or 'UNKNOWN'} "
        f"[{here.get('status') or 'UNKNOWN'}/{here.get('lifecycle') or 'UNKNOWN'}] "
        f"why={here.get('why') or here.get('reason') or 'UNKNOWN'}"
    )
    lines.append(
        f"next_unlock={nxt.get('title') or 'UNKNOWN'} "
        f"[{nxt.get('status') or 'UNKNOWN'}] "
        f"why={nxt.get('why') or nxt.get('unlock_condition') or 'UNKNOWN'}"
    )
    path = roadmap.get("critical_path") or []
    if isinstance(path, list) and path:
        lines.append("critical_path=" + " → ".join(str(item) for item in path))
    else:
        lines.append("critical_path=UNKNOWN")
    blockers = roadmap.get("blockers") if isinstance(roadmap.get("blockers"), list) else []
    if blockers:
        lines.append(f"blockers={len(blockers)}")
        for blocker in blockers[:6]:
            if isinstance(blocker, dict):
                lines.append(
                    f"- {blocker.get('reason') or 'UNKNOWN'} "
                    f"waiting_on={blocker.get('waiting_on') or 'UNKNOWN'} "
                    f"unlock={blocker.get('unlock_condition') or 'UNKNOWN'}"
                )
    unknowns = roadmap.get("unknowns") if isinstance(roadmap.get("unknowns"), list) else []
    if unknowns:
        lines.append("unknowns=" + ", ".join(str(item) for item in unknowns[:8]))
    return lines


def _render_next_section(nxt: dict[str, Any] | None) -> list[str]:
    lines = ["", "## What next (derived)"]
    if not nxt:
        lines.append("UNKNOWN — no next lens")
        return lines
    lines.append("NEXT!=AUTHORITY. NEXT!=COMMAND. Do not auto-execute.")
    raw_primary = nxt.get("primary")
    primary: dict[str, Any] = raw_primary if isinstance(raw_primary, dict) else {}
    lines.append(
        f"primary={primary.get('title') or 'UNKNOWN'} "
        f"[{primary.get('kind') or 'UNKNOWN'}] "
        f"why={primary.get('why') or 'UNKNOWN'}"
    )
    lines.append(f"action={primary.get('action') or 'UNKNOWN'}")
    blocked = nxt.get("why_cannot_advance")
    if blocked:
        lines.append(f"why_cannot_advance={blocked}")
    for item in nxt.get("queue") or []:
        if isinstance(item, dict):
            lines.append(
                f"- [{item.get('kind')}] {item.get('title')}: {item.get('action')}"
            )
    return lines


def _render_context_markdown(
    brief: dict[str, Any],
    captures: list[dict[str, Any]] | None = None,
    *,
    attention: dict[str, Any] | None = None,
    source_health: dict[str, Any] | None = None,
    conversation_captures: list[dict[str, Any]] | None = None,
    roadmap: dict[str, Any] | None = None,
    nxt: dict[str, Any] | None = None,
) -> str:
    next_work = brief.get("suggested_next_work") or []
    evidence = brief.get("evidence_links") or []
    lines = [
        f"# Atlas Agent Context — {brief.get('project_id')}",
        "",
        "Derived from Atlas Truth Core via Coder Alpha brief. UI!=canonical.",
        "MODEL_OUTPUT!=AUTHORITY. UNKNOWN stays UNKNOWN.",
        "",
        "## Project identity",
        str(brief.get("project_identity") or "UNKNOWN"),
        "",
        "## Purpose",
        str(brief.get("purpose") or "UNKNOWN"),
        "",
        "## Tech stack",
        str(brief.get("tech_stack") or "UNKNOWN"),
        "",
        "## Architecture summary",
        str(brief.get("architecture_summary") or "UNKNOWN"),
        "",
        "## Current state",
        str(brief.get("current_state") or "UNKNOWN"),
    ]
    lines.extend(_render_roadmap_section(roadmap))
    lines.extend(_render_next_section(nxt))
    lines.extend(
        [
            "",
            "## Recent meaningful changes",
            str(brief.get("recent_meaningful_changes") or "UNKNOWN"),
            "",
            "## Important decisions",
            str(brief.get("important_decisions") or "UNKNOWN"),
            "",
            "## Known problems / unknown / conflicting",
            str(brief.get("unknown_or_conflicting") or "UNKNOWN"),
            "",
            "## Suggested next work",
        ]
    )
    if isinstance(next_work, list) and next_work:
        lines.extend(f"- {item}" for item in next_work)
    else:
        lines.append("- UNKNOWN")
    lines.extend(_render_attention_section(attention))
    lines.extend(_render_source_health_section(source_health))
    lines.extend(["", "## Session memory (captures)"])
    lines.extend(render_captures_markdown(captures or []))
    lines.extend(render_conversation_captures_markdown(conversation_captures or []))
    lines.extend(["", "## Evidence links"])
    if isinstance(evidence, list) and evidence:
        lines.extend(f"- `{item}`" for item in evidence[:40])
    else:
        lines.append("- UNKNOWN")
    lines.extend(
        [
            "",
            "## Honesty",
            "- authentic_pilot: false",
            "- atlas_opt_wake_gate: CLOSED",
            "- lens_is_authority: false",
            "- roadmap_is_canonical: false",
            "- next_is_command: false",
            "",
        ]
    )
    return "\n".join(lines)


def export_agent_context(
    vault: Path,
    project_id: str,
    *,
    refresh_brief: bool = True,
) -> dict[str, Any]:
    """Export paste-ready agent context for ``project_id``."""
    vault = vault.expanduser().resolve()
    project_id = _safe_project_id(project_id)
    if not vault.is_dir():
        raise AgentHandoffError(f"vault is not a directory: {vault}")
    try:
        brief = build_project_brief(vault, project_id, refresh=refresh_brief)
    except ProjectBriefError as exc:
        raise AgentHandoffError(str(exc)) from exc

    captures = list_captures(vault, project_id=project_id, limit=8)
    conversation_captures = list_conversation_captures(vault, project_id=project_id, limit=8)
    # Project shared Core classifiers — do not re-implement analysis here.
    attention: dict[str, Any] | None = None
    source_health: dict[str, Any] | None = None
    with contextlib.suppress(Exception):
        from project_atlas.attention_hygiene import classify_attention

        attention = classify_attention(vault, project_id)
    with contextlib.suppress(Exception):
        from project_atlas.source_health import explain_source_health

        source_health = explain_source_health(vault, project_id)
    roadmap: dict[str, Any] | None = None
    with contextlib.suppress(Exception):
        from project_atlas.project_roadmap import build_roadmap_lens

        roadmap = build_roadmap_lens(vault, project_id)
    nxt: dict[str, Any] | None = None
    with contextlib.suppress(Exception):
        from project_atlas.project_next import build_next_lens

        nxt = build_next_lens(vault, project_id)
    estate_binding = bind_estate_at_write(vault, project_id)
    estate = evaluate_estate_currentness(
        vault, project_id, frozen_binding=estate_binding
    )
    markdown = _render_context_markdown(
        brief,
        captures=captures,
        attention=attention,
        source_health=source_health,
        conversation_captures=conversation_captures,
        roadmap=roadmap,
        nxt=nxt,
    )
    honesty = {
        "authentic_pilot": False,
        "atlas_opt_wake_gate": "CLOSED",
        "lens_is_authority": False,
        "invented_facts": False,
    }
    honesty.update(estate.get("honesty") or {})
    payload = {
        "schema_version": 1,
        "schema": "atlas.coder-alpha.agent-context.v1",
        "package": PACKAGE_CONTEXT,
        "project_id": project_id,
        "estate_binding": estate_binding,
        "freshness": estate,
        "brief": brief,
        "attention": {
            "rollup": (attention or {}).get("rollup"),
            "item_count": (attention or {}).get("item_count"),
            "package": "AS-CODER-ALPHA-ATTENTION-001",
        },
        "source_health": {
            "source_count": (source_health or {}).get("source_count"),
            "counts": (source_health or {}).get("counts"),
            "package": "AS-CODER-ALPHA-SOURCE-HEALTH-001",
        },
        "session_captures": captures,
        "conversation_captures": conversation_captures,
        "roadmap": {
            "package": "AS-PROJECT-ROADMAP-001",
            "you_are_here": (roadmap or {}).get("you_are_here"),
            "next_unlock": (roadmap or {}).get("next_unlock"),
            "critical_path": (roadmap or {}).get("critical_path"),
            "blockers": (roadmap or {}).get("blockers"),
            "unknowns": (roadmap or {}).get("unknowns"),
            "honesty": (roadmap or {}).get("honesty"),
        },
        "next": {
            "package": "AS-CODER-ALPHA-NEXT-001",
            "primary": (nxt or {}).get("primary"),
            "queue": (nxt or {}).get("queue"),
            "why_cannot_advance": (nxt or {}).get("why_cannot_advance"),
            "suggested_next_work": (nxt or {}).get("suggested_next_work"),
            "honesty": (nxt or {}).get("honesty"),
        },
        "markdown": markdown,
        "generated": {"by": GENERATOR_ID},
        "honesty": honesty,
    }
    md_path = vault / CONTEXT_DIR / f"{project_id}.md"
    json_path = vault / CONTEXT_DIR / f"{project_id}.json"
    _write_atomic(md_path, markdown.encode("utf-8"))
    _write_atomic(
        json_path,
        (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return {
        "schema_version": 1,
        "package": PACKAGE_CONTEXT,
        "status": "ok",
        "project_id": project_id,
        "markdown_path": md_path.relative_to(vault).as_posix(),
        "json_path": json_path.relative_to(vault).as_posix(),
        "purpose": brief.get("purpose"),
        "conversation_captures": conversation_captures,
        "estate_binding": estate_binding,
        "freshness": estate,
        "honesty": honesty,
        "generated": {"by": GENERATOR_ID},
    }


def create_handoff(
    vault: Path,
    project_id: str,
    *,
    note: str | None = None,
    refresh_brief: bool = True,
    auto_capture: bool = True,
) -> dict[str, Any]:
    """Create a durable handoff pack another agent can resume from.

    When ``auto_capture`` is true (default), also writes a semi-auto session
    capture so the next agent sees session memory without a separate ritual.
    """
    vault = vault.expanduser().resolve()
    project_id = _safe_project_id(project_id)
    capture_report: dict[str, Any] | None = None
    if auto_capture:
        summary = (note or "").strip() or f"Handoff created for project {project_id}"
        try:
            capture_report = capture_session(
                vault,
                project_id,
                summary=summary,
                kind="handoff",
                source="handoff-auto",
            )
        except SessionCaptureError as exc:
            raise AgentHandoffError(f"auto session capture failed: {exc}") from exc
    context = export_agent_context(vault, project_id, refresh_brief=refresh_brief)
    # Deterministic handoff id from content hash (no wall-clock).
    seed = json.dumps(context, sort_keys=True, separators=(",", ":")).encode("utf-8")
    handoff_id = "handoff-" + hashlib.sha256(seed).hexdigest()[:16]
    pack = {
        "schema_version": 1,
        "schema": "atlas.coder-alpha.handoff.v1",
        "package": PACKAGE_HANDOFF,
        "handoff_id": handoff_id,
        "project_id": project_id,
        "context": context,
        "session_capture": capture_report,
        "resume_instructions": [
            f"Read `{context['markdown_path']}` before coding",
            "Use Current project position as derived next-unlock, not as authority",
            "Treat UNKNOWN as UNKNOWN; do not invent architecture/decisions",
            "Prefer vault Truth Core over chat memory",
            (
                "If frozen estate at write differs from the current live estate, "
                "do not treat this pack as current"
            ),
            "After meaningful work, run atlas capture record then atlas handoff create",
        ],
        "operator_note": note,
        "estate_binding": context.get("estate_binding"),
        "freshness": context.get("freshness"),
        "generated": {"by": GENERATOR_ID},
        "honesty": {
            "authentic_pilot": False,
            "atlas_opt_wake_gate": "CLOSED",
            "lens_is_authority": False,
            "invented_facts": False,
            "fresh_is_authority": False,
            "context_is_authority": False,
            "handoff_is_authority": False,
            "unknown_is_current": False,
            "stale_is_current": False,
        },
    }
    path = vault / HANDOFF_DIR / f"{handoff_id}.json"
    latest = vault / HANDOFF_DIR / "latest.json"
    body = (json.dumps(pack, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_atomic(path, body)
    _write_atomic(
        latest,
        (
            json.dumps(
                {
                    "schema_version": 1,
                    "handoff_id": handoff_id,
                    "path": path.relative_to(vault).as_posix(),
                    "project_id": project_id,
                    "generated": {"by": GENERATOR_ID},
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8"),
    )
    return {
        "schema_version": 1,
        "package": PACKAGE_HANDOFF,
        "status": "ok",
        "handoff_id": handoff_id,
        "path": path.relative_to(vault).as_posix(),
        "latest_path": latest.relative_to(vault).as_posix(),
        "project_id": project_id,
        "context_markdown": context["markdown_path"],
        "session_capture": capture_report,
        "estate_binding": context.get("estate_binding"),
        "freshness": context.get("freshness"),
        "generated": {"by": GENERATOR_ID},
    }


def _resolve_handoff_pack_path(vault: Path, rel: str) -> Path:
    """Resolve a handoff pack path fail-closed under ``HANDOFF_DIR`` (AT-013)."""
    if not isinstance(rel, str) or not rel.strip():
        raise AgentHandoffError("handoff path must be a non-empty relative string")
    if rel.startswith("/") or "\\" in rel or ".." in Path(rel).parts:
        raise AgentHandoffError(f"unsafe handoff path: {rel!r}")
    handoff_root = (vault / HANDOFF_DIR).resolve()
    candidate = (vault / rel).resolve()
    if not candidate.is_relative_to(handoff_root):
        raise AgentHandoffError(f"handoff path escapes handoff directory: {rel!r}")
    return candidate


def resume_handoff(
    vault: Path,
    *,
    handoff_id: str | None = None,
) -> dict[str, Any]:
    """Load a handoff pack (latest if id omitted)."""
    vault = vault.expanduser().resolve()
    if handoff_id:
        if "/" in handoff_id or "\\" in handoff_id or handoff_id in {".", ".."}:
            raise AgentHandoffError(f"unsafe handoff id: {handoff_id!r}")
        path = _resolve_handoff_pack_path(
            vault, (HANDOFF_DIR / f"{handoff_id}.json").as_posix()
        )
    else:
        latest = vault / HANDOFF_DIR / "latest.json"
        if not latest.is_file():
            raise AgentHandoffError("no handoff latest pointer; run atlas handoff create")
        try:
            pointer = json.loads(latest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AgentHandoffError(f"unreadable handoff latest: {exc}") from exc
        rel = pointer.get("path") if isinstance(pointer, dict) else None
        if not isinstance(rel, str):
            raise AgentHandoffError("handoff latest pointer missing path")
        path = _resolve_handoff_pack_path(vault, rel)
    if not path.is_file():
        raise AgentHandoffError(f"handoff pack missing: {path}")
    try:
        pack = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentHandoffError(f"unreadable handoff pack: {exc}") from exc
    if not isinstance(pack, dict):
        raise AgentHandoffError("handoff pack must be a JSON object")
    written = pack.get("freshness") if isinstance(pack.get("freshness"), dict) else None
    binding = pack.get("estate_binding")
    if not isinstance(binding, dict):
        nested = pack.get("context")
        if isinstance(nested, dict) and isinstance(nested.get("estate_binding"), dict):
            binding = nested["estate_binding"]
        else:
            binding = None
    live_id = pack.get("project_id")
    live = evaluate_estate_currentness(
        vault,
        live_id if isinstance(live_id, str) else "",
        frozen_binding=binding if isinstance(binding, dict) else None,
    )
    pack["freshness_at_write"] = written
    pack["estate_binding_at_write"] = binding
    pack["freshness"] = live
    honesty = dict(pack.get("honesty") or {}) if isinstance(pack.get("honesty"), dict) else {}
    honesty.update(live.get("honesty") or {})
    pack["honesty"] = honesty
    pack["status"] = "resumed"
    pack["resume_path"] = path.relative_to(vault).as_posix()
    # Forged freshness on disk is never authority: live recompute above wins.
    if live.get("is_current") is True:
        pack["resume_warning"] = None
    else:
        pack["resume_warning"] = RESUME_STALE_WARNING
    return pack
