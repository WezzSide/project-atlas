"""Renders WINDOWS-RELIABILITY-REPORT.md from a receipt -- generated from
the same result model as the receipt itself, never hand-maintained
separately."""

from __future__ import annotations

from typing import Any


def render(receipt: dict[str, Any]) -> str:
    experiments: list[dict[str, Any]] = receipt["experiments"]
    lines: list[str] = []
    lines.append("# WINDOWS RELIABILITY REPORT")
    lines.append("")
    lines.append(f"`ATLAS_WINDOWS_RELIABILITY_LAB_V1` schema v{receipt['schema_version']}")
    lines.append(f"HEAD `{receipt['head']}` TREE `{receipt['tree']}`")
    lines.append(
        f"Host: {receipt['host']['os']} {receipt['host']['os_release']} "
        f"({receipt['host']['machine']}), Python {receipt['python']}, "
        f"mode={receipt['mode']}, seed={receipt['seed']}"
    )
    lines.append("")
    s = receipt["summary"]
    lines.append(
        f"**{s['experiment_classes']} experiment classes, "
        f"{s['total_operations']} total operations** "
        f"in {s['suite_wall_clock_sec']}s wall-clock — "
        + ", ".join(f"{k}={v}" for k, v in s["by_verdict"].items())
        + (f" — **{s['lab_errors']} lab errors**" if s["lab_errors"] else "")
        + f" — global_leak_count={s['global_leak_count']}"
    )
    if receipt.get("skipped_due_to_suite_timeout"):
        skipped = receipt["skipped_due_to_suite_timeout"]
        lines.append(f"**Skipped due to whole-suite timeout budget:** {skipped}")
    lines.append("")

    def _section(title: str, predicate: Any, detail_fmt: Any = None) -> None:
        matched = [e for e in experiments if predicate(e)]
        lines.append(f"## {title} ({len(matched)})")
        lines.append("")
        if not matched:
            lines.append("_none_")
        for e in matched:
            extra = detail_fmt(e) if detail_fmt else ""
            lines.append(
                f"- **{e['experiment_id']}** ({e['domain']}): {e['hypothesis']} "
                f"— iterations={e['iterations']} failures={e['failures']} "
                f"failure_rate={e['failure_rate']}{extra}"
            )
        lines.append("")

    _section(
        "RELIABILITY SUMMARY",
        lambda e: True,
        lambda e: f" verdict={e['verdict']} p95_ms={e['latency']['p95_ms']}",
    )
    _section("STABLE CONTRACTS", lambda e: e["verdict"] == "PASS")
    _section(
        "INTERMITTENT FAILURES",
        lambda e: e["verdict"] == "FAIL" and 0 < (e["failure_rate"] or 0) < 1,
    )
    _section(
        "RACE FINDINGS",
        lambda e: e["domain"] in ("lock_races", "path_filesystem_race", "atomic_io_stress")
        and e["verdict"] == "FAIL",
    )
    _section(
        "ENVIRONMENT RISKS",
        lambda e: e["domain"] == "tool_resolution_drift",
    )
    _section(
        "PROVEN CLEANUP",
        lambda e: e["cleanup_failures"] == 0,
        lambda e: " cleanup_failures=0",
    )
    _section(
        "UNKNOWN / UNTESTED",
        lambda e: e["verdict"] in ("INCONCLUSIVE", "NOT_APPLICABLE")
        or e["epistemic_state"] == "UNKNOWN",
    )
    _section(
        "NEW RESIDUALS (verdict=FAIL, not pre-known)",
        lambda e: e["verdict"] == "FAIL" and "already-filed" not in e.get("notes", "")
        and "already tracked" not in e.get("notes", ""),
    )

    if receipt.get("lab_errors"):
        lines.append("## LAB ERRORS (harness defects, not platform findings)")
        lines.append("")
        for e in receipt["lab_errors"]:
            lines.append(f"- `{e['experiment_fn']}`: {e['error']}")
        lines.append("")

    return "\n".join(lines)
