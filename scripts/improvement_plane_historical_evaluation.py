#!/usr/bin/env python3
"""Run historical transition evaluation for AS-IMPR-PLANE-001.

This harness replays authentic packet revisions from git objects into isolated
single-packet snapshots, runs the frozen Improvement Plane report/compare flow,
and grades outcomes against predeclared expectations.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from project_atlas.improvement_plane.compare import compare_reports
from project_atlas.improvement_plane.report import compile_improvement_report


@dataclass(frozen=True)
class Episode:
    episode_id: str
    split: str
    category: str
    source_file: str
    before_commit: str
    after_commit: str
    source_change_type: str
    expected_signal: str
    expected_rationale: str
    acceptable_alternatives: list[str]


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True)


def _git_try_show_file(repo: Path, commit: str, relpath: str) -> str | None:
    probe = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}:{relpath}"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        return None
    return _git(repo, "show", f"{commit}:{relpath}")


def _commit_ts(repo: Path, commit: str) -> str:
    return _git(repo, "show", "-s", "--format=%cI", commit).strip()


def _materialize_snapshot(
    *,
    repo: Path,
    episode: Episode,
    commit: str,
    target_dir: Path,
) -> dict[str, Any]:
    snapshot_root = target_dir
    snapshot_root.mkdir(parents=True, exist_ok=True)
    docs_evidence = snapshot_root / "docs" / "evidence"
    docs_evidence.mkdir(parents=True, exist_ok=True)
    payload = _git_try_show_file(repo, commit, episode.source_file)
    source_present = payload is not None
    if source_present:
        out = snapshot_root / episode.source_file
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload, encoding="utf-8")
    return {
        "source_present": source_present,
        "snapshot_root": str(snapshot_root),
    }


def _run_episode(repo: Path, workspace: Path, episode: Episode) -> dict[str, Any]:
    episode_root = workspace / episode.episode_id
    before_dir = episode_root / "before"
    after_dir = episode_root / "after"
    before_meta = _materialize_snapshot(
        repo=repo,
        episode=episode,
        commit=episode.before_commit,
        target_dir=before_dir,
    )
    after_meta = _materialize_snapshot(
        repo=repo,
        episode=episode,
        commit=episode.after_commit,
        target_dir=after_dir,
    )

    before_report = compile_improvement_report(before_dir)
    after_report = compile_improvement_report(after_dir)
    compare = compare_reports(
        before_report,
        after_report,
        before_label=f"{episode.episode_id}:before:{episode.before_commit[:8]}",
        after_label=f"{episode.episode_id}:after:{episode.after_commit[:8]}",
    )
    counts = compare.get("counts") or {}
    delta_total = int(counts.get("new") or 0) + int(counts.get("changed") or 0) + int(
        counts.get("resolved") or 0
    ) + int(counts.get("claimed_closure") or 0) + int(counts.get("unobservable") or 0)
    actual_signal = "delta_present" if delta_total > 0 else "no_material_delta"

    verdict = "match"
    discrepancy_kind = None
    if episode.expected_signal != actual_signal:
        verdict = "mismatch"
        if episode.expected_signal == "delta_present" and actual_signal == "no_material_delta":
            discrepancy_kind = "missed_progress_or_insensitive"
        elif episode.expected_signal == "no_material_delta" and actual_signal == "delta_present":
            discrepancy_kind = "unexpected_or_false_signal"
        else:
            discrepancy_kind = "classification_mismatch"

    if int(counts.get("resolved") or 0) > 0 and episode.expected_signal == "no_material_delta":
        if discrepancy_kind is None:
            discrepancy_kind = "false_resolution"
        verdict = "mismatch"

    if compare.get("comparison_status") != "ok":
        verdict = "mismatch"
        discrepancy_kind = discrepancy_kind or "compare_not_ok"

    def _dupes(report: dict[str, Any]) -> list[str]:
        ids = [
            str(row.get("recommendation_id"))
            for row in (report.get("recommendations") or [])
            if row.get("recommendation_id")
        ]
        c = Counter(ids)
        return sorted([rid for rid, n in c.items() if n > 1])

    return {
        "episode_id": episode.episode_id,
        "split": episode.split,
        "category": episode.category,
        "source_file": episode.source_file,
        "source_change_type": episode.source_change_type,
        "before_commit": episode.before_commit,
        "before_commit_ts": _commit_ts(repo, episode.before_commit),
        "after_commit": episode.after_commit,
        "after_commit_ts": _commit_ts(repo, episode.after_commit),
        "before_source_present": before_meta["source_present"],
        "after_source_present": after_meta["source_present"],
        "expected": {
            "signal": episode.expected_signal,
            "rationale": episode.expected_rationale,
            "acceptable_alternatives": episode.acceptable_alternatives,
        },
        "actual": {
            "signal": actual_signal,
            "comparison_status": compare.get("comparison_status"),
            "coverage_reduced": compare.get("coverage_reduced"),
            "counts": counts,
            "resolved_ids": [row.get("id") for row in (compare.get("resolved") or [])],
            "claimed_closure_ids": [
                row.get("id") for row in (compare.get("claimed_closure") or [])
            ],
            "uncertain": bool((compare.get("comparability") or {}).get("uncertain")),
            "duplicate_recommendation_ids_before": _dupes(before_report),
            "duplicate_recommendation_ids_after": _dupes(after_report),
        },
        "discrepancy_kind": discrepancy_kind,
        "verdict": verdict,
    }


def _render_markdown(payload: dict[str, Any]) -> str:
    rows = payload["episodes"]
    total = len(rows)
    matches = sum(1 for r in rows if r["verdict"] == "match")
    mismatches = total - matches
    held_out = [r for r in rows if r["split"] == "held_out"]
    dev = [r for r in rows if r["split"] != "held_out"]

    lines = [
        "# AS-IMPR-PLANE-001 Historical Evaluation 001",
        "",
        (
            f"- Frozen implementation subject: "
            f"`{payload['implementation_pin']['head']}` / "
            f"`{payload['implementation_pin']['tree']}`"
        ),
        f"- Corpus episodes: **{total}** (development {len(dev)}, held-out {len(held_out)})",
        f"- Match / mismatch: **{matches} / {mismatches}**",
        "",
        "## Results table",
        "",
        "| Episode | Split | Category | Expected | Actual | Verdict | Discrepancy |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['episode_id']} | {row['split']} | {row['category']} | "
            f"{row['expected']['signal']} | {row['actual']['signal']} | "
            f"{row['verdict']} | {row.get('discrepancy_kind') or '-'} |"
        )

    lines.extend(
        [
            "",
            "## Counts",
            "",
            f"- False resolution: {payload['counts']['false_resolution']}",
            f"- Missed progress/insensitive: {payload['counts']['missed_progress_or_insensitive']}",
            f"- Unexpected/false signal: {payload['counts']['unexpected_or_false_signal']}",
            f"- Compare non-ok: {payload['counts']['compare_not_ok']}",
            (
                "- Episodes with duplicate recommendations: "
                f"{payload['counts']['episodes_with_duplicate_recommendations']}"
            ),
            (
                "- Episodes flagged uncertain by comparability checks: "
                f"{payload['counts']['episodes_flagged_uncertain']}"
            ),
            "",
            "## Operator assessment",
            "",
            payload["operator_assessment"],
            "",
            "## Known capability limits observed",
            "",
        ]
    )
    for limit in payload["observed_limits"]:
        lines.append(f"- {limit}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AS-IMPR-PLANE historical evaluation corpus.")
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path("."),
        help="Repository root to evaluate (default: current directory)",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        required=True,
        help="Corpus JSON file with predeclared episodes and expectations",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for evaluation artifacts",
    )
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    corpus_path = args.corpus.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    episodes = [Episode(**row) for row in corpus["episodes"]]

    workspace = Path(tempfile.mkdtemp(prefix="atlas-impr-hist-eval-"))
    try:
        rows = [_run_episode(repo, workspace, ep) for ep in episodes]
    finally:
        shutil.rmtree(workspace)

    discrepancy_counts = Counter(
        row.get("discrepancy_kind") for row in rows if row.get("discrepancy_kind")
    )
    counts = {
        "false_resolution": discrepancy_counts.get("false_resolution", 0),
        "missed_progress_or_insensitive": discrepancy_counts.get(
            "missed_progress_or_insensitive", 0
        ),
        "unexpected_or_false_signal": discrepancy_counts.get("unexpected_or_false_signal", 0),
        "compare_not_ok": discrepancy_counts.get("compare_not_ok", 0),
        "total_mismatches": sum(1 for row in rows if row["verdict"] == "mismatch"),
        "episodes_with_duplicate_recommendations": sum(
            1
            for row in rows
            if row["actual"]["duplicate_recommendation_ids_before"]
            or row["actual"]["duplicate_recommendation_ids_after"]
        ),
        "episodes_flagged_uncertain": sum(1 for row in rows if row["actual"]["uncertain"]),
    }

    matches = sum(1 for row in rows if row["verdict"] == "match")
    total = len(rows)
    held_out_rows = [row for row in rows if row["split"] == "held_out"]
    held_out_matches = sum(1 for row in held_out_rows if row["verdict"] == "match")
    head = _git(repo, "rev-parse", "HEAD").strip()
    tree = _git(repo, "rev-parse", "HEAD^{tree}").strip()

    observed_limits = [
        "Self-ingest exclusions prevent scoring lane-generated AS-IMPR packets.",
        (
            "Generic receipts without findings/owner_gated_nodes/successor_dag "
            "produce no observation deltas."
        ),
        (
            "Status/prose updates in unsupported packet shapes can represent real "
            "progress but remain unclassified."
        ),
        (
            "Coverage/incompatibility checks are conservative and avoided false "
            "resolved classifications in this corpus."
        ),
    ]
    if counts["false_resolution"] > 0:
        recommendation = "repair"
        assessment = (
            "The frozen implementation produced false resolved outcomes on this corpus. "
            "Retain only with strict constraints and prioritize repair."
        )
    elif counts["missed_progress_or_insensitive"] > 0:
        recommendation = "constrain_or_repair"
        assessment = (
            "The frozen implementation avoided false resolution but missed progress in "
            "multiple authentic transitions. Keep it for narrowly supported packet "
            "shapes only and prioritize parser/contract expansion."
        )
    else:
        recommendation = "retain_with_scope"
        assessment = (
            "Corpus results match expected transitions. Keep current capability within "
            "the evaluated scope; continue monitoring with new authentic episodes."
        )

    payload = {
        "directive": "ATLAS-IMPROVEMENT-PLANE-HISTORICAL-EVALUATION-001",
        "implementation_pin": {
            "head": head,
            "tree": tree,
            "expected_frozen_head": "d4e084c769bfbddd4b6a390e133bcd39011d2b3b",
            "expected_frozen_tree": "087dc06e5678ca935e308543ab9eb640acff2a17",
            "note": (
                "Evaluation lane is branched from the frozen subject. "
                "Only evaluation artifacts should differ."
            ),
        },
        "summary": {
            "episodes_total": total,
            "matches": matches,
            "mismatches": total - matches,
            "held_out_total": len(held_out_rows),
            "held_out_matches": held_out_matches,
            "development_total": total - len(held_out_rows),
        },
        "counts": counts,
        "recommendation": recommendation,
        "operator_assessment": assessment,
        "observed_limits": observed_limits,
        "episodes": rows,
    }

    json_path = output_dir / "historical-evaluation-results.json"
    md_path = output_dir / "historical-evaluation-results.md"
    discrepancy_path = output_dir / "historical-evaluation-discrepancies.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(_render_markdown(payload), encoding="utf-8")

    mismatch_rows = [r for r in rows if r["verdict"] == "mismatch"]
    lines = [
        "# Historical evaluation discrepancies",
        "",
        f"- Total mismatches: {len(mismatch_rows)}",
        "",
    ]
    if mismatch_rows:
        lines.extend(
            [
                "| Episode | Category | Source | Before | After | Expected | Actual | Kind |",
                "|---|---|---|---|---|---|---|---|",
            ]
        )
        for row in mismatch_rows:
            lines.append(
                f"| {row['episode_id']} | {row['category']} | `{row['source_file']}` | "
                f"`{row['before_commit'][:8]}` | `{row['after_commit'][:8]}` | "
                f"{row['expected']['signal']} | {row['actual']['signal']} | "
                f"{row.get('discrepancy_kind') or '-'} |"
            )
    else:
        lines.append("No mismatches detected for this corpus.")
    lines.append("")
    discrepancy_path.write_text("\n".join(lines), encoding="utf-8")

    print(str(json_path))
    print(str(md_path))
    print(str(discrepancy_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
