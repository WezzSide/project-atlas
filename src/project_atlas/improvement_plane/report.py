"""Compile and render the AS-IMPR-PLANE-001 improvement report."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from project_atlas.improvement_plane.analyze import (
    analyze_closed_findings,
    analyze_data_quality_risks,
    analyze_evidence_freshness,
    analyze_owner_action_backlog,
    analyze_progress_signals,
    analyze_recurring_failures,
    analyze_waiting_work,
    build_recommendations,
    collect_source_pin,
    recommendations_by_kind,
)
from project_atlas.improvement_plane.readers import (
    build_coverage_report,
    load_evidence_records,
    load_optional_ops_coverage,
)

PACKAGE_ID = "AS-IMPR-PLANE-001"
SCHEMA_ID = "atlas.improvement-plane.report.v1"
GENERATOR_ID = "atlas-impr-plane-001"
GOAL_ID = "ATLAS-EVIDENCE-TO-IMPROVEMENT-20260910"
TRUTH_BOUNDARY = (
    "RECOMMENDATION ≠ AUTHORITY / TELEMETRY ≠ TRUTH CORE / "
    "MISSING_TIMESTAMP ≠ 0 / MISSING_IV ≠ NEVER_VERIFIED / "
    "OPS RECEIPT ≠ COMPLETION / DEMO_EVIDENCE ≠ LIVE_CERTIFICATION"
)


def _git_pin(repo_root: Path) -> dict[str, Any]:
    pin: dict[str, Any] = {
        "head": None,
        "tree": None,
        "status": "unknown",
    }
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
        tree = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return pin
    if head.returncode == 0 and tree.returncode == 0:
        pin["head"] = head.stdout.strip() or None
        pin["tree"] = tree.stdout.strip() or None
        pin["status"] = "present" if pin["head"] and pin["tree"] else "unknown"
    return pin


def _candidate_identity(repo_root: Path, runtime_pin: dict[str, Any]) -> dict[str, Any]:
    branch = None
    try:
        proc = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            branch = proc.stdout.strip() or None
    except OSError:
        branch = None
    return {
        "package_id": PACKAGE_ID,
        "goal_id": GOAL_ID,
        "worktree": str(repo_root),
        "branch": branch,
        "head": runtime_pin.get("head"),
        "tree": runtime_pin.get("tree"),
        "entry_point": "python -m project_atlas.improvement_plane",
        "shared_cli_registered": False,
        "status": {
            "implementation": "present_in_lane",
            "ci": "not_run_for_this_lane",
            "independent_verification": "not_claimed",
            "merge": "not_merged",
        },
    }


def compile_improvement_report(
    repo_root: Path,
    *,
    vault_path: Path | None = None,
    reference_utc: str | None = None,
) -> dict[str, Any]:
    """Compile a read-only improvement report from available evidence."""
    root = repo_root.expanduser().resolve()
    records = load_evidence_records(root)
    coverage = build_coverage_report(records)
    waiting = analyze_waiting_work(records)
    recurring = analyze_recurring_failures(records)
    closed_findings = analyze_closed_findings(records)
    freshness = analyze_evidence_freshness(records, reference_utc=reference_utc)
    owner = analyze_owner_action_backlog(records)
    progress = analyze_progress_signals(records)
    data_quality = analyze_data_quality_risks(records)
    recommendations = build_recommendations(
        waiting=waiting,
        recurring=recurring,
        freshness=freshness,
        owner=owner,
    )
    by_kind = recommendations_by_kind(recommendations)
    ops_coverage = load_optional_ops_coverage(vault_path)
    source_pin = collect_source_pin(records)
    runtime_pin = _git_pin(root)
    candidate = _candidate_identity(root, runtime_pin)

    readable = sum(1 for row in records if row.get("parse_status") == "ok")
    unreadable = sum(
        1
        for row in records
        if row.get("parse_status") not in {"ok", "excluded_self_ingest"}
    )
    excluded = sum(
        1 for row in records if row.get("parse_status") == "excluded_self_ingest"
    )

    return {
        "package_id": PACKAGE_ID,
        "schema": SCHEMA_ID,
        "generator": GENERATOR_ID,
        "goal_id": GOAL_ID,
        "truth_boundary": TRUTH_BOUNDARY,
        "directive": "ATLAS-PARALLEL-IMPROVEMENT-PLANE-20260910",
        "repo_root": str(root),
        "reference_utc": reference_utc,
        "source_pin": source_pin,
        "runtime_git_pin": runtime_pin,
        "candidate_identity": candidate,
        "measures_supported": {
            "observed_queue_age": "supported_when_timestamp_and_reference_present",
            "ci_failure_categories": "supported_via_finding_failure_class_and_hard_counters",
            "validation_coverage": "not_instrumented_in_evidence_json_v1",
            "stale_evidence": "supported_when_timestamp_and_reference_present",
            "owner_action_backlog": "supported",
            "active_engineering_time": "not_instrumented",
        },
        "honesty": {
            "recommendation_ne_authority": True,
            "telemetry_ne_truth_core": True,
            "missing_timestamp_ne_zero": True,
            "missing_iv_ne_never_verified": True,
            "merge_authorized": False,
            "execution_authorized": False,
            "measured_productivity_gain": "not_demonstrated",
            "independent_verification": "not_claimed",
            "ci_status": "not_claimed",
        },
        "coverage": {
            "evidence_files_scanned": len(records),
            "evidence_files_readable": readable,
            "evidence_files_unreadable": unreadable,
            "evidence_files_excluded_self_ingest": excluded,
            "vault_scanned": bool(vault_path),
            "provenance": coverage,
            "deduplication": {
                "duplicate_finding_ids": len(data_quality.get("duplicate_records") or []),
                "decision": (
                    "Duplicates are retained as recurrence evidence and listed under "
                    "data_quality_risks.duplicate_records; they are not dropped silently."
                ),
                "items": list(data_quality.get("duplicate_records") or [])[:20],
            },
            "limits": [
                "Scans docs/evidence/**/*.json only (plus optional vault generated/ops inventory).",
                "Lane-generated AS-IMPR-PLANE reports/outcomes are excluded (self-ingest guard).",
                "Does not read Studio mission/session/claim/recovery state.",
                "Does not call GitHub, CI APIs, or live PR endpoints.",
                "Does not mutate DAG priority, dispatch agents, retry actions, or resolve gates.",
                "Markdown evidence and WORKLOG prose are out of scope for v1.",
                "File mtime is not used as event time.",
                "validation_coverage is not_instrumented in evidence JSON v1.",
                "File count alone is not evidence quality.",
            ],
        },
        "panels": {
            "waiting_work": waiting,
            "recurring_failures": recurring,
            "closed_findings": closed_findings,
            "evidence_freshness": freshness,
            "owner_action_backlog": owner,
            "progress_signals": progress,
            "data_quality_risks": data_quality,
            "ops_receipt_coverage": ops_coverage,
        },
        "recommendations": recommendations,
        "recommendations_by_kind": by_kind,
        "optional_adoption_proposal": {
            "summary": (
                "Optional later: thin `atlas impr report` CLI wrapper calling "
                "compile_improvement_report without changing this module's contracts."
            ),
            "requires_shared_cli_change": True,
            "required_now": False,
            "blocked_by_this_lane": False,
        },
    }


def render_markdown_summary(report: dict[str, Any]) -> str:
    """Render a concise operator-facing Markdown summary."""
    lines: list[str] = [
        f"# {PACKAGE_ID} — Delivery evidence improvement report",
        "",
        f"Truth boundary: `{report.get('truth_boundary')}`",
        "",
        "## Honesty",
        "",
        "- RECOMMENDATION ≠ AUTHORITY",
        "- Missing timestamps are unknown, not zero waiting time",
        "- Missing IV receipt ≠ verification never happened",
        "- No merge, dispatch, retry, or productivity-gain claim",
        "",
        "## Coverage",
        "",
    ]
    coverage = report.get("coverage") or {}
    lines.append(
        f"- Evidence JSON scanned: {coverage.get('evidence_files_scanned', 0)} "
        f"(readable {coverage.get('evidence_files_readable', 0)}, "
        f"unreadable {coverage.get('evidence_files_unreadable', 0)})"
    )
    for limit in coverage.get("limits") or []:
        lines.append(f"- Limit: {limit}")

    pin = report.get("source_pin") or {}
    candidate = report.get("candidate_identity") or {}
    runtime = report.get("runtime_git_pin") or {}
    lines.extend(
        [
            "",
            "## Identities",
            "",
            f"- Source pin head/tree: `{pin.get('head')}` / `{pin.get('tree')}`",
            f"- Candidate branch: `{candidate.get('branch')}`",
            f"- Candidate worktree: `{candidate.get('worktree')}`",
            f"- Runtime head/tree: `{runtime.get('head')}` / `{runtime.get('tree')}`",
            f"- Status: `{candidate.get('status')}`",
        ]
    )

    measures = report.get("measures_supported") or {}
    if measures:
        lines.extend(["", "## Measures supported", ""])
        for key, value in measures.items():
            lines.append(f"- {key}: `{value}`")

    waiting = (report.get("panels") or {}).get("waiting_work") or {}
    lines.extend(["", "## Waiting work", "", f"- Count: {waiting.get('count', 0)}"])
    for item in (waiting.get("items") or [])[:8]:
        lines.append(
            f"- [{item.get('classification')}] {item.get('id')}: "
            f"{item.get('summary')} _(source: {item.get('source')})_"
        )

    recurring = (report.get("panels") or {}).get("recurring_failures") or {}
    lines.extend(["", "## Recurring failures", "", f"- Count: {recurring.get('count', 0)}"])
    for item in (recurring.get("items") or [])[:8]:
        lines.append(
            f"- `{item.get('finding_id')}` class={item.get('failure_class')} "
            f"open={item.get('open_occurrences')} sources={item.get('source_count')}"
        )

    freshness = (report.get("panels") or {}).get("evidence_freshness") or {}
    lines.extend(
        [
            "",
            "## Evidence freshness",
            "",
            f"- With timestamp: {freshness.get('with_timestamp', 0)}",
            f"- Timestamp unknown: {freshness.get('timestamp_unknown', 0)}",
            f"- Reference: {freshness.get('reference_utc') or 'unknown'}",
        ]
    )

    owner = (report.get("panels") or {}).get("owner_action_backlog") or {}
    lines.extend(["", "## Owner decisions", "", f"- Count: {owner.get('count', 0)}"])
    for item in (owner.get("items") or [])[:8]:
        action = item.get("next_required_owner_action") or item.get("description")
        lines.append(
            f"- {item.get('node_id')}: {action} "
            f"(dependents={item.get('dependent_count', 0)}; "
            f"source={item.get('source')})"
        )

    quality = (report.get("panels") or {}).get("data_quality_risks") or {}
    lines.extend(
        [
            "",
            "## Data-quality risks",
            "",
            f"- Contradictory status rows: {len(quality.get('contradictory_status') or [])}",
            f"- Duplicate-record finding ids: {len(quality.get('duplicate_records') or [])}",
            f"- Incorrect attribution: "
            f"{(quality.get('incorrect_attribution') or {}).get('status')}",
        ]
    )

    lines.extend(["", "## Ranked recommendations", ""])
    for rec in report.get("recommendations") or []:
        lines.extend(
            [
                f"### {rec.get('rank')}. {rec.get('title')}",
                "",
                f"- Observed problem: {rec.get('observed_problem')}",
                f"- Proposed action: {rec.get('proposed_action')}",
                f"- Required actor: {rec.get('required_actor')}",
                f"- Dependency: {rec.get('dependency')}",
                f"- Scope: {rec.get('scope')}",
                f"- Ranking rationale: {rec.get('ranking_rationale')}",
                f"- Sources: {', '.join(rec.get('source_records') or [])}",
                f"- Uncertainty: {rec.get('uncertainty')}",
                f"- Authority: {rec.get('authority')} (does not dispatch or resolve gates)",
                "",
            ]
        )

    ops = (report.get("panels") or {}).get("ops_receipt_coverage") or {}
    lines.extend(
        [
            "## Ops receipt coverage (optional)",
            "",
            f"- Vault provided: {ops.get('vault_provided')}",
            f"- Note: {ops.get('note')}",
            "",
            "## Optional adoption proposal",
            "",
            f"- {(report.get('optional_adoption_proposal') or {}).get('summary')}",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_report_files(
    report: dict[str, Any],
    *,
    output_json: Path | None,
    output_md: Path | None,
) -> None:
    """Write report artifacts atomically when output paths are provided."""
    if output_json is not None:
        path = output_json.expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
    if output_md is not None:
        path = output_md.expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(render_markdown_summary(report), encoding="utf-8")
        tmp.replace(path)
