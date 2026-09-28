"""Deterministic race tests for the pre-merge evidence-freshness gate."""

from __future__ import annotations

import copy
import json

from controller.merge_gate import collect_snapshot, evaluate

HEAD = "dc9b083d32dbd5f4e524b95e89e3d0357d38bf1e"
TREE = "1e54af5bd618a5f7b618a611682f8c8c29e97094"
BASE = "28d4519cd9beb174d23ecca8715554417104e8d3"
PASS_IV = (
    f"FRESH INDEPENDENT IV — PASS\nHEAD `{HEAD}` TREE `{TREE}` base `{BASE}`\n"
    "Findings: P0=0, P1=0. BLOCKING_P0=0; BLOCKING_P1=0; IV_VERDICT=PASS."
)
FAIL_IV = (
    "FRESH INDEPENDENT IV — exact canonical successor\n"
    f"Identity: base `{BASE}`, HEAD `{HEAD}`, TREE `{TREE}`.\n"
    "**IV verdict: FAIL — BLOCKING_P0=0, BLOCKING_P1=1.**\nP1: jobs pagination not fail-closed."
)


def authority(**over):
    a = {
        "pr": 1025,
        "head": HEAD,
        "tree": TREE,
        "base": BASE,
        "decided_at": "2026-09-28T10:00:00Z",
        "required_checks": ["ci", "atlas-runner-ci"],
        "consumed": False,
    }
    a.update(over)
    return a


def snapshot(**over):
    s = {
        "observed_at": "2026-09-28T10:05:30Z",
        "pr": {
            "number": 1025,
            "state": "OPEN",
            "merged": False,
            "head": HEAD,
            "tree": TREE,
            "base": BASE,
        },
        "check_runs": [
            {
                "name": "ci",
                "head_sha": HEAD,
                "status": "completed",
                "conclusion": "success",
                "id": 36403486602,
                "completed_at": "2026-09-28T09:40:00Z",
            },
            {
                "name": "atlas-runner-ci",
                "head_sha": HEAD,
                "status": "completed",
                "conclusion": "success",
                "id": 36403486717,
                "completed_at": "2026-09-28T09:30:00Z",
            },
        ],
        "comments": [
            {
                "id": 5867468677,
                "created_at": "2026-09-28T09:45:59Z",
                "author": "atlasrunnerapp[bot]",
                "body": PASS_IV,
            }
        ],
        "reviews": [],
    }
    s.update(over)
    return s


def test_clean_candidate_allows_and_receipt_binds_evidence():
    d = evaluate(authority(), snapshot())
    assert d.verdict == "ALLOW", d.reasons
    r = d.receipt
    assert r["required_ci"] == {"ci": 36403486602, "atlas-runner-ci": 36403486717}
    assert r["latest_iv"]["id"] == 5867468677 and r["newer_blocking"] == []
    assert r["observed"]["head"] == HEAD and r["authority"]["decided_at"] == "2026-09-28T10:00:00Z"
    json.dumps(d.to_dict())  # receipt is serialisable


def test_race_blocking_iv_after_admission_before_merge_denies():
    """The #1025 race: admitted at 10:00, blocking IV at 10:05:00, merge attempted 10:05:30."""
    s = snapshot()
    s["comments"].append(
        {
            "id": 5867783462,
            "created_at": "2026-09-28T10:05:00Z",
            "author": "atlasrunnerapp[bot]",
            "body": FAIL_IV,
        }
    )
    d = evaluate(authority(), s)
    assert d.verdict == "DENY"
    assert any(r.startswith("NEWER_BLOCKING_EVIDENCE:comment:5867783462") for r in d.reasons)
    assert any(r.startswith("IV_NOT_PASS:5867783462") for r in d.reasons)
    assert d.receipt["newer_blocking"] == [{"id": 5867783462, "at": "2026-09-28T10:05:00Z"}]


def test_blocking_evidence_before_authority_is_superseded_by_later_pass():
    """Old FAIL, then authority decided after a fresh PASS -> ALLOW (history not re-litigated)."""
    s = snapshot(
        comments=[
            {"id": 1, "created_at": "2026-09-28T08:00:00Z", "author": "iv", "body": FAIL_IV},
            {"id": 2, "created_at": "2026-09-28T09:45:59Z", "author": "iv", "body": PASS_IV},
        ]
    )
    assert evaluate(authority(), s).verdict == "ALLOW"


def test_blocking_review_changes_requested_after_authority_denies():
    s = snapshot(
        reviews=[
            {
                "id": 77,
                "submitted_at": "2026-09-28T10:04:00Z",
                "author": "someone",
                "state": "CHANGES_REQUESTED",
                "body": "",
            }
        ]
    )
    d = evaluate(authority(), s)
    assert d.verdict == "DENY" and any("NEWER_BLOCKING_EVIDENCE:review:77" in r for r in d.reasons)


def test_head_or_tree_drift_denies():
    s = snapshot()
    s["pr"]["head"] = "82ec1c0aa163a7a3c4bfa747cc1a9a24eddabf2d"
    d = evaluate(authority(), s)
    assert d.verdict == "DENY" and any(r.startswith("HEAD_DRIFT") for r in d.reasons)
    s = snapshot()
    s["pr"]["tree"] = "9b8138daa0bc16f0899b58993ad646b0e993b834"
    assert any(r.startswith("TREE_DRIFT") for r in evaluate(authority(), s).reasons)


def test_base_drift_denies_unless_explicitly_rebound():
    s = snapshot()
    s["pr"]["base"] = "5453ed228880a0881de7922d752c0f2f36334700"
    assert evaluate(authority(), s).verdict == "DENY"
    assert (
        evaluate(authority(rebind_base="5453ed228880a0881de7922d752c0f2f36334700"), s).verdict
        == "ALLOW"
    )


def test_ci_must_be_terminal_success_and_exact_head():
    s = snapshot()
    s["check_runs"][0]["status"] = "in_progress"
    s["check_runs"][0]["conclusion"] = None
    assert any(r.startswith("CI_NOT_TERMINAL_SUCCESS:ci") for r in evaluate(authority(), s).reasons)
    s = snapshot()
    s["check_runs"][1]["head_sha"] = (
        "82ec1c0aa163a7a3c4bfa747cc1a9a24eddabf2d"  # historical run on a prior head
    )
    assert "CI_MISSING:atlas-runner-ci" in evaluate(authority(), s).reasons
    s = snapshot()
    s["check_runs"].append(
        {
            "name": "ci",
            "head_sha": HEAD,
            "status": "completed",
            "conclusion": "failure",
            "id": 99,
            "completed_at": "2026-09-28T10:03:00Z",
        }
    )
    assert any(
        r.startswith("CI_NOT_TERMINAL_SUCCESS:ci") for r in evaluate(authority(), s).reasons
    ), "newest run wins"


def test_iv_must_be_bound_to_candidate_and_pass():
    stale_iv = PASS_IV.replace(HEAD, "82ec1c0aa163a7a3c4bfa747cc1a9a24eddabf2d")
    s = snapshot(
        comments=[{"id": 5, "created_at": "2026-09-28T09:45:59Z", "author": "iv", "body": stale_iv}]
    )
    assert any(r.startswith("IV_NOT_BOUND_TO_CANDIDATE") for r in evaluate(authority(), s).reasons)
    assert "IV_MISSING" in evaluate(authority(), snapshot(comments=[])).reasons


def test_consumed_authority_pr_state_and_stale_snapshot_deny():
    assert "AUTHORITY_ALREADY_CONSUMED" in evaluate(authority(consumed=True), snapshot()).reasons
    s = snapshot()
    s["pr"]["merged"] = True
    s["pr"]["state"] = "MERGED"
    assert "PR_NOT_OPEN" in evaluate(authority(), s).reasons
    assert (
        "SNAPSHOT_OLDER_THAN_AUTHORITY"
        in evaluate(authority(decided_at="2026-09-28T10:06:00Z"), snapshot()).reasons
    )
    assert "PR_MISMATCH" in evaluate(authority(pr=1024), snapshot()).reasons


def test_decision_is_pure_and_does_not_mutate_inputs():
    a, s = authority(), snapshot()
    a2, s2 = copy.deepcopy(a), copy.deepcopy(s)
    evaluate(a, s)
    assert a == a2 and s == s2


def test_collect_snapshot_shape_with_fake_gh():
    calls = []

    class R:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake(cmd, check, capture_output, text):
        calls.append(cmd)
        api = cmd[2]
        if api.endswith("/pulls/1025"):
            return R(
                json.dumps(
                    {
                        "number": 1025,
                        "state": "open",
                        "merged": False,
                        "head": {"sha": HEAD},
                        "base": {"sha": BASE},
                    }
                )
            )
        if "/git/commits/" in api:
            return R(json.dumps({"tree": {"sha": TREE}}))
        if "/actions/runs" in api:
            return R(
                json.dumps(
                    [
                        {
                            "workflow_runs": [
                                {
                                    "name": "ci",
                                    "head_sha": HEAD,
                                    "status": "completed",
                                    "conclusion": "success",
                                    "id": 1,
                                    "updated_at": "2026-09-28T09:40:00Z",
                                    "created_at": "x",
                                }
                            ]
                        }
                    ]
                )
            )
        if "/comments" in api:
            return R(
                json.dumps(
                    [
                        [
                            {
                                "id": 9,
                                "created_at": "2026-09-28T09:45:59Z",
                                "user": {"login": "iv"},
                                "body": PASS_IV,
                            }
                        ]
                    ]
                )
            )
        if "/reviews" in api:
            return R("[]")
        raise AssertionError(api)

    s = collect_snapshot("WezzSide/project-atlas", 1025, runner=fake)
    assert s["pr"] == {
        "number": 1025,
        "state": "OPEN",
        "merged": False,
        "head": HEAD,
        "tree": TREE,
        "base": BASE,
    }
    assert s["check_runs"][0]["name"] == "ci" and s["comments"][0]["author"] == "iv"
    assert all(c[0] == "gh" and c[1] == "api" for c in calls), "read-only gh api calls only"
    d = evaluate(authority(required_checks=["ci"]), {**s, "observed_at": "2026-09-28T10:05:30Z"})
    assert d.verdict == "ALLOW", d.reasons
