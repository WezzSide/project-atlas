"""AS-GRAPH-005 — Human-readable derived graph projections.

Renders Core-owned Markdown views from machine graph state only:

- ``generated/graph/projections/<project>/relationships.md``
- ``generated/graph/projections/<project>/graph-health.md``

Consumes AS-GRAPH-003 relationship records and AS-GRAPH-004 health snapshots
without mutating certified GRAPH-002/003/004 stores, claim/authority writers,
or Control Plane ``relationships/``.

Truth boundary: GRAPH PROJECTION ≠ AUTOMATIC AUTHORITY.
Projections are derived intelligence / operational views — never Layer A
evidence and never domain-authoritative claims.

HUMAN-region preservation on regeneration is delegated to the canonical
protected-region core (``project_atlas.protected_regions``) through a narrow
adapter (AS-OBSIDIAN-CAPTURE-001-F4); see ``_merge_protected_regions`` for the
one retained graph-specific contract.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from project_atlas.graph_quarantine import GraphHealthSnapshot, HealthState
from project_atlas.graph_relationships import LinkQuality, RelationshipRecord
from project_atlas.protected_regions import (
    ProtectedRegionError,
    generated_marker_diagnosis,
    read_note_text,
    reject_ambiguous_region_identity,
)
from project_atlas.protected_regions import (
    merge_protected_regions as _canonical_merge_protected_regions,
)
from project_atlas.secrets import scan_text

PACKAGE_ID = "AS-GRAPH-005"
SOURCE_RELATIONSHIP_PACKAGE = "AS-GRAPH-003"
SOURCE_HEALTH_PACKAGE = "AS-GRAPH-004"
AUTHORITY_LEVEL = "derived"
TRUTH_BOUNDARY = "GRAPH PROJECTION ≠ AUTOMATIC AUTHORITY"
GENERATED_BY = "atlas-graph-005"
DERIVED_LABEL = "derived / source-linked"
INTELLIGENCE_LABEL = "derived intelligence — not Layer A evidence"

ALLOWED_WRITE_PREFIXES: tuple[str, ...] = ("generated/graph/projections/",)

_FORBIDDEN_WRITE_PREFIXES: tuple[str, ...] = (
    "relationships/",
    "state/current-state/",
    "state/authoritative-state/",
    "state/global-entities/",
    "claims/",
    "generated/indexes/",
    "generated/query/",
    "generated/graph/resolved/",
    "generated/graph/quarantine-candidates/",
    "generated/graph/acceptance/",
    "generated/graph/relationships/",
    "generated/graph/relationship-quarantine/",
    "generated/graph/quarantine/",
    "generated/graph/health/",
    "generated/graph/incremental/",
    "generated/ops/",
)

_GENERATED_START = "<!-- atlas:generated:start -->"
_GENERATED_END = "<!-- atlas:generated:end -->"
_HUMAN_BEGIN = re.compile(r"<!--\s*BEGIN HUMAN:\s*([^\s>]+)\s*-->")
_HUMAN_END = re.compile(r"<!--\s*END HUMAN:\s*([^\s>]+)\s*-->")

ProjectionName = Literal["relationships", "graph-health"]

# Test seam for injected mid-promote failure (leaves prior state intact).
_replace_path = os.replace


class GraphProjectionError(ValueError):
    """Fail-closed graph projection error (metadata-only message)."""


@dataclass(frozen=True)
class ProjectionBundle:
    """Rendered Markdown projections for one project (derived views only)."""

    project_id: str
    relationships_md: str
    graph_health_md: str
    relationship_count: int
    health_present: bool
    source_state: Literal["present", "absent"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "package_id": PACKAGE_ID,
            "project_id": self.project_id,
            "authority": {
                "level": AUTHORITY_LEVEL,
                "note": (
                    "Graph projections are derived views of machine graph state; "
                    "never elevate to domain authority or Layer A evidence."
                ),
            },
            "relationship_count": self.relationship_count,
            "health_present": self.health_present,
            "source_state": self.source_state,
            "truth_boundary": TRUTH_BOUNDARY,
            "generated": {"by": GENERATED_BY},
        }


@dataclass(frozen=True)
class _PromotionEntry:
    path: Path
    staged: Path
    backup: Path
    had_original: bool


def _assert_project_id(project_id: str) -> None:
    if not project_id or "/" in project_id or "\\" in project_id or project_id in {".", ".."}:
        raise GraphProjectionError("project-id-unsafe")



def _redact_text(value: str) -> str:
    """Never echo secret-shaped content in projection Markdown."""
    text = value.strip()
    # AS-SEC-SCAN-GRAPH-PROJ-RELID-JSON-ESC-001: json.loads of retained
    # relationship records can decode ``\u`` ids that scan_text misses on
    # raw bytes. Do not interpolate them into relationships.md.
    if scan_text(text):
        return "redacted-sensitive"
    lowered = text.lower()
    for needle in ("password=", "secret=", "token=", "api_key=", "bearer ", "private-key"):
        if needle in lowered:
            return "redacted-sensitive"
    return text[:240]


def _validate_protected_markers(text: str, *, path: str) -> None:
    """AT-011: fail closed on unbalanced HUMAN / generated markers."""
    begins = _HUMAN_BEGIN.findall(text)
    ends = _HUMAN_END.findall(text)
    if len(begins) != len(ends):
        raise GraphProjectionError(f"malformed-protected-markers:{path}")
    if sorted(begins) != sorted(ends):
        raise GraphProjectionError(f"malformed-protected-markers:{path}")
    start_count = text.count(_GENERATED_START)
    end_count = text.count(_GENERATED_END)
    if start_count != end_count or start_count > 1:
        raise GraphProjectionError(
            f"malformed-generated-markers:"
            f"{generated_marker_diagnosis(text, reason='count')}:{path}"
        )
    if start_count == 1:
        start_index = text.index(_GENERATED_START)
        end_index = text.index(_GENERATED_END)
        if end_index < start_index:
            raise GraphProjectionError(
                f"malformed-generated-markers:"
                f"{generated_marker_diagnosis(text, reason='end-before-begin')}:{path}"
            )


def _generated_span(text: str, *, path: str) -> tuple[int, int] | None:
    """Bounds of the single generated span, or ``None`` when there is none.

    :func:`_validate_protected_markers` treats a document carrying *no*
    generated markers as balanced (zero begins, zero ends), so the retained
    graph-specific splice below cannot assume they are present. Locating them
    with ``str.index`` raised a bare :class:`ValueError` that escaped this
    module's ``GraphProjectionError`` boundary. Fails closed instead when the
    markers are present but unusable -- only one side, or END before BEGIN.
    """
    start = text.find(_GENERATED_START)
    end = text.find(_GENERATED_END)
    if start < 0 and end < 0:
        return None
    if start < 0 or end < 0 or end < start:
        # `end < start` is a distinct condition from a missing marker, and the
        # operator can act on the difference, so report which one it was.
        reason = "end-before-begin" if (start >= 0 and end >= 0) else "count"
        raise GraphProjectionError(
            f"malformed-generated-markers:"
            f"{generated_marker_diagnosis(text, reason=reason)}:{path}"
        )
    return start, end + len(_GENERATED_END)


def _merge_protected_regions(*, existing: str | None, rendered: str, path: str) -> str:
    """Preserve HUMAN regions via the canonical protected-region core (F4).

    AS-OBSIDIAN-CAPTURE-001-F4: HUMAN region identity, ambiguity handling,
    and preservation semantics are delegated to
    :func:`project_atlas.protected_regions.merge_protected_regions` so this
    derived-view surface cannot drift from the canonical implementation
    again. Canonical refusals are translated to
    :class:`GraphProjectionError`; the fail-closed guarantee is unchanged.

    One graph-specific contract is retained deliberately and disclosed: when
    the prior note carries no HUMAN regions, text outside the generated span
    is preserved and only the generated span is replaced, where the canonical
    core returns the fresh render (discarding that outside text).
    """
    if existing is None:
        try:
            return _canonical_merge_protected_regions(existing=None, rendered=rendered, path=path)
        except ProtectedRegionError as exc:
            raise GraphProjectionError(str(exc)) from exc

    if not _HUMAN_BEGIN.search(existing):
        # No HUMAN regions: keep the historical graph contract (preserve text
        # outside the generated span). The canonical core returns the fresh
        # render in this case, which would discard that text.
        _validate_protected_markers(existing, path=path)
        _validate_protected_markers(rendered, path=path)
        # This branch splices by hand instead of delegating to the canonical
        # core, so it never reaches the core's structural parse -- and the
        # marker validation above compares counts and names, never ORDER. A
        # rendered document whose HUMAN markers are reversed or crossed
        # therefore passed here and was WRITTEN, while the canonical core
        # refuses it as `unpaired`. Graph accepted what canonical rejected.
        try:
            reject_ambiguous_region_identity(rendered, path=path)
        except ProtectedRegionError as exc:
            raise GraphProjectionError(str(exc)) from exc
        existing_span = _generated_span(existing, path=path)
        if existing_span is None:
            return rendered
        rendered_span = _generated_span(rendered, path=path)
        if rendered_span is None:
            # The prior note has a generated span to replace but the fresh
            # render offers none. Refuse rather than fall back to overwriting
            # the whole document, which would discard the outside text this
            # branch exists to preserve.
            # The counts must name the artifact they describe. Borrowing the
            # note-shaped vocabulary here reported `begin=0,end=0` against the
            # OPERATOR'S path, telling them their note had no markers when it
            # has one of each -- the render is what lacks a span. Found by
            # verification.
            raise GraphProjectionError(
                f"malformed-generated-markers:rendered-has-no-generated-span,"
                f"rendered-begin={rendered.count(_GENERATED_START)},"
                f"rendered-end={rendered.count(_GENERATED_END)},"
                f"expected=1,no-write:{path}"
            )
        return (
            existing[: existing_span[0]]
            + rendered[rendered_span[0] : rendered_span[1]]
            + existing[existing_span[1] :]
        )

    try:
        return _canonical_merge_protected_regions(existing=existing, rendered=rendered, path=path)
    except ProtectedRegionError as exc:
        raise GraphProjectionError(str(exc)) from exc


def _frontmatter(*, project_id: str, projection: ProjectionName, source_state: str) -> str:
    payload = {
        "type": "GraphProjection",
        "title": f"{projection} — {project_id}",
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "project_id": project_id,
        "projection": projection,
        "authority_level": AUTHORITY_LEVEL,
        "derived_label": DERIVED_LABEL,
        "intelligence_label": INTELLIGENCE_LABEL,
        "source_state": source_state,
        "truth_boundary": TRUTH_BOUNDARY,
        "generated": True,
        "generated_by": GENERATED_BY,
    }
    # YAML-ish deterministic frontmatter without wall-clock timestamps.
    lines = ["---"]
    for key in sorted(payload):
        value = payload[key]
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        else:
            rendered = json.dumps(value, ensure_ascii=True)
        lines.append(f"{key}: {rendered}")
    lines.append("---")
    return "\n".join(lines)


def _human_stub() -> str:
    return "<!-- BEGIN HUMAN: notes -->\n<!-- END HUMAN: notes -->\n"


def render_relationships_markdown(
    relationships: Sequence[RelationshipRecord] | Sequence[Mapping[str, Any]],
    *,
    project_id: str,
) -> str:
    """Render ``relationships.md`` from retained relationship records only."""
    _assert_project_id(project_id)
    records = [_coerce_relationship(item) for item in relationships]
    records.sort(
        key=lambda item: (
            item.relationship_type,
            item.source_entity_id,
            item.target_entity_id,
            item.relationship_id,
        )
    )
    source_state: Literal["present", "absent"] = "present" if records else "absent"
    lines = [
        _frontmatter(project_id=project_id, projection="relationships", source_state=source_state),
        "",
        f"# Derived relationships — `{project_id}`",
        "",
        _GENERATED_START,
        "",
        f"> **{INTELLIGENCE_LABEL}**",
        ">",
        f"> Label: `{DERIVED_LABEL}` · authority: `{AUTHORITY_LEVEL}`",
        ">",
        f"> Truth boundary: `{TRUTH_BOUNDARY}`",
        ">",
        f"> Source package: `{SOURCE_RELATIONSHIP_PACKAGE}` (consume-only).",
        "",
    ]
    if not records:
        lines.extend(
            [
                "## Status",
                "",
                "No retained graph relationships are present for this project.",
                "No speculative relationship content is emitted.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "## Relationships",
                "",
                "| Type | Source | Target | Link quality | Relationship id | Fingerprint |",
                "|---|---|---|---|---|---|",
            ]
        )
        for record in records:
            rel_type = _redact_text(record.relationship_type)
            src = _redact_text(record.source_entity_id)
            tgt = _redact_text(record.target_entity_id)
            lq = _redact_text(record.link_quality)
            rid = _redact_text(record.relationship_id)
            fp = _redact_text(record.relationship_fingerprint[:16])
            lines.append(f"| `{rel_type}` | `{src}` | `{tgt}` | `{lq}` | `{rid}` | `{fp}` |")
        lines.append("")
        lines.extend(["## Source linkage", ""])
        for record in records:
            refs = record.provenance.get("graphify_artifact_refs", [])
            ref_bits: list[str] = []
            if isinstance(refs, list):
                for ref in refs:
                    if isinstance(ref, Mapping):
                        path = _redact_text(str(ref.get("relative_path", "")))
                        digest = _redact_text(str(ref.get("sha256", ""))[:16])
                        if path:
                            ref_bits.append(f"`{path}` (`{digest}…`)")
            joined = ", ".join(ref_bits) if ref_bits else "_none_"
            rid = _redact_text(record.relationship_id)
            lines.append(
                f"- `{rid}` · `{DERIVED_LABEL}` · artifacts: {joined}"
            )
        lines.append("")

    lines.extend([_GENERATED_END, "", _human_stub()])
    return "\n".join(lines)


def render_graph_health_markdown(
    health: GraphHealthSnapshot | Mapping[str, Any] | None,
    *,
    project_id: str,
) -> str:
    """Render ``graph-health.md`` from GRAPH-004 health counters (metadata only)."""
    _assert_project_id(project_id)
    snapshot = None if health is None else _coerce_health(health, project_id=project_id)
    source_state: Literal["present", "absent"] = "present" if snapshot is not None else "absent"
    lines = [
        _frontmatter(project_id=project_id, projection="graph-health", source_state=source_state),
        "",
        f"# Graph health — `{project_id}`",
        "",
        _GENERATED_START,
        "",
        f"> **{INTELLIGENCE_LABEL}**",
        ">",
        "> Operational counters only — not trust scores, not domain authority.",
        ">",
        f"> Truth boundary: `{TRUTH_BOUNDARY}` · also `GRAPH HEALTH ≠ PROJECT AUTHORITY`",
        ">",
        f"> Source package: `{SOURCE_HEALTH_PACKAGE}` (consume-only).",
        "",
    ]
    if snapshot is None:
        lines.extend(
            [
                "## Status",
                "",
                "No graph health snapshot is present for this project.",
                "No speculative health content is emitted.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "## Summary",
                "",
                f"- Health state: `{snapshot.health_state}`",
                f"- Retained relationships: `{snapshot.retained_count}`",
                f"- Quarantined records: `{snapshot.quarantined_count}`",
                f"- Input content hash: `{snapshot.input_content_hash[:16]}…`",
                "",
                "## Quarantine categories (metadata only)",
                "",
            ]
        )
        if not snapshot.category_counts:
            lines.append("_none_")
            lines.append("")
        else:
            lines.extend(["| Category | Count |", "|---|---|"])
            for category, count in sorted(snapshot.category_counts.items()):
                lines.append(f"| `{_redact_text(category)}` | `{count}` |")
            lines.append("")
        lines.extend(["## Link-quality histogram", ""])
        if not snapshot.link_quality_histogram:
            lines.append("_none_")
            lines.append("")
        else:
            lines.extend(["| Link quality | Count |", "|---|---|"])
            for quality, count in sorted(snapshot.link_quality_histogram.items()):
                lines.append(f"| `{_redact_text(quality)}` | `{count}` |")
            lines.append("")

    lines.extend([_GENERATED_END, "", _human_stub()])
    return "\n".join(lines)


def _coerce_relationship(
    item: RelationshipRecord | Mapping[str, Any],
) -> RelationshipRecord:
    if isinstance(item, RelationshipRecord):
        return item
    try:
        quality = str(item.get("link_quality", "inferred"))
        if quality not in {"verified", "supported", "inferred"}:
            raise GraphProjectionError("malformed-relationship-record")
        typed_quality: LinkQuality = cast(LinkQuality, quality)
        return RelationshipRecord(
            project_id=str(item["project_id"]),
            relationship_id=str(item["relationship_id"]),
            relationship_type=str(item["relationship_type"]),
            source_entity_id=str(item["source_entity_id"]),
            target_entity_id=str(item["target_entity_id"]),
            source_graphify_id=str(item.get("source_graphify_id", "")),
            target_graphify_id=str(item.get("target_graphify_id", "")),
            link_quality=typed_quality,
            relationship_fingerprint=str(item["relationship_fingerprint"]),
            provenance=dict(item.get("provenance", {})),
            extension_type=(
                str(item["extension_type"]) if item.get("extension_type") is not None else None
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise GraphProjectionError("malformed-relationship-record") from exc


def _coerce_health(
    item: GraphHealthSnapshot | Mapping[str, Any],
    *,
    project_id: str,
) -> GraphHealthSnapshot:
    if isinstance(item, GraphHealthSnapshot):
        if item.project_id != project_id:
            raise GraphProjectionError("health-project-mismatch")
        return item
    try:
        health_state = str(item["health_state"])
        allowed_health: set[str] = {"healthy", "degraded", "unhealthy", "unknown"}
        if health_state not in allowed_health:
            raise GraphProjectionError("malformed-health-snapshot")
        typed_health: HealthState = cast(HealthState, health_state)
        link_quality_raw = item.get("link_quality_histogram", {})
        return GraphHealthSnapshot(
            project_id=str(item.get("project_id", project_id)),
            retained_count=int(item["retained_count"]),
            quarantined_count=int(item["quarantined_count"]),
            category_counts={
                str(k): int(v) for k, v in dict(item.get("category_counts", {})).items()
            },
            link_quality_histogram={str(k): int(v) for k, v in dict(link_quality_raw).items()},
            health_state=typed_health,
            input_content_hash=str(item["input_content_hash"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise GraphProjectionError("malformed-health-snapshot") from exc


def materialize_projections(
    *,
    project_id: str,
    relationships: Sequence[RelationshipRecord] | Sequence[Mapping[str, Any]] = (),
    health: GraphHealthSnapshot | Mapping[str, Any] | None = None,
) -> ProjectionBundle:
    """Build both MVP projections from in-memory machine state."""
    _assert_project_id(project_id)
    records = [_coerce_relationship(item) for item in relationships]
    for record in records:
        if record.project_id != project_id:
            raise GraphProjectionError("relationship-project-mismatch")
    snapshot = None if health is None else _coerce_health(health, project_id=project_id)
    source_state: Literal["present", "absent"] = (
        "present" if records or snapshot is not None else "absent"
    )
    return ProjectionBundle(
        project_id=project_id,
        relationships_md=render_relationships_markdown(records, project_id=project_id),
        graph_health_md=render_graph_health_markdown(snapshot, project_id=project_id),
        relationship_count=len(records),
        health_present=snapshot is not None,
        source_state=source_state,
    )


def load_relationships_from_vault(vault: Path, *, project_id: str) -> list[RelationshipRecord]:
    """Load GRAPH-003 retained relationship JSON (consume-only; never mutate)."""
    _assert_project_id(project_id)
    root = vault.expanduser().resolve()
    directory = root / "generated" / "graph" / "relationships" / project_id
    if not directory.is_dir():
        return []
    records: list[RelationshipRecord] = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GraphProjectionError(f"malformed-relationship-json:{path.name}") from exc
        if not isinstance(payload, dict):
            raise GraphProjectionError(f"malformed-relationship-json:{path.name}")
        if payload.get("status") == "retained" or "relationship_id" in payload:
            records.append(_coerce_relationship(payload))
    return records


def load_health_from_vault(vault: Path, *, project_id: str) -> GraphHealthSnapshot | None:
    """Load GRAPH-004 health snapshot JSON (consume-only; never mutate)."""
    _assert_project_id(project_id)
    root = vault.expanduser().resolve()
    path = root / "generated" / "graph" / "health" / project_id / "health.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GraphProjectionError("malformed-health-json") from exc
    if not isinstance(payload, dict):
        raise GraphProjectionError("malformed-health-json")
    return _coerce_health(payload, project_id=project_id)


def materialize_projections_from_vault(vault: Path, *, project_id: str) -> ProjectionBundle:
    """Load machine graph state from vault and render MVP projections."""
    relationships = load_relationships_from_vault(vault, project_id=project_id)
    health = load_health_from_vault(vault, project_id=project_id)
    return materialize_projections(
        project_id=project_id,
        relationships=relationships,
        health=health,
    )


def _safe_vault_relative(vault: Path, relative: str) -> Path:
    if relative.startswith(("/", "\\")) or "\\" in relative or ".." in Path(relative).parts:
        raise GraphProjectionError(f"path-escape:{relative}")
    if not any(relative.startswith(prefix) for prefix in ALLOWED_WRITE_PREFIXES):
        raise GraphProjectionError(f"forbidden-write-prefix:{relative}")
    if any(relative.startswith(prefix) for prefix in _FORBIDDEN_WRITE_PREFIXES):
        raise GraphProjectionError(f"forbidden-write-prefix:{relative}")
    root = vault.expanduser().resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise GraphProjectionError(f"path-escape:{relative}")
    return candidate


def _promote(plan: dict[Path, bytes]) -> None:
    """Prepare → validate-staged → promote with rollback (failed promote leaves prior)."""
    transaction = uuid4().hex
    entries: list[_PromotionEntry] = []
    try:
        for path in sorted(plan):
            # Same third failure site as `obsidian_projection._write_atomic`:
            # a blocked or unwritable parent escaped as a raw OSError, outside
            # `GraphProjectionError`, so a caller catching the domain error did
            # not catch this at all.
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise GraphProjectionError(
                    f"unwritable-note-directory:{type(exc).__name__}:{path.parent}"
                ) from exc
            if path.exists() and not path.is_file():
                raise GraphProjectionError(f"canonical-target-not-file:{path}")
            if path.is_file() and path.read_bytes() == plan[path]:
                continue
            staged = path.with_name(f".{path.name}.{transaction}.atlas-stage")
            backup = path.with_name(f".{path.name}.{transaction}.atlas-backup")
            staged.write_bytes(plan[path])
            entries.append(
                _PromotionEntry(
                    path=path,
                    staged=staged,
                    backup=backup,
                    had_original=path.exists(),
                )
            )
    except BaseException:
        for entry in entries:
            entry.staged.unlink(missing_ok=True)
            entry.backup.unlink(missing_ok=True)
        raise

    touched: list[_PromotionEntry] = []
    try:
        for entry in entries:
            if entry.had_original:
                _replace_path(entry.path, entry.backup)
            touched.append(entry)
            _replace_path(entry.staged, entry.path)
    except BaseException as promotion_error:
        for entry in reversed(touched):
            with contextlib.suppress(OSError):
                if entry.had_original:
                    os.replace(entry.backup, entry.path)
                else:
                    entry.path.unlink(missing_ok=True)
        for entry in entries:
            with contextlib.suppress(OSError):
                entry.staged.unlink(missing_ok=True)
                entry.backup.unlink(missing_ok=True)
        raise GraphProjectionError("promotion-failed-prior-state-intact") from promotion_error

    for entry in entries:
        entry.staged.unlink(missing_ok=True)
        entry.backup.unlink(missing_ok=True)


def write_projection_outputs(
    bundle: ProjectionBundle,
    *,
    vault: Path,
) -> list[str]:
    """Deterministic vault emits under ``generated/graph/projections/`` only.

    Preserves HUMAN protected regions byte-for-byte (AT-011 fail-closed).
    Failed promote leaves prior projection bytes intact.
    """
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise GraphProjectionError(f"vault-missing:{vault}")

    project = bundle.project_id
    _assert_project_id(project)

    mapping = {
        f"generated/graph/projections/{project}/graph-health.md": bundle.graph_health_md,
        f"generated/graph/projections/{project}/relationships.md": bundle.relationships_md,
    }
    planned = sorted(mapping)

    plan: dict[Path, bytes] = {}
    for relative, rendered in sorted(mapping.items()):
        path = _safe_vault_relative(vault, relative)
        try:
            existing = read_note_text(path) if path.is_file() else None
        except (OSError, UnicodeError) as exc:
            # Same boundary as above: the read happens while building `plan`,
            # before `_promote`, so nothing has been written yet -- but a raw
            # exception here escaped `GraphProjectionError`, so a caller
            # catching that did not catch this at all.
            raise GraphProjectionError(
                f"unreadable-existing-note:{type(exc).__name__}:{relative}"
            ) from exc
        merged = _merge_protected_regions(existing=existing, rendered=rendered, path=relative)
        plan[path] = merged.encode("utf-8")

    _promote(plan)
    return planned


def promote_projection_path_forbidden(relative: str) -> None:
    """Public helper for tests: assert a relative path is rejected by path policy."""
    _safe_vault_relative(Path("."), relative)


def promote_projection_to_authority_forbidden() -> None:
    """Fail-closed: projections never elevate to domain authority."""
    raise GraphProjectionError("authority-elevation-forbidden")


def promote_projection_to_claim_forbidden() -> None:
    """Fail-closed: projections never write claim stores."""
    raise GraphProjectionError("claim-synthesis-forbidden")


def inspect_projections(bundle: ProjectionBundle) -> dict[str, Any]:
    """Library observability: counts only; no secret payloads."""
    return {
        "package_id": PACKAGE_ID,
        "project_id": bundle.project_id,
        "authority": AUTHORITY_LEVEL,
        "relationship_count": bundle.relationship_count,
        "health_present": bundle.health_present,
        "source_state": bundle.source_state,
        "truth_boundary": TRUTH_BOUNDARY,
    }


__all__ = [
    "ALLOWED_WRITE_PREFIXES",
    "AUTHORITY_LEVEL",
    "DERIVED_LABEL",
    "GENERATED_BY",
    "INTELLIGENCE_LABEL",
    "PACKAGE_ID",
    "TRUTH_BOUNDARY",
    "GraphProjectionError",
    "ProjectionBundle",
    "inspect_projections",
    "load_health_from_vault",
    "load_relationships_from_vault",
    "materialize_projections",
    "materialize_projections_from_vault",
    "promote_projection_path_forbidden",
    "promote_projection_to_authority_forbidden",
    "promote_projection_to_claim_forbidden",
    "render_graph_health_markdown",
    "render_relationships_markdown",
    "write_projection_outputs",
]
