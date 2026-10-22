"""AS-SYNC-001-SCAFFOLD — Dry-run workspace registry (no live estate scan).

Builds a schema-valid dry-run registry from **explicit** roots only.
Writes under ``generated/ops/`` (scaffold plane) — never claims production
``00-system/sync/`` SYNC-001 certification and never invents project UUIDs.
Hard-refuses whole-machine / home / filesystem-root scans.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from project_atlas.schema import validate_record
from project_atlas.secrets import scan_text
from project_atlas.source_identity import validate_project_uuid

GENERATOR_ID = "atlas-sync-001-scaffold"
PACKAGE_ID = "AS-SYNC-001-SCAFFOLD"
REPORT_SCHEMA = "workspace-registry-dry-run"
REPORT_RELATIVE = Path("generated") / "ops" / "workspace-registry-dry-run.json"


class WorkspaceRegistryError(ValueError):
    """Fail-closed dry-run registry error."""


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _refuse_dangerous_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise WorkspaceRegistryError(f"root is not a directory: {resolved}")
    # AT-013 class: refuse filesystem root and home as estate roots.
    if resolved.parent == resolved:
        raise WorkspaceRegistryError(f"refusing filesystem root as workspace root: {resolved}")
    home = Path.home().resolve()
    if resolved == home:
        raise WorkspaceRegistryError(f"refusing home directory as workspace root: {resolved}")
    return resolved


def _read_marker(root: Path) -> dict[str, Any] | None:
    for name in (".atlas-project.yaml", ".atlas-project.yml"):
        marker = root / name
        if marker.is_file():
            raw = yaml.safe_load(marker.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
    return None


def _extract_project_uuid(marker: dict[str, Any]) -> str | None:
    candidates: list[Any] = []
    project = marker.get("project")
    if isinstance(project, dict):
        candidates.extend([project.get("id"), project.get("uuid"), project.get("project_uuid")])
    candidates.extend(
        [marker.get("project_uuid"), marker.get("project_id"), marker.get("id")]
    )
    for value in candidates:
        if isinstance(value, str) and value.strip():
            try:
                return validate_project_uuid(value.strip())
            except ValueError:
                continue
    return None


def build_dry_run_registry(
    *,
    explicit_roots: Sequence[Path],
    vault_identity: str,
    registry_id: str = "dry-run-scaffold",
    allowed_root_prefixes: Sequence[Path] | None = None,
) -> dict[str, Any]:
    """Build a dry-run registry from explicit roots only (no filesystem walk-up)."""
    if not explicit_roots:
        raise WorkspaceRegistryError(
            "explicit_roots required; refusing empty/implied whole-machine scan"
        )
    roots = [_refuse_dangerous_root(Path(p)) for p in explicit_roots]
    if allowed_root_prefixes is None:
        prefixes = [r for r in roots]
    else:
        prefixes = [_refuse_dangerous_root(Path(p)) for p in allowed_root_prefixes]
        if not prefixes:
            raise WorkspaceRegistryError("allowed_root_prefixes empty ⇒ refuse all roots")

    workspaces: list[dict[str, Any]] = []
    projects: list[dict[str, Any]] = []
    quarantine: list[dict[str, Any]] = []

    for index, root in enumerate(sorted(roots, key=lambda p: p.as_posix().lower())):
        under_prefix = any(
            root == pref or root.is_relative_to(pref) for pref in prefixes
        )
        root_id = f"root-{index:04d}"
        workspaces.append(
            {
                "root_id": root_id,
                "path": root.as_posix(),
                "enabled": under_prefix,
                "label": root.name,
            }
        )
        if not under_prefix:
            quarantine.append(
                {
                    "path": root.as_posix(),
                    "reason": "outside_allowed_root_prefixes",
                }
            )
            continue
        marker = _read_marker(root)
        if marker is None:
            quarantine.append(
                {
                    "path": root.as_posix(),
                    "reason": "missing_atlas_project_marker",
                }
            )
            continue
        project_uuid = _extract_project_uuid(marker)
        if project_uuid is None:
            quarantine.append(
                {
                    "path": root.as_posix(),
                    "reason": "missing_or_invalid_project_uuid",
                }
            )
            continue
        display = None
        project = marker.get("project")
        if isinstance(project, dict) and isinstance(project.get("name"), str):
            display = project["name"]
        # AS-SEC-SCAN-WSREG-YAML-001: yaml.safe_load decodes quoted \u/\x
        # after any raw-byte scan; refuse decoded secret persist.
        if isinstance(display, str) and scan_text(display):
            quarantine.append(
                {
                    "path": root.as_posix(),
                    "reason": "secret_findings",
                }
            )
            continue
        projects.append(
            {
                "project_uuid": project_uuid,
                "source_lineage_id": None,
                "root_id": root_id,
                "project_root": root.as_posix(),
                "enabled": True,
                "display_name": display,
                "policy": None,
                "graphify_opt_in": False,
                "portfolio_opt_in": True,
            }
        )

    projects.sort(key=lambda row: str(row["project_uuid"]))
    workspaces.sort(key=lambda row: str(row["root_id"]))
    quarantine.sort(key=lambda row: str(row["path"]))

    document: dict[str, Any] = {
        "schema_version": 1,
        "schema": "atlas.workspace_registry.dry_run.v1",
        "truth_plane": "operational",
        "authority_plane": "none",
        "note": "DRY-RUN REGISTRY SCAFFOLD ≠ AS-SYNC-001 CERTIFIED / ≠ PILOT PASS",
        "package": PACKAGE_ID,
        "production_sync_certified": False,
        "estate_pilot_passed": False,
        "registry_id": registry_id,
        "vault_identity": vault_identity,
        "allowed_root_prefixes": sorted(p.as_posix() for p in prefixes),
        "workspaces": workspaces,
        "projects": projects,
        "quarantine": quarantine,
        "policy_defaults": {
            "include_globs": [],
            "exclude_globs": [],
            "sync_eligible": True,
            "priority": 100,
            "max_file_bytes": None,
            "max_files_per_sync": None,
            "sensitive_defaults": "exclude",
        },
        "generated": {"by": GENERATOR_ID},
    }
    validate_record(document, REPORT_SCHEMA)
    return document


def write_dry_run_registry(vault: Path, document: dict[str, Any]) -> Path:
    """Persist dry-run registry under generated/ops/ only."""
    validate_record(document, REPORT_SCHEMA)
    path = vault.expanduser().resolve() / REPORT_RELATIVE
    # Refuse writing into production sync path from scaffold helpers.
    forbidden = vault.expanduser().resolve() / "00-system" / "sync" / "workspace-registry.json"
    if path.resolve() == forbidden.resolve():  # pragma: no cover - defensive
        raise WorkspaceRegistryError("scaffold must not write production sync registry path")
    payload = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_atomic(path, payload)
    return path
