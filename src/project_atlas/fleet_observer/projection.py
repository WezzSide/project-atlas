"""Public-safe projection of internal observations (schema ``atlas-fleet-provenance-map/v1``).

Detail is reduced, never rounded upward: counters become state words, host-side names are
dropped, and a revision is published only when it is a commit on the reference branch.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Protocol

from project_atlas.fleet_observer.config import ComponentConfig, InstanceConfig, ObserverConfig
from project_atlas.fleet_observer.probe import InstanceObservation

SCHEMA = "atlas-fleet-provenance-map/v1"
HOST_SOURCE = "HOST_OBSERVATION"
REPO_SOURCE = "REPOSITORY_AT_MAIN"
CONFIG_SOURCE = "OPERATOR_DECLARATION"
RESTART_NOTE_THRESHOLD = 3
PROGRESS_WINDOW = timedelta(hours=24)

_SHA = re.compile(r"^[0-9a-f]{40}$")
_IPV4 = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")


class PublicLeakError(RuntimeError):
    """The projection would have published a host-side detail. Nothing is written."""


class Repository(Protocol):
    def main_revision(self) -> str: ...

    def is_on_main(self, revision: str) -> bool: ...

    def commits_since(self, revision: str, paths: tuple[str, ...]) -> list[str]: ...


def _deployment(observation: InstanceObservation | None) -> str:
    if observation is None or not observation.reachable:
        return "UNKNOWN"
    load = observation.unit_properties.get("LoadState")
    if load == "loaded":
        return "DEPLOYED"
    if load == "not-found":
        return "NOT_FOUND"
    return "UNKNOWN"


def _activity(observation: InstanceObservation | None, deployment: str) -> str:
    if observation is None or deployment != "DEPLOYED":
        return "UNKNOWN" if deployment == "UNKNOWN" else "NOT_APPLICABLE"
    active = observation.unit_properties.get("ActiveState")
    if active == "active":
        return "ACTIVE"
    if active in ("inactive", "failed"):
        unit_file = observation.unit_properties.get("UnitFileState")
        return "DISABLED" if unit_file in ("disabled", "masked") else "INACTIVE"
    return "UNKNOWN"


def _recent(value: Any, observed_at: datetime) -> bool:
    if not isinstance(value, str):
        return False
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if moment.tzinfo is None:
        return False
    return timedelta(0) <= observed_at - moment <= PROGRESS_WINDOW


def _health(observation: InstanceObservation | None, activity: str, observed_at: datetime) -> str:
    if observation is None or activity == "UNKNOWN":
        return "UNKNOWN"
    if activity != "ACTIVE":
        return "NOT_APPLICABLE"
    progress = observation.progress
    if not progress:
        return "UNKNOWN"
    failures = progress.get("consecutive_failures")
    if isinstance(failures, int) and not isinstance(failures, bool):
        if failures > 0:
            return "DEGRADED"
        if _recent(progress.get("last_success_at"), observed_at):
            return "PROGRESSING"
    return "UNKNOWN"


def _revision(observation: InstanceObservation | None, repository: Repository) -> str | None:
    if observation is None or not observation.release_target:
        return None
    candidate = observation.release_target.rstrip("/").rsplit("/", 1)[-1]
    if _SHA.match(candidate) and repository.is_on_main(candidate):
        return candidate
    return None


def _note(observation: InstanceObservation | None) -> str | None:
    if observation is None:
        return None
    if not observation.reachable:
        return "host not reached; nothing observed"
    notes: list[str] = []
    restarts = observation.unit_properties.get("NRestarts", "")
    if restarts.isdigit() and int(restarts) >= RESTART_NOTE_THRESHOLD:
        notes.append("restarted repeatedly; cause UNKNOWN")
    if observation.unit_properties.get("ActiveState") == "failed":
        notes.append("last run failed; cause UNKNOWN")
    return "; ".join(notes) or None


def _instance(
    component: ComponentConfig,
    instance: InstanceConfig,
    observation: InstanceObservation | None,
    repository: Repository,
    observed_at: datetime,
    on_fleet: bool,
) -> tuple[dict[str, Any], list[str]]:
    if instance.unit is None:
        unobserved = "UNKNOWN" if on_fleet else "NOT_APPLICABLE"
        return (
            {
                "host": instance.host,
                "deployment": unobserved,
                "activity": "UNKNOWN",
                "health": "UNKNOWN",
                "deployed_revision": None,
                "drift_from_main": unobserved,
                "runtime_identity": instance.runtime_identity,
            },
            [],
        )
    deployment = _deployment(observation)
    activity = _activity(observation, deployment)
    revision = _revision(observation, repository) if deployment == "DEPLOYED" else None
    behind: list[str] = []
    drift = "UNKNOWN"
    if revision is not None and component.paths:
        behind = repository.commits_since(revision, component.paths)
        drift = "BEHIND" if behind else "CURRENT"
    entry: dict[str, Any] = {
        "host": instance.host,
        "deployment": deployment,
        "activity": activity,
        "health": _health(observation, activity, observed_at),
        "deployed_revision": revision,
        "drift_from_main": drift,
        "runtime_identity": instance.runtime_identity,
    }
    note = _note(observation)
    if note:
        entry["note"] = note
    return entry, behind


def _component(
    component: ComponentConfig,
    observations: dict[tuple[str, str], InstanceObservation],
    repository: Repository,
    observed_at: datetime,
    fleet_hosts: frozenset[str],
) -> dict[str, Any]:
    instances: list[dict[str, Any]] = []
    behind: list[str] = []
    for instance in component.instances:
        entry, commits = _instance(
            component,
            instance,
            observations.get((component.component_id, instance.host)),
            repository,
            observed_at,
            instance.host in fleet_hosts,
        )
        instances.append(entry)
        behind.extend(commit for commit in commits if commit not in behind)
    deployed = [entry for entry in instances if entry["deployment"] == "DEPLOYED"]
    exact = bool(deployed) and all(entry["deployed_revision"] for entry in deployed)
    if not exact:
        for entry in instances:
            entry["deployed_revision"] = None
            if entry["drift_from_main"] not in ("NOT_APPLICABLE",):
                entry["drift_from_main"] = "UNKNOWN"
    if exact:
        confidence = "EXACT_REVISION"
    elif component.implementation == "NOT_IN_REPOSITORY" or deployed:
        confidence = "NONE"
    else:
        confidence = "NOT_APPLICABLE"
    repository_block: dict[str, Any] = {
        "implementation": component.implementation,
        "paths": list(component.paths),
    }
    if component.deploy_path:
        repository_block["deploy_path"] = component.deploy_path
    if component.implementation == "NOT_IN_REPOSITORY":
        repository_block["source_location"] = "UNKNOWN"
    observed = any(entry["deployment"] != "UNKNOWN" for entry in instances)
    evidence = [
        {"field": "repository", "source": CONFIG_SOURCE, "label": "RECORDED"},
        {"field": "integration", "source": CONFIG_SOURCE, "label": "RECORDED"},
        {"field": "live_validation", "source": CONFIG_SOURCE, "label": "RECORDED"},
        {
            "field": "instances",
            "source": HOST_SOURCE,
            "label": "OBSERVED" if observed else "UNKNOWN",
        },
        {
            "field": "deployed_revision",
            "source": REPO_SOURCE if exact else HOST_SOURCE,
            "label": "OBSERVED" if exact else "UNKNOWN",
        },
    ]
    result: dict[str, Any] = {
        "id": component.component_id,
        "expected_role": component.expected_role,
        "repository": repository_block,
        "integration": component.integration
        if component.implementation == "IMPLEMENTED"
        else "UNKNOWN",
        "live_validation": component.live_validation
        if component.implementation == "IMPLEMENTED"
        else "NOT_VALIDATED",
        "provenance_confidence": confidence,
        "instances": instances,
        "evidence": evidence,
    }
    if exact:
        result["drift"] = {
            "main_ahead_by_commits_in_paths": len(behind),
            "commits": [commit[:8] for commit in behind],
            "paths": list(component.paths),
        }
    return result


def project_public(
    config: ObserverConfig,
    observations: list[InstanceObservation],
    repository: Repository,
    observed_at: datetime,
) -> dict[str, Any]:
    """Build the public map and refuse to return it if any host-side detail leaked in."""
    index = {(item.component_id, item.host): item for item in observations}
    fleet_hosts = frozenset(host.host_id for host in config.hosts if host.alias)
    document: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "Generated public projection. Not canonical fleet state. Grants nothing. "
        "Unsigned; no publisher identity.",
        "observed_date_utc": observed_at.strftime("%Y-%m-%d"),
        "repository_identity": {
            "repository": config.repository,
            "main": repository.main_revision(),
        },
        "hosts": {
            host.host_id: {"role_id": host.role_id, "role": host.role} for host in config.hosts
        },
        "evidence_sources": {
            HOST_SOURCE: "Read-only host observation by the fleet observer; "
            "the full record is kept off the repository",
            REPO_SOURCE: "The repository at the main revision above",
            CONFIG_SOURCE: "Declared in the operator configuration; not observed by the observer",
        },
        "not_asserted": [
            "ATLAS_VPS_FLEET_AUTONOMOUS",
            "any PROVEN claim",
            "causality for any failure",
            "that any deployed component should be changed",
        ],
        "components": [
            _component(item, index, repository, observed_at, fleet_hosts)
            for item in config.components
        ],
    }
    text = json.dumps(document, sort_keys=True)
    if _IPV4.search(text):
        raise PublicLeakError("address-shaped value in projection")
    for private in config.private_strings():
        if private in text:
            raise PublicLeakError("configured host-side name in projection")
    return document
