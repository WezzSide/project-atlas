"""Successor matrix: closes the fail-open surfaces found by the independent IV of #1027@9c06069c."""

from __future__ import annotations

import json
import subprocess

from controller.merge_gate import collect_snapshot, evaluate

HEAD = "dc9b083d32dbd5f4e524b95e89e3d0357d38bf1e"
TREE = "1e54af5bd618a5f7b618a611682f8c8c29e97094"
BASE = "28d4519cd9beb174d23ecca8715554417104e8d3"
T0 = "2026-09-28T10:00:00Z"


def iv(verdict_line: str, p0: int = 0, p1: int = 0, extra: str = "") -> str:
    return (
        f"FRESH INDEPENDENT IV\nHEAD `{HEAD}` TREE `{TREE}` base `{BASE}`\n"
        f"BLOCKING_P0={p0}; BLOCKING_P1={p1}\n{verdict_line}\n{extra}"
    )


def authority(**over):
    a = {
        "pr": 1025,
        "head": HEAD,
        "tree": TREE,
        "base": BASE,
        "decided_at": T0,
        "required_checks": ["ci", "atlas-runner-ci"],
        "consumed": False,
    }
    a.update(over)
    return a


def run(name, **over):
    return {
        "name": name,
        "head_sha": HEAD,
        "status": "completed",
        "conclusion": "success",
        "id": hash(name) % 10**6,
        "completed_at": "2026-09-28T09:40:00Z",
        **over,
    }


def snapshot(iv_body: str = iv("IV_VERDICT=PASS"), **over):
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
        "check_runs": [run("ci"), run("atlas-runner-ci")],
        "comments": [
            {"id": 1, "created_at": "2026-09-28T09:45:59Z", "author": "iv", "body": iv_body}
        ],
        "reviews": [],
    }
    s.update(over)
    return s


def reasons(d):
    return " ".join(d.reasons)


# 1. IV_VERDICT=FAIL plus incidental PASS text => DENY
def test_1_verdict_fail_with_incidental_pass_text_denies():
    d = evaluate(
        authority(),
        snapshot(iv("IV_VERDICT=FAIL", extra="IV matrix: item 1 PASS; all checks PASS")),
    )
    assert d.verdict == "DENY" and "IV_NOT_PASS" in reasons(d) and "verdict=FAIL" in reasons(d)


# 2. quoted IV_VERDICT=PASS in unrelated prose must not establish PASS
def test_2_quoted_pass_token_in_prose_does_not_establish_pass():
    for quoted in (
        'the record must say "IV_VERDICT=PASS"',
        "the line `IV_VERDICT=PASS` is required",
        "'IV_VERDICT=PASS'",
    ):
        d = evaluate(authority(), snapshot(iv("(no verdict line)", extra=quoted)))
        assert d.verdict == "DENY" and "verdict=MISSING" in reasons(d), quoted
    # heading says FAIL, matrix lines say PASS, no canonical token at all -> DENY (was ALLOW before)
    d = evaluate(
        authority(),
        snapshot(
            iv(
                "FRESH INDEPENDENT IV — FAIL",
                extra="IV matrix: item 1 PASS\nIV_VERDICT=INVALID_BINDING",
            )
        ),
    )
    assert d.verdict == "DENY"


# 3. exact canonical IV_VERDICT=PASS with P0=0/P1=0 => eligible
def test_3_canonical_pass_is_eligible():
    for line in ("IV_VERDICT=PASS", "**IV_VERDICT=PASS**", "IV_VERDICT = PASS"):
        d = evaluate(authority(), snapshot(iv(line)))
        assert d.verdict == "ALLOW", (line, d.reasons)


# 4. contradictory PASS/FAIL evidence => DENY
def test_4_contradictory_verdicts_deny():
    d = evaluate(authority(), snapshot(iv("IV_VERDICT=PASS", extra="\nIV_VERDICT=FAIL")))
    assert d.verdict == "DENY" and "verdict=AMBIGUOUS" in reasons(d)
    d = evaluate(authority(), snapshot(iv("IV_VERDICT=PASS", p1=1)))
    assert d.verdict == "DENY" and "P1=1" in reasons(d)
    d = evaluate(authority(), snapshot(iv("IV_VERDICT=PASS\nIV_VERDICT=WITHDRAWN")))
    assert d.verdict == "DENY"


# 5/6/7. required_checks missing / empty / malformed => DENY
def test_5_missing_required_checks_denies():
    a = authority()
    del a["required_checks"]
    d = evaluate(a, snapshot())
    assert d.verdict == "DENY" and "NO_REQUIRED_CHECKS" in d.reasons


def test_6_empty_required_checks_denies():
    d = evaluate(authority(required_checks=[]), snapshot())
    assert d.verdict == "DENY" and "NO_REQUIRED_CHECKS" in d.reasons


def test_7_malformed_required_checks_denies():
    for bad in ("ci", ["ci", ""], ["ci", None], [{"name": "ci"}], 42):
        d = evaluate(authority(required_checks=bad), snapshot())
        assert d.verdict == "DENY", bad
        assert "NO_REQUIRED_CHECKS" in d.reasons or "MALFORMED_REQUIRED_CHECKS" in d.reasons, bad


# 8/9. timestamp boundary
def test_8_blocking_evidence_exactly_at_decided_at_denies():
    s = snapshot(
        reviews=[
            {"id": 7, "submitted_at": T0, "author": "r", "state": "CHANGES_REQUESTED", "body": ""}
        ]
    )
    d = evaluate(authority(), s)
    assert d.verdict == "DENY" and "NEWER_BLOCKING_EVIDENCE:review:7" in reasons(d)
    s = snapshot()
    s["comments"].append(
        {
            "id": 8,
            "created_at": "2026-09-28T10:00:00+00:00",
            "author": "x",
            "body": "MERGE_AUTHORITY_INVALIDATED",
        }
    )
    assert "NEWER_BLOCKING_EVIDENCE:comment:8" in reasons(evaluate(authority(), s))


def test_9_blocking_evidence_one_microsecond_later_denies():
    s = snapshot()
    s["comments"].append(
        {
            "id": 9,
            "created_at": "2026-09-28T10:00:00.000001Z",
            "author": "x",
            "body": "IV_VERDICT=FAIL\nBLOCKING_P1=1 BLOCKING_P0=0",
        }
    )
    d = evaluate(authority(), s)
    assert d.verdict == "DENY" and "NEWER_BLOCKING_EVIDENCE:comment:9" in reasons(d)


# 10. clean evidence strictly before decision + exact authority => ALLOW
def test_10_clean_evidence_before_decision_allows():
    s = snapshot()
    # an older FAIL note (before the PASS IV record and the decision) is history, not a blocker
    s["comments"].insert(
        0,
        {
            "id": 2,
            "created_at": "2026-09-28T09:30:00Z",
            "author": "iv",
            "body": "older FAIL superseded: IV_FAIL",
        },
    )
    d = evaluate(authority(), s)
    assert d.verdict == "ALLOW", d.reasons
    assert d.receipt["authority"]["decided_at"] == T0 and d.receipt["newer_blocking"] == []


# 11. >100 comments complete retrieval behaves deterministically
def test_11_paginated_comments_over_100_are_collected_completely():
    calls = []

    class R:
        def __init__(self, stdout):
            self.stdout = stdout

    pass_body = iv("IV_VERDICT=PASS")

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
                                run("ci", updated_at="2026-09-28T09:40:00Z"),
                                run("atlas-runner-ci", updated_at="2026-09-28T09:41:00Z"),
                            ]
                        }
                    ]
                )
            )
        if "/comments" in api:
            assert "--paginate" in cmd and "--slurp" in cmd
            page1 = [
                {
                    "id": i,
                    "created_at": f"2026-09-28T08:{i % 60:02d}:00Z",
                    "user": {"login": "u"},
                    "body": "noise",
                }
                for i in range(100)
            ]
            page2 = [
                {
                    "id": i,
                    "created_at": "2026-09-28T09:45:59Z",
                    "user": {"login": "iv"},
                    "body": pass_body,
                }
                for i in range(100, 130)
            ]
            return R(json.dumps([page1, page2]))
        if "/reviews" in api:
            return R("[]")
        raise AssertionError(api)

    s = collect_snapshot("WezzSide/project-atlas", 1025, runner=fake)
    assert len(s["comments"]) == 130 and s["comments"][-1]["id"] == 129
    s["observed_at"] = "2026-09-28T10:05:30Z"
    d1 = evaluate(authority(), s)
    d2 = evaluate(authority(), s)
    assert d1.verdict == "ALLOW" and d1.to_dict() == d2.to_dict()


# 12. malformed API evidence => DENY, never crash-to-allow
def test_12_malformed_evidence_denies_never_crashes():
    s = snapshot()
    s["comments"][0]["created_at"] = "not-a-date"
    d = evaluate(authority(), s)
    assert d.verdict == "DENY" and d.reasons[0].startswith("MALFORMED_EVIDENCE")
    s = snapshot(
        reviews=[{"id": 1, "submitted_at": None, "author": "r", "state": "PENDING", "body": ""}]
    )
    assert evaluate(authority(), s).verdict == "DENY"
    s = snapshot(
        reviews=[{"id": 1, "submitted_at": T0, "author": "r", "state": "WEIRD", "body": ""}]
    )
    assert evaluate(authority(), s).verdict == "DENY"
    s = snapshot()
    s["comments"][0]["created_at"] = "2026-09-28T09:45:59"  # naive
    assert evaluate(authority(), s).verdict == "DENY"
    assert evaluate(authority(), {"observed_at": "2026-09-28T10:05:30Z"}).verdict == "DENY"
    assert evaluate(authority(decided_at=None), snapshot()).verdict == "DENY"

    def boom(cmd, check, capture_output, text):
        raise subprocess.CalledProcessError(1, cmd)

    try:
        collect_snapshot("WezzSide/project-atlas", 1025, runner=boom)
    except (
        Exception
    ) as exc:  # collector failure is a typed, fail-closed error (main() maps it to DENY/exit 2)
        assert type(exc).__name__ == "MalformedEvidence"
    else:
        raise AssertionError("collector must not silently succeed")


def test_sha_case_normalized_without_weakening_binding():
    d = evaluate(
        authority(head=HEAD.upper(), tree=TREE.upper()),
        snapshot(iv("IV_VERDICT=PASS").replace(HEAD, HEAD.upper())),
    )
    assert d.verdict == "ALLOW", d.reasons
    assert d.receipt["authority"]["head"] == HEAD  # normalized lowercase in the receipt
    bad = authority(head="9c06069c")  # short sha is not a binding
    assert "HEAD_DRIFT" in reasons(evaluate(bad, snapshot()))


def test_main_exit_codes_and_no_crash_to_allow(tmp_path, monkeypatch):
    from controller import merge_gate

    auth = tmp_path / "a.json"
    auth.write_text(json.dumps(authority()))
    monkeypatch.setattr(merge_gate, "collect_snapshot", lambda repo, pr: snapshot())
    assert merge_gate.main(["--pr", "1025", "--authority", str(auth)]) == 0

    def fail(repo, pr):
        raise merge_gate.MalformedEvidence("gh api failed")

    monkeypatch.setattr(merge_gate, "collect_snapshot", fail)
    rc = merge_gate.main(
        ["--pr", "1025", "--authority", str(auth), "--receipt", str(tmp_path / "r.json")]
    )
    assert rc == 2 and "EVIDENCE_COLLECTION_FAILED" in (tmp_path / "r.json").read_text()
