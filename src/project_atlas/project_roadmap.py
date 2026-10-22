"""AS-PROJECT-ROADMAP-001 — Living Project Roadmap V1 derived projection.

Product promise: see exactly where the project is, why it is there, and
what unlocks next.

Honesty:
- ROADMAP != CANONICAL_TRUTH
- ROADMAP != AUTHORITY
- ROADMAP != PROJECT STATE MUTATION
- UI != CANONICAL_TRUTH
- DERIVED_STATUS != AUTHORITY
- UNKNOWN != HEALTHY
- NO EVIDENCE != COMPLETE
- BLOCKED != FAILED
- MERGED != CLOSED
- IMPLEMENTATION_COMPLETE != CERTIFIED
- CERTIFIED != MERGED

No invented completion percentages. Package-count theatre is rejected.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

PACKAGE_ID = "AS-PROJECT-ROADMAP-001"
GENERATOR_ID = "atlas-project-roadmap-001"
SCHEMA_ID = "atlas.project-roadmap.v1"
ANSWERS_RELATIVE = Path("generated") / "answers"
_JSON_FENCE_RE = re.compile(
    r"## (?:Roadmap|Semantic) record\s*```json\s*(.*?)\s*```",
    re.DOTALL | re.IGNORECASE,
)

ITEM_STATUSES = (
    "VERIFIED_COMPLETION",
    "IMPLEMENTED",
    "IN_PROGRESS",
    "BLOCKED",
    "NOT_STARTED",
    "UNKNOWN",
)
LIFECYCLES = (
    "PLANNED",
    "ENTRY_GATE",
    "READY",
    "IN_PROGRESS",
    "IMPLEMENTATION_COMPLETE",
    "VERIFICATION_IN_PROGRESS",
    "CERTIFIED_MERGE_ELIGIBLE",
    "MERGE_AUTHORIZED",
    "MERGED",
    "POST_MERGE_VERIFIED",
    "CLOSED",
    "UNKNOWN",
)
_COMPLETE = frozenset({"VERIFIED_COMPLETION"})
_DONE_ENOUGH = frozenset({"VERIFIED_COMPLETION", "IMPLEMENTED"})
_NEXT_LIFECYCLE = {
    "PLANNED": "ENTRY_GATE",
    "ENTRY_GATE": "READY",
    "READY": "IN_PROGRESS",
    "IN_PROGRESS": "IMPLEMENTATION_COMPLETE",
    "IMPLEMENTATION_COMPLETE": "VERIFICATION_IN_PROGRESS",
    "VERIFICATION_IN_PROGRESS": "CERTIFIED_MERGE_ELIGIBLE",
    "CERTIFIED_MERGE_ELIGIBLE": "MERGE_AUTHORIZED",
    "MERGE_AUTHORIZED": "MERGED",
    "MERGED": "POST_MERGE_VERIFIED",
    "POST_MERGE_VERIFIED": "CLOSED",
}
_PERCENT_RE = re.compile(r"\d+\s*(%|/)")
_COUNT_THEATRE_RE = re.compile(
    r"^\s*\d+\s*(packages?|items?|tasks?)?\s*(done|complete)?\s*/\s*\d+",
    re.IGNORECASE,
)


class ProjectRoadmapError(ValueError):
    """Fail-closed roadmap error."""


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
        raise ProjectRoadmapError(f"unsafe project id: {project_id!r}")
    return project_id


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _parse_fenced_record(markdown: str) -> dict[str, Any] | None:
    match = _JSON_FENCE_RE.search(markdown)
    if not match:
        return None
    try:
        raw = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return raw if isinstance(raw, dict) else None


def _evidence_exists(vault: Path, ref: str) -> bool:
    if not ref or ref.startswith(("http://", "https://")):
        return False
    candidate = Path(ref)
    if candidate.is_absolute():
        return False
    posix = ref.replace("\\", "/").lstrip("./")
    if ".." in Path(posix).parts:
        return False
    return (vault / posix).is_file()


def _normalize_status(raw: Any) -> tuple[str, list[str]]:
    notes: list[str] = []
    if raw is None:
        return "UNKNOWN", ["status absent"]
    text = str(raw).strip()
    if not text:
        return "UNKNOWN", ["status empty"]
    if _PERCENT_RE.search(text) or _COUNT_THEATRE_RE.search(text):
        notes.append("rejected_package_count_theatre")
        return "UNKNOWN", notes
    key = text.upper().replace(" ", "_").replace("-", "_")
    aliases = {
        "VERIFIED": "VERIFIED_COMPLETION",
        "COMPLETE": "IMPLEMENTED",
        "DONE": "IMPLEMENTED",
        "IMPLEMENTATION_COMPLETE": "IMPLEMENTED",
        "IMPLEMENTATION-COMPLETE": "IMPLEMENTED",
    }
    if key in aliases:
        notes.append(f"status_alias:{text}->{aliases[key]}")
        key = aliases[key]
    # Informal lifecycle words are not progress collapse.
    if key in {
        "MERGED",
        "CLOSED",
        "CERTIFIED",
        "CERTIFIED_MERGE_ELIGIBLE",
        "MERGE_AUTHORIZED",
        "POST_MERGE_VERIFIED",
        "PLANNED",
        "ENTRY_GATE",
        "READY",
        "IMPLEMENTATION_COMPLETE",
        "VERIFICATION_IN_PROGRESS",
    }:
        notes.append(f"lifecycle_word_not_progress:{text}")
        if key in {"PLANNED", "ENTRY_GATE", "READY"}:
            return "NOT_STARTED", notes
        if key == "CLOSED":
            notes.append("closed_requires_post_merge_verified")
            return "UNKNOWN", notes
        if key in {"MERGED", "CERTIFIED", "CERTIFIED_MERGE_ELIGIBLE", "MERGE_AUTHORIZED"}:
            notes.append("merged_neq_closed")
            notes.append("implemented_neq_verified")
            return "IMPLEMENTED", notes
        if key == "POST_MERGE_VERIFIED":
            return "VERIFIED_COMPLETION", notes
        if key in {"IMPLEMENTATION_COMPLETE", "VERIFICATION_IN_PROGRESS"}:
            notes.append("implemented_neq_verified")
            return "IMPLEMENTED", notes
    if key not in ITEM_STATUSES:
        notes.append(f"unrecognized_status:{text}")
        return "UNKNOWN", notes
    return key, notes


def _normalize_lifecycle(raw: Any, *, progress: str, notes: list[str]) -> str:
    """Preserve lifecycle distinctions. MERGED != CLOSED. IMPLEMENTED != VERIFIED."""
    if raw is not None and str(raw).strip():
        key = str(raw).strip().upper().replace(" ", "_").replace("-", "_")
        key = {
            "CERTIFIED": "CERTIFIED_MERGE_ELIGIBLE",
            "CERTIFIED_MERGE_ELIGIBLE": "CERTIFIED_MERGE_ELIGIBLE",
            "IMPLEMENTATION-COMPLETE": "IMPLEMENTATION_COMPLETE",
            "VERIFICATION-IN-PROGRESS": "VERIFICATION_IN_PROGRESS",
            "POST-MERGE-VERIFIED": "POST_MERGE_VERIFIED",
            "ENTRY-GATE": "ENTRY_GATE",
            "MERGE-AUTHORIZED": "MERGE_AUTHORIZED",
        }.get(key, key)
        if key in LIFECYCLES:
            if key == "CLOSED":
                notes.append("closed_is_not_merged")
            if key == "MERGED":
                notes.append("merged_neq_closed")
            return key
        notes.append(f"unrecognized_lifecycle:{raw}")
    if progress == "VERIFIED_COMPLETION":
        return "POST_MERGE_VERIFIED"
    if progress == "IMPLEMENTED":
        return "IMPLEMENTATION_COMPLETE"
    if progress == "IN_PROGRESS":
        return "IN_PROGRESS"
    if progress == "BLOCKED":
        return "IN_PROGRESS"
    if progress == "NOT_STARTED":
        return "PLANNED"
    return "UNKNOWN"


def _load_roadmap_source(vault: Path, project_id: str) -> tuple[dict[str, Any] | None, list[str]]:
    inspected: list[str] = []
    note = vault / "projects" / project_id / "roadmap.md"
    if note.is_file():
        inspected.append(note.relative_to(vault).as_posix())
        record = _parse_fenced_record(note.read_text(encoding="utf-8"))
        if record is not None:
            return record, inspected
    project_md = vault / "projects" / project_id / "project.md"
    if project_md.is_file():
        inspected.append(project_md.relative_to(vault).as_posix())
        record = _parse_fenced_record(project_md.read_text(encoding="utf-8"))
        if isinstance(record, dict) and (
            isinstance(record.get("roadmap_items"), list) or isinstance(record.get("items"), list)
        ):
            return record, inspected
    return None, inspected


def _load_lens(vault: Path, name: str, project_id: str) -> dict[str, Any] | None:
    path = vault / ANSWERS_RELATIVE / f"ans-{name}-{project_id}.json"
    payload = _read_json(path)
    return payload


def _entry_owner(entry: dict[str, Any]) -> str | None:
    raw = entry.get("project_id") or entry.get("project")
    if raw is None or str(raw).strip() == "":
        return None
    return str(raw)


def _count_scoped_entries(
    entries: list[Any],
    project_id: str,
    *,
    file_is_project_scoped: bool,
    pending_only: bool = False,
) -> int:
    count = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        owner = _entry_owner(entry)
        if owner is None:
            if not file_is_project_scoped:
                continue
            owner = project_id
        if owner != project_id:
            continue
        if pending_only:
            status = str(entry.get("status") or "pending")
            if status not in {"pending", "in-review", ""}:
                continue
        count += 1
    return count


def _pending_review_count(vault: Path, project_id: str) -> tuple[int, bool]:
    """Count pending reviews. Unreadable scoped queue does not fall back.

    Missing ``review/pending/<project>.json`` may still use the global
    ``review/pending.json`` file (scoped by entry owner). A *present but
    unreadable* scoped file must not resurrect stale global rows
    (AS-ROADMAP-UNREADABLE-PENDING-FALLBACK-001 / D-047).
    """
    scoped_path = vault / "review" / "pending" / f"{project_id}.json"
    scoped_present = scoped_path.is_file()
    payload = _read_json(scoped_path)
    if scoped_present and payload is None:
        return 0, True
    file_is_project_scoped = payload is not None
    if not payload:
        payload = _read_json(vault / "review" / "pending.json")
        file_is_project_scoped = False
    if not payload:
        return 0, False
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return 0, False
    return (
        _count_scoped_entries(
            entries,
            project_id,
            file_is_project_scoped=file_is_project_scoped,
            pending_only=True,
        ),
        False,
    )


def _conflict_count(vault: Path, project_id: str) -> tuple[int, bool]:
    """Count unresolved conflicts for one project only.

    Cross-project files and foreign entries must never bleed. Prefer the
    isolated ``review/conflicts/<project>.json`` surface. A stale unknown
    lens must not inflate a live zero. A present-but-unreadable scoped
    file must not fall back to the unknown lens either
    (AS-ROADMAP-UNREADABLE-PENDING-FALLBACK-001).
    """
    live: list[int] = []
    review_path = vault / "review" / "conflicts" / f"{project_id}.json"
    review_present = review_path.is_file()
    review = _read_json(review_path)
    scoped_unreadable = review_present and review is None
    if review is not None:
        entries = review.get("entries")
        if isinstance(entries, list):
            live.append(
                _count_scoped_entries(
                    entries,
                    project_id,
                    file_is_project_scoped=True,
                )
            )
        else:
            live.append(0)

    conflicts_dir = vault / "generated" / "ops" / "conflicts"
    if conflicts_dir.is_dir():
        scoped = 0
        for path in sorted(conflicts_dir.glob("*.json")):
            if not path.is_file():
                continue
            payload = _read_json(path)
            if not payload:
                continue
            owner = str(payload.get("project_id") or payload.get("project") or "")
            if owner == project_id:
                scoped += 1
        live.append(scoped)

    if live:
        return max(live), scoped_unreadable
    if scoped_unreadable:
        return 0, True

    unknown = _load_lens(vault, "unknown", project_id)
    if not unknown:
        return 0, False
    raw = unknown.get("unresolved_conflicts")
    if isinstance(raw, int):
        return raw, False
    if isinstance(raw, list):
        return len(raw), False
    return 0, False


def _normalize_item(
    vault: Path,
    raw: dict[str, Any],
    *,
    index: int,
) -> dict[str, Any]:
    item_id = str(raw.get("id") or raw.get("item_id") or f"item-{index:03d}")
    title = str(raw.get("title") or raw.get("name") or item_id)
    status, status_notes = _normalize_status(raw.get("status") or raw.get("lifecycle"))
    depends_on = [
        str(dep)
        for dep in (raw.get("depends_on") or raw.get("dependencies") or [])
        if str(dep).strip()
    ]
    evidence = [str(ref) for ref in (raw.get("evidence") or []) if str(ref).strip()]
    missing = [ref for ref in evidence if not _evidence_exists(vault, ref)]
    present = [ref for ref in evidence if ref not in missing]
    flags: list[str] = list(status_notes)
    if status == "VERIFIED_COMPLETION" and not present:
        status = "IMPLEMENTED" if evidence else "UNKNOWN"
        flags.append("MISSING_ACCEPTANCE_EVIDENCE")
        flags.append("no_evidence_neq_complete")
    elif status == "VERIFIED_COMPLETION" and missing:
        flags.append("MISSING_ACCEPTANCE_EVIDENCE")
    blockers_raw = raw.get("blockers") or []
    blockers: list[dict[str, Any]] = []
    if isinstance(blockers_raw, list):
        for blocker in blockers_raw:
            if isinstance(blocker, str) and blocker.strip():
                blockers.append(
                    {
                        "reason": blocker.strip(),
                        "waiting_on": None,
                        "unlock_condition": None,
                    }
                )
            elif isinstance(blocker, dict):
                blockers.append(
                    {
                        "reason": str(blocker.get("reason") or "UNKNOWN"),
                        "waiting_on": blocker.get("waiting_on"),
                        "unlock_condition": blocker.get("unlock_condition"),
                    }
                )
    if status == "BLOCKED" and not blockers:
        blockers.append(
            {
                "reason": "UNKNOWN",
                "waiting_on": None,
                "unlock_condition": None,
            }
        )
        flags.append("blocked_reason_unknown")
    lifecycle = _normalize_lifecycle(
        raw.get("lifecycle"),
        progress=status,
        notes=flags,
    )
    if lifecycle == "CLOSED" and status != "VERIFIED_COMPLETION":
        flags.append("closed_without_verified_completion")
    if lifecycle == "MERGED" and status == "VERIFIED_COMPLETION":
        flags.append("merged_is_not_closed_or_verified")
        status = "IMPLEMENTED"
    if lifecycle == "IMPLEMENTATION_COMPLETE" and status == "VERIFIED_COMPLETION":
        flags.append("implemented_neq_verified")
        status = "IMPLEMENTED"
    return {
        "id": item_id,
        "title": title,
        "status": status,
        "progress": status,
        "lifecycle": lifecycle,
        "milestone": raw.get("milestone"),
        "depends_on": depends_on,
        "evidence": evidence,
        "evidence_present": present,
        "evidence_missing": missing,
        "blockers": blockers,
        "critical_path": False,
        "missing_acceptance_evidence": "MISSING_ACCEPTANCE_EVIDENCE" in flags,
        "missing_dependencies": [],
        "flags": sorted(set(flags)),
        "notes": list(raw.get("notes") or []),
    }


def _annotate_missing_dependencies(items: list[dict[str, Any]]) -> None:
    """Missing depends_on targets are UNKNOWN blockers, not silent drops."""
    known = {item["id"] for item in items}
    for item in items:
        missing = [dep for dep in item["depends_on"] if dep not in known]
        item["missing_dependencies"] = missing
        if not missing:
            continue
        flags = set(item["flags"])
        flags.add("MISSING_DEPENDENCY")
        item["flags"] = sorted(flags)
        item["blockers"].append(
            {
                "reason": "missing dependency id",
                "waiting_on": missing[0],
                "unlock_condition": (
                    f"declare roadmap item {missing[0]} or remove the dependency"
                ),
            }
        )


def _detect_cycle(items: list[dict[str, Any]]) -> bool:
    graph = {item["id"]: list(item["depends_on"]) for item in items}
    visiting: set[str] = set()
    seen: set[str] = set()

    def walk(node: str) -> bool:
        if node in visiting:
            return True
        if node in seen:
            return False
        visiting.add(node)
        for dep in graph.get(node, []):
            if dep in graph and walk(dep):
                return True
        visiting.remove(node)
        seen.add(node)
        return False

    return any(walk(item_id) for item_id in graph)


def _path_has_unfinished(path: list[str], by_id: dict[str, dict[str, Any]]) -> bool:
    return any(
        node in by_id and by_id[node]["status"] not in _DONE_ENOUGH for node in path
    )


def _longest_paths(items: list[dict[str, Any]]) -> list[list[str]]:
    """Longest dependency chains (dep → item) among remaining work.

    Finished-only chains are ignored when any unfinished path exists so a
    completed long branch cannot hide a shorter parallel unlock.
    """
    by_id = {item["id"]: item for item in items}
    memo: dict[str, list[str]] = {}

    def path_for(item_id: str, stack: set[str]) -> list[str]:
        if item_id in memo:
            return memo[item_id]
        if item_id in stack:
            return [item_id]
        item = by_id.get(item_id)
        if item is None:
            return []
        best: list[str] = [item_id]
        for dep in item["depends_on"]:
            if dep not in by_id:
                continue
            candidate = [*path_for(dep, stack | {item_id}), item_id]
            if len(candidate) > len(best) or (
                len(candidate) == len(best) and candidate < best
            ):
                best = candidate
        memo[item_id] = best
        return best

    paths = [path for path in (path_for(item_id, set()) for item_id in sorted(by_id)) if path]
    if not paths:
        return []
    remaining = [path for path in paths if _path_has_unfinished(path, by_id)]
    candidates = remaining if remaining else paths
    longest = max(len(path) for path in candidates)
    return sorted(path for path in candidates if len(path) == longest)


def _you_are_here(
    items: list[dict[str, Any]],
    critical_path: list[str],
    state_lens: dict[str, Any] | None,
) -> dict[str, Any]:
    by_id = {item["id"]: item for item in items}
    for item_id in critical_path:
        item = by_id[item_id]
        if item["status"] not in _DONE_ENOUGH:
            return {
                "item_id": item_id,
                "title": item["title"],
                "status": item["status"],
                "lifecycle": item.get("lifecycle"),
                "reason": "critical_path_head",
                "why": (
                    f"{item_id} is the first unfinished critical-path item "
                    f"(progress={item['status']}, lifecycle={item.get('lifecycle')})"
                ),
                "evidence": list(item["evidence_present"]),
            }
    unfinished = sorted(
        (item for item in items if item["status"] not in _DONE_ENOUGH),
        key=lambda item: str(item["id"]),
    )
    if unfinished:
        item = unfinished[0]
        return {
            "item_id": item["id"],
            "title": item["title"],
            "status": item["status"],
            "lifecycle": item.get("lifecycle"),
            "reason": "parallel_remaining_work",
            "why": (
                f"selected critical path is exhausted but {item['id']} "
                f"remains unfinished (progress={item['status']})"
            ),
            "evidence": list(item["evidence_present"]),
        }
    if state_lens:
        rollup = state_lens.get("rollup") or state_lens.get("status")
        summary = state_lens.get("summary")
        if rollup or summary:
            status, notes = _normalize_status(rollup)
            return {
                "item_id": None,
                "title": "current state lens",
                "status": status,
                "reason": "state_lens",
                "why": (
                    f"critical path exhausted; state-lens rollup normalized to {status}"
                    + (f" ({summary})" if summary else "")
                ),
                "evidence": ["generated/answers/ans-state"],
                "flags": notes,
            }
    if not items:
        return {
            "item_id": None,
            "title": "UNKNOWN",
            "status": "UNKNOWN",
            "reason": "no_roadmap_items",
            "why": "no roadmap items",
            "evidence": [],
        }
    if all(item["status"] == "VERIFIED_COMPLETION" for item in items):
        return {
            "item_id": None,
            "title": "no remaining critical-path work",
            "status": "VERIFIED_COMPLETION",
            "lifecycle": "CLOSED",
            "reason": "critical_path_exhausted",
            "why": "all roadmap items have verified completion evidence",
            "evidence": [],
        }
    return {
        "item_id": None,
        "title": "no remaining implementation work",
        "status": "IMPLEMENTED",
        "lifecycle": "IMPLEMENTATION_COMPLETE",
        "reason": "critical_path_exhausted",
        "why": "remaining items are IMPLEMENTED; IMPLEMENTED != VERIFIED",
        "evidence": [],
    }


def _downstream(items: list[dict[str, Any]], item_id: str) -> list[str]:
    return sorted(
        item["id"]
        for item in items
        if item_id in item["depends_on"]
    )


def _smallest_transition(item: dict[str, Any]) -> dict[str, str | None]:
    current = str(item.get("lifecycle") or "UNKNOWN")
    nxt = _NEXT_LIFECYCLE.get(current)
    if item.get("status") == "BLOCKED":
        nxt = current
    return {"from": current, "to": nxt}


def _unlock_for_item(items: list[dict[str, Any]], item: dict[str, Any]) -> dict[str, Any]:
    by_id = {row["id"]: row for row in items}
    item_id = str(item["id"])
    missing_deps = [dep for dep in item["depends_on"] if dep not in by_id]
    deps = [by_id[dep] for dep in item["depends_on"] if dep in by_id]
    unknown_deps = [dep["id"] for dep in deps if dep["status"] == "UNKNOWN"]
    deps_ready = all(dep["status"] in _DONE_ENOUGH for dep in deps) and not missing_deps
    releases = _downstream(items, item_id)
    transition = _smallest_transition(item)
    if missing_deps:
        return {
            "item_id": item_id,
            "title": item["title"],
            "status": item["status"],
            "lifecycle": item.get("lifecycle"),
            "waiting_on": missing_deps[0],
            "unlock_condition": (
                f"declare roadmap item {missing_deps[0]} or remove the dependency"
            ),
            "reason": "unknown_prerequisite",
            "why": (
                f"{item_id} depends on undeclared item {missing_deps[0]}; "
                "missing dependency != ready"
            ),
            "smallest_transition": transition,
            "releases": releases,
        }
    if item["status"] == "UNKNOWN":
        return {
            "item_id": item_id,
            "title": item["title"],
            "status": "UNKNOWN",
            "lifecycle": item.get("lifecycle"),
            "waiting_on": item_id,
            "unlock_condition": f"replace UNKNOWN on {item_id} with evidence-backed state",
            "reason": "unknown_prerequisite",
            "why": f"{item_id} is UNKNOWN; no invented next transition",
            "smallest_transition": None,
            "releases": releases,
        }
    if unknown_deps:
        return {
            "item_id": item_id,
            "title": item["title"],
            "status": item["status"],
            "lifecycle": item.get("lifecycle"),
            "waiting_on": unknown_deps[0],
            "unlock_condition": f"resolve UNKNOWN prerequisite {unknown_deps[0]}",
            "reason": "unknown_prerequisite",
            "why": (
                f"{item_id} cannot advance while prerequisite "
                f"{unknown_deps[0]} is UNKNOWN"
            ),
            "smallest_transition": transition,
            "releases": releases,
        }
    if item["status"] == "BLOCKED":
        blocker = item["blockers"][0] if item["blockers"] else {}
        return {
            "item_id": item_id,
            "title": item["title"],
            "status": "BLOCKED",
            "lifecycle": item.get("lifecycle"),
            "waiting_on": blocker.get("waiting_on"),
            "unlock_condition": blocker.get("unlock_condition") or blocker.get("reason"),
            "reason": "blocked",
            "why": (
                f"{item_id} is BLOCKED waiting on "
                f"{blocker.get('waiting_on') or 'UNKNOWN'}; "
                f"unlock={blocker.get('unlock_condition') or blocker.get('reason')}"
            ),
            "smallest_transition": transition,
            "releases": releases,
        }
    if deps_ready:
        return {
            "item_id": item_id,
            "title": item["title"],
            "status": item["status"],
            "lifecycle": item.get("lifecycle"),
            "waiting_on": None,
            "unlock_condition": (
                f"advance {item_id} {transition['from']} → {transition['to']}"
            ),
            "reason": "next_critical_item",
            "why": (
                f"{item_id} is the first unfinished critical-path item; "
                f"the smallest evidence-backed transition is "
                f"{transition['from']} → {transition['to']}"
                + (f", which releases {', '.join(releases)}" if releases else "")
            ),
            "smallest_transition": transition,
            "releases": releases,
        }
    waiting = [dep["id"] for dep in deps if dep["status"] not in _DONE_ENOUGH]
    return {
        "item_id": item_id,
        "title": item["title"],
        "status": item["status"],
        "lifecycle": item.get("lifecycle"),
        "waiting_on": waiting[0] if waiting else None,
        "unlock_condition": f"satisfy dependencies: {', '.join(waiting)}" if waiting else None,
        "reason": "waiting_on_dependency",
        "why": f"{item_id} waits on unfinished dependencies {', '.join(waiting)}",
        "smallest_transition": transition,
        "releases": releases,
    }


def _completion_unlock(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {
            "item_id": None,
            "title": "UNKNOWN",
            "status": "UNKNOWN",
            "lifecycle": "UNKNOWN",
            "waiting_on": None,
            "unlock_condition": None,
            "reason": "no_roadmap_items",
            "why": "no roadmap items",
            "smallest_transition": None,
            "releases": [],
        }
    if all(item["status"] == "VERIFIED_COMPLETION" for item in items):
        return {
            "item_id": None,
            "title": "none",
            "status": "VERIFIED_COMPLETION",
            "lifecycle": "CLOSED",
            "waiting_on": None,
            "unlock_condition": None,
            "reason": "no_remaining_unlock",
            "why": "all roadmap items have verified completion evidence",
            "smallest_transition": None,
            "releases": [],
        }
    return {
        "item_id": None,
        "title": "none",
        "status": "IMPLEMENTED",
        "lifecycle": "IMPLEMENTATION_COMPLETE",
        "waiting_on": None,
        "unlock_condition": None,
        "reason": "remaining_verification",
        "why": (
            "no unfinished implementation; IMPLEMENTED != VERIFIED "
            "and MERGED != CLOSED"
        ),
        "smallest_transition": None,
        "releases": [],
    }


def _next_unlock(items: list[dict[str, Any]], critical_path: list[str]) -> dict[str, Any]:
    by_id = {item["id"]: item for item in items}
    for item_id in critical_path:
        item = by_id[item_id]
        if item["status"] in _DONE_ENOUGH:
            continue
        return _unlock_for_item(items, item)
    unfinished = sorted(
        (item for item in items if item["status"] not in _DONE_ENOUGH),
        key=lambda item: str(item["id"]),
    )
    if unfinished:
        result = _unlock_for_item(items, unfinished[0])
        if result["reason"] == "next_critical_item":
            result["reason"] = "parallel_remaining_work"
            result["why"] = (
                f"critical path exhausted; next remaining unlock is {unfinished[0]['id']}"
            )
        return result
    return _completion_unlock(items)


def build_roadmap_lens(vault: Path, project_id: str) -> dict[str, Any]:
    """Derive a non-authoritative roadmap projection for one project."""
    vault = vault.expanduser().resolve()
    project_id = _safe_project_id(project_id)
    project_dir = vault / "projects" / project_id
    if not project_dir.is_dir():
        raise ProjectRoadmapError(f"unknown project: {project_id}")

    inspected: list[str] = []
    source, source_inspected = _load_roadmap_source(vault, project_id)
    inspected.extend(source_inspected)
    state_lens = _load_lens(vault, "state", project_id)
    decisions_lens = _load_lens(vault, "decisions", project_id)
    unknown_lens = _load_lens(vault, "unknown", project_id)
    for name, payload in (
        ("state", state_lens),
        ("decisions", decisions_lens),
        ("unknown", unknown_lens),
    ):
        if payload is not None:
            inspected.append(f"generated/answers/ans-{name}-{project_id}.json")

    raw_items: list[Any] = []
    raw_milestones: list[Any] = []
    if source:
        raw_items = source.get("items") or source.get("roadmap_items") or []
        raw_milestones = source.get("milestones") or []
    items = [
        _normalize_item(vault, raw, index=index)
        for index, raw in enumerate(raw_items)
        if isinstance(raw, dict)
    ]
    _annotate_missing_dependencies(items)
    cyclic = _detect_cycle(items) if items else False
    critical_path: list[str] = []
    multiple_critical_paths = False
    if items and not cyclic:
        paths = _longest_paths(items)
        multiple_critical_paths = len(paths) > 1
        critical_path = paths[0] if paths else []
        marked = set(critical_path)
        for item in items:
            item["critical_path"] = item["id"] in marked
    elif cyclic:
        for item in items:
            item["critical_path"] = False

    pending_reviews, pending_unreadable = _pending_review_count(vault, project_id)
    conflicts, conflicts_unreadable = _conflict_count(vault, project_id)
    extra_blockers: list[dict[str, Any]] = []
    if pending_reviews:
        extra_blockers.append(
            {
                "reason": "pending human review",
                "waiting_on": "review/pending",
                "unlock_condition": "owner review decide",
            }
        )
    if conflicts:
        extra_blockers.append(
            {
                "reason": "unresolved conflict",
                "waiting_on": "conflicts",
                "unlock_condition": "resolve or accept conflict",
            }
        )
    item_blockers = [blocker for item in items for blocker in item["blockers"]]
    blockers = extra_blockers + item_blockers

    unknowns: list[str] = []
    if source is None:
        unknowns.append("no structured roadmap record")
    if cyclic:
        unknowns.append("cyclic dependencies")
    if any(item["status"] == "UNKNOWN" for item in items):
        unknowns.append("item status UNKNOWN")
    if any(item["missing_acceptance_evidence"] for item in items):
        unknowns.append("missing acceptance evidence")
    if any(item.get("missing_dependencies") for item in items):
        unknowns.append("missing dependency ids")
    if multiple_critical_paths:
        unknowns.append("multiple equal-length critical paths")
    if pending_reviews:
        unknowns.append("pending reviews")
    if pending_unreadable:
        unknowns.append("pending_queue=unreadable")
    if conflicts_unreadable:
        unknowns.append("conflicts_queue=unreadable")
    if conflicts:
        unknowns.append("unresolved conflicts")
    if unknown_lens and unknown_lens.get("status") == "unknown":
        unknowns.append("unknown lens is UNKNOWN")

    you_are_here = _you_are_here(items, critical_path, state_lens)
    next_unlock = _next_unlock(items, critical_path)
    if cyclic:
        you_are_here = {
            "item_id": None,
            "title": "UNKNOWN",
            "status": "UNKNOWN",
            "lifecycle": "UNKNOWN",
            "reason": "cyclic_dependencies",
            "why": "cycle detected; no fabricated critical path",
            "evidence": [],
        }
        next_unlock = {
            "item_id": None,
            "title": "UNKNOWN",
            "status": "UNKNOWN",
            "lifecycle": "UNKNOWN",
            "waiting_on": None,
            "unlock_condition": None,
            "reason": "cyclic_dependencies",
            "why": "cycle detected; next unlock is UNKNOWN",
            "smallest_transition": None,
            "releases": [],
        }

    milestones: list[dict[str, Any]] = []
    if isinstance(raw_milestones, list):
        for raw in raw_milestones:
            if isinstance(raw, str):
                milestones.append({"id": raw, "title": raw, "status": "UNKNOWN"})
            elif isinstance(raw, dict):
                status, notes = _normalize_status(raw.get("status"))
                milestones.append(
                    {
                        "id": str(raw.get("id") or raw.get("title") or "milestone"),
                        "title": str(raw.get("title") or raw.get("id") or "milestone"),
                        "status": status,
                        "notes": notes,
                    }
                )

    lens_status = "derived" if source is not None or items else "unknown"
    summary = you_are_here.get("title") or "UNKNOWN"
    if you_are_here.get("status") == "UNKNOWN" and not items:
        summary = "UNKNOWN — no roadmap evidence"

    return {
        "schema_version": 1,
        "schema": SCHEMA_ID,
        "package": PACKAGE_ID,
        "answer_id": f"ans-roadmap-{project_id}",
        "subject": project_id,
        "field": "roadmap",
        "title": "Where is this project, and what unlocks next?",
        "summary": summary,
        "status": lens_status,
        "authority": "derived-lens",
        "layer": "C",
        "project_id": project_id,
        "you_are_here": you_are_here,
        "milestones": milestones,
        "items": items,
        "blockers": blockers,
        "critical_path": critical_path,
        "next_unlock": next_unlock,
        "unknowns": unknowns,
        "pending_reviews": pending_reviews,
        "unresolved_conflicts": conflicts,
        "inspected_artifacts": inspected,
        "notes": [
            "ROADMAP!=CANONICAL_TRUTH",
            "DERIVED_STATUS!=AUTHORITY",
            "UNKNOWN!=HEALTHY",
            "NO_EVIDENCE!=COMPLETE",
            "MERGED!=CLOSED",
            "IMPLEMENTED!=VERIFIED",
            "percent_complete_is_canonical=false",
            *(
                ["pending-queue-unreadable; no global pending.json fallback"]
                if pending_unreadable
                else []
            ),
            *(
                ["conflicts-queue-unreadable; no unknown-lens fallback"]
                if conflicts_unreadable
                else []
            ),
        ],
        "lifecycle_vocabulary": list(LIFECYCLES),
        "progress_vocabulary": list(ITEM_STATUSES),
        "generated": {"by": GENERATOR_ID},
        "honesty": {
            "roadmap_is_canonical": False,
            "derived_status_is_authority": False,
            "ui_is_canonical": False,
            "unknown_is_valid": True,
            "percent_complete_is_canonical": False,
            "cyclic_dependencies": cyclic,
            "merged_eq_closed": False,
            "implemented_eq_verified": False,
            "multiple_critical_paths": multiple_critical_paths,
            "pending_queue_unreadable": pending_unreadable,
            "conflicts_queue_unreadable": conflicts_unreadable,
            "dogfood_local_vault_executed": False,
            "authentic_pilot": False,
            "atlas_opt_wake_gate": "CLOSED",
        },
    }


def derive_roadmap_lenses(
    vault: Path,
    *,
    project_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Read-only derive. Does not write generated answers or Layer B."""
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise ProjectRoadmapError(f"vault is not a directory: {vault}")
    selected = project_ids if project_ids is not None else _list_projects(vault)
    lenses = [build_roadmap_lens(vault, project_id) for project_id in selected]
    return {
        "schema_version": 1,
        "schema": "atlas.project-roadmap.receipt.v1",
        "package": PACKAGE_ID,
        "status": "ok",
        "vault": vault.as_posix(),
        "projects": list(selected),
        "answers_written": [],
        "lenses": lenses,
        "generated": {"by": GENERATOR_ID},
        "honesty": {
            "roadmap_is_canonical": False,
            "derived_status_is_authority": False,
            "lens_is_authority": False,
            "read_only": True,
            "atlas_opt_wake_gate": "CLOSED",
        },
    }


def materialize_roadmap_lenses(
    vault: Path,
    *,
    project_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Write roadmap answer lenses under ``generated/answers/``."""
    report = derive_roadmap_lenses(vault, project_ids=project_ids)
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


def _format_critical_path(path: list[str], lens: dict[str, Any]) -> str:
    if path:
        return " → ".join(path)
    if (lens.get("honesty") or {}).get("cyclic_dependencies"):
        return "UNKNOWN"
    return "(none)"


def render_roadmap_text(lens: dict[str, Any]) -> str:
    """Human-readable CLI projection. UI/text != canonical."""
    here = lens.get("you_are_here") or {}
    nxt = lens.get("next_unlock") or {}
    path = lens.get("critical_path") or []
    lines = [
        f"atlas roadmap [{lens.get('status', 'unknown')}]  (ROADMAP!=CANONICAL_TRUTH)",
        f"  project:       {lens.get('project_id')}",
        f"  you are here:  {here.get('title')} [{here.get('status')}/"
        f"{here.get('lifecycle')}] ({here.get('why') or here.get('reason')})",
        f"  next unlock:   {nxt.get('title')} [{nxt.get('status')}] "
        f"why={nxt.get('why') or nxt.get('unlock_condition') or '—'}",
        f"  critical path: {_format_critical_path(path, lens)}",
        f"  blockers:      {len(lens.get('blockers') or [])}",
        f"  unknowns:      {', '.join(lens.get('unknowns') or []) or '(none)'}",
    ]
    for item in lens.get("items") or []:
        mark = "*" if item.get("critical_path") else " "
        lines.append(
            f"  {mark} {item.get('id')}: [{item.get('status')}] {item.get('title')}"
        )
    return "\n".join(lines)
