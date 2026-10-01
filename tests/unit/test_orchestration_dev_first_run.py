from __future__ import annotations

import json
from pathlib import Path

from project_atlas.orchestration.autonomy.dev_first_run import (
    BASE,
    build_package,
    make_devq_0001_work,
    render_package,
)

PKG = Path(__file__).resolve().parents[2] / "docs/autonomy/first-run/ATLAS-DEVQ-0001.package.json"


def test_package_is_deterministic_sealed_and_matches_committed_copy():
    w1, w2 = make_devq_0001_work(), make_devq_0001_work()
    assert w1.seal == w2.seal and w1.base_revision == BASE
    pkg = build_package()
    assert pkg == build_package()
    assert json.loads(PKG.read_text()) == pkg  # regenerate via render_package if this drifts


def test_package_carries_required_fields_and_no_secret_values():
    pkg = build_package()
    for k in (
        "task_id",
        "execution_id",
        "work_seal",
        "repository",
        "base_revision",
        "allowed_paths",
        "authority_reference",
        "workflow",
        "workflow_ref",
        "workflow_inputs",
        "expected_agent_branch_pattern",
        "acceptance",
        "result_discovery_contract",
        "verification_profile",
        "failure_ceiling",
        "abort_conditions",
        "rollback",
    ):
        assert k in pkg
    assert pkg["workflow"] == "atlas-agent-execute.yml" and pkg["workflow_ref"] == "main"
    assert pkg["workflow_inputs"]["base_branch"] == "main"
    assert pkg["secrets"]["ANTHROPIC_API_KEY"].startswith("CONFIRMED_PRESENT")
    text = render_package(pkg)
    assert "sk-ant" not in text and "ghp_" not in text
    cmds = " ".join(pkg["acceptance"]["commands"])
    assert "graph_projections" in cmds or "graph_005" in cmds
    assert "infra/atlas-runner" not in cmds  # runner tests are never the acceptance
