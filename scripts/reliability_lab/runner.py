"""Executes the full experiment registry and produces the
ATLAS_WINDOWS_RELIABILITY_LAB_V1 receipt.

Timeout discipline (mission-required): every trial is already bounded by
its own per-operation timeout (``runctl.run_trials``); this module adds
the next two required layers -- a per-experiment wall-clock timeout (in
case an experiment's own loop/cleanup logic hangs outside the trial
executor) and a whole-suite timeout (remaining experiments are recorded
as skipped, not silently dropped, if the budget runs out).
"""

from __future__ import annotations

import platform
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any

from reliability_lab import SCHEMA_VERSION, procutil
from reliability_lab.model import ExperimentResult
from reliability_lab.registry import ALL_EXPERIMENTS

_LEAK_SCAN_MARKER = "atlas-lab-"  # every spawn across every experiment is tagged with this prefix
PER_EXPERIMENT_TIMEOUT_SEC = 180.0
WHOLE_SUITE_TIMEOUT_SEC = 1800.0  # 30 min hard ceiling regardless of mode


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


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


def normalized_host() -> dict[str, Any]:
    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "os_version": platform.version(),
        "machine": platform.machine(),
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
    }


def _global_leak_scan() -> int:
    """Suite-end cleanup proof, independent of every experiment's own
    per-run cleanup_fn: enumerate anything still matching the shared
    ``atlas-lab-`` marker prefix used by every spawn in this package."""
    if sys.platform != "win32":
        return 0
    pids = procutil.snapshot_pids(_LEAK_SCAN_MARKER)
    for pid in pids:
        procutil.terminate_tree(pid)
    time.sleep(0.5)
    still = procutil.snapshot_pids(_LEAK_SCAN_MARKER)
    return len(still)


def run_suite(mode: str, seed: int) -> dict[str, Any]:
    root = repo_root()
    results: list[ExperimentResult] = []
    lab_errors: list[dict[str, str]] = []
    skipped: list[str] = []

    suite_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=1) as pool:
        for fn in ALL_EXPERIMENTS:
            elapsed = time.perf_counter() - suite_start
            if elapsed > WHOLE_SUITE_TIMEOUT_SEC:
                skipped.append(fn.__name__)
                continue
            future = pool.submit(fn, mode, seed)
            try:
                results.append(future.result(timeout=PER_EXPERIMENT_TIMEOUT_SEC))
            except FutureTimeoutError:
                lab_errors.append(
                    {
                        "experiment_fn": fn.__name__,
                        "error": f"exceeded per-experiment timeout ({PER_EXPERIMENT_TIMEOUT_SEC}s)",
                    }
                )
            except Exception as exc:
                lab_errors.append(
                    {"experiment_fn": fn.__name__, "error": f"{type(exc).__name__}: {exc}"}
                )

    global_leaks = _global_leak_scan()

    verdict_counts = Counter(r.verdict for r in results)
    domain_counts: dict[str, Counter[str]] = {}
    total_operations = 0
    for r in results:
        domain_counts.setdefault(r.domain, Counter())[r.verdict] += 1
        total_operations += r.iterations

    receipt = {
        "schema": "ATLAS_WINDOWS_RELIABILITY_LAB_V1",
        "schema_version": SCHEMA_VERSION,
        "repository": "B0LK13/project-atlas",
        "head": _git(["rev-parse", "HEAD"], root),
        "tree": _git(["rev-parse", "HEAD^{tree}"], root),
        "host": normalized_host(),
        "python": sys.version.split()[0],
        "mode": mode,
        "seed": seed,
        "experiments": [r.to_dict() for r in results],
        "lab_errors": lab_errors,
        "skipped_due_to_suite_timeout": skipped,
        "summary": {
            "experiment_classes": len(results),
            "total_operations": total_operations,
            "by_verdict": dict(verdict_counts),
            "by_domain": {d: dict(c) for d, c in domain_counts.items()},
            "lab_errors": len(lab_errors),
            "global_leak_count": global_leaks,
            "suite_wall_clock_sec": round(time.perf_counter() - suite_start, 1),
        },
    }
    return receipt
