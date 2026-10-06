"""Read-only probe: script construction, execution boundary and parsing.

The probe script is assembled from three fixed command templates. Nothing else can appear in
it, and every interpolated value was validated by :mod:`project_atlas.fleet_observer.config`.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from project_atlas.fleet_observer.config import InstanceConfig, ObserverConfig

# (host alias, probe script) -> probe output. Raises OSError / TimeoutError when unreachable.
Runner = Callable[[str, str], str]

_UNIT_PROPERTIES = ("LoadState", "ActiveState", "SubState", "UnitFileState", "NRestarts")
_PROGRESS_KEYS = ("status", "consecutive_failures", "last_success_at")
_MARK = "@@atlas-observer"


@dataclass(frozen=True)
class InstanceObservation:
    """Precise local evidence for one configured instance. Never published as is."""

    component_id: str
    host: str
    reachable: bool
    unit_properties: dict[str, str] = field(default_factory=dict)
    release_target: str | None = None
    progress: dict[str, Any] | None = None

    def to_raw(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "host": self.host,
            "reachable": self.reachable,
            "unit_properties": dict(self.unit_properties),
            "release_target": self.release_target,
            "progress": self.progress,
        }


def _targets(config: ObserverConfig, host_id: str) -> list[tuple[str, InstanceConfig]]:
    return [
        (component.component_id, instance)
        for component in config.components
        for instance in component.instances
        if instance.host == host_id and instance.unit is not None
    ]


def build_probe_script(config: ObserverConfig, host_id: str) -> str:
    """The complete script sent to one host. Three read-only templates, nothing else."""
    lines: list[str] = []
    properties = " ".join(f"-p {name}" for name in _UNIT_PROPERTIES)
    for index, (_, instance) in enumerate(_targets(config, host_id)):
        lines.append(f"echo '{_MARK} {index} unit'")
        lines.append(f"systemctl show {properties} -- '{instance.unit}' 2>/dev/null || true")
        if instance.release_link:
            lines.append(f"echo '{_MARK} {index} link'")
            lines.append(f"readlink -- '{instance.release_link}' 2>/dev/null || true")
        if instance.progress_file:
            lines.append(f"echo '{_MARK} {index} progress'")
            lines.append(f"cat -- '{instance.progress_file}' 2>/dev/null || true")
    lines.append(f"echo '{_MARK} end'")
    return "\n".join(lines) + "\n"


def _progress(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return {key: data[key] for key in _PROGRESS_KEYS if key in data}


def parse_probe_output(
    config: ObserverConfig, host_id: str, output: str
) -> list[InstanceObservation]:
    """Turn one host's probe output into observations. Missing sections stay empty."""
    targets = _targets(config, host_id)
    sections: dict[tuple[int, str], list[str]] = {}
    current: tuple[int, str] | None = None
    complete = False
    for line in output.splitlines():
        if line.startswith(_MARK):
            parts = line.split()
            if len(parts) == 2 and parts[1] == "end":
                complete = True
                current = None
            elif len(parts) == 3 and parts[1].isdigit():
                current = (int(parts[1]), parts[2])
                sections[current] = []
            else:
                current = None
        elif current is not None:
            sections[current].append(line)
    observations: list[InstanceObservation] = []
    for index, (component_id, _) in enumerate(targets):
        properties: dict[str, str] = {}
        for line in sections.get((index, "unit"), []):
            name, separator, value = line.partition("=")
            if separator and name in _UNIT_PROPERTIES:
                properties[name] = value.strip()
        link_lines = [line.strip() for line in sections.get((index, "link"), []) if line.strip()]
        progress_text = "\n".join(sections.get((index, "progress"), []))
        observations.append(
            InstanceObservation(
                component_id=component_id,
                host=host_id,
                reachable=complete,
                unit_properties=properties if complete else {},
                release_target=link_lines[0] if complete and link_lines else None,
                progress=_progress(progress_text) if complete and progress_text.strip() else None,
            )
        )
    return observations


def observe(config: ObserverConfig, runner: Runner) -> list[InstanceObservation]:
    """Probe every host that has something to observe. An unreachable host yields UNKNOWN."""
    observations: list[InstanceObservation] = []
    for host in config.hosts:
        targets = _targets(config, host.host_id)
        if not targets or host.alias is None:
            continue
        try:
            output = runner(host.alias, build_probe_script(config, host.host_id))
        except (OSError, TimeoutError, subprocess.SubprocessError):
            observations.extend(
                InstanceObservation(component_id, host.host_id, reachable=False)
                for component_id, _ in targets
            )
            continue
        observations.extend(parse_probe_output(config, host.host_id, output))
    return observations


def ssh_runner(alias: str, script: str, *, timeout: float = 30.0) -> str:
    """Run the probe through the local SSH client's own configuration for ``alias``.

    Non-interactive, no agent or credential handling here, no remote command other than a
    shell reading the probe script from standard input.
    """
    completed = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--", alias, "bash", "-s"],
        input=script.encode("utf-8"),
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    output = completed.stdout.decode("utf-8", errors="replace")
    if completed.returncode != 0 and _MARK not in output:
        raise OSError("probe did not run")
    return output
