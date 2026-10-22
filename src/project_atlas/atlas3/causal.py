"""AT3-060 — Isolated causal graph (CAUSED_BY).

Declared derived edges only. Graph != authority.
Missing declarations stay UNKNOWN. Does not invent causality.
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

PACKAGE_ID: Final[str] = "AT3-060"
DECLARED_NAME: Final[str] = "declared.json"
ALLOWED_RELATIONSHIP: Final[str] = "CAUSED_BY"


def _declared_path(vault: Path, project_id: str) -> Path:
    return vault / OPS_RELATIVE / "causal-graph" / project_id / DECLARED_NAME


def _load_declared(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Atlas3Error(
            "CAUSAL_GRAPH_CORRUPT",
            "declared causal graph is not readable JSON",
        ) from exc
    if not isinstance(raw, dict):
        raise Atlas3Error("CAUSAL_GRAPH_CORRUPT", "declared causal graph must be an object")
    return raw


def _edges(raw: object, *, project_id: str) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise Atlas3Error("CAUSAL_GRAPH_CORRUPT", "edges must be a list")
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise Atlas3Error("CAUSAL_GRAPH_CORRUPT", "edges row is not an object")
        relationship = str(item.get("relationship") or ALLOWED_RELATIONSHIP).strip().upper()
        if relationship != ALLOWED_RELATIONSHIP:
            raise Atlas3Error(
                "CAUSAL_RELATIONSHIP_INVALID",
                f"causal graph allows only {ALLOWED_RELATIONSHIP}",
            )
        from_id = str(item.get("from_id") or item.get("from") or "").strip()
        to_id = str(item.get("to_id") or item.get("to") or "").strip()
        if not from_id or not to_id:
            raise Atlas3Error("CAUSAL_IDENTITY_INCOMPLETE", "edge requires from_id and to_id")
        explicit = item.get("project_id")
        if explicit is not None and str(explicit) != project_id:
            raise Atlas3Error(
                "CROSS_PROJECT",
                f"causal edge project_id {explicit!r} != requested {project_id!r}",
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
        rows.append(row)
    return rows


def compile_causal_graph(vault: Path | str, project_id: str) -> dict[str, Any]:
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
            "reason": "NO_DECLARED_CAUSAL_GRAPH",
            "graph_is_authority": False,
            "promoted_to_truth_core": 0,
            "truth_boundary": TRUTH_BOUNDARY,
            "honesty": honesty_block(),
        }
    declared = _load_declared(path)
    declared_project = str(declared.get("project_id") or "").strip()
    if declared_project and declared_project != pid:
        raise Atlas3Error(
            "CROSS_PROJECT",
            "declared causal graph project_id does not match request",
        )
    if declared.get("graph_is_authority") is True:
        raise Atlas3Error(
            "GRAPH_AUTHORITY_CLAIMED",
            "causal graph must not claim graph authority",
        )
    edges = _edges(declared.get("edges"), project_id=pid)
    return {
        "package": PACKAGE_ID,
        "project_id": pid,
        "relationship": ALLOWED_RELATIONSHIP,
        "edges": edges,
        "counts": {"edges": len(edges)},
        "status": "derived",
        "reason": "DECLARED_CAUSAL_GRAPH",
        "graph_is_authority": False,
        "promoted_to_truth_core": 0,
        "truth_boundary": TRUTH_BOUNDARY,
        "honesty": honesty_block(),
    }
