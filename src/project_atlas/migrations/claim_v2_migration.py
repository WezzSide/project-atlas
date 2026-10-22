"""Claim Identity v2 migration (AS-CORE-003).

Recompile historical v1 claim identities from governed source evidence, map
them to durable v2 identities, and commit each project's alias map and audit
receipt as one atomic bundle. Ambiguous mappings remain explicit.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from atlas_contracts.identity import safe_relative_component
from project_atlas.claim_identity import (
    _digest,
    canonical_identity_key,
    claim_id_from_key,
    extract_claims,
)
from project_atlas.schema import validate_record
from project_atlas.source_identity import ProjectIdentityLock

_TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".html"}


def _run_git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout


def _inside(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"migration path escapes Vault root: {candidate}") from exc
    return resolved


def _v1_claim_id(
    project_identity: str,
    source_identity: str,
    claim_type: str,
    field: str,
    value: str,
) -> str:
    """Reproduce the merge-base v1 identity formula exactly."""
    normalized = " ".join(value.split()).lower()
    key = (
        f"{project_identity}|{source_identity}|{claim_type}|{field}|"
        f"{_digest(normalized)}"
    )
    return f"claim-{_digest(key)[:20]}"


def _v2_claim_id(
    project_identity: str,
    source_lineage_id: str,
    claim_type: str,
    normalized_field: str,
    stable_semantic_locator: str,
) -> str:
    identity_key = canonical_identity_key(
        project_identity,
        source_lineage_id,
        claim_type,
        normalized_field,
        stable_semantic_locator,
    )
    return claim_id_from_key(identity_key)


@dataclass(frozen=True)
class _SourceMetadata:
    source_id: str
    source_lineage_id: str
    project_identity: str
    original_path: str
    classification: str


@dataclass(frozen=True)
class _Candidate:
    v1_claim_id: str
    v2_claim_id: str
    project_identity: str
    source_lineage_id: str
    claim_type: str
    field: str
    stable_semantic_locator: str
    source_commit: str
    source_path: str


def _json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _load_source_metadata(vault_root: Path, project_id: str) -> dict[str, _SourceMetadata]:
    registry = _json_object(vault_root / "state" / "sources.json")
    raw_registry = registry.get("sources", [])
    registry_records = [item for item in raw_registry if isinstance(item, dict)]

    manifest = _json_object(
        vault_root / "sources" / "manifests" / "source-manifest.json"
    )
    raw_manifest = manifest.get("sources", [])
    manifest_records = [item for item in raw_manifest if isinstance(item, dict)]
    manifest_by_id = {
        str(item["source_id"]): item
        for item in manifest_records
        if item.get("source_id")
    }

    classifications = _json_object(
        vault_root / "generated" / "reports" / "ingestion-report.json"
    ).get("classifications", {})
    if not isinstance(classifications, dict):
        classifications = {}

    requested_source_ids = {
        str(item["source_id"])
        for item in manifest_records
        if item.get("likely_project") == project_id and item.get("source_id")
    }
    requested_uuids = {
        str(item["project_uuid"])
        for item in manifest_records
        if item.get("likely_project") == project_id and item.get("project_uuid")
    }
    requested_uuids.update(
        str(item["canonical_project_id"])
        for item in registry_records
        if item.get("source_id") in requested_source_ids
        and item.get("canonical_project_id")
    )
    if len(requested_uuids) > 1:
        raise ValueError(f"project has multiple canonical identities: {project_id}")

    registry_uuids = {
        str(item["canonical_project_id"])
        for item in registry_records
        if item.get("canonical_project_id")
    }
    selected_uuid = next(iter(requested_uuids), None)
    if selected_uuid is None and project_id in registry_uuids:
        selected_uuid = project_id
    if selected_uuid is None and registry_uuids:
        # Do not adopt the sole remaining registry UUID when --project
        # matches no manifest row. That writes aliases for a foreign
        # project identity under the requested id.
        raise ValueError(
            f"cannot resolve project identity from source evidence: {project_id}"
        )

    metadata: dict[str, _SourceMetadata] = {}
    for record in registry_records:
        source_id = str(record.get("source_id", ""))
        if not source_id:
            continue
        record_uuid = str(record.get("canonical_project_id", "")) or None
        if selected_uuid is not None and record_uuid not in {None, selected_uuid}:
            continue
        manifest_record = manifest_by_id.get(source_id, {})
        likely_project = manifest_record.get("likely_project")
        if selected_uuid is None and likely_project and likely_project != project_id:
            continue
        project_identity = (
            selected_uuid
            or record_uuid
            or str(manifest_record.get("project_uuid", ""))
            or project_id
        )
        source_lineage_id = str(record.get("source_lineage_id", "")) or source_id
        original_path = (
            str(record.get("current_path", ""))
            or str(manifest_record.get("path", ""))
            or source_id
        )
        classification_record = classifications.get(source_id, {})
        classification = (
            str(classification_record.get("type", ""))
            if isinstance(classification_record, dict)
            else ""
        )
        item = _SourceMetadata(
            source_id=source_id,
            source_lineage_id=source_lineage_id,
            project_identity=project_identity,
            original_path=original_path,
            classification=classification,
        )
        metadata[source_id] = item
        for historical in record.get("path_history", []) or []:
            if isinstance(historical, dict) and historical.get("path"):
                metadata[str(historical["path"])] = item
        for path_key in (record.get("current_path"), record.get("first_seen_path")):
            if path_key:
                metadata[str(path_key)] = item

    return metadata


def _source_id_from_evidence_path(file_path: str) -> str:
    return Path(file_path).stem


def _classification(metadata: _SourceMetadata, file_path: str, content: str) -> str:
    if metadata.classification:
        return metadata.classification
    haystack = f"{metadata.original_path}\n{file_path}\n{content[:4000]}".lower()
    if "architecture" in haystack or "design" in haystack:
        return "architecture"
    return "unknown"


def _extract_candidates(
    project_id: str,
    source_metadata: dict[str, _SourceMetadata],
    commit: str,
    file_path: str,
    content: str,
) -> list[_Candidate]:
    metadata = source_metadata.get(file_path) or source_metadata.get(
        _source_id_from_evidence_path(file_path)
    )
    if metadata is None:
        return []

    is_project_manifest = metadata.original_path.endswith(".atlas-project.yaml")
    candidates: list[_Candidate] = []
    for claim in extract_claims(
        content,
        is_project_manifest=is_project_manifest,
        classification=_classification(metadata, file_path, content),
        reject_unresolved=True,
    ):
        claim_type = str(claim["claim_type"])
        field = str(claim["field"])
        v1_id = _v1_claim_id(
            metadata.project_identity,
            metadata.source_lineage_id,
            claim_type,
            field,
            str(claim["legacy_value"]),
        )
        v2_id = _v2_claim_id(
            metadata.project_identity,
            metadata.source_lineage_id,
            claim_type,
            field,
            str(claim["locator"]),
        )
        candidates.append(
            _Candidate(
                v1_claim_id=v1_id,
                v2_claim_id=v2_id,
                project_identity=metadata.project_identity,
                source_lineage_id=metadata.source_lineage_id,
                claim_type=claim_type,
                field=field,
                stable_semantic_locator=str(claim["locator"]),
                source_commit=commit,
                source_path=file_path,
            )
        )
    return candidates


def _scan_historical_sources(
    vault_root: Path, source_metadata: dict[str, _SourceMetadata]
) -> tuple[list[str], list[tuple[str, str, str]]]:
    """Return governed text evidence from every source-bearing Git commit."""
    try:
        commits = _run_git(
            ["rev-list", "--all", "--", "sources/"], cwd=vault_root
        ).splitlines()
    except subprocess.CalledProcessError:
        return [], []

    records: list[tuple[str, str, str]] = []
    for commit in commits:
        try:
            files = _run_git(
                ["ls-tree", "-r", "--name-only", commit, "sources/"],
                cwd=vault_root,
            ).splitlines()
        except subprocess.CalledProcessError:
            continue
        for file_path in files:
            if Path(file_path).suffix.lower() not in _TEXT_SUFFIXES:
                continue
            source_id = _source_id_from_evidence_path(file_path)
            if file_path not in source_metadata and source_id not in source_metadata:
                continue
            try:
                content = _run_git(["show", f"{commit}:{file_path}"], cwd=vault_root)
            except subprocess.CalledProcessError:
                continue
            records.append((commit, file_path, content))
    return commits, records


def _candidate_to_record(candidate: _Candidate) -> dict[str, Any]:
    return {
        "v1_claim_id": candidate.v1_claim_id,
        "v2_claim_id": candidate.v2_claim_id,
        "project_identity": candidate.project_identity,
        "source_lineage_id": candidate.source_lineage_id,
        "claim_type": candidate.claim_type,
        "field": candidate.field,
        "stable_semantic_locator": candidate.stable_semantic_locator,
        "source_commit": candidate.source_commit,
        "source_path": candidate.source_path,
    }


def _validate_alias_payload(payload: dict[str, Any], project_id: str) -> None:
    validate_record(payload, "claim-alias")
    if payload.get("project_id") != project_id:
        raise ValueError("claim alias map belongs to a different project")
    resolved = {str(item["v1_claim_id"]) for item in payload["aliases"]}
    ambiguous = {str(item["v1_claim_id"]) for item in payload["ambiguous"]}
    overlap = resolved & ambiguous
    if overlap:
        raise ValueError(
            "claim alias map contains resolved/ambiguous overlap: "
            + ", ".join(sorted(overlap))
        )
    audit = payload["audit"]
    if int(audit["output_aliases"]) != len(payload["aliases"]):
        raise ValueError("claim alias audit count does not match alias records")


def _state_hash(payload: dict[str, Any]) -> str:
    return _digest(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _receipt_data(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "receipt_type": "v2-identity-migration",
        "project_id": payload["project_id"],
        "migrated_claims": len(payload["aliases"]),
        "ambiguous_count": len(payload["ambiguous"]),
        "state_sha256": _state_hash(payload),
    }


def _validate_receipt(
    receipt: dict[str, Any], payload: dict[str, Any], project_id: str
) -> None:
    expected = _receipt_data(payload)
    if receipt != expected or receipt.get("project_id") != project_id:
        raise ValueError("claim identity migration receipt does not match alias state")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _load_existing_bundle(
    alias_path: Path, receipt_path: Path, project_id: str
) -> dict[str, Any] | None:
    if not alias_path.exists() and not receipt_path.exists():
        return None
    if not alias_path.is_file() or not receipt_path.is_file():
        raise ValueError("incomplete claim identity migration bundle")
    alias_payload = json.loads(alias_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if not isinstance(alias_payload, dict) or not isinstance(receipt, dict):
        raise ValueError("invalid claim identity migration bundle")
    _validate_alias_payload(alias_payload, project_id)
    _validate_receipt(receipt, alias_payload, project_id)
    return alias_payload


def migrate_v2(vault_root: Path, project_id: str) -> dict[str, Any]:
    """Migrate one project under isolated, fail-closed state and receipt paths."""
    safe_project = safe_relative_component(project_id, label="project id")
    vault_root = vault_root.resolve()
    bundle_root = _inside(vault_root, vault_root / "state" / "claim-alias-maps")
    bundle_dir = _inside(vault_root, bundle_root / safe_project)
    alias_path = _inside(vault_root, bundle_dir / "claim-alias-map.json")
    receipt_path = _inside(vault_root, bundle_dir / "migration-receipt.json")
    lock_key = _digest(safe_project)[:20]
    lock_path = _inside(
        vault_root,
        vault_root / ".atlas" / "claim-v2-migration-locks" / f"{lock_key}.lock",
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with ProjectIdentityLock(lock_path, wait_seconds=30.0, stale_seconds=300.0):
        existing = _load_existing_bundle(alias_path, receipt_path, safe_project)
        if existing is not None:
            return {
                "status": "idempotent",
                "message": "Migration already completed",
                "alias_map_path": str(alias_path),
                "migrated_claims": existing["audit"]["output_aliases"],
                "receipt": str(receipt_path),
            }

        source_metadata = _load_source_metadata(vault_root, safe_project)
        commits, records = _scan_historical_sources(vault_root, source_metadata)
        all_candidates = [
            candidate
            for commit, file_path, content in records
            for candidate in _extract_candidates(
                safe_project, source_metadata, commit, file_path, content
            )
        ]

        aliases: list[dict[str, Any]] = []
        ambiguous: list[dict[str, Any]] = []
        by_v1: dict[str, list[_Candidate]] = {}
        for candidate in all_candidates:
            by_v1.setdefault(candidate.v1_claim_id, []).append(candidate)

        for v1_id, group in sorted(by_v1.items()):
            distinct_v2 = {candidate.v2_claim_id for candidate in group}
            if len(distinct_v2) > 1:
                seen: set[tuple[str, str]] = set()
                records_for_group: list[dict[str, Any]] = []
                for candidate in group:
                    key = (candidate.v2_claim_id, candidate.source_commit)
                    if key in seen:
                        continue
                    seen.add(key)
                    records_for_group.append(_candidate_to_record(candidate))
                ambiguous.append(
                    {
                        "v1_claim_id": v1_id,
                        "reason": "single v1 identity maps to multiple v2 identities",
                        "records": records_for_group,
                    }
                )
            else:
                aliases.append(_candidate_to_record(group[0]))

        alias_payload = {
            "schema_version": 1,
            "project_id": safe_project,
            "aliases": aliases,
            "ambiguous": ambiguous,
            "audit": {
                "migrated_at": datetime.now(UTC).isoformat(),
                "source_commits_scanned": len(commits),
                "input_claims": len(all_candidates),
                "output_aliases": len(aliases),
            },
        }
        _validate_alias_payload(alias_payload, safe_project)
        receipt_data = _receipt_data(alias_payload)

        bundle_root.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{safe_project}.", suffix=".tmp", dir=bundle_root
            )
        )
        try:
            staged_alias = staging / alias_path.name
            staged_receipt = staging / receipt_path.name
            _write_json(staged_alias, alias_payload)
            _write_json(staged_receipt, receipt_data)
            staged_payload = json.loads(staged_alias.read_text(encoding="utf-8"))
            staged_receipt_payload = json.loads(
                staged_receipt.read_text(encoding="utf-8")
            )
            _validate_alias_payload(staged_payload, safe_project)
            _validate_receipt(
                staged_receipt_payload, staged_payload, safe_project
            )
            os.replace(staging, bundle_dir)
        except BaseException:
            if staging.exists():
                shutil.rmtree(staging)
            raise

        return {
            "status": "success",
            "migrated_claims": len(aliases),
            "ambiguous_count": len(ambiguous),
            "alias_map_path": str(alias_path),
            "receipt": str(receipt_path),
        }
