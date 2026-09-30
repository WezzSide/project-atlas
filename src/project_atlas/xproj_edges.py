"""AS-XPROJ-002 — Cross-project relationship / global edge registry.

Explicit edges between **registered** ``global_entity_id`` endpoints only.
Never name-merge, never fuzzy/LLM joins, never elevate above
``authority.level = derived``, never synthesize claims from ``conflicts-with``.

Intra-project Graph edges remain AS-GRAPH-003. This module refuses edges that
do not span at least two distinct projects via join coverage
(AS-XPROJ-INV-EDGE-001).

Truth boundary: CROSS-PROJECT EDGE ≠ AUTOMATIC AUTHORITY
(AS-XPROJ-INV-TRUTH-001). AS-XPROJ-INV-EXPLICIT-001: both ends must be
governed globals.
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
from typing import Any, Literal

from project_atlas.schema import validate_record
from project_atlas.secrets import scan_text
from project_atlas.xproj_registry import (
    EvidenceRef,
    GlobalEntityRecord,
    JoinKeyRecord,
    load_registry_state,
)

PACKAGE_ID = "AS-XPROJ-002"
AUTHORITY_LEVEL = "derived"
TRUTH_BOUNDARY = "CROSS-PROJECT EDGE ≠ AUTOMATIC AUTHORITY"

# Mirror GRAPH-003 MVP vocabulary (consume-only; do not mutate graph modules).
MVP_RELATIONSHIP_TYPES: frozenset[str] = frozenset(
    {
        "part-of",
        "depends-on",
        "documents",
        "validates",
        "supersedes",
        "derived-from",
        "conflicts-with",
        "extension",
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

ALLOWED_WRITE_PREFIXES: tuple[str, ...] = (
    "state/global-entities/edges/",
    "state/global-entities/edge-quarantine/",
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
    "generated/xproj/conflicts/",
    "generated/xproj/indexes/",
    # Entity/join registry remains XPROJ-001 owned emit surface.
    "state/global-entities/joins/",
    "state/global-entities/quarantine-candidates/",
)

_EDGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GLOBAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

LinkQuality = Literal["verified", "supported", "inferred"]
EdgeQuarantineCategory = Literal[
    "missing-endpoint-registration",
    "name-only-edge-forbidden",
    "fuzzy-edge-forbidden",
    "not-cross-project",
    "self-loop-forbidden",
    "incompatible-duplicate-edge",
    "unknown-relationship-type",
    "endpoint-guess-forbidden",
    "secret-finding",
    "evidence-refs-required",
    "edge-id-invalid",
]


class XprojEdgeError(ValueError):
    """Fail-closed XPROJ edge registry error (metadata-only message)."""


@dataclass(frozen=True)
class GlobalEdgeRecord:
    """Explicit cross-project edge (promoted registry record)."""

    edge_id: str
    relationship_type: str
    source_global_entity_id: str
    target_global_entity_id: str
    evidence_refs: tuple[EvidenceRef, ...]
    edge_fingerprint: str
    link_quality: LinkQuality = "supported"
    source_project_ids: tuple[str, ...] = ()
    target_project_ids: tuple[str, ...] = ()
    extension_type: str | None = None
    notes: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "package_id": PACKAGE_ID,
            "edge_id": self.edge_id,
            "relationship_type": self.relationship_type,
            "source_global_entity_id": self.source_global_entity_id,
            "target_global_entity_id": self.target_global_entity_id,
            "link_quality": self.link_quality,
            "edge_fingerprint": self.edge_fingerprint,
            "evidence_refs": [ref.as_dict() for ref in self.evidence_refs],
            "source_project_ids": list(self.source_project_ids),
            "target_project_ids": list(self.target_project_ids),
            "authority": {
                "level": AUTHORITY_LEVEL,
                "note": (
                    "XPROJ edge is derived portfolio intelligence; "
                    "not claim/temporal/authoritative truth. "
                    "conflicts-with never synthesizes Core claim conflicts."
                ),
            },
            "registration_kind": "explicit",
            "status": "registered",
            "truth_boundary": TRUTH_BOUNDARY,
        }
        if self.relationship_type == "extension":
            assert self.extension_type is not None
            payload["extension_type"] = self.extension_type
        if self.notes is not None:
            payload["notes"] = self.notes
        return payload

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n"


@dataclass(frozen=True)
class EdgeQuarantineCandidate:
    """Fail-closed quarantine — never includes winning_choice."""

    candidate_id: str
    category: EdgeQuarantineCategory
    reason: str
    inputs_considered: Mapping[str, Any]
    edge_id: str | None = None
    source_global_entity_id: str | None = None
    target_global_entity_id: str | None = None
    relationship_type: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "package_id": PACKAGE_ID,
            "candidate_id": self.candidate_id,
            "category": self.category,
            "authority": {
                "level": AUTHORITY_LEVEL,
                "note": "Edge quarantine is derived-only; never pick a winner.",
            },
            "status": "quarantine_candidate",
            "reason": self.reason,
            "inputs_considered": dict(self.inputs_considered),
            "truth_boundary": TRUTH_BOUNDARY,
        }
        if self.edge_id is not None:
            payload["edge_id"] = self.edge_id
        if self.source_global_entity_id is not None:
            payload["source_global_entity_id"] = self.source_global_entity_id
        if self.target_global_entity_id is not None:
            payload["target_global_entity_id"] = self.target_global_entity_id
        if self.relationship_type is not None:
            payload["relationship_type"] = self.relationship_type
        return payload

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n"


@dataclass
class EdgeRegistryResult:
    """In-memory edge registration outcome (deterministic ordering on emit)."""

    edges: list[GlobalEdgeRecord] = field(default_factory=list)
    quarantine: list[EdgeQuarantineCandidate] = field(default_factory=list)

    @property
    def registered_count(self) -> int:
        return len(self.edges)

    @property
    def quarantined_count(self) -> int:
        return len(self.quarantine)


def _redact_reason(reason: str) -> str:
    text = reason.strip()
    lowered = text.lower()
    for needle in ("password=", "secret=", "token=", "api_key=", "bearer "):
        if needle in lowered:
            return "redacted-sensitive-reason"
    return text[:240]


def _safe_name(token: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", token).strip("-")
    return cleaned or "edge"


def _emit_filename(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
    safe = _safe_name(token)[:80]
    return f"{safe}--{digest}"


def _normalize_relationship_type(raw: str | None) -> str | None:
    if raw is None:
        return None
    token = raw.strip().casefold().replace("_", "-")
    token = _TYPE_ALIASES.get(token.replace("-", ""), token)
    token = _TYPE_ALIASES.get(token, token)
    if token in MVP_RELATIONSHIP_TYPES:
        return token
    return None


def _validate_edge_id(edge_id: str) -> str:
    token = edge_id.strip()
    if not token or not _EDGE_ID_RE.fullmatch(token):
        raise XprojEdgeError("edge-id-invalid")
    return token


def _validate_global_id(global_entity_id: str) -> str:
    token = global_entity_id.strip()
    if not token or not _GLOBAL_ID_RE.fullmatch(token):
        raise XprojEdgeError("global-entity-id-invalid")
    return token


def _validate_evidence(
    refs: Sequence[EvidenceRef | Mapping[str, str]],
) -> tuple[EvidenceRef, ...]:
    out: list[EvidenceRef] = []
    for item in refs:
        if isinstance(item, EvidenceRef):
            ref = item
        else:
            relative = str(item.get("relative_path") or "").strip()
            digest = str(item.get("sha256") or "").strip()
            if not relative or not _SHA256_RE.fullmatch(digest):
                raise XprojEdgeError("evidence-ref-invalid")
            if (
                relative.startswith(("/", "\\"))
                or "\\" in relative
                or ".." in Path(relative).parts
            ):
                raise XprojEdgeError(f"path-escape:{relative}")
            ref = EvidenceRef(relative_path=relative, sha256=digest)
        out.append(ref)
    if not out:
        raise XprojEdgeError("evidence-refs-required")
    out.sort(key=lambda item: (item.relative_path.casefold(), item.sha256))
    return tuple(out)


def _secret_findings_present(
    *,
    notes: str | None,
    extension_type: str | None,
) -> bool:
    blobs: list[str] = []
    if notes:
        blobs.append(notes)
    if extension_type:
        blobs.append(extension_type)
    return any(scan_text(blob) for blob in blobs)


def _projects_for_global(
    global_entity_id: str,
    joins: Sequence[JoinKeyRecord],
) -> tuple[str, ...]:
    projects = sorted(
        {
            join.project_id
            for join in joins
            if join.status == "joined" and join.global_entity_id == global_entity_id
        },
        key=str.casefold,
    )
    return tuple(projects)


def compute_edge_fingerprint(
    *,
    relationship_type: str,
    source_global_entity_id: str,
    target_global_entity_id: str,
    evidence_refs: Sequence[EvidenceRef],
    extension_type: str | None = None,
) -> str:
    """Deterministic SHA-256 over canonical edge identity + evidence digests."""
    payload = {
        "relationship_type": relationship_type,
        "source_global_entity_id": source_global_entity_id,
        "target_global_entity_id": target_global_entity_id,
        "extension_type": extension_type,
        "evidence_sha256": [ref.sha256 for ref in evidence_refs],
        "evidence_paths": [ref.relative_path for ref in evidence_refs],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _quarantine(
    *,
    category: EdgeQuarantineCategory,
    reason: str,
    inputs: Mapping[str, Any],
    edge_id: str | None = None,
    source: str | None = None,
    target: str | None = None,
    relationship_type: str | None = None,
) -> EdgeQuarantineCandidate:
    token = edge_id or source or target or category
    return EdgeQuarantineCandidate(
        candidate_id=f"q-edge-{category}-{_safe_name(str(token))}",
        category=category,
        reason=reason,
        inputs_considered=dict(inputs),
        edge_id=edge_id,
        source_global_entity_id=source,
        target_global_entity_id=target,
        relationship_type=relationship_type,
    )


def register_global_edge(
    *,
    edge_id: str | None,
    relationship_type: str | None,
    source_global_entity_id: str | None,
    target_global_entity_id: str | None,
    evidence_refs: Sequence[EvidenceRef | Mapping[str, str]],
    entities: Mapping[str, GlobalEntityRecord] | Sequence[GlobalEntityRecord],
    joins: Sequence[JoinKeyRecord],
    existing_edges: Sequence[GlobalEdgeRecord] = (),
    link_quality: str = "supported",
    extension_type: str | None = None,
    notes: str | None = None,
    mint_from_names: bool = False,
    fuzzy: bool = False,
    source_display_name: str | None = None,
    target_display_name: str | None = None,
) -> GlobalEdgeRecord | EdgeQuarantineCandidate:
    """Explicit cross-project edge. Name-only / fuzzy / missing globals fail closed."""
    if fuzzy:
        return _quarantine(
            category="fuzzy-edge-forbidden",
            reason="fuzzy-edge-forbidden",
            inputs={"source_display_name": source_display_name or ""},
        )
    if mint_from_names or source_display_name or target_display_name:
        return _quarantine(
            category="name-only-edge-forbidden",
            reason="name-only-edge-forbidden",
            inputs={
                "source_display_name": source_display_name or "",
                "target_display_name": target_display_name or "",
                "mint_from_names": mint_from_names,
            },
        )

    if (
        source_global_entity_id is None
        or target_global_entity_id is None
        or not str(source_global_entity_id).strip()
        or not str(target_global_entity_id).strip()
    ):
        return _quarantine(
            category="endpoint-guess-forbidden",
            reason="endpoint-guess-forbidden",
            inputs={
                "source_global_entity_id": source_global_entity_id or "",
                "target_global_entity_id": target_global_entity_id or "",
            },
        )

    if edge_id is None or not str(edge_id).strip():
        return _quarantine(
            category="edge-id-invalid",
            reason="edge-id-required",
            inputs={},
            source=str(source_global_entity_id).strip(),
            target=str(target_global_entity_id).strip(),
        )

    try:
        eid = _validate_edge_id(str(edge_id))
        source = _validate_global_id(str(source_global_entity_id))
        target = _validate_global_id(str(target_global_entity_id))
        refs = _validate_evidence(evidence_refs)
    except XprojEdgeError as exc:
        code = str(exc)
        if code.startswith("path-escape"):
            raise
        if code in {"evidence-refs-required", "evidence-ref-invalid"}:
            return _quarantine(
                category="evidence-refs-required",
                reason=code,
                inputs={"edge_id": str(edge_id).strip()},
                edge_id=str(edge_id).strip() if edge_id else None,
            )
        if code in {"edge-id-invalid", "global-entity-id-invalid"}:
            category: EdgeQuarantineCategory = (
                "edge-id-invalid" if code == "edge-id-invalid" else "endpoint-guess-forbidden"
            )
            return _quarantine(
                category=category,
                reason=code,
                inputs={"edge_id": str(edge_id or "")},
                edge_id=str(edge_id).strip() if edge_id else None,
            )
        raise

    if _secret_findings_present(notes=notes, extension_type=extension_type) or any(
        scan_text(part) for part in (eid, source, target)
    ):
        # AS-SEC-SCAN-XPROJ-ID-JSON-ESC-001: identity tokens scanned too.
        return _quarantine(
            category="secret-finding",
            reason="secret-finding",
            inputs={"edge_id": "[redacted-scan]", "notes": "[redacted-scan]"},
        )

    normalized = _normalize_relationship_type(relationship_type)
    if normalized is None:
        return _quarantine(
            category="unknown-relationship-type",
            reason="unknown-relationship-type",
            inputs={"relationship_type": relationship_type or ""},
            edge_id=eid,
            source=source,
            target=target,
            relationship_type=relationship_type,
        )

    if normalized == "extension":
        ext = (extension_type or "").strip()
        if not ext:
            return _quarantine(
                category="unknown-relationship-type",
                reason="extension-type-required",
                inputs={"relationship_type": "extension"},
                edge_id=eid,
                source=source,
                target=target,
                relationship_type="extension",
            )
    else:
        ext = None

    if source == target:
        return _quarantine(
            category="self-loop-forbidden",
            reason="self-loop-forbidden",
            inputs={"global_entity_id": source},
            edge_id=eid,
            source=source,
            target=target,
            relationship_type=normalized,
        )

    entity_map: dict[str, GlobalEntityRecord]
    if isinstance(entities, Mapping):
        entity_map = dict(entities)
    else:
        entity_map = {item.global_entity_id: item for item in entities}

    missing: list[str] = []
    if source not in entity_map:
        missing.append(source)
    if target not in entity_map:
        missing.append(target)
    if missing:
        return _quarantine(
            category="missing-endpoint-registration",
            reason="missing-endpoint-registration",
            inputs={"missing_global_entity_ids": missing},
            edge_id=eid,
            source=source,
            target=target,
            relationship_type=normalized,
        )

    source_projects = _projects_for_global(source, joins)
    target_projects = _projects_for_global(target, joins)
    union_projects = sorted(set(source_projects) | set(target_projects), key=str.casefold)
    if len(union_projects) < 2:
        return _quarantine(
            category="not-cross-project",
            reason="not-cross-project",
            inputs={
                "source_project_ids": list(source_projects),
                "target_project_ids": list(target_projects),
                "note": "Intra-project edges belong to AS-GRAPH-003",
            },
            edge_id=eid,
            source=source,
            target=target,
            relationship_type=normalized,
        )

    quality_raw = (link_quality or "supported").strip().casefold()
    if quality_raw not in {"verified", "supported", "inferred"}:
        quality_raw = "supported"
    quality: LinkQuality = quality_raw  # type: ignore[assignment]

    fingerprint = compute_edge_fingerprint(
        relationship_type=normalized,
        source_global_entity_id=source,
        target_global_entity_id=target,
        evidence_refs=refs,
        extension_type=ext,
    )

    for prior in existing_edges:
        if (
            prior.relationship_type == normalized
            and prior.source_global_entity_id == source
            and prior.target_global_entity_id == target
            and prior.edge_fingerprint != fingerprint
        ):
            return _quarantine(
                category="incompatible-duplicate-edge",
                reason="incompatible-duplicate-edge",
                inputs={
                    "prior_edge_id": prior.edge_id,
                    "prior_fingerprint": prior.edge_fingerprint,
                    "requested_fingerprint": fingerprint,
                },
                edge_id=eid,
                source=source,
                target=target,
                relationship_type=normalized,
            )

    record = GlobalEdgeRecord(
        edge_id=eid,
        relationship_type=normalized,
        source_global_entity_id=source,
        target_global_entity_id=target,
        evidence_refs=refs,
        edge_fingerprint=fingerprint,
        link_quality=quality,
        source_project_ids=source_projects,
        target_project_ids=target_projects,
        extension_type=ext,
        notes=_redact_reason(notes) if notes else None,
    )
    validate_record(record.as_dict(), "xproj-global-edge")
    return record


def apply_edge_registrations(
    requests: Sequence[Mapping[str, Any]],
    *,
    entities: Mapping[str, GlobalEntityRecord] | Sequence[GlobalEntityRecord],
    joins: Sequence[JoinKeyRecord],
    prior_edges: Sequence[GlobalEdgeRecord] | None = None,
) -> EdgeRegistryResult:
    """Deterministic batch apply for edges (stable sort)."""
    result = EdgeRegistryResult()
    edges: list[GlobalEdgeRecord] = list(prior_edges or ())

    edge_reqs = [item for item in requests if item.get("kind", "edge") == "edge"]
    edge_reqs.sort(
        key=lambda item: (
            str(item.get("edge_id") or "").casefold(),
            str(item.get("relationship_type") or "").casefold(),
            str(item.get("source_global_entity_id") or "").casefold(),
            str(item.get("target_global_entity_id") or "").casefold(),
        )
    )

    new_edges: list[GlobalEdgeRecord] = []
    for req in edge_reqs:
        raw_refs = req.get("evidence_refs") or []
        evidence: list[EvidenceRef | Mapping[str, str]] = []
        if isinstance(raw_refs, Sequence) and not isinstance(raw_refs, (str, bytes)):
            for item in raw_refs:
                if isinstance(item, (EvidenceRef, Mapping)):
                    evidence.append(item)

        outcome = register_global_edge(
            edge_id=str(req["edge_id"]) if req.get("edge_id") is not None else None,
            relationship_type=(
                str(req["relationship_type"]) if req.get("relationship_type") is not None else None
            ),
            source_global_entity_id=(
                str(req["source_global_entity_id"])
                if req.get("source_global_entity_id") is not None
                else None
            ),
            target_global_entity_id=(
                str(req["target_global_entity_id"])
                if req.get("target_global_entity_id") is not None
                else None
            ),
            evidence_refs=evidence,
            entities=entities,
            joins=joins,
            existing_edges=edges + new_edges,
            link_quality=str(req.get("link_quality") or "supported"),
            extension_type=(
                str(req["extension_type"]) if req.get("extension_type") is not None else None
            ),
            notes=str(req["notes"]) if req.get("notes") is not None else None,
            mint_from_names=bool(req.get("mint_from_names")),
            fuzzy=bool(req.get("fuzzy")),
            source_display_name=(
                str(req["source_display_name"])
                if req.get("source_display_name") is not None
                else None
            ),
            target_display_name=(
                str(req["target_display_name"])
                if req.get("target_display_name") is not None
                else None
            ),
        )
        if isinstance(outcome, EdgeQuarantineCandidate):
            result.quarantine.append(outcome)
            continue
        if any(
            existing.edge_id == outcome.edge_id
            and existing.edge_fingerprint == outcome.edge_fingerprint
            for existing in edges + new_edges
        ) or any(
            existing.relationship_type == outcome.relationship_type
            and existing.source_global_entity_id == outcome.source_global_entity_id
            and existing.target_global_entity_id == outcome.target_global_entity_id
            and existing.edge_fingerprint == outcome.edge_fingerprint
            for existing in edges + new_edges
        ):
            continue
        colliding = [
            existing
            for existing in edges + new_edges
            if existing.edge_id == outcome.edge_id
            and existing.edge_fingerprint != outcome.edge_fingerprint
        ]
        if colliding:
            prior = colliding[0]
            result.quarantine.append(
                _quarantine(
                    category="incompatible-duplicate-edge",
                    reason="edge-id-fingerprint-collision",
                    inputs={
                        "edge_id": outcome.edge_id,
                        "prior_fingerprint": prior.edge_fingerprint,
                        "requested_fingerprint": outcome.edge_fingerprint,
                    },
                    edge_id=outcome.edge_id,
                    source=outcome.source_global_entity_id,
                    target=outcome.target_global_entity_id,
                    relationship_type=outcome.relationship_type,
                )
            )
            continue
        new_edges.append(outcome)
        result.edges.append(outcome)

    result.edges.sort(key=lambda item: item.edge_id.casefold())
    result.quarantine.sort(key=lambda item: item.candidate_id.casefold())
    return result


def inspect_edge_registry(result: EdgeRegistryResult) -> dict[str, Any]:
    """Observability summary — counts only; no secret payloads."""
    return {
        "package_id": PACKAGE_ID,
        "authority_level": AUTHORITY_LEVEL,
        "truth_boundary": TRUTH_BOUNDARY,
        "registered": result.registered_count,
        "quarantined": result.quarantined_count,
    }


def _safe_vault_relative(vault: Path, relative: str) -> Path:
    if relative.startswith(("/", "\\")) or "\\" in relative or ".." in Path(relative).parts:
        raise XprojEdgeError(f"path-escape:{relative}")
    if not any(relative.startswith(prefix) for prefix in ALLOWED_WRITE_PREFIXES):
        raise XprojEdgeError(f"forbidden-write-prefix:{relative}")
    if any(relative.startswith(prefix) for prefix in _FORBIDDEN_WRITE_PREFIXES):
        raise XprojEdgeError(f"forbidden-write-prefix:{relative}")
    root = vault.expanduser().resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise XprojEdgeError(f"path-escape:{relative}")
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


def load_edge_registry_state(vault: Path) -> list[GlobalEdgeRecord]:
    """Load previously persisted edges from ``state/global-entities/edges/``."""
    vault = vault.expanduser().resolve()
    edges: list[GlobalEdgeRecord] = []
    root = vault / "state" / "global-entities" / "edges"
    if not root.is_dir():
        return edges

    for path in sorted(root.glob("*.json"), key=lambda item: item.name.casefold()):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise XprojEdgeError(f"malformed-edge:{path.name}")
        validate_record(payload, "xproj-global-edge")
        refs = _validate_evidence(payload.get("evidence_refs") or [])
        edges.append(
            GlobalEdgeRecord(
                edge_id=str(payload["edge_id"]),
                relationship_type=str(payload["relationship_type"]),
                source_global_entity_id=str(payload["source_global_entity_id"]),
                target_global_entity_id=str(payload["target_global_entity_id"]),
                evidence_refs=refs,
                edge_fingerprint=str(payload["edge_fingerprint"]),
                link_quality=payload["link_quality"],
                source_project_ids=tuple(
                    str(p) for p in (payload.get("source_project_ids") or [])
                ),
                target_project_ids=tuple(
                    str(p) for p in (payload.get("target_project_ids") or [])
                ),
                extension_type=(
                    str(payload["extension_type"])
                    if payload.get("extension_type") is not None
                    else None
                ),
                notes=str(payload["notes"]) if payload.get("notes") is not None else None,
            )
        )
    return edges


def write_edge_outputs(
    result: EdgeRegistryResult,
    *,
    vault: Path,
) -> list[str]:
    """Optional deterministic vault emits under edge prefixes only."""
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise XprojEdgeError(f"vault-missing:{vault}")

    written: list[str] = []
    planned: dict[str, str] = {}

    def _plan(relative: str, content: str) -> None:
        if relative in planned and planned[relative] != content:
            raise XprojEdgeError(f"emit-path-collision:{relative}")
        path = _safe_vault_relative(vault, relative)
        if relative in planned:
            return
        planned[relative] = content
        _write_atomic(path, content)
        written.append(relative)

    for record in result.edges:
        safe = _emit_filename(record.edge_id)
        relative = f"state/global-entities/edges/{safe}.json"
        validate_record(record.as_dict(), "xproj-global-edge")
        _plan(relative, record.to_json())

    for candidate in result.quarantine:
        safe = _emit_filename(candidate.candidate_id)
        relative = f"state/global-entities/edge-quarantine/{safe}.json"
        validate_record(candidate.as_dict(), "xproj-edge-quarantine")
        _plan(relative, candidate.to_json())

    written.sort()
    return written


def promote_edge_path_forbidden(relative: str) -> None:
    """Public helper for tests: assert a relative path is rejected by path policy."""
    _safe_vault_relative(Path("."), relative)


def load_entities_and_joins_for_edges(
    vault: Path,
) -> tuple[dict[str, GlobalEntityRecord], list[JoinKeyRecord]]:
    """Consume-only load of XPROJ-001 registry state for edge endpoint checks."""
    return load_registry_state(vault)


__all__ = [
    "ALLOWED_WRITE_PREFIXES",
    "AUTHORITY_LEVEL",
    "MVP_RELATIONSHIP_TYPES",
    "PACKAGE_ID",
    "TRUTH_BOUNDARY",
    "EdgeQuarantineCandidate",
    "EdgeRegistryResult",
    "GlobalEdgeRecord",
    "XprojEdgeError",
    "apply_edge_registrations",
    "compute_edge_fingerprint",
    "inspect_edge_registry",
    "load_edge_registry_state",
    "load_entities_and_joins_for_edges",
    "promote_edge_path_forbidden",
    "register_global_edge",
    "write_edge_outputs",
]
