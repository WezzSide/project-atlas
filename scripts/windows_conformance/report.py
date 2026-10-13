"""Generates the human-readable "WINDOWS PLATFORM REALITY" report from a
receipt -- always derived from the same result model, never hand-maintained
separately."""

from __future__ import annotations

from typing import Any


def render(receipt: dict[str, Any]) -> str:
    probes: list[dict[str, Any]] = receipt["probes"]
    lines: list[str] = []
    lines.append("# WINDOWS PLATFORM REALITY")
    lines.append("")
    lines.append(f"`ATLAS_WINDOWS_CONFORMANCE_V1` schema v{receipt['schema_version']}")
    lines.append(f"HEAD `{receipt['head']}` TREE `{receipt['tree']}`")
    lines.append(f"Host: {receipt['host']['os']} {receipt['host']['os_release']} ({receipt['host']['machine']}), Python {receipt['python']}")
    lines.append(f"Content hash: `{receipt['content_hash']}`")
    lines.append("")
    s = receipt["summary"]
    lines.append(
        f"**{s['probes_total']} probes** — "
        + ", ".join(f"{k}={v}" for k, v in s["by_result"].items())
        + (f" — **{s['harness_errors']} harness errors**" if s["harness_errors"] else "")
    )
    lines.append("")

    def _section(title: str, predicate: Any) -> None:
        matched = [p for p in probes if predicate(p)]
        lines.append(f"## {title} ({len(matched)})")
        lines.append("")
        if not matched:
            lines.append("_none_")
        for p in matched:
            lines.append(f"- **{p['probe_id']}** ({p['domain']}): {p['observation']}")
        lines.append("")

    _section("PROVEN COMPATIBLE", lambda p: p["result"] == "PASS" and p["epistemic_state"] == "OBSERVED")
    _section(
        "ATLAS-STRONGER-THAN-OS",
        lambda p: p["domain"] == "path_reality"
        and p["result"] == "PASS"
        and any(not c.get("os_accepts", True) is False and c.get("atlas_contract_accepts") is False for c in p["evidence"].get("cases", [])),
    )
    _section("PLATFORM DIFFERENCES", lambda p: p["domain"] in ("atomic_io", "file_lock_semantics", "process_identity"))
    _section("KNOWN DEFECTS", lambda p: p["result"] == "FAIL")
    _section("UNKNOWN / NOT TESTED", lambda p: p["result"] in ("NOT_TESTED",) or p["epistemic_state"] == "UNKNOWN")
    _section("HIGH-VALUE FOLLOW-UPS", lambda p: p["result"] == "FAIL")

    if receipt["harness_errors"]:
        lines.append("## HARNESS ERRORS (fix, not a platform finding)")
        lines.append("")
        for e in receipt["harness_errors"]:
            lines.append(f"- `{e['probe_fn']}`: {e['error']}")
        lines.append("")

    return "\n".join(lines)
