"""ATLAS_WINDOWS_CONFORMANCE_V1 -- native-Windows platform-reality harness.

Answers, with reproducible probes and machine-readable evidence, not
assumption: "what does native Windows actually do, on this host, for the
operating-system semantics Atlas depends on?"

Truth boundaries this package holds itself to (AGENTS.md/CLAUDE.md style):
  DOCUMENTATION != OBSERVATION
  PLATFORM ASSUMPTION != CONTRACT
  COMMAND EXIT 0 != STATE PROVEN
  PROCESS SPAWNED != PROCESS IDENTIFIED
  PID EXISTS != PROCESS IDENTITY PROVEN
  PATH EXISTS != PATH SAFE
  WINDOWS != ONE UNIVERSAL FILESYSTEM CONFIGURATION
  MISSING EVIDENCE != PASS
  UNKNOWN != HEALTHY

This is measurement infrastructure, not a product feature: it lives under
``scripts/`` (mirroring ``scripts/atlas_dag/``), imported by inserting
``scripts/`` onto ``sys.path`` -- see ``scripts/windows-conformance.py``.

Security posture (never violated by any probe in this package): no
administrator elevation, no registry/global-PATH/global-package mutation,
no writes outside a probe's own scratch root, no touching any process this
harness did not itself spawn, no persisted credentials, no raw
user-identifying paths in the receipt (normalized/relativized instead).
"""

from __future__ import annotations

SCHEMA_VERSION: int = 1

__all__ = ["SCHEMA_VERSION"]
