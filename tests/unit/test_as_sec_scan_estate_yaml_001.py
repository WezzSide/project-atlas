"""AS-SEC-SCAN-ESTATE-YAML-001 — scan decoded YAML marker ids before persist."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.orchestration.autonomy.authentic_estate import (
    EstatePreflight,
    run_estate_preflight,
    write_estate_credential,
)
from project_atlas.secrets import scan_text

TOKEN = "bearer " + ("A" * 32)


def _estate_with_id(tmp_path: Path, raw_id_line: str) -> Path:
    estate = tmp_path / "live-root"
    estate.mkdir()
    (estate / "README.md").write_text("hello estate\n", encoding="utf-8")
    (estate / ".atlas-project.yaml").write_text(
        "schema_version: 1\n"
        "project:\n"
        f"  id: {raw_id_line}\n",
        encoding="utf-8",
    )
    return estate


def test_quoted_unicode_escape_project_id_is_not_persisted(tmp_path: Path) -> None:
    raw_id = f'"\\u0062earer {"A" * 32}"'
    estate = _estate_with_id(tmp_path, raw_id)
    raw = (estate / ".atlas-project.yaml").read_text(encoding="utf-8")
    assert scan_text(raw) == []
    assert TOKEN not in raw

    preflight = run_estate_preflight(estate)
    assert preflight.project_id is None
    assert preflight.preflight_pass is False
    assert preflight.root_is_authentic is False

    repo = tmp_path / "repo"
    repo.mkdir()
    path = write_estate_credential(repo, estate, preflight)
    persisted = path.read_text(encoding="utf-8")
    loaded = json.loads(persisted)
    assert TOKEN not in persisted
    assert loaded["project_id"] is None
    assert loaded["preflight"]["project_id"] is None
    assert loaded["preflight_pass"] is False
    assert loaded["AUTHENTIC_ESTATE_CREDENTIAL_SATISFIED"] is False
    assert loaded["OWNER_CAPABILITY_GRANTED"] is False


def test_quoted_hex_escape_project_id_is_not_persisted(tmp_path: Path) -> None:
    raw_id = f'"\\x62earer {"A" * 32}"'
    estate = _estate_with_id(tmp_path, raw_id)
    raw = (estate / ".atlas-project.yaml").read_text(encoding="utf-8")
    assert scan_text(raw) == []

    preflight = run_estate_preflight(estate)
    assert preflight.project_id is None
    assert preflight.preflight_pass is False

    repo = tmp_path / "repo"
    repo.mkdir()
    path = write_estate_credential(repo, estate, preflight)
    persisted = path.read_text(encoding="utf-8")
    assert TOKEN not in persisted
    assert json.loads(persisted)["project_id"] is None


def test_write_credential_strips_secret_shaped_preflight_identity(tmp_path: Path) -> None:
    """Defense in depth: do not persist a planted decoded token on write."""
    estate = tmp_path / "live-root"
    estate.mkdir()
    (estate / "README.md").write_text("hello estate\n", encoding="utf-8")
    (estate / ".atlas-project.yaml").write_text(
        "schema_version: 1\nproject:\n  id: sample\n",
        encoding="utf-8",
    )
    planted = EstatePreflight(
        root=str(estate.resolve()),
        root_exists=True,
        root_is_directory=True,
        root_is_authentic=True,
        root_is_not_atlas_fixture=True,
        root_is_not_test_fixture=True,
        root_is_not_synthetic_demo=True,
        root_is_readable=True,
        has_project_marker=True,
        project_id=TOKEN,
        project_uuid=None,
        preflight_pass=True,
        estate_fingerprint=None,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    path = write_estate_credential(repo, estate, planted)
    persisted = path.read_text(encoding="utf-8")
    loaded = json.loads(persisted)
    assert TOKEN not in persisted
    assert loaded["project_id"] is None
    assert loaded["preflight"]["project_id"] is None
    assert loaded["preflight_pass"] is False
    assert loaded["AUTHENTIC_ESTATE_CREDENTIAL_SATISFIED"] is False
    assert loaded["OWNER_CAPABILITY_GRANTED"] is False


def test_clean_project_id_still_survives(tmp_path: Path) -> None:
    estate = _estate_with_id(tmp_path, "sample-estate")
    preflight = run_estate_preflight(estate)
    assert preflight.project_id == "sample-estate"
    assert preflight.preflight_pass is True
    repo = tmp_path / "repo"
    repo.mkdir()
    path = write_estate_credential(repo, estate, preflight)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["project_id"] == "sample-estate"
    assert TOKEN not in json.dumps(loaded)
