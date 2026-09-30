"""First-run package for ATLAS-DEVQ-0001 (AS-DEVLOOP-001).

Everything needed for ONE bounded workflow dispatch, generated deterministically from the sealed
WorkItem. No secret values. ``ANTHROPIC_API_KEY`` presence is recorded as UNKNOWN until the owner
confirms it; the package never inspects secrets.
"""

from __future__ import annotations

import json
from typing import Any

from project_atlas.orchestration.autonomy.dev_contracts import WorkItem, make_work
from project_atlas.orchestration.autonomy.dev_fabric_adapter import (
    AGENT_BRANCH,
    EVIDENCE_ARTIFACT,
    REPORT_ARTIFACT,
    VERIFY_WORKFLOW,
    build_dispatch_payload,
)

TASK_ID = "ATLAS-DEVQ-0001"
BASE = "40ad664e16bc7f88735ab3801fab7276da4ab08a"
TASK_TESTS = (
    "PYTHONPATH=src python -m pytest tests/unit/test_as_graph_005_projections.py "
    "tests/unit/test_as_graph_005_adversarial.py "
    "tests/unit/test_as_obsidian_capture_001_f11_mkdir_boundary.py -q --no-cov",
    "PYTHONPATH=src python -m pytest tests/unit -k 'f14 or graph_projection' -q --no-cov",
    "python -m ruff check src/project_atlas/graph_projections.py tests/unit",
    "python -m mypy src/project_atlas/graph_projections.py",
)
STATEMENT = (
    "Fix GitHub issue #757 (F14) in src/project_atlas/graph_projections.py. In the canonical "
    "promotion loop, a read-only output directory (staged.write_bytes), an unreadable existing "
    "target (path.read_bytes() == plan[path]) and ENAMETOOLONG (path.exists() and not "
    "path.is_file()) currently escape as raw OSError; each must raise GraphProjectionError naming "
    "the path, mirroring the existing F11 unwritable-note-directory:<Type>:<path> pattern. "
    "Assert on the measured raised exception class, never on hard-coded platform-coupled strings. "
    "Add a new test file tests/unit/test_as_obsidian_capture_001_f14_write_boundary.py."
)


def make_devq_0001_work() -> WorkItem:
    return make_work(
        task_id=TASK_ID,
        execution_id=f"{TASK_ID}-E1",
        lineage_root=TASK_ID,
        repository="WezzSide/project-atlas",
        base_revision=BASE,
        authority_ref="OWNER-DIRECTIVE-2026-09-30-AUTONOMOUS-LOOP-FIRST-TASK",
        allowed_paths=("src/project_atlas/graph_projections.py", "tests/unit/"),
        forbidden_paths=(
            "infra/atlas-runner/controller",
            "src/project_atlas/ingestion.py",
            "src/project_atlas/orchestration/autonomy/trust.py",
            ".github/",
        ),
        expected_outputs=("dedicated atlas/agent-* branch with exact HEAD/TREE",),
        acceptance_contract=(
            "issue #757 F14: read-only dir, unreadable target and ENAMETOOLONG raise "
            "GraphProjectionError naming the path",
            "task-specific tests (graph_projections, F11 boundary, new F14 test) pass; new F14 "
            "tests fail on base and pass on head",
            "ruff and mypy clean on changed files",
            "infra/atlas-runner tests are corroborating only, never sufficient",
        ),
    )


def task_statement(work: WorkItem) -> tuple[str, tuple[str, ...]]:
    if work.lineage_root != TASK_ID:
        raise ValueError("no statement registered for this lineage")
    if work.attempt > 1:
        return (
            STATEMENT + " This is a REPAIR attempt: resolve every RESOLVE:<finding_id> listed "
            "in the acceptance contract on top of the previous result branch.",
            TASK_TESTS,
        )
    return STATEMENT, TASK_TESTS


def build_package(work: WorkItem | None = None) -> dict[str, Any]:
    w = work or make_devq_0001_work()
    statement, commands = task_statement(w)
    payload = build_dispatch_payload(
        w, base_branch="main", task_statement=statement, acceptance_commands=commands
    )
    return {
        "package_version": 1,
        "task_id": w.task_id,
        "execution_id": w.execution_id,
        "work_seal": w.seal,
        "repository": w.repository,
        "base_revision": w.base_revision,
        "allowed_paths": list(w.allowed_paths),
        "forbidden_paths": list(w.forbidden_paths),
        "authority_reference": w.authority_ref,
        "workflow": payload.workflow,
        "workflow_ref": payload.ref,
        "workflow_inputs": payload.inputs,
        "workflow_inputs_sha256": payload.sha256(),
        "expected_agent_branch_pattern": AGENT_BRANCH.pattern,
        "acceptance": {
            "contract": list(w.acceptance_contract),
            "commands": list(commands),
            "note": "infra/atlas-runner tests are corroborating evidence only",
        },
        "result_discovery_contract": {
            "run": "single workflow_dispatch run of atlas-agent-execute.yml on main created after "
            "the write-ahead DISPATCH record; ambiguity => refuse",
            "branch": "atlas/agent-<run_id>-<run_attempt>; head sha = result revision; tree via "
            "git commit; merge-base with the sealed base must equal the base; changed paths must "
            "lie within allowed_paths and outside forbidden_paths",
            "evidence_artifact": EVIDENCE_ARTIFACT,
            "ingestion": "ingest_report -> Crosswalk-bound ResultRecord",
        },
        "verification_profile": {
            "profile": "github_hosted",
            "workflow": VERIFY_WORKFLOW,
            "report_artifact": REPORT_ARTIFACT,
            "policy": "PASS iff verifier verdict VERIFIED for the source run AND every REQUIRED "
            "check (control-plane + the 3 quality jobs) is present, completed and success on the "
            "exact result head, and no other check failed (draft evidence PR opened by the "
            "adapter); UNESTABLISHED or missing/in-progress checks => no verdict",
            "required_checks": [
                "control-plane",
                "quality (ubuntu-latest, 3.12, full)",
                "quality (ubuntu-latest, 3.13, compat)",
                "quality (windows-latest, 3.12, windows)",
            ],
        },
        "failure_ceiling": {
            "max_attempts": w.max_attempts,
            "repair_base": "previous result branch",
        },
        "abort_conditions": [
            "main is not at the sealed base revision before dispatch",
            "run correlation ambiguous (correlation is serialised: one unbound dispatch at a time; "
            "no foreign/manual dispatch of atlas-agent-execute during the run)",
            "result branch moves after ingestion",
            "result touches forbidden or out-of-scope paths",
            "verifier verdict REJECTED/UNESTABLISHED repeatedly or attempt ceiling reached",
        ],
        "rollback": "delete the atlas/agent-* branch and close the draft evidence PR; nothing is "
        "merged and nothing touches main",
        "secrets": {"ANTHROPIC_API_KEY": "UNKNOWN (owner confirmation pending; never inspected)"},
        "grant_required": "ONE_WORKFLOW_DISPATCH_GRANT",
    }


def render_package(pkg: dict[str, Any]) -> str:
    return json.dumps(pkg, indent=2, sort_keys=True) + "\n"
