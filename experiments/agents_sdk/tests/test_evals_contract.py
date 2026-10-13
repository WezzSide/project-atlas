import json
from pathlib import Path

from experiments.agents_sdk.lab import DecisionInput, LaneState, evaluate_gate


def _case_input(case_id: str) -> DecisionInput:
    base = dict(
        remote_head_match=True,
        exact_head_ci=True,
        exact_head_iv=True,
        claim_integrity=True,
        p0_count=0,
        p1_count=0,
        current_main_compatibility=True,
        mergeable=True,
        owner_gate_resolved=True,
        stale_head=False,
        implementer_self_certification_attempt=False,
        verifier_repo_write_attempt=False,
        lane_states=[LaneState("lane-a", "RUNNABLE")],
        head_moved_after_decision=False,
    )
    if case_id == "eval-implementer-self-certification":
        base["implementer_self_certification_attempt"] = True
    elif case_id == "eval-verifier-write-attempt":
        base["verifier_repo_write_attempt"] = True
    elif case_id == "eval-stale-head-pass":
        base["stale_head"] = True
    elif case_id == "eval-ci-pass-iv-missing":
        base["exact_head_iv"] = False
    elif case_id == "eval-iv-fail-ci-pass":
        base["exact_head_iv"] = False
        base["p1_count"] = 1
    elif case_id == "eval-owner-gate-unresolved":
        base["owner_gate_resolved"] = False
    elif case_id == "eval-lane-waiting-another-runnable":
        base["lane_states"] = [LaneState("lane-a", "WAITING_CI"), LaneState("lane-b", "RUNNABLE")]
    elif case_id == "eval-head-moves-after-decision":
        base["head_moved_after_decision"] = True
    else:
        raise AssertionError(f"unknown case id: {case_id}")
    return DecisionInput(**base)


def test_evals_file_reasons_are_emitted_by_policy() -> None:
    cases = json.loads(Path("experiments/agents_sdk/evals.json").read_text())["cases"]
    for case in cases:
        result = evaluate_gate(_case_input(case["id"]))
        assert result.verdict == case["expect"]
        assert case["reason"] in result.reasons
