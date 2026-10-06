"""AS-CORE2-008: duplicate-source conflict facets + review-queue honesty.

Tip-safe residual polish over the existing claims/conflict/review spine
(C8-FR-001…015). Does not invent Graph conflicts, trust scores, or a second
review-queue root. TEMPORAL same-source writers remain foreign (label-only).
"""

from __future__ import annotations

from typing import Any

from project_atlas.domain import ConflictRecord, ReviewCategory, ReviewEntry
from project_atlas.domain.authority_semantics import (
    AuthoritativeStateRecord,
    AuthorityDisposition,
)
from project_atlas.secrets import scan_text

DUPLICATE_SOURCE_KIND = "duplicate-source"


def distinct_source_ids(conflict: ConflictRecord) -> tuple[str, ...]:
    """Deterministic distinct ``source_id`` values across conflicting sides."""
    return tuple(sorted({side.source_id for side in conflict.claims if side.source_id}))


def distinct_source_lineage_ids(conflict: ConflictRecord) -> tuple[str, ...]:
    """Deterministic distinct lineage ids from sides and record field."""
    values: set[str] = set(conflict.source_lineage_ids)
    for side in conflict.claims:
        if side.source_lineage_id is not None:
            values.add(side.source_lineage_id)
    return tuple(sorted(values))


def duplicate_source_facet(conflict: ConflictRecord) -> dict[str, Any] | None:
    """Project a duplicate-source facet when sides span ≥2 sources (C8-FR-002).

    Fail closed: omit the facet when fewer than two distinct ``source_id``
    values are present (same-source multi-value stays TEMPORAL's plane).
    Never invents a conflict from Graph edges.
    """
    source_ids = distinct_source_ids(conflict)
    if len(source_ids) < 2:
        return None
    facet: dict[str, Any] = {
        "kind": DUPLICATE_SOURCE_KIND,
        "source_ids": list(source_ids),
    }
    lineage_ids = distinct_source_lineage_ids(conflict)
    if len(lineage_ids) >= 2:
        facet["source_lineage_ids"] = list(lineage_ids)
    return facet


def conflict_markdown_line(conflict: ConflictRecord) -> str:
    """Honesty line for ``projects/{project}/conflicts.md`` projections."""
    state = conflict.state.value
    line = f"- `{conflict.conflict_id}` `{conflict.field}` — {state}"
    facet = duplicate_source_facet(conflict)
    if facet is None:
        return line
    sources = ",".join(facet["source_ids"])
    return f"{line}; {DUPLICATE_SOURCE_KIND}[{sources}]"


def authority_disposition_for_conflict(
    conflict: ConflictRecord,
    authoritative_states: (
        tuple[AuthoritativeStateRecord, ...] | list[AuthoritativeStateRecord]
    ) = (),
) -> AuthorityDisposition | None:
    """Consume tip authority dispositions for a conflict subject+field."""
    matches = [
        state
        for state in authoritative_states
        if state.subject == conflict.subject and state.field == conflict.field
    ]
    if not matches:
        return None
    matches.sort(key=lambda item: (item.disposition.value, item.rule_id or ""))
    return matches[0].disposition


def conflict_review_reason(
    conflict: ConflictRecord,
    authoritative_states: (
        tuple[AuthoritativeStateRecord, ...] | list[AuthoritativeStateRecord]
    ) = (),
) -> str:
    """Harden CONFLICT review reasons with objective signals (C8-FR-004).

    No subjective trust / confidence invention beyond existing spine signals.
    """
    parts = ["materially incompatible source-backed claims"]
    facet = duplicate_source_facet(conflict)
    if facet is not None:
        parts.append(DUPLICATE_SOURCE_KIND)
        parts.append("sources=" + ",".join(facet["source_ids"]))
    disposition = authority_disposition_for_conflict(conflict, authoritative_states)
    if disposition is not None:
        parts.append(f"authority_disposition={disposition.value}")
    return "; ".join(parts)


def harden_conflict_reviews(
    reviews: list[ReviewEntry],
    conflicts: list[ConflictRecord] | tuple[ConflictRecord, ...],
    authoritative_states: (
        tuple[AuthoritativeStateRecord, ...] | list[AuthoritativeStateRecord]
    ) = (),
) -> list[ReviewEntry]:
    """Rewrite CONFLICT queue reasons using consumed authority dispositions."""
    by_id = {conflict.conflict_id: conflict for conflict in conflicts}
    hardened: list[ReviewEntry] = []
    for entry in reviews:
        if entry.category is ReviewCategory.CONFLICT and entry.subject_id in by_id:
            conflict = by_id[entry.subject_id]
            hardened.append(
                entry.model_copy(
                    update={
                        "reason": conflict_review_reason(conflict, authoritative_states),
                    }
                )
            )
        else:
            hardened.append(entry)
    return hardened


def conflict_index_companions(
    records: list[dict[str, Any]],
) -> dict[str, dict[str, list[str]]]:
    """Additive ``conflicts.json`` companion maps (C8-FR-003).

    Returns unsorted maps; caller applies deterministic ``_sorted_index``.
    """
    by_source: dict[str, list[str]] = {}
    by_lineage: dict[str, list[str]] = {}
    by_project: dict[str, list[str]] = {}

    def _add(index: dict[str, list[str]], key: object, value: str) -> None:
        text = str(key)
        if not text or text == "None":
            return
        # AS-SEC-SCAN-INDEXES-CONFLICT-PROJECTID-JSON-ESC-001: companion
        # maps decode ``\u`` project_id / source_id after json.loads.
        if scan_text(text) or scan_text(value):
            return
        index.setdefault(text, []).append(value)

    for conflict in records:
        conflict_id = str(conflict["conflict_id"])
        project_id = conflict.get("project_id")
        if project_id:
            _add(by_project, project_id, conflict_id)
        for side in conflict.get("claims", []):
            if not isinstance(side, dict):
                continue
            source_id = side.get("source_id")
            if source_id:
                _add(by_source, source_id, conflict_id)
            lineage = side.get("source_lineage_id")
            if lineage:
                _add(by_lineage, lineage, conflict_id)
        for lineage in conflict.get("source_lineage_ids", []):
            _add(by_lineage, lineage, conflict_id)
    return {
        "by_source_id": by_source,
        "by_source_lineage_id": by_lineage,
        "by_project_id": by_project,
    }


def review_index_companions(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build additive lexical ``reviews.json`` payload over pending entries.

    Companion index only — does not invent a second durable queue root
    (C8-FR-005).
    """
    by_id: dict[str, list[str]] = {}
    by_category: dict[str, list[str]] = {}
    by_project: dict[str, list[str]] = {}
    by_source: dict[str, list[str]] = {}

    def _add(index: dict[str, list[str]], key: object, value: str) -> None:
        text = str(key)
        if not text or text == "None":
            return
        # AS-SEC-SCAN-INDEXES-REVIEW-PROJECTID-JSON-ESC-001: companion
        # maps decode ``\u`` project_id after json.loads.
        if scan_text(text) or scan_text(value):
            return
        index.setdefault(text, []).append(value)

    for entry in records:
        review_id = str(entry["review_id"])
        _add(by_id, review_id, review_id)
        _add(by_category, entry.get("category", ""), review_id)
        _add(by_project, entry.get("project_id", ""), review_id)
        for source_id in entry.get("source_ids", []):
            _add(by_source, source_id, review_id)

    def _sorted(index: dict[str, list[str]]) -> dict[str, list[str]]:
        return {key: sorted(set(values)) for key, values in sorted(index.items())}

    return {
        "schema_version": 1,
        "ids": sorted(by_id),
        "by_review_id": _sorted(by_id),
        "by_category": _sorted(by_category),
        "by_project_id": _sorted(by_project),
        "by_source_id": _sorted(by_source),
    }
