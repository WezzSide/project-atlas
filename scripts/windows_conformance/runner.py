"""Executes the full probe registry and produces the
ATLAS_WINDOWS_CONFORMANCE_V1 receipt."""

from __future__ import annotations

import platform
import subprocess
import sys
import traceback
from collections import Counter
from pathlib import Path
from typing import Any

from windows_conformance import SCHEMA_VERSION, procutil
from windows_conformance.model import ProbeOutcome, stable_digest
from windows_conformance.registry import ALL_PROBES


def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        creationflags=procutil.NO_WINDOW,
    )
    return result.stdout.strip()


def _tool_version(args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=15, check=False,
            creationflags=procutil.NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (result.stdout or result.stderr or "").strip()
    return text.splitlines()[0] if text else None


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def normalized_host() -> dict[str, Any]:
    """Host facts with no raw user-identifying paths -- OS/tool identity
    only, matching the receipt's own no-sensitive-path requirement."""
    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "os_version": platform.version(),
        "machine": platform.machine(),
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
    }


def run_all(precomputed_outcomes: list[ProbeOutcome] | None = None) -> dict[str, Any]:
    """Execute every probe and build the receipt.

    ``precomputed_outcomes``, when given, is used instead of re-executing
    ``ALL_PROBES`` -- lets a caller (notably the test suite's shared
    fixture) build a receipt from outcomes it already computed once,
    rather than triggering a second full run of every real-process probe.
    Harness-error capture only applies to a fresh execution: outcomes
    supplied this way are assumed already-successful ProbeOutcome objects.
    """
    root = repo_root()
    outcomes: list[ProbeOutcome] = []
    harness_errors: list[dict[str, str]] = []
    if precomputed_outcomes is not None:
        outcomes = list(precomputed_outcomes)
    else:
        for fn in ALL_PROBES:
            try:
                outcomes.append(fn())
            except Exception as exc:  # noqa: BLE001 -- a probe crashing is itself evidence
                harness_errors.append(
                    {
                        "probe_fn": fn.__name__,
                        "error": f"{type(exc).__name__}: {exc}",
                        "traceback": "".join(traceback.format_exception(exc))[-2000:],
                    }
                )

    result_counts = Counter(o.result for o in outcomes)
    epistemic_counts = Counter(o.epistemic_state for o in outcomes)
    domain_counts: dict[str, Counter[str]] = {}
    for o in outcomes:
        domain_counts.setdefault(o.domain, Counter())[o.result] += 1

    receipt = {
        "schema": "ATLAS_WINDOWS_CONFORMANCE_V1",
        "schema_version": SCHEMA_VERSION,
        "repository": "B0LK13/project-atlas",
        "head": _git(["rev-parse", "HEAD"], root),
        "tree": _git(["rev-parse", "HEAD^{tree}"], root),
        "host": normalized_host(),
        "python": sys.version.split()[0],
        "git": _tool_version(["git", "--version"]),
        "powershell": _tool_version(["powershell", "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"]),
        "probes": [o.to_dict() for o in outcomes],
        "harness_errors": harness_errors,
        "summary": {
            "probes_total": len(outcomes),
            "harness_errors": len(harness_errors),
            "by_result": dict(result_counts),
            "by_epistemic_state": dict(epistemic_counts),
            "by_domain": {d: dict(c) for d, c in domain_counts.items()},
        },
        "content_hash": stable_digest(outcomes),
    }
    return receipt
