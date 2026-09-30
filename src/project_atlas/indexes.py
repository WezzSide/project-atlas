"""Deterministic lexical indexes for the Atlas Vault (AS-RET-001)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from project_atlas.conflict_projections import (
    conflict_index_companions,
    review_index_companions,
)
from project_atlas.ingestion import _promote

GENERATED_INDEX_ROOT = "generated/indexes"
LEGACY_INDEX_ROOT = "indexes"


def _ensure_no_legacy_indexes(vault: Path) -> None:
    legacy = vault / LEGACY_INDEX_ROOT
    if legacy.exists():
        raise ValueError(
            f"obsolete generated index directory: {legacy}; remove it before rebuilding "
            f"{GENERATED_INDEX_ROOT}"
        )


def _json(path: Path, default: Any, overlay: dict[Path, bytes] | None = None) -> Any:
    if overlay is not None and path in overlay:
        return json.loads(overlay[path].decode("utf-8"))
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


_AGGREGATE_STATE_STEMS = frozenset(
    {"claims", "concepts", "authorities", "authority", "conflicts"}
)


def _item_bound_to_stem(item: dict[str, Any], stem: str) -> bool:
    """Filename stem is project ownership. In-record project_id is not authority.

    AS-RET-FILENAME-PROJECT-BIND-001: ``state/<kind>/<project>.json`` (and
    ``review/conflicts/<project>.json``) must not contribute records that
    declare a different ``project_id``. A planted sibling file cannot
    enter another project's lexical index.

    Aggregate test/legacy stems (``claims.json``, ``conflicts.json``) are
    still loaded; scoped consumers filter them by requested project_id.
    Those names are not a cross-project forge of ``attacker.json``.
    """
    if stem in _AGGREGATE_STATE_STEMS:
        return True
    value = item.get("project_id")
    return isinstance(value, str) and value.strip() == stem


def _add(index: dict[str, list[str]], key: object, value: str) -> None:
    index.setdefault(str(key), []).append(value)


def _sorted_index(index: dict[str, list[str]]) -> dict[str, list[str]]:
    return {key: sorted(set(values)) for key, values in sorted(index.items())}


def _state_records(
    vault: Path, directory: str, key: str, overlay: dict[Path, bytes] | None = None
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    root = vault / "state" / directory
    paths_set = set(root.glob("*.json")) if root.is_dir() else set()
    paths_set.update(path for path in (overlay or {}) if path.parent == root)
    paths = sorted(paths_set)
    for path in paths:
        raw = _json(path, {}, overlay)
        for item in raw.get(key, []) if isinstance(raw, dict) else []:
            if isinstance(item, dict) and _item_bound_to_stem(item, path.stem):
                result.append(item)
    return result


def _claim_index(vault: Path, overlay: dict[Path, bytes] | None = None) -> dict[str, Any]:
    records = _state_records(vault, "claims", "claims", overlay)
    by_id: dict[str, list[str]] = {}
    by_lineage: dict[str, list[str]] = {}
    by_concept: dict[str, list[str]] = {}
    by_field: dict[str, list[str]] = {}
    for claim in records:
        claim_id = str(claim["claim_id"])
        _add(by_id, claim_id, claim_id)
        _add(by_concept, claim.get("subject", ""), claim_id)
        _add(by_field, claim.get("field", ""), claim_id)
        for lineage in claim.get("source_lineage_ids", []):
            _add(by_lineage, lineage, claim_id)
        for provenance in claim.get("provenance", []):
            if isinstance(provenance, dict) and provenance.get("source_lineage_id"):
                _add(by_lineage, provenance["source_lineage_id"], claim_id)
    return {
        "schema_version": 1,
        "ids": sorted(by_id),
        "by_claim_id": _sorted_index(by_id),
        "by_source_lineage_id": _sorted_index(by_lineage),
        "by_concept_id": _sorted_index(by_concept),
        "by_field": _sorted_index(by_field),
    }


def _concept_index(vault: Path, overlay: dict[Path, bytes] | None = None) -> dict[str, Any]:
    records = _state_records(vault, "concepts", "concepts", overlay)
    by_id: dict[str, list[str]] = {}
    by_type: dict[str, list[str]] = {}
    by_project: dict[str, list[str]] = {}
    by_tag: dict[str, list[str]] = {}
    by_relationship: dict[str, list[str]] = {}
    for concept in records:
        concept_id = str(concept["concept_id"])
        _add(by_id, concept_id, concept_id)
        _add(by_type, concept.get("type", ""), concept_id)
        _add(by_project, concept.get("project_id", ""), concept_id)
        for tag in concept.get("tags", []):
            _add(by_tag, tag, concept_id)
        for relationship in concept.get("relationships", []):
            if isinstance(relationship, dict):
                _add(by_relationship, relationship.get("target", ""), concept_id)
    return {
        "schema_version": 1,
        "ids": sorted(by_id),
        "by_concept_id": _sorted_index(by_id),
        "by_type": _sorted_index(by_type),
        "by_project_id": _sorted_index(by_project),
        "by_tag": _sorted_index(by_tag),
        "by_relationship_target": _sorted_index(by_relationship),
    }


def _conflict_index(vault: Path, overlay: dict[Path, bytes] | None = None) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    root = vault / "review" / "conflicts"
    paths_set = set(root.glob("*.json")) if root.is_dir() else set()
    paths_set.update(path for path in (overlay or {}) if path.parent == root)
    paths = sorted(paths_set)
    for path in paths:
        raw = _json(path, {}, overlay)
        records.extend(
            item
            for item in raw.get("entries", [])
            if isinstance(item, dict) and _item_bound_to_stem(item, path.stem)
        )
    by_id: dict[str, list[str]] = {}
    by_pair: dict[str, list[str]] = {}
    for conflict in records:
        conflict_id = str(conflict["conflict_id"])
        _add(by_id, conflict_id, conflict_id)
        claim_ids = sorted(str(item) for item in conflict.get("claim_ids", []))
        if claim_ids:
            _add(by_pair, "|".join(claim_ids), conflict_id)
    # AS-CORE2-008: additive companion keys only (C8-FR-003).
    companions = conflict_index_companions(records)
    return {
        "schema_version": 1,
        "ids": sorted(by_id),
        "by_conflict_id": _sorted_index(by_id),
        "by_claim_pair": _sorted_index(by_pair),
        "by_source_id": _sorted_index(companions["by_source_id"]),
        "by_source_lineage_id": _sorted_index(companions["by_source_lineage_id"]),
        "by_project_id": _sorted_index(companions["by_project_id"]),
    }


def _review_index(vault: Path, overlay: dict[Path, bytes] | None = None) -> dict[str, Any]:
    """Lexical companion over ``review/pending/**`` (C8-FR-005) — not a queue root."""
    records: list[dict[str, Any]] = []
    root = vault / "review" / "pending"
    paths_set = set(root.glob("*.json")) if root.is_dir() else set()
    paths_set.update(path for path in (overlay or {}) if path.parent == root)
    paths = sorted(paths_set)
    for path in paths:
        raw = _json(path, {}, overlay)
        records.extend(item for item in raw.get("entries", []) if isinstance(item, dict))
    return review_index_companions(records)


def _authority_index(
    vault: Path, overlay: dict[Path, bytes] | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    records = _state_records(vault, "authority", "authorities", overlay)
    by_id: dict[str, list[str]] = {}
    by_lineage: dict[str, list[str]] = {}
    by_source: dict[str, list[str]] = {}
    for authority in records:
        authority_id = str(authority["authority_id"])
        _add(by_id, authority_id, authority_id)
        for lineage in authority.get("source_lineage_ids", []):
            _add(by_lineage, lineage, authority_id)
        for source_id in authority.get("source_ids", []):
            _add(by_source, source_id, authority_id)
    authority_index = {
        "schema_version": 1,
        "ids": sorted(by_id),
        "by_authority_id": _sorted_index(by_id),
        "by_source_lineage_id": _sorted_index(by_lineage),
        "by_source_id": _sorted_index(by_source),
        "by_receipt_id": {},
    }
    provenance_by_lineage: dict[str, list[str]] = {}
    provenance_by_receipt: dict[str, list[str]] = {}
    sources: list[dict[str, Any]] = [
        *_state_records(vault, "claims", "claims", overlay),
        *_state_records(vault, "concepts", "concepts", overlay),
    ]
    conflict_root = vault / "review" / "conflicts"
    conflict_paths_set = (
        set(conflict_root.glob("*.json")) if conflict_root.is_dir() else set()
    )
    conflict_paths_set.update(path for path in (overlay or {}) if path.parent == conflict_root)
    conflict_paths = sorted(conflict_paths_set)
    for path in conflict_paths:
        raw = _json(path, {}, overlay)
        sources.extend(
            item
            for item in raw.get("entries", [])
            if isinstance(item, dict) and _item_bound_to_stem(item, path.stem)
        )
    for record in sources:
        record_id = str(
            record.get("claim_id") or record.get("concept_id") or record.get("conflict_id")
        )
        for provenance in record.get("provenance", record.get("sources", [])):
            if not isinstance(provenance, dict):
                continue
            lineage = provenance.get("source_lineage_id")
            receipt = provenance.get("receipt_id")
            if lineage:
                _add(provenance_by_lineage, lineage, record_id)
            if receipt:
                _add(provenance_by_receipt, receipt, record_id)
    provenance_index: dict[str, Any] = {
        "schema_version": 1,
        "by_source_lineage_id": _sorted_index(provenance_by_lineage),
        "by_receipt_id": _sorted_index(provenance_by_receipt),
    }
    return authority_index, provenance_index


def _source_index(vault: Path, overlay: dict[Path, bytes] | None = None) -> dict[str, Any]:
    raw = _json(vault / "state" / "sources.json", {"sources": []}, overlay)
    by_lineage: dict[str, list[str]] = {}
    by_source: dict[str, list[str]] = {}
    by_project: dict[str, list[str]] = {}
    by_current_path: dict[str, list[str]] = {}
    by_historical_path: dict[str, list[str]] = {}
    for source in raw.get("sources", []) if isinstance(raw, dict) else []:
        if not isinstance(source, dict):
            continue
        lineage = str(source.get("source_lineage_id", ""))
        source_id = str(source.get("source_id", ""))
        _add(by_lineage, lineage, lineage)
        _add(by_source, source_id, lineage)
        _add(by_project, source.get("canonical_project_id", ""), lineage)
        _add(by_current_path, source.get("current_path", ""), lineage)
        for history in source.get("path_history", []):
            if isinstance(history, dict):
                _add(by_historical_path, history.get("path", ""), lineage)
    return {
        "schema_version": 1,
        "ids": sorted(key for key in by_lineage if key),
        "by_source_lineage_id": _sorted_index(by_lineage),
        "by_source_id": _sorted_index(by_source),
        "by_project_uuid": _sorted_index(by_project),
        "by_current_path": _sorted_index(by_current_path),
        "by_historical_path": _sorted_index(by_historical_path),
    }


def _write_plan(vault: Path, plan: dict[str, Any]) -> dict[Path, bytes]:
    return {
        vault / GENERATED_INDEX_ROOT / name: (
            json.dumps(value, indent=2, sort_keys=True) + "\n"
        ).encode()
        for name, value in sorted(plan.items())
    }


def canonical_index_payloads(
    vault: Path, overlay: dict[Path, bytes] | None = None
) -> dict[Path, bytes]:
    """Return generated lexical-index writes from disk plus staged state."""
    _ensure_no_legacy_indexes(vault)
    plan: dict[str, Any] = {
        "sources.json": _source_index(vault, overlay),
        "claims.json": _claim_index(vault, overlay),
        "concepts.json": _concept_index(vault, overlay),
        "conflicts.json": _conflict_index(vault, overlay),
        # AS-CORE2-008 companion — regenerable lexical view of pending reviews.
        "reviews.json": _review_index(vault, overlay),
    }
    authority_index, provenance_index = _authority_index(vault, overlay)
    plan["authority.json"] = authority_index
    plan["provenance.json"] = provenance_index
    return _write_plan(vault, plan)


def build_indexes(vault: Path) -> dict[str, int | bool]:
    """Build generated navigation and lexical indexes through Core promotion."""
    vault = vault.expanduser().resolve()
    projects_root = vault / "projects"
    projects = (
        sorted(path for path in projects_root.iterdir() if path.is_dir())
        if projects_root.is_dir()
        else []
    )
    project_lines = ["# Projects", ""]
    for project in projects:
        project_lines.append(
            f"- [{project.name}](../../projects/{project.name}/project.md)"
        )
    portfolio_lines = ["# Portfolio", "", "Generated from canonical project projections.", ""]
    for project in projects:
        portfolio_lines.append(f"- [{project.name}](../projects/{project.name}/project.md)")
    source_root = vault / "sources" / "imported-documents"
    source_files = sorted(source_root.glob("*")) if source_root.is_dir() else []
    source_lines = ["# Sources", ""] + [
        f"- [{path.name}](imported-documents/{path.name})" for path in source_files
    ]
    writes = canonical_index_payloads(vault)
    writes.update(
        {
            vault / "generated" / "navigation" / "projects.md": (
                "\n".join(project_lines) + "\n"
            ).encode(),
            vault / "generated" / "navigation" / "portfolio.md": (
                "\n".join(portfolio_lines).replace("../projects/", "../../projects/") + "\n"
            ).encode(),
            vault / "generated" / "navigation" / "sources.md": (
                "\n".join(source_lines).replace(
                    "imported-documents/", "../../sources/imported-documents/"
                )
                + "\n"
            ).encode(),
        }
    )
    _promote(writes)
    return {"ok": True, "projects": len(projects), "sources": len(source_files)}
