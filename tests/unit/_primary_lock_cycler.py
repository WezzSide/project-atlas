"""Standalone process spawned by the hardening-mission crash-matrix test
(`test_kill_during_random_acquire_release_cycling`). Loops
acquire -> tiny sleep -> release repeatedly so an external SIGKILL /
TerminateProcess lands at an effectively random point across the full
acquire/hold/release cycle -- including points a single fixed kill-timing
test could never hit (mid-publish, immediately post-acquire pre-publish,
mid-release, between release and the next acquire).

Usage: _primary_lock_cycler.py <root> <go_file>
"""

from __future__ import annotations

import time
from pathlib import Path
from sys import argv


def main() -> int:
    root = Path(argv[1])
    go_file = Path(argv[2])

    from project_atlas.orchestration.sdk.resident_driver import (
        acquire_primary_lock,
        release_primary_lock,
    )

    deadline = time.time() + 30.0
    while not go_file.is_file():
        if time.time() > deadline:
            return 1
        time.sleep(0.001)

    while True:
        if acquire_primary_lock(root):
            time.sleep(0.01)
            release_primary_lock(root)
        time.sleep(0.005)
    return 0  # pragma: no cover -- unreachable, killed externally


if __name__ == "__main__":
    raise SystemExit(main())
