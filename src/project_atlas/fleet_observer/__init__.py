"""Read-only fleet observer.

``probe`` -> normalized internal observation -> public-safe projection.

Evidence collection only. It starts, stops, deploys, schedules and dispatches nothing, and it
never escalates: a datum it cannot read is reported as ``UNKNOWN``.

Connection details and host-side names come from an operator-provided configuration file that
is not part of the repository. The public projection never contains them.
"""

from __future__ import annotations

from project_atlas.fleet_observer.config import ConfigError, ObserverConfig, load_config
from project_atlas.fleet_observer.probe import (
    InstanceObservation,
    Runner,
    build_probe_script,
    observe,
    parse_probe_output,
)
from project_atlas.fleet_observer.projection import PublicLeakError, project_public

__all__ = [
    "ConfigError",
    "InstanceObservation",
    "ObserverConfig",
    "PublicLeakError",
    "Runner",
    "build_probe_script",
    "load_config",
    "observe",
    "parse_probe_output",
    "project_public",
]
