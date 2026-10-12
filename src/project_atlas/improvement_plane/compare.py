"""Compare two improvement-plane report snapshots."""

from __future__ import annotations

from typing import Any

from project_atlas.improvement_plane.errors import ImprovementPlaneError
from project_atlas.improvement_plane.quality import (
    coverage_fingerprint,
    path_continuous_closure,
)


def _snapshot_compat(report: object, *, label: str) -> dict[str, Any]:
    """Return compatibility assessment for a compare input."""
    if not isinstance(report, dict):
        return {
            "label": label,
            "status": "incompatible",
            "reason": "not-a-json-object",
        }
    schema = report.get("schema")
    panels = report.get("panels")
    if not isinstance(panels, dict):
        return {
            "label": label,
            "status": "incomplete",
            "reason": "missing-panels-object",
            "schema": schema,
        }
    required = (
        "owner_action_backlog",
        "recurring_failures",
        "waiting_work",
    )
    missing = [name for name in required if name not in panels]
    if missing:
        return {
            "label": label,
            "status": "incomplete",
            "reason": "missing-required-panels",
            "missing_panels": missing,
            "schema": schema,
        }
    if (
        isinstance(schema, str)
        and schema.startswith("atlas.")
        and "improvement-plane.report" not in schema
    ):
        return {
            "label": label,
            "status": "incompatible",
            "reason": "foreign-schema",
            "schema": schema,
        }
    return {"label": label, "status": "ok", "schema": schema}


def _observation_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Extract stable observation IDs from a compiled report."""
    panels = report.get("panels") or {}
    observations: dict[str, dict[str, Any]] = {}

    for item in (panels.get("owner_action_backlog") or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        oid = f"owner:{item.get('node_id')}"
        observations[oid] = {
            "id": oid,
            "kind": "owner_decision",
            "summary": item.get("description") or item.get("next_required_owner_action"),
            "sources": [item.get("source")],
            "status": "open",
        }

    for item in (panels.get("recurring_failures") or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        oid = f"finding:{item.get('finding_id')}"
        observations[oid] = {
            "id": oid,
            "kind": "engineering_failure",
            "summary": f"{item.get('finding_id')} class={item.get('failure_class')}",
            "sources": list(item.get("sources") or []),
            "status": "open",
            "failure_class": item.get("failure_class"),
            "open_occurrences": item.get("open_occurrences"),
        }

    for item in (panels.get("waiting_work") or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        classification = item.get("classification")
        if classification in {"BLOCKED_EXTERNAL"}:
            oid = f"external:{item.get('id')}"
            kind = "external_dependency"
        elif classification in {"READY", "DERIVABLE"}:
            oid = f"queue:{classification}:{item.get('id')}"
            kind = "queued_opportunity"
        elif classification in {"BLOCKED_BY_OWNER", "WAITING_OWNER"}:
            oid = f"waiting:{classification}:{item.get('id')}"
            kind = "owner_decision"
        else:
            oid = f"waiting:{classification}:{item.get('id')}"
            kind = "waiting"
        observations[oid] = {
            "id": oid,
            "kind": kind,
            "summary": item.get("summary") or classification,
            "sources": [item.get("source")],
            "status": "open",
            "classification": classification,
        }

    for item in (panels.get("data_quality_risks") or {}).get("contradictory_status") or []:
        if not isinstance(item, dict):
            continue
        oid = f"quality:contradictory:{item.get('finding_id')}"
        observations[oid] = {
            "id": oid,
            "kind": "data_quality",
            "summary": f"contradictory statuses for {item.get('finding_id')}",
            "sources": list(item.get("sources") or []),
            "status": "open",
        }

    for item in (panels.get("progress_signals") or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        oid = str(item.get("id") or "")
        if not oid:
            continue
        observations[oid] = {
            "id": oid,
            "kind": str(item.get("kind") or "progress"),
            "summary": str(item.get("summary") or ""),
            "sources": [item.get("source")],
            "status": str(item.get("status") or "evidence_progress"),
        }

    return observations


def _closed_finding_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    panels = report.get("panels") or {}
    closed: dict[str, dict[str, Any]] = {}
    for item in (panels.get("closed_findings") or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        fid = item.get("finding_id")
        if not fid:
            continue
        oid = f"finding:{fid}"
        closed[oid] = {
            "id": oid,
            "sources": list(item.get("sources") or []),
            "statuses": list(item.get("statuses") or []),
        }
    return closed


def _fingerprint(obs: dict[str, Any]) -> tuple[Any, ...]:
    """Identity-relevant fingerprint; ignores timestamps and ordering noise."""
    return (
        obs.get("kind"),
        obs.get("failure_class"),
        obs.get("classification"),
        obs.get("summary"),
        obs.get("open_occurrences"),
    )


def render_compare_summary(result: dict[str, Any]) -> str:
    lines = [
        "# AS-IMPR-PLANE-001 — Snapshot compare",
        "",
        f"- Before: `{result.get('before_label')}`",
        f"- After: `{result.get('after_label')}`",
        f"- Comparison status: `{result.get('comparison_status')}`",
        f"- Coverage reduced: `{result.get('coverage_reduced')}`",
        f"- Counts: {result.get('counts')}",
        "",
        "Honesty: planted CLOSED on a new path is claimed_closure, not resolved.",
        "Disappearance alone is unobservable. Annotations cannot certify resolution.",
        "",
    ]
    if result.get("comparison_status") != "ok":
        lines.append(f"Issue: {result.get('comparison_issue')}")
        return "\n".join(lines) + "\n"
    for label in (
        "resolved",
        "claimed_closure",
        "new",
        "changed",
        "persistent",
        "unobservable",
    ):
        rows = result.get(label) or []
        lines.append(f"## {label} ({len(rows)})")
        for row in rows[:8]:
            lines.append(f"- `{row.get('id')}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def compare_reports(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    before_label: str,
    after_label: str,
) -> dict[str, Any]:
    """Compare two explicit snapshots with path-continuous resolution rules."""
    before_compat = _snapshot_compat(before, label=before_label)
    after_compat = _snapshot_compat(after, label=after_label)
    statuses = {before_compat["status"], after_compat["status"]}
    empty_counts = {
        "new": 0,
        "persistent": 0,
        "changed": 0,
        "unobservable": 0,
        "resolved": 0,
        "claimed_closure": 0,
    }
    honesty = {
        "disappearing_ne_resolved": True,
        "annotation_ne_resolution": True,
        "planted_closure_ne_resolved": True,
        "coverage_reduction_ne_improvement": True,
        "ordering_insensitive": True,
        "timestamp_noise_ignored_for_identity": True,
        "authority": "none",
    }
    if "incompatible" in statuses:
        return {
            "schema": "atlas.improvement-plane.compare.v1",
            "package_id": "AS-IMPR-PLANE-001",
            "before_label": before_label,
            "after_label": after_label,
            "comparison_status": "incompatible",
            "comparison_issue": {"before": before_compat, "after": after_compat},
            "coverage_reduced": None,
            "counts": empty_counts,
            "new": [],
            "persistent": [],
            "changed": [],
            "unobservable": [],
            "resolved": [],
            "claimed_closure": [],
            "honesty": honesty,
            "note": "Comparison refused: one or both inputs are incompatible.",
        }
    if "incomplete" in statuses:
        return {
            "schema": "atlas.improvement-plane.compare.v1",
            "package_id": "AS-IMPR-PLANE-001",
            "before_label": before_label,
            "after_label": after_label,
            "comparison_status": "incomplete",
            "comparison_issue": {"before": before_compat, "after": after_compat},
            "coverage_reduced": None,
            "counts": empty_counts,
            "new": [],
            "persistent": [],
            "changed": [],
            "unobservable": [],
            "resolved": [],
            "claimed_closure": [],
            "honesty": honesty,
            "note": "Comparison incomplete: required panels missing on one or both inputs.",
        }

    before_fp = coverage_fingerprint(before)
    after_fp = coverage_fingerprint(after)
    coverage_reduced = after_fp["accepted_count"] < before_fp["accepted_count"]
    repo_mismatch = (
        before.get("repo_root")
        and after.get("repo_root")
        and before.get("repo_root") != after.get("repo_root")
    )
    reference_mismatch = (
        before.get("reference_utc")
        and after.get("reference_utc")
        and before.get("reference_utc") != after.get("reference_utc")
    )
    schema_mismatch = (
        before.get("schema")
        and after.get("schema")
        and before.get("schema") != after.get("schema")
    )

    left = _observation_map(before)
    right = _observation_map(after)
    closed_after = _closed_finding_map(after)

    new_ids = sorted(set(right) - set(left))
    gone_ids = sorted(set(left) - set(right))
    shared = sorted(set(left) & set(right))

    persistent: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    for oid in shared:
        a = left[oid]
        b = right[oid]
        if _fingerprint(a) == _fingerprint(b):
            persistent.append({"id": oid, "before": a, "after": b})
        else:
            changed.append({"id": oid, "before": a, "after": b})

    resolved: list[dict[str, Any]] = []
    claimed_closure: list[dict[str, Any]] = []
    unobservable: list[dict[str, Any]] = []
    for oid in gone_ids:
        closed = closed_after.get(oid)
        if closed and path_continuous_closure(
            before_sources=list(left[oid].get("sources") or []),
            closed_sources=list(closed.get("sources") or []),
        ):
            resolved.append(
                {
                    "id": oid,
                    "before": left[oid],
                    "disposition": "resolved",
                    "trust": "path_continuous_closure",
                    "resolution_evidence": closed,
                    "note": (
                        "Resolved: CLOSED evidence overlaps a before-open source path. "
                        "This is observed continuity, not an outcome annotation."
                    ),
                }
            )
        elif closed:
            claimed_closure.append(
                {
                    "id": oid,
                    "before": left[oid],
                    "disposition": "claimed_closure",
                    "trust": "operator_or_new_path_assertion",
                    "resolution_evidence": closed,
                    "note": (
                        "CLOSED appears only on paths not present in the before-open "
                        "sources. Treated as unproven claim, not resolved improvement."
                    ),
                }
            )
        else:
            note = (
                "Observation absent in after snapshot; disappearing evidence is not "
                "proof the blocker was resolved."
            )
            if coverage_reduced:
                note += " After snapshot also has reduced accepted coverage."
            unobservable.append(
                {
                    "id": oid,
                    "before": left[oid],
                    "disposition": "unobservable",
                    "note": note,
                }
            )

    return {
        "schema": "atlas.improvement-plane.compare.v1",
        "package_id": "AS-IMPR-PLANE-001",
        "before_label": before_label,
        "after_label": after_label,
        "comparison_status": "ok",
        "comparison_issue": (
            {
                "repo_mismatch": bool(repo_mismatch),
                "reference_utc_mismatch": bool(reference_mismatch),
                "schema_mismatch": bool(schema_mismatch),
            }
            if (repo_mismatch or reference_mismatch or schema_mismatch)
            else None
        ),
        "comparability": {
            "repo_mismatch": bool(repo_mismatch),
            "reference_utc_mismatch": bool(reference_mismatch),
            "schema_mismatch": bool(schema_mismatch),
            "coverage_reduced": coverage_reduced,
            "uncertain": bool(
                repo_mismatch or reference_mismatch or schema_mismatch or coverage_reduced
            ),
        },
        "coverage": {"before": before_fp, "after": after_fp},
        "coverage_reduced": coverage_reduced,
        "counts": {
            "new": len(new_ids),
            "persistent": len(persistent),
            "changed": len(changed),
            "unobservable": len(unobservable),
            "resolved": len(resolved),
            "claimed_closure": len(claimed_closure),
        },
        "new": [{"id": oid, "after": right[oid]} for oid in new_ids],
        "persistent": persistent,
        "changed": changed,
        "unobservable": unobservable,
        "resolved": resolved,
        "claimed_closure": claimed_closure,
        "honesty": honesty,
        "note": (
            "resolved requires path-continuous CLOSED evidence; planted CLOSED on a "
            "new path is claimed_closure; reduced coverage cannot be treated as improvement."
        ),
    }


def require_compareable_reports(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    before_label: str,
    after_label: str,
) -> None:
    """Raise operator-facing error for hard-incompatible compare inputs."""
    result = compare_reports(
        before, after, before_label=before_label, after_label=after_label
    )
    if result["comparison_status"] == "incompatible":
        raise ImprovementPlaneError(
            "compare-incompatible",
            f"Cannot compare incompatible snapshots: {before_label} vs {after_label}",
        )
