"""AS-CODER-ALPHA-HUMAN-LOOP-001 — human review decisions into Truth Core.

``atlas review decide`` records durable dispositions under
``state/human-decisions/`` and updates review queue status. Knowledge compile
honors dispositions so reconnect does not resurrect decided items.
Fail-closed; no OPT/pilot; disposition != invented authority winner.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from atlas_contracts.identity import safe_relative_component

PACKAGE_ID = "AS-CODER-ALPHA-HUMAN-LOOP-001"
GENERATOR_ID = "atlas-coder-alpha-human-loop-001"
DECISIONS_DIR = Path("state") / "human-decisions"
RECEIPT_DIR = Path("generated") / "ops" / "human-decisions"
ALLOWED_DECISIONS = frozenset({"accept", "reject"})


class HumanLoopError(ValueError):
    """Fail-closed human-loop error."""


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _safe_project_id(project_id: str) -> str:
    try:
        return safe_relative_component(project_id, label="project id")
    except ValueError as exc:
        raise HumanLoopError(str(exc)) from exc


def _safe_token(value: str, *, label: str) -> str:
    try:
        return safe_relative_component(value, label=label)
    except ValueError as exc:
        raise HumanLoopError(str(exc)) from exc


def decisions_path(vault: Path, project_id: str) -> Path:
    return vault / DECISIONS_DIR / f"{_safe_project_id(project_id)}.json"


def load_human_decisions(vault: Path, project_id: str) -> dict[str, Any]:
    """Load durable human decision registry for a project (empty if absent)."""
    path = decisions_path(vault, project_id)
    if not path.is_file():
        return {
            "schema_version": 1,
            "schema": "atlas.coder-alpha.human-decisions.v1",
            "package": PACKAGE_ID,
            "project_id": project_id,
            "decisions": [],
            "generated": {"by": GENERATOR_ID},
        }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HumanLoopError(f"unreadable human decisions: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("decisions"), list):
        raise HumanLoopError("invalid human decisions registry")
    return raw


def _pending_path(vault: Path, project_id: str) -> Path:
    return vault / "review" / "pending" / f"{project_id}.json"


def _conflict_claim_ids(vault: Path, project_id: str, conflict_id: str) -> set[str]:
    """Bind a conflict winner to claim ids on the pending conflict record."""
    path = vault / "review" / "conflicts" / f"{project_id}.json"
    if not path.is_file():
        return set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    entries = raw.get("entries") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        return set()
    for item in entries:
        if not isinstance(item, dict) or item.get("conflict_id") != conflict_id:
            continue
        ids = item.get("claim_ids") or []
        return {str(cid) for cid in ids if isinstance(cid, str) and cid.strip()}
    return set()


def _find_pending_entry(
    vault: Path, project_id: str, review_id: str
) -> dict[str, Any]:
    path = _pending_path(vault, project_id)
    if not path.is_file():
        raise HumanLoopError(f"pending review file missing for project {project_id}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HumanLoopError(f"unreadable pending reviews: {exc}") from exc
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise HumanLoopError("pending reviews missing entries")
    for entry in entries:
        if isinstance(entry, dict) and entry.get("review_id") == review_id:
            return entry
    raise HumanLoopError(f"unknown pending review_id: {review_id}")


def apply_review_decision(
    vault: Path,
    *,
    project_id: str,
    review_id: str,
    decision: str,
    reason: str,
    winner_claim_id: str | None = None,
) -> dict[str, Any]:
    """Record a human accept/reject disposition for one pending review entry."""
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise HumanLoopError(f"vault is not a directory: {vault}")
    project_id = _safe_project_id(project_id)
    review_id = _safe_token(review_id, label="review id")
    decision_norm = (decision or "").strip().lower()
    if decision_norm not in ALLOWED_DECISIONS:
        raise HumanLoopError("decision must be accept or reject")
    reason_text = (reason or "").strip()
    if not reason_text:
        raise HumanLoopError("reason is required")
    entry = _find_pending_entry(vault, project_id, review_id)
    status = str(entry.get("status") or "pending")
    if status != "pending":
        raise HumanLoopError(f"review already decided: {review_id} status={status}")
    category = str(entry.get("category") or "")
    subject_id = str(entry.get("subject_id") or "")
    if not subject_id:
        raise HumanLoopError("pending review missing subject_id")
    winner: str | None = None
    if decision_norm == "accept" and category == "conflict":
        if not winner_claim_id:
            raise HumanLoopError(
                "conflict accept requires --winner-claim-id (no silent winner)"
            )
        winner = _safe_token(winner_claim_id, label="winner claim id")
        allowed = _conflict_claim_ids(vault, project_id, subject_id)
        if winner not in allowed:
            raise HumanLoopError(
                "winner_claim_id is not a claim on the pending conflict"
            )

    registry = load_human_decisions(vault, project_id)
    existing_ids = {
        item.get("review_id")
        for item in registry.get("decisions") or []
        if isinstance(item, dict)
    }
    if review_id in existing_ids:
        raise HumanLoopError(f"duplicate human decision for review_id: {review_id}")

    record = {
        "schema_version": 1,
        "package": PACKAGE_ID,
        "review_id": review_id,
        "project_id": project_id,
        "decision": decision_norm,
        "reason": reason_text,
        "category": category,
        "subject_id": subject_id,
        "winner_claim_id": winner,
        "status": "resolved" if decision_norm == "accept" else "rejected",
        "generated": {"by": GENERATOR_ID},
        "honesty": {
            "authentic_pilot": False,
            "atlas_opt_wake_gate": "CLOSED",
            "lens_is_authority": False,
            "invented_winner": False,
        },
    }
    decisions = list(registry.get("decisions") or [])
    decisions.append(record)
    registry["decisions"] = sorted(
        decisions,
        key=lambda item: str(item.get("review_id") or ""),
    )
    registry["generated"] = {"by": GENERATOR_ID}
    registry["package"] = PACKAGE_ID
    registry["project_id"] = project_id

    # Update pending queue entry status in place (compile will also honor registry).
    pending_path = _pending_path(vault, project_id)
    pending_payload = json.loads(pending_path.read_text(encoding="utf-8"))
    updated_entries: list[dict[str, Any]] = []
    for item in pending_payload.get("entries") or []:
        if isinstance(item, dict) and item.get("review_id") == review_id:
            item = dict(item)
            item["status"] = record["status"]
        if isinstance(item, dict):
            updated_entries.append(item)
    pending_payload["entries"] = updated_entries

    disposition_path = decisions_path(vault, project_id)
    receipt_path = vault / RECEIPT_DIR / f"{project_id}-{review_id}.json"
    _write_atomic(
        disposition_path,
        (json.dumps(registry, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    _write_atomic(
        pending_path,
        (json.dumps(pending_payload, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    _write_atomic(
        receipt_path,
        (json.dumps(record, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )

    # Refresh honesty + state + brief so pending counts agree everywhere (D-044 B3).
    from project_atlas.project_brief import materialize_project_briefs
    from project_atlas.project_state import materialize_state_lenses
    from project_atlas.project_unknown import materialize_unknown_lenses

    materialize_unknown_lenses(vault, project_ids=[project_id])
    materialize_state_lenses(vault, project_ids=[project_id])
    materialize_project_briefs(vault, project_ids=[project_id], refresh=False)

    return {
        "schema_version": 1,
        "package": PACKAGE_ID,
        "status": "ok",
        "decision": record,
        "disposition_path": disposition_path.relative_to(vault).as_posix(),
        "receipt_path": receipt_path.relative_to(vault).as_posix(),
        "pending_path": pending_path.relative_to(vault).as_posix(),
        "generated": {"by": GENERATOR_ID},
    }


def disposition_index(vault: Path, project_id: str) -> dict[str, dict[str, Any]]:
    """Map review_id → decision record for compile-time honor."""
    registry = load_human_decisions(vault, project_id)
    out: dict[str, dict[str, Any]] = {}
    for item in registry.get("decisions") or []:
        if isinstance(item, dict) and isinstance(item.get("review_id"), str):
            out[str(item["review_id"])] = item
    return out


def rejected_claim_ids(vault: Path, project_id: str) -> dict[str, str]:
    """Claim ids rejected by human disposition → reason."""
    out: dict[str, str] = {}
    for item in disposition_index(vault, project_id).values():
        if item.get("decision") != "reject":
            continue
        subject = item.get("subject_id")
        category = item.get("category")
        if isinstance(subject, str) and category in {
            "pending-claim",
            "low-confidence",
            "stale-or-superseded",
        }:
            out[subject] = str(item.get("reason") or "human rejected")
    return out


def accepted_claim_ids(vault: Path, project_id: str) -> set[str]:
    """Claim ids accepted/verified by human disposition."""
    out: set[str] = set()
    for item in disposition_index(vault, project_id).values():
        if item.get("decision") != "accept":
            continue
        subject = item.get("subject_id")
        category = item.get("category")
        if isinstance(subject, str) and category in {
            "pending-claim",
            "low-confidence",
        }:
            out.add(subject)
        winner = item.get("winner_claim_id")
        if isinstance(winner, str) and category == "conflict":
            out.add(winner)
    return out
