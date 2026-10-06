"""Operator-provided observer configuration.

The file lives outside the repository. It is the only place that names a host alias, a unit
or a host path; everything in it is validated before it can reach a probe script.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
_UNIT = re.compile(r"^[A-Za-z0-9@_.:-]{1,128}$")
_PATH = re.compile(r"^/[A-Za-z0-9_./+-]{1,255}$")
_ID = re.compile(r"^[a-z0-9-]{1,64}$")
_HOST_ID = re.compile(r"^[A-Z0-9_]{1,32}$")

IMPLEMENTATION = ("IMPLEMENTED", "NOT_IN_REPOSITORY")
INTEGRATION = ("INTEGRATED", "NOT_INTEGRATED", "UNKNOWN")
LIVE_VALIDATION = ("RECORDED_SINGLE_HOST", "NOT_VALIDATED", "UNKNOWN")
RUNTIME_IDENTITY = ("DEDICATED", "SHARED", "NOT_APPLICABLE", "UNKNOWN")


class ConfigError(ValueError):
    """The operator configuration is malformed or unsafe to probe with."""


@dataclass(frozen=True)
class HostConfig:
    host_id: str
    alias: str | None
    role_id: str
    role: str


@dataclass(frozen=True)
class InstanceConfig:
    host: str
    unit: str | None
    release_link: str | None
    progress_file: str | None
    runtime_identity: str


@dataclass(frozen=True)
class ComponentConfig:
    component_id: str
    expected_role: str
    implementation: str
    paths: tuple[str, ...]
    deploy_path: str | None
    integration: str
    live_validation: str
    instances: tuple[InstanceConfig, ...]


@dataclass(frozen=True)
class ObserverConfig:
    repository: str
    hosts: tuple[HostConfig, ...]
    components: tuple[ComponentConfig, ...]

    def host(self, host_id: str) -> HostConfig:
        for host in self.hosts:
            if host.host_id == host_id:
                return host
        raise ConfigError(f"undeclared host: {host_id}")

    def private_strings(self) -> tuple[str, ...]:
        """Every string that must never appear in a public projection."""
        found: set[str] = set()
        for host in self.hosts:
            if host.alias:
                found.add(host.alias)
        for component in self.components:
            for instance in component.instances:
                for value in (instance.unit, instance.release_link, instance.progress_file):
                    if value:
                        found.add(value)
        return tuple(sorted(found))


def _text(node: dict[str, Any], key: str, where: str) -> str:
    value = node.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}: {key} must be a non-empty string")
    return value


def _optional(node: dict[str, Any], key: str, pattern: re.Pattern[str], where: str) -> str | None:
    value = node.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not pattern.match(value) or ".." in value:
        raise ConfigError(f"{where}: {key} is not a safe value")
    return value


def _choice(node: dict[str, Any], key: str, allowed: tuple[str, ...], where: str) -> str:
    value = _text(node, key, where)
    if value not in allowed:
        raise ConfigError(f"{where}: {key} must be one of {', '.join(allowed)}")
    return value


def _instance(node: Any, where: str) -> InstanceConfig:
    if not isinstance(node, dict):
        raise ConfigError(f"{where}: instance must be an object")
    host = _text(node, "host", where)
    if not _HOST_ID.match(host):
        raise ConfigError(f"{where}: host id is not valid")
    identity = node.get("runtime_identity", "UNKNOWN")
    if identity not in RUNTIME_IDENTITY:
        raise ConfigError(f"{where}: runtime_identity is not valid")
    unit = _optional(node, "unit", _UNIT, where)
    link = _optional(node, "release_link", _PATH, where)
    progress = _optional(node, "progress_file", _PATH, where)
    if unit is None and (link or progress):
        raise ConfigError(f"{where}: release_link and progress_file need a unit")
    return InstanceConfig(host, unit, link, progress, identity)


def _component(node: Any, index: int) -> ComponentConfig:
    where = f"components[{index}]"
    if not isinstance(node, dict):
        raise ConfigError(f"{where}: must be an object")
    component_id = _text(node, "id", where)
    if not _ID.match(component_id):
        raise ConfigError(f"{where}: id is not valid")
    repository = node.get("repository")
    if not isinstance(repository, dict):
        raise ConfigError(f"{where}: repository must be an object")
    implementation = _choice(repository, "implementation", IMPLEMENTATION, where)
    raw_paths = repository.get("paths", [])
    if not isinstance(raw_paths, list) or not all(isinstance(p, str) for p in raw_paths):
        raise ConfigError(f"{where}: repository.paths must be a list of strings")
    paths = tuple(str(p) for p in raw_paths)
    if (implementation == "IMPLEMENTED") != bool(paths):
        raise ConfigError(f"{where}: paths are required exactly when IMPLEMENTED")
    deploy_path = repository.get("deploy_path")
    if deploy_path is not None and not isinstance(deploy_path, str):
        raise ConfigError(f"{where}: repository.deploy_path must be a string")
    raw_instances = node.get("instances")
    if not isinstance(raw_instances, list) or not raw_instances:
        raise ConfigError(f"{where}: at least one instance is required")
    instances = tuple(
        _instance(item, f"{where}.instances[{n}]") for n, item in enumerate(raw_instances)
    )
    return ComponentConfig(
        component_id=component_id,
        expected_role=_text(node, "expected_role", where),
        implementation=implementation,
        paths=paths,
        deploy_path=deploy_path,
        integration=_choice(node, "integration", INTEGRATION, where),
        live_validation=_choice(node, "live_validation", LIVE_VALIDATION, where),
        instances=instances,
    )


def parse_config(data: Any) -> ObserverConfig:
    if not isinstance(data, dict):
        raise ConfigError("configuration must be an object")
    raw_hosts = data.get("hosts")
    if not isinstance(raw_hosts, dict) or not raw_hosts:
        raise ConfigError("hosts must be a non-empty object")
    hosts: list[HostConfig] = []
    for host_id, node in raw_hosts.items():
        where = f"hosts.{host_id}"
        if (
            not isinstance(host_id, str)
            or not _HOST_ID.match(host_id)
            or not isinstance(node, dict)
        ):
            raise ConfigError(f"{where}: not a valid host entry")
        alias = _optional(node, "alias", _ALIAS, where)
        hosts.append(
            HostConfig(host_id, alias, _text(node, "role_id", where), _text(node, "role", where))
        )
    raw_components = data.get("components")
    if not isinstance(raw_components, list) or not raw_components:
        raise ConfigError("components must be a non-empty list")
    components = tuple(_component(node, n) for n, node in enumerate(raw_components))
    ids = [component.component_id for component in components]
    if len(ids) != len(set(ids)):
        raise ConfigError("component ids must be unique")
    config = ObserverConfig(_text(data, "repository", "configuration"), tuple(hosts), components)
    for component in components:
        for instance in component.instances:
            host = config.host(instance.host)
            if instance.unit is not None and host.alias is None:
                raise ConfigError(
                    f"{component.component_id}: {instance.host} has a unit but no alias"
                )
    return config


def load_config(path: Path) -> ObserverConfig:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read configuration: {exc.__class__.__name__}") from exc
    return parse_config(data)
