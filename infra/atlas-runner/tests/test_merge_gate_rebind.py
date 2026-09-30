"""Regression tests for explicit rebind semantics of the merge gate (gate-repair 001).

Two identities are kept independent:
  * historical PR base  (pulls/<n>.base.sha)  -> provenance, must equal authority.base
  * current target head (live tip of base ref) -> must equal authority.base, or
                                                  authority.rebind_base when rebound
`rebind_base` is never a merge-base and never a mergeability claim.
"""

from __future__ import annotations

import json

from controller.merge_gate import _body_sha256, collect_snapshot, evaluate

A = "7276b6719612c153b6b7d26b491c6760266c2f47"  # historical base (old main)
B = "40ad664e16bc7f88735ab3801fab7276da4ab08a"  # advanced main
C = "5453ed228880a0881de7922d752c0f2f36334700"  # unrelated sha
X = "9b8138daa0bc16f0899b58993ad646b0e993b834"
HEAD = "bb7e0251e75a9b64ac108fa28d7f2f3071c99a27"
TREE = "08b93f264fd7ff167f8ac8f21508b3083eca23d8"
IV_ID = 5912816899
IV_AT = "2026-09-30T14:20:10Z"
IV_AUTHOR = "WezzSide"
IV_BODY = (
    "CANDIDATE_PR=1029\n"
    f"HEAD={HEAD}\nTREE={TREE}\nBASE={A}\n"
    "IV_VERDICT=PASS\nBLOCKING_P0=0\nBLOCKING_P1=0"
)


def authority(**over):
    a = {
        "pr": 1029,
        "head": HEAD,
        "tree": TREE,
        "base": A,
        "decided_at": "2026-09-30T14:29:55Z",
        "required_checks": ["ci"],
        "iv_binding": {
            "iv_evidence_id": IV_ID,
            "iv_author": IV_AUTHOR,
            "iv_updated_at": IV_AT,
            "iv_body_sha256": _body_sha256(IV_BODY),
        },
        "trusted_iv_authors": [IV_AUTHOR],
        "consumed": False,
    }
    a.update(over)
    return a


def snapshot(*, base=A, base_ref="main", current=A, **over):
    s = {
        "observed_at": "2026-09-30T14:30:01Z",
        "pr": {
            "number": 1029,
            "state": "OPEN",
            "merged": False,
            "head": HEAD,
            "tree": TREE,
            "base": base,
            "base_ref": base_ref,
            "current_base_head": current,
        },
        "check_runs": [
            {
                "name": "ci",
                "head_sha": HEAD,
                "status": "completed",
                "conclusion": "success",
                "id": 1,
                "completed_at": "2026-09-30T13:00:00Z",
            }
        ],
        "comments": [
            {
                "id": IV_ID,
                "created_at": IV_AT,
                "updated_at": IV_AT,
                "author": IV_AUTHOR,
                "body": IV_BODY,
            }
        ],
        "reviews": [],
    }
    s.update(over)
    return s


def reasons(a, s):
    return evaluate(a, s).reasons


def test_a_original_base_no_drift_allows():
    d = evaluate(authority(), snapshot())
    assert d.verdict == "ALLOW", d.reasons


def test_b_target_advanced_without_rebind_denies():
    r = reasons(authority(), snapshot(current=B))
    assert any(x.startswith("CURRENT_BASE_DRIFT") for x in r), r


def test_c_target_advanced_with_correct_rebind_allows():
    d = evaluate(authority(rebind_base=B), snapshot(current=B))
    assert d.verdict == "ALLOW", d.reasons
    assert d.receipt["authority"]["rebind_base"] == B
    assert d.receipt["observed"]["current_base_head"] == B
    assert d.receipt["observed"]["base"] == A  # provenance preserved, not rewritten


def test_d_wrong_rebind_denies():
    r = reasons(authority(rebind_base=C), snapshot(current=B))
    assert any(x.startswith("REBIND_BASE_MISMATCH") for x in r), r


def test_e_pr_base_provenance_drift_denies_even_with_rebind():
    r = reasons(authority(rebind_base=B), snapshot(base=X, current=B))
    assert any(x.startswith("BASE_DRIFT") for x in r), r


def test_f_target_ref_retarget_denies_even_when_sha_matches():
    r = reasons(authority(), snapshot(base_ref="release"))
    assert any(x.startswith("BASE_REF_MISMATCH") for x in r), r
    r = reasons(authority(rebind_base=B), snapshot(base_ref="other", current=B))
    assert any(x.startswith("BASE_REF_MISMATCH") for x in r), r
    # an explicit authority ref binds too
    assert evaluate(authority(base_ref="release"), snapshot(base_ref="release")).verdict == "ALLOW"


def test_g_realistic_1029_reproduction_no_false_base_drift():
    a = authority(rebind_base=B)
    s = snapshot(base=A, current=B)  # GitHub keeps reporting the historical base
    d = evaluate(a, s)
    assert d.verdict == "ALLOW", d.reasons
    assert not any(x.startswith("BASE_DRIFT") for x in d.reasons)


def test_missing_current_base_head_fails_closed():
    s = snapshot()
    del s["pr"]["current_base_head"]
    assert "CURRENT_BASE_UNOBSERVED" in reasons(authority(rebind_base=B), s)


def test_rebind_is_not_a_merge_base_and_not_mergeability():
    # candidate provenance base A, current target B, rebind B: allowed by identity binding alone;
    # a rebind equal to the historical base while the target advanced must still fail closed
    r = reasons(authority(rebind_base=A), snapshot(current=B))
    assert any(x.startswith("REBIND_BASE_MISMATCH") for x in r), r


def _fake_runner(historical, tip, ref="main", ref_type="commit", ref_name=None):
    class R:
        def __init__(self, stdout):
            self.stdout = stdout

    calls = []

    def fake(cmd, check, capture_output, text):
        calls.append(cmd)
        api = cmd[2]
        if api.endswith("/pulls/1029"):
            return R(
                json.dumps(
                    {
                        "number": 1029,
                        "state": "open",
                        "merged": False,
                        "head": {"sha": HEAD},
                        "base": {"sha": historical, "ref": ref},
                    }
                )
            )
        if "/git/ref/heads/" in api:
            return R(
                json.dumps(
                    {
                        "ref": ref_name or f"refs/heads/{ref}",
                        "object": {"type": ref_type, "sha": tip},
                    }
                )
            )
        if "/git/commits/" in api:
            return R(json.dumps({"tree": {"sha": TREE}}))
        if "/actions/runs" in api:
            return R("[]")
        if "/comments" in api or "/reviews" in api:
            return R("[]")
        raise AssertionError(api)

    return fake, calls


def test_collector_observes_live_target_head_independently_of_pr_base():
    fake, calls = _fake_runner(historical=A, tip=B)
    s = collect_snapshot("WezzSide/project-atlas", 1029, runner=fake)
    assert s["pr"]["base"] == A
    assert s["pr"]["base_ref"] == "main"
    assert s["pr"]["current_base_head"] == B
    assert any("/git/ref/heads/main" in c[2] for c in calls)
    assert all(c[0] == "gh" and c[1] == "api" for c in calls), "read-only gh api calls only"


def test_collector_fails_closed_on_unobservable_target_head():
    import pytest

    from controller.merge_gate import MalformedEvidence

    for kwargs in (
        {"ref_type": "tag"},
        {"ref_name": "refs/heads/other"},
        {"tip": "not-a-sha"},
    ):
        args = {"historical": A, "tip": B, **kwargs}
        fake, _ = _fake_runner(**args)
        with pytest.raises(MalformedEvidence):
            collect_snapshot("WezzSide/project-atlas", 1029, runner=fake)
