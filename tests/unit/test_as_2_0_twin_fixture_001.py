"""AS-2.0-TWIN-FIXTURE-001 disposable twin projection fixture tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.schema import available_schemas, validate_record
from project_atlas.twin_fixtures import (
    TwinFixtureError,
    TwinProjectRow,
    build_twin_projection_fixture,
)

ROOT = Path(__file__).resolve().parents[2]


def test_twin_fixture_never_claims_pilot_or_ready(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    report = build_twin_projection_fixture(
        vault,
        projection_id="fixture-alpha",
        projects=[
            TwinProjectRow("demo-project", "Demo Project", health="healthy"),
        ],
        authentic_pilot_roots=0,
    )
    assert report["estate_pilot_passed"] is False
    assert report["twin_production_ready"] is False
    assert report["twin_001_status"] == "BLOCKED"
    assert report["pilot_status"] == "fixture-only"
    # No authentic roots ⇒ healthy demoted to unknown.
    assert report["projects"][0]["health"] == "unknown"
    validate_record(report, "twin-projection-fixture")
    assert (vault / "generated" / "ops" / "twin-fixtures" / "fixture-alpha.json").is_file()


def test_twin_fixture_rejects_duplicate_projects(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(TwinFixtureError, match="project-duplicate"):
        build_twin_projection_fixture(
            vault,
            projection_id="dup",
            projects=[
                TwinProjectRow("a", "A"),
                TwinProjectRow("a", "A2"),
            ],
        )


def test_twin_fixture_docs_and_schema() -> None:
    assert "twin-projection-fixture" in available_schemas()
    assert (ROOT / "docs" / "AS-2.0-TWIN-FIXTURE-001.md").is_file()
    assert (ROOT / "docs" / "atlas-2.0" / "fixtures" / "twin-projection" / "README.md").is_file()
    sample = ROOT / "docs" / "atlas-2.0" / "fixtures" / "twin-projection" / "sample-projection.json"
    import json

    payload = json.loads(sample.read_text(encoding="utf-8"))
    validate_record(payload, "twin-projection-fixture")
    sample_text = sample.read_text(encoding="utf-8")
    assert '"estate_pilot_passed": false' in sample_text
    assert '"twin_production_ready": false' in sample_text
    assert payload["estate_pilot_passed"] is False
    assert payload["twin_production_ready"] is False
    assert payload["twin_001_status"] == "BLOCKED"
