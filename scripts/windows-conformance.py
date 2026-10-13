#!/usr/bin/env python3
"""ATLAS_WINDOWS_CONFORMANCE_V1 CLI.

Usage:
    python scripts/windows-conformance.py run [--out DIR]
    python scripts/windows-conformance.py run --repeat  # runs twice, checks
                                                          # normalized digest
                                                          # equivalence

Measurement only -- never mutates production state, never requires
elevation, never touches a process it did not itself spawn.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from windows_conformance import report as report_mod  # noqa: E402
from windows_conformance.runner import run_all  # noqa: E402


def _write(receipt: dict[str, object], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = out_dir / "windows-conformance-receipt.json"
    report_path = out_dir / "WINDOWS-PLATFORM-REALITY.md"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path.write_text(report_mod.render(receipt) + "\n", encoding="utf-8")
    return receipt_path, report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="windows-conformance")
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="run every probe and write the receipt + report")
    run_p.add_argument(
        "--out", default=str(REPO_ROOT / "generated" / "ops" / "windows-conformance")
    )
    run_p.add_argument(
        "--repeat", action="store_true", help="run twice and verify normalized equivalence"
    )
    run_p.add_argument(
        "--json", action="store_true", help="print the receipt JSON to stdout instead of a summary"
    )
    args = parser.parse_args(argv)

    if args.command == "run":
        receipt = run_all()
        out_dir = Path(args.out)
        receipt_path, report_path = _write(receipt, out_dir)

        if args.repeat:
            receipt2 = run_all()
            stable_equal = receipt["content_hash"] == receipt2["content_hash"]
            print(f"REPEAT_RUN_DIGEST_1={receipt['content_hash']}")
            print(f"REPEAT_RUN_DIGEST_2={receipt2['content_hash']}")
            print(f"NORMALIZED_REPEATABILITY={'PASS' if stable_equal else 'FAIL'}")
            if not stable_equal:
                return 1

        if args.json:
            print(json.dumps(receipt, indent=2, sort_keys=True))
        else:
            s = receipt["summary"]
            print(f"ATLAS_WINDOWS_CONFORMANCE_V1  HEAD={receipt['head'][:12]}")
            print(f"PROBES_TOTAL={s['probes_total']}  HARNESS_ERRORS={s['harness_errors']}")
            for k, v in s["by_result"].items():
                print(f"  {k}={v}")
            print(f"content_hash={receipt['content_hash']}")
            print(f"receipt: {receipt_path}")
            print(f"report:  {report_path}")
        return 1 if receipt["summary"]["harness_errors"] else 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
