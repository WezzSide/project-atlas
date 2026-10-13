"""AS-CODER-ALPHA-CONNECT-PERF-001 — measure, do not game.

Records reconstructable micro-timings for cold connect, warm unchanged
reconnect, one-file delta, and derived lenses. This is a baseline /
regression-band instrument, not a product SLA.

Stacked on AS-CODER-ALPHA-INCREMENTAL-CONNECT-001. Skip is operational
only. Telemetry != Truth Core. Does not rewrite ``connect.py``.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from project_atlas.agent_handoff import create_handoff, export_agent_context
from project_atlas.connect import connect_project
from project_atlas.project_brief import build_project_brief
from project_atlas.project_next import derive_next_lenses
from project_atlas.source_health import explain_source_health

PACKAGE_ID: Final[str] = "AS-CODER-ALPHA-CONNECT-PERF-001"
GENERATOR_ID: Final[str] = "atlas-coder-alpha-connect-perf-001"
SCHEMA_NAME: Final[str] = "atlas.coder-alpha.connect-perf.v1"
RECEIPT_RELATIVE: Final[Path] = Path("generated") / "ops" / "connect-perf-baseline.json"
DEPENDENCY_PR: Final[int] = 374
TRUTH_BOUNDARY: Final[str] = (
    "BASELINE != SLA / PERF != PRODUCT GATE / TELEMETRY != TRUTH CORE / "
    "DEMO_FIXTURE != AUTHENTIC_PILOT / INCREMENTAL SKIP != AUTHORITY"
)

LaneName = Literal[
    "cold_connect",
    "warm_unchanged_reconnect",
    "one_file_delta",
    "context_generation",
    "brief_generation",
    "handoff_generation",
    "source_health",
    "atlas_next",
]


class ConnectPerfError(ValueError):
    """Fail-closed connect-perf error."""


@dataclass(frozen=True, slots=True)
class LaneSample:
    name: LaneName
    wall_ms: int
    files_inspected: int | None
    files_reparsed: int | None
    records_changed: int | None
    writes: int | None
    peak_rss_kb: int | None
    notes: str


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _peak_rss_kb() -> int | None:
    """Peak resident set size in KB, or ``None`` when unsupported.

    ``resource`` is POSIX-only -- it does not exist on Windows at all, and
    typeshed's stub reflects that: it declares no ``getrusage``/
    ``RUSAGE_SELF`` under the win32 platform mypy targets by default on a
    Windows host, so a bare ``try: import resource / except ImportError``
    left this file's single Windows-reached line statically unresolvable
    (mypy checks it as if it could still run there, since it has no way to
    prove the ImportError branch already returned). The explicit
    ``if sys.platform == "win32": ... else: ...`` shape below is not just a
    comment for humans: mypy specially narrows on ``sys.platform``
    comparisons shaped this way, so on a win32 target it skips checking the
    ``else`` body against the (correctly) resource-less win32 stub --
    while on a non-Windows target it narrows the other way and the real
    POSIX path is checked exactly as before (an ``if/else``, not an
    early-return, because with ``warn_unreachable = true`` an early-return
    form makes mypy flag the POSIX-only code after it as an unreachable-
    statement *error* on a win32 target, rather than simply skip-checking
    it). ``None`` is the existing, already-tested "unsupported platform"
    contract (see ``tests/unit/test_as_coder_alpha_connect_perf_001.py``'s
    fixtures) -- this changes typing only, not the returned value or any
    caller. The inner ``except ImportError`` fallback is kept for any other
    POSIX-like platform that also lacks ``resource`` (e.g. some restricted/
    embedded interpreters) -- Windows is the only platform excluded
    unconditionally because it is the only one confirmed to never have the
    module.
    """
    if sys.platform == "win32":
        return None
    else:
        try:
            import resource
        except ImportError:
            return None
        usage = resource.getrusage(resource.RUSAGE_SELF)
        value = int(usage.ru_maxrss)
        return value if value >= 0 else None


def _time_ms(fn: Callable[[], Any]) -> tuple[Any, int]:
    start = time.perf_counter()
    result = fn()
    return result, int((time.perf_counter() - start) * 1000)


def _incr(report: dict[str, Any]) -> dict[str, Any]:
    payload = report.get("incremental")
    return payload if isinstance(payload, dict) else {}


def _int_or_none(payload: dict[str, Any], key: str) -> int | None:
    raw = payload.get(key)
    return int(raw) if isinstance(raw, int) else None


def compare_cold_warm(*, cold_ms: int, warm_ms: int) -> dict[str, Any]:
    """Objective cold vs warm comparison. No SLA verdict."""
    if cold_ms < 0 or warm_ms < 0:
        raise ConnectPerfError("negative duration is not a measurement")
    ratio = round(warm_ms / cold_ms, 4) if cold_ms > 0 else None
    if ratio is None:
        band = "unknown"
        note = "cold_ms=0; ratio not computed; not a pass"
    elif warm_ms < cold_ms:
        band = "warm_faster"
        note = "warm wall time is lower than cold; not an SLA"
    elif warm_ms == cold_ms:
        band = "warm_equal"
        note = "warm wall time equals cold; not an SLA"
    else:
        band = "warm_slower"
        note = "warm wall time is higher than cold; not a failure"
    return {
        "cold_ms": cold_ms,
        "warm_ms": warm_ms,
        "warm_over_cold": ratio,
        "regression_band": band,
        "sla_declared": False,
        "note": note,
    }


def sample_as_dict(sample: LaneSample) -> dict[str, Any]:
    return {
        "name": sample.name,
        "wall_ms": sample.wall_ms,
        "files_inspected": sample.files_inspected,
        "files_reparsed": sample.files_reparsed,
        "records_changed": sample.records_changed,
        "writes": sample.writes,
        "peak_rss_kb": sample.peak_rss_kb,
        "peak_rss_note": (
            "process-lifetime ru_maxrss when available; not isolated to this lane"
        ),
        "notes": sample.notes,
    }


def report_as_dict(
    samples: list[LaneSample],
    *,
    comparison: dict[str, Any],
    project_id: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "schema": SCHEMA_NAME,
        "package": PACKAGE_ID,
        "dependency_pr": DEPENDENCY_PR,
        "owner_merge_required": True,
        "merge_eligible_to_main": False,
        "project_id": project_id,
        "lanes": [sample_as_dict(item) for item in samples],
        "cold_vs_warm": comparison,
        "terminology": {
            "baseline": True,
            "regression_band": True,
            "product_sla": False,
        },
        "performance_result": "OBSERVATIONAL",
        "truth_boundary": TRUTH_BOUNDARY,
        "generated": {"by": GENERATOR_ID},
        "honesty": {
            "authentic_pilot": False,
            "demo_fixture_ne_authentic_pilot": True,
            "baseline_ne_sla": True,
            "perf_ne_product_gate": True,
            "telemetry_ne_truth_core": True,
            "incremental_skip_is_authority": False,
        },
    }


def _sample_from_connect(name: LaneName, report: dict[str, Any], wall_ms: int) -> LaneSample:
    incr = _incr(report)
    inspected = _int_or_none(incr, "files_inspected")
    changed = _int_or_none(incr, "content_changed")
    semantic = _int_or_none(incr, "semantic_records_changed")
    writes = _int_or_none(incr, "physical_writes")
    ingest = _int_or_none(incr, "ingest_invocations")
    disposition = incr.get("disposition")
    return LaneSample(
        name=name,
        wall_ms=wall_ms,
        files_inspected=inspected,
        files_reparsed=ingest,
        records_changed=semantic if semantic is not None else changed,
        writes=writes,
        peak_rss_kb=_peak_rss_kb(),
        notes=f"disposition={disposition!s}",
    )


def seed_perf_fixture(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("# Connect Perf Fixture\n\nv1\n", encoding="utf-8")
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "DECISIONS.md").write_text(
        "# Decisions\n\nMeasure reconnect; do not invent an SLA.\n",
        encoding="utf-8",
    )
    return root


def run_connect_perf_baseline(project_root: Path) -> dict[str, Any]:
    """Run the measured sequence against an explicit project root."""
    project_root = project_root.expanduser().resolve()
    if not project_root.is_dir():
        raise ConnectPerfError(f"project root is not a directory: {project_root}")

    cold_report, cold_ms = _time_ms(lambda: connect_project(project_root))
    if not isinstance(cold_report, dict) or cold_report.get("status") != "connected":
        raise ConnectPerfError("cold connect did not report connected")
    cold_disposition = _incr(cold_report).get("disposition")
    if cold_disposition != "full_compile":
        raise ConnectPerfError(
            "cold_connect lane requires a genuinely unconnected project_root "
            f"(expected disposition=full_compile, got {cold_disposition!r}); "
            "measure, do not game -- do not label a skip as cold"
        )
    vault = Path(str(cold_report["vault"]))
    project_id = str(cold_report.get("bound_project_id") or "")
    samples = [_sample_from_connect("cold_connect", cold_report, cold_ms)]

    warm_report, warm_ms = _time_ms(lambda: connect_project(project_root))
    if not isinstance(warm_report, dict):
        raise ConnectPerfError("warm reconnect did not return a report")
    samples.append(_sample_from_connect("warm_unchanged_reconnect", warm_report, warm_ms))

    (project_root / "README.md").write_text("# Connect Perf Fixture\n\nv2\n", encoding="utf-8")
    delta_report, delta_ms = _time_ms(lambda: connect_project(project_root))
    if not isinstance(delta_report, dict):
        raise ConnectPerfError("one-file delta connect did not return a report")
    samples.append(_sample_from_connect("one_file_delta", delta_report, delta_ms))

    def _brief() -> dict[str, Any]:
        return build_project_brief(vault, project_id, refresh=False)

    brief, brief_ms = _time_ms(_brief)
    samples.append(
        LaneSample(
            name="brief_generation",
            wall_ms=brief_ms,
            files_inspected=None,
            files_reparsed=None,
            records_changed=None,
            writes=None,
            peak_rss_kb=_peak_rss_kb(),
            notes="build_project_brief refresh=False",
        )
    )
    if not isinstance(brief, dict):
        raise ConnectPerfError("brief generation failed")

    def _context() -> dict[str, Any]:
        return export_agent_context(vault, project_id, refresh_brief=False)

    context, context_ms = _time_ms(_context)
    samples.append(
        LaneSample(
            name="context_generation",
            wall_ms=context_ms,
            files_inspected=None,
            files_reparsed=None,
            records_changed=None,
            writes=None,
            peak_rss_kb=_peak_rss_kb(),
            notes="export_agent_context refresh_brief=False",
        )
    )
    if not isinstance(context, dict):
        raise ConnectPerfError("context generation failed")

    def _handoff() -> dict[str, Any]:
        return create_handoff(vault, project_id, note="connect-perf baseline", refresh_brief=False)

    handoff, handoff_ms = _time_ms(_handoff)
    samples.append(
        LaneSample(
            name="handoff_generation",
            wall_ms=handoff_ms,
            files_inspected=None,
            files_reparsed=None,
            records_changed=None,
            writes=1,
            peak_rss_kb=_peak_rss_kb(),
            notes="create_handoff writes ops pack only",
        )
    )
    if not isinstance(handoff, dict):
        raise ConnectPerfError("handoff generation failed")

    def _health() -> dict[str, Any]:
        return explain_source_health(vault, project_id)

    health, health_ms = _time_ms(_health)
    samples.append(
        LaneSample(
            name="source_health",
            wall_ms=health_ms,
            files_inspected=len(health.get("inspected_artifacts") or [])
            if isinstance(health, dict)
            else None,
            files_reparsed=0,
            records_changed=0,
            writes=0,
            peak_rss_kb=_peak_rss_kb(),
            notes="explain_source_health read-only",
        )
    )

    def _next() -> dict[str, Any]:
        return derive_next_lenses(vault, project_ids=[project_id])

    nxt, next_ms = _time_ms(_next)
    samples.append(
        LaneSample(
            name="atlas_next",
            wall_ms=next_ms,
            files_inspected=None,
            files_reparsed=0,
            records_changed=0,
            writes=0,
            peak_rss_kb=_peak_rss_kb(),
            notes="derive_next_lenses read-only",
        )
    )
    if not isinstance(nxt, dict):
        raise ConnectPerfError("next lens failed")

    comparison = compare_cold_warm(cold_ms=cold_ms, warm_ms=warm_ms)
    report = report_as_dict(samples, comparison=comparison, project_id=project_id)
    dest = vault / RECEIPT_RELATIVE
    _write_atomic(
        dest,
        (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    report["receipt_path"] = dest.as_posix()
    report["vault"] = vault.as_posix()
    return report
