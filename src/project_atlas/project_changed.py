"""AS-CODER-ALPHA-CHANGED-001 — What Changed derived lens from connect inventory.

Compares the previous connect active-source inventory to the current one and
emits a non-authoritative ``generated/answers/ans-changed-<project>.json``
lens. Defaults to last-connect → now (no tribal kdiff flags required).

Honesty:
- lens != Layer B authority; UI != canonical
- first connect has no prior baseline → UNKNOWN/baseline, not invented history
- live STALE + historical UNCHANGED => unchanged is not current
  (AS-CODER-ALPHA-HONESTY-TAIL-001 / #384)
- live drift never invents temporal added/modified history; rollup may stay unchanged
- no wall-clock timestamps (NFR-001 / ADR-001)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from project_atlas.inventory_drift import evaluate_connect_inventory_drift

PACKAGE_ID = "AS-CODER-ALPHA-CHANGED-001"
GENERATOR_ID = "atlas-coder-alpha-changed-001"
ANSWERS_RELATIVE = Path("generated") / "answers"
INVENTORY_RELATIVE = Path("generated") / "ops" / "connect-inventory.json"
PREV_INVENTORY_RELATIVE = Path("generated") / "ops" / "connect-inventory.prev.json"


class ProjectChangedError(ValueError):
    """Fail-closed what-changed lens error."""


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


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
        raise ProjectChangedError(f"unsafe project id: {project_id!r}")
    return project_id


def inventory_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Build a deterministic active-source inventory from a discover manifest."""
    sources = manifest.get("sources")
    rows: list[dict[str, str]] = []
    if isinstance(sources, list):
        for row in sources:
            if not isinstance(row, dict):
                continue
            if row.get("exclusion_reason"):
                continue
            path = row.get("path")
            digest = row.get("sha256")
            project = row.get("likely_project") or "unknown-project"
            if not isinstance(path, str) or not isinstance(digest, str):
                continue
            rows.append(
                {
                    "path": path,
                    "sha256": digest,
                    "project_id": str(project),
                }
            )
    rows.sort(key=lambda item: item["path"])
    by_path = {item["path"]: item["sha256"] for item in rows}
    return {
        "schema_version": 1,
        "schema": "atlas.coder-alpha.connect-inventory.v1",
        "package": PACKAGE_ID,
        "sources": rows,
        "by_path": by_path,
        "generated": {"by": GENERATOR_ID},
    }


def diff_inventories(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> dict[str, Any]:
    """Compute added/removed/modified paths between inventories."""
    prev_map = previous.get("by_path") if isinstance(previous, dict) else None
    if not isinstance(prev_map, dict):
        prev_map = {}
    curr_map = current.get("by_path")
    if not isinstance(curr_map, dict):
        curr_map = {}
    prev_paths = {str(key) for key in prev_map}
    curr_paths = {str(key) for key in curr_map}
    added = sorted(curr_paths - prev_paths)
    removed = sorted(prev_paths - curr_paths)
    modified = sorted(
        path
        for path in sorted(prev_paths & curr_paths)
        if prev_map.get(path) != curr_map.get(path)
    )
    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "unchanged": len(prev_paths & curr_paths) - len(modified),
        "prior_baseline": bool(previous),
    }


def _project_paths(paths: list[str], project_id: str, inventory: dict[str, Any]) -> list[str]:
    rows = inventory.get("sources")
    if not isinstance(rows, list):
        return [path for path in paths if path]
    claimed: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        path = str(row.get("path") or "")
        owner = str(row.get("project_id") or "").strip()
        if path and owner:
            claimed[path] = owner
    owned = {path for path, owner in claimed.items() if owner == project_id}
    scoped: list[str] = []
    for path in paths:
        if not path:
            continue
        claimed_owner = claimed.get(path)
        if claimed_owner == project_id or path in owned:
            scoped.append(path)
            continue
        if claimed_owner:
            # Sibling-owned path under this project's folder prefix.
            continue
        if path.split("/", 1)[0] == project_id:
            scoped.append(path)
    return scoped


def _semantic_class_for_path(path: str) -> str | None:
    """Map a source path to a higher-level semantic change class (D-043).

    Inventory diffs remain authoritative for add/mod/remove counts. This
    classifier only answers what a user should actually know about — not every
    file touch. Returns None when the path is operational churn.
    """
    posix = path.replace("\\", "/").removeprefix("./").lower()
    name = Path(posix).name
    # Operational / generated noise never becomes semantic project change.
    if any(
        token in posix
        for token in (
            ".atlas-vault",
            "node_modules/",
            "__pycache__/",
            ".git/",
            "coverage",
            "/dist/",
            "/build/",
        )
    ):
        return None
    if any(
        hint in posix
        for hint in (
            "/adr",
            "decisions.md",
            "/decision",
            "docs/decisions",
        )
    ) or name.startswith("adr-"):
        return "decision_change"
    if name in {"agents.md", "claude.md", "readme.md"} or posix in {
        "docs/plan.md",
        "docs/prp.md",
        "docs/product/coder-alpha-north-star.md",
    }:
        return "project_state_change"
    if any(
        hint in posix
        for hint in (
            "roadmap",
            "backlog",
            "todo",
            "next-work",
            "implementation-roadmap",
            "master-roadmap",
        )
    ):
        return "next_work_change"
    if posix.startswith("docs/") and name.endswith(".md"):
        return "project_state_change"
    return None


def _semantic_narrative(
    *,
    added: list[str],
    removed: list[str],
    modified: list[str],
) -> dict[str, Any]:
    """Build a bounded semantic change narrative from inventory path classes."""
    buckets: dict[str, list[dict[str, str]]] = {
        "decision_change": [],
        "project_state_change": [],
        "next_work_change": [],
    }
    for action, paths in (
        ("added", added),
        ("removed", removed),
        ("modified", modified),
    ):
        for path in paths:
            klass = _semantic_class_for_path(path)
            if klass is None:
                continue
            buckets[klass].append({"path": path, "action": action})

    signals: list[str] = []
    if buckets["decision_change"]:
        sample = buckets["decision_change"][0]
        signals.append(
            f"decision_change: {sample['action']} {sample['path']} "
            f"(+{len(buckets['decision_change']) - 1} more)"
            if len(buckets["decision_change"]) > 1
            else f"decision_change: {sample['action']} {sample['path']}"
        )
    if buckets["project_state_change"]:
        sample = buckets["project_state_change"][0]
        signals.append(
            f"project_state_change: {sample['action']} {sample['path']}"
            + (
                f" (+{len(buckets['project_state_change']) - 1} more)"
                if len(buckets["project_state_change"]) > 1
                else ""
            )
        )
    if buckets["next_work_change"]:
        sample = buckets["next_work_change"][0]
        signals.append(
            f"next_work_change: {sample['action']} {sample['path']}"
            + (
                f" (+{len(buckets['next_work_change']) - 1} more)"
                if len(buckets["next_work_change"]) > 1
                else ""
            )
        )

    meaningful = bool(signals)
    return {
        "meaningful": meaningful,
        "signals": signals[:6],
        "decision_change_count": len(buckets["decision_change"]),
        "project_state_change_count": len(buckets["project_state_change"]),
        "next_work_change_count": len(buckets["next_work_change"]),
        "examples": {
            key: value[:8] for key, value in buckets.items() if value
        },
    }


def build_changed_lens(
    vault: Path,
    project_id: str,
    *,
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    delta: dict[str, Any],
) -> dict[str, Any]:
    """Build one derived what-changed lens for ``project_id``."""
    project_id = _safe_project_id(project_id)
    inspected = [
        INVENTORY_RELATIVE.as_posix(),
        PREV_INVENTORY_RELATIVE.as_posix(),
        f"projects/{project_id}/project.md",
    ]
    added = _project_paths(list(delta.get("added") or []), project_id, current)
    removed = _project_paths(list(delta.get("removed") or []), project_id, previous or {})
    modified = _project_paths(list(delta.get("modified") or []), project_id, current)
    semantic = _semantic_narrative(added=added, removed=removed, modified=modified)

    if not delta.get("prior_baseline"):
        status = "unknown"
        rollup = "baseline"
        summary = "No prior connect inventory; baseline established (UNKNOWN history)."
        value = None
        notes = [
            "First connect baseline; not an invented change history",
            "lens!=Layer-B-authority",
            "UI!=canonical",
            "UNKNOWN!=healthy",
        ]
    else:
        total = len(added) + len(removed) + len(modified)
        status = "derived"
        rollup = "changed" if total else "unchanged"
        inventory_bit = (
            f"rollup={rollup}; added={len(added)}; removed={len(removed)}; "
            f"modified={len(modified)}"
        )
        if semantic["meaningful"]:
            summary = inventory_bit + "; know_about=" + "; ".join(semantic["signals"])
        elif total:
            summary = (
                inventory_bit
                + "; know_about=inventory-only (no decision/state/next-work signal)"
            )
        else:
            summary = inventory_bit
        value = summary
        notes = [
            "Derived from last-connect → now active-source inventory diff",
            "semantic signals are path-class narratives, not invented history",
            "lens!=Layer-B-authority",
            "UI!=canonical",
            "not a kdiff temporal authority claim",
        ]

    # Live drift is a freshness signal only. It must not mint added/modified
    # history or rewrite historical rollup (AS-CODER-ALPHA-HONESTY-TAIL-001 / #384).
    live = evaluate_connect_inventory_drift(vault, project_id)
    live_status = str(live.get("status") or "UNKNOWN")
    live_paths = [item for item in (live.get("changed_paths") or []) if isinstance(item, str)]
    inspected.append("generated/ops/connect-manifest.json")
    honesty = {
        "lens_is_authority": False,
        "stale_is_current": False,
        "unchanged_is_current": rollup == "unchanged" and live_status == "FRESH",
        "unknown_is_healthy": False,
        "unknown_is_fresh": False,
    }
    if live_status == "STALE" and rollup == "unchanged":
        notes.append(
            "STALE LIVE != UNCHANGED / reconnect before treating What Changed as current"
        )
        if isinstance(summary, str):
            summary = f"{summary}; live sources drifted (reconnect required)"
            value = summary

    return {
        "schema_version": 1,
        "schema": "atlas.coder-alpha.changed-lens.v1",
        "package": PACKAGE_ID,
        "answer_id": f"ans-changed-{project_id}",
        "subject": project_id,
        "field": "what_changed",
        "title": "What changed?",
        "summary": summary,
        "value": value,
        "status": status,
        "authority": "derived-lens",
        "layer": "C",
        "project_id": project_id,
        "rollup": rollup,
        "delta": {
            "added": added[:50],
            "removed": removed[:50],
            "modified": modified[:50],
            "added_count": len(added),
            "removed_count": len(removed),
            "modified_count": len(modified),
            "truncated": len(added) > 50 or len(removed) > 50 or len(modified) > 50,
        },
        "semantic": semantic,
        "inspected_artifacts": inspected,
        "notes": notes,
        "generated": {"by": GENERATOR_ID},
        "source_drift": {
            "status": live_status,
            "reason": live.get("reason"),
            "reason_code": live.get("reason_code"),
            "changed_paths": live_paths[:20],
            "package": live.get("package"),
            "honesty": live.get("honesty") or {
                "unknown_is_fresh": False,
                "unknown_is_healthy": False,
                "stale_is_current": False,
                "lens_is_authority": False,
            },
        },
        "honesty": honesty,
    }


def rotate_and_diff_inventory(
    vault: Path,
    manifest: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    """Write current inventory, keep previous, return (current, delta, previous)."""
    vault = vault.expanduser().resolve()
    current = inventory_from_manifest(manifest)
    inv_path = vault / INVENTORY_RELATIVE
    prev_path = vault / PREV_INVENTORY_RELATIVE
    previous = _read_json(inv_path)
    if inv_path.is_file():
        # Preserve prior baseline for diff/receipt inspection.
        _write_atomic(prev_path, inv_path.read_bytes())
    _write_atomic(
        inv_path,
        (json.dumps(current, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    delta = diff_inventories(previous, current)
    return current, delta, previous


def materialize_changed_lenses(
    vault: Path,
    *,
    manifest: dict[str, Any] | None = None,
    project_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Materialize what-changed lenses; optionally rotate inventory from manifest."""
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise ProjectChangedError(f"vault is not a directory: {vault}")

    previous: dict[str, Any] | None
    current: dict[str, Any]
    if manifest is not None:
        current, delta, previous = rotate_and_diff_inventory(vault, manifest)
    else:
        loaded = _read_json(vault / INVENTORY_RELATIVE)
        previous = _read_json(vault / PREV_INVENTORY_RELATIVE)
        if loaded is None:
            raise ProjectChangedError(
                "missing connect inventory; run atlas connect or pass a manifest"
            )
        current = loaded
        delta = diff_inventories(previous, current)

    selected = project_ids if project_ids is not None else _list_projects(vault)
    written: list[str] = []
    lenses: list[dict[str, Any]] = []
    for project_id in selected:
        lens = build_changed_lens(
            vault,
            project_id,
            previous=previous,
            current=current,
            delta=delta,
        )
        lenses.append(lens)
        answer_id = str(lens["answer_id"])
        path = vault / ANSWERS_RELATIVE / f"{answer_id}.json"
        _write_atomic(
            path,
            (json.dumps(lens, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
        written.append(path.relative_to(vault).as_posix())

    return {
        "schema_version": 1,
        "schema": "atlas.coder-alpha.changed-receipt.v1",
        "package": PACKAGE_ID,
        "status": "ok",
        "vault": vault.as_posix(),
        "projects": list(selected),
        "answers_written": written,
        "lenses": lenses,
        "delta": {
            "added_count": len(delta.get("added") or []),
            "removed_count": len(delta.get("removed") or []),
            "modified_count": len(delta.get("modified") or []),
            "prior_baseline": bool(delta.get("prior_baseline")),
        },
        "generated": {"by": GENERATOR_ID},
        "honesty": {
            "authentic_pilot": False,
            "release_certified": False,
            "atlas_opt_wake_gate": "CLOSED",
            "lens_is_authority": False,
        },
    }
