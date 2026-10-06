"""AS-GRAPH-003 — Canonical derived relationship / edge store.

Normalizes accepted Graphify edges against AS-GRAPH-002 resolved endpoints into
provenance-backed relationship records permanently marked ``authority: derived``.

Never writes claims, temporal/authoritative state, knowledge-query caches, or
Control Plane ``relationships/``. Never invents Core claim conflicts from
graph ``conflicts-with``. Never last-write-wins on incompatible fingerprints.

Truth boundary: GRAPH RELATIONSHIP ≠ AUTOMATIC AUTHORITY (AS-GRAPH-INV-TRUTH-001).
Link-quality ``verified`` ≠ domain-authoritative ≠ knowledge-query authoritative.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from project_atlas.graph_acceptance import AcceptanceReceipt
from project_atlas.graph_resolution import (
    ResolutionResult,
    ResolvedNode,
    resolve_from_acceptance,
)
from project_atlas.schema import validate_record
from project_atlas.secrets import scan_text

if TYPE_CHECKING:
    from project_atlas.graph_quarantine import QuarantineStoreResult

PACKAGE_ID = "AS-GRAPH-003"
AUTHORITY_LEVEL = "derived"
TRUTH_BOUNDARY = "GRAPH RELATIONSHIP ≠ AUTOMATIC AUTHORITY"

# ADV-G3-030 / G3-FX-010 — structured capacity fail-closed (never silent subset).
DEFAULT_MAX_EDGES = 50_000

MVP_RELATIONSHIP_TYPES: frozenset[str] = frozenset(
    {
        "part-of",
        "depends-on",
        "documents",
        "validates",
        "supersedes",
        "derived-from",
        "conflicts-with",
    }
)

_TYPE_ALIASES: dict[str, str] = {
    "part_of": "part-of",
    "partof": "part-of",
    "depends_on": "depends-on",
    "dependson": "depends-on",
    "derived_from": "derived-from",
    "derivedfrom": "derived-from",
    "conflicts_with": "conflicts-with",
    "conflictswith": "conflicts-with",
}

LinkQuality = Literal["verified", "supported", "inferred"]
QuarantineCategory = Literal[
    "orphaned-endpoint",
    "quarantined-endpoint",
    "cross-project-endpoint",
    "incompatible-duplicate",
    "capacity-rejected",
    "malformed-edge",
]

ALLOWED_WRITE_PREFIXES: tuple[str, ...] = (
    "generated/graph/relationships/",
    "generated/graph/relationship-quarantine/",
)

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
)


class GraphRelationshipError(ValueError):
    """Fail-closed graph relationship store error (metadata-only message)."""


@dataclass(frozen=True)
class ArtifactRef:
    relative_path: str
    sha256: str

    def as_dict(self) -> dict[str, str]:
        return {"relative_path": self.relative_path, "sha256": self.sha256}


@dataclass(frozen=True)
class RelationshipRecord:
    """Canonical derived relationship (promoted store record)."""

    project_id: str
    relationship_id: str
    relationship_type: str
    source_entity_id: str
    target_entity_id: str
    source_graphify_id: str
    target_graphify_id: str
    link_quality: LinkQuality
    relationship_fingerprint: str
    provenance: dict[str, Any]
    extension_type: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "package_id": PACKAGE_ID,
            "project_id": self.project_id,
            "relationship_id": self.relationship_id,
            "relationship_type": self.relationship_type,
            "source_entity_id": self.source_entity_id,
            "target_entity_id": self.target_entity_id,
            "source_graphify_id": _safe_graphify_id(self.source_graphify_id),
            "target_graphify_id": _safe_graphify_id(self.target_graphify_id),
            "link_quality": self.link_quality,
            "relationship_fingerprint": self.relationship_fingerprint,
            "authority": {
                "level": AUTHORITY_LEVEL,
                "note": (
                    "Graph relationship is derived-only; "
                    "link-quality verified ≠ domain-authoritative."
                ),
            },
            "status": "retained",
            "provenance": _safe_provenance(self.provenance),
            "truth_boundary": TRUTH_BOUNDARY,
        }
        if self.relationship_type == "extension":
            assert self.extension_type is not None
            payload["extension_type"] = self.extension_type
        return payload

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n"


@dataclass(frozen=True)
class RelationshipQuarantine:
    """Soft quarantine candidate (handoff AS-GRAPH-004; never LWW promote).

    Durable persistence, health counters, and incremental hash/state live in
    ``project_atlas.graph_quarantine`` (AS-GRAPH-004). This type remains the
    GRAPH-003 soft-candidate shape only — never elevate to authority.
    """

    project_id: str
    candidate_id: str
    category: QuarantineCategory
    reason: str
    relationship_fingerprint: str | None = None
    graphify_edge_ids: tuple[str, ...] = ()
    source_graphify_id: str | None = None
    target_graphify_id: str | None = None
    artifact_refs: tuple[ArtifactRef, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "package_id": PACKAGE_ID,
            "project_id": self.project_id,
            "candidate_id": self.candidate_id,
            "category": self.category,
            "authority": {
                "level": AUTHORITY_LEVEL,
                "note": "Quarantine candidate is derived-only; never claim synthesis.",
            },
            "status": "quarantine_candidate",
            "reason": self.reason,
            "truth_boundary": TRUTH_BOUNDARY,
        }
        if self.relationship_fingerprint is not None:
            payload["relationship_fingerprint"] = self.relationship_fingerprint
        if self.graphify_edge_ids:
            payload["graphify_edge_ids"] = _safe_graphify_ids(self.graphify_edge_ids)
        if self.source_graphify_id is not None:
            payload["source_graphify_id"] = _safe_graphify_id(self.source_graphify_id)
        if self.target_graphify_id is not None:
            payload["target_graphify_id"] = _safe_graphify_id(self.target_graphify_id)
        if self.artifact_refs:
            payload["artifact_refs"] = [item.as_dict() for item in self.artifact_refs]
        return payload

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n"


@dataclass(frozen=True)
class RelationshipStoreResult:
    """Deterministic batch relationship-store output."""

    project_id: str
    relationships: tuple[RelationshipRecord, ...]
    quarantine: tuple[RelationshipQuarantine, ...]
    link_quality_histogram: dict[str, int]
    errors: tuple[dict[str, str], ...] = ()

    @property
    def retained_count(self) -> int:
        return len(self.relationships)

    @property
    def quarantined_count(self) -> int:
        return len(self.quarantine)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "package_id": PACKAGE_ID,
            "project_id": self.project_id,
            "authority": {"level": AUTHORITY_LEVEL},
            "retained_count": self.retained_count,
            "quarantined_count": self.quarantined_count,
            "link_quality_histogram": dict(sorted(self.link_quality_histogram.items())),
            "relationships": [item.as_dict() for item in self.relationships],
            "quarantine": [item.as_dict() for item in self.quarantine],
            "errors": list(self.errors),
            "truth_boundary": TRUTH_BOUNDARY,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n"


@dataclass
class _EdgeMaterial:
    """Internal edge candidate before collapse / quarantine."""

    graphify_edge_id: str
    record_index: int
    raw_type: str
    relationship_type: str
    extension_type: str | None
    source_graphify_id: str
    target_graphify_id: str
    source_entity_id: str | None
    target_entity_id: str | None
    source_docs: tuple[str, ...]
    confidence: str | None
    artifact_refs: tuple[ArtifactRef, ...]
    material_fingerprint: str
    reject_category: QuarantineCategory | None = None
    reject_reason: str | None = None
    force_incompatible: bool = False


@dataclass
class _CollapseBucket:
    materials: list[_EdgeMaterial] = field(default_factory=list)


def _safe_graphify_id(raw: str | None) -> str:
    """Omit decoded secret-shaped Graphify ids from persist payloads.

    AS-SEC-SCAN-GRAPH-REL-GID-JSON-ESC-001: json.loads of Graphify
    nodes/edges can decode ``\\u`` ids that scan_text misses on raw bytes.
    """
    text = str(raw or "").strip()
    if not text or scan_text(text):
        return "UNKNOWN"
    return text


def _safe_graphify_ids(values: Sequence[Any]) -> list[str]:
    return [_safe_graphify_id(str(item)) for item in values]


def _safe_provenance(provenance: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(provenance)
    for key in ("source_graphify_ids", "target_graphify_ids", "graphify_edge_ids"):
        raw = payload.get(key)
        if isinstance(raw, list):
            payload[key] = _safe_graphify_ids(raw)
    return payload


def normalize_relationship_type(raw: str | None) -> tuple[str, str | None]:
    """Map raw Graphify type to MVP type or extension (no silent remap)."""
    if raw is None or not str(raw).strip():
        return "extension", "unknown"
    text = str(raw).strip().lower().replace(" ", "-")
    aliased = _TYPE_ALIASES.get(text.replace("_", "-"), _TYPE_ALIASES.get(text, text))
    if aliased in MVP_RELATIONSHIP_TYPES:
        return aliased, None
    return "extension", str(raw).strip()


def relationship_fingerprint(
    *,
    relationship_type: str,
    source_entity_id: str,
    target_entity_id: str,
) -> str:
    """Deterministic collapse fingerprint (type + ordered endpoints)."""
    payload = {
        "relationship_type": relationship_type,
        "source_entity_id": source_entity_id,
        "target_entity_id": target_entity_id,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _material_fingerprint(material: _EdgeMaterial) -> str:
    payload = {
        "relationship_type": material.relationship_type,
        "extension_type": material.extension_type,
        "source_entity_id": material.source_entity_id,
        "target_entity_id": material.target_entity_id,
        "source_graphify_id": material.source_graphify_id,
        "target_graphify_id": material.target_graphify_id,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _endpoint_ids(edge: Mapping[str, Any]) -> tuple[str, str]:
    source = edge.get("source")
    if source is None:
        source = edge.get("from")
    target = edge.get("target")
    if target is None:
        target = edge.get("to")
    if not isinstance(source, str) or not source.strip():
        raise GraphRelationshipError("malformed-edge-source")
    if not isinstance(target, str) or not target.strip():
        raise GraphRelationshipError("malformed-edge-target")
    return source.strip(), target.strip()


def _edge_id(edge: Mapping[str, Any], index: int) -> str:
    raw = edge.get("id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return f"edge-index-{index}"


def _source_docs(edge: Mapping[str, Any]) -> tuple[str, ...]:
    docs = edge.get("source_documents")
    if docs is None:
        docs = edge.get("sources")
    if docs is None:
        return ()
    if not isinstance(docs, list):
        return ()
    cleaned = sorted({str(item).strip() for item in docs if str(item).strip()})
    return tuple(cleaned)


def _confidence(edge: Mapping[str, Any]) -> str | None:
    value = edge.get("confidence")
    if value is None:
        value = edge.get("link_quality")
    if value is None:
        return None
    return str(value).strip().lower() or None


def _derive_link_quality(
    *,
    source_docs: Sequence[str],
    confidence: str | None,
) -> LinkQuality:
    """Freeze names: verified / supported / inferred (never authority)."""
    has_docs = len(source_docs) > 0
    primary = confidence in {"high", "primary", "verified", "primary-linked"}
    if has_docs and primary:
        return "verified"
    if has_docs:
        return "supported"
    return "inferred"


def _safe_name(token: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", token).strip("-")
    return cleaned or "edge"


def _redact_reason(reason: str) -> str:
    """ADV-G3-093 — never echo secret-shaped content in quarantine reasons."""
    text = reason.strip()
    lowered = text.lower()
    for needle in ("password=", "secret=", "token=", "api_key=", "bearer "):
        if needle in lowered:
            return "redacted-sensitive-reason"
    return text[:240]


def _resolution_maps(
    nodes: Sequence[ResolvedNode],
) -> tuple[dict[str, ResolvedNode], dict[str, ResolvedNode]]:
    resolved: dict[str, ResolvedNode] = {}
    quarantined: dict[str, ResolvedNode] = {}
    ordered = sorted(nodes, key=lambda item: item.graphify_node_id.casefold())
    for node in ordered:
        if node.status == "resolved":
            resolved[node.graphify_node_id] = node
        else:
            quarantined[node.graphify_node_id] = node
    return resolved, quarantined


def _load_edges_from_artifact(path: Path, family: str) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if family == "edges" and path.suffix.lower() == ".jsonl":
        edges: list[dict[str, Any]] = []
        for index, line in enumerate(text.splitlines()):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise GraphRelationshipError(f"malformed-jsonl:{index}") from exc
            if isinstance(payload, dict):
                edges.append(payload)
        return edges

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GraphRelationshipError("malformed-json") from exc

    if family == "edges":
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            if isinstance(payload.get("edges"), list):
                return [item for item in payload["edges"] if isinstance(item, dict)]
            return [payload]
        return []

    if family == "envelope" and isinstance(payload, dict):
        edges_raw = payload.get("edges", [])
        if isinstance(edges_raw, list):
            return [item for item in edges_raw if isinstance(item, dict)]
    return []


def load_accepted_edges(
    *,
    project_root: Path,
    receipt: AcceptanceReceipt,
) -> tuple[list[dict[str, Any]], list[ArtifactRef]]:
    """Load edge dicts + artifact refs from accepted edge/envelope artifacts."""
    project_root = project_root.expanduser().resolve()
    edges: list[dict[str, Any]] = []
    refs: list[ArtifactRef] = []

    accepted = [
        item
        for item in receipt.artifacts
        if item.status == "accepted" and item.family in {"envelope", "edges"}
    ]
    accepted.sort(key=lambda item: item.relative_path.casefold())

    for artifact in accepted:
        relative = artifact.relative_path.replace("\\", "/")
        if relative.startswith(("/", "\\")) or "\\" in relative or ".." in Path(relative).parts:
            raise GraphRelationshipError(f"path-escape:{relative}")
        path = (project_root / relative).resolve()
        if not path.is_relative_to(project_root):
            raise GraphRelationshipError(f"path-escape:{relative}")
        if not path.is_file():
            raise GraphRelationshipError(f"missing-file:{relative}")
        refs.append(ArtifactRef(relative_path=relative, sha256=artifact.sha256))
        for edge in _load_edges_from_artifact(path, artifact.family):
            bound = dict(edge)
            bound["_atlas_artifact_ref"] = {
                "relative_path": relative,
                "sha256": artifact.sha256,
            }
            edges.append(bound)

    refs.sort(key=lambda item: item.relative_path)
    return edges, refs


def _artifact_ref_from_edge(
    edge: Mapping[str, Any],
    fallback: Sequence[ArtifactRef],
) -> tuple[ArtifactRef, ...]:
    raw = edge.get("_atlas_artifact_ref")
    if isinstance(raw, Mapping):
        relative = str(raw.get("relative_path") or "").strip()
        digest = str(raw.get("sha256") or "").strip()
        if relative and re.fullmatch(r"[0-9a-f]{64}", digest):
            return (ArtifactRef(relative_path=relative, sha256=digest),)
    if fallback:
        return tuple(fallback)
    raise GraphRelationshipError("missing-artifact-ref")


def _build_material(
    edge: Mapping[str, Any],
    *,
    index: int,
    project_id: str,
    resolved: Mapping[str, ResolvedNode],
    quarantined: Mapping[str, ResolvedNode],
    default_refs: Sequence[ArtifactRef],
) -> _EdgeMaterial:
    try:
        source_gid, target_gid = _endpoint_ids(edge)
        edge_id = _edge_id(edge, index)
    except GraphRelationshipError as exc:
        return _EdgeMaterial(
            graphify_edge_id=f"malformed-{index}",
            record_index=index,
            raw_type="",
            relationship_type="extension",
            extension_type="unknown",
            source_graphify_id="",
            target_graphify_id="",
            source_entity_id=None,
            target_entity_id=None,
            source_docs=(),
            confidence=None,
            artifact_refs=tuple(default_refs[:1]),
            material_fingerprint="",
            reject_category="malformed-edge",
            reject_reason=_redact_reason(str(exc)),
        )

    raw_type = str(edge.get("type") or edge.get("relationship_type") or "")
    rel_type, extension_type = normalize_relationship_type(raw_type or None)
    docs = _source_docs(edge)
    confidence = _confidence(edge)
    try:
        refs = _artifact_ref_from_edge(edge, default_refs)
    except GraphRelationshipError:
        refs = tuple(default_refs[:1]) if default_refs else ()

    stamped = edge.get("project_id")
    if stamped is not None and str(stamped).strip() and str(stamped).strip() != project_id:
        material = _EdgeMaterial(
            graphify_edge_id=edge_id,
            record_index=index,
            raw_type=raw_type,
            relationship_type=rel_type,
            extension_type=extension_type,
            source_graphify_id=source_gid,
            target_graphify_id=target_gid,
            source_entity_id=None,
            target_entity_id=None,
            source_docs=docs,
            confidence=confidence,
            artifact_refs=refs,
            material_fingerprint="",
            reject_category="cross-project-endpoint",
            reject_reason="cross-project-endpoint",
        )
        material.material_fingerprint = _material_fingerprint(material)
        return material

    def _classify_endpoint(
        gid: str,
    ) -> tuple[str | None, QuarantineCategory | None, str | None]:
        if gid in quarantined:
            node = quarantined[gid]
            if node.quarantine_category == "cross-project-resolution-forbidden":
                return None, "cross-project-endpoint", "cross-project-endpoint"
            return None, "quarantined-endpoint", "quarantined-endpoint"
        if gid not in resolved:
            return None, "orphaned-endpoint", "orphaned-endpoint"
        entity_id = resolved[gid].resolved_entity_id
        assert entity_id is not None
        return entity_id, None, None

    source_entity, src_cat, src_reason = _classify_endpoint(source_gid)
    target_entity, tgt_cat, tgt_reason = _classify_endpoint(target_gid)
    reject_category = src_cat or tgt_cat
    reject_reason = src_reason or tgt_reason
    # Optional attacker-supplied reason must be redacted (ADV-G3-093).
    raw_reason = edge.get("reason")
    if reject_category is not None and isinstance(raw_reason, str) and raw_reason.strip():
        reject_reason = _redact_reason(raw_reason)

    material = _EdgeMaterial(
        graphify_edge_id=edge_id,
        record_index=index,
        raw_type=raw_type,
        relationship_type=rel_type,
        extension_type=extension_type,
        source_graphify_id=source_gid,
        target_graphify_id=target_gid,
        source_entity_id=source_entity,
        target_entity_id=target_entity,
        source_docs=docs,
        confidence=confidence,
        artifact_refs=refs,
        material_fingerprint="",
        reject_category=reject_category,
        reject_reason=reject_reason,
        force_incompatible=bool(edge.get("_atlas_force_incompatible")),
    )
    material.material_fingerprint = _material_fingerprint(material)
    return material


def _compatible(a: _EdgeMaterial, b: _EdgeMaterial) -> bool:
    return (
        a.relationship_type == b.relationship_type
        and a.extension_type == b.extension_type
        and a.source_entity_id == b.source_entity_id
        and a.target_entity_id == b.target_entity_id
        and a.source_graphify_id == b.source_graphify_id
        and a.target_graphify_id == b.target_graphify_id
        and not a.force_incompatible
        and not b.force_incompatible
    )


def _unique_refs(refs: Sequence[ArtifactRef]) -> tuple[ArtifactRef, ...]:
    seen: dict[tuple[str, str], ArtifactRef] = {}
    for ref in refs:
        seen[(ref.relative_path, ref.sha256)] = ref
    return tuple(sorted(seen.values(), key=lambda item: item.relative_path.casefold()))


def _stronger_quality(a: LinkQuality, b: LinkQuality) -> LinkQuality:
    order = {"inferred": 0, "supported": 1, "verified": 2}
    return a if order[a] >= order[b] else b


def _collapse_and_emit(
    *,
    project_id: str,
    materials: Sequence[_EdgeMaterial],
) -> tuple[list[RelationshipRecord], list[RelationshipQuarantine], dict[str, int]]:
    retained: list[RelationshipRecord] = []
    quarantine: list[RelationshipQuarantine] = []
    histogram: dict[str, int] = {
        "verified": 0,
        "supported": 0,
        "inferred": 0,
        "orphaned": 0,
    }

    promotable: list[_EdgeMaterial] = []
    for material in sorted(
        materials, key=lambda item: (item.graphify_edge_id.casefold(), item.record_index)
    ):
        if material.reject_category is not None:
            if material.reject_category == "orphaned-endpoint":
                histogram["orphaned"] = histogram.get("orphaned", 0) + 1
            candidate_id = (
                f"qrel-{_safe_name(material.graphify_edge_id)}-{material.reject_category}"
            )
            quarantine.append(
                RelationshipQuarantine(
                    project_id=project_id,
                    candidate_id=candidate_id,
                    category=material.reject_category,
                    reason=_redact_reason(material.reject_reason or material.reject_category),
                    graphify_edge_ids=(material.graphify_edge_id,),
                    source_graphify_id=material.source_graphify_id or None,
                    target_graphify_id=material.target_graphify_id or None,
                    artifact_refs=material.artifact_refs,
                )
            )
            continue
        promotable.append(material)

    buckets: dict[str, _CollapseBucket] = {}
    for material in promotable:
        assert material.source_entity_id is not None
        assert material.target_entity_id is not None
        fp = relationship_fingerprint(
            relationship_type=material.relationship_type,
            source_entity_id=material.source_entity_id,
            target_entity_id=material.target_entity_id,
        )
        buckets.setdefault(fp, _CollapseBucket()).materials.append(material)

    for fp in sorted(buckets.keys()):
        group = buckets[fp].materials
        group.sort(key=lambda item: (item.graphify_edge_id.casefold(), item.record_index))
        head = group[0]
        incompatible = any(not _compatible(head, other) for other in group[1:])
        if not incompatible:
            incompatible = any(
                other.material_fingerprint != head.material_fingerprint for other in group[1:]
            )

        if incompatible:
            incompat_edge_ids = tuple(item.graphify_edge_id for item in group)
            quarantine.append(
                RelationshipQuarantine(
                    project_id=project_id,
                    candidate_id=f"qrel-incompat-{fp[:16]}",
                    category="incompatible-duplicate",
                    reason="incompatible-duplicate",
                    relationship_fingerprint=fp,
                    graphify_edge_ids=incompat_edge_ids,
                    source_graphify_id=head.source_graphify_id,
                    target_graphify_id=head.target_graphify_id,
                    artifact_refs=_unique_refs(
                        [ref for item in group for ref in item.artifact_refs]
                    ),
                )
            )
            continue

        quality = _derive_link_quality(source_docs=head.source_docs, confidence=head.confidence)
        for item in group[1:]:
            other_q = _derive_link_quality(
                source_docs=item.source_docs, confidence=item.confidence
            )
            quality = _stronger_quality(quality, other_q)

        histogram[quality] = histogram.get(quality, 0) + 1
        edge_ids: list[str] = sorted(
            {item.graphify_edge_id for item in group}, key=str.casefold
        )
        indexes: list[int] = sorted({item.record_index for item in group})
        docs: list[str] = sorted(
            {doc for item in group for doc in item.source_docs}, key=str.casefold
        )
        refs = _unique_refs([ref for item in group for ref in item.artifact_refs])
        source_gids: list[str] = sorted(
            {item.source_graphify_id for item in group}, key=str.casefold
        )
        target_gids: list[str] = sorted(
            {item.target_graphify_id for item in group}, key=str.casefold
        )

        relationship_id = f"grel-{fp[:24]}"
        provenance = {
            "graphify_artifact_refs": [ref.as_dict() for ref in refs],
            "graphify_edge_ids": edge_ids,
            "graphify_edge_indexes": indexes,
            "source_graphify_ids": source_gids,
            "target_graphify_ids": target_gids,
            "supporting_source_docs": docs,
        }
        retained.append(
            RelationshipRecord(
                project_id=project_id,
                relationship_id=relationship_id,
                relationship_type=head.relationship_type,
                source_entity_id=head.source_entity_id or "",
                target_entity_id=head.target_entity_id or "",
                source_graphify_id=head.source_graphify_id,
                target_graphify_id=head.target_graphify_id,
                link_quality=quality,
                relationship_fingerprint=fp,
                provenance=provenance,
                extension_type=head.extension_type,
            )
        )

    retained.sort(key=lambda item: item.relationship_id.casefold())
    quarantine.sort(key=lambda item: item.candidate_id.casefold())
    return retained, quarantine, histogram


def _coerce_refs(
    artifact_refs: Sequence[ArtifactRef] | Sequence[Mapping[str, str]] | None,
) -> tuple[ArtifactRef, ...]:
    if not artifact_refs:
        return ()
    out: list[ArtifactRef] = []
    for item in artifact_refs:
        if isinstance(item, ArtifactRef):
            out.append(item)
        else:
            out.append(
                ArtifactRef(
                    relative_path=str(item["relative_path"]),
                    sha256=str(item["sha256"]),
                )
            )
    return tuple(out)


def normalize_edges(
    edges: Sequence[Any],
    *,
    project_id: str,
    resolution: ResolutionResult | Sequence[ResolvedNode],
    artifact_refs: Sequence[ArtifactRef] | Sequence[Mapping[str, str]] | None = None,
    max_edges: int = DEFAULT_MAX_EDGES,
    strict: bool = True,
) -> RelationshipStoreResult:
    """Normalize Graphify edges against resolved endpoints into derived relationships."""
    if not project_id or "/" in project_id or "\\" in project_id or project_id in {".", ".."}:
        raise GraphRelationshipError("project-id-unsafe")

    if len(edges) > max_edges:
        raise GraphRelationshipError("edge-capacity-exceeded")

    if isinstance(resolution, ResolutionResult):
        nodes = resolution.nodes
        if resolution.project_id != project_id:
            raise GraphRelationshipError("project-id-mismatch")
    else:
        nodes = tuple(resolution)

    resolved, quarantined = _resolution_maps(nodes)
    refs = _coerce_refs(artifact_refs)

    materials: list[_EdgeMaterial] = []
    errors: list[dict[str, str]] = []
    for index, edge in enumerate(edges):
        if not isinstance(edge, Mapping):
            if strict:
                raise GraphRelationshipError("malformed-edge")
            errors.append({"index": str(index), "error": "malformed-edge"})
            continue
        materials.append(
            _build_material(
                edge,
                index=index,
                project_id=project_id,
                resolved=resolved,
                quarantined=quarantined,
                default_refs=refs,
            )
        )

    retained, quarantine, histogram = _collapse_and_emit(
        project_id=project_id,
        materials=materials,
    )
    return RelationshipStoreResult(
        project_id=project_id,
        relationships=tuple(retained),
        quarantine=tuple(quarantine),
        link_quality_histogram=histogram,
        errors=tuple(errors),
    )


def store_from_acceptance(
    *,
    project_root: Path,
    manifest: dict[str, Any],
    mapping_table: Mapping[str, Any] | None = None,
    config: Any = None,
    local_project_uuid: str | None = None,
    strict: bool = True,
    max_edges: int = DEFAULT_MAX_EDGES,
) -> tuple[AcceptanceReceipt, ResolutionResult, RelationshipStoreResult]:
    """Accept then resolve then normalize edges (consumes 001/002; does not redefine)."""
    receipt, resolution = resolve_from_acceptance(
        project_root=project_root,
        manifest=manifest,
        mapping_table=mapping_table,
        config=config,
        local_project_uuid=local_project_uuid,
        strict=strict,
    )
    edges, refs = load_accepted_edges(project_root=project_root, receipt=receipt)
    store = normalize_edges(
        edges,
        project_id=receipt.project_id,
        resolution=resolution,
        artifact_refs=refs,
        max_edges=max_edges,
        strict=strict,
    )
    return receipt, resolution, store


def inspect_relationship_store(result: RelationshipStoreResult) -> dict[str, Any]:
    """Library observability: counts and histogram; no secret payloads."""
    categories: dict[str, int] = {}
    for item in result.quarantine:
        categories[item.category] = categories.get(item.category, 0) + 1
    return {
        "package_id": PACKAGE_ID,
        "project_id": result.project_id,
        "authority": AUTHORITY_LEVEL,
        "retained_count": result.retained_count,
        "quarantined_count": result.quarantined_count,
        "link_quality_histogram": dict(sorted(result.link_quality_histogram.items())),
        "quarantine_categories": dict(sorted(categories.items())),
        "truth_boundary": TRUTH_BOUNDARY,
    }


def _safe_vault_relative(vault: Path, relative: str) -> Path:
    if relative.startswith(("/", "\\")) or "\\" in relative or ".." in Path(relative).parts:
        raise GraphRelationshipError(f"path-escape:{relative}")
    if not any(relative.startswith(prefix) for prefix in ALLOWED_WRITE_PREFIXES):
        raise GraphRelationshipError(f"forbidden-write-prefix:{relative}")
    if any(relative.startswith(prefix) for prefix in _FORBIDDEN_WRITE_PREFIXES):
        raise GraphRelationshipError(f"forbidden-write-prefix:{relative}")
    root = vault.expanduser().resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise GraphRelationshipError(f"path-escape:{relative}")
    return candidate


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def write_relationship_outputs(
    result: RelationshipStoreResult,
    *,
    vault: Path,
) -> list[str]:
    """Optional deterministic vault emits under frozen relationship prefixes only."""
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise GraphRelationshipError(f"vault-missing:{vault}")

    project = result.project_id
    if not project or "/" in project or "\\" in project or project in {".", ".."}:
        raise GraphRelationshipError("project-id-unsafe-for-path")

    written: list[str] = []
    for record in result.relationships:
        safe = _safe_name(record.relationship_id)
        relative = f"generated/graph/relationships/{project}/{safe}.json"
        path = _safe_vault_relative(vault, relative)
        validate_record(record.as_dict(), "graph-relationship")
        _write_atomic(path, record.to_json())
        written.append(relative)

    for candidate in result.quarantine:
        safe = _safe_name(candidate.candidate_id)
        relative = f"generated/graph/relationship-quarantine/{project}/{safe}.json"
        path = _safe_vault_relative(vault, relative)
        validate_record(candidate.as_dict(), "graph-relationship-quarantine")
        _write_atomic(path, candidate.to_json())
        written.append(relative)

    written.sort()
    return written


def promote_relationship_path_forbidden(relative: str) -> None:
    """Public helper for tests: assert a relative path is rejected by path policy."""
    _safe_vault_relative(Path("."), relative)


def handoff_quarantine_store(
    result: RelationshipStoreResult,
    *,
    prior_state: Mapping[str, Any] | None = None,
) -> QuarantineStoreResult:
    """Thin AS-GRAPH-004 handoff — durable quarantine / health / incremental.

    Does not mutate retained relationships, claims, authority, or CP paths.
    """
    from project_atlas.graph_quarantine import materialize_quarantine_store

    return materialize_quarantine_store(result, prior_state=prior_state)


__all__ = [
    "ALLOWED_WRITE_PREFIXES",
    "AUTHORITY_LEVEL",
    "DEFAULT_MAX_EDGES",
    "PACKAGE_ID",
    "TRUTH_BOUNDARY",
    "ArtifactRef",
    "GraphRelationshipError",
    "RelationshipQuarantine",
    "RelationshipRecord",
    "RelationshipStoreResult",
    "handoff_quarantine_store",
    "inspect_relationship_store",
    "load_accepted_edges",
    "normalize_edges",
    "normalize_relationship_type",
    "promote_relationship_path_forbidden",
    "relationship_fingerprint",
    "store_from_acceptance",
    "write_relationship_outputs",
]
