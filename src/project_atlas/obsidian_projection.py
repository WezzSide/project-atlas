"""AS-CODER-ALPHA-OBSIDIAN-001 — living Obsidian project projection.

Writes derived Markdown under ``generated/obsidian/projects/<id>/`` that humans
can open in Obsidian. Atlas owns generated regions; HUMAN regions are preserved
byte-for-byte (AT-011). Not a plugin; not Layer B authority.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

from atlas_contracts.identity import ensure_under_root, safe_relative_component
from project_atlas.logging import get_logger
from project_atlas.project_brief import ProjectBriefError, build_project_brief
from project_atlas.protected_regions import GENERATED_END as _GENERATED_END
from project_atlas.protected_regions import GENERATED_START as _GENERATED_START
from project_atlas.protected_regions import ProtectedRegionError, read_note_text
from project_atlas.protected_regions import merge_protected_regions as _merge_protected_regions
from project_atlas.secrets import scan_text

PACKAGE_ID = "AS-CODER-ALPHA-OBSIDIAN-001"
PACKAGE_ID_R1 = "AS-CODER-ALPHA-OBSIDIAN-R1-PROJECTION-001"
# Generator identity must agree with the module/package identity (PACKAGE_ID,
# used in frontmatter package_id + receipt "package") — not the R1 gap
# work-package id — so downstream tooling keyed on generator id stays stable
# across the R1 gap-fill (finding 4, PR #412 remediation).
GENERATOR_ID = "atlas-coder-alpha-obsidian-001"
OBS_ROOT = Path("generated") / "obsidian" / "projects"


_LOG = get_logger("obsidian_projection")


class ObsidianProjectionError(ValueError):
    """Fail-closed Obsidian living-projection error."""


def _write_atomic(path: Path, content: bytes, *, vault: Path) -> None:
    # Defence in depth against a symlinked/junction path component already
    # planted under the vault (e.g. a stale or tampered
    # ``generated/obsidian/projects/<id>`` directory): re-resolve the target
    # against ``vault`` and fail closed if it would escape the vault root,
    # matching the ``ensure_under_root`` containment check every other Atlas
    # write surface performs immediately before a sensitive write.
    try:
        ensure_under_root(vault, path, label="obsidian projection note")
    except ValueError as exc:
        raise ObsidianProjectionError(str(exc)) from exc
    # F6 guarded the read and `os.replace`; this `mkdir` was the third failure
    # site in the same function and was left outside the boundary, so a blocked
    # or unwritable parent escaped as a raw OSError -- the same defect one step
    # earlier. Reproduced: a plain file where the note's parent must be raises
    # NotADirectoryError straight through `ObsidianProjectionError`.
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ObsidianProjectionError(
            f"unwritable-note-directory:{type(exc).__name__}:{path.parent}"
        ) from exc
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content)
        os.replace(tmp, path)
    except OSError as exc:
        # On Windows a note that "cannot be opened" surfaces HERE rather than
        # at the read: a read-only target still reads fine, and it is
        # ``os.replace`` that fails with WinError 5. Without this the raw
        # OSError escapes `ObsidianProjectionError` entirely, so a caller
        # catching that does not catch this at all
        # -- the same defect this module's read guard fixes, one step later.
        # Found by independent verification of the read-side fix against the
        # Windows CI job, which the Linux-only reproduction could not see.
        raise ObsidianProjectionError(
            f"unwritable-note:{type(exc).__name__}:{path}"
        ) from exc
    finally:
        # Best-effort: if the staging file cannot be removed -- a concurrent
        # permission change on the directory, say -- that OSError would
        # otherwise propagate *out of the finally* and REPLACE the
        # ObsidianProjectionError raised just above, handing the caller a raw
        # exception on the very path this guard exists to cover, and leaving
        # the residue `test_f6_failed_write_leaves_no_tmp_residue` pins as
        # absent. Cleanup failure must not mask the real error; the residue is
        # then a disclosed residual rather than a silent one.
        try:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            # Swallowed so it cannot replace the real error -- but NOT silently.
            # The receipt calls the surviving `.tmp` a "disclosed residual";
            # that was only true in a document until this log made it true at
            # the surface an operator actually sees.
            _LOG.warning(
                "obsidian projection: staging file could not be removed",
                # `extra` MUST nest under "context": both formatters render
                # `record.context` and silently drop anything else. An earlier
                # revision passed the keys at top level, so this warning emitted
                # neither the path nor the error class -- the log existed but
                # carried nothing, which is worse than no log because the
                # receipt claimed the residual was now operator-visible.
                extra={
                    "context": {
                        "path": str(tmp),
                        "error": type(cleanup_exc).__name__,
                    }
                },
            )


def _safe_project_id(project_id: str) -> str:
    try:
        return safe_relative_component(project_id, label="project id")
    except ValueError as exc:
        raise ObsidianProjectionError(str(exc)) from exc


def _list_projects(vault: Path) -> list[str]:
    root = vault / "projects"
    if not root.is_dir():
        return []
    return sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )


def _escape_marker_tokens(text: str) -> str:
    """Neutralize HTML-comment marker syntax in source-derived text.

    Values rendered here (attention items, source-health rows, roadmap
    titles) originate from vault artifacts and source paths, not Atlas
    itself. If such a value happened to contain a literal Atlas
    generated-marker token (``<!-- atlas:generated:... -->``) or a balanced
    ``BEGIN HUMAN``/``END HUMAN`` comment pair, interpolating it raw would
    let ``protected_regions.validate_protected_markers``/``extract_human_regions``
    treat it as real projection structure on the next read — aborting projection
    (extra generated-marker token) or preserving a fake human-edit block
    after its source disappears (balanced HUMAN pair). Escaping the comment
    delimiters renders the text as inert Markdown instead.
    """
    return text.replace("<!--", "&lt;!--").replace("-->", "--&gt;")


def _lens_field(raw: Any, *, default: str = "UNKNOWN") -> str:
    """Render one derived-lens value: missing -> UNKNOWN, never a raw "None"."""
    text = str(raw) if raw not in (None, "") else default
    # AS-SEC-SCAN-OBS-PROJ-SOURCE-JSON-ESC-001: json.loads of compilation
    # outcomes / source-manifest / secret-findings can decode ``\u`` source
    # paths that scan_text misses on raw bytes. Do not echo them into the
    # living note.
    if scan_text(text):
        return default
    return _escape_marker_tokens(text)


def _yaml_scalar(value: str) -> str:
    if value == "" or any(ch in value for ch in (":", "#", "\n", '"', "'")):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _render_attention_section(attention: dict[str, Any] | None) -> list[str]:
    lines = ["", "## Attention (what requires action)"]
    if not attention:
        lines.append("UNKNOWN")
        return lines
    lines.append("ATTENTION LENS != AUTHORITY. Not an objective health score.")
    rollup = attention.get("rollup")
    lines.append(f"rollup={_lens_field(rollup)}")
    raw_care = attention.get("care_about")
    care_positively_inspected = isinstance(raw_care, list)
    care: list[Any] = raw_care if isinstance(raw_care, list) else []
    if not care:
        # A positively-returned empty list (rollup=CLEAR) is confirmed-clear
        # data, not missing/malformed data — do not render UNKNOWN for it.
        if care_positively_inspected and rollup == "CLEAR":
            lines.append("- no attention items")
        else:
            lines.append("- UNKNOWN")
        return lines
    for item in care[:8]:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"- [{_lens_field(item.get('level'))}] {_lens_field(item.get('reason_code'))}: "
            f"{_lens_field(item.get('why_seeing_this'))} → {_lens_field(item.get('what_to_do'))}"
        )
    return lines


def _render_source_health_section(health: dict[str, Any] | None) -> list[str]:
    lines = ["", "## Source health (failures / exclusions)"]
    if not health:
        lines.append("UNKNOWN")
        return lines
    lines.append("SOURCE HEALTH != AUTHORITY. No secret echo.")
    lines.append(f"health_state={_lens_field(health.get('health_state'))}")
    raw_rows = health.get("actionable")
    rows: list[Any] = raw_rows if isinstance(raw_rows, list) else []
    sample = [row for row in rows if isinstance(row, dict)][:6]
    if not sample:
        noise_count = health.get("noise_count")
        source_count = health.get("source_count")
        if isinstance(noise_count, int) and noise_count > 0:
            # noise-only exclusions exist (source_count/noise_count nonzero)
            # even though there are no *actionable* failures — say so
            # explicitly instead of claiming there are no exclusions at all.
            lines.append(
                f"- no actionable failures (scoped report: {source_count or 0} "
                f"excluded source(s), {noise_count} classified as noise-only)"
            )
            raw_noise = health.get("noise")
            noise_rows = (
                [row for row in raw_noise if isinstance(row, dict)][:6]
                if isinstance(raw_noise, list)
                else []
            )
            for row in noise_rows:
                lines.append(
                    f"  - noise: {_lens_field(row.get('source'))} | "
                    f"{_lens_field(row.get('reason_code'))}"
                )
        else:
            lines.append("- no failed/excluded sources in scoped report")
        return lines
    for row in sample:
        next_action = _lens_field(row.get("suggested_next_action"))
        lines.append(
            f"- {_lens_field(row.get('source'))} | {_lens_field(row.get('status'))} | "
            f"{_lens_field(row.get('reason_code'))} | {next_action}"
        )
    return lines


def _render_roadmap_section(roadmap: dict[str, Any] | None) -> list[str]:
    lines = ["", "## Current project position (derived roadmap)"]
    if not roadmap:
        lines.append("UNKNOWN — no roadmap lens")
        return lines
    raw_here = roadmap.get("you_are_here")
    raw_nxt = roadmap.get("next_unlock")
    here: dict[str, Any] = raw_here if isinstance(raw_here, dict) else {}
    nxt: dict[str, Any] = raw_nxt if isinstance(raw_nxt, dict) else {}
    lines.append("ROADMAP!=CANONICAL_TRUTH. DERIVED_STATUS!=AUTHORITY.")
    lines.append(
        f"you_are_here={_lens_field(here.get('title'))} "
        f"[{_lens_field(here.get('status'))}/{_lens_field(here.get('lifecycle'))}]"
    )
    lines.append(
        f"next_unlock={_lens_field(nxt.get('title'))} "
        f"[{_lens_field(nxt.get('status'))}] "
        f"why={_lens_field(nxt.get('why') or nxt.get('unlock_condition'))}"
    )
    return lines


def _render_living_markdown(
    brief: dict[str, Any],
    *,
    attention: dict[str, Any] | None = None,
    source_health: dict[str, Any] | None = None,
    roadmap: dict[str, Any] | None = None,
) -> str:
    project_id = str(brief.get("project_id") or "UNKNOWN")
    next_work = brief.get("suggested_next_work") or []
    evidence = brief.get("evidence_links") or []
    lines = [
        "---",
        "type: AtlasLivingProject",
        f"title: {_yaml_scalar(f'Living knowledge — {project_id}')}",
        "schema_version: 1",
        f"package_id: {PACKAGE_ID}",
        f"project_id: {_yaml_scalar(project_id)}",
        "authority_level: derived",
        "plugin_shipped: false",
        "canonical_writes: false",
        "obsidian_ui_is_authority: false",
        "generated:",
        f"  by: {GENERATOR_ID}",
        "---",
        "",
        f"# Living knowledge — {project_id}",
        "",
        "Derived Obsidian projection from Atlas Truth Core via Coder Alpha brief.",
        "UI!=canonical. MODEL_OUTPUT!=AUTHORITY. UNKNOWN stays UNKNOWN.",
        "Not an Obsidian plugin; Atlas owns generated regions only.",
        "",
        _GENERATED_START,
        "",
        "## Project identity",
        _lens_field(brief.get("project_identity")),
        "",
        "## Purpose",
        _lens_field(brief.get("purpose")),
        "",
        "## Tech stack",
        _lens_field(brief.get("tech_stack")),
        "",
        "## Architecture summary",
        _lens_field(brief.get("architecture_summary")),
        "",
        "## Current state",
        _lens_field(brief.get("current_state")),
        "",
        "## Recent meaningful changes",
        _lens_field(brief.get("recent_meaningful_changes")),
        "",
        "## Important decisions",
        _lens_field(brief.get("important_decisions")),
        "",
        "## Known problems / unknown / conflicting",
        _lens_field(brief.get("unknown_or_conflicting")),
    ]
    lines.extend(_render_roadmap_section(roadmap))
    lines.extend(_render_attention_section(attention))
    lines.extend(_render_source_health_section(source_health))
    lines.extend(
        [
            "",
            "## Suggested next work",
        ]
    )
    if isinstance(next_work, list) and next_work:
        safe_next = [
            item
            for item in next_work
            if isinstance(item, str) and item.strip() and not scan_text(item)
        ]
        if safe_next:
            lines.extend(f"- {item}" for item in safe_next)
        else:
            lines.append("- UNKNOWN")
    else:
        lines.append("- UNKNOWN")
    lines.extend(["", "## Evidence links"])
    if isinstance(evidence, list) and evidence:
        safe_evidence = [
            item
            for item in evidence[:40]
            if isinstance(item, str) and item.strip() and not scan_text(item)
        ]
        if safe_evidence:
            lines.extend(f"- `{item}`" for item in safe_evidence)
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
            "- attention_is_health_score: false",
            "- obsidian_ui_is_authority: false",
            "- plugin_shipped: false",
            "",
            _GENERATED_END,
            "",
            "<!-- BEGIN HUMAN: notes -->",
            "<!-- END HUMAN: notes -->",
            "",
        ]
    )
    return "\n".join(lines)


def project_note_path(vault: Path, project_id: str) -> Path:
    project_id = _safe_project_id(project_id)
    return vault / OBS_ROOT / project_id / "project-living.md"


def materialize_obsidian_projection(
    vault: Path,
    *,
    project_id: str | None = None,
    refresh_brief: bool = True,
) -> dict[str, Any]:
    """Materialize living Obsidian Markdown for one or all vault projects."""
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise ObsidianProjectionError(f"vault is not a directory: {vault}")
    projects = [_safe_project_id(project_id)] if project_id else _list_projects(vault)
    if not projects:
        raise ObsidianProjectionError("no projects found to project")

    written: list[str] = []
    for pid in projects:
        try:
            brief = build_project_brief(vault, pid, refresh=refresh_brief)
        except ProjectBriefError as exc:
            raise ObsidianProjectionError(str(exc)) from exc
        attention: dict[str, Any] | None = None
        source_health: dict[str, Any] | None = None
        roadmap: dict[str, Any] | None = None
        # Narrow to each lens's own domain error type so a real programmer
        # bug (ImportError, AttributeError, etc.) surfaces instead of
        # silently degrading the projection to UNKNOWN (finding 5). The
        # import itself happens outside the suppress block so a broken
        # import is never swallowed.
        from project_atlas.attention_hygiene import AttentionHygieneError, classify_attention

        with contextlib.suppress(AttentionHygieneError):
            attention = classify_attention(vault, pid)

        from project_atlas.source_health import SourceHealthError, explain_source_health

        with contextlib.suppress(SourceHealthError):
            source_health = explain_source_health(vault, pid)

        from project_atlas.project_roadmap import ProjectRoadmapError, build_roadmap_lens

        with contextlib.suppress(ProjectRoadmapError):
            roadmap = build_roadmap_lens(vault, pid)
        rendered = _render_living_markdown(
            brief,
            attention=attention,
            source_health=source_health,
            roadmap=roadmap,
        )
        path = project_note_path(vault, pid)
        # Re-check containment before reading too: a symlinked/junction
        # ``generated/obsidian/projects/<id>`` directory could otherwise
        # let a stale/tampered path pull arbitrary outside content into
        # ``existing`` before the write-side check below ever runs.
        try:
            ensure_under_root(vault, path, label="obsidian projection note")
        except ValueError as exc:
            raise ObsidianProjectionError(str(exc)) from exc
        try:
            existing = read_note_text(path) if path.is_file() else None
        except (OSError, UnicodeError) as exc:
            # An unreadable or non-UTF-8 prior note is an operator-facing
            # condition, not an internal fault: it must name the note and stay
            # inside this module's error boundary. `obsidian_capture_note`
            # already does exactly this; the projection writers did not, so a
            # raw UnicodeDecodeError or PermissionError escaped to the caller.
            raise ObsidianProjectionError(
                f"unreadable-existing-note:{type(exc).__name__}:{path}"
            ) from exc
        try:
            merged = _merge_protected_regions(
                existing=existing,
                rendered=rendered,
                path=path.relative_to(vault).as_posix(),
            )
        except ProtectedRegionError as exc:
            raise ObsidianProjectionError(str(exc)) from exc
        _write_atomic(path, merged.encode("utf-8"), vault=vault)
        written.append(path.relative_to(vault).as_posix())

    receipt = {
        "schema_version": 1,
        "schema": "atlas.coder-alpha.obsidian-projection.v1",
        "package": PACKAGE_ID,
        "status": "ok",
        "notes_written": written,
        "plugin_shipped": False,
        "canonical_writes": False,
        "honesty": {
            "authentic_pilot": False,
            "atlas_opt_wake_gate": "CLOSED",
            "lens_is_authority": False,
            "roadmap_is_canonical": False,
            "attention_is_health_score": False,
            "obsidian_ui_is_authority": False,
            "canonical_knowledge_remains_atlas": True,
        },
        "r1_package": PACKAGE_ID_R1,
        "generated": {"by": GENERATOR_ID},
    }
    receipt_path = vault / "generated" / "ops" / "obsidian" / "living-projection-receipt.json"
    _write_atomic(
        receipt_path,
        (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        vault=vault,
    )
    receipt["receipt_path"] = receipt_path.relative_to(vault).as_posix()
    return receipt
