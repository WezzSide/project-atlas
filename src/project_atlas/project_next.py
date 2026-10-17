"""AS-CODER-ALPHA-NEXT-001 — first-class daily What Next lens.

Composes existing derived Coder Alpha signals into one honest answer to
``what should happen next?``:

- attention ``care_about`` (BLOCKING / ACTION_REQUIRED first)
- unknown/conflict + pending review honesty
- source-health actionable failures
- roadmap ``next_unlock`` / blockers
- brief-style coverage and decision gaps

This is **not** ``AS-2.0-NEXT-001`` / ``intelligence/next_action.py`` and does
not expose Wave 15/16 API or Web intelligence surfaces.

Honesty:
- NEXT LENS != AUTHORITY
- NEXT ACTION != COMMAND
- UNKNOWN is valid
- no invented work
- no auto-execution
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

from atlas_contracts.identity import safe_relative_component
from project_atlas.inventory_drift import (
    attach_source_drift,
    evaluate_connect_inventory_drift,
)
from project_atlas.secrets import scan_text

PACKAGE_ID = "AS-CODER-ALPHA-NEXT-001"
GENERATOR_ID = "atlas-coder-alpha-next-001"
SCHEMA_ID = "atlas.coder-alpha.next.v1"
ANSWERS_RELATIVE = Path("generated") / "answers"

_KIND_RANK = {
    "blocking_attention": 10,
    "stale_evidence": 15,
    "unresolved_conflict": 20,
    "pending_review": 30,
    "source_failure": 40,
    "roadmap_blocked": 50,
    "roadmap_unlock": 60,
    "coverage_gap": 70,
    "missing_decisions": 80,
    "stale_changed": 90,
    "unknown": 100,
}

_BLOCKING_KINDS = frozenset(
    {
        "blocking_attention",
        "unresolved_conflict",
        "source_failure",
        "roadmap_blocked",
    }
)


class ProjectNextError(ValueError):
    """Fail-closed What Next lens error."""


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _list_projects(vault: Path) -> list[str]:
    root = vault / "projects"
    if not root.is_dir():
        return []
    return sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )


def _safe_project_id(project_id: str) -> str:
    try:
        return safe_relative_component(project_id, label="project id")
    except ValueError as exc:
        raise ProjectNextError(str(exc)) from exc


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _load_answer(vault: Path, name: str, project_id: str) -> dict[str, Any] | None:
    path = vault / ANSWERS_RELATIVE / f"ans-{name}-{project_id}.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _queue_item(
    *,
    kind: str,
    title: str,
    why: str,
    action: str,
    evidence: list[str],
    source_package: str,
    subject_id: str | None = None,
) -> dict[str, Any]:
    # AS-SEC-SCAN-NEXT-JSON-ESC-001: json.loads of pending review_id can
    # decode \\u escapes that scan_text misses on raw bytes.
    if subject_id and scan_text(subject_id):
        subject_id = None
    if scan_text(title) or scan_text(why):
        title = "UNKNOWN"
        why = "secret-shaped attention scalar redacted"
    return {
        "kind": kind,
        "title": title,
        "why": why,
        "action": action,
        "evidence": evidence,
        "source_package": source_package,
        "subject_id": subject_id,
        "rank": _KIND_RANK.get(kind, 99),
        "is_command": False,
        "is_authority": False,
    }


def _dedupe_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("kind") or ""),
        str(item.get("title") or ""),
        str(item.get("subject_id") or ""),
    )


def _collect_attention(vault: Path, project_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    items: list[dict[str, Any]] = []
    inspected: list[str] = []
    with contextlib.suppress(Exception):
        from project_atlas.attention_hygiene import classify_attention

        attention = classify_attention(vault, project_id)
        inspected.extend(str(path) for path in (attention.get("inspected_artifacts") or []) if path)
        for row in attention.get("care_about") or []:
            if not isinstance(row, dict):
                continue
            level = str(row.get("level") or "")
            kind_by_level = {
                "BLOCKING": "blocking_attention",
                "ACTION_REQUIRED": "unresolved_conflict",
                "CONFLICT": "unresolved_conflict",
                "NEEDS_HUMAN_REVIEW": "pending_review",
                "SOURCE_FAILURE": "source_failure",
            }
            kind = kind_by_level.get(level)
            if kind is None:
                continue
            title = str(row.get("reason_code") or row.get("kind") or "attention")
            items.append(
                _queue_item(
                    kind=kind,
                    title=title,
                    why=str(row.get("why_seeing_this") or "attention signal"),
                    action=str(row.get("what_to_do") or "Inspect atlas attention"),
                    evidence=[str(path) for path in (row.get("evidence") or []) if path][:6],
                    source_package="AS-CODER-ALPHA-ATTENTION-001",
                    subject_id=str(row.get("subject_id") or "") or None,
                )
            )
    return items, inspected


def _collect_source_health(
    vault: Path, project_id: str
) -> tuple[list[dict[str, Any]], list[str]]:
    items: list[dict[str, Any]] = []
    inspected: list[str] = []
    with contextlib.suppress(Exception):
        from project_atlas.source_health import explain_source_health

        health = explain_source_health(vault, project_id)
        inspected.extend(str(path) for path in (health.get("inspected_artifacts") or []) if path)
        for row in health.get("actionable") or []:
            if not isinstance(row, dict):
                continue
            status = str(row.get("status") or "")
            if status not in {
                "quarantined",
                "compile_failed",
                "promotion_failed",
                "unreadable",
            }:
                continue
            source = str(row.get("source") or row.get("reason_code") or "source")
            items.append(
                _queue_item(
                    kind="source_failure",
                    title=f"{status}:{source}",
                    why=str(row.get("human_explanation") or row.get("reason_code") or status),
                    action=str(row.get("suggested_next_action") or "Inspect atlas source-health"),
                    evidence=[str(row.get("evidence") or "")][:1] if row.get("evidence") else [],
                    source_package="AS-CODER-ALPHA-SOURCE-HEALTH-001",
                    subject_id=str(row.get("source_id") or "") or None,
                )
            )
    return items, inspected


def _collect_roadmap(vault: Path, project_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    items: list[dict[str, Any]] = []
    inspected: list[str] = []
    with contextlib.suppress(Exception):
        from project_atlas.project_roadmap import build_roadmap_lens

        roadmap = build_roadmap_lens(vault, project_id)
        inspected.extend(str(path) for path in (roadmap.get("inspected_artifacts") or []) if path)
        nxt = _as_dict(roadmap.get("next_unlock"))
        reason = str(nxt.get("reason") or "")
        title = str(nxt.get("title") or nxt.get("item_id") or "UNKNOWN")
        blocked_status = str(nxt.get("status") or "") == "BLOCKED"
        if reason in {"blocked", "waiting_on_dependency"} or blocked_status:
            items.append(
                _queue_item(
                    kind="roadmap_blocked",
                    title=title,
                    why=str(
                        nxt.get("why")
                        or nxt.get("unlock_condition")
                        or "roadmap item is blocked"
                    ),
                    action=str(
                        nxt.get("unlock_condition")
                        or "Satisfy the documented unlock condition before advancing"
                    ),
                    evidence=["generated/answers/ans-roadmap-" + project_id + ".json"],
                    source_package="AS-PROJECT-ROADMAP-001",
                    subject_id=str(nxt.get("item_id") or "") or None,
                )
            )
        elif nxt.get("item_id") and reason not in {
            "no_roadmap_items",
            "no_remaining_unlock",
            "remaining_verification",
        }:
            items.append(
                _queue_item(
                    kind="roadmap_unlock",
                    title=title,
                    why=str(
                        nxt.get("why")
                        or nxt.get("unlock_condition")
                        or "next critical-path unlock"
                    ),
                    action=str(
                        nxt.get("unlock_condition")
                        or "Advance the first unfinished critical-path item"
                    ),
                    evidence=["generated/answers/ans-roadmap-" + project_id + ".json"],
                    source_package="AS-PROJECT-ROADMAP-001",
                    subject_id=str(nxt.get("item_id") or "") or None,
                )
            )
        for blocker in roadmap.get("blockers") or []:
            if not isinstance(blocker, dict):
                continue
            items.append(
                _queue_item(
                    kind="roadmap_blocked",
                    title=str(blocker.get("title") or blocker.get("item_id") or "blocked"),
                    why=str(blocker.get("reason") or blocker.get("why") or "blocked"),
                    action=str(
                        blocker.get("unlock_condition")
                        or f"waiting_on={blocker.get('waiting_on') or 'UNKNOWN'}"
                    ),
                    evidence=["generated/answers/ans-roadmap-" + project_id + ".json"],
                    source_package="AS-PROJECT-ROADMAP-001",
                    subject_id=str(blocker.get("item_id") or "") or None,
                )
            )
    return items, inspected


def _collect_honesty_gaps(vault: Path, project_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    items: list[dict[str, Any]] = []
    inspected: list[str] = []
    unknown = _load_answer(vault, "unknown", project_id)
    decisions = _load_answer(vault, "decisions", project_id)
    changed = _load_answer(vault, "changed", project_id)
    if unknown is not None:
        inspected.append(f"generated/answers/ans-unknown-{project_id}.json")
        signals = _as_dict(unknown.get("signals"))
        if int(signals.get("unresolved_conflicts") or 0) > 0:
            items.append(
                _queue_item(
                    kind="unresolved_conflict",
                    title="unresolved conflicts",
                    why="Unknown lens reports unresolved conflicts",
                    action="Resolve unresolved conflicts in review/conflicts",
                    evidence=[f"generated/answers/ans-unknown-{project_id}.json"],
                    source_package="AS-CODER-ALPHA-UNKNOWN-001",
                )
            )
        if int(signals.get("pending_reviews") or 0) > 0:
            items.append(
                _queue_item(
                    kind="pending_review",
                    title="pending human reviews",
                    why="Unknown lens reports pending reviews",
                    action="Triage pending human reviews in review/pending",
                    evidence=[f"generated/answers/ans-unknown-{project_id}.json"],
                    source_package="AS-CODER-ALPHA-UNKNOWN-001",
                )
            )
        raw_absent = signals.get("coverage_absent")
        if isinstance(raw_absent, list) and raw_absent:
            labels = [str(item) for item in raw_absent[:6]]
            items.append(
                _queue_item(
                    kind="coverage_gap",
                    title="absent coverage",
                    why="Add source evidence for absent coverage: " + ", ".join(labels),
                    action="Add source evidence for absent coverage: " + ", ".join(labels),
                    evidence=[f"generated/answers/ans-unknown-{project_id}.json"],
                    source_package="AS-CODER-ALPHA-UNKNOWN-001",
                )
            )
    if decisions is not None:
        inspected.append(f"generated/answers/ans-decisions-{project_id}.json")
        if decisions.get("status") == "unknown":
            items.append(
                _queue_item(
                    kind="missing_decisions",
                    title="decision memory unknown",
                    why="No derived decision memory for this project",
                    action="Capture important decisions in docs/DECISIONS.md or ADRs",
                    evidence=[f"generated/answers/ans-decisions-{project_id}.json"],
                    source_package="AS-CODER-ALPHA-DECISIONS-001",
                )
            )
    if changed is not None:
        inspected.append(f"generated/answers/ans-changed-{project_id}.json")
        if changed.get("rollup") == "baseline":
            items.append(
                _queue_item(
                    kind="stale_changed",
                    title="what-changed baseline only",
                    why="What Changed is still a first-connect baseline",
                    action="Re-run atlas connect after edits to populate What Changed",
                    evidence=[f"generated/answers/ans-changed-{project_id}.json"],
                    source_package="AS-CODER-ALPHA-CHANGED-001",
                )
            )
    return items, inspected


def _unknown_item() -> dict[str, Any]:
    return _queue_item(
        kind="unknown",
        title="UNKNOWN",
        why="no concrete next-work signal derived from Truth Core",
        action=(
            "Run atlas connect, then inspect atlas attention / atlas roadmap / "
            "atlas unknown before inventing work"
        ),
        evidence=[],
        source_package=PACKAGE_ID,
    )


def _suggested_lines(queue: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in queue[:8]:
        title = str(item.get("title") or "UNKNOWN")
        action = str(item.get("action") or "").strip()
        if action and action != title:
            lines.append(f"{title} — {action}")
        else:
            lines.append(title)
    return lines


def build_next_lens(vault: Path, project_id: str) -> dict[str, Any]:
    """Derive one non-authoritative What Next lens for ``project_id``."""
    vault = vault.expanduser().resolve()
    project_id = _safe_project_id(project_id)
    if not vault.is_dir():
        raise ProjectNextError(f"vault is not a directory: {vault}")
    project_dir = vault / "projects" / project_id
    if not project_dir.is_dir():
        raise ProjectNextError(f"unknown project: {project_id}")

    collected: list[dict[str, Any]] = []
    inspected: list[str] = []
    for collector in (
        _collect_attention,
        _collect_source_health,
        _collect_roadmap,
        _collect_honesty_gaps,
    ):
        rows, seen = collector(vault, project_id)
        collected.extend(rows)
        inspected.extend(seen)

    drift = evaluate_connect_inventory_drift(vault, project_id)
    inventory_stale = drift.get("status") == "STALE"
    if inventory_stale:
        collected.append(
            _queue_item(
                kind="stale_evidence",
                title="source inventory stale",
                why="live active sources drifted from connect-manifest",
                action="Re-run atlas connect before treating next-work as current",
                evidence=["generated/ops/connect-manifest.json"],
                source_package="AS-CODER-ALPHA-INVENTORY-DRIFT-001",
            )
        )

    deduped: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str]] = set()
    for item in collected:
        key = _dedupe_key(item)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(item)
    deduped.sort(key=lambda item: (int(item["rank"]), str(item["kind"]), str(item["title"])))
    queue = deduped[:10]
    if not queue:
        queue = [_unknown_item()]
    primary = queue[0]
    blockers = [item for item in queue if item["kind"] in _BLOCKING_KINDS]
    unknowns = [item for item in queue if item["kind"] == "unknown"]
    why_blocked = None
    if primary["kind"] in _BLOCKING_KINDS:
        why_blocked = str(primary.get("why") or primary.get("action") or "blocked")

    summary = str(primary.get("title") or "UNKNOWN")
    if primary["kind"] == "unknown":
        summary = "UNKNOWN"
    suggested = _suggested_lines(queue)
    return attach_source_drift(
        {
            "schema_version": 1,
            "schema": SCHEMA_ID,
            "package": PACKAGE_ID,
            "answer_id": f"ans-next-{project_id}",
            "project_id": project_id,
            "subject": project_id,
            "field": "next",
            "title": "What next",
            "summary": summary,
            "value": suggested[0] if suggested else "UNKNOWN",
            "status": "derived" if primary["kind"] != "unknown" else "unknown",
            "primary": primary,
            "queue": queue,
            "blockers": blockers,
            "unknowns": unknowns,
            "why_cannot_advance": why_blocked,
            "suggested_next_work": suggested,
            "inspected_artifacts": sorted(set(inspected)),
            "generated": {"by": GENERATOR_ID},
            "honesty": {
                "authentic_pilot": False,
                "release_certified": False,
                "atlas_opt_wake_gate": "CLOSED",
                "lens_is_authority": False,
                "next_is_authority": False,
                "next_is_command": False,
                "auto_execution": False,
                "unknown_is_valid": True,
                "fabricated_fields": False,
                "not_as_2_0_next_001": True,
                "derived_only": True,
                "answer_evidence_stale": inventory_stale,
            },
            "notes": [
                "Coder Alpha What Next over derived lenses",
                "NEXT!=AUTHORITY",
                "NEXT!=COMMAND",
                "UNKNOWN!=healthy",
                "Not AS-2.0-NEXT-001 / not Wave 15-16 intelligence",
            ],
            "truth_boundary": "NEXT LENS != AUTHORITY / NEXT ACTION != COMMAND",
        },
        vault,
        project_id,
    )


def derive_next_lenses(
    vault: Path,
    *,
    project_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Read-only derive. Does not write generated answers or Layer B."""
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise ProjectNextError(f"vault is not a directory: {vault}")
    selected = project_ids if project_ids is not None else _list_projects(vault)
    lenses = [build_next_lens(vault, project_id) for project_id in selected]
    return {
        "schema_version": 1,
        "schema": "atlas.coder-alpha.next-receipt.v1",
        "package": PACKAGE_ID,
        "status": "ok",
        "vault": vault.as_posix(),
        "projects": list(selected),
        "answers_written": [],
        "lenses": lenses,
        "generated": {"by": GENERATOR_ID},
        "honesty": {
            "lens_is_authority": False,
            "next_is_command": False,
            "read_only": True,
            "atlas_opt_wake_gate": "CLOSED",
            "not_as_2_0_next_001": True,
        },
    }


def materialize_next_lenses(
    vault: Path,
    *,
    project_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Write What Next answer lenses under ``generated/answers/``."""
    report = derive_next_lenses(vault, project_ids=project_ids)
    vault = Path(str(report["vault"]))
    written: list[str] = []
    for lens in report["lenses"]:
        answer_id = str(lens["answer_id"])
        path = vault / ANSWERS_RELATIVE / f"{answer_id}.json"
        payload = (json.dumps(lens, indent=2, sort_keys=True) + "\n").encode("utf-8")
        _write_atomic(path, payload)
        written.append(path.relative_to(vault).as_posix())
    report["answers_written"] = written
    honesty = dict(report["honesty"])
    honesty["read_only"] = False
    report["honesty"] = honesty
    return report


def render_next_text(lens: dict[str, Any]) -> str:
    """Human-readable CLI projection. Text != canonical."""
    primary = _as_dict(lens.get("primary"))
    lines = [
        f"atlas next [{lens.get('status', 'unknown')}]  (NEXT!=AUTHORITY / NEXT!=COMMAND)",
        f"  project:            {lens.get('project_id')}",
        f"  primary:            {primary.get('title') or 'UNKNOWN'} [{primary.get('kind')}]",
        f"  why:                {primary.get('why') or 'UNKNOWN'}",
        f"  action:             {primary.get('action') or 'UNKNOWN'}",
        f"  why_cannot_advance: {lens.get('why_cannot_advance') or '(none)'}",
        f"  blockers:           {len(lens.get('blockers') or [])}",
    ]
    for item in lens.get("queue") or []:
        lines.append(
            f"  - [{item.get('kind')}] {item.get('title')}: {item.get('action')}"
        )
    return "\n".join(lines)
