"""Standalone contender process spawned by
`test_primary_governor_atomic_lock.py`. Not collected by pytest itself
(leading underscore + no `test_` prefix). Calls the real production
`acquire_primary_lock()`, optionally holds it briefly, and reports a JSON
result line.

Usage: _primary_lock_worker.py <root> <go_file> <out_file> <hold_sec>
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    root = Path(sys.argv[1])
    go_file = Path(sys.argv[2])
    out_file = Path(sys.argv[3])
    hold_sec = float(sys.argv[4])

    from project_atlas.orchestration.sdk.resident_driver import acquire_primary_lock

    deadline = time.time() + 30.0
    while not go_file.is_file():
        if time.time() > deadline:
            out_file.write_text(
                json.dumps({"pid": os.getpid(), "error": "go_file_timeout"}), encoding="utf-8"
            )
            return 1
        time.sleep(0.001)

    won = acquire_primary_lock(root)
    out_file.write_text(json.dumps({"pid": os.getpid(), "won": bool(won)}), encoding="utf-8")

    if won and hold_sec > 0:
        time.sleep(hold_sec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
