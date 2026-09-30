"""AT3-062 — Isolated DECIDED_BY provenance.

Declared derived edges only. Confirmed decisions require owner_origin.
Graph != authority. Missing declarations stay UNKNOWN. Model != owner.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from project_atlas.atlas3.contracts import (
    OPS_RELATIVE,
    TRUTH_BOUNDARY,
    Atlas3Error,
    honesty_block,
    require_project,
    require_vault,
)
from project_atlas.atlas3.twin import make_relationship

PACKAGE_ID: Final[str] = "AT3-062"
DECLARED_NAME: Final[str] = "declared.json"
ALLOWED_RELATIONSHIP: Final[str] = "DECIDED_BY"


def _declared_path(vault: Path, project_id: str) -> Path:
    return vault / OPS_RELATIVE / "decided-by" / project_id / DECLARED_NAME


def _valid_owner_origin(origin: dict[str, Any] | None) -> bool:
    if not isinstance(origin, dict):
        return False
    return (
        origin.get("evidence_kind") == "explicit_owner_statement"
        and str(origin.get("origin") or "").lower() == "owner"
        and bool(str(origin.get("statement") or "").strip())
    )


def _load_declared(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Atlas3Error(
            "DECIDED_BY_CORRUPT",
            "declared DECIDED_BY graph is not readable JSON",
        ) from exc
    if not isinstance(raw, dict):
        raise Atlas3Error("DECIDED_BY_CORRUPT", "declared DECIDED_BY graph must be an object")
    return raw


def _edges(raw: object, *, project_id: str) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise Atlas3Error("DECIDED_BY_CORRUPT", "edges must be a list")
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise Atlas3Error("DECIDED_BY_CORRUPT", "edges row is not an object")
        relationship = str(item.get("relationship") or ALLOWED_RELATIONSHIP).strip().upper()
        if relationship != ALLOWED_RELATIONSHIP:
            raise Atlas3Error(
                "DECIDED_RELATIONSHIP_INVALID",
                f"DECIDED_BY graph allows only {ALLOWED_RELATIONSHIP}",
            )
        from_id = str(item.get("from_id") or item.get("from") or "").strip()
        to_id = str(item.get("to_id") or item.get("to") or "").strip()
        if not from_id or not to_id:
            raise Atlas3Error("DECIDED_IDENTITY_INCOMPLETE", "edge requires from_id and to_id")
        explicit = item.get("project_id")
        if explicit is not None and str(explicit) != project_id:
            raise Atlas3Error(
                "CROSS_PROJECT",
                f"DECIDED_BY edge project_id {explicit!r} != requested {project_id!r}",
            )
        origin = item.get("owner_origin")
        if not _valid_owner_origin(origin if isinstance(origin, dict) else None):
            raise Atlas3Error(
                "FALSE_OWNER_DECISION",
                "DECIDED_BY requires explicit owner_origin",
            )
        evidence = item.get("evidence_refs") or item.get("evidence")
        if not isinstance(evidence, list):
            raise Atlas3Error("PROVENANCE_REQUIRED", f"{from_id}->{to_id} requires evidence_refs")
        refs = [str(ref).strip() for ref in evidence if str(ref).strip()]
        row = make_relationship(
            relationship=ALLOWED_RELATIONSHIP,
            from_id=from_id,
            to_id=to_id,
            project_id=project_id,
            evidence_refs=refs,
        )
        row["package"] = PACKAGE_ID
        row["owner_origin"] = {
            "evidence_kind": "explicit_owner_statement",
            "origin": "owner",
        }
        row["model_is_owner"] = False
        rows.append(row)
    return rows


def compile_decided_by(vault: Path | str, project_id: str) -> dict[str, Any]:
    root = require_vault(vault)
    pid = require_project(root, project_id)
    path = _declared_path(root, pid)
    if not path.is_file():
        return {
            "package": PACKAGE_ID,
            "project_id": pid,
            "relationship": ALLOWED_RELATIONSHIP,
            "edges": [],
            "counts": {"edges": 0},
            "status": "UNKNOWN",
            "reason": "NO_DECLARED_DECIDED_BY",
            "graph_is_authority": False,
            "model_is_owner": False,
            "promoted_to_truth_core": 0,
            "truth_boundary": TRUTH_BOUNDARY,
            "honesty": honesty_block(),
        }
    declared = _load_declared(path)
    declared_project = str(declared.get("project_id") or "").strip()
    if declared_project and declared_project != pid:
        raise Atlas3Error(
            "CROSS_PROJECT",
            "declared DECIDED_BY project_id does not match request",
        )
    if declared.get("graph_is_authority") is True:
        raise Atlas3Error(
            "GRAPH_AUTHORITY_CLAIMED",
            "DECIDED_BY graph must not claim graph authority",
        )
    edges = _edges(declared.get("edges"), project_id=pid)
    return {
        "package": PACKAGE_ID,
        "project_id": pid,
        "relationship": ALLOWED_RELATIONSHIP,
        "edges": edges,
        "counts": {"edges": len(edges)},
        "status": "derived",
        "reason": "DECLARED_DECIDED_BY",
        "graph_is_authority": False,
        "model_is_owner": False,
        "promoted_to_truth_core": 0,
        "truth_boundary": TRUTH_BOUNDARY,
        "honesty": honesty_block(),
    }
