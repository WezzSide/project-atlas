"""Fleet provenance map (2026-10-06): the public projection is schema-valid, internally
consistent, bound to repository paths that exist, and carries no host access detail."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs" / "global" / "baseline" / "evidence"
MAP = EVIDENCE / "fleet-provenance-map-2026-10-06.json"
SCHEMA = EVIDENCE / "fleet-provenance-map.schema.json"
RECORD = ROOT / "docs" / "global" / "baseline" / "2026-10-06-FLEET-PROVENANCE-MAP.md"

_SHA = re.compile(r"^[0-9a-f]{40}$")
_IPV4 = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PORT = re.compile(r"\bports?\s+\d", re.I)
_NOT_PUBLIC = (
    "D:\\",
    "/mnt/",
    "/home/",
    "/etc/",
    "/opt/",
    "/var/",
    "/usr/",
    ".service",
    "sudo",
    "ssh",
    "fingerprint",
    "password",
    "tailnet",
)


def _load() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(MAP.read_text(encoding="utf-8"))
    return data


def _components() -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = _load()["components"]
    return components


def test_map_is_valid_against_its_schema() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(_load())


def test_component_ids_are_unique_and_hosts_are_declared() -> None:
    data = _load()
    ids = [component["id"] for component in data["components"]]
    assert len(ids) == len(set(ids))
    declared = set(data["hosts"]) | {"GITHUB_HOSTED"}
    for component in data["components"]:
        for instance in component["instances"]:
            assert instance["host"] in declared, component["id"]


def test_implemented_components_name_paths_that_exist() -> None:
    for component in _components():
        repository = component["repository"]
        if repository["implementation"] == "IMPLEMENTED":
            assert repository["paths"], component["id"]
            for path in (*repository["paths"], *filter(None, [repository.get("deploy_path")])):
                assert (ROOT / path).exists(), f"{component['id']}: {path}"
        else:
            assert repository["paths"] == [], component["id"]
            assert component["provenance_confidence"] == "NONE", component["id"]


def test_a_revision_is_claimed_only_with_exact_provenance() -> None:
    for component in _components():
        exact = component["provenance_confidence"] == "EXACT_REVISION"
        for instance in component["instances"]:
            revision = instance["deployed_revision"]
            if exact and instance["deployment"] == "DEPLOYED":
                assert revision is not None and _SHA.match(revision), component["id"]
            else:
                assert revision is None, component["id"]
            if revision is None:
                assert instance["drift_from_main"] in ("UNKNOWN", "NOT_APPLICABLE"), component["id"]
        if not exact:
            assert "drift" not in component, component["id"]


def test_state_is_not_rounded_upward() -> None:
    for component in _components():
        for instance in component["instances"]:
            if instance["deployment"] != "DEPLOYED":
                assert instance["activity"] != "ACTIVE", component["id"]
            if instance["activity"] != "ACTIVE":
                assert instance["health"] != "PROGRESSING", component["id"]
        if component["repository"]["implementation"] == "NOT_IN_REPOSITORY":
            assert component["integration"] == "UNKNOWN", component["id"]
            assert component["live_validation"] == "NOT_VALIDATED", component["id"]


def test_every_evidence_entry_names_a_declared_source() -> None:
    data = _load()
    for component in data["components"]:
        for entry in component["evidence"]:
            assert entry["source"] in data["evidence_sources"], component["id"]


def test_nothing_is_asserted_as_proven() -> None:
    text = MAP.read_text(encoding="utf-8")
    assert '"PROVEN"' not in text
    assert "ATLAS_VPS_FLEET_AUTONOMOUS" in _load()["not_asserted"]


def test_public_projection_carries_no_host_access_detail() -> None:
    for path in (MAP, RECORD):
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        assert not _IPV4.search(text), path.name
        assert not _EMAIL.search(text), path.name
        assert not _PORT.search(text), path.name
        for token in _NOT_PUBLIC:
            assert token.lower() not in lowered, f"{path.name}: {token!r}"
