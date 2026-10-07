"""Validated, deterministic event-package boundary for Atlas Core."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from atlas_contracts.agent_event import AgentEvent, SkillBinding, VaultIdentity
from atlas_contracts.identity import safe_relative_component
from atlas_contracts.paths import safe_relative_path
from atlas_contracts.provenance import ProvenanceRecord
from atlas_contracts.receipts import PipelineState, ReceiptReference
from atlas_contracts.versions import ID_PATTERN

EVENT_PACKAGE_FILES = frozenset({"event.md", "event.json", "provenance.json", "receipt.yaml"})


class PackageValidationError(ValueError):
    """Raised when a package cannot safely enter the Core ingestion boundary."""


class AgentEventEnvelope(BaseModel):
    """The signed-by-reference event JSON contract."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    event: AgentEvent
    skill: SkillBinding
    vault: VaultIdentity
    provenance: ProvenanceRecord
    pipeline: PipelineState
    receipt: ReceiptReference


class EventPackage(BaseModel):
    """A fully validated event package and its confined source location."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(pattern=ID_PATTERN)
    event_id: str = Field(min_length=1)
    package_path: str = Field(min_length=1)
    envelope: AgentEventEnvelope
    receipt: ReceiptReference
    component_sha256: dict[str, str]


class EventPackageInventory(BaseModel):
    """Discovery record; invalid packages remain visible for quarantine."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    package_path: str = Field(min_length=1)
    classification: str = Field(min_length=1)
    status: Literal["valid", "pending", "incomplete", "invalid", "conflicting"]
    component_sha256: dict[str, str] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


def _confined(root: Path, relative: str) -> Path:
    """Join ``relative`` under ``root`` using canonical SEC-004/018 path rules.

    SEC-SCAN-A-002: must reject the same forms ``safe_relative_path`` rejects
    (drive-relative ``C:…``, ADS/colon segments, absolutes, ``..``, etc.).
    """
    try:
        segments = safe_relative_path(relative, label="event package path")
    except ValueError as exc:
        raise PackageValidationError(f"unsafe event package path: {relative!r}") from exc
    candidate = root.joinpath(*segments).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise PackageValidationError(f"event package escapes source root: {relative}") from exc
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: object) -> str:
    content = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _reject_symlinked_package_directory(root: Path, package_path: str) -> None:
    """Make the module's existing symlinked-package refusal actually fire.

    The check below it inspects the path `_confined` has already resolved, so
    `is_symlink()` there is always False and its error string is unreachable.
    Asking before resolution is what makes the answer about the directory
    rather than about its target. Pure repair: the verdict this restores is
    the one the module already declares, and `discovery.py` already enforces
    the same shape at its own call site.
    """
    try:
        segments = safe_relative_path(package_path, label="event package path")
    except ValueError:
        return  # `_confined` raises the canonical error for this a moment later
    if root.joinpath(*segments).is_symlink():
        raise PackageValidationError("event package directory is missing or symlinked")


def _reject_symlinked_components(package: Path) -> None:
    """Refuse a package whose component files are symbolic links.

    ``_confined`` guarantees the package *directory* is inside the source
    root, and the check above intends to refuse a symlinked package -- but it
    inspects an already-resolved path, so it never fires, and confinement of
    the directory says nothing about where its contents point. A component
    symlink therefore made an arbitrary file outside the root readable as
    event evidence: the recorded component hash was the outside file's hash,
    and the bytes copied into the Vault were the outside file's bytes.

    Detected with ``lstat`` on the unresolved component path, which is what
    makes the answer about the entry rather than about its target. Refusing is
    the same verdict this module already states for a symlinked package; a
    component is simply the case the original check could not see.
    """
    symlinked = sorted(
        name for name in EVENT_PACKAGE_FILES if (package / name).is_symlink()
    )
    if symlinked:
        raise PackageValidationError(
            f"event package component is symlinked: {','.join(symlinked)}"
        )


def _raw_inventory(root: Path, package_path: str) -> tuple[Path, dict[str, str]]:
    _reject_symlinked_package_directory(root, package_path)
    package = _confined(root, package_path)
    if not package.is_dir() or package.is_symlink():
        raise PackageValidationError("event package directory is missing or symlinked")
    _reject_symlinked_components(package)
    names = {path.name for path in package.iterdir()}
    if names != EVENT_PACKAGE_FILES:
        missing = sorted(EVENT_PACKAGE_FILES - names)
        extra = sorted(names - EVENT_PACKAGE_FILES)
        raise PackageValidationError(f"package structure invalid; missing={missing}, extra={extra}")
    hashes = {name: _sha256(package / name) for name in sorted(EVENT_PACKAGE_FILES)}
    return package, hashes


def _load_envelope(package: Path) -> AgentEventEnvelope:
    try:
        raw = json.loads((package / "event.json").read_text(encoding="utf-8"))
        return AgentEventEnvelope.model_validate(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise PackageValidationError(f"event.json invalid: {exc}") from exc


def _load_provenance(package: Path) -> ProvenanceRecord:
    try:
        raw = json.loads((package / "provenance.json").read_text(encoding="utf-8"))
        return ProvenanceRecord.model_validate(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise PackageValidationError(f"provenance.json invalid: {exc}") from exc


def _load_receipt(package: Path) -> ReceiptReference:
    try:
        raw = yaml.safe_load((package / "receipt.yaml").read_text(encoding="utf-8"))
        return ReceiptReference.model_validate(raw)
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError, TypeError) as exc:
        raise PackageValidationError(f"receipt.yaml invalid: {exc}") from exc


def load_event_package(
    root: Path, project_id: str, event_id: str, package_path: str
) -> EventPackage:
    """Load and independently verify one package at the Core trust boundary."""
    safe_relative_component(project_id, label="project_id")
    safe_relative_component(event_id, label="event_id")
    package, hashes = _raw_inventory(root, package_path)
    envelope = _load_envelope(package)
    provenance = _load_provenance(package)
    receipt = _load_receipt(package)
    if envelope.event.project_id != project_id or envelope.event.event_id != event_id:
        raise PackageValidationError("event identity does not match package identity")
    if envelope.receipt != receipt or envelope.provenance != provenance:
        raise PackageValidationError("event.json references do not match package metadata")
    if receipt.event_id != event_id or receipt.status != "valid":
        raise PackageValidationError("receipt is not a valid reference for this event")
    if not envelope.pipeline.is_verified():
        raise PackageValidationError("event pipeline is not fully verified")
    if provenance.content_sha256 != hashes["event.md"]:
        raise PackageValidationError("event.md hash mismatch")
    event_json = json.loads((package / "event.json").read_text(encoding="utf-8"))
    if not isinstance(event_json, dict) or provenance.normalized_sha256 != _canonical_hash(
        event_json.get("event")
    ):
        raise PackageValidationError("normalized event hash mismatch")
    return EventPackage(
        project_id=project_id,
        event_id=event_id,
        package_path=package_path,
        envelope=envelope,
        receipt=receipt,
        component_sha256=hashes,
    )


def inspect_event_package(
    root: Path, project_id: str, event_id: str, package_path: str
) -> EventPackageInventory:
    """Inventory a package without promoting unverified data to final evidence."""
    try:
        package = load_event_package(root, project_id, event_id, package_path)
    except (PackageValidationError, ValueError) as exc:
        status: Literal["valid", "pending", "incomplete", "invalid", "conflicting"] = "invalid"
        try:
            candidate = _confined(root, package_path)
            if candidate.is_dir() and (candidate / "event.json").is_file():
                raw: Any = json.loads((candidate / "event.json").read_text(encoding="utf-8"))
                pipeline = raw.get("pipeline", {}) if isinstance(raw, dict) else {}
                if not all(
                    pipeline.get(key) is True
                    for key in ("captured", "normalized", "verified", "routed")
                ):
                    status = "pending"
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            TypeError,
            AttributeError,
            PackageValidationError,
            ValueError,
        ):
            pass
        return EventPackageInventory(
            project_id=project_id,
            event_id=event_id,
            package_path=package_path,
            classification="agent-event",
            status=status,
            errors=[str(exc)],
        )
    return EventPackageInventory(
        project_id=package.project_id,
        event_id=package.event_id,
        package_path=package.package_path,
        classification=f"agent-{package.envelope.event.event_type.value}",
        status="valid",
        component_sha256=package.component_sha256,
    )
