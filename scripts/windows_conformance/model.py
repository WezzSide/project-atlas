"""Result model for ATLAS_WINDOWS_CONFORMANCE_V1.

Every probe returns one :class:`ProbeOutcome`. The model deliberately keeps
three states distinct that are easy to collapse by accident:

  RESULT (PASS/FAIL/NOT_APPLICABLE/NOT_TESTED)
      whether the *conformance comparison* (observed platform behavior vs.
      Atlas's own contract) held -- never inferred from "the probe ran
      without raising".

  EPISTEMIC_STATE (OBSERVED/DERIVED/UNKNOWN)
      how confident the observation itself is: OBSERVED = measured directly
      this run; DERIVED = computed/inferred from other observations this
      run (e.g. a digest, a comparison of two observations); UNKNOWN = the
      probe could not establish the fact at all (missing capability,
      environment gap, permission denial) -- this is never silently
      promoted to a RESULT of PASS.

  ERROR (harness-internal failure)
      the probe itself crashed while trying to observe -- this is always a
      harness defect to fix in this mission, not a platform finding, and is
      surfaced separately from RESULT/EPISTEMIC_STATE so it can never be
      mistaken for a legitimate NOT_TESTED/UNKNOWN platform observation.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from dataclasses import dataclass, field
from typing import Any, Literal

ResultState = Literal["PASS", "FAIL", "NOT_APPLICABLE", "NOT_TESTED"]
EpistemicState = Literal["OBSERVED", "DERIVED", "UNKNOWN"]
Domain = Literal[
    "process_identity",
    "process_termination",
    "path_reality",
    "links_reparse_points",
    "atomic_io",
    "file_lock_semantics",
    "tool_resolution",
    "import_provenance",
    "git_worktree",
]

#: Evidence keys known to vary run-to-run for reasons unrelated to platform
#: conformance (PIDs, scratch paths, timings). Excluded from the digest
#: computed over "stable evidence" (mission requirement: exclude PID,
#: temp directory, wall-clock time from semantic-equality/digest input).
_VOLATILE_EVIDENCE_KEYS: frozenset[str] = frozenset(
    {
        "pid",
        "spawned_pid",
        "resolved_pid",
        "child_pid",
        "grandchild_pid",
        "parent_pid",
        "launcher_pid",
        "processid",
        "parentprocessid",
        "cleanup_marker",
        "tmp_path",
        "scratch_root",
        # Process-count fields reflect OS scheduling/termination timing
        # (how fast a just-killed process actually leaves the process
        # table), not a stable platform fact -- the stable claim worth
        # keeping is the derived "leak_free" boolean, which is itself
        # polled-until-stable, not these raw counts.
        "pre_spawn_count",
        # A concurrency race's incidental error list reflects true OS
        # thread-scheduling luck (whether two racing os.replace() calls
        # actually collided this run), not a stable platform fact -- the
        # stable claim is the derived "final_is_clean_single_writer".
        "writer_errors",
        "post_cleanup_count",
        "wall_ms",
        "elapsed_ms",
        "elapsed_sec",
        "timestamp",
        "at",
    }
)

#: Any occurrence of the OS temp directory (which random-suffixed scratch
#: roots live under, e.g. tempfile.mkdtemp()) inside a *string* value is
#: replaced with a fixed placeholder before hashing -- volatile scratch
#: paths otherwise leak into free-text fields (exception messages, human
#: observation summaries) that a simple key-based strip cannot reach.
# Matches the OS temp dir *plus* the one random-suffixed scratch-root
# segment every probe's tempfile.mkdtemp() creates directly under it (e.g.
# "...\Temp\atlas-conform-path-xy9hyw98") -- the random suffix, not just
# the fixed tempdir prefix, is what actually varies run to run.
#
# OSError's own __str__ renders an embedded filename through repr-style
# escaping, so the same path can appear in evidence either as single
# backslashes ("C:\Users\...") or doubled ("C:\\Users\\...", literally two
# backslash characters per separator) depending which code path produced
# the string -- match 1-or-2 backslashes at each separator so both forms
# redact identically.
_TMP_DIR_PATTERN = re.compile(
    r"\\{1,2}".join(re.escape(seg) for seg in tempfile.gettempdir().split("\\"))
    + r"(?:\\{1,2})[^\\/'\"]+",
    re.IGNORECASE,
)


def _redact_volatile_paths(text: str) -> str:
    return _TMP_DIR_PATTERN.sub("<TMPDIR>", text)


def _normalize_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """Strip volatile keys and redact volatile scratch paths (recursively)
    for stable-digest purposes only.

    The raw ``evidence`` in the receipt is never mutated -- this is used
    solely to compute :func:`stable_digest`.
    """

    def _walk(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                k: _walk(v)
                for k, v in sorted(value.items())
                if k.lower() not in _VOLATILE_EVIDENCE_KEYS
            }
        if isinstance(value, list):
            return [_walk(v) for v in value]
        if isinstance(value, str):
            return _redact_volatile_paths(value)
        return value

    normalized = _walk(evidence)
    assert isinstance(normalized, dict)
    return normalized


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    probe_id: str
    domain: Domain
    contract: str
    """The Atlas-side expectation being checked -- what *should* be true,
    independent of what the OS happens to do."""
    observation: str
    """Human-readable summary of what was actually observed this run."""
    result: ResultState
    epistemic_state: EpistemicState
    evidence: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    """Set only for a harness-internal failure (the probe itself crashed).
    Never used to represent a legitimate platform observation."""

    def stable_key(self) -> dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "domain": self.domain,
            "contract": self.contract,
            "result": self.result,
            "epistemic_state": self.epistemic_state,
            "evidence": _normalize_evidence(self.evidence),
            "error": self.error,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "domain": self.domain,
            "contract": self.contract,
            "observation": self.observation,
            "result": self.result,
            "epistemic_state": self.epistemic_state,
            "evidence": self.evidence,
            "error": self.error,
        }


def stable_digest(outcomes: list[ProbeOutcome]) -> str:
    """Digest over stable (non-volatile) fields, for repeat-run equivalence."""
    stable = [o.stable_key() for o in sorted(outcomes, key=lambda o: o.probe_id)]
    payload = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
