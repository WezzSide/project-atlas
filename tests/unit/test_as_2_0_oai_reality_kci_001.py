"""AS-2.0-OAI-IMPORT-002 / REALITY-GAP-UI / KCI-HARNESS tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.knowledge_ci_harness import (
    KnowledgeCiHarnessError,
    build_knowledge_ci_harness,
)
from project_atlas.openai_import_path import (
    OpenaiImportPathError,
    build_openai_import_path_receipt,
)
from project_atlas.reality_gap_ui import (
    RealityGapUiError,
    build_reality_gap_ui_catalog,
)
from project_atlas.schema import available_schemas, validate_record


def test_oai_no_export(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    report = build_openai_import_path_receipt(vault, record_id="path-a")
    assert report["live_api"] is False
    assert report["path_mode"] == "fixture-no-export"
    validate_record(report, "openai-import-path-receipt")


def test_oai_with_export(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    report = build_openai_import_path_receipt(vault, record_id="path-b", export_present=True)
    assert report["status"] == "ready-fixture"
    validate_record(report, "openai-import-path-receipt")


def test_oai_rejects_live(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(OpenaiImportPathError, match="live-api-forbidden"):
        build_openai_import_path_receipt(vault, record_id="path-a", enable_live_api=True)


def test_reality_gap_ui(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    report = build_reality_gap_ui_catalog(vault, record_id="rg-ui")
    assert report["canonical_writes"] is False
    validate_record(report, "reality-gap-ui-catalog")


def test_reality_gap_ui_rejects_writes(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(RealityGapUiError, match="canonical-writes-forbidden"):
        build_reality_gap_ui_catalog(vault, record_id="rg-ui", allow_canonical_writes=True)


def test_kci_harness(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    report = build_knowledge_ci_harness(vault, record_id="harness-a")
    assert report["authority_promoted"] is False
    validate_record(report, "knowledge-ci-harness")


def test_kci_harness_rejects_promote(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(KnowledgeCiHarnessError, match="authority-promote-forbidden"):
        build_knowledge_ci_harness(vault, record_id="harness-a", promote_authority=True)


def test_docs() -> None:
    root = Path(__file__).resolve().parents[2]
    for name in (
        "AS-2.0-OAI-IMPORT-002.md",
        "AS-2.0-REALITY-GAP-UI-001.md",
        "AS-2.0-KCI-HARNESS-001.md",
    ):
        assert (root / "docs" / name).is_file()
    for kind in (
        "openai-import-path-receipt",
        "reality-gap-ui-catalog",
        "knowledge-ci-harness",
    ):
        assert kind in available_schemas()
