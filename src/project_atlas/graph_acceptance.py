"""AS-GRAPH-001 — Graphify artifact acceptance (derived-only).

Accepts inventory/manifest-backed Graphify artifacts under Core ownership.
Never writes claims, temporal state, authoritative state, or relationship
stores. Semantic promotion remains disabled until AS-GRAPH-003+.

Truth boundary: GRAPH RELATIONSHIP ≠ AUTOMATIC AUTHORITY (AS-GRAPH-INV-TRUTH-001).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from project_atlas.config import AtlasConfig, GraphifyConfig
from project_atlas.schema import SchemaValidationError, validate_record
from project_atlas.secrets import scan_text

SUPPORTED_SCHEMA = "graphify-1.0"
PACKAGE_ID = "AS-GRAPH-001"
AUTHORITY_LEVEL = "derived"
TRUTH_BOUNDARY = "GRAPH RELATIONSHIP ≠ AUTOMATIC AUTHORITY"

ArtifactFamily = Literal["envelope", "nodes", "edges", "metadata"]

GRAPHIFY_BASENAMES: dict[str, ArtifactFamily] = {
    "graph.json": "envelope",
    "nodes.json": "nodes",
    "nodes.jsonl": "nodes",
    "edges.json": "edges",
    "edges.jsonl": "edges",
    "metadata.json": "metadata",
    "metadata.yaml": "metadata",
    "metadata.yml": "metadata",
}


class GraphAcceptanceError(ValueError):
    """Fail-closed Graphify acceptance error (metadata-only message)."""


@dataclass(frozen=True)
class AcceptedArtifact:
    """One inventory-bound, schema-validated Graphify artifact."""

    artifact_id: str
    relative_path: str
    sha256: str
    family: ArtifactFamily
    authority_level: str
    node_count: int
    edge_count: int
    status: Literal["accepted", "rejected"]
    error_code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "artifact_id": self.artifact_id,
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "family": self.family,
            "authority_level": self.authority_level,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "status": self.status,
        }
        if self.error_code is not None:
            payload["error_code"] = self.error_code
        return payload


@dataclass(frozen=True)
class AcceptanceReceipt:
    """Deterministic acceptance report (no vault mutation)."""

    project_id: str
    artifacts: list[AcceptedArtifact]
    errors: list[dict[str, str]]
    semantic_enabled: bool
    semantic_status: Literal["disabled", "unsupported"]
    node_count: int
    edge_count: int

    @property
    def accepted_count(self) -> int:
        return sum(1 for item in self.artifacts if item.status == "accepted")

    @property
    def rejected_count(self) -> int:
        return sum(1 for item in self.artifacts if item.status == "rejected")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "package_id": PACKAGE_ID,
            "project_id": self.project_id,
            "authority": {
                "level": AUTHORITY_LEVEL,
                "note": "Graphify acceptance is derived-only; never domain-authoritative.",
            },
            "semantic_ingestion": {
                "enabled": self.semantic_enabled,
                "status": self.semantic_status,
            },
            "artifacts": [item.as_dict() for item in self.artifacts],
            "accepted_count": self.accepted_count,
            "rejected_count": self.rejected_count,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "errors": list(self.errors),
            "truth_boundary": TRUTH_BOUNDARY,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n"


def is_graphify_artifact_path(path: str) -> bool:
    """Return True when ``path`` basenames a documented Graphify family."""
    normalized = path.replace("\\", "/").rstrip("/")
    name = Path(normalized).name.lower()
    return name in GRAPHIFY_BASENAMES


def graphify_family_for_path(path: str) -> ArtifactFamily | None:
    """Return the artifact family for a documented Graphify basename."""
    normalized = path.replace("\\", "/").rstrip("/")
    return GRAPHIFY_BASENAMES.get(Path(normalized).name.lower())


def classify_graphify_document(path: str) -> str | None:
    """Return ``graphify-output`` for documented Graphify artifact paths."""
    if is_graphify_artifact_path(path):
        return "graphify-output"
    return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(relative: str, project_root: Path) -> Path:
    """Resolve ``relative`` under ``project_root`` or raise (AT-013)."""
    if not relative or relative.startswith(("/", "\\")) or "\\" in relative:
        raise GraphAcceptanceError(f"path-escape:{relative}")
    parts = Path(relative).parts
    if any(part == ".." for part in parts):
        raise GraphAcceptanceError(f"path-escape:{relative}")
    root = project_root.resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise GraphAcceptanceError(f"path-escape:{relative}")
    return candidate


def _inventory_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize Core ``sources`` or heritage ``documents`` inventory rows."""
    entries: list[dict[str, Any]] = []
    sources = manifest.get("sources")
    if isinstance(sources, list):
        for item in sources:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path", "")).replace("\\", "/")
            if not path:
                continue
            entries.append(
                {
                    "relative_path": path,
                    "sha256": item.get("sha256"),
                    "source_id": item.get("source_id"),
                    "classification": item.get("classification"),
                    "authority": item.get("authority"),
                }
            )
    documents = manifest.get("documents")
    if isinstance(documents, list):
        for item in documents:
            if not isinstance(item, dict):
                continue
            path = str(item.get("relative_path") or item.get("path") or "").replace("\\", "/")
            if not path:
                continue
            entries.append(
                {
                    "relative_path": path,
                    "sha256": item.get("sha256"),
                    "source_id": item.get("document_id") or item.get("source_id"),
                    "classification": item.get("classification"),
                    "authority": item.get("authority"),
                }
            )
    return entries


def _safe_project_token(token: str) -> str:
    """AS-SEC-SCAN-GRAPH-YAML-PROJECTID-001: decoded YAML/JSON project ids
    must not become graph write-path segments."""
    cleaned = token.strip()
    if scan_text(cleaned):
        raise GraphAcceptanceError("secret-content")
    return cleaned


def _project_id(manifest: dict[str, Any], project_root: Path) -> str:
    for key in ("project_id", "likely_project"):
        value = manifest.get(key)
        if isinstance(value, str) and value.strip():
            return _safe_project_token(value)
    marker = project_root / ".atlas-project.yaml"
    if marker.is_file():
        try:
            data = yaml.safe_load(marker.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            data = None
        if isinstance(data, dict):
            project = data.get("project")
            if isinstance(project, dict) and isinstance(project.get("id"), str):
                return _safe_project_token(str(project["id"]))
            if isinstance(data.get("id"), str):
                return _safe_project_token(str(data["id"]))
    return _safe_project_token(project_root.name)


def _graphify_config(config: AtlasConfig | GraphifyConfig | None) -> GraphifyConfig:
    if config is None:
        return GraphifyConfig()
    if isinstance(config, GraphifyConfig):
        return config
    return config.graphify


def _validate_schema_version(payload: dict[str, Any]) -> None:
    version = payload.get("schema_version")
    if version in (1, "1", "1.0", "graphify-1.0"):
        return
    if "nodes" in payload or "edges" in payload:
        # Heritage envelopes without explicit version are accepted as graphify-1.0
        # only when they already look like Graphify envelopes; unknown shapes fail.
        return
    raise GraphAcceptanceError("unknown-schema")


def _parse_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GraphAcceptanceError(f"malformed-jsonl:{index}") from exc
        if not isinstance(value, dict):
            raise GraphAcceptanceError(f"malformed-record:{index}")
        records.append(value)
    return records


def _json_safe(value: Any) -> Any:
    """Normalize YAML scalars (datetime/date) for JSON Schema validation."""
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _parse_artifact(path: Path, family: ArtifactFamily) -> tuple[int, int]:
    """Validate and count nodes/edges. Metadata always returns (0, 0)."""
    if family == "metadata":
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() in {".yaml", ".yml"}:
            try:
                payload = yaml.safe_load(text)
            except yaml.YAMLError as exc:
                raise GraphAcceptanceError("malformed-metadata") from exc
        else:
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise GraphAcceptanceError("malformed-json") from exc
        if not isinstance(payload, dict):
            raise GraphAcceptanceError("malformed-metadata")
        validate_record(_json_safe(payload), "graphify-metadata")
        return 0, 0

    if path.suffix.lower() == ".jsonl":
        records = _parse_jsonl(path)
        if family == "nodes":
            for item in records:
                validate_record(item, "graphify-node")
            return len(records), 0
        for item in records:
            validate_record(item, "graphify-edge")
        return 0, len(records)

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GraphAcceptanceError("malformed-json") from exc
    if not isinstance(payload, dict):
        raise GraphAcceptanceError("malformed-record")

    if family == "envelope":
        _validate_schema_version(payload)
        try:
            validate_record(payload, "graphify-envelope")
        except SchemaValidationError as exc:
            raise GraphAcceptanceError("unknown-schema") from exc
        nodes = [item for item in payload.get("nodes", []) if isinstance(item, dict)]
        edges = [item for item in payload.get("edges", []) if isinstance(item, dict)]
        for item in nodes:
            validate_record(item, "graphify-node")
        for item in edges:
            validate_record(item, "graphify-edge")
        return len(nodes), len(edges)

    if family == "nodes":
        if "nodes" in payload and isinstance(payload.get("nodes"), list):
            nodes = [item for item in payload["nodes"] if isinstance(item, dict)]
        else:
            nodes = [payload]
        for item in nodes:
            validate_record(item, "graphify-node")
        return len(nodes), 0

    if "edges" in payload and isinstance(payload.get("edges"), list):
        edges = [item for item in payload["edges"] if isinstance(item, dict)]
    else:
        edges = [payload]
    for item in edges:
        validate_record(item, "graphify-edge")
    return 0, len(edges)


def accept_graphify_artifacts(
    *,
    project_root: Path,
    manifest: dict[str, Any],
    config: AtlasConfig | GraphifyConfig | None = None,
    strict: bool = True,
) -> AcceptanceReceipt:
    """Accept inventory-backed Graphify artifacts (AS-GRAPH-001).

    Persistence choice: library-only. Returns a deterministic receipt and does
    not write claims, temporal/authoritative state, or relationship stores.
    """
    graphify = _graphify_config(config)
    project_root = project_root.expanduser().resolve()
    if not project_root.is_dir():
        raise GraphAcceptanceError(f"project-root-missing:{project_root}")

    project_id = _project_id(manifest, project_root)
    errors: list[dict[str, str]] = []
    artifacts: list[AcceptedArtifact] = []

    if not graphify.enabled:
        receipt = AcceptanceReceipt(
            project_id=project_id,
            artifacts=[],
            errors=[],
            semantic_enabled=False,
            semantic_status="disabled",
            node_count=0,
            edge_count=0,
        )
        validate_record(receipt.as_dict(), "graph-acceptance-receipt")
        return receipt

    if graphify.semantic_ingestion:
        # AS-GRAPH-001-FR-006: enabling the flag before AS-GRAPH-003 fails closed.
        raise GraphAcceptanceError("semantic_ingestion_unsupported")

    inventory_by_path = {
        str(entry["relative_path"]).replace("\\", "/"): entry
        for entry in _inventory_entries(manifest)
        if is_graphify_artifact_path(str(entry["relative_path"]))
    }

    for relative_path in sorted(inventory_by_path, key=str.casefold):
        family = graphify_family_for_path(relative_path)
        assert family is not None
        entry = inventory_by_path[relative_path]
        expected_hash = entry.get("sha256")
        artifact_id = f"{project_id}:{relative_path}"

        try:
            if not isinstance(expected_hash, str) or len(expected_hash) != 64:
                raise GraphAcceptanceError("missing-inventory-hash")
            path = _safe_relative(relative_path, project_root)
            if not path.is_file():
                raise GraphAcceptanceError("missing-file")
            actual = _sha256_file(path)
            if actual != expected_hash.lower():
                raise GraphAcceptanceError("hash-mismatch")
            # Authority must be derived when inventory carries an authority block.
            authority = entry.get("authority")
            level = authority.get("level") if isinstance(authority, dict) else None
            if level not in (None, AUTHORITY_LEVEL):
                raise GraphAcceptanceError("authority-not-derived")
            node_count, edge_count = _parse_artifact(path, family)
            artifacts.append(
                AcceptedArtifact(
                    artifact_id=artifact_id,
                    relative_path=relative_path,
                    sha256=actual,
                    family=family,
                    authority_level=AUTHORITY_LEVEL,
                    node_count=node_count,
                    edge_count=edge_count,
                    status="accepted",
                )
            )
        except (GraphAcceptanceError, SchemaValidationError, OSError) as exc:
            code = str(exc) if isinstance(exc, GraphAcceptanceError) else "schema-violation"
            if isinstance(exc, OSError):
                code = "io-error"
            message = f"{code}:{relative_path}"
            errors.append(
                {
                    "code": code.split(":", 1)[0],
                    "relative_path": relative_path,
                    "message": message,
                }
            )
            artifacts.append(
                AcceptedArtifact(
                    artifact_id=artifact_id,
                    relative_path=relative_path,
                    sha256=str(expected_hash).lower()
                    if isinstance(expected_hash, str) and len(str(expected_hash)) == 64
                    else "0" * 64,
                    family=family,
                    authority_level=AUTHORITY_LEVEL,
                    node_count=0,
                    edge_count=0,
                    status="rejected",
                    error_code=code.split(":", 1)[0],
                )
            )
            if strict:
                raise GraphAcceptanceError(message) from exc

    node_total = sum(item.node_count for item in artifacts if item.status == "accepted")
    edge_total = sum(item.edge_count for item in artifacts if item.status == "accepted")
    receipt = AcceptanceReceipt(
        project_id=project_id,
        artifacts=artifacts,
        errors=errors,
        semantic_enabled=False,
        semantic_status="disabled",
        node_count=node_total,
        edge_count=edge_total,
    )
    validate_record(receipt.as_dict(), "graph-acceptance-receipt")
    return receipt


def inspect_acceptance(receipt: AcceptanceReceipt) -> dict[str, Any]:
    """Library observability surface (FR-010): counts and disabled semantic status."""
    return {
        "package_id": PACKAGE_ID,
        "project_id": receipt.project_id,
        "accepted_count": receipt.accepted_count,
        "rejected_count": receipt.rejected_count,
        "node_count": receipt.node_count,
        "edge_count": receipt.edge_count,
        "authority_level": AUTHORITY_LEVEL,
        "semantic_ingestion": receipt.semantic_status,
        "artifact_ids": [
            item.artifact_id for item in receipt.artifacts if item.status == "accepted"
        ],
        "truth_boundary": TRUTH_BOUNDARY,
    }
