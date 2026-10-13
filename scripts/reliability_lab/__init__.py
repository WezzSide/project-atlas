"""ATLAS_WINDOWS_RELIABILITY_LAB_V1 -- controlled reliability laboratory.

``ATLAS_WINDOWS_CONFORMANCE_V1`` (PR #771, unmerged at this package's
inception) answered "what does native Windows actually do?" -- once, per
probe. This package answers a different question:

    Does that behavior remain reliable when timing, concurrency,
    repetition, and injected failure conditions become hostile?

    ONE PASS != RELIABILITY
    NO FAILURE OBSERVED != IMPOSSIBLE
    1000 PASSES != FORMAL PROOF
    STRESS RESULT != AUTHORITY

## Duplication reconciliation (mission-required, see mission §32)

This package deliberately does NOT import from ``scripts/windows_conformance``
(PR #771): that PR is unmerged, and "unmerged != canonical dependency" --
depending on it would make this package's own tests fail closed the
instant someone runs them without #771's branch checked out, and would
make #771 a *hidden* runtime dependency of a supposedly independent lane.

Instead, ``reliability_lab/procutil.py`` is a small, deliberately-scoped
copy of #771's ``windows_conformance/procutil.py`` (the process spawn/
snapshot/cleanup helpers -- NO_WINDOW, snapshot_pids, process_alive,
terminate_tree, win32_process_info, CleanupProof). That module is already
battle-tested (three real, user-visible console-window incidents during
#771's own development taught the exact lessons baked into it) and
re-deriving it from scratch here would either repeat those incidents or
silently regress the fixes. The copy is intentionally minimal -- this
package needs process lifecycle primitives, not #771's probe registry or
its conformance-specific result model.

**Duplication risk**: if #771 and this package's ``procutil.py`` diverge
(a bug fixed in one but not the other), that divergence is real and must
be caught, not assumed away. Once #771 merges, a follow-up should either
(a) delete this copy and depend on the canonical module, or (b) if this
package has since diverged for lab-specific reasons, explicitly document
why they're no longer the same code. Do not let this note go stale.

Security posture (unchanged from #771, restated because this package
spawns far more processes): no administrator elevation, no registry/
global-PATH/global-package mutation, no writes outside a scratch root, no
touching a process this lab did not itself spawn, no persisted
credentials, every subprocess call sets NO_WINDOW.
"""

from __future__ import annotations

SCHEMA_VERSION: int = 1

__all__ = ["SCHEMA_VERSION"]
