"""Fixture worker for TestAcquireIsNowAtomicNotCheckThenWrite: a real,
independent OS process (a genuine, distinct pid) that tries once to acquire
the resident-driver primary lock at the path given on the command line.

Prints ACQUIRED or REFUSED, then -- if it won -- holds the lock until stdin
closes. The parent must collect every first line *before* closing stdin so
the assertion measures concurrent exclusivity, not sequential reclaim after
a winner exits (the pre-fix harness printed and returned immediately, so a
later worker could reclaim a stale lock and also print ACQUIRED).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(REPO_SRC))

from project_atlas.orchestration.sdk.resident_driver import acquire_primary_lock  # noqa: E402

if __name__ == "__main__":
    root = Path(sys.argv[1])
    won = acquire_primary_lock(root)
    print("ACQUIRED" if won else "REFUSED", flush=True)
    if won:
        sys.stdin.read()
