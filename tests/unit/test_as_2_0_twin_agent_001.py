"""AS-2.0-TWIN-FIXTURE-002 / AS-2.0-AGENT-EVAL-001 tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.agent_eval_shadow import (
    AgentEvalShadowError,
    build_agent_eval_shadow_receipt,
)
from project_atlas.schema import available_schemas, validate_record
from project_atlas.twin_fixture_scenarios import (
    TwinFixtureScenarioError,
    build_twin_fixture_scenario,
)


def test_twin_scenario(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    report = build_twin_fixture_scenario(vault, record_id="scen-a")
    assert report["twin_production_ready"] is False
    validate_record(report, "twin-fixture-scenario")


def test_twin_rejects_prod(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(TwinFixtureScenarioError, match="production-ready-forbidden"):
        build_twin_fixture_scenario(vault, record_id="scen-a", claim_production_ready=True)


def test_agent_eval(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    report = build_agent_eval_shadow_receipt(vault, record_id="eval-a", cases_run=3)
    assert report["mode"] == "shadow"
    assert report["score_subjective"] is False
    validate_record(report, "agent-eval-shadow-receipt")


def test_agent_eval_rejects_subjective(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(AgentEvalShadowError, match="subjective-score-forbidden"):
        build_agent_eval_shadow_receipt(vault, record_id="eval-a", allow_subjective_score=True)


def test_docs() -> None:
    root = Path(__file__).resolve().parents[2]
    assert (root / "docs" / "AS-2.0-TWIN-FIXTURE-002.md").is_file()
    assert (root / "docs" / "AS-2.0-AGENT-EVAL-001.md").is_file()
    assert "twin-fixture-scenario" in available_schemas()
    assert "agent-eval-shadow-receipt" in available_schemas()
