"""Evaluate recorded outcomes against a later observation report."""

from __future__ import annotations

from typing import Any

from project_atlas.improvement_plane.compare import _closed_finding_map, _observation_map
from project_atlas.improvement_plane.quality import path_continuous_closure


def render_evaluate_summary(result: dict[str, Any]) -> str:
    lines = [
        "# AS-IMPR-PLANE-001 — Outcome evaluation",
        "",
        f"- Sample size: {result.get('sample_size')}",
        f"- Counts: {result.get('counts')}",
        "",
        "Honesty: association ≠ causation; annotations ≠ certification; "
        "planted CLOSED on a new path is inconclusive.",
        "",
        "## Evaluations",
        "",
    ]
    for row in result.get("evaluations") or []:
        lines.append(
            f"- `{row.get('recommendation_id')}` → **{row.get('result')}** "
            f"(outcome={row.get('outcome_status')}): {row.get('detail')}"
        )
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def evaluate_outcomes(
    *,
    before_report: dict[str, Any],
    after_report: dict[str, Any],
    outcomes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare annotations with later observations.

    Distinguishes improved / persisted / regressed / inconclusive.
    A local annotation cannot certify itself as improvement.
    """
    before = _observation_map(before_report)
    after = _observation_map(after_report)
    closed_after = _closed_finding_map(after_report)
    before_accepted = int(
        ((before_report.get("coverage") or {}).get("provenance") or {}).get(
            "accepted_count"
        )
        or 0
    )
    after_accepted = int(
        ((after_report.get("coverage") or {}).get("provenance") or {}).get(
            "accepted_count"
        )
        or 0
    )
    coverage_reduced = (
        before_accepted > 0 and after_accepted > 0 and after_accepted < before_accepted
    )

    latest: dict[str, dict[str, Any]] = {}
    for row in outcomes:
        rid = row.get("recommendation_id")
        if isinstance(rid, str) and rid.strip():
            latest[rid] = row

    evaluations: list[dict[str, Any]] = []
    for rid, outcome in sorted(latest.items()):
        status = outcome.get("status")
        obs_id = rid[4:] if rid.startswith("rec:") else rid
        before_obs = before.get(obs_id)
        after_obs = after.get(obs_id)
        closed = closed_after.get(obs_id)
        continuous = bool(
            before_obs
            and closed
            and path_continuous_closure(
                before_sources=list(before_obs.get("sources") or []),
                closed_sources=list(closed.get("sources") or []),
            )
        )

        if before_obs is None and after_obs is None:
            result = "inconclusive"
            detail = "Observation id not present in before or after open panels."
        elif before_obs is not None and after_obs is None:
            if continuous:
                result = "improved"
                detail = (
                    "Open observation gone with path-continuous CLOSED evidence; "
                    "association only. Outcome annotation is not the certifier."
                )
            elif closed:
                result = "inconclusive"
                detail = (
                    "CLOSED evidence exists only on new/unrelated paths "
                    "(claimed_closure). Operator annotation cannot upgrade this "
                    "to improved."
                )
            else:
                result = "inconclusive"
                detail = (
                    "Observation unobservable in after snapshot; disappearance is not "
                    "proof of resolution even when annotated completed."
                )
                if coverage_reduced:
                    detail += " After coverage is reduced versus before."
        elif before_obs is None and after_obs is not None:
            result = "regressed"
            detail = "Observation absent before but present after (new/reopened)."
        else:
            assert before_obs is not None
            assert after_obs is not None
            before_occ = before_obs.get("open_occurrences")
            after_occ = after_obs.get("open_occurrences")
            if (
                isinstance(before_occ, int)
                and isinstance(after_occ, int)
                and after_occ < before_occ
            ):
                result = "improved"
                detail = (
                    f"Open occurrences decreased {before_occ}→{after_occ} while still "
                    "present; association only."
                )
            elif (
                isinstance(before_occ, int)
                and isinstance(after_occ, int)
                and after_occ > before_occ
            ):
                result = "regressed"
                detail = (
                    f"Open occurrences increased {before_occ}→{after_occ}; "
                    "association only."
                )
            elif status in {"completed", "accepted"}:
                result = "persisted"
                detail = (
                    f"Annotated as {status}, but observation remains present after; "
                    "annotation is not certification."
                )
            elif status == "deferred":
                result = "persisted"
                detail = "Deferred annotation; observation still tracked."
            elif status == "attempted":
                result = "persisted"
                detail = "Attempted annotation with observation still present afterward."
            else:
                result = "inconclusive"
                detail = f"Unhandled outcome status {status!r}."

        evaluations.append(
            {
                "recommendation_id": rid,
                "outcome_status": status,
                "observation_id": obs_id,
                "result": result,
                "detail": detail,
                "evidence_refs": list(outcome.get("evidence_refs") or []),
                "before_present": before_obs is not None,
                "after_present": after_obs is not None,
                "closed_evidence_present": closed is not None,
                "path_continuous_closure": continuous,
                "annotation_certified_resolution": False,
            }
        )

    counts = {
        "improved": 0,
        "persisted": 0,
        "regressed": 0,
        "inconclusive": 0,
    }
    for row in evaluations:
        key = str(row["result"])
        if key in counts:
            counts[key] += 1

    return {
        "schema": "atlas.improvement-plane.evaluation.v1",
        "package_id": "AS-IMPR-PLANE-001",
        "sample_size": len(evaluations),
        "counts": counts,
        "coverage_reduced": coverage_reduced,
        "evaluations": evaluations,
        "honesty": {
            "association_ne_causation": True,
            "time_saved_claimed": False,
            "disappearing_ne_resolved": True,
            "annotation_ne_certification": True,
            "planted_closure_ne_improved": True,
            "authority": "none",
            "dag_gate_resolved": False,
        },
        "note": (
            "Evaluation reports association only. Local outcome annotations never "
            "certify resolution. Path-continuous CLOSED evidence is required before "
            "an absent observation may be classified improved."
        ),
    }
