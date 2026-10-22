"""AS-J-005 — Derived impact graph from canonical relationship store.

Consumes ``generated/graph/relationships/**/*.json`` (AS-GRAPH-003) and emits
a deterministic derived impact projection. Graph ≠ authority — never elevates
winners or trust scores.

Hard rules:
- READ relationship store only; do not rewrite relationship records.
- Output under generated/indexes/ only.
- No wall-clock stamps; no apps/web dual-own; no INT retention dual-own.
- Forbidden payload keys: trust_score, authority_winner (never emitted).
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from project_atlas.schema import validate_record
from project_atlas.secrets import scan_text

PACKAGE_ID = "AS-J-005"
GENERATOR_ID = "atlas-j-005"
SCHEMA_KIND = "impact-graph"
AUTHORITY_LEVEL = "derived"
TRUTH_BOUNDARY = "IMPACT GRAPH ≠ AUTOMATIC AUTHORITY"

OUTPUT_RELATIVE = Path("generated") / "indexes" / "impact-graph.json"
RELATIONSHIPS_ROOT = Path("generated") / "graph" / "relationships"

# Impact polarity: when entity X changes, which entities are impacted?
# Maps relationship_type → how (source, target) produce impact edges (from→to
# means "from impacts to" when from changes).
_IMPACT_DIRECTION: dict[str, str] = {
    "depends-on": "target_to_source",  # B changes → impacts A (A depends-on B)
    "derived-from": "target_to_source",
    "documents": "source_to_target",
    "validates": "source_to_target",
    "extension": "source_to_target",
    "supersedes": "source_to_target",
    "part-of": "bidirectional",
    "conflicts-with": "bidirectional",
}

_FORBIDDEN_WRITE_PREFIXES: tuple[str, ...] = (
    "generated/graph/relationships/",
    "generated/graph/projections/",
    "generated/graph/relationship-quarantine/",
    "generated/graph/resolved/",
    "generated/graph/quarantine-candidates/",
    "generated/graph/acceptance/",
    "relationships/",
    "claims/",
    "apps/web/",
    "state/",
    "projects/",
)

_FORBIDDEN_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {"trust_score", "authority_winner", "confidence_score"}
)


class ImpactGraphError(ValueError):
    """Raised when impact graph compilation cannot proceed safely."""


def _inside(vault: Path, path: Path) -> Path:
    root = vault.expanduser().resolve()
    resolved = path.expanduser().resolve()
    if not resolved.is_relative_to(root):
        raise ImpactGraphError(f"path escapes vault root: {path}")
    return resolved


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp_name)
        raise


def promote_impact_to_authority_forbidden() -> None:
    """Hard firewall: impact graph must never promote to authority."""
    raise ImpactGraphError("impact-to-authority-forbidden")


def promote_impact_path_forbidden(relative: str) -> None:
    """Reject writes outside generated/indexes/impact-graph.json."""
    if (
        relative.startswith(("/", "\\"))
        or "\\" in relative
        or ".." in Path(relative).parts
        or relative.startswith("../")
    ):
        raise ImpactGraphError(f"path-escape:{relative}")
    if any(relative.startswith(prefix) for prefix in _FORBIDDEN_WRITE_PREFIXES):
        raise ImpactGraphError(f"forbidden-write-prefix:{relative}")
    if relative != OUTPUT_RELATIVE.as_posix():
        raise ImpactGraphError(f"forbidden-write-prefix:{relative}")


def _load_relationships(vault: Path) -> list[dict[str, Any]]:
    root = vault / RELATIONSHIPS_ROOT
    records: list[dict[str, Any]] = []
    if not root.is_dir():
        return records
    for path in sorted(root.rglob("*.json")):
        rel = path.relative_to(vault).as_posix()
        if ".." in Path(rel).parts:
            raise ImpactGraphError(f"path-escape:{rel}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ImpactGraphError(f"unreadable relationship record {rel}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ImpactGraphError(f"relationship record must be object: {rel}")
        validate_record(payload, "graph-relationship")
        identity_fields = (
            str(payload.get("source_entity_id") or ""),
            str(payload.get("target_entity_id") or ""),
            str(payload.get("project_id") or ""),
            str(payload.get("relationship_id") or ""),
        )
        if any(scan_text(field) for field in identity_fields):
            continue
        records.append(payload)
    records.sort(
        key=lambda item: (
            str(item.get("project_id", "")).casefold(),
            str(item.get("relationship_id", "")).casefold(),
        )
    )
    return records


def _edge_pairs(source: str, target: str, direction: str) -> list[tuple[str, str]]:
    if source == target:
        return []
    if direction == "source_to_target":
        return [(source, target)]
    if direction == "target_to_source":
        return [(target, source)]
    if direction == "bidirectional":
        return sorted({(source, target), (target, source)})
    raise ImpactGraphError(f"unknown impact direction: {direction}")


def compile_impact_graph(vault: Path) -> dict[str, Any]:
    """Compile derived impact graph document from relationship store (in-memory)."""
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise ImpactGraphError(f"vault missing: {vault}")
    relationships = _load_relationships(vault)
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for rel in relationships:
        project_id = str(rel["project_id"])
        rel_type = str(rel["relationship_type"])
        source = str(rel["source_entity_id"])
        target = str(rel["target_entity_id"])
        direction = _IMPACT_DIRECTION.get(rel_type, "source_to_target")
        for frm, to in _edge_pairs(source, target, direction):
            key = (project_id, frm, to, rel_type)
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                {
                    "project_id": project_id,
                    "from_entity_id": frm,
                    "to_entity_id": to,
                    "via_relationship_type": rel_type,
                    "via_relationship_id": str(rel["relationship_id"]),
                }
            )
    edges.sort(
        key=lambda item: (
            item["project_id"].casefold(),
            item["from_entity_id"].casefold(),
            item["to_entity_id"].casefold(),
            item["via_relationship_type"].casefold(),
            item["via_relationship_id"].casefold(),
        )
    )
    entities: dict[str, dict[str, Any]] = {}
    for edge in edges:
        for entity_id in (edge["from_entity_id"], edge["to_entity_id"]):
            bucket = entities.setdefault(
                entity_id,
                {
                    "entity_id": entity_id,
                    "impacts": [],
                    "impacted_by": [],
                },
            )
            if (
                entity_id == edge["from_entity_id"]
                and edge["to_entity_id"] not in bucket["impacts"]
            ):
                bucket["impacts"].append(edge["to_entity_id"])
            if (
                entity_id == edge["to_entity_id"]
                and edge["from_entity_id"] not in bucket["impacted_by"]
            ):
                bucket["impacted_by"].append(edge["from_entity_id"])
    for bucket in entities.values():
        bucket["impacts"] = sorted(bucket["impacts"], key=str.casefold)
        bucket["impacted_by"] = sorted(bucket["impacted_by"], key=str.casefold)
    entity_list = [entities[eid] for eid in sorted(entities, key=str.casefold)]
    document = {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "generated": {"by": GENERATOR_ID},
        "authority": {
            "level": AUTHORITY_LEVEL,
            "note": "Derived impact projection; never automatic authority.",
        },
        "truth_boundary": TRUTH_BOUNDARY,
        "relationship_count": len(relationships),
        "edge_count": len(edges),
        "entity_count": len(entity_list),
        "entities": entity_list,
        "edges": edges,
    }
    raw = json.dumps(document).lower()
    for forbidden_key in _FORBIDDEN_PAYLOAD_KEYS:
        if forbidden_key in raw:
            raise ImpactGraphError(f"forbidden payload key leaked: {forbidden_key}")
    validate_record(document, SCHEMA_KIND)
    return document


def impacted_entity_ids(
    document: dict[str, Any],
    *,
    changed_entity_id: str,
) -> list[str]:
    """Return entities impacted when ``changed_entity_id`` changes (sorted)."""
    for entity in document.get("entities", []):
        if entity.get("entity_id") == changed_entity_id:
            impacts = entity.get("impacts", [])
            return sorted((str(item) for item in impacts), key=str.casefold)
    return []


def inspect_impact_graph(document: dict[str, Any]) -> dict[str, Any]:
    """Library observability: counts only; never winners."""
    return {
        "package_id": document.get("package_id", PACKAGE_ID),
        "authority": dict(document.get("authority", {"level": AUTHORITY_LEVEL})),
        "truth_boundary": document.get("truth_boundary", TRUTH_BOUNDARY),
        "relationship_count": document.get("relationship_count", 0),
        "edge_count": document.get("edge_count", 0),
        "entity_count": document.get("entity_count", 0),
    }


def write_impact_graph(vault: Path, *, document: dict[str, Any] | None = None) -> str:
    """Write impact graph under generated/indexes/impact-graph.json."""
    vault = vault.expanduser().resolve()
    relative = OUTPUT_RELATIVE.as_posix()
    promote_impact_path_forbidden(relative)
    payload = document if document is not None else compile_impact_graph(vault)
    validate_record(payload, SCHEMA_KIND)
    path = _inside(vault, vault / OUTPUT_RELATIVE)
    _write_atomic(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return relative
