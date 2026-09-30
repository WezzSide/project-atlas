"""AS-CODER-ALPHA-STATE-001 — Current State derived lens from Core.

Builds a non-authoritative ``generated/answers/ans-state-<project>.json``
lens from knowledge-status signals, review queues, conflicts, and project
lifecycle metadata so humans/agents can answer "what is the current state?"
after ``atlas connect`` without tribal flags.

Honesty:
- lens != Layer B authority
- UI != canonical truth
- UNKNOWN stays UNKNOWN when signals are absent
- no wall-clock timestamps (NFR-001 / ADR-001)
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from project_atlas.inventory_drift import attach_source_drift

PACKAGE_ID = "AS-CODER-ALPHA-STATE-001"
GENERATOR_ID = "atlas-coder-alpha-state-001"
ANSWERS_RELATIVE = Path("generated") / "answers"
_JSON_FENCE_RE = re.compile(
    r"## Semantic record\s*```json\s*(.*?)\s*```",
    re.DOTALL | re.IGNORECASE,
)
_STATUS_ROW_RE = re.compile(
    r"^\|\s*([^|]+?)\s*\|\s*([0-9]+)\s*\|\s*$",
    re.MULTILINE,
)


class ProjectStateError(ValueError):
    """Fail-closed current-state lens error."""


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
    if not project_id or project_id in {".", ".."} or "/" in project_id or "\\" in project_id:
        raise ProjectStateError(f"unsafe project id: {project_id!r}")
    return project_id


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _parse_semantic_record(project_md: str) -> dict[str, Any] | None:
    match = _JSON_FENCE_RE.search(project_md)
    if not match:
        return None
    try:
        raw = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return raw if isinstance(raw, dict) else None


def _parse_status_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for match in _STATUS_ROW_RE.finditer(text):
        label = match.group(1).strip().lower()
        if label in {"signal", "---"} or set(label) <= {"-"}:
            continue
        counts[label] = int(match.group(2))
    return counts


def _entry_count(payload: dict[str, Any] | None, *, pending_only: bool = False) -> int:
    """Count review entries; optionally only unresolved pending rows (D-044)."""
    if not payload:
        return 0
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return 0
    if not pending_only:
        return len(entries)
    count = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "pending")
        if status in {"pending", "in-review", ""}:
            count += 1
    return count


def _rollup(
    *,
    lifecycle: str | None,
    pending_reviews: int,
    unresolved_conflicts: int,
    stale_claims: int,
    sources_failed: int,
    status_file_present: bool,
) -> str:
    """Deterministic honesty rollup — not a trust score."""
    if not status_file_present and lifecycle is None:
        return "unknown"
    if unresolved_conflicts > 0 or sources_failed > 0:
        return "attention"
    if pending_reviews > 0 or stale_claims > 0:
        return "review"
    if lifecycle in {None, "", "unknown"}:
        return "unknown"
    return "stable"


def build_state_lens(vault: Path, project_id: str) -> dict[str, Any]:
    """Build one derived current-state lens for ``project_id`` (no disk writes)."""
    project_id = _safe_project_id(project_id)
    inspected: list[str] = []
    project_note = vault / "projects" / project_id / "project.md"
    status_path = vault / "projects" / project_id / "knowledge-status.md"
    pending_path = vault / "review" / "pending" / f"{project_id}.json"
    conflicts_path = vault / "review" / "conflicts" / f"{project_id}.json"
    current_state_path = vault / "state" / "current-state" / f"{project_id}.json"

    lifecycle: str | None = None
    if project_note.is_file():
        inspected.append(f"projects/{project_id}/project.md")
        try:
            note_text = project_note.read_text(encoding="utf-8")
        except OSError as exc:
            raise ProjectStateError(f"unreadable project note: {project_note}: {exc}") from exc
        semantic = _parse_semantic_record(note_text)
        if isinstance(semantic, dict):
            raw_lifecycle = semantic.get("lifecycle")
            if isinstance(raw_lifecycle, str) and raw_lifecycle.strip():
                lifecycle = raw_lifecycle.strip()

    status_counts: dict[str, int] = {}
    status_present = status_path.is_file()
    if status_present:
        inspected.append(f"projects/{project_id}/knowledge-status.md")
        try:
            status_counts = _parse_status_counts(status_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ProjectStateError(f"unreadable knowledge-status: {status_path}: {exc}") from exc

    pending_payload = _read_json(pending_path)
    pending_unreadable = pending_path.is_file() and pending_payload is None
    if pending_path.is_file():
        inspected.append(pending_path.relative_to(vault).as_posix())
    conflicts_payload = _read_json(conflicts_path)
    if conflicts_path.is_file():
        inspected.append(conflicts_path.relative_to(vault).as_posix())
    if current_state_path.is_file():
        inspected.append(current_state_path.relative_to(vault).as_posix())

    # Prefer live pending-queue status after human dispositions (D-044 B3).
    # knowledge-status.md can lag until reconnect; never resurrect decided rows.
    # Unreadable pending file: do not claim 0 as authoritative and do not fall
    # back to stale knowledge-status (D-047 IV / BRIEF_PENDING_MISMATCH).
    queue_pending = _entry_count(pending_payload, pending_only=True)
    if pending_unreadable:
        pending_reviews = 0
    elif pending_path.is_file():
        pending_reviews = queue_pending
    else:
        pending_reviews = max(
            queue_pending,
            status_counts.get("claims awaiting review", 0),
        )
    unresolved_conflicts = max(
        _entry_count(conflicts_payload),
        status_counts.get("unresolved conflicts", 0),
    )
    stale_claims = status_counts.get("stale claims", 0)
    sources_failed = status_counts.get("sources failed", 0)
    sources_complete = status_counts.get("sources complete", 0)
    verified_claims = status_counts.get("verified claims", 0)

    rollup = _rollup(
        lifecycle=lifecycle,
        pending_reviews=pending_reviews,
        unresolved_conflicts=unresolved_conflicts,
        stale_claims=stale_claims,
        sources_failed=sources_failed,
        status_file_present=status_present,
    )
    # Unreadable pending is integrity-unknown, not a confirmed-empty queue.
    # A declared lifecycle must not promote that hole to rollup=stable
    # (AS-STATE-UNREADABLE-PENDING-ROLLUP-001). Keep attention/review.
    if pending_unreadable and rollup == "stable":
        rollup = "unknown"

    summary_bits = [
        f"lifecycle={lifecycle or 'unknown'}",
        f"rollup={rollup}",
        f"pending_reviews={pending_reviews}",
        f"unresolved_conflicts={unresolved_conflicts}",
        f"stale_claims={stale_claims}",
        f"sources_complete={sources_complete}",
        f"sources_failed={sources_failed}",
        f"verified_claims={verified_claims}",
    ]
    summary = "; ".join(summary_bits)
    value = summary if status_present or lifecycle is not None else None
    status = "derived" if value is not None else "unknown"
    if pending_unreadable:
        status = "unknown"
        summary = f"{summary}; pending_queue=unreadable" if value is not None else (
            "pending_queue=unreadable"
        )
        value = summary

    notes = [
        "Derived from knowledge-status + review queues + project lifecycle",
        "lens!=Layer-B-authority",
        "UI!=canonical",
        "rollup!=trust-score",
        "UNKNOWN!=healthy",
    ]
    if pending_unreadable:
        notes.append("pending-queue-unreadable; not CLEAR; not stale knowledge-status")
    if rollup == "unknown":
        notes.append("lifecycle/status insufficient; rollup stays UNKNOWN")

    lens = {
        "schema_version": 1,
        "schema": "atlas.coder-alpha.state-lens.v1",
        "package": PACKAGE_ID,
        "answer_id": f"ans-state-{project_id}",
        "subject": project_id,
        "field": "current_state",
        "title": "What is the current state?",
        "summary": summary if value is not None else None,
        "value": value,
        "status": status,
        "authority": "derived-lens",
        "layer": "C",
        "project_id": project_id,
        "lifecycle": lifecycle or "unknown",
        "rollup": rollup,
        "signals": {
            "pending_reviews": pending_reviews,
            "unresolved_conflicts": unresolved_conflicts,
            "stale_claims": stale_claims,
            "sources_complete": sources_complete,
            "sources_failed": sources_failed,
            "verified_claims": verified_claims,
            "status_counts": status_counts,
        },
        "inspected_artifacts": inspected,
        "notes": notes,
        "generated": {"by": GENERATOR_ID},
    }
    lens = attach_source_drift(lens, vault, project_id)
    source_drift = lens.get("source_drift")
    drift_status = (
        str(source_drift.get("status") or "UNKNOWN")
        if isinstance(source_drift, dict)
        else "UNKNOWN"
    )
    if drift_status == "STALE" and lens.get("rollup") == "stable":
        lens["rollup"] = "unknown"
        rewritten = lens.get("summary")
        if isinstance(rewritten, str):
            rewritten = rewritten.replace("rollup=stable", "rollup=unknown")
            lens["summary"] = rewritten
            if lens.get("value") is not None:
                lens["value"] = rewritten
        raw_notes = lens.get("notes")
        extra_notes = (
            [item for item in raw_notes if isinstance(item, str)]
            if isinstance(raw_notes, list)
            else []
        )
        extra_notes.append("source inventory STALE; rollup stable rewritten to unknown")
        lens["notes"] = extra_notes
    return lens


def materialize_state_lenses(
    vault: Path,
    *,
    project_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Write current-state answer lenses under ``generated/answers/``."""
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise ProjectStateError(f"vault is not a directory: {vault}")
    selected = project_ids if project_ids is not None else _list_projects(vault)
    written: list[str] = []
    lenses: list[dict[str, Any]] = []
    for project_id in selected:
        lens = build_state_lens(vault, project_id)
        lenses.append(lens)
        answer_id = str(lens["answer_id"])
        path = vault / ANSWERS_RELATIVE / f"{answer_id}.json"
        payload = (json.dumps(lens, indent=2, sort_keys=True) + "\n").encode("utf-8")
        _write_atomic(path, payload)
        written.append(path.relative_to(vault).as_posix())

    return {
        "schema_version": 1,
        "schema": "atlas.coder-alpha.state-receipt.v1",
        "package": PACKAGE_ID,
        "status": "ok",
        "vault": vault.as_posix(),
        "projects": list(selected),
        "answers_written": written,
        "lenses": lenses,
        "generated": {"by": GENERATOR_ID},
        "honesty": {
            "authentic_pilot": False,
            "release_certified": False,
            "atlas_opt_wake_gate": "CLOSED",
            "lens_is_authority": False,
        },
    }
