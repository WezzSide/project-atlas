"""Decision-quality helpers: closure trust, coverage fingerprints, ranking caps."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def coverage_fingerprint(report: dict[str, Any]) -> dict[str, Any]:
    """Derive a comparable coverage fingerprint from a compiled report."""
    provenance = ((report.get("coverage") or {}).get("provenance") or {})
    accepted = provenance.get("accepted") or []
    paths = sorted(
        str(row.get("path"))
        for row in accepted
        if isinstance(row, dict) and row.get("path")
    )
    digest = hashlib.sha256(
        json.dumps(paths, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    return {
        "accepted_count": int(provenance.get("accepted_count") or len(paths)),
        "accepted_paths": paths,
        "accepted_paths_sha256": digest,
        "schema": report.get("schema"),
        "package_id": report.get("package_id"),
        "repo_root": report.get("repo_root"),
    }


def path_continuous_closure(
    *,
    before_sources: list[Any] | None,
    closed_sources: list[Any] | None,
) -> bool:
    """True when closed evidence overlaps a before-open source path."""
    before_set = {str(p) for p in (before_sources or []) if p}
    closed_set = {str(p) for p in (closed_sources or []) if p}
    return bool(before_set & closed_set)


def capped_occurrence_score_inputs(
    *,
    open_occurrences: int,
    source_count: int,
) -> tuple[int, int, str]:
    """Prevent duplicate entries in one corpus from inflating rank.

    Ranking uses unique sources primarily; raw occurrences are capped at
    ``2 * source_count`` so repeated rows in one file cannot dominate.
    """
    sources = max(0, int(source_count))
    raw = max(0, int(open_occurrences))
    capped = min(raw, max(sources, 1) * 2) if sources or raw else 0
    note = (
        f"rank_inputs open_occurrences_raw={raw} open_occurrences_capped={capped} "
        f"unique_sources={sources}"
    )
    return capped, sources, note
