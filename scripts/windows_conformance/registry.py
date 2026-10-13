"""Aggregates every domain's probe list into one ordered registry."""

from __future__ import annotations

from collections.abc import Callable

from windows_conformance import probes_git, probes_io, probes_links, probes_path, probes_process, probes_tooling
from windows_conformance.model import ProbeOutcome

ProbeFn = Callable[[], ProbeOutcome]

ALL_PROBES: list[ProbeFn] = [
    *probes_process.PROBES,
    *probes_path.PROBES,
    *probes_links.PROBES,
    *probes_io.PROBES,
    *probes_tooling.PROBES,
    *probes_git.PROBES,
]


def probe_ids() -> list[str]:
    """IDs without executing anything -- used by structural/meta tests."""
    seen: list[str] = []
    for fn in ALL_PROBES:
        seen.append(fn.__name__)
    return seen
