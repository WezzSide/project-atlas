"""AS-CODER-ALPHA-DOGFOOD-COMPILER-COVERAGE-001 — imported-source recall.

Derives tech_stack / purpose / important_decisions from Layer A imported
evidence only (pyproject.toml, AGENTS, ADR/DECISIONS). Does not invent
authority. README-only or unsupported evidence stays UNKNOWN.

This module is compiler-coverage only. It does not rank Next, compose
briefs, extract architecture slots, or classify state.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any, Final

from atlas_contracts.identity import safe_relative_component
from atlas_contracts.paths import resolve_under_root
from project_atlas.secrets import redact_text, scan_text

PACKAGE_ID: Final[str] = "AS-CODER-ALPHA-DOGFOOD-COMPILER-COVERAGE-001"
GENERATOR_ID: Final[str] = "atlas-coder-alpha-dogfood-compiler-coverage-001"
CONNECT_MANIFEST: Final[Path] = Path("generated") / "ops" / "connect-manifest.json"
IMPORTED_RELATIVE: Final[Path] = Path("sources") / "imported-documents"

UNKNOWN: Final[str] = "UNKNOWN"
_UNKNOWN_PROJECT: Final[str] = "unknown-project"
_SUMMARY_MAX: Final[int] = 480
_STACK_MAX: Final[int] = 240
_DECISION_MAX: Final[int] = 8

_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_JSON_FENCE_RE = re.compile(
    r"## Semantic record\s*```json\s*(.*?)\s*```",
    re.DOTALL | re.IGNORECASE,
)
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
_ADR_TITLE_RE = re.compile(r"\bADR[- ]?\d+\b", re.IGNORECASE)
_TOML_FENCE_RE = re.compile(r"```toml\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)

_IMPORTED_SUFFIXES: Final[tuple[str, ...]] = (
    ".md",
    ".toml",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
)
_SECRET_BASENAMES: Final[frozenset[str]] = frozenset(
    {
        ".env",
        "credentials.json",
        "secrets.pem",
        "secrets.json",
        "secrets.toml",
        "id_rsa",
        "id_ed25519",
    }
)
_NESTED_PREFIXES: Final[tuple[str, ...]] = (
    "deps/",
    "node_modules/",
    "apps/",
    "integrations/",
    "fixtures/",
    "docs/demo/",
)
_DECISION_NOISE: Final[frozenset[str]] = frozenset(
    {
        "context",
        "consequences",
        "decision",
        "status",
        "background",
        "summary",
        "overview",
        "notes",
        "references",
        "related",
        "options",
        "alternatives",
        "rationale",
        "discussion",
        "appendix",
        "see also",
        "next steps",
        "changelog",
        "implementation",
        "motivation",
        "problem",
        "proposal",
        "goals",
        "non-goals",
        "scope",
        "out of scope",
        "future work",
        "open questions",
        "security",
        "testing",
        "rollout",
        "compatibility",
    }
)


class CompilerCoverageError(ValueError):
    """Fail-closed compiler-coverage error."""


def _honesty() -> dict[str, bool]:
    return {
        "lens_is_authority": False,
        "fabricated_fields": False,
        "unknown_is_valid": True,
        "unknown_is_healthy": False,
    }


def _safe_project_id(project_id: str) -> str:
    try:
        return safe_relative_component(project_id, label="project id")
    except ValueError as exc:
        raise CompilerCoverageError(str(exc)) from exc


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _posix_path(path: str) -> str:
    return path.replace("\\", "/").removeprefix("./")


def _is_secret_path(path: str) -> bool:
    name = Path(_posix_path(path)).name.lower()
    if name in _SECRET_BASENAMES or name.startswith(".env"):
        return True
    return name.endswith((".pem", ".key")) or "credential" in name


def _is_nested_noise(path: str) -> bool:
    posix = _posix_path(path).lower()
    return posix.startswith(_NESTED_PREFIXES)


def _imported_document(vault: Path, source_id: str) -> Path | None:
    try:
        safe_id = safe_relative_component(source_id, label="source_id")
    except ValueError:
        return None
    for suffix in _IMPORTED_SUFFIXES:
        try:
            candidate = resolve_under_root(
                vault,
                IMPORTED_RELATIVE.as_posix() + f"/{safe_id}{suffix}",
                label="imported document",
            )
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None


def _read_imported_text(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    if scan_text(text):
        return None
    return text


def _parse_semantic_record(project_md: str) -> dict[str, Any] | None:
    match = _JSON_FENCE_RE.search(project_md)
    if not match:
        return None
    try:
        raw = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return raw if isinstance(raw, dict) else None


def _owned_source_rows(vault: Path, project_id: str) -> list[dict[str, Any]]:
    """Return project-owned imported source rows. Sibling / sentinel never leak."""
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    blocked: set[str] = set()

    def _accept(row: dict[str, Any], *, require_owner: bool) -> None:
        if row.get("exclusion_reason"):
            return
        path = str(row.get("path") or "")
        source_id = str(row.get("source_id") or "")
        if not path or not source_id or source_id in seen:
            return
        if source_id in blocked:
            return
        if _is_secret_path(path):
            return
        if require_owner:
            # likely_project is the owner bind. row.project_id is spoofable
            # (AS-DOGFOOD-SEMANTIC-OWNER-001 leftover P1-C-001).
            owner = str(row.get("likely_project") or "").strip()
            if owner != project_id or owner == _UNKNOWN_PROJECT:
                # Remember rejected ids so a later semantic row cannot
                # rebind the same source_id to the requested project.
                blocked.add(source_id)
                return
        seen.add(source_id)
        selected.append(row)

    manifest = _read_json(vault / CONNECT_MANIFEST)
    if manifest:
        rows = manifest.get("sources")
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    _accept(row, require_owner=True)

    note = vault / "projects" / project_id / "project.md"
    if note.is_file():
        try:
            semantic = _parse_semantic_record(note.read_text(encoding="utf-8"))
        except OSError:
            semantic = None
        sources = semantic.get("sources") if isinstance(semantic, dict) else None
        if isinstance(sources, list):
            for row in sources:
                if isinstance(row, dict):
                    # Semantic record is not an owner bypass. Foreign
                    # likely_project / project_id must not enter harbor scope.
                    _accept(row, require_owner=True)
    return selected


def _load_owned_documents(
    vault: Path, project_id: str
) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Return ``(path, source_id, text)`` plus inspected artifact paths."""
    inspected: list[str] = [CONNECT_MANIFEST.as_posix()]
    loaded: list[tuple[str, str, str]] = []
    for row in _owned_source_rows(vault, project_id):
        path = _posix_path(str(row.get("path") or ""))
        source_id = str(row.get("source_id") or "")
        imported = _imported_document(vault, source_id)
        if imported is None:
            continue
        rel = imported.relative_to(vault).as_posix()
        inspected.append(rel)
        text = _read_imported_text(imported)
        if text is None:
            inspected.append(f"skipped-secret-or-unreadable:{path}")
            continue
        loaded.append((path, source_id, text))
    return loaded, inspected


def _root_candidates(
    documents: list[tuple[str, str, str]], names: frozenset[str]
) -> list[tuple[str, str, str]]:
    hits: list[tuple[int, str, str, str]] = []
    for path, source_id, text in documents:
        posix = _posix_path(path)
        if Path(posix).name.lower() not in names:
            continue
        if _is_nested_noise(posix):
            continue
        hits.append((posix.count("/"), posix, source_id, text))
    return [(path, source_id, text) for _depth, path, source_id, text in sorted(hits)]


def _toml_payload(text: str) -> dict[str, Any] | None:
    body = text
    fence = _TOML_FENCE_RE.search(text)
    if fence:
        body = fence.group(1)
    try:
        payload = tomllib.loads(body)
    except tomllib.TOMLDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _stack_from_pyproject(documents: list[tuple[str, str, str]]) -> str | None:
    for path, _source_id, text in documents:
        posix = _posix_path(path)
        if posix.lower() != "pyproject.toml" or _is_nested_noise(posix):
            continue
        parsed = _toml_payload(text)
        parts: list[str] = []
        if parsed is not None:
            project = parsed.get("project")
            if isinstance(project, dict):
                requires = project.get("requires-python")
                if isinstance(requires, str) and requires.strip():
                    parts.append(f"Python {requires.strip()}")
                deps = project.get("dependencies")
                names: list[str] = []
                if isinstance(deps, list):
                    for item in deps:
                        if not isinstance(item, str) or not item.strip():
                            continue
                        token = item.strip().strip("\"'")
                        name = token.split(">=")[0].split("==")[0].split("[")[0].strip()
                        if name:
                            names.append(name)
                if names:
                    parts.append(", ".join(names[:6]))
            if not parts and (
                isinstance(project, dict) or isinstance(parsed.get("build-system"), dict)
            ):
                parts.append("Python")
        if not parts:
            lowered = text.lower()
            if (
                "requires-python" in lowered
                or "[project]" in lowered
                or "[build-system]" in lowered
            ):
                if "requires-python" in lowered:
                    for line in text.splitlines():
                        stripped = line.strip()
                        if stripped.lower().startswith("requires-python"):
                            requires = stripped.split("=", 1)[-1].strip().strip("\"'")
                            if requires:
                                parts.append(f"Python {requires}")
                                break
                if not parts:
                    parts.append("Python")
        if parts:
            return redact_text(" · ".join(parts))[:_STACK_MAX]
    return None


def _first_prose_paragraph(markdown: str, *, max_chars: int = _SUMMARY_MAX) -> str | None:
    body = _FRONTMATTER_RE.sub("", markdown, count=1).strip()
    if not body:
        return None
    paragraphs: list[str] = []
    buf: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            if buf:
                paragraphs.append(" ".join(buf).strip())
                buf = []
            if paragraphs:
                break
            continue
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
    text = paragraphs[0] if paragraphs else None
    if not text:
        return None
    cleaned = redact_text(text)
    if len(cleaned) > max_chars:
        cleaned = cleaned[: max_chars - 1].rstrip() + "…"
    return cleaned


def _section_body(markdown: str, titles: frozenset[str]) -> str | None:
    body = _FRONTMATTER_RE.sub("", markdown, count=1)
    capture = False
    buf: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        heading = _HEADING_RE.match(stripped)
        if heading:
            title = heading.group(2).strip().lower()
            if capture:
                break
            if title in titles:
                capture = True
            continue
        if capture:
            buf.append(line)
    if not buf:
        return None
    return "\n".join(buf)


def _purpose_from_agents(documents: list[tuple[str, str, str]]) -> str | None:
    agent_names = frozenset({"agents.md", "claude.md"})
    for _path, _source_id, text in _root_candidates(documents, agent_names):
        section = _section_body(
            text,
            frozenset({"project overview", "overview", "purpose", "what this project is"}),
        )
        blurb = _first_prose_paragraph(section) if section else _first_prose_paragraph(text)
        if blurb:
            return blurb
    return None


def _purpose_from_pyproject(documents: list[tuple[str, str, str]]) -> str | None:
    for path, _source_id, text in documents:
        if _posix_path(path).lower() != "pyproject.toml":
            continue
        parsed = _toml_payload(text)
        if not parsed:
            continue
        project = parsed.get("project")
        if not isinstance(project, dict):
            continue
        description = project.get("description")
        if isinstance(description, str) and description.strip():
            return redact_text(description.strip())[:_SUMMARY_MAX]
    return None


def _is_decision_path(path: str) -> bool:
    posix = _posix_path(path).lower()
    if _is_nested_noise(posix):
        return False
    name = Path(posix).name.lower()
    if name in {"decisions.md", "decision.md"}:
        return True
    if name.startswith("adr-") or name.startswith("adr_"):
        return True
    parts = posix.split("/")
    return "adr" in parts or "adrs" in parts or "decisions" in parts


def _is_decision_noise(title: str) -> bool:
    lowered = title.strip().lower().rstrip(":")
    return lowered in _DECISION_NOISE


def _looks_like_decision_title(title: str) -> bool:
    if _is_decision_noise(title):
        return False
    if _ADR_TITLE_RE.search(title):
        return True
    lower = title.strip().lower()
    if lower.startswith(("decide ", "decision:", "we will ", "adopt ", "use ", "prefer ")):
        return True
    return bool(re.match(r"^\d+\.\s+\S+", title.strip()) and len(lower.split()) >= 4)


def _decisions_from_documents(documents: list[tuple[str, str, str]]) -> str | None:
    titles: list[str] = []
    evidence_paths: list[str] = []
    for path, _source_id, text in documents:
        if not _is_decision_path(path):
            continue
        evidence_paths.append(path)
        body = _FRONTMATTER_RE.sub("", text, count=1)
        file_titles: list[str] = []
        for line in body.splitlines():
            match = _HEADING_RE.match(line.strip())
            if not match:
                continue
            title = match.group(2).strip()
            if not title or _is_decision_noise(title):
                continue
            if _looks_like_decision_title(title) or not file_titles:
                file_titles.append(redact_text(title)[:200])
            if len(file_titles) >= 4:
                break
        if not file_titles:
            file_titles.append(redact_text(Path(path).stem.replace("_", " ").replace("-", " ")))
        titles.extend(file_titles)
    if not evidence_paths:
        return None
    unique: list[str] = []
    seen: set[str] = set()
    for title in titles:
        key = title.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(title.strip())
        if len(unique) >= _DECISION_MAX:
            break
    if not unique:
        unique = [Path(path).name for path in evidence_paths[:_DECISION_MAX]]
    summary = "; ".join(unique)
    if len(summary) > _SUMMARY_MAX:
        summary = summary[: _SUMMARY_MAX - 1].rstrip() + "…"
    return summary


def compile_dogfood_coverage(vault: Path, project_id: str) -> dict[str, Any]:
    """Derive coverage fields from imported Layer A evidence only."""
    project_id = _safe_project_id(project_id)
    vault = vault.expanduser().resolve()
    documents, inspected = _load_owned_documents(vault, project_id)

    tech_stack = _stack_from_pyproject(documents)
    purpose = _purpose_from_agents(documents) or _purpose_from_pyproject(documents)
    decisions = _decisions_from_documents(documents)

    notes = [
        "Derived from imported pyproject.toml / AGENTS / ADR evidence only",
        "lens≠authority",
        "UI≠canonical",
        "MODEL_OUTPUT≠AUTHORITY",
        "UNKNOWN remains when evidence is absent",
    ]
    if tech_stack:
        notes.append("tech_stack_source=pyproject.toml")
    if purpose:
        notes.append("purpose_source=agents-or-pyproject-description")
    if decisions:
        notes.append("decision_source=adr-or-decisions")

    return {
        "schema_version": 1,
        "schema": "atlas.coder-alpha.dogfood-compiler-coverage.v1",
        "package": PACKAGE_ID,
        "project_id": project_id,
        "tech_stack": tech_stack or UNKNOWN,
        "purpose": purpose or UNKNOWN,
        "important_decisions": decisions or UNKNOWN,
        "inspected_artifacts": inspected,
        "notes": notes,
        "generated": {"by": GENERATOR_ID},
        "honesty": _honesty(),
        "fabricated_field_count": 0,
        "cross_project_leak_count": 0,
    }


def attach_compiler_coverage(
    lens: dict[str, Any],
    vault: Path,
    project_id: str,
) -> dict[str, Any]:
    """Attach coverage to a derived lens. Does not invent authority."""
    coverage = compile_dogfood_coverage(vault, project_id)
    inspected = [item for item in (lens.get("inspected_artifacts") or []) if isinstance(item, str)]
    for item in coverage.get("inspected_artifacts") or []:
        if isinstance(item, str) and item not in inspected:
            inspected.append(item)
    notes = [item for item in (lens.get("notes") or []) if isinstance(item, str)]
    notes.append(f"compiler_coverage={PACKAGE_ID}")
    honesty = dict(lens.get("honesty") or {}) if isinstance(lens.get("honesty"), dict) else {}
    honesty.update(_honesty())

    summary = lens.get("summary")
    value = lens.get("value")
    status = lens.get("status")
    purpose = coverage.get("purpose")
    if status == "unknown" and isinstance(purpose, str) and purpose != UNKNOWN:
        summary = purpose
        value = purpose
        status = "derived"
        notes.append("overview purpose recalled from AGENTS/pyproject evidence")

    lens["summary"] = summary
    lens["value"] = value
    lens["status"] = status
    lens["inspected_artifacts"] = inspected
    lens["notes"] = notes
    lens["compiler_coverage"] = {
        "package": PACKAGE_ID,
        "tech_stack": coverage.get("tech_stack"),
        "purpose": coverage.get("purpose"),
        "important_decisions": coverage.get("important_decisions"),
        "fabricated_field_count": 0,
        "cross_project_leak_count": 0,
        "honesty": coverage.get("honesty") or _honesty(),
    }
    lens["honesty"] = honesty
    return lens
