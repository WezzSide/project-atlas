"""FIXTURE worker for AS-ORCH-PROGRAM-SUPERVISOR-001 acceptance tests.

LABEL: FIXTURE. Not a model, not an agent runtime, and never presented as
either. It exists so the supervisor's own behaviour -- ordering, leasing,
checkpointing, restart, waiting, cancellation, limits, fault classification --
can be established deterministically and without a model call.

FIXTURE_RUN != REAL_RUNTIME_COMPATIBILITY.

Behaviour is chosen entirely by ``ATLAS_FIXTURE_MODE`` so the supervisor never
needs a test-only branch:

  write        create/append to ``ATLAS_FIXTURE_TARGET`` (default
               ``<task>.txt``), exit 0
  claim-only   print a confident completion claim, change nothing, exit 0
  fail-once    fail on attempt 1, write on attempt 2 and after
  hang         sleep until killed
  exit:<n>     exit with status n (20/21/22/23 map to failure classes)
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def main() -> int:
    mode = os.environ.get("ATLAS_FIXTURE_MODE", "write")
    attempt = int(os.environ.get("ATLAS_PROGRAM_ATTEMPT", "1"))
    task = os.environ.get("ATLAS_PROGRAM_TASK", "unknown")
    # Default to a per-task file. Two fixture tasks sharing one output would
    # share a mutation path, and the existing surface-overlap gate would then
    # correctly refuse to run them alongside each other -- turning any test
    # about independent work into a test about the overlap gate.
    target = os.environ.get("ATLAS_FIXTURE_TARGET") or f"{task}.txt"

    if mode == "hang":
        print(f"FIXTURE: {task} hanging on purpose", flush=True)
        while True:
            time.sleep(0.2)

    if mode.startswith("exit:"):
        code = int(mode.split(":", 1)[1])
        print(f"FIXTURE: {task} exiting {code}", flush=True)
        return code

    if mode == "claim-only":
        # The whole point of this mode: a worker that says exactly what a
        # successful one says, and does nothing. Acceptance must catch it.
        print("Task complete. All requirements have been satisfied.", flush=True)
        return 0

    if mode == "fail-once" and attempt == 1:
        print("FIXTURE: transient failure on attempt 1", file=sys.stderr, flush=True)
        return 20

    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{task}:attempt={attempt}\n")
    print(f"FIXTURE: {task} wrote {target}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
