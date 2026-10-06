"""AS-GRAPH-002 — Deterministic Graph Entity Resolution (derived-only).

Consumes AS-GRAPH-001 accepted / inventory-bound Graphify nodes and resolves
them to project-local Atlas entity identifiers via frozen precedence.
Never writes claims, temporal state, authoritative state, knowledge-query
caches, or Control Plane ``relationships/``.

Truth boundary: GRAPH RELATIONSHIP ≠ AUTOMATIC AUTHORITY (AS-GRAPH-INV-TRUTH-001).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from project_atlas.graph_acceptance import (
    AcceptanceReceipt,
    AcceptedArtifact,
    GraphAcceptanceError,
    accept_graphify_artifacts,
)
from project_atlas.schema import validate_record
from project_atlas.secrets import scan_text
from project_atlas.source_identity import validate_project_uuid

PACKAGE_ID = "AS-GRAPH-002"
AUTHORITY_LEVEL = "derived"
TRUTH_BOUNDARY = "GRAPH RELATIONSHIP ≠ AUTOMATIC AUTHORITY"

ResolutionStep = Literal[
    "explicit_atlas_id",
    "durable_core_id",
    "mapping_table",
    "graphify_stable",
    "none",
]
StepOutcome = Literal["matched", "skipped", "rejected"]
Confidence = Literal["explicit", "durable-core", "mapped", "graphify-stable", "none"]
EntityClass = Literal[
    "project",
    "document",
    "work-package",
    "decision",
    "requirement",
    "unknown",
    "extension",
]
ResolveStatus = Literal["resolved", "quarantine_candidate"]
QuarantineCategory = Literal[
    "ambiguous-identity",
    "unresolved-identity",
    "cross-project-resolution-forbidden",
]

PRECEDENCE_STEPS: tuple[ResolutionStep, ...] = (
    "explicit_atlas_id",
    "durable_core_id",
    "mapping_table",
    "graphify_stable",
)

CONFIDENCE_FOR_STEP: dict[ResolutionStep, Confidence] = {
    "explicit_atlas_id": "explicit",
    "durable_core_id": "durable-core",
    "mapping_table": "mapped",
    "graphify_stable": "graphify-stable",
    "none": "none",
}

MVP_ENTITY_CLASSES: frozenset[str] = frozenset(
    {"project", "document", "work-package", "decision", "requirement"}
)
_TYPE_ALIASES: dict[str, EntityClass] = {
    "work_package": "work-package",
    "workpackage": "work-package",
    "req": "requirement",
    "requirement": "requirement",
    "decision": "decision",
    "document": "document",
    "doc": "document",
    "project": "project",
}

_STABLE_GRAPHIFY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SLINE_ID = re.compile(r"^sline-[0-9a-f]{20}$")
_CLAIM_ID = re.compile(r"^claim-[0-9a-f]{20}$")
# Identity-relevant fields for duplicate Graphify-id conflict detection (ADV-G2-008).
# Display label alone is not identity-relevant; type/explicit/durable/scope are.
_IDENTITY_FINGERPRINT_KEYS: tuple[str, ...] = (
    "type",
    "entity_type",
    "atlas_entity_id",
    "atlas_id",
    "entity_id",
    "source_lineage_id",
    "claim_id",
    "project_uuid",
    "project_id",
)
_FORBIDDEN_WRITE_PREFIXES: tuple[str, ...] = (
    "relationships/",
    "state/current-state/",
    "state/authoritative-state/",
    "claims/",
    "generated/indexes/",
    "generated/query/",
)

ALLOWED_WRITE_PREFIXES: tuple[str, ...] = (
    "generated/graph/resolved/",
    "generated/graph/quarantine-candidates/",
)


class GraphResolutionError(ValueError):
    """Fail-closed graph entity resolution error (metadata-only message)."""


@dataclass(frozen=True)
class MappingEntry:
    """One project-local Graphify → Atlas entity mapping."""

    graphify_node_id: str
    resolved_entity_id: str
    entity_class: EntityClass | None = None


@dataclass(frozen=True)
class MappingTable:
    """Deterministic project-local mapping table (no remote fetch)."""

    project_id: str
    entries: dict[str, MappingEntry]

    @classmethod
    def from_mapping(
        cls,
        project_id: str,
        mapping: Mapping[str, Any] | None,
    ) -> MappingTable:
        """Build a table from a dict or structured payload; fail closed if malformed."""
        if mapping is None:
            return cls(project_id=project_id, entries={})
        if not isinstance(mapping, Mapping):
            raise GraphResolutionError("mapping-table-malformed")

        # Structured form: {project_id, mappings:[{graphify_node_id, resolved_entity_id, ...}]}
        if "mappings" in mapping or "project_id" in mapping:
            table_project = mapping.get("project_id")
            if table_project is not None and str(table_project) != project_id:
                raise GraphResolutionError("cross-project-resolution-forbidden")
            raw_entries = mapping.get("mappings", [])
            if not isinstance(raw_entries, list):
                raise GraphResolutionError("mapping-table-malformed")
            entries: dict[str, MappingEntry] = {}
            for item in raw_entries:
                if not isinstance(item, dict):
                    raise GraphResolutionError("mapping-table-malformed")
                row_project = item.get("project_id")
                if row_project is not None and str(row_project) != project_id:
                    # ADV-G2-052: foreign project keys in mapping table fail closed.
                    raise GraphResolutionError("cross-project-resolution-forbidden")
                gid = str(item.get("graphify_node_id") or "").strip()
                eid = str(item.get("resolved_entity_id") or "").strip()
                if not gid or not eid:
                    raise GraphResolutionError("mapping-table-malformed")
                if gid in entries and entries[gid].resolved_entity_id != eid:
                    raise GraphResolutionError("mapping-table-malformed")
                entity_class = _optional_entity_class(item.get("entity_class"))
                entries[gid] = MappingEntry(
                    graphify_node_id=gid,
                    resolved_entity_id=eid,
                    entity_class=entity_class,
                )
            return cls(project_id=project_id, entries=entries)

        # Flat form: {graphify_node_id: resolved_entity_id | {resolved_entity_id, entity_class}}
        entries = {}
        for key, value in mapping.items():
            gid = str(key).strip()
            if not gid:
                raise GraphResolutionError("mapping-table-malformed")
            if isinstance(value, str):
                eid = value.strip()
                entity_class = None
            elif isinstance(value, Mapping):
                eid = str(value.get("resolved_entity_id") or "").strip()
                entity_class = _optional_entity_class(value.get("entity_class"))
            else:
                raise GraphResolutionError("mapping-table-malformed")
            if not eid:
                raise GraphResolutionError("mapping-table-malformed")
            if gid in entries and entries[gid].resolved_entity_id != eid:
                raise GraphResolutionError("mapping-table-malformed")
            entries[gid] = MappingEntry(
                graphify_node_id=gid,
                resolved_entity_id=eid,
                entity_class=entity_class,
            )
        return cls(project_id=project_id, entries=entries)


@dataclass(frozen=True)
class StepConsideration:
    """One precedence-step evaluation for an identity explanation."""

    step: ResolutionStep
    outcome: StepOutcome
    reason: str
    inputs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "step": self.step,
            "outcome": self.outcome,
            "reason": self.reason,
        }
        if self.inputs:
            payload["inputs"] = list(self.inputs)
        return payload


@dataclass(frozen=True)
class IdentityExplanation:
    """Explainable categorical resolution record (never a trust score)."""

    graphify_node_id: str
    project_id: str
    winning_step: ResolutionStep
    considered_steps: tuple[StepConsideration, ...]
    confidence: Confidence
    notes: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "package_id": PACKAGE_ID,
            "graphify_node_id": self.graphify_node_id,
            "project_id": self.project_id,
            "winning_step": self.winning_step,
            "considered_steps": [item.as_dict() for item in self.considered_steps],
            "confidence": self.confidence,
        }
        if self.notes is not None:
            payload["notes"] = self.notes
        return payload

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n"


@dataclass(frozen=True)
class ResolvedNode:
    """Resolved node or quarantine candidate (derived-only)."""

    project_id: str
    graphify_node_id: str
    entity_class: EntityClass
    resolution_step: ResolutionStep
    status: ResolveStatus
    source_artifact_refs: tuple[dict[str, str], ...]
    resolved_entity_id: str | None = None
    quarantine_category: QuarantineCategory | None = None
    explanation: IdentityExplanation | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "package_id": PACKAGE_ID,
            "project_id": self.project_id,
            "graphify_node_id": self.graphify_node_id,
            "entity_class": self.entity_class,
            "resolution_step": self.resolution_step,
            "authority": {
                "level": AUTHORITY_LEVEL,
                "note": (
                    "Graph entity resolution is derived-only; "
                    "never domain-authoritative."
                ),
            },
            "status": self.status,
            "source_artifact_refs": [dict(item) for item in self.source_artifact_refs],
            "truth_boundary": TRUTH_BOUNDARY,
        }
        if self.status == "resolved":
            assert self.resolved_entity_id is not None
            payload["resolved_entity_id"] = self.resolved_entity_id
        else:
            assert self.quarantine_category is not None
            payload["quarantine_category"] = self.quarantine_category
        return payload

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n"


@dataclass(frozen=True)
class ResolutionResult:
    """Deterministic batch resolution output (library-only by default)."""

    project_id: str
    nodes: tuple[ResolvedNode, ...]
    explanations: tuple[IdentityExplanation, ...]
    errors: tuple[dict[str, str], ...] = ()

    @property
    def resolved_count(self) -> int:
        return sum(1 for item in self.nodes if item.status == "resolved")

    @property
    def quarantined_count(self) -> int:
        return sum(1 for item in self.nodes if item.status == "quarantine_candidate")

    def as_dict(self) -> dict[str, Any]:
        step_counts: dict[str, int] = {}
        for item in self.nodes:
            if item.status == "resolved":
                step_counts[item.resolution_step] = step_counts.get(item.resolution_step, 0) + 1
        return {
            "schema_version": 1,
            "package_id": PACKAGE_ID,
            "project_id": self.project_id,
            "authority": {"level": AUTHORITY_LEVEL},
            "resolved_count": self.resolved_count,
            "quarantined_count": self.quarantined_count,
            "winning_steps": dict(sorted(step_counts.items())),
            "nodes": [item.as_dict() for item in self.nodes],
            "explanations": [item.as_dict() for item in self.explanations],
            "errors": list(self.errors),
            "truth_boundary": TRUTH_BOUNDARY,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n"


@dataclass
class _StepHit:
    entity_id: str
    entity_class: EntityClass | None = None
    quarantine: QuarantineCategory | None = None
    reason: str = ""
    inputs: tuple[str, ...] = ()
    hard_reject: bool = False


def _optional_entity_class(value: Any) -> EntityClass | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise GraphResolutionError("mapping-table-malformed")
    normalized = _normalize_entity_class(value)
    return normalized


def _normalize_entity_class(raw: str | None) -> EntityClass:
    if not raw:
        return "unknown"
    text = raw.strip().lower().replace(" ", "-")
    if text.startswith("ext:") or text == "extension":
        return "extension"
    aliased: str = _TYPE_ALIASES.get(text, text)
    if aliased in MVP_ENTITY_CLASSES:
        return aliased  # type: ignore[return-value]
    return "unknown"


def _graphify_node_id(node: Mapping[str, Any]) -> str:
    for key in ("id", "node_id"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            node_id = value.strip()
            # ADV-G2-002: empty/`.`/`..` are unsafe identifiers.
            if node_id in {".", ".."}:
                raise GraphResolutionError("malformed-accepted-node")
            # AS-SEC-SCAN-GRAPH-NODEID-JSON-ESC-001: decoded JSON ``\\u``
            # node ids must not become resolved filenames or entity ids.
            if scan_text(node_id):
                raise GraphResolutionError("secret-content")
            return node_id
    raise GraphResolutionError("malformed-accepted-node")


def _artifact_refs(
    refs: Sequence[Mapping[str, Any]] | None,
) -> tuple[dict[str, str], ...]:
    if not refs:
        return ()
    normalized: list[dict[str, str]] = []
    for item in refs:
        if not isinstance(item, Mapping):
            raise GraphResolutionError("malformed-source-artifact-ref")
        path = str(item.get("relative_path") or "").replace("\\", "/").strip()
        digest = str(item.get("sha256") or "").strip().lower()
        if not path or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise GraphResolutionError("malformed-source-artifact-ref")
        if path.startswith(("/", "\\")) or "\\" in path or ".." in Path(path).parts:
            raise GraphResolutionError(f"path-escape:{path}")
        normalized.append({"relative_path": path, "sha256": digest})
    return tuple(sorted(normalized, key=lambda row: row["relative_path"]))


def _explicit_candidates(node: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("atlas_entity_id", "atlas_id"):
        raw = node.get(key)
        if isinstance(raw, str) and raw.strip():
            values.append(raw.strip())
    entity_id = node.get("entity_id")
    if isinstance(entity_id, str) and entity_id.strip():
        text = entity_id.strip()
        if text.startswith("atlas:") or ":" in text:
            values.append(text)
    # Preserve order, drop exact duplicates.
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def _is_well_formed_explicit(value: str, project_id: str) -> bool:
    if value.startswith(f"atlas:{project_id}:"):
        return len(value) > len(f"atlas:{project_id}:")
    if value.startswith(f"{project_id}:"):
        return len(value) > len(f"{project_id}:")
    return False


def _is_cross_project_explicit(value: str, project_id: str) -> bool:
    if value.startswith("atlas:"):
        rest = value[len("atlas:") :]
        other, _, remainder = rest.partition(":")
        return bool(other) and other != project_id and bool(remainder)
    if ":" in value:
        other, _, remainder = value.partition(":")
        if not other or not remainder:
            return False
        # Project-scoped ids use a project token without path separators.
        if "/" in other or "\\" in other:
            return False
        return other != project_id
    return False


def _eval_explicit(node: Mapping[str, Any], project_id: str) -> _StepHit | None:
    candidates = _explicit_candidates(node)
    if not candidates:
        return None
    inputs = tuple(f"explicit:{item}" for item in candidates)
    if len(candidates) > 1:
        distinct = set(candidates)
        if len(distinct) > 1:
            return _StepHit(
                entity_id="",
                quarantine="ambiguous-identity",
                reason="ambiguous-explicit-id",
                inputs=inputs,
            )
    value = candidates[0]
    if _is_cross_project_explicit(value, project_id):
        return _StepHit(
            entity_id="",
            quarantine="cross-project-resolution-forbidden",
            reason="cross-project-resolution-forbidden",
            inputs=inputs,
            hard_reject=True,
        )
    if _is_well_formed_explicit(value, project_id):
        return _StepHit(entity_id=value, reason="explicit-atlas-id", inputs=inputs)
    return _StepHit(
        entity_id="",
        quarantine="ambiguous-identity",
        reason="malformed-explicit-id",
        inputs=inputs,
    )


def _durable_candidates(
    node: Mapping[str, Any],
    *,
    local_project_uuid: str | None,
) -> list[tuple[str, str, EntityClass | None]] | _StepHit:
    """Return durable candidates, or a quarantine hit for foreign project_uuid.

    ``project_uuid`` is conclusive only when it matches an explicit local
    project-UUID binding (ADV-G2-007 / INV-002). Foreign or unbound UUIDs
    fail closed as cross-project.
    """
    found: list[tuple[str, str, EntityClass | None]] = []
    lineage = node.get("source_lineage_id")
    if isinstance(lineage, str) and lineage.strip():
        value = lineage.strip()
        if _SLINE_ID.fullmatch(value):
            found.append(("source_lineage_id", value, "document"))
    claim = node.get("claim_id")
    if isinstance(claim, str) and claim.strip():
        value = claim.strip()
        if _CLAIM_ID.fullmatch(value):
            found.append(("claim_id", value, None))
    project_uuid = node.get("project_uuid")
    if isinstance(project_uuid, str) and project_uuid.strip():
        try:
            value = validate_project_uuid(project_uuid.strip())
        except ValueError:
            value = ""
        if value:
            bound: str | None = None
            if local_project_uuid is not None:
                try:
                    bound = validate_project_uuid(local_project_uuid.strip())
                except ValueError as exc:
                    raise GraphResolutionError("local-project-uuid-invalid") from exc
            if bound is None:
                return _StepHit(
                    entity_id="",
                    quarantine="cross-project-resolution-forbidden",
                    reason="project-uuid-binding-required",
                    inputs=(f"project_uuid:{value}",),
                    hard_reject=True,
                )
            if value != bound:
                return _StepHit(
                    entity_id="",
                    quarantine="cross-project-resolution-forbidden",
                    reason="cross-project-resolution-forbidden",
                    inputs=(f"project_uuid:{value}", f"local_project_uuid:{bound}"),
                    hard_reject=True,
                )
            found.append(("project_uuid", value, "project"))
    return found


def _eval_durable(
    node: Mapping[str, Any],
    *,
    local_project_uuid: str | None,
) -> _StepHit | None:
    candidates = _durable_candidates(node, local_project_uuid=local_project_uuid)
    if isinstance(candidates, _StepHit):
        return candidates
    if not candidates:
        # Present-but-invalid durable fields are skipped (consume-only; no minting).
        present = []
        for key in ("source_lineage_id", "claim_id", "project_uuid"):
            raw = node.get(key)
            if isinstance(raw, str) and raw.strip():
                present.append(key)
        if present:
            return _StepHit(
                entity_id="",
                reason="invalid-durable-core-id",
                inputs=tuple(present),
            )
        return None
    inputs = tuple(f"{field}:{value}" for field, value, _ in candidates)
    distinct_values = {value for _, value, _ in candidates}
    if len(distinct_values) > 1:
        return _StepHit(
            entity_id="",
            quarantine="ambiguous-identity",
            reason="multiple-durable-core-identities",
            inputs=inputs,
        )
    field_name, value, suggested = candidates[0]
    return _StepHit(
        entity_id=value,
        entity_class=suggested,
        reason=f"durable-core:{field_name}",
        inputs=inputs,
    )


def _stamped_project_scope_hit(
    node: Mapping[str, Any], project_id: str
) -> _StepHit | None:
    """ADV-G2-051: node-stamped project_id ≠ resolution scope → fail closed."""
    stamped = node.get("project_id")
    if not isinstance(stamped, str) or not stamped.strip():
        return None
    stamped = stamped.strip()
    if stamped == project_id:
        return None
    return _StepHit(
        entity_id="",
        quarantine="cross-project-resolution-forbidden",
        reason="cross-project-resolution-forbidden",
        inputs=(f"stamped_project_id:{stamped}", f"scope_project_id:{project_id}"),
        hard_reject=True,
    )


def _identity_fingerprint(node: Mapping[str, Any]) -> str:
    """Deterministic fingerprint of identity-relevant node fields (ADV-G2-008)."""
    material: dict[str, Any] = {}
    for key in _IDENTITY_FINGERPRINT_KEYS:
        if key in node:
            material[key] = node[key]
    return json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)


def _quarantine_candidate_node(
    *,
    project_id: str,
    node: Mapping[str, Any],
    category: QuarantineCategory,
    reason: str,
    source_artifact_refs: Sequence[Mapping[str, Any]] | None,
) -> ResolvedNode:
    """Build a schema-valid quarantine candidate with explanation."""
    node_id = _graphify_node_id(node)
    entity_class = _normalize_entity_class(
        str(node.get("type") or node.get("entity_type") or "") or None
    )
    refs = _artifact_refs(source_artifact_refs)
    explanation = IdentityExplanation(
        graphify_node_id=node_id,
        project_id=project_id,
        winning_step="none",
        considered_steps=(
            StepConsideration(
                step="explicit_atlas_id",
                outcome="rejected",
                reason=reason,
            ),
            StepConsideration(
                step="durable_core_id",
                outcome="skipped",
                reason="lower-precedence-not-evaluated",
            ),
            StepConsideration(
                step="mapping_table",
                outcome="skipped",
                reason="lower-precedence-not-evaluated",
            ),
            StepConsideration(
                step="graphify_stable",
                outcome="skipped",
                reason="lower-precedence-not-evaluated",
            ),
        ),
        confidence="none",
        notes="categorical confidence only; not an authority or trust score",
    )
    resolved = ResolvedNode(
        project_id=project_id,
        graphify_node_id=node_id,
        entity_class=entity_class,
        resolution_step="none",
        status="quarantine_candidate",
        quarantine_category=category,
        source_artifact_refs=refs,
        explanation=explanation,
    )
    validate_record(resolved.as_dict(), "graph-resolved-node")
    validate_record(explanation.as_dict(), "graph-identity-explanation")
    return ResolvedNode(
        project_id=resolved.project_id,
        graphify_node_id=resolved.graphify_node_id,
        entity_class=resolved.entity_class,
        resolution_step=resolved.resolution_step,
        status=resolved.status,
        source_artifact_refs=resolved.source_artifact_refs,
        resolved_entity_id=None,
        quarantine_category=resolved.quarantine_category,
        explanation=explanation,
    )


def _eval_mapping(
    node_id: str,
    project_id: str,
    table: MappingTable,
) -> _StepHit | None:
    entry = table.entries.get(node_id)
    if entry is None:
        return None
    inputs = (f"mapping:{node_id}->{entry.resolved_entity_id}",)
    if _is_cross_project_explicit(entry.resolved_entity_id, project_id):
        return _StepHit(
            entity_id="",
            quarantine="cross-project-resolution-forbidden",
            reason="cross-project-resolution-forbidden",
            inputs=inputs,
            hard_reject=True,
        )
    if not (
        _is_well_formed_explicit(entry.resolved_entity_id, project_id)
        or _SLINE_ID.fullmatch(entry.resolved_entity_id)
        or _CLAIM_ID.fullmatch(entry.resolved_entity_id)
        or _STABLE_GRAPHIFY_ID.fullmatch(entry.resolved_entity_id)
    ):
        return _StepHit(
            entity_id="",
            quarantine="ambiguous-identity",
            reason="malformed-mapping-target",
            inputs=inputs,
        )
    return _StepHit(
        entity_id=entry.resolved_entity_id,
        entity_class=entry.entity_class,
        reason="mapping-table",
        inputs=inputs,
    )


def _eval_graphify_stable(
    node_id: str, project_id: str, entity_class: EntityClass
) -> _StepHit | None:
    if not _STABLE_GRAPHIFY_ID.fullmatch(node_id):
        return None
    entity_id = f"{project_id}:{entity_class}:{node_id}"
    return _StepHit(
        entity_id=entity_id,
        entity_class=entity_class,
        reason="graphify-stable-id",
        inputs=(f"graphify_node_id:{node_id}",),
    )


def resolve_node(
    node: Mapping[str, Any],
    *,
    project_id: str,
    mapping_table: MappingTable | Mapping[str, Any] | None = None,
    source_artifact_refs: Sequence[Mapping[str, Any]] | None = None,
    local_project_uuid: str | None = None,
) -> ResolvedNode:
    """Resolve one accepted Graphify node (AS-GRAPH-002-FR-001…005)."""
    if not isinstance(project_id, str) or not project_id.strip():
        raise GraphResolutionError("project-id-required")
    project_id = project_id.strip()
    if not isinstance(node, Mapping):
        raise GraphResolutionError("malformed-accepted-node")

    node_id = _graphify_node_id(node)
    refs = _artifact_refs(source_artifact_refs)
    table = (
        mapping_table
        if isinstance(mapping_table, MappingTable)
        else MappingTable.from_mapping(project_id, mapping_table)
    )
    if table.project_id != project_id:
        raise GraphResolutionError("cross-project-resolution-forbidden")

    entity_class = _normalize_entity_class(
        str(node.get("type") or node.get("entity_type") or "") or None
    )

    # ADV-G2-051: stamped project_id ≠ scope fails closed before precedence.
    scope_hit = _stamped_project_scope_hit(node, project_id)
    if scope_hit is not None:
        return _quarantine_candidate_node(
            project_id=project_id,
            node=node,
            category="cross-project-resolution-forbidden",
            reason=scope_hit.reason,
            source_artifact_refs=source_artifact_refs,
        )

    considerations: list[StepConsideration] = []
    winner: _StepHit | None = None
    winning_step: ResolutionStep = "none"

    evaluators: list[tuple[ResolutionStep, Any]] = [
        ("explicit_atlas_id", lambda: _eval_explicit(node, project_id)),
        (
            "durable_core_id",
            lambda: _eval_durable(node, local_project_uuid=local_project_uuid),
        ),
        ("mapping_table", lambda: _eval_mapping(node_id, project_id, table)),
        ("graphify_stable", lambda: _eval_graphify_stable(node_id, project_id, entity_class)),
    ]

    for step, evaluator in evaluators:
        if winner is not None:
            considerations.append(
                StepConsideration(
                    step=step,
                    outcome="skipped",
                    reason="lower-precedence-not-evaluated",
                )
            )
            continue
        hit = evaluator()
        if hit is None:
            considerations.append(
                StepConsideration(
                    step=step,
                    outcome="skipped",
                    reason="no-input",
                )
            )
            continue
        if hit.quarantine is not None:
            considerations.append(
                StepConsideration(
                    step=step,
                    outcome="rejected",
                    reason=hit.reason,
                    inputs=hit.inputs,
                )
            )
            winner = hit
            winning_step = "none"
            continue
        if hit.entity_id:
            considerations.append(
                StepConsideration(
                    step=step,
                    outcome="matched",
                    reason=hit.reason,
                    inputs=hit.inputs,
                )
            )
            winner = hit
            winning_step = step
            continue
        considerations.append(
            StepConsideration(
                step=step,
                outcome="rejected",
                reason=hit.reason or "rejected",
                inputs=hit.inputs,
            )
        )

    explanation = IdentityExplanation(
        graphify_node_id=node_id,
        project_id=project_id,
        winning_step=winning_step,
        considered_steps=tuple(considerations),
        confidence=CONFIDENCE_FOR_STEP[winning_step],
        notes="categorical confidence only; not an authority or trust score",
    )

    if winner is not None and winner.quarantine is not None:
        resolved = ResolvedNode(
            project_id=project_id,
            graphify_node_id=node_id,
            entity_class=entity_class,
            resolution_step="none",
            status="quarantine_candidate",
            quarantine_category=winner.quarantine,
            source_artifact_refs=refs,
            explanation=explanation,
        )
    elif winner is not None and winner.entity_id:
        final_class = winner.entity_class or entity_class
        resolved = ResolvedNode(
            project_id=project_id,
            graphify_node_id=node_id,
            entity_class=final_class,
            resolution_step=winning_step,
            status="resolved",
            resolved_entity_id=winner.entity_id,
            source_artifact_refs=refs,
            explanation=explanation,
        )
    else:
        resolved = ResolvedNode(
            project_id=project_id,
            graphify_node_id=node_id,
            entity_class=entity_class,
            resolution_step="none",
            status="quarantine_candidate",
            quarantine_category="unresolved-identity",
            source_artifact_refs=refs,
            explanation=explanation,
        )

    validate_record(resolved.as_dict(), "graph-resolved-node")
    validate_record(explanation.as_dict(), "graph-identity-explanation")
    return ResolvedNode(
        project_id=resolved.project_id,
        graphify_node_id=resolved.graphify_node_id,
        entity_class=resolved.entity_class,
        resolution_step=resolved.resolution_step,
        status=resolved.status,
        source_artifact_refs=resolved.source_artifact_refs,
        resolved_entity_id=resolved.resolved_entity_id,
        quarantine_category=resolved.quarantine_category,
        explanation=explanation,
    )


def resolve_nodes(
    nodes: Sequence[Any],
    *,
    project_id: str,
    mapping_table: MappingTable | Mapping[str, Any] | None = None,
    source_artifact_refs: Sequence[Mapping[str, Any]] | None = None,
    local_project_uuid: str | None = None,
    strict: bool = True,
) -> ResolutionResult:
    """Resolve many nodes deterministically (stable sort by graphify id)."""
    if not isinstance(project_id, str) or not project_id.strip():
        raise GraphResolutionError("project-id-required")
    project_id = project_id.strip()
    table = (
        mapping_table
        if isinstance(mapping_table, MappingTable)
        else MappingTable.from_mapping(project_id, mapping_table)
    )

    indexed: list[tuple[str, int, Mapping[str, Any]]] = []
    for index, node in enumerate(nodes):
        if not isinstance(node, Mapping):
            if strict:
                raise GraphResolutionError("malformed-accepted-node")
            continue
        try:
            node_id = _graphify_node_id(node)
        except GraphResolutionError:
            if strict:
                raise
            continue
        indexed.append((node_id, index, node))
    indexed.sort(key=lambda row: (row[0].casefold(), row[1]))

    # ADV-G2-008: group by Graphify id; divergent identity payloads → quarantine.
    groups: dict[str, list[tuple[int, Mapping[str, Any]]]] = {}
    for node_id, index, node in indexed:
        groups.setdefault(node_id, []).append((index, node))

    resolved_nodes: list[ResolvedNode] = []
    explanations: list[IdentityExplanation] = []
    errors: list[dict[str, str]] = []
    for node_id in sorted(groups, key=str.casefold):
        members = sorted(groups[node_id], key=lambda row: row[0])
        fingerprints = {_identity_fingerprint(node) for _, node in members}
        if len(fingerprints) > 1:
            item = _quarantine_candidate_node(
                project_id=project_id,
                node=members[0][1],
                category="ambiguous-identity",
                reason="duplicate-graphify-id-divergent-payload",
                source_artifact_refs=source_artifact_refs,
            )
            resolved_nodes.append(item)
            if item.explanation is not None:
                explanations.append(item.explanation)
            continue
        try:
            item = resolve_node(
                members[0][1],
                project_id=project_id,
                mapping_table=table,
                source_artifact_refs=source_artifact_refs,
                local_project_uuid=local_project_uuid,
            )
        except GraphResolutionError as exc:
            code = str(exc).split(":", 1)[0]
            errors.append({"code": code, "graphify_node_id": node_id, "message": str(exc)})
            if strict:
                raise
            continue
        resolved_nodes.append(item)
        if item.explanation is not None:
            explanations.append(item.explanation)

    return ResolutionResult(
        project_id=project_id,
        nodes=tuple(resolved_nodes),
        explanations=tuple(explanations),
        errors=tuple(errors),
    )


def _load_nodes_from_artifact(path: Path, family: str) -> list[dict[str, Any]]:
    if family == "nodes" and path.suffix.lower() == ".jsonl":
        records: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                records.append(value)
        return records
    payload = json.loads(path.read_text(encoding="utf-8"))
    if family == "envelope" and isinstance(payload, dict):
        return [item for item in payload.get("nodes", []) if isinstance(item, dict)]
    if family == "nodes" and isinstance(payload, dict):
        if isinstance(payload.get("nodes"), list):
            return [item for item in payload["nodes"] if isinstance(item, dict)]
        return [payload]
    return []


def load_accepted_nodes(
    *,
    project_root: Path,
    receipt: AcceptanceReceipt,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Load node dicts + artifact refs from AS-GRAPH-001 accepted artifacts.

    Duplicate Graphify ids are retained so ``resolve_nodes`` can quarantine
    divergent identity payloads (ADV-G2-008). Identical overlaps collapse later.
    """
    project_root = project_root.expanduser().resolve()
    nodes: list[dict[str, Any]] = []
    refs: list[dict[str, str]] = []
    seen: dict[str, str] = {}

    accepted = [
        item
        for item in receipt.artifacts
        if item.status == "accepted" and item.family in {"envelope", "nodes"}
    ]
    accepted.sort(key=lambda item: item.relative_path.casefold())

    for artifact in accepted:
        relative = artifact.relative_path.replace("\\", "/")
        if relative.startswith(("/", "\\")) or "\\" in relative or ".." in Path(relative).parts:
            raise GraphResolutionError(f"path-escape:{relative}")
        path = (project_root / relative).resolve()
        if not path.is_relative_to(project_root):
            raise GraphResolutionError(f"path-escape:{relative}")
        if not path.is_file():
            raise GraphResolutionError(f"missing-file:{relative}")
        refs.append({"relative_path": relative, "sha256": artifact.sha256})
        for node in _load_nodes_from_artifact(path, artifact.family):
            try:
                node_id = _graphify_node_id(node)
            except GraphResolutionError as exc:
                if str(exc) == "secret-content":
                    continue
                raise GraphResolutionError("malformed-accepted-node") from None
            fingerprint = _identity_fingerprint(node)
            prior = seen.get(node_id)
            if prior is None:
                seen[node_id] = fingerprint
                nodes.append(node)
            elif prior != fingerprint:
                # Retain divergent duplicate for batch ambiguity quarantine.
                nodes.append(node)
            # Identical fingerprint: keep first occurrence only.
    nodes.sort(key=lambda item: _graphify_node_id(item).casefold())
    refs.sort(key=lambda item: item["relative_path"])
    return nodes, refs


def resolve_from_acceptance(
    *,
    project_root: Path,
    manifest: dict[str, Any],
    mapping_table: MappingTable | Mapping[str, Any] | None = None,
    config: Any = None,
    local_project_uuid: str | None = None,
    strict: bool = True,
) -> tuple[AcceptanceReceipt, ResolutionResult]:
    """Accept (AS-GRAPH-001) then resolve (AS-GRAPH-002). Does not redefine acceptance."""
    receipt = accept_graphify_artifacts(
        project_root=project_root,
        manifest=manifest,
        config=config,
        strict=strict,
    )
    nodes, refs = load_accepted_nodes(project_root=project_root, receipt=receipt)
    result = resolve_nodes(
        nodes,
        project_id=receipt.project_id,
        mapping_table=mapping_table,
        source_artifact_refs=refs,
        local_project_uuid=local_project_uuid,
        strict=strict,
    )
    return receipt, result


def inspect_resolution(result: ResolutionResult) -> dict[str, Any]:
    """Library observability (FR-012): counts and winning steps; no secret payloads."""
    step_counts: dict[str, int] = {}
    quarantine_counts: dict[str, int] = {}
    for item in result.nodes:
        if item.status == "resolved":
            step_counts[item.resolution_step] = step_counts.get(item.resolution_step, 0) + 1
        elif item.quarantine_category:
            key = item.quarantine_category
            quarantine_counts[key] = quarantine_counts.get(key, 0) + 1
    return {
        "package_id": PACKAGE_ID,
        "project_id": result.project_id,
        "resolved_count": result.resolved_count,
        "quarantined_count": result.quarantined_count,
        "winning_steps": dict(sorted(step_counts.items())),
        "quarantine_categories": dict(sorted(quarantine_counts.items())),
        "authority_level": AUTHORITY_LEVEL,
        "truth_boundary": TRUTH_BOUNDARY,
    }


def _safe_vault_relative(vault: Path, relative: str) -> Path:
    if not relative or relative.startswith(("/", "\\")) or "\\" in relative:
        raise GraphResolutionError(f"path-escape:{relative}")
    parts = Path(relative).parts
    if any(part == ".." for part in parts):
        raise GraphResolutionError(f"path-escape:{relative}")
    normalized = relative.replace("\\", "/").lstrip("./")
    if not any(normalized.startswith(prefix) for prefix in ALLOWED_WRITE_PREFIXES):
        raise GraphResolutionError(f"path-policy-forbidden:{normalized}")
    if any(normalized.startswith(prefix) for prefix in _FORBIDDEN_WRITE_PREFIXES):
        raise GraphResolutionError(f"path-policy-forbidden:{normalized}")
    if normalized.startswith("generated/graph/acceptance/"):
        raise GraphResolutionError(f"path-policy-forbidden:{normalized}")
    root = vault.expanduser().resolve()
    candidate = (root / normalized).resolve()
    if not candidate.is_relative_to(root):
        raise GraphResolutionError(f"path-escape:{relative}")
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


def write_resolution_outputs(
    result: ResolutionResult,
    *,
    vault: Path,
) -> list[str]:
    """Optional deterministic vault emits under frozen §8 derived paths only."""
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise GraphResolutionError(f"vault-missing:{vault}")

    written: list[str] = []
    project = result.project_id
    # Sanitize project token for path segment (fail closed on traversal).
    if not project or "/" in project or "\\" in project or project in {".", ".."}:
        raise GraphResolutionError("project-id-unsafe-for-path")

    for node in result.nodes:
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", node.graphify_node_id).strip("-") or "node"
        if node.status == "resolved":
            relative = f"generated/graph/resolved/{project}/{safe_name}.json"
        else:
            relative = f"generated/graph/quarantine-candidates/{project}/{safe_name}.json"
        path = _safe_vault_relative(vault, relative)
        validate_record(node.as_dict(), "graph-resolved-node")
        _write_atomic(path, node.to_json())
        written.append(relative)

        if node.explanation is not None:
            expl_rel = f"generated/graph/resolved/{project}/explanations/{safe_name}.json"
            expl_path = _safe_vault_relative(vault, expl_rel)
            validate_record(node.explanation.as_dict(), "graph-identity-explanation")
            _write_atomic(expl_path, node.explanation.to_json())
            written.append(expl_rel)

    written.sort()
    return written


def promote_resolution_path_forbidden(relative: str) -> None:
    """Public helper for tests: assert a relative path is rejected by path policy."""
    _safe_vault_relative(Path("."), relative)


# Re-export acceptance types used by callers/tests (consume, do not redefine).
__all__ = [
    "ALLOWED_WRITE_PREFIXES",
    "AUTHORITY_LEVEL",
    "PACKAGE_ID",
    "PRECEDENCE_STEPS",
    "TRUTH_BOUNDARY",
    "AcceptanceReceipt",
    "AcceptedArtifact",
    "GraphAcceptanceError",
    "GraphResolutionError",
    "IdentityExplanation",
    "MappingEntry",
    "MappingTable",
    "ResolutionResult",
    "ResolvedNode",
    "StepConsideration",
    "inspect_resolution",
    "load_accepted_nodes",
    "resolve_from_acceptance",
    "resolve_node",
    "resolve_nodes",
    "write_resolution_outputs",
]
