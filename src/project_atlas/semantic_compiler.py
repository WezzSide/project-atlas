"""Deterministic compilation of validated project records (AS-CORE-002)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any, Literal

from project_atlas.domain.claims import Claim
from project_atlas.domain.concepts import ConceptRecord
from project_atlas.domain.semantic import (
    AgentEventReference,
    AuthorityRecord,
    CoverageRecord,
    ProjectRecord,
    ReviewEntry,
    SourceAuthority,
    SourceLifecycleRecord,
)
from project_atlas.domain.vocabulary import (
    DocumentLifecycle,
    KnowledgeState,
    LifecycleStatus,
    SourceChangeState,
)

COVERAGE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("overview", ("project-overview",)),
    ("architecture", ("architecture",)),
    ("setup", ("setup", "readme")),
    ("operations", ("operations", "runbook")),
    ("development", ("development", "work-package")),
    ("testing", ("validation", "test")),
    ("security", ("security",)),
    ("roadmap", ("roadmap",)),
    ("decisions", ("decision",)),
    ("deployment", ("deployment",)),
    ("troubleshooting", ("troubleshooting",)),
)


def coverage_for(entries: Iterable[dict[str, Any]]) -> list[CoverageRecord]:
    values = list(entries)
    result: list[CoverageRecord] = []
    for category, classifications in COVERAGE_RULES:
        matches = [
            str(entry["source_id"])
            for entry in values
            if str(entry.get("classification", "")) in classifications
        ]
        state: Literal["absent", "partial", "present", "stale", "conflicting"] = (
            "present" if matches else "absent"
        )
        if len(matches) == 1 and category in {"overview", "architecture", "security"}:
            state = "partial"
        result.append(CoverageRecord(category=category, state=state, source_ids=sorted(matches)))
    return result


def compile_project_record(
    project_id: str,
    entries: list[dict[str, Any]],
    event_entries: list[dict[str, Any]],
    *,
    concepts: list[ConceptRecord] | None = None,
    claims: list[Claim] | None = None,
    authorities: list[AuthorityRecord] | None = None,
    reviews: list[ReviewEntry] | None = None,
) -> ProjectRecord:
    sources = [
        SourceLifecycleRecord(
            source_id=str(entry["source_id"]),
            project_uuid=entry.get("project_uuid"),
            source_lineage_id=entry.get("source_lineage_id") or None,
            lineage_generation=entry.get("lineage_generation"),
            path=str(entry["path"]),
            sha256=entry.get("sha256"),
            document_lifecycle=DocumentLifecycle.VERIFIED,
            source_change_state=SourceChangeState.UNCHANGED,
            first_seen=None,
            last_seen=None,
        )
        for entry in sorted(entries, key=lambda item: str(item["source_id"]))
    ]
    events = [
        AgentEventReference(
            event_id=str(entry["event_id"]),
            event_type=str(entry["event_type"]),
            session_id=str(entry["session_id"]),
            receipt_id=str(entry["receipt_id"]),
            source_package=str(entry["source"]),
        )
        for entry in sorted(
            event_entries, key=lambda item: (str(item["timestamp"]), str(item["event_id"]))
        )
    ]
    return ProjectRecord(
        project_id=project_id,
        name=project_id,
        lifecycle=LifecycleStatus.UNKNOWN,
        generated=True,
        sources=sources,
        authority=[
            SourceAuthority(level=KnowledgeState.IMPORTED_SOURCE, reason="discovered source")
        ],
        coverage=coverage_for(entries),
        concepts=concepts or [],
        claims=claims or [],
        authorities=authorities or [],
        reviews=reviews or [],
        agent_events=events,
        relationships=[],
    )


def _inert_span(value: object) -> str:
    """Render an untrusted token as a single-line code span (AS-CORE-002-F1)."""
    text = str(value).replace("`", "'").replace("\n", " ").replace("\r", "")
    return f"`{text}`"


def render_project_record(record: ProjectRecord, entries: list[dict[str, Any]]) -> str:
    """Render a deterministic project note with a protected human region."""
    payload = record.model_dump(
        mode="json", exclude={"concepts", "claims", "validations", "decisions"}
    )
    frontmatter = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    lines = [
        "---",
        "type: Project",
        f"title: {record.name}",
        "schema_version: 1",
        "generated: true",
        "knowledge_state: evidence-backed",
        "---",
        "",
        "# " + record.name,
        "",
        "<!-- atlas:generated:start -->",
        "## Semantic record",
        "",
        "```json",
        frontmatter,
        "```",
        "",
        "## Sources",
        "",
    ]
    for entry in sorted(entries, key=lambda item: str(item.get("path", "")).lower()):
        # AS-CORE-002-F1: untrusted path/source must not form a Markdown link.
        # A filename like ``a](b.md`` spoofs display text vs target.
        lines.append(
            f"- {_inert_span(entry.get('path'))} ({_inert_span(entry.get('source'))}) — "
            f"{_inert_span(entry.get('classification'))} — {_inert_span(entry.get('sha256'))}"
        )
    lines.extend(["", "<!-- atlas:generated:end -->", ""])
    return "\n".join(lines)
