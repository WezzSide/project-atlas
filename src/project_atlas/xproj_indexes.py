"""AS-XPROJ-004 — Cross-project conflict intelligence + global derived indexes.

Builds deterministic, derived-only indexes and conflict reports from the
XPROJ-001 global entity registry and XPROJ-002 cross-project edges.

Never rewrites AS-RET-001 lexical indexes (``generated/indexes/``), never
elevates authority, never auto-resolves conflicts, never mutates claims, and
never dual-owns GRAPH-005 human projections or XPROJ-003 duplicate-candidates.

Truth boundary: CROSS-PROJECT INDEX ≠ AUTOMATIC AUTHORITY
(AS-XPROJ-INV-TRUTH-001).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from project_atlas.schema import validate_record
from project_atlas.secrets import scan_text
from project_atlas.xproj_edges import GlobalEdgeRecord, load_edge_registry_state
from project_atlas.xproj_registry import (
    GlobalEntityRecord,
    JoinKeyRecord,
    load_registry_state,
)

PACKAGE_ID = "AS-XPROJ-004"
AUTHORITY_LEVEL = "derived"
TRUTH_BOUNDARY = "CROSS-PROJECT INDEX ≠ AUTOMATIC AUTHORITY"


def _safe_index_text(value: str | None) -> str | None:
    """Return persist-safe text; decoded secret-shaped values are omitted.

    AS-SEC-SCAN-XPROJ-INDEX-DISPLAY-JSON-ESC-001: json.loads of global-entity
    records can decode ``\\u`` display names that scan_text misses on raw bytes.
    """
    if value is None:
        return None
    text = str(value)
    if not text or scan_text(text):
        return None
    return text


def _safe_id_tuple(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {item for item in values if _safe_index_text(item) is not None},
            key=str.casefold,
        )
    )

ALLOWED_WRITE_PREFIXES: tuple[str, ...] = (
    "generated/xproj/indexes/",
    "generated/xproj/conflicts/",
)

_FORBIDDEN_WRITE_PREFIXES: tuple[str, ...] = (
    "relationships/",
    "state/current-state/",
    "state/authoritative-state/",
    "claims/",
    "generated/indexes/",
    "generated/query/",
    "generated/graph/",
    "generated/xproj/duplicate-candidates/",
    "generated/xproj/edges/",
    "state/global-entities/",
)

INDEX_BUCKETS: tuple[str, ...] = (
    "projects",
    "technologies",
    "components",
    "services",
    "agents",
    "skills",
    "work-packages",
    "decisions",
    "risks",
    "relationships",
)

_ENTITY_CLASS_TO_BUCKET: dict[str, str] = {
    "technology": "technologies",
    "service": "services",
    "library": "components",
    "infrastructure": "components",
    "environment": "components",
    "external-api": "services",
    "organization": "projects",
    "extension": "components",
}

ConflictKind = Literal[
    "explicit-conflicts-with",
    "version-divergence",
]

_SAFE_RE = re.compile(r"[^A-Za-z0-9._:-]+")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class XprojIndexError(ValueError):
    """Fail-closed XPROJ index / conflict-intelligence error."""


@dataclass(frozen=True)
class EvidenceLink:
    """Metadata-only evidence pointer (no secret content)."""

    relative_path: str
    sha256: str
    kind: str = "source"

    def as_dict(self) -> dict[str, str]:
        path = _safe_index_text(self.relative_path) or "UNKNOWN"
        return {
            "relative_path": path,
            "sha256": self.sha256,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class ConflictReport:
    """Derived conflict intelligence — never includes winning_choice."""

    conflict_id: str
    kind: ConflictKind
    summary: str
    global_entity_ids: tuple[str, ...]
    project_ids: tuple[str, ...]
    evidence_links: tuple[EvidenceLink, ...]
    edge_ids: tuple[str, ...] = ()
    display_name: str | None = None
    entity_class: str | None = None
    versions: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "package_id": PACKAGE_ID,
            "conflict_id": self.conflict_id,
            "kind": self.kind,
            "summary": self.summary,
            "global_entity_ids": list(_safe_id_tuple(self.global_entity_ids)),
            "project_ids": list(_safe_id_tuple(self.project_ids)),
            "evidence_links": [item.as_dict() for item in self.evidence_links],
            "edge_ids": list(_safe_id_tuple(self.edge_ids)),
            "authority": {
                "level": AUTHORITY_LEVEL,
                "note": (
                    "XPROJ conflict intelligence is derived portfolio reporting; "
                    "never auto-resolves; never claim/authority truth."
                ),
            },
            "status": "reported",
            "truth_boundary": TRUTH_BOUNDARY,
            "resolution": {
                "auto_resolve": False,
                "winning_choice": None,
            },
        }
        if self.display_name is not None:
            payload["display_name"] = self.display_name
        if self.entity_class is not None:
            payload["entity_class"] = self.entity_class
        if self.versions:
            payload["versions"] = list(_safe_id_tuple(self.versions))
        return payload

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n"


@dataclass(frozen=True)
class IndexDocument:
    """One deterministic global index bucket document."""

    bucket: str
    entries: tuple[dict[str, Any], ...]
    content_fingerprint: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "package_id": PACKAGE_ID,
            "bucket": self.bucket,
            "entry_count": len(self.entries),
            "entries": list(self.entries),
            "content_fingerprint": self.content_fingerprint,
            "authority": {
                "level": AUTHORITY_LEVEL,
                "note": (
                    "XPROJ global indexes are derived; not AS-RET-001 lexical "
                    "indexes and not claim/authority truth."
                ),
            },
            "status": "built",
            "truth_boundary": TRUTH_BOUNDARY,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n"


@dataclass
class XprojIndexBuildResult:
    """In-memory build outcome (deterministic ordering on emit)."""

    indexes: list[IndexDocument] = field(default_factory=list)
    conflicts: list[ConflictReport] = field(default_factory=list)

    @property
    def index_count(self) -> int:
        return len(self.indexes)

    @property
    def conflict_count(self) -> int:
        return len(self.conflicts)


def _safe_name(token: str) -> str:
    cleaned = _SAFE_RE.sub("-", token.strip()).strip("-._:")
    return cleaned[:120] or "unnamed"


def _emit_filename(token: str) -> str:
    return f"{_safe_name(token)}.json"


def _fingerprint(payload: Mapping[str, Any] | Sequence[Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _projects_for_global(
    global_entity_id: str, joins: Sequence[JoinKeyRecord]
) -> tuple[str, ...]:
    projects = sorted(
        {
            join.project_id
            for join in joins
            if join.global_entity_id == global_entity_id and join.status == "joined"
        },
        key=str.casefold,
    )
    return tuple(projects)


def _version_of(entity: GlobalEntityRecord) -> str | None:
    if entity.attributes is None:
        return None
    raw = entity.attributes.get("version")
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    return _safe_index_text(text)


def _evidence_from_joins(
    global_entity_ids: Sequence[str], joins: Sequence[JoinKeyRecord]
) -> tuple[EvidenceLink, ...]:
    links: list[EvidenceLink] = []
    seen: set[tuple[str, str]] = set()
    wanted = set(global_entity_ids)
    for join in sorted(
        joins,
        key=lambda item: (
            item.global_entity_id.casefold(),
            item.project_id.casefold(),
            item.project_local_entity_id.casefold(),
        ),
    ):
        if join.global_entity_id not in wanted:
            continue
        for ref in join.evidence_refs:
            key = (ref.relative_path, ref.sha256)
            if key in seen:
                continue
            if not _SHA256_RE.fullmatch(ref.sha256):
                continue
            seen.add(key)
            links.append(
                EvidenceLink(
                    relative_path=ref.relative_path,
                    sha256=ref.sha256,
                    kind="join",
                )
            )
    return tuple(links)


def _evidence_from_edges(edges: Sequence[GlobalEdgeRecord]) -> tuple[EvidenceLink, ...]:
    links: list[EvidenceLink] = []
    seen: set[tuple[str, str]] = set()
    for edge in sorted(edges, key=lambda item: item.edge_id.casefold()):
        for ref in edge.evidence_refs:
            key = (ref.relative_path, ref.sha256)
            if key in seen:
                continue
            if not _SHA256_RE.fullmatch(ref.sha256):
                continue
            seen.add(key)
            links.append(
                EvidenceLink(
                    relative_path=ref.relative_path,
                    sha256=ref.sha256,
                    kind="edge",
                )
            )
    return tuple(links)


def detect_explicit_conflicts(
    edges: Sequence[GlobalEdgeRecord],
    joins: Sequence[JoinKeyRecord],
) -> list[ConflictReport]:
    """Surface ``conflicts-with`` edges as derived reports (no claim synthesis)."""
    reports: list[ConflictReport] = []
    for edge in sorted(edges, key=lambda item: item.edge_id.casefold()):
        if edge.relationship_type != "conflicts-with":
            continue
        entity_ids = _safe_id_tuple(
            [edge.source_global_entity_id, edge.target_global_entity_id]
        )
        projects = _safe_id_tuple(
            [
                *_projects_for_global(edge.source_global_entity_id, joins),
                *_projects_for_global(edge.target_global_entity_id, joins),
            ]
        )
        safe_edge = _safe_index_text(edge.edge_id) or "UNKNOWN"
        conflict_id = f"xc-explicit-{_safe_name(safe_edge)}"
        reports.append(
            ConflictReport(
                conflict_id=conflict_id,
                kind="explicit-conflicts-with",
                summary=(
                    "Explicit cross-project conflicts-with edge reported "
                    "(derived-only; no auto-resolve)."
                ),
                global_entity_ids=entity_ids,
                project_ids=projects,
                evidence_links=_evidence_from_edges([edge]),
                edge_ids=(edge.edge_id,),
            )
        )
    reports.sort(key=lambda item: item.conflict_id.casefold())
    return reports


def detect_version_divergence(
    entities: Mapping[str, GlobalEntityRecord] | Sequence[GlobalEntityRecord],
    joins: Sequence[JoinKeyRecord],
) -> list[ConflictReport]:
    """Report multi-ID same-name/class families spanning ≥2 projects.

    This is intelligence only: it never collapses IDs and never picks a winner.
    """
    entity_list = (
        list(entities.values()) if isinstance(entities, Mapping) else list(entities)
    )

    families: dict[tuple[str, str], list[GlobalEntityRecord]] = {}
    for entity in entity_list:
        display = _safe_index_text(entity.display_name)
        gid = _safe_index_text(entity.global_entity_id)
        if display is None or gid is None:
            continue
        key = (display.casefold(), entity.entity_class)
        families.setdefault(key, []).append(entity)

    reports: list[ConflictReport] = []
    for (name_key, entity_class), members in sorted(
        families.items(), key=lambda item: (item[0][0], item[0][1])
    ):
        if len(members) < 2:
            continue
        members_sorted = sorted(members, key=lambda item: item.global_entity_id.casefold())
        entity_ids = _safe_id_tuple(item.global_entity_id for item in members_sorted)
        project_ids = _safe_id_tuple(
            project
            for gid in entity_ids
            for project in _projects_for_global(gid, joins)
        )
        if len(project_ids) < 2:
            continue
        versions = tuple(
            sorted(
                {
                    version
                    for item in members_sorted
                    if (version := _version_of(item)) is not None
                },
                key=str.casefold,
            )
        )
        # Require either explicit version attributes that differ, or ≥2 IDs
        # with multi-project joins under the same display_name+class family.
        if versions and len(versions) < 2 and len(entity_ids) < 2:
            continue
        display_name = _safe_index_text(members_sorted[0].display_name)
        conflict_id = (
            f"xc-version-{_safe_name(entity_class)}-{_safe_name(name_key)}"
        )
        reports.append(
            ConflictReport(
                conflict_id=conflict_id,
                kind="version-divergence",
                summary=(
                    "Divergent global entities share display_name+class across "
                    "projects (derived report; no merge)."
                ),
                global_entity_ids=entity_ids,
                project_ids=project_ids,
                evidence_links=_evidence_from_joins(entity_ids, joins),
                display_name=display_name,
                entity_class=entity_class,
                versions=versions,
            )
        )
    reports.sort(key=lambda item: item.conflict_id.casefold())
    return reports


def _project_index_entries(joins: Sequence[JoinKeyRecord]) -> list[dict[str, Any]]:
    by_project: dict[str, set[str]] = {}
    for join in joins:
        if join.status != "joined":
            continue
        by_project.setdefault(join.project_id, set()).add(join.global_entity_id)
    entries: list[dict[str, Any]] = []
    for project_id in sorted(by_project, key=str.casefold):
        if _safe_index_text(project_id) is None:
            continue
        globals_sorted = [
            gid
            for gid in sorted(by_project[project_id], key=str.casefold)
            if _safe_index_text(gid) is not None
        ]
        entries.append(
            {
                "project_id": project_id,
                "global_entity_ids": globals_sorted,
                "global_entity_count": len(globals_sorted),
            }
        )
    return entries


def _entity_bucket_entries(
    bucket: str,
    entities: Sequence[GlobalEntityRecord],
    joins: Sequence[JoinKeyRecord],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for entity in sorted(entities, key=lambda item: item.global_entity_id.casefold()):
        mapped = _ENTITY_CLASS_TO_BUCKET.get(entity.entity_class)
        if mapped != bucket:
            continue
        gid = _safe_index_text(entity.global_entity_id)
        if gid is None:
            continue
        projects = [
            project
            for project in _projects_for_global(entity.global_entity_id, joins)
            if _safe_index_text(project) is not None
        ]
        entry: dict[str, Any] = {
            "global_entity_id": gid,
            "entity_class": entity.entity_class,
            "display_name": _safe_index_text(entity.display_name),
            "project_ids": projects,
        }
        version = _version_of(entity)
        if version is not None:
            entry["version"] = version
        entries.append(entry)
    return entries


def _relationship_index_entries(edges: Sequence[GlobalEdgeRecord]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for edge in sorted(edges, key=lambda item: item.edge_id.casefold()):
        entries.append(
            {
                "edge_id": _safe_index_text(edge.edge_id) or "UNKNOWN",
                "relationship_type": edge.relationship_type,
                "source_global_entity_id": _safe_index_text(edge.source_global_entity_id),
                "target_global_entity_id": _safe_index_text(edge.target_global_entity_id),
                "source_project_ids": list(_safe_id_tuple(edge.source_project_ids)),
                "target_project_ids": list(_safe_id_tuple(edge.target_project_ids)),
            }
        )
    return entries


def build_index_documents(
    *,
    entities: Mapping[str, GlobalEntityRecord] | Sequence[GlobalEntityRecord],
    joins: Sequence[JoinKeyRecord],
    edges: Sequence[GlobalEdgeRecord],
) -> list[IndexDocument]:
    """Build all frozen index buckets deterministically (empty buckets retained)."""
    entity_list = (
        list(entities.values()) if isinstance(entities, Mapping) else list(entities)
    )

    documents: list[IndexDocument] = []
    for bucket in INDEX_BUCKETS:
        if bucket == "projects":
            entries = _project_index_entries(joins)
        elif bucket == "relationships":
            entries = _relationship_index_entries(edges)
        elif bucket in {"agents", "skills", "work-packages", "decisions", "risks"}:
            # Optional until modeled — emit empty deterministic documents.
            entries = []
        else:
            entries = _entity_bucket_entries(bucket, entity_list, joins)
        fingerprint = _fingerprint({"bucket": bucket, "entries": entries})
        documents.append(
            IndexDocument(
                bucket=bucket,
                entries=tuple(entries),
                content_fingerprint=fingerprint,
            )
        )
    return documents


def build_xproj_indexes(
    *,
    entities: Mapping[str, GlobalEntityRecord] | Sequence[GlobalEntityRecord] | None = None,
    joins: Sequence[JoinKeyRecord] | None = None,
    edges: Sequence[GlobalEdgeRecord] | None = None,
    vault: Path | None = None,
) -> XprojIndexBuildResult:
    """Compile derived indexes + conflict reports (in-memory or from vault state)."""
    if vault is not None:
        loaded_entities, loaded_joins = load_registry_state(vault)
        loaded_edges = load_edge_registry_state(vault)
        entity_map = loaded_entities
        join_list = loaded_joins
        edge_list = loaded_edges
    else:
        if entities is None or joins is None:
            raise XprojIndexError("entities-and-joins-required-without-vault")
        entity_map = (
            dict(entities)
            if isinstance(entities, Mapping)
            else {item.global_entity_id: item for item in entities}
        )
        join_list = list(joins)
        edge_list = list(edges or ())

    indexes = build_index_documents(entities=entity_map, joins=join_list, edges=edge_list)
    conflicts = [
        *detect_explicit_conflicts(edge_list, join_list),
        *detect_version_divergence(entity_map, join_list),
    ]
    conflicts.sort(key=lambda item: item.conflict_id.casefold())
    return XprojIndexBuildResult(indexes=indexes, conflicts=conflicts)


def inspect_xproj_indexes(result: XprojIndexBuildResult) -> dict[str, Any]:
    """Compact deterministic summary (stdout-friendly)."""
    return {
        "package_id": PACKAGE_ID,
        "authority_level": AUTHORITY_LEVEL,
        "truth_boundary": TRUTH_BOUNDARY,
        "index_count": result.index_count,
        "conflict_count": result.conflict_count,
        "buckets": [doc.bucket for doc in result.indexes],
        "conflict_ids": [item.conflict_id for item in result.conflicts],
    }


def _safe_vault_relative(vault: Path, relative: str) -> Path:
    normalized = relative.replace("\\", "/")
    if (
        normalized.startswith("/")
        or normalized.startswith("../")
        or "/../" in normalized
        or normalized.endswith("/..")
    ):
        raise XprojIndexError(f"path-escape:{normalized}")
    if not any(normalized.startswith(prefix) for prefix in ALLOWED_WRITE_PREFIXES):
        raise XprojIndexError(f"write-prefix-forbidden:{normalized}")
    if any(normalized.startswith(prefix) for prefix in _FORBIDDEN_WRITE_PREFIXES):
        raise XprojIndexError(f"write-prefix-forbidden:{normalized}")
    target = (vault / normalized).resolve()
    vault_resolved = vault.resolve()
    if not target.is_relative_to(vault_resolved):
        raise XprojIndexError(f"path-escape:{normalized}")
    return target


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".xproj004-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(tmp_name, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)


def write_xproj_index_outputs(
    result: XprojIndexBuildResult,
    *,
    vault: Path,
    validate: bool = True,
) -> list[str]:
    """Promote index + conflict documents under frozen ``generated/xproj/**`` paths."""
    written: list[str] = []
    for document in sorted(result.indexes, key=lambda item: item.bucket.casefold()):
        payload = document.as_dict()
        if validate:
            validate_record(payload, "xproj-index-document")
        relative = f"generated/xproj/indexes/{document.bucket}/index.json"
        target = _safe_vault_relative(vault, relative)
        _write_atomic(target, document.to_json())
        written.append(relative)
    for report in sorted(result.conflicts, key=lambda item: item.conflict_id.casefold()):
        payload = report.as_dict()
        if validate:
            validate_record(payload, "xproj-conflict-report")
        relative = f"generated/xproj/conflicts/{_emit_filename(report.conflict_id)}"
        target = _safe_vault_relative(vault, relative)
        _write_atomic(target, report.to_json())
        written.append(relative)
    written.sort(key=str.casefold)
    return written


def promote_xproj_index_path_forbidden(relative: str) -> None:
    """Raise if ``relative`` is outside the owned emit surface (path-policy ADV)."""
    normalized = relative.replace("\\", "/")
    if not any(normalized.startswith(prefix) for prefix in ALLOWED_WRITE_PREFIXES):
        raise XprojIndexError(f"write-prefix-forbidden:{normalized}")
    if any(normalized.startswith(prefix) for prefix in _FORBIDDEN_WRITE_PREFIXES):
        raise XprojIndexError(f"write-prefix-forbidden:{normalized}")
    if (
        normalized.startswith("/")
        or "\\" in relative
        or normalized.startswith("../")
        or "/../" in normalized
        or normalized.endswith("/..")
    ):
        raise XprojIndexError(f"path-escape:{normalized}")


def claims_authority_paths_untouched() -> tuple[str, ...]:
    """Documented non-write surfaces for ADV assertions."""
    return (
        "claims/",
        "state/current-state/",
        "state/authoritative-state/",
        "generated/indexes/",
        "generated/graph/",
        "generated/xproj/duplicate-candidates/",
    )


__all__ = [
    "ALLOWED_WRITE_PREFIXES",
    "AUTHORITY_LEVEL",
    "INDEX_BUCKETS",
    "PACKAGE_ID",
    "TRUTH_BOUNDARY",
    "ConflictReport",
    "EvidenceLink",
    "IndexDocument",
    "XprojIndexBuildResult",
    "XprojIndexError",
    "build_index_documents",
    "build_xproj_indexes",
    "claims_authority_paths_untouched",
    "detect_explicit_conflicts",
    "detect_version_divergence",
    "inspect_xproj_indexes",
    "promote_xproj_index_path_forbidden",
    "write_xproj_index_outputs",
]
