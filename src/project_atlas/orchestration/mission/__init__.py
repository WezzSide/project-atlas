"""AS-MISSION-VERTICAL-SLICE-001 -- project knowledge to verified engineering
result, end to end.

Built against fresh `origin/main` (not stacked on any open, unmerged PR).
Several other open PRs at the time this package was written cover adjacent
ground -- Mission Journey (#785), OWNERSHIP_CLAIM (#776), execution identity
(#743, #765), Studio task-context (#786) -- and were all under active,
concurrent development when this package was started. This package
deliberately does not depend on, import from, or modify any of them:
UNMERGED != CANONICAL DEPENDENCY, and touching an open PR's own branch/files
is out of bounds regardless. Where this package's own scope overlaps
conceptually with theirs, that is convergent design on a shared problem, not
coordination with their implementations -- reconciling the two is a
follow-up for whoever holds accepted authority over both once they land.

Modules:
  - `context_packet.py` (KNOWLEDGE) -- bounded, mission-specific context
    compilation from real repository documents, with explicit staleness
    detection and a hard separation between retrieved material (data) and
    trusted policy (authority).
  - `os_lock.py` -- cross-platform kernel-lock primitive. A deliberate,
    noted duplication of `project_atlas.orchestration.sdk.os_lock` from the
    (at time of writing) unmerged PR #780 -- authored in this same session,
    copied rather than imported for the same reason: that PR is not a
    dependency this package can take.
  - `lease.py` (DEVELOPMENT/RECOVERY) -- general-purpose, single-holder
    mission-run ownership lease built on `os_lock`, independent of the
    package-specific `orchestration.sdk.lease_registry` (which is scoped to
    one package's own governed mutation route, not general-purpose).
  - `adapter.py` (DEVELOPMENT) -- the agent-adapter protocol and one real,
    bounded reference implementation.
  - `execution.py` (DEVELOPMENT/RECOVERY) -- isolated-workspace mission
    runs, durable checkpointing, idempotency-aware external-effect
    recording.
  - `recovery.py` (RECOVERY) -- UI-reconnect vs. worker-recovery
    reconciliation; ambiguous outcomes are reconciled, never blindly
    retried.
  - `baseline.py` (MEASUREMENT) -- a small, honest, reproducible baseline
    over representative missions.
"""

from __future__ import annotations

from typing import Final

#: Where every operational file this package writes into a mission
#: workspace lives -- the lease, its receipt, the run checkpoint, and the
#: delivered context file -- rather than the workspace root directly.
#: Discovered as a real gap while dogfooding this package's own pipeline:
#: using a real repository checkout AS the workspace (a real, expected
#: usage -- the lease then protects exclusive ownership of that checkout
#: during a run) left `mission.lease`, `mission-run-checkpoint.json`, etc.
#: sitting directly in the repo root, untracked and easy to mistake for
#: real repository content. Mirrors this repository's own established
#: convention (`orchestration.sdk.models.STATE_DIR_RELATIVE`) for exactly
#: this kind of operational-state-vs-content separation.
MISSION_STATE_DIR_NAME: Final[str] = ".atlas-mission"
