"""AS-2.0-COLLAB / SCALE / SEC-ADV tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.collaboration_stubs import (
    CollaborationStubError,
    build_collaboration_stub_registry,
)
from project_atlas.scale_harness import ScaleHarnessError, build_scale_harness_plan
from project_atlas.schema import available_schemas, validate_record
from project_atlas.security_adv import SecurityAdvError, build_security_adv_matrix


def test_collab(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    report = build_collaboration_stub_registry(vault, record_id="collab-a")
    assert report["live_collab"] is False
    validate_record(report, "collaboration-stub-registry")


def test_collab_rejects_live(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(CollaborationStubError, match="live-forbidden"):
        build_collaboration_stub_registry(vault, record_id="collab-a", enable_live_collab=True)


def test_scale(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    report = build_scale_harness_plan(vault, record_id="scale-a")
    assert report["live_load"] is False
    validate_record(report, "scale-harness-plan")


def test_scale_rejects_live(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(ScaleHarnessError, match="live-load-forbidden"):
        build_scale_harness_plan(vault, record_id="scale-a", enable_live_load=True)


def test_sec_adv(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    report = build_security_adv_matrix(vault, record_id="secadv-a")
    assert report["matched_content_logged"] is False
    validate_record(report, "security-adv-matrix")


def test_sec_adv_rejects_log(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(SecurityAdvError, match="matched-content-log-forbidden"):
        build_security_adv_matrix(vault, record_id="secadv-a", log_matched_content=True)


def test_docs() -> None:
    root = Path(__file__).resolve().parents[2]
    for name in (
        "AS-2.0-COLLAB-001.md",
        "AS-2.0-SCALE-001.md",
        "AS-2.0-SEC-ADV-001.md",
    ):
        assert (root / "docs" / name).is_file()
    for kind in (
        "collaboration-stub-registry",
        "scale-harness-plan",
        "security-adv-matrix",
    ):
        assert kind in available_schemas()
