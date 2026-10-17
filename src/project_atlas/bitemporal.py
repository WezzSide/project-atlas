"""AS-2.0-TEMPORAL-001 — bitemporal claim validity windows.

Deepens AS-CORE-005 with explicit valid-time windows. Knowledge-time remains
compilation-bound. Never invents wall-clock "now", never mutates Claim Identity
v2, and never rewrites the CORE-005 temporal evaluator. Bound to the Atlas 1.0
compatibility anchor.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from project_atlas.compat_anchor import (
    SNAPSHOT_ID,
    CompatibilityAnchor,
    require_compatibility_anchor,
)
from project_atlas.schema import SchemaValidationError, validate_record
from project_atlas.secrets import scan_text

PACKAGE_ID = "AS-2.0-TEMPORAL-001"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_COMPILATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

EvidenceKind = Literal[
    "semantic-event",
    "document-declared",
    "source-version",
    "unknown",
]

AsOfStatus = Literal[
    "selected",
    "not_found",
    "unresolved_overlap",
    "unresolved_incomplete",
    "rejected_malformed",
]


class BitemporalError(ValueError):
    """Fail-closed bitemporal / validity-window error."""


@dataclass(frozen=True, slots=True)
class ClaimValidityWindow:
    """Valid-time window for one immutable claim (knowledge time is separate)."""

    claim_id: str
    valid_from: str
    knowledge_compilation_id: str
    valid_to: str | None = None
    evidence_kind: EvidenceKind = "unknown"
    core005_temporal_status: str | None = None
    rationale: str = "explicit-validity-window"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp.replace(path)


def _as_utc(instant: datetime) -> datetime:
    """Canonical comparison form: UTC-aware. Naive instants are UTC, not local.

    D-178 / KDIFF_TZ_AWARE_CRASH: mixed aware/naive comparison is forbidden.
    Date-only and offset-naive ISO strings keep legacy chronology as midnight
    UTC / the written clock interpreted as UTC. Offsets are converted, never
    stripped, so ``T00:00:00+01:00`` is not the same instant as ``T00:00:00Z``.
    """
    if instant.tzinfo is None:
        return instant.replace(tzinfo=UTC)
    return instant.astimezone(UTC)


def _parse_instant(raw: str, *, field: str) -> datetime:
    text = raw.strip()
    if not text:
        raise BitemporalError(f"bitemporal-{field}-empty")
    # Reject wall-clock sentinel language — callers must supply evidenced instants.
    if text.lower() in {"now", "today", "utcnow", "utc-now"}:
        raise BitemporalError(f"bitemporal-{field}-wall-clock-forbidden")
    try:
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            parsed = datetime.combine(date.fromisoformat(text), datetime.min.time())
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BitemporalError(f"bitemporal-{field}-invalid:{text}") from exc
    return _as_utc(parsed)


def normalize_validity_window(window: ClaimValidityWindow) -> dict[str, Any]:
    """Validate and serialize one claim validity window (fail-closed)."""
    claim_id = window.claim_id.strip()
    if not _ID_RE.fullmatch(claim_id):
        raise BitemporalError("bitemporal-claim-id-invalid")
    # AS-SEC-SCAN-BITEMPORAL-CLAIMID-JSON-ESC-001: json.loads of persisted
    # claims can decode ``\u`` claim ids that scan_text misses on raw bytes.
    # Do not persist those values into generated/ops/bitemporal/.
    if scan_text(claim_id):
        raise BitemporalError("bitemporal-claim-id-secret")
    compilation = window.knowledge_compilation_id.strip()
    if not _COMPILATION_RE.fullmatch(compilation):
        raise BitemporalError("bitemporal-compilation-id-invalid")
    if scan_text(compilation):
        raise BitemporalError("bitemporal-compilation-id-secret")

    start = _parse_instant(window.valid_from, field="valid-from")
    end: datetime | None = None
    if window.valid_to is not None:
        end = _parse_instant(window.valid_to, field="valid-to")
        if end < start:
            raise BitemporalError("bitemporal-window-inverted")

    if window.evidence_kind == "unknown" and not window.rationale.strip():
        raise BitemporalError("bitemporal-unknown-evidence-needs-rationale")

    payload: dict[str, Any] = {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "compat_snapshot_id": SNAPSHOT_ID,
        "claim_id": claim_id,
        "valid_from": window.valid_from.strip(),
        "knowledge_compilation_id": compilation,
        "evidence_kind": window.evidence_kind,
        "rationale": window.rationale.strip(),
        "authority": {
            "level": "derived",
            "note": "Validity windows deepen AS-CORE-005; they do not rewrite Claim Identity",
        },
        "truth_boundary": "VALIDITY WINDOW ≠ AUTHORITY / ≠ CLAIM IDENTITY MUTATION",
        "generated": {"by": "project-atlas"},
    }
    if window.valid_to is not None:
        payload["valid_to"] = window.valid_to.strip()
    if window.core005_temporal_status is not None:
        status = window.core005_temporal_status.strip()
        if status not in {
            "current",
            "historical",
            "unresolved",
            "authority-pending",
        }:
            raise BitemporalError("bitemporal-core005-status-invalid")
        payload["core005_temporal_status"] = status

    try:
        validate_record(payload, "claim-validity-window")
    except SchemaValidationError as exc:
        raise BitemporalError(f"bitemporal-window-schema:{exc}") from exc
    return payload


def _covers(window: dict[str, Any], as_of: datetime) -> bool:
    instant = _as_utc(as_of)
    start = _parse_instant(str(window["valid_from"]), field="valid-from")
    if instant < start:
        return False
    if "valid_to" not in window or window["valid_to"] is None:
        return True
    end = _parse_instant(str(window["valid_to"]), field="valid-to")
    return instant <= end


def evaluate_as_of(
    windows: list[ClaimValidityWindow],
    *,
    as_of_valid_time: str,
    subject: str,
    field: str,
    anchor: CompatibilityAnchor | None = None,
) -> dict[str, Any]:
    """Select the claim covering as-of valid time; fail closed on overlap/malformed."""
    _ = anchor or require_compatibility_anchor()
    subject_token = subject.strip()
    field_token = field.strip()
    if not subject_token or not field_token:
        raise BitemporalError("bitemporal-subject-field-required")

    as_of = _parse_instant(as_of_valid_time, field="as-of")
    normalized: list[dict[str, Any]] = []
    try:
        for item in windows:
            normalized.append(normalize_validity_window(item))
    except BitemporalError as exc:
        result: dict[str, Any] = {
            "schema_version": 1,
            "package_id": PACKAGE_ID,
            "compat_snapshot_id": SNAPSHOT_ID,
            "subject": subject_token,
            "field": field_token,
            "as_of_valid_time": as_of_valid_time.strip(),
            "status": "rejected_malformed",
            "selected_claim_id": None,
            "candidate_claim_ids": [],
            "rationale": str(exc),
            "authority": {
                "level": "derived",
                "note": "Fail-closed: malformed validity windows never select current",
            },
            "truth_boundary": "AS-OF RESULT ≠ AUTHORITY / ≠ WALL-CLOCK NOW",
            "generated": {"by": "project-atlas"},
        }
        validate_record(result, "bitemporal-as-of-result")
        return result

    try:
        covering = [w for w in normalized if _covers(w, as_of)]
    except TypeError as exc:
        # Fail closed if a window still cannot be compared after UTC
        # normalization. This is not the primary fix; mixed naive/aware
        # comparison is prevented by `_as_utc`.
        result = {
            "schema_version": 1,
            "package_id": PACKAGE_ID,
            "compat_snapshot_id": SNAPSHOT_ID,
            "subject": subject_token,
            "field": field_token,
            "as_of_valid_time": as_of_valid_time.strip(),
            "status": "rejected_malformed",
            "selected_claim_id": None,
            "candidate_claim_ids": [],
            "rationale": f"invalid temporal comparison: {exc}",
            "authority": {
                "level": "derived",
                "note": "Fail-closed: incomparable validity instants never select current",
            },
            "truth_boundary": "AS-OF RESULT ≠ AUTHORITY / ≠ WALL-CLOCK NOW",
            "generated": {"by": "project-atlas"},
        }
        validate_record(result, "bitemporal-as-of-result")
        return result
    covering.sort(key=lambda w: (w["claim_id"], w["valid_from"]))

    if not covering:
        status: AsOfStatus = "not_found"
        selected = None
        rationale = "no-validity-window-covers-as-of"
    elif len(covering) == 1:
        only = covering[0]
        if only["evidence_kind"] == "unknown":
            status = "unresolved_incomplete"
            selected = None
            rationale = "unknown-evidence-kind-fail-closed"
        else:
            status = "selected"
            selected = only["claim_id"]
            rationale = "single-evidenced-window-covers-as-of"
    else:
        status = "unresolved_overlap"
        selected = None
        rationale = "multiple-windows-cover-as-of-without-core005-supersession"

    result = {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "compat_snapshot_id": SNAPSHOT_ID,
        "subject": subject_token,
        "field": field_token,
        "as_of_valid_time": as_of_valid_time.strip(),
        "status": status,
        "selected_claim_id": selected,
        "candidate_claim_ids": [w["claim_id"] for w in covering],
        "rationale": rationale,
        "authority": {
            "level": "derived",
            "note": "As-of selection consumes validity windows; does not recompute CORE-005",
        },
        "truth_boundary": "AS-OF RESULT ≠ AUTHORITY / ≠ WALL-CLOCK NOW",
        "generated": {"by": "project-atlas"},
    }
    try:
        validate_record(result, "bitemporal-as-of-result")
    except SchemaValidationError as exc:
        raise BitemporalError(f"bitemporal-as-of-schema:{exc}") from exc
    return result


def write_validity_catalog(
    vault: Path,
    windows: list[ClaimValidityWindow],
    *,
    catalog_id: str = "default",
    anchor: CompatibilityAnchor | None = None,
) -> dict[str, Any]:
    """Write a deterministic validity-window catalog under generated/ops/."""
    _ = anchor or require_compatibility_anchor()
    cid = catalog_id.strip()
    if not _COMPILATION_RE.fullmatch(cid):
        raise BitemporalError("bitemporal-catalog-id-invalid")
    if scan_text(cid):
        raise BitemporalError("bitemporal-catalog-id-secret")

    items = [normalize_validity_window(w) for w in windows]
    items.sort(key=lambda w: (w["claim_id"], w["valid_from"]))
    payload: dict[str, Any] = {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "compat_snapshot_id": SNAPSHOT_ID,
        "catalog_id": cid,
        "windows": items,
        "window_count": len(items),
        "authority": {
            "level": "derived",
            "note": (
                "Catalog is derived evidence; CORE-005 evaluator remains sole "
                "disposition writer"
            ),
        },
        "truth_boundary": "VALIDITY CATALOG ≠ TEMPORAL EVALUATOR REWRITE",
        "generated": {"by": "project-atlas"},
    }
    try:
        validate_record(payload, "claim-validity-catalog")
    except SchemaValidationError as exc:
        raise BitemporalError(f"bitemporal-catalog-schema:{exc}") from exc

    out = (
        vault.resolve()
        / "generated"
        / "ops"
        / "bitemporal"
        / f"{cid}-validity-catalog.json"
    )
    _atomic_write_json(out, payload)
    return payload
