"""AS-2.0-TEMPORAL-001 / AS-2.2-KDIFF-001 — validity-catalog derivation.

Wires the already-shipped bitemporal validity-window writer
(:func:`project_atlas.bitemporal.write_validity_catalog`) to persisted Core
state so the shipped Knowledge Diff / Time Machine reader
(:mod:`project_atlas.knowledge_diff`) has a real catalog to consume.

This module derives **no new time representation**. It reuses:

* persisted Layer-B claims (``state/claims/<project>.json``) — real claim ids,
  subjects, fields, values;
* document-declared valid-time from Layer-A imported evidence
  (``sources/imported-documents/<source_id>.*``) via the existing AS-CORE-005
  temporal-evidence extractor
  (:func:`project_atlas.temporal_evidence.extract_source_temporal_facts`);
* the existing AS-2.0-TEMPORAL-001 catalog writer.

Valid-time model (document-declared succession): for each ``(subject, field)``
the claims whose source declares a ``timestamp:`` become validity windows
ordered by that declared valid-time. Each window's ``valid_to`` is the next
later declared valid-time for the same ``(subject, field)`` (open-ended for the
most recent). Claims whose source declares no valid-time contribute no window —
the KDIFF reader then reports honest ``unknown`` (temporal-data-missing) rather
than inventing a current.

Read-only toward canonical state; deterministic (declared valid-time only, no
wall-clock); writes only the derived ``generated/ops/bitemporal/`` catalog.
Truth boundary: VALIDITY CATALOG != AUTHORITY / != CLAIM IDENTITY MUTATION.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from project_atlas.bitemporal import (
    BitemporalError,
    ClaimValidityWindow,
    EvidenceKind,
    _parse_instant,
    write_validity_catalog,
)
from project_atlas.temporal_evidence import extract_source_temporal_facts

PACKAGE_ID = "AS-2.0-TEMPORAL-001"
_EVIDENCE_KIND: Final[EvidenceKind] = "document-declared"
_DEFAULT_COMPILATION_ID = "unbound-compilation"


class BitemporalCatalogError(ValueError):
    """Fail-closed validity-catalog derivation error."""


def load_json_object(path: Path) -> dict[str, Any]:
    """Load a JSON object from ``path`` (fail-closed on non-object)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BitemporalCatalogError(f"bitemporal-catalog-json-not-object:{path.name}")
    return data


@dataclass(frozen=True, slots=True)
class _ClaimTemporalRow:
    claim_id: str
    subject: str
    field: str
    valid_from: str


def _project_ids(root: Path) -> list[str]:
    claims_dir = root / "state" / "claims"
    if not claims_dir.is_dir():
        return []
    return sorted(p.stem for p in claims_dir.glob("*.json") if p.is_file())


def _compilation_id(root: Path, project_id: str) -> str:
    """Bind windows to the real knowledge-time compilation when persisted."""
    path = root / "state" / "current-state" / f"{project_id}.json"
    if not path.is_file():
        return _DEFAULT_COMPILATION_ID
    try:
        payload = load_json_object(path)
    except (OSError, ValueError):
        return _DEFAULT_COMPILATION_ID
    cid = str(payload.get("compilation_id") or "").strip()
    return cid or _DEFAULT_COMPILATION_ID


def _resolve_imported_source(root: Path, resource: str) -> Path | None:
    """Resolve a vault-relative provenance resource, fail-closed on traversal."""
    token = (resource or "").strip().replace("\\", "/")
    if not token or token.startswith("/") or ".." in token.split("/"):
        return None
    candidate = (root / token).resolve()
    root_resolved = root.resolve()
    if not candidate.is_relative_to(root_resolved):
        return None
    if not candidate.is_file():
        return None
    return candidate


def _declared_valid_from(root: Path, claim: dict[str, Any]) -> str | None:
    """Document-declared valid-time for a claim's primary imported source."""
    provenance = claim.get("provenance")
    if not isinstance(provenance, list) or not provenance:
        return None
    ref = provenance[0]
    if not isinstance(ref, dict):
        return None
    resource = str(ref.get("resource") or "")
    source_id = str(ref.get("source_id") or "")
    source_path = _resolve_imported_source(root, resource)
    if source_path is None:
        return None
    try:
        text = source_path.read_text(encoding="utf-8")
    except OSError:
        return None
    facts = extract_source_temporal_facts(
        source_id=source_id, path=resource, text=text
    )
    if facts.document_timestamp is None:
        return None
    # Deterministic ISO-8601 serialization consumed by AS-2.0-TEMPORAL-001.
    return facts.document_timestamp.isoformat()


def _declared_instant(raw: str) -> datetime:
    """Parse a document-declared instant for succession, never lexicographic order.

    Mixed offset syntax (``+00:00`` vs ``-05:00``) must compare as UTC instants.
    Lexicographic ``>`` on the raw ISO strings inverts later windows (AS-TEMPORAL-F1).
    """
    try:
        return _parse_instant(raw, field="valid-from")
    except BitemporalError as exc:
        raise BitemporalCatalogError(
            f"bitemporal-catalog-valid-from-invalid:{raw}"
        ) from exc


def build_project_validity_windows(
    vault: Path, project_id: str
) -> list[ClaimValidityWindow]:
    """Derive AS-2.0-TEMPORAL-001 windows for one project from persisted state."""
    root = vault.expanduser().resolve()
    compilation_id = _compilation_id(root, project_id)
    claims_path = root / "state" / "claims" / f"{project_id}.json"
    if not claims_path.is_file():
        raise BitemporalCatalogError(
            f"bitemporal-catalog-claims-missing:{project_id}"
        )
    payload = load_json_object(claims_path)
    entries = payload.get("claims")
    if not isinstance(entries, list):
        raise BitemporalCatalogError(
            f"bitemporal-catalog-claims-malformed:{project_id}"
        )

    rows_by_key: dict[tuple[str, str], list[_ClaimTemporalRow]] = {}
    for claim in entries:
        if not isinstance(claim, dict):
            continue
        claim_id = str(claim.get("claim_id") or "").strip()
        subject = str(claim.get("subject") or "").strip()
        field_name = str(claim.get("field") or "").strip()
        if not claim_id or not subject or not field_name:
            continue
        valid_from = _declared_valid_from(root, claim)
        if valid_from is None:
            continue  # No document-declared valid-time -> no window (honest unknown).
        rows_by_key.setdefault((subject, field_name), []).append(
            _ClaimTemporalRow(
                claim_id=claim_id,
                subject=subject,
                field=field_name,
                valid_from=valid_from,
            )
        )

    windows: list[ClaimValidityWindow] = []
    for key in sorted(rows_by_key):
        rows = sorted(
            rows_by_key[key],
            key=lambda r: (_declared_instant(r.valid_from), r.claim_id),
        )
        for index, row in enumerate(rows):
            # Document-declared succession: valid_to is the next later declared
            # valid-time for this subject+field (open-ended for the most recent).
            # Compare parsed UTC instants — never raw ISO string order.
            successor_from: str | None = None
            row_instant = _declared_instant(row.valid_from)
            for later in rows[index + 1 :]:
                if _declared_instant(later.valid_from) > row_instant:
                    successor_from = later.valid_from
                    break
            windows.append(
                ClaimValidityWindow(
                    claim_id=row.claim_id,
                    valid_from=row.valid_from,
                    knowledge_compilation_id=compilation_id,
                    valid_to=successor_from,
                    evidence_kind=_EVIDENCE_KIND,
                    rationale="document-declared-valid-time-succession",
                )
            )
    windows.sort(key=lambda w: (w.claim_id, w.valid_from))
    return windows


def write_project_validity_catalog(
    vault: Path, project_id: str
) -> dict[str, Any] | None:
    """Build and write one project's validity catalog; None when empty."""
    windows = build_project_validity_windows(vault, project_id)
    if not windows:
        return None
    try:
        return write_validity_catalog(
            vault.expanduser().resolve(), windows, catalog_id=project_id
        )
    except BitemporalError as exc:
        raise BitemporalCatalogError(
            f"bitemporal-catalog-write-failed:{project_id}:{exc}"
        ) from exc


def build_bitemporal_catalogs(vault: Path) -> dict[str, Any]:
    """Derive validity catalogs for every project with document-declared time."""
    root = vault.expanduser().resolve()
    written: list[str] = []
    total_windows = 0
    for project_id in _project_ids(root):
        result = write_project_validity_catalog(root, project_id)
        if result is not None:
            written.append(project_id)
            total_windows += int(result.get("window_count") or 0)
    return {
        "ok": True,
        "package_id": PACKAGE_ID,
        "projects_with_catalog": sorted(written),
        "catalog_count": len(written),
        "window_count": total_windows,
    }
