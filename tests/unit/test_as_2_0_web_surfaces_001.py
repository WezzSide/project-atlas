"""AS-2.0-WEB-ASK-001 / AS-2.0-WEB-SURFACE-001 tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.schema import available_schemas, validate_record
from project_atlas.web_ask_atlas import WebAskAtlasError, build_web_ask_atlas_contract
from project_atlas.web_surface_catalog import (
    WebSurfaceCatalogError,
    build_web_surface_catalog,
)


def test_ask_atlas(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    report = build_web_ask_atlas_contract(vault, record_id="ask-default")
    assert report["canonical_writes"] is False
    validate_record(report, "web-ask-atlas-contract")


def test_ask_rejects_writes(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(WebAskAtlasError, match="canonical-writes-forbidden"):
        build_web_ask_atlas_contract(vault, record_id="ask-default", allow_canonical_writes=True)


def test_surfaces(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    report = build_web_surface_catalog(vault, record_id="surfaces-default")
    assert report["ui_rewrite"] is False
    assert all(s["estate_live"] is False for s in report["surfaces"])
    validate_record(report, "web-surface-catalog")


def test_surfaces_reject_rewrite(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(WebSurfaceCatalogError, match="ui-rewrite-forbidden"):
        build_web_surface_catalog(vault, record_id="surfaces-default", allow_ui_rewrite=True)


def test_docs() -> None:
    root = Path(__file__).resolve().parents[2]
    assert (root / "docs" / "AS-2.0-WEB-ASK-001.md").is_file()
    assert (root / "docs" / "AS-2.0-WEB-SURFACE-001.md").is_file()
    assert "web-ask-atlas-contract" in available_schemas()
    assert "web-surface-catalog" in available_schemas()
