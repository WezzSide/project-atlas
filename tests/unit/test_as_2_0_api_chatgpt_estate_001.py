"""AS-2.0-API / CHATGPT-CAPTURE / ESTATE-INTEL tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.api_surface_registry import (
    ApiSurfaceRegistryError,
    build_api_surface_registry,
)
from project_atlas.chatgpt_capture import (
    ChatgptCaptureError,
    build_chatgpt_capture_receipt,
)
from project_atlas.estate_intel_fixture import (
    EstateIntelFixtureError,
    build_estate_intel_fixture,
)
from project_atlas.schema import available_schemas, validate_record


def test_api(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    report = build_api_surface_registry(vault, record_id="api-1")
    assert report["write_enabled"] is False
    validate_record(report, "api-surface-registry")


def test_api_reject_writes(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(ApiSurfaceRegistryError, match="writes-forbidden"):
        build_api_surface_registry(vault, record_id="api-1", enable_writes=True)


def test_chatgpt(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    report = build_chatgpt_capture_receipt(vault, record_id="cap-1", turn_count=2)
    assert report["live_api"] is False
    validate_record(report, "chatgpt-capture-receipt")


def test_chatgpt_reject_live(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(ChatgptCaptureError, match="live-api-forbidden"):
        build_chatgpt_capture_receipt(vault, record_id="cap-1", enable_live_api=True)


def test_estate(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    report = build_estate_intel_fixture(vault, record_id="est-1")
    assert report["pilot_roots"] == 0
    assert report["estate_pilot_passed"] is False
    validate_record(report, "estate-intel-fixture")


def test_estate_reject_pilot_claim(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(EstateIntelFixtureError, match="pilot-pass-forbidden"):
        build_estate_intel_fixture(vault, record_id="est-1", claim_pilot_passed=True)


def test_docs() -> None:
    root = Path(__file__).resolve().parents[2]
    for name in (
        "AS-2.0-API-001.md",
        "AS-2.0-CHATGPT-CAPTURE-001.md",
        "AS-2.0-ESTATE-INTEL-001.md",
    ):
        assert (root / "docs" / name).is_file()
    for kind in (
        "api-surface-registry",
        "chatgpt-capture-receipt",
        "estate-intel-fixture",
    ):
        assert kind in available_schemas()
