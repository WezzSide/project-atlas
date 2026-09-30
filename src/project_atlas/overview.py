"""AS-CODER-ALPHA-OVERVIEW-001 — Project Overview derived lens from Core.

Builds a non-authoritative ``generated/answers/ans-overview-<project>.json``
lens from vault Layer B project notes + Layer A imported evidence so web
``/v1/knowledge`` and Ask-live are not empty after ``atlas connect``.

Honesty:
- lens ≠ Layer B authority
- UI ≠ canonical truth
- UNKNOWN stays UNKNOWN when evidence is insufficient
- no wall-clock timestamps (NFR-001 / ADR-001)
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from project_atlas.dogfood_compiler_coverage import attach_compiler_coverage
from project_atlas.inventory_drift import attach_source_drift

PACKAGE_ID = "AS-CODER-ALPHA-OVERVIEW-001"
GENERATOR_ID = "atlas-coder-alpha-overview-001"
ANSWERS_RELATIVE = Path("generated") / "answers"
_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_JSON_FENCE_RE = re.compile(
    r"## Semantic record\s*```json\s*(.*?)\s*```",
    re.DOTALL | re.IGNORECASE,
)


class OverviewError(ValueError):
    """Fail-closed overview error."""


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
        path.name for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")
    )


def _parse_semantic_record(project_md: str) -> dict[str, Any] | None:
    match = _JSON_FENCE_RE.search(project_md)
    if not match:
        return None
    try:
        raw = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return raw if isinstance(raw, dict) else None


def _first_prose_blurb(markdown: str, *, max_chars: int = 480) -> str | None:
    """Extract title + first paragraph from imported Markdown evidence."""
    body = _FRONTMATTER_RE.sub("", markdown, count=1).strip()
    if not body:
        return None
    lines = [line.rstrip() for line in body.splitlines()]
    title = None
    paragraphs: list[str] = []
    buf: list[str] = []
    for line in lines:
        stripped = line.strip()
        if title is None and stripped.startswith("#"):
            title = stripped.lstrip("#").strip() or None
            continue
        if stripped.startswith("#"):
            break
        if not stripped:
            if buf:
                paragraphs.append(" ".join(buf).strip())
                buf = []
            if paragraphs:
                break
            continue
        if stripped.startswith("```") or stripped.startswith("---"):
            continue
        buf.append(stripped)
    if buf:
        paragraphs.append(" ".join(buf).strip())
    parts = [part for part in (title, paragraphs[0] if paragraphs else None) if part]
    if not parts:
        return None
    text = " — ".join(parts) if len(parts) == 2 else parts[0]
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def _source_authority_rank(path: str) -> tuple[int, int, str]:
    """Prefer project-root identity docs over nested package READMEs (D-038).

    Lower tuple sorts first. Root README/AGENTS/plan beat deps/apps nests so
    monorepo dogfood does not describe Atlas as a nested research package.
    """
    posix = path.replace("\\", "/").lstrip("./")
    name = Path(posix).name.lower()
    depth = posix.count("/")
    if posix.lower() in {"readme.md", "readme.txt", "readme"}:
        return (0, 0, posix)
    if posix.lower() in {"agents.md", "claude.md"}:
        return (0, 1, posix)
    if posix.lower() in {"docs/plan.md", "docs/prp.md", "docs/product/coder-alpha-north-star.md"}:
        return (0, 2, posix)
    if name in {"readme.md", "readme.txt", "readme"}:
        # Deprioritize vendored / nested package READMEs.
        if posix.startswith(("deps/", "node_modules/", "apps/", "integrations/")):
            return (3, depth, posix)
        return (1, depth, posix)
    if name in {"agents.md", "plan.md", "prp.md"}:
        return (2, depth, posix)
    return (9, depth, posix)


def _source_id_owners(vault: Path) -> dict[str, str]:
    """Map connect-manifest source_id → likely_project when declared."""
    path = vault / "generated" / "ops" / "connect-manifest.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    rows = raw.get("sources")
    if not isinstance(rows, list):
        return {}
    owners: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_id = str(row.get("source_id") or "").strip()
        likely = str(row.get("likely_project") or "").strip()
        if source_id and likely:
            owners[source_id] = likely
    return owners


def _readme_blurb(
    vault: Path,
    semantic: dict[str, Any] | None,
    project_id: str,
) -> tuple[str | None, list[str]]:
    inspected: list[str] = []
    if not isinstance(semantic, dict):
        return None, inspected
    sources = semantic.get("sources")
    if not isinstance(sources, list):
        return None, inspected
    owners = _source_id_owners(vault)
    candidates: list[tuple[tuple[int, int, str], str, str]] = []
    for row in sources:
        if not isinstance(row, dict):
            continue
        path = str(row.get("path") or "")
        source_id = str(row.get("source_id") or "")
        if not path or not source_id:
            continue
        owner = owners.get(source_id)
        if owner and owner != project_id:
            continue
        rank = _source_authority_rank(path)
        if rank[0] >= 9:
            continue
        candidates.append((rank, path, source_id))
    for _rank, path, source_id in sorted(candidates, key=lambda item: item[0]):
        imported = vault / "sources" / "imported-documents" / f"{source_id}.md"
        inspected.append(imported.relative_to(vault).as_posix())
        if not imported.is_file():
            continue
        try:
            text = imported.read_text(encoding="utf-8")
        except OSError:
            continue
        blurb = _first_prose_blurb(text)
        if blurb:
            inspected.append(f"selected:{path}")
            return blurb, inspected
    return None, inspected


def _coverage_summary(semantic: dict[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(semantic, dict):
        return out
    coverage = semantic.get("coverage")
    if not isinstance(coverage, list):
        return out
    for row in coverage:
        if not isinstance(row, dict):
            continue
        category = row.get("category")
        state = row.get("state")
        if isinstance(category, str) and isinstance(state, str):
            out[category] = state
    return out


def build_overview_lens(vault: Path, project_id: str) -> dict[str, Any]:
    """Build one derived overview lens for ``project_id`` (no disk writes)."""
    if not project_id or project_id in {".", ".."} or "/" in project_id or "\\" in project_id:
        raise OverviewError(f"unsafe project id: {project_id!r}")
    project_note = vault / "projects" / project_id / "project.md"
    inspected = [f"projects/{project_id}/project.md"]
    if not project_note.is_file():
        return attach_compiler_coverage(
            attach_source_drift(
                {
                    "schema_version": 1,
                    "schema": "atlas.coder-alpha.overview-lens.v1",
                    "package": PACKAGE_ID,
                    "answer_id": f"ans-overview-{project_id}",
                    "subject": project_id,
                    "field": "overview",
                    "title": "What is this project?",
                    "summary": None,
                    "value": None,
                    "status": "unknown",
                    "authority": "derived-lens",
                    "layer": "C",
                    "project_id": project_id,
                    "coverage": {},
                    "inspected_artifacts": inspected,
                    "notes": [
                        "project.md missing; UNKNOWN (not invented)",
                        "lens≠Layer-B-authority",
                        "UI≠canonical",
                    ],
                    "generated": {"by": GENERATOR_ID},
                },
                vault,
                project_id,
            ),
            vault,
            project_id,
        )

    try:
        note_text = project_note.read_text(encoding="utf-8")
    except OSError as exc:
        raise OverviewError(f"unreadable project note: {project_note}: {exc}") from exc

    semantic = _parse_semantic_record(note_text)
    blurb, readme_inspected = _readme_blurb(vault, semantic, project_id)
    inspected.extend(readme_inspected)
    coverage = _coverage_summary(semantic)
    # D-044 A3: coverage PRESENT must not contradict architecture lens UNKNOWN.
    arch_path = vault / "generated" / "answers" / f"ans-architecture-{project_id}.json"
    if arch_path.is_file() and coverage.get("architecture") in {"present", "partial"}:
        try:
            arch_payload = json.loads(arch_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            arch_payload = None
        if isinstance(arch_payload, dict) and arch_payload.get("status") == "unknown":
            coverage["architecture"] = "absent"
            inspected.append(arch_path.relative_to(vault).as_posix())
    present = sorted(key for key, state in coverage.items() if state in {"present", "partial"})
    absent = sorted(key for key, state in coverage.items() if state == "absent")

    if blurb:
        status = "derived"
        summary = blurb
        value = blurb
        notes = [
            "Derived from Layer A README evidence + Layer B project note metadata",
            "lens≠Layer-B-authority",
            "UI≠canonical",
            "MODEL_OUTPUT≠AUTHORITY",
        ]
    else:
        status = "unknown"
        summary = None
        value = None
        notes = [
            "No README prose evidence found; overview value is UNKNOWN",
            "lens≠Layer-B-authority",
            "UI≠canonical",
        ]
    if present:
        notes.append("coverage_present=" + ",".join(present[:12]))
    if absent:
        notes.append("coverage_absent_sample=" + ",".join(absent[:8]))

    return attach_compiler_coverage(
        attach_source_drift(
            {
                "schema_version": 1,
                "schema": "atlas.coder-alpha.overview-lens.v1",
                "package": PACKAGE_ID,
                "answer_id": f"ans-overview-{project_id}",
                "subject": project_id,
                "field": "overview",
                "title": "What is this project?",
                "summary": summary,
                "value": value,
                "status": status,
                "authority": "derived-lens",
                "layer": "C",
                "project_id": project_id,
                "coverage": coverage,
                "inspected_artifacts": inspected,
                "notes": notes,
                "generated": {"by": GENERATOR_ID},
            },
            vault,
            project_id,
        ),
        vault,
        project_id,
    )


def materialize_overview_lenses(
    vault: Path,
    *,
    project_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Write overview answer lenses under ``generated/answers/`` for vault projects."""
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise OverviewError(f"vault is not a directory: {vault}")
    selected = project_ids if project_ids is not None else _list_projects(vault)
    written: list[str] = []
    lenses: list[dict[str, Any]] = []
    for project_id in selected:
        lens = build_overview_lens(vault, project_id)
        lenses.append(lens)
        answer_id = str(lens["answer_id"])
        path = vault / ANSWERS_RELATIVE / f"{answer_id}.json"
        payload = (json.dumps(lens, indent=2, sort_keys=True) + "\n").encode("utf-8")
        _write_atomic(path, payload)
        written.append(path.relative_to(vault).as_posix())

    return {
        "schema_version": 1,
        "schema": "atlas.coder-alpha.overview-receipt.v1",
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
