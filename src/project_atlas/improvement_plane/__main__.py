"""Executable entry point: ``python -m project_atlas.improvement_plane``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from project_atlas.improvement_plane.compare import compare_reports, render_compare_summary
from project_atlas.improvement_plane.errors import ImprovementPlaneError
from project_atlas.improvement_plane.evaluate import evaluate_outcomes, render_evaluate_summary
from project_atlas.improvement_plane.outcomes import load_outcomes, record_outcome
from project_atlas.improvement_plane.readers import read_json_object
from project_atlas.improvement_plane.report import (
    PACKAGE_ID,
    compile_improvement_report,
    render_markdown_summary,
    write_report_files,
)


def _emit_error(exc: ImprovementPlaneError, *, as_json: bool) -> int:
    if as_json:
        sys.stdout.write(json.dumps(exc.to_dict(), indent=2, sort_keys=True) + "\n")
    else:
        sys.stderr.write(f"error[{exc.code}]: {exc}\n")
    return 2


def _add_repo_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path("."),
        help="Repository root containing docs/evidence (default: .)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m project_atlas.improvement_plane",
        description=(
            f"{PACKAGE_ID}: observe → compare → recommend → record outcome → evaluate. "
            "Recommendations cite sources and grant no authority."
        ),
    )
    sub = parser.add_subparsers(dest="command")

    report = sub.add_parser("report", help="Compile read-only improvement report (default)")
    _add_repo_args(report)
    report.add_argument("--vault", type=Path, default=None)
    report.add_argument("--reference-utc", default=None)
    report.add_argument("--output-json", type=Path, default=None)
    report.add_argument("--output-md", type=Path, default=None)
    report.add_argument("--json", action="store_true")

    inspect_p = sub.add_parser("inspect", help="Alias for report (read-only)")
    _add_repo_args(inspect_p)
    inspect_p.add_argument("--vault", type=Path, default=None)
    inspect_p.add_argument("--reference-utc", default=None)
    inspect_p.add_argument("--output-json", type=Path, default=None)
    inspect_p.add_argument("--output-md", type=Path, default=None)
    inspect_p.add_argument("--json", action="store_true")

    compare = sub.add_parser("compare", help="Compare two explicit report snapshots")
    compare.add_argument("--before", type=Path, required=True)
    compare.add_argument("--after", type=Path, required=True)
    compare.add_argument("--output-json", type=Path, default=None)
    compare.add_argument("--output-md", type=Path, default=None)
    compare.add_argument("--json", action="store_true", default=False)

    outcome = sub.add_parser(
        "outcome",
        help="Record a local recommendation outcome annotation (writes local store)",
    )
    _add_repo_args(outcome)
    outcome.add_argument("--recommendation-id", required=True)
    outcome.add_argument(
        "--status",
        required=True,
        choices=["accepted", "deferred", "attempted", "completed"],
    )
    outcome.add_argument(
        "--evidence-ref",
        action="append",
        default=[],
        dest="evidence_refs",
        help="Evidence reference path (repeatable)",
    )
    outcome.add_argument("--note", default=None)
    outcome.add_argument("--report-path", default=None)
    outcome.add_argument("--outcomes-file", type=Path, default=None)
    outcome.add_argument("--json", action="store_true")

    evaluate = sub.add_parser(
        "evaluate",
        help="Evaluate recorded outcomes against before/after reports",
    )
    _add_repo_args(evaluate)
    evaluate.add_argument("--before", type=Path, required=True)
    evaluate.add_argument("--after", type=Path, required=True)
    evaluate.add_argument("--outcomes-file", type=Path, default=None)
    evaluate.add_argument("--output-json", type=Path, default=None)
    evaluate.add_argument("--output-md", type=Path, default=None)
    evaluate.add_argument("--json", action="store_true", default=False)

    # Default-compatible top-level flags when no subcommand is used.
    parser.add_argument("--vault", type=Path, default=None)
    parser.add_argument("--reference-utc", default=None)
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--output-md", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    _add_repo_args(parser)
    return parser


def _run_report(args: argparse.Namespace) -> int:
    report = compile_improvement_report(
        args.repo,
        vault_path=args.vault,
        reference_utc=args.reference_utc,
    )
    write_report_files(
        report,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    if args.json:
        sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(render_markdown_summary(report))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "report"
    as_json = bool(getattr(args, "json", False))

    try:
        if command in {"report", "inspect"}:
            return _run_report(args)

        if command == "compare":
            before = read_json_object(args.before, label="before report")
            after = read_json_object(args.after, label="after report")
            result = compare_reports(
                before,
                after,
                before_label=str(args.before),
                after_label=str(args.after),
            )
            write_report_files(
                result,
                output_json=args.output_json,
                output_md=None,
            )
            if args.output_md is not None:
                md_path = args.output_md.expanduser().resolve()
                md_path.parent.mkdir(parents=True, exist_ok=True)
                md_path.write_text(render_compare_summary(result), encoding="utf-8")
            if args.json:
                sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
            else:
                sys.stdout.write(render_compare_summary(result))
            return 0

        if command == "outcome":
            result = record_outcome(
                args.repo,
                recommendation_id=args.recommendation_id,
                status=args.status,
                evidence_refs=list(args.evidence_refs),
                note=args.note,
                report_path=args.report_path,
                outcomes_file=args.outcomes_file,
            )
            if as_json:
                sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
            else:
                sys.stdout.write(
                    f"recorded {result['outcome']['status']} for "
                    f"{result['outcome']['recommendation_id']} -> {result['path']}\n"
                )
            return 0

        if command == "evaluate":
            before = read_json_object(args.before, label="before report")
            after = read_json_object(args.after, label="after report")
            outcomes = load_outcomes(args.repo, outcomes_file=args.outcomes_file)
            result = evaluate_outcomes(
                before_report=before,
                after_report=after,
                outcomes=outcomes,
            )
            write_report_files(
                result,
                output_json=args.output_json,
                output_md=None,
            )
            if args.output_md is not None:
                md_path = args.output_md.expanduser().resolve()
                md_path.parent.mkdir(parents=True, exist_ok=True)
                md_path.write_text(render_evaluate_summary(result), encoding="utf-8")
            if args.json:
                sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
            else:
                sys.stdout.write(render_evaluate_summary(result))
            return 0

        raise ImprovementPlaneError("unknown-command", f"Unknown command: {command}")
    except ImprovementPlaneError as exc:
        return _emit_error(exc, as_json=as_json or command in {"compare", "evaluate", "outcome"})


if __name__ == "__main__":
    raise SystemExit(main())
