"""D-149 — authentic-estate availability must never grant owner authority.

Adversarial matrix: CREDENTIAL+AUTHENTIC_ESTATE_ROOT may become runnable;
MERGE/SECURITY/HUMAN/OWNER and unrelated credentials must stay blocked.

PROBE (live main f0e0c979, before this remediation):
- CREDENTIAL + SOME_OTHER_CREDENTIAL → OWNER_GATE rewritten to NONE
- SUPERSEDED MERGE + estate-absent O2 reseed → OWNER_GATE rewritten to CREDENTIAL
Those two cases failed before remediation and must pass afterward.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from project_atlas.orchestration.autonomy.authentic_estate import (
    AUTHENTIC_O2_PACKAGES,
    estate_credential_binding_ok,
    refresh_authentic_o2_node_states,
    run_estate_preflight,
    write_estate_credential,
)
from project_atlas.orchestration.sdk.mission_reconciler import (
    MissionObjective,
    WorkNode,
    _idempotency_key,
    load_nodes,
    mission_reconcile,
    persist_nodes,
    persist_objectives,
)


def _git_init(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)


def _init_estate(path: Path, *, project_id: str = "sample") -> Path:
    path.mkdir(parents=True)
    _git_init(path)
    (path / "README.md").write_text(
        f"# {project_id}\n\nPurpose: inventory tooling.\n",
        encoding="utf-8",
    )
    (path / "docs").mkdir()
    (path / "docs" / "overview.md").write_text(f"Overview of {project_id}.\n", encoding="utf-8")
    (path / ".atlas-project.yaml").write_text(
        "schema_version: 1\nproject:\n  id: "
        f"{project_id}\n  name: {project_id.title()}\nproject_uuid: "
        "00000000-0000-4000-8000-000000000001\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    return path


def _init_atlas_repo(tmp_path: Path, estate: Path) -> Path:
    root = tmp_path / "atlas"
    root.mkdir()
    _git_init(root)
    (root / "README.md").write_text("atlas\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
    preflight = run_estate_preflight(estate)
    write_estate_credential(root, estate, preflight)
    return root


def _o2_node(
    node_id: str,
    *,
    owner_gate: str,
    deps: list[str],
    status: str = "BLOCKED_OWNER",
    package: str = AUTHENTIC_O2_PACKAGES[0],
) -> WorkNode:
    return WorkNode(
        NODE_ID=node_id,
        OBJECTIVE_ID="O2",
        PACKAGE_ID=package,
        TASK_KIND="AUTHENTIC_E2E",
        PRIORITY=90,
        DEPENDENCIES=deps,
        ALLOWED_PATHS=["docs/"],
        SURFACE_SET=["docs/"],
        WORKER_ROLE="IMPLEMENTER",
        ACCEPTANCE_CRITERIA="ingest",
        REQUIRED_VERIFICATION=["unit"],
        OWNER_GATE=owner_gate,  # type: ignore[arg-type]
        GENERATION=1,
        IDEMPOTENCY_KEY=f"{node_id}-key",
        status=status,  # type: ignore[arg-type]
    )


def _snapshot(node: WorkNode) -> dict[str, Any]:
    return {
        "status": node.status,
        "OWNER_GATE": node.OWNER_GATE,
        "DEPENDENCIES": list(node.DEPENDENCIES),
    }


def test_credential_plus_estate_root_becomes_runnable(tmp_path: Path) -> None:
    estate = _init_estate(tmp_path / "estate")
    root = _init_atlas_repo(tmp_path, estate)
    persist_nodes(
        root,
        {
            "cred": _o2_node(
                "cred",
                owner_gate="CREDENTIAL",
                deps=["AUTHENTIC_ESTATE_ROOT"],
            )
        },
    )
    changed = refresh_authentic_o2_node_states(root)
    after = load_nodes(root)["cred"]
    assert "cred" in changed
    assert after.OWNER_GATE == "NONE"
    assert after.DEPENDENCIES == []
    assert after.status == "READY"


@pytest.mark.parametrize(
    "gate",
    ["MERGE", "SECURITY", "HUMAN", "OWNER", "RELEASE", "GOVERNOR", "SIGNOFF"],
)
def test_owner_held_gates_never_cleared_by_estate(tmp_path: Path, gate: str) -> None:
    estate = _init_estate(tmp_path / "estate")
    root = _init_atlas_repo(tmp_path, estate)
    persist_nodes(
        root,
        {
            "held": _o2_node(
                "held",
                owner_gate=gate,
                deps=["AUTHENTIC_ESTATE_ROOT"],
            )
        },
    )
    prior = _snapshot(load_nodes(root)["held"])
    changed = refresh_authentic_o2_node_states(root)
    after = load_nodes(root)["held"]
    assert "held" not in changed
    assert gate == after.OWNER_GATE
    assert after.OWNER_GATE != "NONE"
    assert _snapshot(after) == prior


def test_adversarial_merge_cannot_become_none(tmp_path: Path) -> None:
    """Refresh path: MERGE must stay MERGE even when estate is valid."""
    estate = _init_estate(tmp_path / "estate")
    root = _init_atlas_repo(tmp_path, estate)
    persist_nodes(
        root,
        {
            "merge": _o2_node(
                "merge",
                owner_gate="MERGE",
                deps=["AUTHENTIC_ESTATE_ROOT"],
            )
        },
    )
    refresh_authentic_o2_node_states(root)
    after = load_nodes(root)["merge"]
    assert after.OWNER_GATE == "MERGE"
    assert after.status == "BLOCKED_OWNER"


def test_other_credential_remains_blocked(tmp_path: Path) -> None:
    """Failed on live main f0e0c979: CREDENTIAL-other was rewritten to NONE."""
    estate = _init_estate(tmp_path / "estate")
    root = _init_atlas_repo(tmp_path, estate)
    persist_nodes(
        root,
        {
            "other": _o2_node(
                "other",
                owner_gate="CREDENTIAL",
                deps=["SOME_OTHER_CREDENTIAL"],
            )
        },
    )
    prior = _snapshot(load_nodes(root)["other"])
    changed = refresh_authentic_o2_node_states(root)
    after = load_nodes(root)["other"]
    assert changed == []
    assert after.OWNER_GATE == "CREDENTIAL"
    assert after.status == "BLOCKED_OWNER"
    assert _snapshot(after) == prior


def test_estate_dep_consumed_other_deps_preserved(tmp_path: Path) -> None:
    """D149-001 CASE C: estate + HUMAN_APPROVAL must not widen gate to NONE."""
    estate = _init_estate(tmp_path / "estate")
    root = _init_atlas_repo(tmp_path, estate)
    persist_nodes(
        root,
        {
            "mixed": _o2_node(
                "mixed",
                owner_gate="CREDENTIAL",
                deps=["AUTHENTIC_ESTATE_ROOT", "HUMAN_APPROVAL"],
            )
        },
    )
    refresh_authentic_o2_node_states(root)
    after = load_nodes(root)["mixed"]
    assert after.DEPENDENCIES == ["HUMAN_APPROVAL"]
    assert "AUTHENTIC_ESTATE_ROOT" not in after.DEPENDENCIES
    assert after.OWNER_GATE == "CREDENTIAL"
    assert after.OWNER_GATE != "NONE"
    assert after.status == "BLOCKED_OWNER"


def test_invalid_estate_does_not_widen(tmp_path: Path) -> None:
    estate = tmp_path / "missing-root"
    root = tmp_path / "atlas"
    root.mkdir()
    _git_init(root)
    (root / "README.md").write_text("atlas\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
    persist_nodes(
        root,
        {
            "cred": _o2_node(
                "cred",
                owner_gate="CREDENTIAL",
                deps=["AUTHENTIC_ESTATE_ROOT"],
            )
        },
    )
    prior = _snapshot(load_nodes(root)["cred"])
    os.environ["AUTHENTIC_ESTATE_ROOT"] = str(estate)
    try:
        assert refresh_authentic_o2_node_states(root) == []
    finally:
        os.environ.pop("AUTHENTIC_ESTATE_ROOT", None)
    assert _snapshot(load_nodes(root)["cred"]) == prior


def test_missing_project_marker_does_not_widen(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    estate.mkdir()
    (estate / "README.md").write_text("no marker\n", encoding="utf-8")
    root = tmp_path / "atlas"
    root.mkdir()
    _git_init(root)
    (root / "README.md").write_text("atlas\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
    persist_nodes(
        root,
        {
            "cred": _o2_node(
                "cred",
                owner_gate="CREDENTIAL",
                deps=["AUTHENTIC_ESTATE_ROOT"],
            )
        },
    )
    write_estate_credential(root, estate, run_estate_preflight(estate))
    payload = json.loads(
        (root / ".atlas/orchestration/sdk-runtime/d148-authentic-estate-credential.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["AUTHENTIC_ESTATE_CREDENTIAL_SATISFIED"] is False
    assert payload["OWNER_CAPABILITY_GRANTED"] is False
    prior = _snapshot(load_nodes(root)["cred"])
    assert refresh_authentic_o2_node_states(root) == []
    assert _snapshot(load_nodes(root)["cred"]) == prior


def test_malformed_marker_does_not_widen(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    estate.mkdir()
    (estate / ".atlas-project.yaml").write_text("not: valid: yaml: [[", encoding="utf-8")
    root = tmp_path / "atlas"
    root.mkdir()
    _git_init(root)
    (root / "README.md").write_text("atlas\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
    persist_nodes(
        root,
        {
            "cred": _o2_node(
                "cred",
                owner_gate="CREDENTIAL",
                deps=["AUTHENTIC_ESTATE_ROOT"],
            )
        },
    )
    write_estate_credential(root, estate, run_estate_preflight(estate))
    prior = _snapshot(load_nodes(root)["cred"])
    assert refresh_authentic_o2_node_states(root) == []
    assert _snapshot(load_nodes(root)["cred"]) == prior


def test_stale_estate_credential_rejected(tmp_path: Path) -> None:
    estate = _init_estate(tmp_path / "estate")
    root = _init_atlas_repo(tmp_path, estate)
    persist_nodes(
        root,
        {
            "cred": _o2_node(
                "cred",
                owner_gate="CREDENTIAL",
                deps=["AUTHENTIC_ESTATE_ROOT"],
            )
        },
    )
    (estate / "docs" / "overview.md").write_text("Overview CHANGED.\n", encoding="utf-8")
    assert estate_credential_binding_ok(root) is False
    prior = _snapshot(load_nodes(root)["cred"])
    assert refresh_authentic_o2_node_states(root) == []
    assert _snapshot(load_nodes(root)["cred"]) == prior


def test_stale_main_certification_does_not_transfer(tmp_path: Path) -> None:
    estate = _init_estate(tmp_path / "estate")
    root = _init_atlas_repo(tmp_path, estate)
    rt = root / ".atlas" / "orchestration" / "sdk-runtime"
    (rt / "d148-o2-certification.json").write_text(
        json.dumps(
            {
                "live_main_head": "0" * 40,
                "AUTHENTIC_ESTATE_ROOT": str(estate.resolve()),
                "estate_fingerprint": "stale-fingerprint",
                "AUTHENTIC_INGEST_SATISFIED": True,
                "AUTHENTIC_COMPILE_SATISFIED": True,
                "AUTHENTIC_QUERY_SATISFIED": True,
                "AUTHENTIC_PILOT": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    persist_nodes(
        root,
        {
            "compile": _o2_node(
                "compile",
                owner_gate="CREDENTIAL",
                deps=["AUTHENTIC_ESTATE_ROOT"],
                package=AUTHENTIC_O2_PACKAGES[1],
            )
        },
    )
    # Stale cert must not mark compile ready via transferred ingest/compile flags.
    refresh_authentic_o2_node_states(root)
    after = load_nodes(root)["compile"]
    assert after.status != "READY"
    assert after.OWNER_GATE == "CREDENTIAL"
    assert "AUTHENTIC_ESTATE_ROOT" in after.DEPENDENCIES


def test_closure_integrity_failure_blocks_durable_gate_removal(tmp_path: Path) -> None:
    estate = _init_estate(tmp_path / "estate")
    root = _init_atlas_repo(tmp_path, estate)
    runbook = root / "docs" / "productization" / "CLEAN-MACHINE-PREP-RUNBOOK.md"
    runbook.parent.mkdir(parents=True)
    runbook.write_text(
        '$TARGET_HEAD = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n'
        "`TREE` = `bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb`\n",
        encoding="utf-8",
    )
    persist_nodes(
        root,
        {
            "cred": _o2_node(
                "cred",
                owner_gate="CREDENTIAL",
                deps=["AUTHENTIC_ESTATE_ROOT"],
            )
        },
    )
    prior = _snapshot(load_nodes(root)["cred"])
    assert refresh_authentic_o2_node_states(root) == []
    assert _snapshot(load_nodes(root)["cred"]) == prior


def test_refresh_idempotent_on_repeat(tmp_path: Path) -> None:
    estate = _init_estate(tmp_path / "estate")
    root = _init_atlas_repo(tmp_path, estate)
    persist_nodes(
        root,
        {
            "cred": _o2_node(
                "cred",
                owner_gate="CREDENTIAL",
                deps=["AUTHENTIC_ESTATE_ROOT"],
            )
        },
    )
    first = refresh_authentic_o2_node_states(root)
    mid = _snapshot(load_nodes(root)["cred"])
    second = refresh_authentic_o2_node_states(root)
    assert first == ["cred"]
    assert second == []
    assert _snapshot(load_nodes(root)["cred"]) == mid
    assert mid["status"] == "READY"


def test_superseded_estate_credential_is_not_resurrected(tmp_path: Path) -> None:
    estate = _init_estate(tmp_path / "estate")
    root = _init_atlas_repo(tmp_path, estate)
    persist_nodes(
        root,
        {
            "old": _o2_node(
                "old",
                owner_gate="CREDENTIAL",
                deps=["AUTHENTIC_ESTATE_ROOT"],
                status="SUPERSEDED",
            )
        },
    )
    prior = _snapshot(load_nodes(root)["old"])
    assert refresh_authentic_o2_node_states(root) == []
    assert _snapshot(load_nodes(root)["old"]) == prior


def test_existing_ready_node_stable(tmp_path: Path) -> None:
    estate = _init_estate(tmp_path / "estate")
    root = _init_atlas_repo(tmp_path, estate)
    persist_nodes(
        root,
        {
            "ready": _o2_node(
                "ready",
                owner_gate="NONE",
                deps=[],
                status="READY",
            )
        },
    )
    prior = _snapshot(load_nodes(root)["ready"])
    assert refresh_authentic_o2_node_states(root) == []
    assert _snapshot(load_nodes(root)["ready"]) == prior


def test_cross_project_credential_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    estate_a = _init_estate(tmp_path / "estate-a", project_id="alpha")
    estate_b = _init_estate(tmp_path / "estate-b", project_id="beta")
    root = _init_atlas_repo(tmp_path, estate_a)
    persist_nodes(
        root,
        {
            "cred": _o2_node(
                "cred",
                owner_gate="CREDENTIAL",
                deps=["AUTHENTIC_ESTATE_ROOT"],
            )
        },
    )
    monkeypatch.setenv("AUTHENTIC_ESTATE_ROOT", str(estate_b.resolve()))
    prior = _snapshot(load_nodes(root)["cred"])
    assert estate_credential_binding_ok(root) is False
    assert refresh_authentic_o2_node_states(root) == []
    assert _snapshot(load_nodes(root)["cred"]) == prior


def test_demo_fixture_estate_cannot_masquerade(tmp_path: Path) -> None:
    fixture = _init_estate(tmp_path / "tests" / "fixtures" / "demo" / "harbor-api")
    preflight = run_estate_preflight(fixture)
    assert preflight.preflight_pass is False
    root = tmp_path / "atlas"
    root.mkdir()
    _git_init(root)
    (root / "README.md").write_text("atlas\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
    write_estate_credential(root, fixture, preflight)
    persist_nodes(
        root,
        {
            "cred": _o2_node(
                "cred",
                owner_gate="CREDENTIAL",
                deps=["AUTHENTIC_ESTATE_ROOT"],
            ),
            "merge": _o2_node(
                "merge",
                owner_gate="MERGE",
                deps=["AUTHENTIC_ESTATE_ROOT"],
            ),
        },
    )
    prior_cred = _snapshot(load_nodes(root)["cred"])
    prior_merge = _snapshot(load_nodes(root)["merge"])
    assert refresh_authentic_o2_node_states(root) == []
    assert _snapshot(load_nodes(root)["cred"]) == prior_cred
    assert _snapshot(load_nodes(root)["merge"]) == prior_merge


def test_owner_capability_granted_string_true_is_rejected(tmp_path: Path) -> None:
    estate = _init_estate(tmp_path / "estate")
    root = _init_atlas_repo(tmp_path, estate)
    cred = root / ".atlas/orchestration/sdk-runtime/d148-authentic-estate-credential.json"
    payload = json.loads(cred.read_text(encoding="utf-8"))
    payload["OWNER_CAPABILITY_GRANTED"] = "true"
    cred.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    persist_nodes(
        root,
        {
            "cred": _o2_node(
                "cred",
                owner_gate="CREDENTIAL",
                deps=["AUTHENTIC_ESTATE_ROOT"],
            )
        },
    )
    prior = _snapshot(load_nodes(root)["cred"])
    assert estate_credential_binding_ok(root) is False
    assert refresh_authentic_o2_node_states(root) == []
    assert _snapshot(load_nodes(root)["cred"]) == prior


def test_missing_fingerprint_on_credential_file_is_rejected(tmp_path: Path) -> None:
    """P1 from IV: empty estate_fingerprint must not authorize mutation."""
    estate = _init_estate(tmp_path / "estate")
    root = _init_atlas_repo(tmp_path, estate)
    cred = root / ".atlas/orchestration/sdk-runtime/d148-authentic-estate-credential.json"
    payload = json.loads(cred.read_text(encoding="utf-8"))
    payload["estate_fingerprint"] = ""
    cred.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    persist_nodes(
        root,
        {
            "cred": _o2_node(
                "cred",
                owner_gate="CREDENTIAL",
                deps=["AUTHENTIC_ESTATE_ROOT"],
            )
        },
    )
    prior = _snapshot(load_nodes(root)["cred"])
    assert estate_credential_binding_ok(root) is False
    assert refresh_authentic_o2_node_states(root) == []
    assert _snapshot(load_nodes(root)["cred"]) == prior


def test_ready_work_items_demote_immutable_owner_gates(tmp_path: Path) -> None:
    from project_atlas.orchestration.sdk.mission_reconciler import ready_work_items

    root = tmp_path / "atlas"
    (root / ".atlas" / "orchestration" / "sdk-runtime").mkdir(parents=True)
    persist_nodes(
        root,
        {
            "merge-ready": _o2_node(
                "merge-ready",
                owner_gate="MERGE",
                deps=[],
                status="READY",
            ),
            "security-ready": _o2_node(
                "security-ready",
                owner_gate="SECURITY",
                deps=[],
                status="READY",
            ),
            "ok": _o2_node(
                "ok",
                owner_gate="NONE",
                deps=[],
                status="READY",
            ),
        },
    )
    items = ready_work_items(root, capacity=5)
    after = load_nodes(root)
    assert after["merge-ready"].status == "BLOCKED_OWNER"
    assert after["security-ready"].status == "BLOCKED_OWNER"
    assert after["ok"].status == "READY"
    assert [item.node_id for item in items] == ["ok"]


def test_write_credential_never_grants_owner_capability(tmp_path: Path) -> None:
    estate = _init_estate(tmp_path / "estate")
    root = _init_atlas_repo(tmp_path, estate)
    payload = json.loads(
        (root / ".atlas/orchestration/sdk-runtime/d148-authentic-estate-credential.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["OWNER_CAPABILITY_GRANTED"] is False
    assert payload["merge_authorized"] is False


def test_exception_after_preflight_leaves_gates_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    estate = _init_estate(tmp_path / "estate")
    root = _init_atlas_repo(tmp_path, estate)
    persist_nodes(
        root,
        {
            "cred": _o2_node(
                "cred",
                owner_gate="CREDENTIAL",
                deps=["AUTHENTIC_ESTATE_ROOT"],
            ),
            "merge": _o2_node(
                "merge",
                owner_gate="MERGE",
                deps=["AUTHENTIC_ESTATE_ROOT"],
            ),
        },
    )
    prior_cred = _snapshot(load_nodes(root)["cred"])
    prior_merge = _snapshot(load_nodes(root)["merge"])

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected persist failure")

    monkeypatch.setattr(
        "project_atlas.orchestration.sdk.mission_reconciler.persist_nodes",
        boom,
    )
    with pytest.raises(RuntimeError, match="injected persist failure"):
        refresh_authentic_o2_node_states(root)
    assert _snapshot(load_nodes(root)["cred"]) == prior_cred
    assert _snapshot(load_nodes(root)["merge"]) == prior_merge


def test_reconciler_does_not_rewrite_merge_to_credential(tmp_path: Path) -> None:
    """Failed on live main f0e0c979: SUPERSEDED MERGE was rewritten to CREDENTIAL."""
    root = tmp_path / "atlas"
    runtime = root / ".atlas" / "orchestration" / "sdk-runtime"
    runtime.mkdir(parents=True)
    persist_objectives(
        root,
        [
            MissionObjective(
                objective_id="O2",
                desired_state="SATISFIED",
                current_state="SATISFIED",
                blockers=[],
                completion_criteria="met",
            )
        ],
    )
    key = _idempotency_key(
        objective="O2",
        kind="IMPLEMENTATION",
        package="AS-CODER-ALPHA-AUTHENTIC-INGEST-001",
        surface="src/project_atlas/,tests/",
    )
    persist_nodes(
        root,
        {
            f"O2-IMPLEMENTATION-{key}": WorkNode(
                NODE_ID=f"O2-IMPLEMENTATION-{key}",
                OBJECTIVE_ID="O2",
                PACKAGE_ID="AS-CODER-ALPHA-AUTHENTIC-INGEST-001",
                TASK_KIND="IMPLEMENTATION",
                PRIORITY=92,
                DEPENDENCIES=["PR431"],
                ALLOWED_PATHS=["src/project_atlas/", "tests/"],
                SURFACE_SET=["src/project_atlas/", "tests/"],
                WORKER_ROLE="IMPLEMENTER",
                ACCEPTANCE_CRITERIA="Authentic ingest on real project docs",
                REQUIRED_VERIFICATION=["receipt", "idempotent"],
                OWNER_GATE="MERGE",
                GENERATION=1,
                IDEMPOTENCY_KEY=key,
                status="SUPERSEDED",
            )
        },
    )
    mission_reconcile(root, main_head="a" * 40)
    after = load_nodes(root)[f"O2-IMPLEMENTATION-{key}"]
    assert after.OWNER_GATE == "MERGE"
    assert after.OWNER_GATE != "NONE"
    assert after.OWNER_GATE != "CREDENTIAL"
    assert after.DEPENDENCIES == ["PR431"]


def test_reconciler_preserves_superseded_non_estate_credential(tmp_path: Path) -> None:
    root = tmp_path / "atlas"
    runtime = root / ".atlas" / "orchestration" / "sdk-runtime"
    runtime.mkdir(parents=True)
    persist_objectives(
        root,
        [
            MissionObjective(
                objective_id="O2",
                desired_state="SATISFIED",
                current_state="SATISFIED",
                blockers=[],
                completion_criteria="met",
            )
        ],
    )
    key = _idempotency_key(
        objective="O2",
        kind="IMPLEMENTATION",
        package="AS-CODER-ALPHA-AUTHENTIC-INGEST-001",
        surface="src/project_atlas/,tests/",
    )
    persist_nodes(
        root,
        {
            f"O2-IMPLEMENTATION-{key}": WorkNode(
                NODE_ID=f"O2-IMPLEMENTATION-{key}",
                OBJECTIVE_ID="O2",
                PACKAGE_ID="AS-CODER-ALPHA-AUTHENTIC-INGEST-001",
                TASK_KIND="IMPLEMENTATION",
                PRIORITY=92,
                DEPENDENCIES=["SOME_OTHER_CREDENTIAL"],
                ALLOWED_PATHS=["src/project_atlas/", "tests/"],
                SURFACE_SET=["src/project_atlas/", "tests/"],
                WORKER_ROLE="IMPLEMENTER",
                ACCEPTANCE_CRITERIA="Wait for an unrelated credential",
                REQUIRED_VERIFICATION=["receipt", "idempotent"],
                OWNER_GATE="CREDENTIAL",
                GENERATION=1,
                IDEMPOTENCY_KEY=key,
                status="SUPERSEDED",
            )
        },
    )
    mission_reconcile(root, main_head="a" * 40)
    after = load_nodes(root)[f"O2-IMPLEMENTATION-{key}"]
    assert after.OWNER_GATE == "CREDENTIAL"
    assert after.DEPENDENCIES == ["SOME_OTHER_CREDENTIAL"]
    assert "AUTHENTIC_ESTATE_ROOT" not in after.DEPENDENCIES


# ---------------------------------------------------------------------------
# D-176 / D149-001 — residual-dependency non-widening ADV matrix
# ---------------------------------------------------------------------------


def test_d149_001_case_a_estate_only_clears_gate(tmp_path: Path) -> None:
    estate = _init_estate(tmp_path / "estate")
    root = _init_atlas_repo(tmp_path, estate)
    persist_nodes(
        root,
        {
            "a": _o2_node(
                "a",
                owner_gate="CREDENTIAL",
                deps=["AUTHENTIC_ESTATE_ROOT"],
            )
        },
    )
    refresh_authentic_o2_node_states(root)
    after = load_nodes(root)["a"]
    assert after.DEPENDENCIES == []
    assert after.OWNER_GATE == "NONE"
    assert after.status == "READY"


def test_d149_001_case_b_other_credential_keeps_gate(tmp_path: Path) -> None:
    estate = _init_estate(tmp_path / "estate")
    root = _init_atlas_repo(tmp_path, estate)
    persist_nodes(
        root,
        {
            "b": _o2_node(
                "b",
                owner_gate="CREDENTIAL",
                deps=["AUTHENTIC_ESTATE_ROOT", "OTHER_CREDENTIAL"],
            )
        },
    )
    refresh_authentic_o2_node_states(root)
    after = load_nodes(root)["b"]
    assert after.DEPENDENCIES == ["OTHER_CREDENTIAL"]
    assert after.OWNER_GATE == "CREDENTIAL"
    assert after.status == "BLOCKED_OWNER"


def test_d149_001_case_c_human_approval_keeps_blocking_gate(tmp_path: Path) -> None:
    """Claude reproduction: CREDENTIAL + estate + HUMAN_APPROVAL must not → NONE."""
    estate = _init_estate(tmp_path / "estate")
    root = _init_atlas_repo(tmp_path, estate)
    persist_nodes(
        root,
        {
            "c": _o2_node(
                "c",
                owner_gate="CREDENTIAL",
                deps=["AUTHENTIC_ESTATE_ROOT", "HUMAN_APPROVAL"],
                status="BLOCKED_OWNER",
            )
        },
    )
    refresh_authentic_o2_node_states(root)
    after = load_nodes(root)["c"]
    assert after.DEPENDENCIES == ["HUMAN_APPROVAL"]
    assert after.OWNER_GATE != "NONE"
    assert after.OWNER_GATE == "CREDENTIAL"
    assert after.status == "BLOCKED_OWNER"


def test_d149_001_two_residual_deps_keep_gate(tmp_path: Path) -> None:
    estate = _init_estate(tmp_path / "estate")
    root = _init_atlas_repo(tmp_path, estate)
    persist_nodes(
        root,
        {
            "two": _o2_node(
                "two",
                owner_gate="CREDENTIAL",
                deps=["AUTHENTIC_ESTATE_ROOT", "HUMAN_APPROVAL", "OTHER_CREDENTIAL"],
            )
        },
    )
    refresh_authentic_o2_node_states(root)
    after = load_nodes(root)["two"]
    assert after.DEPENDENCIES == ["HUMAN_APPROVAL", "OTHER_CREDENTIAL"]
    assert after.OWNER_GATE == "CREDENTIAL"
    assert after.OWNER_GATE != "NONE"


def test_d149_001_duplicated_estate_dep_cleared_once(tmp_path: Path) -> None:
    estate = _init_estate(tmp_path / "estate")
    root = _init_atlas_repo(tmp_path, estate)
    persist_nodes(
        root,
        {
            "dup": _o2_node(
                "dup",
                owner_gate="CREDENTIAL",
                deps=["AUTHENTIC_ESTATE_ROOT", "AUTHENTIC_ESTATE_ROOT", "HUMAN_APPROVAL"],
            )
        },
    )
    refresh_authentic_o2_node_states(root)
    after = load_nodes(root)["dup"]
    assert after.DEPENDENCIES == ["HUMAN_APPROVAL"]
    assert "AUTHENTIC_ESTATE_ROOT" not in after.DEPENDENCIES
    assert after.OWNER_GATE == "CREDENTIAL"


@pytest.mark.parametrize(
    "gate",
    ["MERGE", "SECURITY", "HUMAN", "OWNER", "RELEASE", "GOVERNOR", "SIGNOFF"],
)
def test_d149_001_case_d_immutable_gates_unchanged(tmp_path: Path, gate: str) -> None:
    estate = _init_estate(tmp_path / "estate")
    root = _init_atlas_repo(tmp_path, estate)
    persist_nodes(
        root,
        {
            "imm": _o2_node(
                "imm",
                owner_gate=gate,
                deps=["AUTHENTIC_ESTATE_ROOT", "HUMAN_APPROVAL"],
            )
        },
    )
    prior = _snapshot(load_nodes(root)["imm"])
    assert refresh_authentic_o2_node_states(root) == []
    assert _snapshot(load_nodes(root)["imm"]) == prior


@pytest.mark.parametrize("status", ["SUPERSEDED", "DISPATCHED", "RUNNING", "COMPLETED"])
def test_d149_001_case_f_terminal_statuses_not_resurrected(tmp_path: Path, status: str) -> None:
    estate = _init_estate(tmp_path / "estate")
    root = _init_atlas_repo(tmp_path, estate)
    persist_nodes(
        root,
        {
            "term": _o2_node(
                "term",
                owner_gate="CREDENTIAL",
                deps=["AUTHENTIC_ESTATE_ROOT", "HUMAN_APPROVAL"],
                status=status,
            )
        },
    )
    prior = _snapshot(load_nodes(root)["term"])
    assert refresh_authentic_o2_node_states(root) == []
    assert _snapshot(load_nodes(root)["term"]) == prior


def test_d149_001_residual_invariant_global(tmp_path: Path) -> None:
    """RESIDUAL_DEPENDENCIES > 0 → OWNER_GATE != NONE after refresh."""
    estate = _init_estate(tmp_path / "estate")
    root = _init_atlas_repo(tmp_path, estate)
    persist_nodes(
        root,
        {
            "r": _o2_node(
                "r",
                owner_gate="CREDENTIAL",
                deps=["AUTHENTIC_ESTATE_ROOT", "HUMAN_APPROVAL"],
                status="BLOCKED_OWNER",
            )
        },
    )
    refresh_authentic_o2_node_states(root)
    after = load_nodes(root)["r"]
    assert len(after.DEPENDENCIES) > 0
    assert after.OWNER_GATE != "NONE"


def test_d149_001_repeated_refresh_idempotent_with_residual(tmp_path: Path) -> None:
    estate = _init_estate(tmp_path / "estate")
    root = _init_atlas_repo(tmp_path, estate)
    persist_nodes(
        root,
        {
            "id": _o2_node(
                "id",
                owner_gate="CREDENTIAL",
                deps=["AUTHENTIC_ESTATE_ROOT", "HUMAN_APPROVAL"],
            )
        },
    )
    first = refresh_authentic_o2_node_states(root)
    snap = _snapshot(load_nodes(root)["id"])
    second = refresh_authentic_o2_node_states(root)
    assert first == ["id"]
    assert second == []
    assert _snapshot(load_nodes(root)["id"]) == snap
    assert snap["OWNER_GATE"] == "CREDENTIAL"
    assert snap["DEPENDENCIES"] == ["HUMAN_APPROVAL"]


def test_d149_001_none_gate_with_residual_restored_to_credential(tmp_path: Path) -> None:
    """Absolute invariant: residual deps must not leave OWNER_GATE as NONE."""
    estate = _init_estate(tmp_path / "estate")
    root = _init_atlas_repo(tmp_path, estate)
    persist_nodes(
        root,
        {
            "none_res": _o2_node(
                "none_res",
                owner_gate="NONE",
                deps=["AUTHENTIC_ESTATE_ROOT", "HUMAN_APPROVAL"],
                status="BLOCKED_OWNER",
            )
        },
    )
    refresh_authentic_o2_node_states(root)
    after = load_nodes(root)["none_res"]
    assert after.DEPENDENCIES == ["HUMAN_APPROVAL"]
    assert after.OWNER_GATE != "NONE"
    assert after.OWNER_GATE == "CREDENTIAL"
    assert after.status == "BLOCKED_OWNER"
