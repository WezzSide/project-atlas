"""AS-2.0-UX-002 mode catalog deepen tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.schema import available_schemas, validate_record
from project_atlas.ux_mode_catalog import (
    UxModeCatalogError,
    build_ux_mode_catalog,
)


def test_ux_mode_catalog_happy_path(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    report = build_ux_mode_catalog(vault, catalog_id="cc-default")
    assert report["ui_rewrite"] is False
    assert report["canonical_writes"] is False
    assert {m["mode_id"] for m in report["modes"]} >= {
        "overview",
        "projects",
        "ops",
        "impact",
    }
    assert all(m["graph_authority"] is False for m in report["modes"])
    validate_record(report, "ux-mode-catalog")


def test_ux_rejects_ui_rewrite(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(UxModeCatalogError, match="ui-rewrite-forbidden"):
        build_ux_mode_catalog(vault, catalog_id="cc-default", allow_ui_rewrite=True)


def test_ux_docs_and_schema() -> None:
    root = Path(__file__).resolve().parents[2]
    assert (root / "docs" / "AS-2.0-UX-002.md").is_file()
    assert (root / "docs" / "AS-2.0-UX-001.md").is_file()
    assert "ux-mode-catalog" in available_schemas()
