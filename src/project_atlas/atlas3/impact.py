"""AT3-080 — Isolated impact explorer data.

Declared impact rows only. Graph != authority. No trust scores.
Missing declarations stay UNKNOWN. Does not write AS-GRAPH-003.
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

PACKAGE_ID: Final[str] = "AT3-080"
GENERATOR_ID: Final[str] = "atlas3-impact-explorer-080"
DECLARED_NAME: Final[str] = "declared.json"
ALLOWED_IMPACT_KINDS: Final[frozenset[str]] = frozenset(
    {"depends_on", "blocks", "caused_by", "invalidates"}
)


def _declared_path(vault: Path, project_id: str) -> Path:
    return vault / OPS_RELATIVE / "impact" / project_id / DECLARED_NAME


def _load_declared(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Atlas3Error(
            "IMPACT_CORRUPT",
            "declared impact explorer data is not readable JSON",
        ) from exc
    if not isinstance(raw, dict):
        raise Atlas3Error("IMPACT_CORRUPT", "declared impact explorer data must be an object")
    return raw


def _rows(raw: object, *, project_id: str) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise Atlas3Error("IMPACT_CORRUPT", "impacts must be a list")
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise Atlas3Error("IMPACT_CORRUPT", "impacts row is not an object")
        if item.get("trust_score") is not None or item.get("authority_winner") is True:
            raise Atlas3Error(
                "TRUST_SCORE_FORBIDDEN",
                "impact explorer must not store trust scores or authority winners",
            )
        kind = str(item.get("impact_kind") or item.get("kind") or "").strip().lower()
        if kind not in ALLOWED_IMPACT_KINDS:
            raise Atlas3Error("IMPACT_KIND_UNKNOWN", f"unsupported impact_kind {kind!r}")
        from_id = str(item.get("from_id") or item.get("from") or "").strip()
        to_id = str(item.get("to_id") or item.get("to") or "").strip()
        if not from_id or not to_id:
            raise Atlas3Error("IMPACT_IDENTITY_INCOMPLETE", "impact requires from_id and to_id")
        explicit = item.get("project_id")
        if explicit is not None and str(explicit) != project_id:
            raise Atlas3Error(
                "CROSS_PROJECT",
                f"impact row project_id {explicit!r} != requested {project_id!r}",
            )
        evidence = item.get("evidence_refs") or item.get("evidence")
        if not isinstance(evidence, list):
            raise Atlas3Error("PROVENANCE_REQUIRED", f"{from_id}->{to_id} requires evidence_refs")
        refs = [str(ref).strip() for ref in evidence if str(ref).strip()]
        if not refs:
            raise Atlas3Error("PROVENANCE_REQUIRED", f"{from_id}->{to_id} requires evidence_refs")
        rows.append(
            {
                "impact_kind": kind,
                "from_id": from_id,
                "to_id": to_id,
                "project_id": project_id,
                "evidence_refs": refs,
                "authority": "derived",
                "graph_is_authority": False,
                "trust_score": None,
            }
        )
    return rows


def compile_impact_explorer(vault: Path | str, project_id: str) -> dict[str, Any]:
    """Compile declared impact-explorer data. Missing stays UNKNOWN."""
    root = require_vault(vault)
    pid = require_project(root, project_id)
    path = _declared_path(root, pid)
    if not path.is_file():
        return {
            "schema_version": 1,
            "package_id": PACKAGE_ID,
            "generated": {"by": GENERATOR_ID},
            "project_id": pid,
            "impacts": [],
            "counts": {"impacts": 0},
            "status": "UNKNOWN",
            "reason": "NO_DECLARED_IMPACT",
            "graph_is_authority": False,
            "trust_score_used": False,
            "certified_for_merge": False,
            "merge_authorization": "NOT_GRANTED",
            "promoted_to_truth_core": 0,
            "write_applied": False,
            "truth_boundary": TRUTH_BOUNDARY,
            "honesty": honesty_block(),
        }
    declared = _load_declared(path)
    declared_project = str(declared.get("project_id") or "").strip()
    if declared_project and declared_project != pid:
        raise Atlas3Error(
            "CROSS_PROJECT",
            "declared impact explorer project_id does not match request",
        )
    if declared.get("graph_is_authority") is True:
        raise Atlas3Error(
            "GRAPH_AUTHORITY_CLAIMED",
            "impact explorer must not claim graph authority",
        )
    if declared.get("trust_score") is not None:
        raise Atlas3Error("TRUST_SCORE_FORBIDDEN", "impact explorer must not use trust scores")
    impacts = _rows(declared.get("impacts"), project_id=pid)
    return {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "generated": {"by": GENERATOR_ID},
        "project_id": pid,
        "impacts": impacts,
        "counts": {"impacts": len(impacts)},
        "status": "derived",
        "reason": "DECLARED_IMPACT",
        "graph_is_authority": False,
        "trust_score_used": False,
        "certified_for_merge": False,
        "merge_authorization": "NOT_GRANTED",
        "promoted_to_truth_core": 0,
        "write_applied": False,
        "truth_boundary": TRUTH_BOUNDARY,
        "honesty": honesty_block(),
    }
