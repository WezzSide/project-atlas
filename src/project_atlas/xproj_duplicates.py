"""AS-XPROJ-003 — Duplicate / successor project detection.

Deterministic **review candidates** only. Never auto-collapse project UUIDs,
never name-merge, never fuzzy/LLM matching, never elevate above
``authority.level = derived``.

Truth boundary: DUPLICATE CANDIDATE ≠ AUTOMATIC UUID COLLAPSE
(AS-XPROJ-INV-NO-AUTOCOLLAPSE-001).

Allowed signals (architecture §6.3): canonical remote URL equality, AS-ID-001
lineage / retired-slot mappings, identity-lock / marker collisions, explicit
successor registration, structural path-prefix monorepo overlap under approved
roots.

Writes only under ``generated/xproj/duplicate-candidates/**``. Does not dual-own
AS-XPROJ-004 indexes/conflicts, GRAPH-005 projections, GRAPH-004 quarantine
store, or ``knowledge_compiler``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from project_atlas.schema import validate_record
from project_atlas.secrets import scan_text

PACKAGE_ID = "AS-XPROJ-003"
AUTHORITY_LEVEL = "derived"
TRUTH_BOUNDARY = "DUPLICATE CANDIDATE ≠ AUTOMATIC UUID COLLAPSE"

ALLOWED_WRITE_PREFIXES: tuple[str, ...] = ("generated/xproj/duplicate-candidates/",)

_FORBIDDEN_WRITE_PREFIXES: tuple[str, ...] = (
    "relationships/",
    "state/current-state/",
    "state/authoritative-state/",
    "claims/",
    "generated/indexes/",
    "generated/query/",
    "generated/graph/",
    "generated/xproj/conflicts/",
    "generated/xproj/indexes/",
    "state/global-entities/",
)

_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

CandidateCategory = Literal[
    "canonical-remote-url-collision",
    "lineage-retired-slot-collision",
    "identity-lock-collision",
    "explicit-successor",
    "monorepo-path-prefix-overlap",
    "name-only-match-forbidden",
    "fuzzy-match-forbidden",
    "autocollapse-forbidden",
    "llm-match-forbidden",
    "invalid-observation",
    "secret-finding",
]

ReviewSignal = Literal[
    "canonical-remote-url",
    "lineage-retired-slot",
    "identity-lock",
    "explicit-successor",
    "monorepo-path-prefix",
]


class XprojDuplicateError(ValueError):
    """Fail-closed duplicate-detection error."""


@dataclass(frozen=True)
class ProjectObservation:
    """Structured, deterministic project observation (no name-similarity fields)."""

    project_id: str
    canonical_remote_url: str | None = None
    lineage_id: str | None = None
    retired_slot_id: str | None = None
    identity_lock_key: str | None = None
    root_path: str | None = None
    explicit_successor_of: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"project_id": self.project_id}
        if self.canonical_remote_url is not None:
            payload["canonical_remote_url"] = self.canonical_remote_url
        if self.lineage_id is not None:
            payload["lineage_id"] = self.lineage_id
        if self.retired_slot_id is not None:
            payload["retired_slot_id"] = self.retired_slot_id
        if self.identity_lock_key is not None:
            payload["identity_lock_key"] = self.identity_lock_key
        if self.root_path is not None:
            payload["root_path"] = self.root_path
        if self.explicit_successor_of is not None:
            payload["explicit_successor_of"] = self.explicit_successor_of
        return payload


@dataclass(frozen=True)
class DuplicateCandidate:
    """Review / quarantine candidate — never includes winning_choice or UUID rewrite."""

    candidate_id: str
    category: CandidateCategory
    reason: str
    project_ids: tuple[str, ...]
    signal: ReviewSignal | None
    inputs_considered: Mapping[str, Any]
    status: Literal["review_candidate", "reject"] = "review_candidate"

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "package_id": PACKAGE_ID,
            "candidate_id": self.candidate_id,
            "category": self.category,
            "authority": {
                "level": AUTHORITY_LEVEL,
                "note": (
                    "Duplicate candidate is derived review intelligence; "
                    "never auto-collapse project UUIDs."
                ),
            },
            "status": self.status,
            "reason": self.reason,
            "project_ids": list(self.project_ids),
            "inputs_considered": dict(self.inputs_considered),
            "truth_boundary": TRUTH_BOUNDARY,
            "autocollapse": False,
        }
        if self.signal is not None:
            payload["signal"] = self.signal
        return payload

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n"


@dataclass
class DuplicateDetectionResult:
    """In-memory detection outcome (deterministic ordering on emit)."""

    candidates: list[DuplicateCandidate] = field(default_factory=list)
    rejects: list[DuplicateCandidate] = field(default_factory=list)

    @property
    def review_count(self) -> int:
        return len(self.candidates)

    @property
    def reject_count(self) -> int:
        return len(self.rejects)


def _redact_reason(reason: str) -> str:
    cleaned = " ".join(reason.split())
    if len(cleaned) > 240:
        return cleaned[:237] + "..."
    return cleaned


def _safe_name(token: str) -> str:
    return re.sub(r"[^A-Za-z0-9._:-]+", "_", token)[:128]


def _emit_filename(token: str) -> str:
    return f"{_safe_name(token)}.json"


def _validate_project_id(project_id: str) -> str:
    if not _PROJECT_ID_RE.fullmatch(project_id):
        raise XprojDuplicateError(f"project-id-invalid:{project_id!r}")
    return project_id


def _secret_findings_present(*parts: str | None) -> bool:
    return any(part and scan_text(part) for part in parts)


def normalize_canonical_remote_url(url: str) -> str:
    """Normalize a remote URL for exact equality (no fuzzy host matching)."""
    raw = url.strip()
    if not raw:
        raise XprojDuplicateError("canonical-remote-url-empty")
    if re.match(r"^[^/@]+@[^:]+:", raw) and "://" not in raw:
        user_host, path = raw.split(":", 1)
        raw = f"ssh://{user_host}/{path.lstrip('/')}"
    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        raise XprojDuplicateError(f"canonical-remote-url-unparseable:{url!r}")
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    if netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    path = (parts.path or "").rstrip("/")
    if path.lower().endswith(".git"):
        path = path[:-4]
    path = path.casefold()
    return urlunsplit((scheme, netloc, path, "", ""))


def normalize_root_path(path: str) -> str:
    """Normalize a filesystem root to POSIX-ish form for prefix checks."""
    cleaned = path.strip().replace("\\", "/")
    if not cleaned:
        raise XprojDuplicateError("root-path-empty")
    normalized = str(PurePosixPath(cleaned))
    if normalized != "/" and normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    return normalized.casefold()


def _candidate_id(*parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"xdup-{digest[:24]}"


def _reject(
    *,
    category: CandidateCategory,
    reason: str,
    project_ids: Sequence[str] = (),
    inputs: Mapping[str, Any] | None = None,
) -> DuplicateCandidate:
    ids = tuple(sorted({_validate_project_id(p) for p in project_ids})) if project_ids else ()
    cid = _candidate_id("reject", category, *ids, reason)
    return DuplicateCandidate(
        candidate_id=cid,
        category=category,
        reason=_redact_reason(reason),
        project_ids=ids,
        signal=None,
        inputs_considered=dict(inputs or {}),
        status="reject",
    )


def _review(
    *,
    category: CandidateCategory,
    signal: ReviewSignal,
    reason: str,
    project_ids: Sequence[str],
    inputs: Mapping[str, Any],
) -> DuplicateCandidate:
    ids = tuple(sorted({_validate_project_id(p) for p in project_ids}))
    if len(ids) < 2:
        raise XprojDuplicateError("review-candidate-requires-two-projects")
    cid = _candidate_id(category, signal, *ids, json.dumps(inputs, sort_keys=True))
    return DuplicateCandidate(
        candidate_id=cid,
        category=category,
        reason=_redact_reason(reason),
        project_ids=ids,
        signal=signal,
        inputs_considered=dict(inputs),
        status="review_candidate",
    )


def parse_observation(raw: Mapping[str, Any]) -> ProjectObservation:
    """Parse one observation mapping; refuse name-similarity / winner fields."""
    if "display_name" in raw or "project_name" in raw or "name" in raw:
        raise XprojDuplicateError("name-fields-forbidden-in-observation")
    for banned in (
        "winning_choice",
        "winning_project_id",
        "collapsed_uuid",
        "rewritten_project_id",
        "fuzzy_score",
        "embedding",
    ):
        if banned in raw:
            raise XprojDuplicateError(f"banned-field:{banned}")
    project_id = raw.get("project_id")
    if not isinstance(project_id, str):
        raise XprojDuplicateError("project-id-required")
    _validate_project_id(project_id)

    def _opt_str(key: str) -> str | None:
        value = raw.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise XprojDuplicateError(f"{key}-not-string")
        return value

    return ProjectObservation(
        project_id=project_id,
        canonical_remote_url=_opt_str("canonical_remote_url"),
        lineage_id=_opt_str("lineage_id"),
        retired_slot_id=_opt_str("retired_slot_id"),
        identity_lock_key=_opt_str("identity_lock_key"),
        root_path=_opt_str("root_path"),
        explicit_successor_of=_opt_str("explicit_successor_of"),
    )


def detect_project_duplicates(
    observations: Sequence[ProjectObservation | Mapping[str, Any]],
    *,
    approved_monorepo_roots: Sequence[str] | None = None,
    match_by_name: bool = False,
    fuzzy: bool = False,
    llm: bool = False,
    rewrite_uuids: bool = False,
) -> DuplicateDetectionResult:
    """Detect duplicate / successor review candidates from structured observations."""
    result = DuplicateDetectionResult()

    if match_by_name:
        result.rejects.append(
            _reject(
                category="name-only-match-forbidden",
                reason="Name-only project matching is forbidden (AS-XPROJ-INV-NO-FUZZY-001).",
                inputs={"match_by_name": True},
            )
        )
        return result
    if fuzzy:
        result.rejects.append(
            _reject(
                category="fuzzy-match-forbidden",
                reason="Fuzzy / embedding project matching is forbidden.",
                inputs={"fuzzy": True},
            )
        )
        return result
    if llm:
        result.rejects.append(
            _reject(
                category="llm-match-forbidden",
                reason="LLM 'same project' matching is forbidden.",
                inputs={"llm": True},
            )
        )
        return result
    if rewrite_uuids:
        result.rejects.append(
            _reject(
                category="autocollapse-forbidden",
                reason="Automatic project UUID rewrite is forbidden "
                "(AS-XPROJ-INV-NO-AUTOCOLLAPSE-001).",
                inputs={"rewrite_uuids": True},
            )
        )
        return result

    parsed: list[ProjectObservation] = []
    for index, item in enumerate(observations):
        try:
            obs = item if isinstance(item, ProjectObservation) else parse_observation(item)
            if _secret_findings_present(
                obs.canonical_remote_url,
                obs.lineage_id,
                obs.retired_slot_id,
                obs.identity_lock_key,
                obs.root_path,
                obs.project_id,
            ):
                # AS-SEC-SCAN-XPROJ-DUP-JSON-ESC-001: do not echo a
                # JSON-``\\u``-decoded secret project_id into reject
                # candidate_id, project_ids, or inputs.
                result.rejects.append(
                    _reject(
                        category="secret-finding",
                        reason="Observation fields contain secret-like material; excluded.",
                        project_ids=(),
                        inputs={"index": index},
                    )
                )
                continue
            parsed.append(obs)
        except XprojDuplicateError as exc:
            result.rejects.append(
                _reject(
                    category="invalid-observation",
                    reason=str(exc),
                    inputs={"index": index},
                )
            )

    by_id: dict[str, ProjectObservation] = {}
    for obs in parsed:
        if obs.project_id in by_id:
            result.rejects.append(
                _reject(
                    category="invalid-observation",
                    reason="Duplicate observation for same project_id in one batch.",
                    project_ids=[obs.project_id],
                    inputs={"project_id": obs.project_id},
                )
            )
            continue
        by_id[obs.project_id] = obs
    projects = list(by_id.values())

    url_groups: dict[str, list[str]] = defaultdict(list)
    for obs in projects:
        if not obs.canonical_remote_url:
            continue
        try:
            key = normalize_canonical_remote_url(obs.canonical_remote_url)
        except XprojDuplicateError as exc:
            result.rejects.append(
                _reject(
                    category="invalid-observation",
                    reason=str(exc),
                    project_ids=[obs.project_id],
                )
            )
            continue
        url_groups[key].append(obs.project_id)
    for url_key, ids in sorted(url_groups.items()):
        unique = sorted(set(ids))
        if len(unique) >= 2:
            result.candidates.append(
                _review(
                    category="canonical-remote-url-collision",
                    signal="canonical-remote-url",
                    reason="Distinct project_ids share a normalized canonical remote URL.",
                    project_ids=unique,
                    inputs={"normalized_remote_url": url_key, "project_ids": unique},
                )
            )

    lineage_groups: dict[str, list[str]] = defaultdict(list)
    for obs in projects:
        if not obs.lineage_id and not obs.retired_slot_id:
            continue
        key = f"{obs.lineage_id or ''}|{obs.retired_slot_id or ''}"
        if key == "|":
            continue
        lineage_groups[key].append(obs.project_id)
    for lineage_key, ids in sorted(lineage_groups.items()):
        unique = sorted(set(ids))
        if len(unique) >= 2:
            lineage_id, retired = lineage_key.split("|", 1)
            result.candidates.append(
                _review(
                    category="lineage-retired-slot-collision",
                    signal="lineage-retired-slot",
                    reason="Distinct project_ids share lineage / retired-slot mapping.",
                    project_ids=unique,
                    inputs={
                        "lineage_id": lineage_id or None,
                        "retired_slot_id": retired or None,
                        "project_ids": unique,
                    },
                )
            )

    lock_groups: dict[str, list[str]] = defaultdict(list)
    for obs in projects:
        if not obs.identity_lock_key:
            continue
        lock_groups[obs.identity_lock_key.casefold()].append(obs.project_id)
    for lock_key, ids in sorted(lock_groups.items()):
        unique = sorted(set(ids))
        if len(unique) >= 2:
            result.candidates.append(
                _review(
                    category="identity-lock-collision",
                    signal="identity-lock",
                    reason="Distinct project_ids share an identity-lock / marker key.",
                    project_ids=unique,
                    inputs={"identity_lock_key": lock_key, "project_ids": unique},
                )
            )

    for obs in projects:
        if not obs.explicit_successor_of:
            continue
        predecessor = obs.explicit_successor_of
        if predecessor == obs.project_id:
            result.rejects.append(
                _reject(
                    category="invalid-observation",
                    reason="Project cannot be an explicit successor of itself.",
                    project_ids=[obs.project_id],
                )
            )
            continue
        try:
            _validate_project_id(predecessor)
        except XprojDuplicateError as exc:
            result.rejects.append(
                _reject(
                    category="invalid-observation",
                    reason=str(exc),
                    project_ids=[obs.project_id],
                )
            )
            continue
        pair = sorted([obs.project_id, predecessor])
        result.candidates.append(
            _review(
                category="explicit-successor",
                signal="explicit-successor",
                reason="Explicit successor registration recorded between projects.",
                project_ids=pair,
                inputs={
                    "successor_project_id": obs.project_id,
                    "predecessor_project_id": predecessor,
                },
            )
        )

    approved = [normalize_root_path(r) for r in (approved_monorepo_roots or ())]
    path_map: dict[str, str] = {}
    for obs in projects:
        if not obs.root_path:
            continue
        try:
            path_map[obs.project_id] = normalize_root_path(obs.root_path)
        except XprojDuplicateError as exc:
            result.rejects.append(
                _reject(
                    category="invalid-observation",
                    reason=str(exc),
                    project_ids=[obs.project_id],
                )
            )
    if approved and path_map:
        ids = sorted(path_map)
        for i, left_id in enumerate(ids):
            left = path_map[left_id]
            under_left = any(
                left == root or left.startswith(root.rstrip("/") + "/") for root in approved
            )
            if not under_left:
                continue
            for right_id in ids[i + 1 :]:
                right = path_map[right_id]
                under_right = any(
                    right == root or right.startswith(root.rstrip("/") + "/") for root in approved
                )
                if not under_right:
                    continue
                left_is_prefix = right.startswith(left.rstrip("/") + "/")
                right_is_prefix = left.startswith(right.rstrip("/") + "/")
                if left_is_prefix or right_is_prefix:
                    pair = sorted([left_id, right_id])
                    result.candidates.append(
                        _review(
                            category="monorepo-path-prefix-overlap",
                            signal="monorepo-path-prefix",
                            reason=(
                                "Project roots form a path-prefix overlap under an "
                                "approved monorepo root."
                            ),
                            project_ids=pair,
                            inputs={
                                "root_paths": {
                                    left_id: path_map[left_id],
                                    right_id: path_map[right_id],
                                },
                                "approved_monorepo_roots": list(approved),
                            },
                        )
                    )

    result.candidates.sort(key=lambda item: item.candidate_id.casefold())
    result.rejects.sort(key=lambda item: item.candidate_id.casefold())
    return result


def inspect_duplicate_detection(result: DuplicateDetectionResult) -> dict[str, Any]:
    """Operator summary (derived-only metadata)."""
    return {
        "package_id": PACKAGE_ID,
        "authority_level": AUTHORITY_LEVEL,
        "truth_boundary": TRUTH_BOUNDARY,
        "review_candidates": result.review_count,
        "rejects": result.reject_count,
        "autocollapse": False,
    }


def _safe_vault_relative(vault: Path, relative: str) -> Path:
    normalized = relative.replace("\\", "/").lstrip("/")
    if ".." in PurePosixPath(normalized).parts:
        raise XprojDuplicateError(f"path-escape:{relative}")
    if not any(normalized.startswith(prefix) for prefix in ALLOWED_WRITE_PREFIXES):
        raise XprojDuplicateError(f"write-prefix-forbidden:{relative}")
    if any(normalized.startswith(forbidden) for forbidden in _FORBIDDEN_WRITE_PREFIXES):
        raise XprojDuplicateError(f"write-prefix-forbidden:{relative}")
    target = (vault / normalized).resolve()
    vault_resolved = vault.resolve()
    if not target.is_relative_to(vault_resolved):
        raise XprojDuplicateError(f"path-escape:{relative}")
    return target


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".xproj-dup-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        with contextlib.suppress(OSError):
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)


def write_duplicate_outputs(
    result: DuplicateDetectionResult,
    *,
    vault: Path,
) -> list[str]:
    """Atomically emit review candidates + rejects under duplicate-candidates/."""
    written: list[str] = []
    for candidate in [*result.candidates, *result.rejects]:
        validate_record(candidate.as_dict(), "xproj-duplicate-candidate")
        payload = candidate.as_dict()
        for banned in (
            "winning_choice",
            "winning_project_id",
            "collapsed_uuid",
            "rewritten_project_id",
        ):
            if banned in payload:
                raise XprojDuplicateError(f"banned-emit-field:{banned}")
        if payload.get("autocollapse") is not False:
            raise XprojDuplicateError("autocollapse-must-be-false")
        relative = f"generated/xproj/duplicate-candidates/{_emit_filename(candidate.candidate_id)}"
        target = _safe_vault_relative(vault, relative)
        _write_atomic(target, candidate.to_json())
        written.append(relative.replace("\\", "/"))
    written.sort()
    return written


def promote_duplicate_path_forbidden(relative: str) -> None:
    """Fail closed if a promote plan targets a non-owned path."""
    normalized = relative.replace("\\", "/").lstrip("/")
    if ".." in PurePosixPath(normalized).parts:
        raise XprojDuplicateError(f"path-escape:{relative}")
    if not any(normalized.startswith(prefix) for prefix in ALLOWED_WRITE_PREFIXES):
        raise XprojDuplicateError(f"write-prefix-forbidden:{relative}")
    if any(normalized.startswith(forbidden) for forbidden in _FORBIDDEN_WRITE_PREFIXES):
        raise XprojDuplicateError(f"write-prefix-forbidden:{relative}")


def claims_authority_paths_untouched() -> tuple[str, ...]:
    """Document foreign surfaces this package must never write."""
    return (
        "claims/",
        "state/authoritative-state/",
        "state/current-state/",
        "generated/graph/",
        "generated/xproj/conflicts/",
        "generated/xproj/indexes/",
        "knowledge_compiler",
    )


def both_projects_remain_ingestable(candidate: DuplicateCandidate) -> bool:
    """Invariant helper: review candidates never imply ingest skip / UUID rewrite."""
    payload = candidate.as_dict()
    return (
        candidate.status in {"review_candidate", "reject"}
        and payload.get("autocollapse") is False
        and "rewritten_project_id" not in payload
        and "collapsed_uuid" not in payload
        and "winning_choice" not in payload
    )


__all__ = [
    "ALLOWED_WRITE_PREFIXES",
    "AUTHORITY_LEVEL",
    "PACKAGE_ID",
    "TRUTH_BOUNDARY",
    "DuplicateCandidate",
    "DuplicateDetectionResult",
    "ProjectObservation",
    "XprojDuplicateError",
    "both_projects_remain_ingestable",
    "claims_authority_paths_untouched",
    "detect_project_duplicates",
    "inspect_duplicate_detection",
    "normalize_canonical_remote_url",
    "normalize_root_path",
    "parse_observation",
    "promote_duplicate_path_forbidden",
    "write_duplicate_outputs",
]
