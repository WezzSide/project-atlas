"""AS-CODER-ALPHA-BRIEF-001 — Unified project intelligence brief.

Assembles derived Coder Alpha answer lenses into one honest briefing for
humans and agents after ``atlas connect``. Does not invent missing fields;
UNKNOWN remains UNKNOWN.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

from project_atlas.inventory_drift import evaluate_connect_inventory_drift
from project_atlas.overview import materialize_overview_lenses
from project_atlas.project_architecture import (
    _manifest_source_rows,
    materialize_architecture_lenses,
)
from project_atlas.project_changed import ProjectChangedError, materialize_changed_lenses
from project_atlas.project_decisions import materialize_decisions_lenses
from project_atlas.project_state import materialize_state_lenses
from project_atlas.project_unknown import materialize_unknown_lenses
from project_atlas.secrets import scan_text
from project_atlas.web_api.knowledge import list_knowledge_answers

PACKAGE_ID = "AS-CODER-ALPHA-BRIEF-001"
GENERATOR_ID = "atlas-coder-alpha-brief-001"
ANSWERS_RELATIVE = Path("generated") / "answers"
BRIEF_RELATIVE = Path("generated") / "ops" / "project-brief.json"
_UNVERIFIED_REASON_CODES = frozenset(
    {
        "SOURCE_ROOT_UNVERIFIED",
        "MANIFEST_ABSENT",
        "NO_ACTIVE_SOURCES",
        "INVALID_SOURCE_ROOT",
        "PROJECT_REQUIRED",
    }
)
_STALE_NEXT_LINE = (
    "NOT CURRENT — live source evidence is stale; reconnect before treating "
    "this recommendation as current"
)
_UNVERIFIED_NEXT_LINE = (
    "UNVERIFIED — live source inventory could not be verified; uncertainty preserved"
)


class ProjectBriefError(ValueError):
    """Fail-closed project brief error."""


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


def _load_answer(vault: Path, answer_id: str) -> dict[str, Any] | None:
    path = vault / ANSWERS_RELATIVE / f"{answer_id}.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    # AS-SEC-SCAN-BRIEF-ANSWERID-JSON-ESC-001: json.loads of answer JSON can
    # decode ``\u`` answer_id / inspected_artifacts escapes that scan_text
    # misses on raw bytes.
    loaded_id = raw.get("answer_id")
    if isinstance(loaded_id, str) and scan_text(loaded_id):
        raw["answer_id"] = None
    artifacts = raw.get("inspected_artifacts")
    if isinstance(artifacts, list):
        raw["inspected_artifacts"] = [
            item
            for item in artifacts
            if not (isinstance(item, str) and scan_text(item))
        ]
    return raw


def _next_honesty_flag(next_lens: dict[str, Any] | None, key: str) -> bool:
    if not isinstance(next_lens, dict):
        return False
    honesty = next_lens.get("honesty")
    return isinstance(honesty, dict) and bool(honesty.get(key))


def _brief_live_honesty(
    vault: Path,
    project_id: str,
    next_lens: dict[str, Any] | None,
) -> dict[str, Any]:
    """Observe stale/unverified inventory without cloning hash helpers.

    NEXT.answer_evidence_stale / NEXT.live_source_unverified are copied when
    present. On current main those keys are absent, so brief evaluates
    ``evaluate_connect_inventory_drift`` itself (AS-CODER-ALPHA-HONESTY-TAIL-001
    / #382). project_next.py is left untouched because that primitive already
    exposes SOURCE_ROOT_UNVERIFIED / MANIFEST_ABSENT / NO_ACTIVE_SOURCES.
    """
    live = evaluate_connect_inventory_drift(vault, project_id)
    status = str(live.get("status") or "UNKNOWN")
    reason_code = str(live.get("reason_code") or "")
    answer_evidence_stale = _next_honesty_flag(
        next_lens, "answer_evidence_stale"
    ) or status == "STALE"
    live_source_unverified = _next_honesty_flag(
        next_lens, "live_source_unverified"
    ) or (status == "UNKNOWN" and reason_code in _UNVERIFIED_REASON_CODES)
    return {
        "answer_evidence_stale": answer_evidence_stale,
        "live_source_unverified": live_source_unverified,
        "source_drift": {
            "status": status,
            "reason": live.get("reason"),
            "reason_code": live.get("reason_code"),
            "changed_paths": [
                item for item in (live.get("changed_paths") or []) if isinstance(item, str)
            ][:20],
            "package": live.get("package"),
        },
    }


def _qualify_next_work(
    next_work: list[str],
    *,
    answer_evidence_stale: bool,
    live_source_unverified: bool,
) -> list[str]:
    """Keep recommendations, but never present stale/unverified work as current."""
    qualified = list(next_work)
    if answer_evidence_stale and _STALE_NEXT_LINE not in qualified:
        qualified.insert(0, _STALE_NEXT_LINE)
    if live_source_unverified and _UNVERIFIED_NEXT_LINE not in qualified:
        qualified.insert(0, _UNVERIFIED_NEXT_LINE)
    return qualified


def _field(lens: dict[str, Any] | None, key: str = "summary") -> str | None:
    if not lens:
        return None
    value = lens.get(key)
    if isinstance(value, str) and value.strip():
        # Same persist class as answer_id: decoded ``\u`` summary must not
        # become purpose / stack / state prose.
        if scan_text(value):
            return None
        return value
    return None


def _stack_from_pyproject(vault: Path, project_id: str) -> str | None:
    """Derive a concrete stack label from root ``pyproject.toml`` when present."""
    for row in _manifest_source_rows(vault, project_id):
        path = str(row.get("path") or "").replace("\\", "/")
        source_id = str(row.get("source_id") or "")
        if path.lower() != "pyproject.toml" or not source_id:
            continue
        imported = vault / "sources" / "imported-documents" / f"{source_id}.md"
        # toml may be stored as imported document text
        if not imported.is_file():
            # some ingests keep original extension via adjacent path; try basename
            alt = vault / "sources" / "imported-documents" / f"{source_id}.toml"
            imported = alt if alt.is_file() else imported
        if not imported.is_file():
            continue
        try:
            text = imported.read_text(encoding="utf-8")
        except OSError:
            continue
        requires = None
        deps: list[str] = []
        in_deps = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("requires-python"):
                requires = stripped.split("=", 1)[-1].strip().strip('"').strip("'")
            if stripped.startswith("dependencies"):
                in_deps = True
                continue
            if in_deps:
                if stripped.startswith("["):
                    in_deps = False
                    continue
                if stripped.startswith("]"):
                    in_deps = False
                    continue
                token = stripped.strip(",").strip('"').strip("'")
                if token and not token.startswith("#"):
                    name = token.split(">=")[0].split("==")[0].split("[")[0].strip()
                    if name:
                        deps.append(name)
        parts: list[str] = []
        if requires:
            parts.append(f"Python {requires}")
        if deps:
            parts.append(", ".join(deps[:6]))
        if parts:
            return " · ".join(parts)[:240]
    return None


def _extract_stack_blurb(vault: Path, project_id: str) -> str | None:
    """Best-effort stack from README ## Stack, else root pyproject.toml."""
    readme_rows: list[tuple[int, dict[str, Any]]] = []
    for row in _manifest_source_rows(vault, project_id):
        path = str(row.get("path") or "").replace("\\", "/")
        if Path(path).name.lower() not in {"readme.md", "readme.txt", "readme"}:
            continue
        depth = path.count("/")
        # Prefer root README over nested package READMEs.
        priority = 0 if depth == 0 else 1 + depth
        if path.startswith(("deps/", "apps/", "integrations/")):
            priority += 10
        readme_rows.append((priority, row))
    for _priority, row in sorted(readme_rows, key=lambda item: item[0]):
        source_id = str(row.get("source_id") or "")
        if not source_id:
            continue
        imported = vault / "sources" / "imported-documents" / f"{source_id}.md"
        if not imported.is_file():
            continue
        try:
            text = imported.read_text(encoding="utf-8")
        except OSError:
            continue
        lines = text.splitlines()
        capture = False
        buf: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.lower().startswith("## stack"):
                capture = True
                continue
            if capture and stripped.startswith("#"):
                break
            if capture and stripped:
                buf.append(stripped)
        if buf:
            return " ".join(buf)[:240]
    return _stack_from_pyproject(vault, project_id)


def build_project_brief(
    vault: Path,
    project_id: str,
    *,
    refresh: bool = True,
) -> dict[str, Any]:
    """Build one project brief dict (optionally refreshing lenses first)."""
    if not project_id or project_id in {".", ".."} or "/" in project_id or "\\" in project_id:
        raise ProjectBriefError(f"unsafe project id: {project_id!r}")
    vault = vault.expanduser().resolve()
    if refresh:
        materialize_overview_lenses(vault, project_ids=[project_id])
        materialize_architecture_lenses(vault, project_ids=[project_id])
        materialize_state_lenses(vault, project_ids=[project_id])
        # changed without manifest uses existing inventory pair
        with contextlib.suppress(ProjectChangedError, OSError, ValueError):
            materialize_changed_lenses(vault, project_ids=[project_id])
        materialize_decisions_lenses(vault, project_ids=[project_id])
        materialize_unknown_lenses(vault, project_ids=[project_id])

    overview = _load_answer(vault, f"ans-overview-{project_id}")
    architecture = _load_answer(vault, f"ans-architecture-{project_id}")
    state = _load_answer(vault, f"ans-state-{project_id}")
    changed = _load_answer(vault, f"ans-changed-{project_id}")
    decisions = _load_answer(vault, f"ans-decisions-{project_id}")
    unknown = _load_answer(vault, f"ans-unknown-{project_id}")

    unknown_signals = (unknown or {}).get("signals") if isinstance(unknown, dict) else {}
    coverage_absent = []
    if isinstance(unknown_signals, dict):
        raw_absent = unknown_signals.get("coverage_absent")
        if isinstance(raw_absent, list):
            coverage_absent = [
                str(item) for item in raw_absent if not scan_text(str(item))
            ]

    # Suggested next work: prefer the composed What Next lens, then honesty fallbacks.
    next_lens: dict[str, Any] | None = None
    with contextlib.suppress(Exception):
        from project_atlas.project_next import build_next_lens

        next_lens = build_next_lens(vault, project_id)
    next_work: list[str] = []
    if next_lens is not None:
        raw_next = next_lens.get("suggested_next_work")
        if isinstance(raw_next, list):
            next_work = [str(item) for item in raw_next if str(item).strip()]
    if not next_work:
        if isinstance(unknown_signals, dict):
            if int(unknown_signals.get("unresolved_conflicts") or 0) > 0:
                next_work.append("Resolve unresolved conflicts in review/conflicts")
            if int(unknown_signals.get("pending_reviews") or 0) > 0:
                next_work.append("Triage pending human reviews in review/pending")
        if coverage_absent:
            next_work.append(
                "Add source evidence for absent coverage: " + ", ".join(coverage_absent[:6])
            )
        if (decisions or {}).get("status") == "unknown":
            next_work.append("Capture important decisions in docs/DECISIONS.md or ADRs")
        if (changed or {}).get("rollup") == "baseline":
            next_work.append("Re-run atlas connect after edits to populate What Changed")
    if not next_work:
        next_work.append("UNKNOWN - no concrete next-work signal derived from Truth Core")

    live_honesty = _brief_live_honesty(vault, project_id, next_lens)
    next_work = _qualify_next_work(
        next_work,
        answer_evidence_stale=bool(live_honesty["answer_evidence_stale"]),
        live_source_unverified=bool(live_honesty["live_source_unverified"]),
    )

    evidence = []
    for lens in (overview, state, changed, decisions, unknown):
        if not lens:
            continue
        for item in lens.get("inspected_artifacts") or []:
            if isinstance(item, str) and item not in evidence and not scan_text(item):
                evidence.append(item)

    tech_stack = _extract_stack_blurb(vault, project_id)
    architecture_evidence = (architecture or {}).get("evidence")
    if isinstance(architecture_evidence, list):
        for item in architecture_evidence:
            if isinstance(item, str) and item not in evidence and not scan_text(item):
                evidence.append(item)

    brief = {
        "schema_version": 1,
        "schema": "atlas.coder-alpha.project-brief.v1",
        "package": PACKAGE_ID,
        "project_id": project_id,
        "project_identity": project_id,
        "purpose": _field(overview) or "UNKNOWN",
        "tech_stack": tech_stack or "UNKNOWN",
        "architecture_summary": _field(architecture) or "UNKNOWN",
        "current_state": _field(state) or "UNKNOWN",
        "recent_meaningful_changes": _field(changed) or "UNKNOWN",
        "important_decisions": _field(decisions) or "UNKNOWN",
        "known_problems": _field(unknown) or "UNKNOWN",
        "unknown_or_conflicting": _field(unknown) or "UNKNOWN",
        "suggested_next_work": next_work,
        "evidence_links": evidence[:40],
        "lenses": {
            "overview": (overview or {}).get("answer_id"),
            "architecture": (architecture or {}).get("answer_id"),
            "state": (state or {}).get("answer_id"),
            "changed": (changed or {}).get("answer_id"),
            "decisions": (decisions or {}).get("answer_id"),
            "unknown": (unknown or {}).get("answer_id"),
            "next": (next_lens or {}).get("answer_id"),
        },
        "knowledge_answers": [
            row["answer_id"]
            for row in list_knowledge_answers(vault)
            if row.get("subject") == project_id
            and not (
                isinstance(row.get("answer_id"), str) and scan_text(str(row["answer_id"]))
            )
        ],
        "generated": {"by": GENERATOR_ID},
        "source_drift": live_honesty["source_drift"],
        "honesty": {
            "authentic_pilot": False,
            "release_certified": False,
            "atlas_opt_wake_gate": "CLOSED",
            "lens_is_authority": False,
            "brief_is_authority": False,
            "fabricated_fields": False,
            "unknown_is_valid": True,
            "unknown_is_healthy": False,
            "stale_is_current": False,
            "answer_evidence_stale": bool(live_honesty["answer_evidence_stale"]),
            "live_source_unverified": bool(live_honesty["live_source_unverified"]),
        },
        "notes": [
            "Unified Coder Alpha brief over derived lenses",
            "UI!=canonical",
            "MODEL_OUTPUT!=AUTHORITY",
            "UNKNOWN!=healthy",
            "BRIEF!=AUTHORITY",
        ],
    }
    # stale_is_current remains False always. Observed stale/unverified
    # warnings are rendered only when that state is actually proven.
    if live_honesty["answer_evidence_stale"]:
        brief["notes"].append(
            "STALE NEXT EVIDENCE != CURRENT BRIEF RECOMMENDATION"
        )
    if live_honesty["live_source_unverified"]:
        brief["notes"].append("LIVE SOURCE UNVERIFIED; uncertainty preserved")
    return brief


def materialize_project_briefs(
    vault: Path,
    *,
    project_ids: list[str] | None = None,
    refresh: bool = True,
) -> dict[str, Any]:
    """Write per-project briefs + aggregate ops receipt."""
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise ProjectBriefError(f"vault is not a directory: {vault}")
    selected = project_ids if project_ids is not None else _list_projects(vault)
    briefs: list[dict[str, Any]] = []
    written: list[str] = []
    for project_id in selected:
        brief = build_project_brief(vault, project_id, refresh=refresh)
        briefs.append(brief)
        path = vault / "generated" / "ops" / f"project-brief-{project_id}.json"
        _write_atomic(
            path,
            (json.dumps(brief, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
        written.append(path.relative_to(vault).as_posix())
    receipt = {
        "schema_version": 1,
        "schema": "atlas.coder-alpha.brief-receipt.v1",
        "package": PACKAGE_ID,
        "status": "ok",
        "vault": vault.as_posix(),
        "projects": list(selected),
        "briefs_written": written,
        "briefs": briefs,
        "generated": {"by": GENERATOR_ID},
        "honesty": {
            "authentic_pilot": False,
            "release_certified": False,
            "atlas_opt_wake_gate": "CLOSED",
            "lens_is_authority": False,
        },
    }
    _write_atomic(
        vault / BRIEF_RELATIVE,
        (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return receipt
