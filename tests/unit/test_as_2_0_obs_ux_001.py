"""AS-2.0-OBS-UX-001 Obsidian lens registry tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.obsidian_ux import (
    ObsidianUxError,
    build_obsidian_lens_registry,
)
from project_atlas.schema import available_schemas, validate_record


def test_obsidian_lens_registry_happy_path(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    report = build_obsidian_lens_registry(vault, registry_id="default")
    assert report["plugin_shipped"] is False
    assert report["canonical_writes"] is False
    assert report["compat_snapshot_id"] == "atlas-1.0.0-compat"
    validate_record(report, "obsidian-lens-registry")
    assert (vault / "generated" / "ops" / "obsidian" / "default-lens-registry.json").is_file()


def test_obsidian_rejects_bad_id(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(ObsidianUxError, match="registry-id-invalid"):
        build_obsidian_lens_registry(vault, registry_id="BAD ID")


def test_obsidian_docs_and_schema() -> None:
    root = Path(__file__).resolve().parents[2]
    assert (root / "docs" / "AS-2.0-OBS-UX-001.md").is_file()
    assert "obsidian-lens-registry" in available_schemas()
