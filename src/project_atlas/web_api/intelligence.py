"""AS-2.0-API-001 — read-only intelligence projections for LIVE_API.

Library results only. Never writes Layer B. Never replaces ``/v1/conflicts``.
``DERIVED_INTELLIGENCE_IS_AUTHORITY = NO``.
"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Any, assert_never

from atlas_contracts.identity import safe_relative_component
from project_atlas.domain import (
    AuthorityLevel,
    ClaimLifecycle,
    ConfidenceState,
    ProvenanceReference,
)
from project_atlas.intelligence import (
    IntelligenceAnswer,
    IntelligenceQuery,
    IntelligenceQueryKind,
    aggregate_portfolio_state,
    detect_portfolio_dependencies,
    query_intelligence,
    rank_portfolio_attention,
    synthesize_project_state,
)
from project_atlas.intelligence.boundary import (
    CANONICAL_WRITE,
    DERIVED_INTELLIGENCE_IS_AUTHORITY,
    TRUTH_BOUNDARY_CONTRADICTION,
    TRUTH_BOUNDARY_QUERY,
    TRUTH_BOUNDARY_RISK,
    TRUTH_BOUNDARY_STATE,
    UNKNOWN_IS_VALID,
)
from project_atlas.intelligence.derived_state import StateContext
from project_atlas.intelligence.query import SlotStatus
from project_atlas.intelligence.timewin import IntelligenceTimeError, parse_instant
from project_atlas.intelligence.types import AssessableClaim, ValidityWindowInput

PACKAGE_ID = "AS-2.0-API-001"
CERTIFIED_QUERY_KINDS: frozenset[str] = frozenset(
    {
        IntelligenceQueryKind.CHANGE.value,
        IntelligenceQueryKind.CONTEXT.value,
        IntelligenceQueryKind.GAP_PRIORITY.value,
        IntelligenceQueryKind.DEPENDENCIES.value,
        IntelligenceQueryKind.DECISION.value,
    }
)
_DEDICATED_KINDS: frozenset[str] = frozenset(
    {
        IntelligenceQueryKind.EVIDENCE.value,
        IntelligenceQueryKind.CONFLICTS.value,
        IntelligenceQueryKind.EXPLAIN.value,
        IntelligenceQueryKind.STATE.value,
        IntelligenceQueryKind.ATTENTION.value,
    }
)
# Same project-token boundary as /v1/conflicts. Validate before any path.
_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_LIMITATIONS: tuple[str, ...] = (
    "DERIVED_INTELLIGENCE_IS_AUTHORITY=NO",
    "API_RESULT_IS_AUTHORITY=NO",
    "CANONICAL_WRITE=NO",
    "UNKNOWN_IS_VALID=YES",
    "CONTRADICTION_CANDIDATE_IS_PROVEN_FALSEHOOD=NO",
    "RISK_IS_FACT=NO",
    "NO_FAKE_PROBABILITY",
)


class HonestyClass(StrEnum):
    """HTTP-facing honesty classes. Never collapse these into each other."""

    UNKNOWN = "UNKNOWN"
    NO_DATA = "NO_DATA"
    VALID_EMPTY = "VALID_EMPTY"
    NO_MATCH = "NO_MATCH"
    CONTESTED = "CONTESTED"
    STALE = "STALE"
    HTTP_FAILURE = "HTTP_FAILURE"
    MALFORMED_INPUT = "MALFORMED_INPUT"
    UNSUPPORTED_SCOPE = "UNSUPPORTED_SCOPE"


class WebIntelligenceError(ValueError):
    """Fail-closed intelligence API read error."""

    def __init__(self, message: str, honesty: HonestyClass | None = None) -> None:
        super().__init__(message)
        self.honesty = honesty or (
            HonestyClass.UNSUPPORTED_SCOPE
            if "unsupported" in message
            else HonestyClass.MALFORMED_INPUT
        )


def _safe_project_id(project_id: str) -> str:
    token = (project_id or "").strip()
    if not token:
        raise WebIntelligenceError(
            "intel-api-project-id-required", HonestyClass.MALFORMED_INPUT
        )
    if not _ID_RE.fullmatch(token):
        raise WebIntelligenceError(
            "intel-api-project-id-invalid", HonestyClass.MALFORMED_INPUT
        )
    try:
        return safe_relative_component(token, label="project id")
    except ValueError as exc:
        raise WebIntelligenceError(
            "intel-api-project-id-invalid", HonestyClass.MALFORMED_INPUT
        ) from exc


def _existing_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError as exc:
        raise WebIntelligenceError(
            "intel-api-filesystem-unreadable", HonestyClass.MALFORMED_INPUT
        ) from exc


def _optional_as_of(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        parse_instant(text, field="as-of")
    except IntelligenceTimeError as exc:
        raise WebIntelligenceError(str(exc), HonestyClass.MALFORMED_INPUT) from exc
    return text


def claims_file_present(vault: Path, project_id: str) -> bool:
    token = _safe_project_id(project_id)
    return _existing_file(vault / "state" / "claims" / f"{token}.json")


def load_assessable_claims(vault: Path, project_id: str) -> tuple[AssessableClaim, ...]:
    """Read ``state/claims/<project>.json``. Missing file is empty, not invented."""
    token = _safe_project_id(project_id)
    path = vault / "state" / "claims" / f"{token}.json"
    if not _existing_file(path):
        return ()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WebIntelligenceError(
            f"intel-api-claims-unreadable:{token}", HonestyClass.MALFORMED_INPUT
        ) from exc
    entries = raw.get("claims") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        raise WebIntelligenceError(
            f"intel-api-claims-malformed:{token}", HonestyClass.MALFORMED_INPUT
        )
    found: list[AssessableClaim] = []
    for item in entries:
        # AS-INTEL-MIXED-CORRUPT-CLAIMS-001: mixed valid+corrupt is not
        # a healthy partial read. Core query/kdiff fail closed on an
        # incomplete claim entry; silently skipping here reported
        # OBSERVED / VALID_EMPTY over a damaged claims file.
        if not isinstance(item, dict):
            raise WebIntelligenceError(
                f"intel-api-claims-malformed:{token}", HonestyClass.MALFORMED_INPUT
            )
        claim = _claim_from_record(item, token)
        if claim is None:
            raise WebIntelligenceError(
                f"intel-api-claims-malformed:{token}", HonestyClass.MALFORMED_INPUT
            )
        found.append(claim)
    return tuple(found)


def load_validity_windows(vault: Path, project_id: str) -> tuple[ValidityWindowInput, ...]:
    """Document-declared windows from claim records and optional catalog."""
    token = _safe_project_id(project_id)
    by_claim: dict[str, ValidityWindowInput] = {}
    path = vault / "state" / "claims" / f"{token}.json"
    if _existing_file(path):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WebIntelligenceError(
                f"intel-api-claims-unreadable:{token}", HonestyClass.MALFORMED_INPUT
            ) from exc
        entries = raw.get("claims") if isinstance(raw, dict) else None
        if not isinstance(entries, list):
            raise WebIntelligenceError(
                f"intel-api-claims-malformed:{token}", HonestyClass.MALFORMED_INPUT
            )
        for item in entries:
            if not isinstance(item, dict):
                raise WebIntelligenceError(
                    f"intel-api-claims-malformed:{token}", HonestyClass.MALFORMED_INPUT
                )
            claim_id = str(item.get("claim_id") or "").strip()
            if not claim_id:
                raise WebIntelligenceError(
                    f"intel-api-claims-malformed:{token}", HonestyClass.MALFORMED_INPUT
                )
            valid_from = _optional_bound(item.get("valid_from"))
            valid_to = _optional_bound(item.get("valid_to"))
            if valid_from or valid_to:
                by_claim[claim_id] = ValidityWindowInput(
                    claim_id=claim_id,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    evidence_kind="claim-record",
                )
    catalog = vault / "generated" / "ops" / "bitemporal" / f"{token}-validity-catalog.json"
    if _existing_file(catalog):
        try:
            payload = json.loads(catalog.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WebIntelligenceError(
                f"intel-api-catalog-unreadable:{token}", HonestyClass.MALFORMED_INPUT
            ) from exc
        windows = payload.get("windows") if isinstance(payload, dict) else None
        if not isinstance(windows, list):
            raise WebIntelligenceError(
                f"intel-api-catalog-malformed:{token}", HonestyClass.MALFORMED_INPUT
            )
        for item in windows:
            if not isinstance(item, dict):
                raise WebIntelligenceError(
                    f"intel-api-catalog-malformed:{token}", HonestyClass.MALFORMED_INPUT
                )
            claim_id = str(item.get("claim_id") or "").strip()
            if not claim_id:
                raise WebIntelligenceError(
                    f"intel-api-catalog-malformed:{token}", HonestyClass.MALFORMED_INPUT
                )
            existing = by_claim.get(claim_id)
            by_claim[claim_id] = ValidityWindowInput(
                claim_id=claim_id,
                valid_from=_optional_bound(item.get("valid_from"))
                or (existing.valid_from if existing else None),
                valid_to=_optional_bound(item.get("valid_to"))
                or (existing.valid_to if existing else None),
                evidence_kind=str(item.get("evidence_kind") or "catalog"),
            )
    return tuple(sorted(by_claim.values(), key=lambda item: item.claim_id))


def _optional_bound(raw: object) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    try:
        parse_instant(text, field="validity-bound")
    except IntelligenceTimeError as exc:
        raise WebIntelligenceError(str(exc), HonestyClass.MALFORMED_INPUT) from exc
    return text


def _claim_from_record(item: dict[str, Any], project_id: str) -> AssessableClaim | None:
    claim_id = str(item.get("claim_id") or "").strip()
    subject = str(item.get("subject") or "").strip()
    field = str(item.get("field") or "").strip()
    if not claim_id or not subject or not field:
        return None
    refs: list[ProvenanceReference] = []
    provenance = item.get("provenance")
    if isinstance(provenance, list):
        for ref in provenance:
            if not isinstance(ref, dict):
                continue
            source_id = str(ref.get("source_id") or "").strip()
            resource = str(ref.get("resource") or "").strip()
            if not source_id or not resource:
                continue
            sha = ref.get("sha256")
            try:
                refs.append(
                    ProvenanceReference(
                        source_id=source_id,
                        resource=resource,
                        sha256=sha if isinstance(sha, str) else None,
                    )
                )
            except ValueError:
                continue
    return AssessableClaim(
        claim_id=claim_id,
        project_id=str(item.get("project_id") or project_id),
        subject=subject,
        field=field,
        value=str(item.get("value") or ""),
        provenance=tuple(refs),
        authority=_enum_or_none(AuthorityLevel, item.get("authority")),
        confidence=_enum_or_none(ConfidenceState, item.get("confidence")),
        lifecycle=_enum_or_none(ClaimLifecycle, item.get("lifecycle")),
        claim_type=str(item.get("claim_type") or "") or None,
    )


def _enum_or_none(enum_type: type[Any], raw: object) -> Any:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return enum_type(raw)
    except ValueError:
        return None


def _query(
    project_id: str,
    kind: IntelligenceQueryKind,
    *,
    subject: str | None = None,
    field: str | None = None,
    claim_id: str | None = None,
    as_of_valid_time: str | None = None,
) -> IntelligenceQuery:
    return IntelligenceQuery(
        project_id=project_id,
        kind=kind,
        subject=subject or None,
        field=field or None,
        claim_id=claim_id or None,
        as_of_valid_time=as_of_valid_time,
    )


def classify_honesty(
    status: SlotStatus | str,
    *,
    claims_present: bool,
    filtered: bool = False,
    empty_payload: bool = False,
) -> str:
    slot = status if isinstance(status, SlotStatus) else SlotStatus(status)
    if slot is SlotStatus.CONTESTED:
        return HonestyClass.CONTESTED.value
    if slot is SlotStatus.STALE:
        return HonestyClass.STALE.value
    if slot is SlotStatus.INVALID:
        return HonestyClass.MALFORMED_INPUT.value
    if slot is SlotStatus.NO_EVIDENCE:
        if not claims_present:
            return HonestyClass.NO_DATA.value
        if filtered:
            return HonestyClass.NO_MATCH.value
        return HonestyClass.VALID_EMPTY.value
    if empty_payload and not claims_present:
        return HonestyClass.NO_DATA.value
    if empty_payload:
        return HonestyClass.VALID_EMPTY.value
    if slot is SlotStatus.UNKNOWN:
        return HonestyClass.UNKNOWN.value
    if slot is SlotStatus.DERIVED:
        return "DERIVED"
    if slot is SlotStatus.OBSERVED:
        return "OBSERVED"
    assert_never(slot)


def _envelope(
    *,
    project_id: str,
    as_of: str | None,
    status: str,
    honesty: str,
    reason: str,
    authority_note: str,
    truth_boundary: str,
    extra: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "package_id": PACKAGE_ID,
        "status": status,
        "honesty": honesty,
        "project": project_id,
        "project_id": project_id,
        "scope": "project",
        "as_of": as_of,
        "derived_classification": status,
        "reasons": [reason] if reason else [],
        "reason": reason,
        "authority_note": authority_note,
        "authority": {
            "derived_intelligence_is_authority": DERIVED_INTELLIGENCE_IS_AUTHORITY,
            "canonical_write": CANONICAL_WRITE == "YES",
            "unknown_is_valid": UNKNOWN_IS_VALID,
            "api_result_is_authority": "NO",
        },
        "temporal_context": {
            "as_of_valid_time": as_of,
            "evaluation": "as-of-valid-time" if as_of else "unspecified-valid-time",
        },
        "limitations": list(_LIMITATIONS),
        "derived_intelligence_is_authority": DERIVED_INTELLIGENCE_IS_AUTHORITY,
        "canonical_write": False,
        "truth_boundary": truth_boundary,
        "numeric_confidence": None,
    }
    payload.update(extra)
    return payload


def _run_query(
    vault: Path,
    project_id: str,
    kind: IntelligenceQueryKind,
    *,
    subject: str | None = None,
    field: str | None = None,
    claim_id: str | None = None,
    as_of_valid_time: str | None = None,
) -> tuple[str, str | None, tuple[AssessableClaim, ...], IntelligenceAnswer, bool]:
    token = _safe_project_id(project_id)
    as_of = _optional_as_of(as_of_valid_time)
    claims = load_assessable_claims(vault, token)
    windows = load_validity_windows(vault, token)
    answer = query_intelligence(
        _query(
            token,
            kind,
            subject=subject,
            field=field,
            claim_id=claim_id,
            as_of_valid_time=as_of,
        ),
        claims,
        validity_windows=windows,
    )
    return token, as_of, claims, answer, claims_file_present(vault, token)


def read_intelligence_evidence(
    vault: Path,
    project_id: str,
    *,
    subject: str | None = None,
    field: str | None = None,
    claim_id: str | None = None,
    as_of_valid_time: str | None = None,
) -> dict[str, Any]:
    token, as_of, _claims, answer, present = _run_query(
        vault,
        project_id,
        IntelligenceQueryKind.EVIDENCE,
        subject=subject,
        field=field,
        claim_id=claim_id,
        as_of_valid_time=as_of_valid_time,
    )
    assessments = [item.model_dump(mode="json") for item in answer.assessments]
    filtered = bool(subject or field or claim_id)
    honesty = classify_honesty(
        answer.status,
        claims_present=present,
        filtered=filtered,
        empty_payload=not assessments,
    )
    supporting = [
        ref
        for item in assessments
        for ref in item.get("supporting_evidence", [])
    ]
    contradicting = [
        ref
        for item in assessments
        for ref in item.get("contradicting_evidence", [])
    ]
    unknown = [
        factor
        for item in assessments
        for factor in item.get("unknown_factors", [])
    ]
    return _envelope(
        project_id=token,
        as_of=as_of,
        status=answer.status.value,
        honesty=honesty,
        reason=answer.reason,
        authority_note=answer.authority_note,
        truth_boundary=TRUTH_BOUNDARY_QUERY,
        extra={
            "outcome": answer.outcome.value,
            "assessments": assessments,
            "supporting_evidence": supporting,
            "contradicting_evidence": contradicting,
            "unknown_evidence": unknown,
            "provenance": [
                link
                for item in assessments
                for link in item.get("provenance_links", [])
            ],
        },
    )


def read_intelligence_conflicts(
    vault: Path,
    project_id: str,
    *,
    as_of_valid_time: str | None = None,
) -> dict[str, Any]:
    token, as_of, _claims, answer, present = _run_query(
        vault,
        project_id,
        IntelligenceQueryKind.CONFLICTS,
        as_of_valid_time=as_of_valid_time,
    )
    candidates = [item.model_dump() for item in answer.candidates]
    honesty = classify_honesty(
        answer.status,
        claims_present=present,
        empty_payload=not candidates,
    )
    return _envelope(
        project_id=token,
        as_of=as_of,
        status=answer.status.value,
        honesty=honesty,
        reason=answer.reason,
        authority_note="candidate-not-resolution",
        truth_boundary=TRUTH_BOUNDARY_CONTRADICTION,
        extra={
            "candidates": candidates,
            "replaces_v1_conflicts": False,
            "contradiction_is_proven_falsehood": "NO",
        },
    )


def read_intelligence_explain(
    vault: Path,
    project_id: str,
    *,
    subject: str | None = None,
    field: str | None = None,
    claim_id: str | None = None,
    as_of_valid_time: str | None = None,
) -> dict[str, Any]:
    token, as_of, _claims, answer, present = _run_query(
        vault,
        project_id,
        IntelligenceQueryKind.EXPLAIN,
        subject=subject,
        field=field,
        claim_id=claim_id,
        as_of_valid_time=as_of_valid_time,
    )
    honesty = classify_honesty(
        answer.status,
        claims_present=present,
        filtered=bool(subject or field or claim_id),
        empty_payload=answer.explanation is None,
    )
    return _envelope(
        project_id=token,
        as_of=as_of,
        status=answer.status.value,
        honesty=honesty,
        reason=answer.reason,
        authority_note=answer.authority_note,
        truth_boundary=TRUTH_BOUNDARY_QUERY,
        extra={"explanation": answer.explanation},
    )


def read_project_state(
    vault: Path,
    project_id: str,
    *,
    as_of_valid_time: str | None = None,
) -> dict[str, Any]:
    token = _safe_project_id(project_id)
    as_of = _optional_as_of(as_of_valid_time)
    claims = load_assessable_claims(vault, token)
    windows = load_validity_windows(vault, token)
    state = synthesize_project_state(
        token,
        claims,
        StateContext(as_of_valid_time=as_of, validity_windows=windows),
    )
    payload = state.model_dump(mode="json")
    present = claims_file_present(vault, token)
    if state.contested_facts:
        honesty = HonestyClass.CONTESTED.value
        status = "contested"
    elif state.stale_facts:
        honesty = HonestyClass.STALE.value
        status = "stale"
    elif not present:
        honesty = HonestyClass.NO_DATA.value
        status = "unknown"
    elif not claims:
        honesty = HonestyClass.VALID_EMPTY.value
        status = "unknown"
    elif state.unknown_facts and not state.known_facts:
        honesty = HonestyClass.UNKNOWN.value
        status = "unknown"
    else:
        honesty = HonestyClass.UNKNOWN.value
        status = payload.get("overall_status") or "derived"
    payload.update(
        _envelope(
            project_id=token,
            as_of=as_of,
            status=str(status),
            honesty=honesty,
            reason=str(payload.get("why") or "derived-state-not-canonical"),
            authority_note="derived-state-not-canonical",
            truth_boundary=TRUTH_BOUNDARY_STATE,
            extra={},
        )
    )
    return payload


def read_project_attention(
    vault: Path,
    project_id: str,
    *,
    as_of_valid_time: str | None = None,
) -> dict[str, Any]:
    token, as_of, _claims, answer, present = _run_query(
        vault,
        project_id,
        IntelligenceQueryKind.ATTENTION,
        as_of_valid_time=as_of_valid_time,
    )
    honesty = classify_honesty(
        answer.status,
        claims_present=present,
        empty_payload=not answer.risks,
    )
    return _envelope(
        project_id=token,
        as_of=as_of,
        status=answer.status.value,
        honesty=honesty,
        reason=answer.reason,
        authority_note="risk-is-not-fact",
        truth_boundary=TRUTH_BOUNDARY_RISK,
        extra={
            "risks": list(answer.risks),
            "attention_rank_is_score": "NO",
            "numeric_priority_score": None,
        },
    )


def read_intelligence_query(
    vault: Path,
    project_id: str,
    kind: str,
    *,
    subject: str | None = None,
    field: str | None = None,
    claim_id: str | None = None,
    as_of_valid_time: str | None = None,
) -> dict[str, Any]:
    token = (kind or "").strip()
    if token in _DEDICATED_KINDS:
        raise WebIntelligenceError(
            f"intel-api-use-dedicated-route:{token}",
            HonestyClass.UNSUPPORTED_SCOPE,
        )
    if token not in CERTIFIED_QUERY_KINDS:
        raise WebIntelligenceError(
            f"intel-api-kind-unsupported:{token or 'missing'}",
            HonestyClass.UNSUPPORTED_SCOPE,
        )
    query_kind = IntelligenceQueryKind(token)
    project, as_of, _claims, answer, present = _run_query(
        vault,
        project_id,
        query_kind,
        subject=subject,
        field=field,
        claim_id=claim_id,
        as_of_valid_time=as_of_valid_time,
    )
    empty = not (
        answer.changes
        or answer.context
        or answer.prioritized_gaps
        or answer.dependencies
        or answer.decision
    )
    honesty = classify_honesty(
        answer.status,
        claims_present=present,
        filtered=bool(subject or field or claim_id),
        empty_payload=empty,
    )
    extra: dict[str, Any] = {
        "kind": query_kind.value,
        "outcome": answer.outcome.value,
        "changes": list(answer.changes),
        "context": answer.context,
        "prioritized_gaps": list(answer.prioritized_gaps),
        "dependencies": list(answer.dependencies),
        "decision": answer.decision,
        "gap_priority_is_fact": "NO",
        "dependency_is_inferred": "NO",
        "decision_engine_is_authority": "NO",
        "decision_candidate_is_command": "NO",
    }
    return _envelope(
        project_id=project,
        as_of=as_of,
        status=answer.status.value,
        honesty=honesty,
        reason=answer.reason,
        authority_note=answer.authority_note,
        truth_boundary=TRUTH_BOUNDARY_QUERY,
        extra=extra,
    )


def read_portfolio_state(
    vault: Path,
    project_ids: tuple[str, ...] = (),
    *,
    as_of_valid_time: str | None = None,
) -> dict[str, Any]:
    as_of = _optional_as_of(as_of_valid_time)
    requested = tuple(item.strip() for item in project_ids if item.strip())
    if not requested:
        raise WebIntelligenceError(
            "intel-api-portfolio-scope-required",
            HonestyClass.UNSUPPORTED_SCOPE,
        )
    keys = tuple(_safe_project_id(item) for item in requested)
    projects = {key: load_assessable_claims(vault, key) for key in keys}
    windows = {key: load_validity_windows(vault, key) for key in keys}
    state = aggregate_portfolio_state(
        projects, as_of_valid_time=as_of, validity_windows_by_project=windows
    )
    deps = detect_portfolio_dependencies(projects)
    attention = rank_portfolio_attention(
        projects, as_of_valid_time=as_of, validity_windows_by_project=windows
    )
    if not keys:
        honesty = HonestyClass.NO_DATA.value
        status = "unknown"
    elif not any(projects.values()):
        honesty = HonestyClass.VALID_EMPTY.value
        status = "unknown"
    else:
        honesty = HonestyClass.UNKNOWN.value
        status = "derived"
    payload = _envelope(
        project_id=",".join(keys) or "portfolio",
        as_of=as_of,
        status=status,
        honesty=honesty,
        reason="portfolio-not-authority",
        authority_note="portfolio-not-authority",
        truth_boundary=TRUTH_BOUNDARY_STATE,
        extra={
            "scope": "portfolio",
            "project_ids": list(keys),
            "state": state.model_dump(mode="json"),
            "dependencies": [item.model_dump(mode="json") for item in deps],
            "attention": [item.model_dump(mode="json") for item in attention],
            "numeric_priority_score": None,
        },
    )
    return payload
