"""Standalone process spawned by `test_os_lock_primitive.py`. Acquires an
`os_lock` directly (not through `resident_driver`), reports success, holds
for `hold_sec`, then releases and exits cleanly.

Usage: _os_lock_holder.py <path> <go_file> <out_file> <hold_sec>
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def main() -> int:
    lock_path = Path(sys.argv[1])
    go_file = Path(sys.argv[2])
    out_file = Path(sys.argv[3])
    hold_sec = float(sys.argv[4])

    from project_atlas.orchestration.sdk import os_lock

    deadline = time.time() + 30.0
    while not go_file.is_file():
        if time.time() > deadline:
            return 1
        time.sleep(0.001)

    fd = os_lock.try_acquire_exclusive(lock_path)
    out_file.write_text(json.dumps({"acquired": fd is not None}), encoding="utf-8")
    if fd is None:
        return 1

    time.sleep(hold_sec)
    os_lock.release(fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
