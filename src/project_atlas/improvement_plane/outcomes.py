"""Local recommendation outcome annotations (non-authoritative)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal

from project_atlas.improvement_plane.errors import ImprovementPlaneError
from project_atlas.improvement_plane.readers import is_self_ingest_path

OutcomeStatus = Literal["accepted", "deferred", "attempted", "completed"]

OUTCOME_STATUSES: frozenset[str] = frozenset(
    {"accepted", "deferred", "attempted", "completed"}
)
_REC_ID_RE = re.compile(r"^[A-Za-z0-9_.:/=+-]{1,128}$")

DEFAULT_OUTCOMES_REL = Path(".atlas") / "improvement-plane" / "outcomes.jsonl"
MAX_OUTCOMES_BYTES = 5_000_000
SINGLE_WRITER_NOTE = (
    "Supported usage is single-writer. Concurrent writers are not coordinated "
    "across hosts; local advisory locking is best-effort only."
)


def outcomes_path(repo_root: Path, explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    return (repo_root.expanduser().resolve() / DEFAULT_OUTCOMES_REL).resolve()


def _lock_file(handle: Any) -> None:
    try:
        import fcntl
    except ImportError:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except OSError:
        # Best-effort only; limitation documented in SINGLE_WRITER_NOTE.
        return


def _unlock_file(handle: Any) -> None:
    try:
        import fcntl
    except ImportError:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        return


def _write_atomic_append(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        _lock_file(handle)
        try:
            handle.seek(0, os.SEEK_END)
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            _unlock_file(handle)


def _next_sequence(existing: list[dict[str, Any]]) -> int:
    seq = 0
    for row in existing:
        value = row.get("sequence")
        if isinstance(value, int) and value > seq:
            seq = value
    return seq + 1


def record_outcome(
    repo_root: Path,
    *,
    recommendation_id: str,
    status: str,
    evidence_refs: list[str],
    note: str | None = None,
    report_path: str | None = None,
    outcomes_file: Path | None = None,
) -> dict[str, Any]:
    """Record a local outcome annotation. Never resolves DAG gates."""
    if status not in OUTCOME_STATUSES:
        raise ImprovementPlaneError(
            "invalid-outcome-status",
            f"status must be one of {sorted(OUTCOME_STATUSES)}; got {status!r}",
        )
    if not _REC_ID_RE.fullmatch(recommendation_id):
        raise ImprovementPlaneError(
            "invalid-recommendation-id",
            "recommendation_id must be 1..128 safe characters",
        )
    if not evidence_refs:
        raise ImprovementPlaneError(
            "missing-evidence-refs",
            "At least one evidence_refs entry is required",
        )
    for ref in evidence_refs:
        if not isinstance(ref, str) or not ref.strip():
            raise ImprovementPlaneError(
                "invalid-evidence-ref",
                "evidence_refs entries must be non-empty strings",
            )
        normalized = ref.replace("\\", "/")
        if (
            is_self_ingest_path(normalized)
            or normalized.startswith(".atlas/improvement-plane/")
            or "/improvement-plane/outcomes" in normalized
        ):
            raise ImprovementPlaneError(
                "invalid-evidence-ref-self",
                "evidence_refs must not point at outcome journals or lane-generated reports",
            )

    path = outcomes_path(repo_root, outcomes_file)
    if path.is_file() and path.stat().st_size > MAX_OUTCOMES_BYTES:
        raise ImprovementPlaneError(
            "outcomes-store-too-large",
            f"Outcomes store exceeds {MAX_OUTCOMES_BYTES} byte bound: {path}",
        )

    existing = load_outcomes(repo_root, outcomes_file=outcomes_file)
    sequence = _next_sequence(existing)
    payload = {
        "schema": "atlas.improvement-plane.outcome.v1",
        "package_id": "AS-IMPR-PLANE-001",
        "sequence": sequence,
        "recommendation_id": recommendation_id,
        "status": status,
        "evidence_refs": list(evidence_refs),
        "note": note,
        "report_path": report_path,
        "authority": "none",
        "dag_gate_resolved": False,
        "certifies_resolution": False,
        "writer_model": "single_writer_best_effort_lock",
        "truth_boundary": (
            "OUTCOME ANNOTATION ≠ AUTHORITY / ANNOTATION ≠ GATE RESOLUTION / "
            "ANNOTATION ≠ SOURCE EVIDENCE / ANNOTATION ≠ CERTIFIED IMPROVEMENT"
        ),
    }
    _write_atomic_append(path, json.dumps(payload, sort_keys=True) + "\n")
    return {
        "ok": True,
        "path": str(path),
        "outcome": payload,
        "writer_limitation": SINGLE_WRITER_NOTE,
    }


def load_outcomes(
    repo_root: Path,
    *,
    outcomes_file: Path | None = None,
) -> list[dict[str, Any]]:
    path = outcomes_path(repo_root, outcomes_file)
    if not path.exists():
        return []
    if not path.is_file():
        raise ImprovementPlaneError(
            "outcomes-store-invalid",
            f"Outcomes path is not a file: {path}",
        )
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise ImprovementPlaneError(
            "outcomes-store-unreadable",
            f"Cannot read outcomes store: {path}",
        ) from None
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            raise ImprovementPlaneError(
                "outcomes-store-corrupt",
                f"Corrupt JSONL at {path}:{line_no}",
            ) from None
        if not isinstance(raw, dict):
            raise ImprovementPlaneError(
                "outcomes-store-corrupt",
                f"Non-object JSONL row at {path}:{line_no}",
            )
        rows.append(raw)
    return rows
