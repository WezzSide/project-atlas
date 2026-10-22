"""AT3-094 — Isolated Decision Explorer.

Declared owner decisions only. Model paraphrase is not an owner decision.
Confirmed decisions require owner_origin. Missing stays UNKNOWN.
Decision Explorer is not Truth Core. MERGE_AUTHORIZATION = NOT_GRANTED.
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

PACKAGE_ID: Final[str] = "AT3-094"
GENERATOR_ID: Final[str] = "atlas3-decision-explorer-094"
DECLARED_NAME: Final[str] = "declared.json"
ALLOWED_STATUSES: Final[frozenset[str]] = frozenset(
    {"confirmed_owner", "proposed", "superseded", "unknown"}
)
_FORBIDDEN_ORIGINS: Final[frozenset[str]] = frozenset(
    {"model", "assistant", "llm", "agent", "paraphrase"}
)


def _declared_path(vault: Path, project_id: str) -> Path:
    return vault / OPS_RELATIVE / "decision-explorer" / project_id / DECLARED_NAME


def _valid_owner_origin(origin: dict[str, Any] | None) -> bool:
    if not isinstance(origin, dict):
        return False
    origin_name = str(origin.get("origin") or "").strip().lower()
    if origin_name in _FORBIDDEN_ORIGINS:
        return False
    return (
        origin.get("evidence_kind") == "explicit_owner_statement"
        and origin_name == "owner"
        and bool(str(origin.get("statement") or "").strip())
    )


def _load_declared(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Atlas3Error(
            "DECISION_EXPLORER_CORRUPT",
            "declared Decision Explorer data is not readable JSON",
        ) from exc
    if not isinstance(raw, dict):
        raise Atlas3Error(
            "DECISION_EXPLORER_CORRUPT",
            "declared Decision Explorer data must be an object",
        )
    return raw


def _rows(raw: object, *, project_id: str) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise Atlas3Error("DECISION_EXPLORER_CORRUPT", "decisions must be a list")
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise Atlas3Error("DECISION_EXPLORER_CORRUPT", "decisions row is not an object")
        if (
            item.get("model_paraphrase") is True
            or item.get("inferred_from_model") is True
            or item.get("assistant_claim") is True
        ):
            raise Atlas3Error(
                "FALSE_OWNER_DECISION",
                "Decision Explorer must not infer owner decisions from model paraphrase",
            )
        status = str(item.get("status") or "").strip().lower()
        if status not in ALLOWED_STATUSES:
            raise Atlas3Error("DECISION_STATUS_UNKNOWN", f"unsupported decision status {status!r}")
        decision_id = str(item.get("decision_id") or item.get("id") or "").strip()
        text = str(item.get("text") or item.get("statement") or "").strip()
        if not decision_id or not text:
            raise Atlas3Error(
                "DECISION_IDENTITY_INCOMPLETE",
                "decision requires decision_id and text",
            )
        explicit = item.get("project_id")
        if explicit is not None and str(explicit) != project_id:
            raise Atlas3Error(
                "CROSS_PROJECT",
                f"decision row project_id {explicit!r} != requested {project_id!r}",
            )
        origin = item.get("owner_origin")
        origin_dict = origin if isinstance(origin, dict) else None
        if status == "confirmed_owner" and not _valid_owner_origin(origin_dict):
            raise Atlas3Error(
                "FALSE_OWNER_DECISION",
                "confirmed_owner decisions require explicit owner_origin",
            )
        if origin_dict is not None and not _valid_owner_origin(origin_dict):
            raise Atlas3Error(
                "FALSE_OWNER_DECISION",
                "owner_origin must be an explicit owner statement",
            )
        evidence = item.get("evidence_refs") or item.get("evidence")
        if not isinstance(evidence, list):
            raise Atlas3Error("PROVENANCE_REQUIRED", f"{decision_id} requires evidence_refs")
        refs = [str(ref).strip() for ref in evidence if str(ref).strip()]
        if not refs:
            raise Atlas3Error("PROVENANCE_REQUIRED", f"{decision_id} requires evidence_refs")
        row: dict[str, Any] = {
            "decision_id": decision_id,
            "text": text,
            "status": status,
            "project_id": project_id,
            "evidence_refs": refs,
            "authority": "derived",
            "model_is_owner": False,
            "explorer_is_authority": False,
        }
        if status == "confirmed_owner":
            row["owner_origin"] = {
                "evidence_kind": "explicit_owner_statement",
                "origin": "owner",
            }
        rows.append(row)
    return rows


def compile_decision_explorer(vault: Path | str, project_id: str) -> dict[str, Any]:
    """Compile declared Decision Explorer data. Missing stays UNKNOWN."""
    root = require_vault(vault)
    pid = require_project(root, project_id)
    path = _declared_path(root, pid)
    if not path.is_file():
        return {
            "schema_version": 1,
            "package_id": PACKAGE_ID,
            "generated": {"by": GENERATOR_ID},
            "project_id": pid,
            "decisions": [],
            "counts": {"decisions": 0, "confirmed_owner": 0},
            "status": "UNKNOWN",
            "reason": "NO_DECLARED_DECISIONS",
            "model_is_owner": False,
            "explorer_is_authority": False,
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
            "declared Decision Explorer project_id does not match request",
        )
    if declared.get("explorer_is_authority") is True or declared.get("graph_is_authority") is True:
        raise Atlas3Error(
            "EXPLORER_AUTHORITY_CLAIMED",
            "Decision Explorer must not claim authority",
        )
    if declared.get("model_is_owner") is True:
        raise Atlas3Error("FALSE_OWNER_DECISION", "model cannot be treated as owner")
    decisions = _rows(declared.get("decisions"), project_id=pid)
    confirmed = sum(1 for item in decisions if item["status"] == "confirmed_owner")
    return {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "generated": {"by": GENERATOR_ID},
        "project_id": pid,
        "decisions": decisions,
        "counts": {"decisions": len(decisions), "confirmed_owner": confirmed},
        "status": "derived",
        "reason": "DECLARED_DECISIONS",
        "model_is_owner": False,
        "explorer_is_authority": False,
        "certified_for_merge": False,
        "merge_authorization": "NOT_GRANTED",
        "promoted_to_truth_core": 0,
        "write_applied": False,
        "truth_boundary": TRUTH_BOUNDARY,
        "honesty": honesty_block(),
    }
