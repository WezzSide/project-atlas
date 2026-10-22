"""AS-SEC-SCAN-WSREG-YAML-001 — scan decoded YAML marker names before persist."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.scaffold import create_scaffold
from project_atlas.secrets import scan_text
from project_atlas.workspace_registry import (
    build_dry_run_registry,
    write_dry_run_registry,
)

UUID = "550e8400-e29b-41d4-a716-446655440000"
TOKEN = "bearer " + ("A" * 32)


def test_quoted_unicode_escape_name_is_not_persisted(tmp_path: Path) -> None:
    raw = (
        "schema_version: 1\n"
        "project:\n"
        "  id: hunt-proj\n"
        f'  name: "\\u0062earer {"A" * 32}"\n'
        f"project_uuid: {UUID}\n"
    )
    assert scan_text(raw) == []
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".atlas-project.yaml").write_text(raw, encoding="utf-8")
    vault = tmp_path / "vault"
    create_scaffold(vault)
    doc = build_dry_run_registry(
        explicit_roots=[root],
        vault_identity="hunt-vault",
    )
    path = write_dry_run_registry(vault, doc)
    persisted = path.read_text(encoding="utf-8")
    assert TOKEN not in persisted
    assert doc["projects"] == []
    assert doc["quarantine"][0]["reason"] == "secret_findings"
    loaded = json.loads(persisted)
    assert loaded["projects"] == []
    assert TOKEN not in json.dumps(loaded)


def test_quoted_hex_escape_name_is_not_persisted(tmp_path: Path) -> None:
    raw = (
        "schema_version: 1\n"
        "project:\n"
        "  id: hunt-proj\n"
        f'  name: "\\x62earer {"A" * 32}"\n'
        f"project_uuid: {UUID}\n"
    )
    assert scan_text(raw) == []
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".atlas-project.yaml").write_text(raw, encoding="utf-8")
    doc = build_dry_run_registry(
        explicit_roots=[root],
        vault_identity="hunt-vault",
    )
    assert TOKEN not in json.dumps(doc)
    assert doc["projects"] == []
    assert doc["quarantine"][0]["reason"] == "secret_findings"
