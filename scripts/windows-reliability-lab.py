#!/usr/bin/env python3
"""ATLAS_WINDOWS_RELIABILITY_LAB_V1 CLI.

Usage:
    python scripts/windows-reliability-lab.py run --mode quick
    python scripts/windows-reliability-lab.py run --mode standard --seed 1234
    python scripts/windows-reliability-lab.py run --mode soak
    python scripts/windows-reliability-lab.py run --mode standard --repeat

Never elevates privileges, never mutates global/system state, never
touches a process it did not itself spawn. Every subprocess call across
the whole package suppresses window creation (CREATE_NO_WINDOW).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from reliability_lab import report as report_mod  # noqa: E402
from reliability_lab.runner import run_suite  # noqa: E402


def _write(receipt: dict[str, object], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = out_dir / "windows-reliability-receipt.json"
    report_path = out_dir / "WINDOWS-RELIABILITY-REPORT.md"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path.write_text(report_mod.render(receipt) + "\n", encoding="utf-8")
    return receipt_path, report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="windows-reliability-lab")
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="run every reliability experiment, write receipt + report")
    run_p.add_argument("--mode", choices=["quick", "standard", "soak"], default="standard")
    run_p.add_argument("--seed", type=int, default=None, help="omit for a fresh random seed")
    run_p.add_argument(
        "--out", default=str(REPO_ROOT / "generated" / "ops" / "windows-reliability-lab")
    )
    run_p.add_argument(
        "--repeat", action="store_true", help="run twice with the same seed, compare verdicts"
    )
    args = parser.parse_args(argv)

    if args.command == "run":
        seed = args.seed if args.seed is not None else random.SystemRandom().randrange(1, 2**31)
        receipt = run_suite(args.mode, seed)
        out_dir = Path(args.out)
        receipt_path, report_path = _write(receipt, out_dir)

        if args.repeat:
            receipt2 = run_suite(args.mode, seed)
            verdicts_1 = {e["experiment_id"]: e["verdict"] for e in receipt["experiments"]}
            verdicts_2 = {e["experiment_id"]: e["verdict"] for e in receipt2["experiments"]}
            same = verdicts_1 == verdicts_2
            print(f"REPEAT_VERDICTS_1={verdicts_1}")
            print(f"REPEAT_VERDICTS_2={verdicts_2}")
            print(f"BASELINE_REPEATABILITY={'PASS' if same else 'DIFFERS'}")

        s = receipt["summary"]
        print(f"ATLAS_WINDOWS_RELIABILITY_LAB_V1  HEAD={receipt['head'][:12]}  mode={args.mode}")
        print(f"seed={seed}")
        print(f"EXPERIMENT_CLASSES={s['experiment_classes']}")
        print(f"TOTAL_OPERATIONS={s['total_operations']}")
        for k, v in s["by_verdict"].items():
            print(f"  {k}={v}")
        print(f"GLOBAL_LEAK_COUNT={s['global_leak_count']}  LAB_ERRORS={s['lab_errors']}")
        print(f"receipt: {receipt_path}")
        print(f"report:  {report_path}")
        return 1 if (s["lab_errors"] or s["global_leak_count"]) else 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
