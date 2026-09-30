"""AS-CODER-ALPHA-ARCH-002 — structured architecture answer lens.

Builds a non-authoritative architecture lens from imported architecture-bearing
docs selected through the connect manifest. Extraction is content-based: source
paths select candidate authority, but slot values are filled only from headings
and prose signals. README files are not architecture authority.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from project_atlas.inventory_drift import attach_source_drift
from project_atlas.secrets import scan_text

PACKAGE_ID = "AS-CODER-ALPHA-ARCH-002"
GENERATOR_ID = "atlas-coder-alpha-architecture-002"
ANSWERS_RELATIVE = Path("generated") / "answers"

ARCHITECTURE_SLOTS: tuple[str, ...] = (
    "system_purpose",
    "major_components",
    "component_responsibilities",
    "data_flow",
    "control_flow",
    "trust_boundaries",
    "human_agent_interaction",
    "runtime_surfaces",
    "data_stores",
    "knowledge_pipeline",
    "web_cli_mcp_obsidian",
    "key_integrations",
    "important_arch_decisions",
    "known_gaps",
)
_SUMMARY_SLOT_ORDER: tuple[str, ...] = (
    "knowledge_pipeline",
    "important_arch_decisions",
    "major_components",
    "component_responsibilities",
    "data_flow",
    "control_flow",
    "trust_boundaries",
    "runtime_surfaces",
    "data_stores",
    "web_cli_mcp_obsidian",
    "human_agent_interaction",
    "key_integrations",
    "known_gaps",
    "system_purpose",
)

_UNKNOWN = "UNKNOWN"
_SUMMARY_MAX_CHARS = 720
_SLOT_MAX_CHARS = 320
_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_STAGE_RE = re.compile(r"^#+\s*Stage\s+\d+\s+[-\u2013\u2014]\s*(.+?)\s*$", re.IGNORECASE)
# Prefer fenced identifiers so underscores survive Markdown cleanup.
_MODULE_NAME_RE = re.compile(r"`([^`]+?\.py)`")
_BARE_PY_MODULE_RE = re.compile(r"\b[\w./-]+?\.py\b")
_TABLE_SEPARATOR_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?$")
_NON_PACKAGE_MODULE_PREFIXES = (
    "docs/",
    "fixtures/",
    "tests/",
    "deps/",
    "apps/",
    "integrations/",
)


class ProjectArchitectureError(ValueError):
    """Fail-closed architecture lens error."""


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


def _safe_project_id(project_id: str) -> str:
    if not project_id or project_id in {".", ".."} or "/" in project_id or "\\" in project_id:
        raise ProjectArchitectureError(f"unsafe project id: {project_id!r}")
    return project_id


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _manifest_source_rows(vault: Path, project_id: str) -> list[dict[str, Any]]:
    """Select connect-manifest rows owned by ``project_id``.

    D-044 / D-OWNER-DRIFT-039: ``unknown-project`` is never an authoritative
    owner — including when the requested scope is itself the sentinel. Unowned
    architecture documents must not leak into any project lens response.
    Ownership follows ``likely_project`` or ``project_id`` (inventory-drift parity).
    """
    manifest = _read_json(vault / "generated" / "ops" / "connect-manifest.json")
    if not manifest:
        return []
    rows = manifest.get("sources")
    if not isinstance(rows, list):
        return []
    if project_id == "unknown-project":
        return []
    selected: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("exclusion_reason"):
            continue
        raw_owner = row.get("likely_project") or row.get("project_id")
        if not isinstance(raw_owner, str):
            continue
        owner = raw_owner.strip() or "unknown-project"
        if owner == "unknown-project" or owner != project_id:
            continue
        source_id = row.get("source_id")
        path = row.get("path")
        if isinstance(source_id, str) and scan_text(source_id):
            continue
        if isinstance(path, str) and scan_text(path):
            continue
        if scan_text(owner):
            continue
        selected.append(row)
    return selected


def _architecture_rank(path: str) -> tuple[int, int, str] | None:
    """Select architecture-authority candidates without inferring slot values."""
    posix = path.replace("\\", "/").removeprefix("./")
    lower = posix.lower()
    depth = posix.count("/")
    # Demo / fixture architecture docs are not repository authority.
    if lower.startswith(("docs/demo/", "fixtures/", "deps/")):
        return None
    if lower == "docs/plan.md":
        return (0, depth, posix)
    if lower in {"agents.md", "claude.md"}:
        return (1, depth, posix)
    # Root ARCHITECTURE.md is authoritative estate evidence (D-041/D-044).
    if lower == "architecture.md":
        return (1, depth, posix)
    if lower == "docs/prp.md":
        return (2, depth, posix)
    if lower == "docs/atlas-2.0/architecture.md":
        return (3, depth, posix)
    # Nested product-slice ARCHITECTURE.md (atlas-2.2/*, etc.) dilute Core
    # architecture with optional surfaces — exclude from authority merge.
    if "/atlas-2.2/" in lower or "/atlas-2.1/" in lower:
        return None
    if lower.endswith("/architecture.md") and lower.startswith("docs/"):
        return (5, depth, posix)
    if lower.endswith("/plan.md"):
        return (4, depth, posix)
    if Path(posix).name.lower() in {"readme.md", "readme.txt", "readme"}:
        return None
    return None


def _imported_document_path(vault: Path, source_id: str) -> Path:
    return vault / "sources" / "imported-documents" / f"{source_id}.md"


def _clean_line(line: str) -> str:
    text = line.strip()
    if not text or text.startswith("```") or _TABLE_SEPARATOR_RE.match(text):
        return ""
    if text.startswith("|") and text.endswith("|"):
        cells = [cell.strip() for cell in text.strip("|").split("|")]
        cells = [cell for cell in cells if cell and set(cell) - {"-", ":"}]
        text = ": ".join(cells)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text)
    text = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", text)
    text = text.replace("\u2192", "->").replace("\u2014", "-").replace("\u2013", "-")
    text = _LINK_RE.sub(r"\1", text)
    # Strip Markdown emphasis/code fences without destroying identifiers
    # like MODEL_OUTPUT or mcp_server.py (review P2).
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"(?<!\w)_(?!\w)", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ;")
    return text


def _clean_text(text: str) -> str:
    return _FRONTMATTER_RE.sub("", text, count=1)


def _heading_level(line: str) -> int | None:
    match = _HEADING_RE.match(line.strip())
    if not match:
        return None
    return len(match.group(1))


def _heading_title(line: str) -> str | None:
    match = _HEADING_RE.match(line.strip())
    if not match:
        return None
    return _clean_line(match.group(2))


def _capture_section(
    text: str,
    heading_predicate: Callable[[str], bool],
    *,
    max_lines: int = 12,
) -> list[str]:
    lines = _clean_text(text).splitlines()
    capture = False
    level_start = 0
    out: list[str] = []
    for line in lines:
        title = _heading_title(line)
        level = _heading_level(line)
        if title is not None and level is not None and heading_predicate(title.lower()):
            capture = True
            level_start = level
            out = []
            continue
        if capture and title is not None and level is not None and level <= level_start:
            break
        if not capture:
            continue
        cleaned = _clean_line(line)
        if cleaned:
            out.append(cleaned)
        if len(out) >= max_lines:
            break
    return out


def _first_signal_lines(
    text: str,
    tokens: tuple[str, ...],
    *,
    max_lines: int = 4,
    banned: tuple[str, ...] = (),
) -> list[str]:
    found: list[str] = []
    for line in _clean_text(text).splitlines():
        cleaned = _clean_line(line)
        if not cleaned:
            continue
        lower = cleaned.lower()
        if any(token in lower for token in banned):
            continue
        if any(token in lower for token in tokens):
            found.append(cleaned)
        if len(found) >= max_lines:
            break
    return found


def _unique(parts: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        cleaned = _clean_line(part)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def _join(parts: list[str], *, max_chars: int = _SLOT_MAX_CHARS) -> str | None:
    unique = _unique(parts)
    if not unique:
        return None
    text = "; ".join(unique)
    if len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


def _dedupe_major_components(text: str) -> str:
    """Deduplicate ``*.py`` module names without dropping non-module prose.

    AGENTS/CLAUDE merges often repeat the same Core modules; plan.md may also
    contribute prose components. Rebuild only the module portion (D-043 review).
    """
    modules: list[str] = []
    seen_mod: set[str] = set()
    for match in _BARE_PY_MODULE_RE.finditer(text):
        name = match.group(0)
        if not _is_package_module(name):
            continue
        key = name.lower()
        if key in seen_mod:
            continue
        seen_mod.add(key)
        modules.append(name)
    if not modules:
        return text

    prose_parts: list[str] = []
    seen_prose: set[str] = set()
    for part in text.split("; "):
        residual = part
        for name in sorted(modules, key=len, reverse=True):
            residual = re.sub(re.escape(name), " ", residual, flags=re.IGNORECASE)
        residual = re.sub(r"(?i)\bcore package modules\b\s*:?\s*", " ", residual)
        residual = re.sub(r"[\s,|/]+", " ", residual).strip(" :-")
        if not residual:
            continue
        key = residual.lower()
        if key in seen_prose:
            continue
        seen_prose.add(key)
        prose_parts.append(residual)

    module_bit = "Core package modules: " + ", ".join(modules[:12])
    merged = "; ".join([*prose_parts, module_bit]) if prose_parts else module_bit
    if len(merged) > _SLOT_MAX_CHARS:
        return merged[: _SLOT_MAX_CHARS - 3].rstrip() + "..."
    return merged


def _layer_pipeline(text: str) -> str | None:
    """Derive Layer A/B/C meanings from source prose — never hard-code Atlas semantics."""
    lower = text.lower()
    if not all(token in lower for token in ("layer a", "layer b", "layer c")):
        return None
    meanings: dict[str, str] = {}
    for label in ("a", "b", "c"):
        # Prefer explicit headings: "## Layer A - Source evidence"
        layer_token = label

        def _layer_heading(title: str, *, lab: str = layer_token) -> bool:
            return bool(re.search(rf"\blayer\s*{lab}\b", title, flags=re.IGNORECASE))

        heading_lines = _capture_section(
            text,
            _layer_heading,
            max_lines=2,
        )
        if heading_lines:
            meanings[label] = _clean_line(heading_lines[0])
            continue
        # Fallback: first prose line that mentions the layer with a description.
        for line in _clean_text(text).splitlines():
            cleaned = _clean_line(line)
            if not cleaned:
                continue
            match = re.search(
                rf"\blayer\s*{label}\b\s*[-\u2013\u2014:]\s*(.+)$",
                cleaned,
                flags=re.IGNORECASE,
            )
            if match:
                meanings[label] = _clean_line(match.group(1))
                break
            match = re.search(
                rf"\blayer\s*{label}\b\s+(.+)$",
                cleaned,
                flags=re.IGNORECASE,
            )
            if match and len(match.group(1).split()) >= 2:
                meanings[label] = _clean_line(match.group(1))
                break
    if len(meanings) < 3:
        return None
    return (
        "Three-layer model: "
        f"Layer A {meanings['a']}; Layer B {meanings['b']}; Layer C {meanings['c']}"
    )


def _stage_pipeline(text: str) -> str | None:
    stages: list[str] = []
    for line in _clean_text(text).splitlines():
        match = _STAGE_RE.match(line.strip())
        if not match:
            continue
        stage = _clean_line(match.group(1))
        if stage:
            stages.append(stage)
    if len(stages) < 3:
        return None
    return "Evidence pipeline stages: " + " -> ".join(stages[:6])


def _module_rows(text: str) -> list[str]:
    rows: list[str] = []
    in_component_section = False
    section_level = 0
    for line in _clean_text(text).splitlines():
        title = _heading_title(line)
        level = _heading_level(line)
        if title is not None and level is not None:
            lower = title.lower()
            if any(
                token in lower
                for token in ("code organization", "architecture", "package layout")
            ):
                in_component_section = True
                section_level = level
                continue
            if in_component_section and level <= section_level:
                in_component_section = False
        if not in_component_section:
            continue
        # Keep raw line so fenced ``module.py`` identifiers retain underscores
        # for downstream component extraction (D-043 factual fidelity).
        if not (_MODULE_NAME_RE.search(line) or _BARE_PY_MODULE_RE.search(line)):
            continue
        if line.strip():
            rows.append(line.rstrip())
        if len(rows) >= 10:
            break
    return rows


def _is_package_module(name: str) -> bool:
    """Accept Core Python modules only — never docs/markers as components."""
    posix = name.replace("\\", "/").removeprefix("./")
    lower = posix.lower()
    if not lower.endswith(".py"):
        return False
    if any(lower.startswith(prefix) for prefix in _NON_PACKAGE_MODULE_PREFIXES):
        return False
    basename = Path(posix).name
    return bool(basename.endswith(".py") and basename.count(".") == 1)


def _component_summary(module_rows: list[str]) -> str | None:
    """Collect exact ``*.py`` module identifiers from package-layout rows.

    Root cause fix for Fresh Agent V2 error #1/#2 (D-043):
    - do not treat docs/plan.md or atlas-project.yaml as Core modules
    - preserve underscores by reading fenced names before Markdown cleanup
    """
    modules: list[str] = []
    seen: set[str] = set()
    for row in module_rows:
        # Prefer backtick-captured names from the raw row text before cleanup.
        candidates = [match.group(1) for match in _MODULE_NAME_RE.finditer(row)]
        if not candidates:
            cleaned = _clean_line(row)
            candidates = [match.group(0) for match in _BARE_PY_MODULE_RE.finditer(cleaned)]
        for name in candidates:
            if not _is_package_module(name):
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            modules.append(name)
        if len(modules) >= 12:
            break
    if not modules:
        return None
    return "Core package modules: " + ", ".join(modules[:12])


def _responsibility_summary(module_rows: list[str]) -> str | None:
    if not module_rows:
        return None
    cleaned_rows = [_clean_line(row) for row in module_rows]
    cleaned_rows = [row for row in cleaned_rows if row]
    return _join(cleaned_rows[:5])


def _surface_terms(text: str) -> list[str]:
    """Detect CLI/Web/MCP/Obsidian surface mentions — not bare filenames."""
    scrubbed = text.lower()
    scrubbed = re.sub(r"`[^`]+`", " ", scrubbed)
    scrubbed = re.sub(r"\b[\w./-]+\.(?:py|md|json|ya?ml|toml)\b", " ", scrubbed)
    found: list[str] = []
    for term, label in (
        (r"\bcli\b", "CLI"),
        (r"\bweb\b", "Web"),
        (r"\bmcp\b", "MCP"),
        (r"\bobsidian\b", "Obsidian"),
    ):
        if re.search(term, scrubbed):
            found.append(label)
    return found


def _surface_summary(text: str) -> str | None:
    # Require surface vocabulary outside filename tokens.
    if not _surface_terms(text):
        return None
    lines = _first_signal_lines(
        text,
        ("cli ", " cli", "live_api", "api-serve", "web ", " mcp", "obsidian", "chatgpt bridge"),
        max_lines=4,
    )
    if not lines:
        return None
    return _join(lines)


def _web_cli_mcp_obsidian_summary(text: str) -> str | None:
    terms = _surface_terms(text)
    if not terms:
        return None
    lines = _first_signal_lines(text, ("cli ", " web", " mcp", "obsidian"), max_lines=3)
    prefix = "Mentioned surfaces: " + ", ".join(terms)
    return _join([prefix, *lines]) if lines else prefix


def _plan_slots(text: str) -> dict[str, str]:
    slots: dict[str, str] = {}
    purpose = _first_signal_lines(
        text,
        ("project atlas converts", "knowledge compiler", "knowledge control plane"),
        max_lines=2,
    )
    value = _join(purpose)
    if value:
        slots["system_purpose"] = value

    layer_value = _layer_pipeline(text)
    if layer_value:
        slots["knowledge_pipeline"] = layer_value
        slots["important_arch_decisions"] = "Core architectural decision: " + layer_value
    else:
        three_layer = _first_signal_lines(text, ("three-layer vault", "three-layer"), max_lines=2)
        three_layer_value = _join(three_layer)
        if three_layer_value:
            slots["important_arch_decisions"] = "Core architectural decision: " + three_layer_value

    stage_value = _stage_pipeline(text)
    if stage_value:
        slots["data_flow"] = stage_value

    component_lines = _capture_section(
        text,
        lambda title: "component" in title,
        max_lines=5,
    )
    component_value = _join(component_lines)
    if component_value:
        slots["major_components"] = "Components: " + component_value

    human_agent = _first_signal_lines(
        text,
        ("humans and ai agents", "human navigation", "ai agents", "human-readable"),
        max_lines=3,
    )
    human_agent_value = _join(human_agent)
    if human_agent_value:
        slots["human_agent_interaction"] = human_agent_value

    integrations = _capture_section(
        text,
        lambda title: "discovery" in title or "strong recommendation" in title,
        max_lines=9,
    )
    integrations_value = _join(
        [
            line
            for line in integrations
            if any(
                token in line.lower()
                for token in (
                    "git repositories",
                    "google drive",
                    "markdown vaults",
                    "pdfs",
                    "word documents",
                    "obsidian",
                    "static sites",
                    "ai agents",
                )
            )
        ]
    )
    if integrations_value:
        slots["key_integrations"] = integrations_value

    web_value = _web_cli_mcp_obsidian_summary(text)
    if web_value:
        slots["web_cli_mcp_obsidian"] = web_value
    return slots


def _agents_or_claude_slots(text: str) -> dict[str, str]:
    slots: dict[str, str] = {}
    purpose = _first_signal_lines(
        text,
        ("project atlas is a local-first", "project knowledge compiler", "source-backed"),
        max_lines=2,
    )
    value = _join(purpose)
    if value:
        slots["system_purpose"] = value

    module_rows = _module_rows(text)
    component_value = _component_summary(module_rows)
    if component_value:
        slots["major_components"] = component_value
    responsibility_value = _responsibility_summary(module_rows)
    if responsibility_value:
        slots["component_responsibilities"] = responsibility_value

    control = _first_signal_lines(
        text,
        ("core pipeline", "discover", "ingest", "build-indexes", "validate"),
        max_lines=3,
    )
    control_value = _join(
        [line for line in control if "discover" in line.lower() and "ingest" in line.lower()]
    )
    if control_value:
        slots["control_flow"] = control_value

    trust = _first_signal_lines(
        text,
        (
            "no claim without",
            "truth boundaries",
            "model_output",
            "model output",
            "read-only",
            "fail-closed",
            "protected paths",
            "lens != authority",
            "lens≠",
        ),
        max_lines=5,
    )
    trust_value = _join(trust)
    if trust_value:
        slots["trust_boundaries"] = trust_value

    human_agent = _first_signal_lines(
        text,
        ("human-readable", "agent-readable", "humans and agents", "managed atlas agents"),
        max_lines=3,
    )
    human_agent_value = _join(human_agent)
    if human_agent_value:
        slots["human_agent_interaction"] = human_agent_value

    surface_value = _surface_summary(text)
    if surface_value:
        slots["runtime_surfaces"] = surface_value

    web_value = _web_cli_mcp_obsidian_summary(text)
    if web_value:
        slots["web_cli_mcp_obsidian"] = web_value

    integrations = _first_signal_lines(
        text,
        ("shared contracts", "mcp bridge", "chatgpt bridge", "google drive"),
        max_lines=4,
    )
    integrations_value = _join(integrations)
    if integrations_value:
        slots["key_integrations"] = integrations_value

    decisions = _first_signal_lines(
        text,
        ("src layout", "separate sibling deliverable", "package layout", "read-only mcp"),
        max_lines=4,
    )
    decisions_value = _join(decisions)
    if decisions_value:
        slots["important_arch_decisions"] = decisions_value

    gaps = _first_signal_lines(
        text,
        ("out of scope", "known gaps", "missing", "not implemented"),
        max_lines=3,
    )
    gaps_value = _join(gaps)
    if gaps_value:
        slots["known_gaps"] = gaps_value
    return slots


def _generic_architecture_slots(text: str) -> dict[str, str]:
    """Extract architecture slots from generic architecture documents (D-050 R5).

    Bounded heading/section capture only — never invents missing facts and never
    treats README prose as architecture authority.
    """
    slots: dict[str, str] = {}

    def _fill(
        slot: str,
        predicate: Callable[[str], bool],
        *,
        prefix: str = "",
        max_lines: int = 8,
    ) -> None:
        if slot in slots:
            return
        lines = _capture_section(text, predicate, max_lines=max_lines)
        value = _join(lines)
        if value:
            slots[slot] = f"{prefix}{value}" if prefix else value

    _fill(
        "major_components",
        lambda title: "component" in title and "responsibil" not in title,
        prefix="Components: ",
    )
    _fill(
        "component_responsibilities",
        lambda title: "responsibil" in title,
    )
    _fill(
        "runtime_surfaces",
        lambda title: title == "runtime"
        or title.startswith("runtime ")
        or "runtime surface" in title,
    )
    _fill(
        "data_stores",
        lambda title: "data store" in title
        or title in {"persistence", "storage", "data stores"},
    )
    _fill(
        "data_flow",
        lambda title: "data flow" in title or title == "dataflow",
    )
    _fill(
        "control_flow",
        lambda title: "control flow" in title or title == "controlflow",
    )
    _fill(
        "trust_boundaries",
        lambda title: "trust boundary" in title
        or title in {"boundaries", "boundary", "trust boundaries"},
    )
    _fill(
        "system_purpose",
        lambda title: title in {"purpose", "overview", "summary", "system purpose"},
        max_lines=3,
    )
    _fill(
        "important_arch_decisions",
        lambda title: "decision" in title or "architecture decision" in title,
        prefix="Architecture decision: ",
        max_lines=5,
    )
    _fill(
        "known_gaps",
        lambda title: "gap" in title or "out of scope" in title or "limitation" in title,
        max_lines=4,
    )
    return slots


def _slots_from_source(path: str, text: str) -> dict[str, str]:
    lower = path.replace("\\", "/").removeprefix("./").lower()
    if lower == "docs/plan.md" or lower.endswith("/plan.md"):
        return _plan_slots(text)
    basename = Path(lower).name
    if basename == "architecture.md" or lower.endswith("/architecture.md"):
        # Generic architecture docs first; AGENTS/CLAUDE-shaped signals fill gaps.
        slots = _generic_architecture_slots(text)
        for key, value in _agents_or_claude_slots(text).items():
            slots.setdefault(key, value)
        return slots
    return _agents_or_claude_slots(text)


def _render_summary(slots: dict[str, str]) -> str | None:
    filled = [
        f"{slot.upper()}: {value}"
        for slot in _SUMMARY_SLOT_ORDER
        for value in [slots.get(slot, _UNKNOWN)]
        if isinstance(value, str) and value != _UNKNOWN
    ]
    if not filled:
        return None
    summary = "; ".join(filled)
    if len(summary) > _SUMMARY_MAX_CHARS:
        return summary[: _SUMMARY_MAX_CHARS - 3].rstrip() + "..."
    return summary


def _candidate_sources(vault: Path, project_id: str) -> list[tuple[tuple[int, int, str], str, str]]:
    candidates: list[tuple[tuple[int, int, str], str, str]] = []
    for row in _manifest_source_rows(vault, project_id):
        path = str(row.get("path") or "")
        rank = _architecture_rank(path)
        source_id = str(row.get("source_id") or "")
        if rank is None or not source_id:
            continue
        candidates.append((rank, path, source_id))
    return sorted(candidates, key=lambda item: item[0])


def build_architecture_lens(vault: Path, project_id: str) -> dict[str, Any]:
    """Build one structured architecture lens for ``project_id`` (no disk writes)."""
    project_id = _safe_project_id(project_id)
    vault = vault.expanduser().resolve()
    collected: dict[str, list[str]] = {slot: [] for slot in ARCHITECTURE_SLOTS}
    evidence: list[str] = []
    inspected: list[str] = []

    # Primary authorities (rank 0-3) always merge; secondary (4+) only fill
    # still-empty slots so nested docs cannot dominate Core architecture.
    for rank, source_path, source_id in _candidate_sources(vault, project_id):
        imported = _imported_document_path(vault, source_id)
        inspected.append(imported.relative_to(vault).as_posix())
        if not imported.is_file():
            continue
        try:
            text = imported.read_text(encoding="utf-8")
        except OSError:
            continue
        source_slots = _slots_from_source(source_path, text)
        used_source = False
        secondary = rank[0] >= 4
        for slot, value in source_slots.items():
            if slot not in collected:
                continue
            cleaned = _clean_line(value)
            if not cleaned or cleaned == _UNKNOWN:
                continue
            if secondary and collected[slot]:
                continue
            collected[slot].append(cleaned)
            used_source = True
        if used_source and source_path not in evidence:
            evidence.append(source_path)

    slots = {
        slot: _join(collected[slot], max_chars=_SLOT_MAX_CHARS) or _UNKNOWN
        for slot in ARCHITECTURE_SLOTS
    }
    # Deduplicate Core module identifiers across AGENTS/CLAUDE merges while
    # preserving non-module prose components from plan / architecture docs.
    if slots.get("major_components") not in {None, _UNKNOWN}:
        slots["major_components"] = _dedupe_major_components(slots["major_components"])
    summary = _render_summary(slots)
    status = "derived" if summary else "unknown"

    return attach_source_drift(
        {
            "schema_version": 1,
            "schema": "atlas.coder-alpha.architecture-lens.v1",
            "package": PACKAGE_ID,
            "answer_id": f"ans-architecture-{project_id}",
            "subject": project_id,
            "field": "architecture",
            "title": "What is the architecture?",
            "summary": summary,
            "value": summary,
            "slots": slots,
            "status": status,
            "authority": "derived-lens",
            "layer": "C",
            "project_id": project_id,
            "evidence": evidence,
            "inspected_artifacts": inspected,
            "generated": {"by": GENERATOR_ID},
            "honesty": {
                "authentic_pilot": False,
                "release_certified": False,
                "atlas_opt_wake_gate": "CLOSED",
                "lens_is_authority": False,
                "fabricated_fields": False,
                "unknown_is_valid": True,
                "confidence_scores": False,
            },
            "notes": [
                "Derived from imported architecture-bearing docs selected through connect-manifest",
                "README is not architecture authority",
                "source path selects candidate docs only; slots require content signals",
                "UNKNOWN when unsupported",
                "MODEL_OUTPUT!=AUTHORITY",
            ],
        },
        vault,
        project_id,
        path_filter=lambda path: _architecture_rank(path) is not None,
    )


def materialize_architecture_lenses(
    vault: Path,
    *,
    project_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Write architecture answer lenses under ``generated/answers/``."""
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise ProjectArchitectureError(f"vault is not a directory: {vault}")
    selected = project_ids if project_ids is not None else _list_projects(vault)
    written: list[str] = []
    lenses: list[dict[str, Any]] = []
    for project_id in selected:
        lens = build_architecture_lens(vault, project_id)
        lenses.append(lens)
        path = vault / ANSWERS_RELATIVE / f"{lens['answer_id']}.json"
        _write_atomic(
            path,
            (json.dumps(lens, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
        written.append(path.relative_to(vault).as_posix())
    return {
        "schema_version": 1,
        "schema": "atlas.coder-alpha.architecture-receipt.v1",
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
