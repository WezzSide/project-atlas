"""AT3-040 — Deterministic knowledge extraction into landed ITEM_TYPES."""

from __future__ import annotations

import re
from typing import Any, Final

from project_atlas.atlas3.contracts import ITEM_TYPES, Atlas3Error
from project_atlas.atlas3.memory.privacy import scan_or_raise

PACKAGE_ID: Final[str] = "AT3-040"
EXTRACTOR_VERSION: Final[str] = "atlas3-extract-v1"

_QUESTION = re.compile(r"\?")
_DECISION = re.compile(r"\b(we decided|owner decided|decision is|decided to)\b", re.I)
_PLANNED = re.compile(r"\b(planned|later|after|migrate|migration|intent)\b", re.I)
_CLAIM = re.compile(r"\b(uses|use|production|database|postgres|postgresql|endpoint)\b", re.I)
_FAIL = re.compile(r"\b(failed|does not work|rollback)\b", re.I)
_NEXT = re.compile(r"\b(next|todo|should|look at)\b", re.I)


def _classify(text: str, *, role: str, owner_origin: dict[str, Any] | None) -> str:
    if owner_origin:
        return "confirmed_owner_decision"
    if role == "assistant" and _DECISION.search(text):
        return "proposed_decision"
    if _QUESTION.search(text):
        return "open_question"
    if _FAIL.search(text):
        return "failed_approach"
    if _PLANNED.search(text) and _CLAIM.search(text):
        return "claim_candidate"
    if _CLAIM.search(text):
        return "claim_candidate"
    if _NEXT.search(text):
        return "next_step"
    if role == "owner":
        return "proposed_decision"
    return "observation"


def extract_capability() -> dict[str, Any]:
    return {
        "package": PACKAGE_ID,
        "extractor_version": EXTRACTOR_VERSION,
        "extraction_class": "deterministic_heuristic",
        "llm_assisted": False,
        "authority": "NON_CANONICAL",
        "auto_promote_to_truth_core": False,
        "item_types": sorted(ITEM_TYPES),
    }


def extract_items(
    envelopes: list[dict[str, Any]],
    *,
    owner_origin: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Heuristic extraction. LLM-assisted extraction is not invoked here."""
    if not isinstance(envelopes, list):
        raise Atlas3Error("EXTRACT_INVALID", "envelopes must be a list")
    scoped: set[str] = set()
    items: list[dict[str, Any]] = []
    for envelope in envelopes:
        if not isinstance(envelope, dict):
            raise Atlas3Error("EXTRACT_INVALID", "envelope is not an object")
        explicit = envelope.get("project_id")
        if explicit is not None and str(explicit).strip():
            scoped.add(str(explicit))
        if len(scoped) > 1:
            raise Atlas3Error(
                "PROJECT_MISMATCH",
                f"mixed-project extract batch: {sorted(scoped)}",
            )
        text = str(envelope.get("content_reference") or "").strip()
        if not text:
            continue
        scan_or_raise(text)
        role = str(envelope.get("role") or "assistant")
        bound_origin = owner_origin if role == "owner" else None
        item_type = _classify(text, role=role, owner_origin=bound_origin)
        if item_type not in ITEM_TYPES:
            raise Atlas3Error("UNSUPPORTED_ITEM_TYPE", item_type)
        if item_type == "confirmed_owner_decision":
            if not isinstance(bound_origin, dict):
                raise Atlas3Error(
                    "FALSE_OWNER_DECISION",
                    "confirmed_owner_decision requires explicit owner_origin",
                )
            if (
                bound_origin.get("evidence_kind") != "explicit_owner_statement"
                or str(bound_origin.get("origin") or "").lower() != "owner"
                or not str(bound_origin.get("statement") or "").strip()
            ):
                raise Atlas3Error(
                    "FALSE_OWNER_DECISION",
                    "owner_origin contract is not satisfied",
                )
        item: dict[str, Any] = {
            "item_type": item_type,
            "text": text,
            "provider": envelope.get("provider"),
            "conversation_id": envelope.get("conversation_id"),
            "message_id": envelope.get("message_id"),
            "source_content_hash": envelope.get("content_hash"),
            "project_id": envelope.get("project_id"),
            "extractor": PACKAGE_ID,
            "extractor_version": EXTRACTOR_VERSION,
            "extraction_class": "deterministic_heuristic",
            "authority": "NON_CANONICAL",
        }
        if item_type == "confirmed_owner_decision" and bound_origin is not None:
            item["owner_origin"] = {
                "evidence_kind": "explicit_owner_statement",
                "origin": "owner",
                "statement": str(bound_origin["statement"]).strip(),
            }
        items.append(item)
    return items


def reject_forged_owner_decision(text: str) -> dict[str, Any]:
    """Model paraphrase of an owner decision stays proposed_decision."""
    return {
        "item_type": "proposed_decision",
        "text": text,
        "authority": "NON_CANONICAL",
        "forged_owner_blocked": True,
    }
