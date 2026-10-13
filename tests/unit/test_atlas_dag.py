"""Offline adversarial tests for the atlas-dag coordinator.

Covers acceptance-matrix rows DAG/OWN/IV/CI/MG/EV with a fake `gh` runner —
no live network. Each scenario pins exact heads/trees; any evidence attached
to a different head must be demoted to predecessor (DAG-005).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from atlas_dag import events as events_mod  # noqa: E402
from atlas_dag.gh import GhClient  # noqa: E402
from atlas_dag.model import build_snapshot  # noqa: E402

MAIN_SHA = "1" * 40
MAIN_TREE = "2" * 40
H1 = "a" * 40
T1 = "b" * 40
H2 = "c" * 40
T2 = "d" * 40
H3 = "e" * 40
T3 = "f" * 40

POOL_BODY = (
    "```json\n"
    + json.dumps({"schema": "ATLAS_VERIFIER_POOL_V1", "verifiers": [
        {"verifier_id": "IV-A", "principal": "github:iv-a-user"},
        {"verifier_id": "IV-B", "principal": "github:iv-b-user"},
    ]})
    + "\n```"
)

BARE_POOL_BODY = (
    "```json\n"
    + json.dumps({"schema": "ATLAS_VERIFIER_POOL_V1", "verifiers": ["IV-A", "IV-B"]})
    + "\n```"
)


def make_event(event_id, event, *, ts="2026-09-01T00:00:00Z", pr=None, head=None,
               actor="agent-main", role="COORDINATOR", state="FROZEN", lane=None,
               dependencies=None):
    return {
        "schema": "ATLAS_EVENT_V1",
        "event_id": event_id,
        "timestamp_utc": ts,
        "actor": actor,
        "role": role,
        "session_id": f"sess-{actor}",
        "lane": lane or (f"pr/{pr}" if pr is not None else "main"),
        "pr": pr,
        "head": head,
        "event": event,
        "state": state,
        "dependencies": dependencies or [],
        "evidence": [],
        "invalidates": [],
        "next_actions": [],
    }


def make_receipt(receipt_id, *, pr=10, head=H1, tree=T1, verifier="IV-A",
                 session="sess-iv-a-1", author_conflict=False, writes=0,
                 result="PASS", p0=0, p1=0, p2=0, claim="PASS", independence="PASS",
                 ts="2026-09-01T01:00:00Z"):
    return {
        "schema": "ATLAS_IV_RECEIPT_V1",
        "receipt_id": receipt_id,
        "timestamp_utc": ts,
        "pr": pr,
        "head": head,
        "tree": tree,
        "verifier_id": verifier,
        "session_id": session,
        "candidate_author_conflict": author_conflict,
        "write_activity_count": writes,
        "result": result,
        "p0": p0,
        "p1": p1,
        "p2": p2,
        "claim_integrity": claim,
        "formal_independence": independence,
        "findings": [],
        "evidence": ["evidence://local"],
    }


def fenced(payload):
    return "```json\n" + json.dumps(payload) + "\n```"


def comments_with(*payloads, author="iv-a-user"):
    return [
        {"body": fenced(p), "id": 1000 + i,
         "user": {"login": author}, "html_url": f"https://example/comment/{1000 + i}"}
        for i, p in enumerate(payloads)
    ]


class FakeEnv:
    """Recorded GitHub state keyed by the gh argument patterns GhClient emits."""

    def __init__(self):
        self.data = {
            "default_branch": "main",
            ("branch", "main"): {
                "commit": {"sha": MAIN_SHA, "commit": {"tree": {"sha": MAIN_TREE}}}
            },
            "prs": [],
            "dag_issue": None,
            "fail": set(),
        }
        self.commits = {}
        self.runs = {}
        self.reviews = {}
        self.issue_body = ""
        self.comments = []

    def add_pr(self, number, head, tree, *, mergeable="MERGEABLE"):
        self.data["prs"].append({
            "number": number, "title": f"PR {number}", "author": {"login": "someone"},
            "isDraft": False, "headRefName": f"branch-{number}", "headRefOid": head,
            "baseRefName": "main", "mergeable": mergeable, "url": f"https://example/{number}",
            "updatedAt": "2026-09-01T00:00:00Z",
        })
        self.commits[head] = {"sha": head, "commit": {"tree": {"sha": tree}}}

    def add_ci(self, head, run_id=101, conclusion="success", status="completed"):
        self.runs.setdefault(head, []).append({
            "id": run_id, "created_at": "2026-09-01T10:00:00Z",
            "status": status, "conclusion": conclusion,
        })

    def add_issue(self, number=1, body=POOL_BODY, comments=None):
        self.data["dag_issue"] = {"number": number, "title": "Atlas Autonomous DAG Control",
                                  "state": "OPEN"}
        self.issue_body = body
        self.comments = comments or []

    def key_for(self, args):
        if "--paginate" in args:
            args = [a for a in args if a != "--paginate"]
        if args[0] == "api" and args[1].startswith(f"repos/{REPO}"):
            rest = args[1][len(f"repos/{REPO}/"):]
            kinds = ("branches", "commits", "actions", "pulls", "issues")
            if not rest.startswith(kinds):
                return "default_branch"
            if rest.startswith("branches/"):
                return ("branch", rest.split("/", 1)[1])
            if rest.startswith("commits/"):
                return ("commit", rest.split("/", 1)[1])
            if rest.startswith("actions/runs"):
                sha = rest.split("head_sha=")[1].split("&")[0]
                return ("runs", sha)
            if rest.startswith("pulls/") and "comments" in rest:
                return ("reviews", int(rest.split("/")[1]))
            if rest.startswith("issues/") and "comments" in rest:
                return ("comments", int(rest.split("/")[1]))
            if rest.startswith("issues/"):
                return ("issue_body", int(rest.split("/")[1]))
            return "default_branch"
        if args[:2] == ["pr", "list"]:
            return "prs"
        if args[:2] == ["issue", "list"]:
            return "dag_issue"
        raise AssertionError(f"unexpected gh args: {args}")

    def runner(self):
        def _run(argv, **_kw):
            args = argv[1:]
            key = self.key_for(args)
            if key in self.data["fail"]:
                return _completed(argv, 1, "", "simulated gh failure")
            if key == "default_branch":
                if key in self.data["fail"]:
                    return _completed(argv, 1, "", "boom")
                return _completed(argv, 0, self.data.get("default_branch") or "", "")
            if key == "prs":
                return _completed(argv, 0, json.dumps(self.data["prs"]), "")
            if key == "dag_issue":
                issue = self.data["dag_issue"]
                return _completed(argv, 0, json.dumps([issue] if issue else []), "")
            if isinstance(key, tuple):
                kind, arg = key
                if kind == "branch":
                    val = self.data.get(key)
                elif kind == "commit":
                    val = self.commits.get(arg)
                elif kind == "runs":
                    val = {"workflow_runs": self.runs.get(arg, [])}
                elif kind == "reviews":
                    val = self.reviews.get(arg, [])
                elif kind == "comments":
                    val = self.comments
                elif kind == "issue_body":
                    return _completed(argv, 0, self.issue_body, "")
                else:
                    val = None
                if val is None:
                    return _completed(argv, 1, "", f"missing fixture for {key}")
                return _completed(argv, 0, json.dumps(val), "")
            raise AssertionError(f"unhandled key: {key}")

        return _run


def _completed(argv, code, stdout, stderr):
    import subprocess
    return subprocess.CompletedProcess(argv, code, stdout, stderr)


REPO = "B0LK13/project-atlas"


def make_client(env):
    return GhClient(repo=REPO, runner=env.runner())


def base_env(*, with_pool=True):
    env = FakeEnv()
    env.add_pr(10, H1, T1)
    env.add_pr(11, H2, T2)
    env.add_ci(H1)
    env.add_ci(H2)
    if with_pool:
        env.add_issue(body=POOL_BODY)
    return env


def node_for(snapshot, pr):
    return next(n for n in snapshot["nodes"] if n["pr"] == pr)


# -- event stream (EV-*) ---------------------------------------------------

def test_ev001_duplicate_event_id_is_idempotent():
    env = base_env()
    ev = make_event("evt-dup-001", "OWNER_CLAIMED", pr=10, actor="agent-x")
    env.comments = comments_with(ev, dict(ev))
    result = events_mod.ingest_comments(env.comments)
    assert len(result.events) == 1


def test_ev002_reordered_events_are_canonically_ordered():
    late = make_event("evt-b-long01", "OWNER_CLAIMED", ts="2026-09-02T00:00:00Z", pr=10)
    early = make_event("evt-a-long01", "OWNER_CLAIMED", ts="2026-09-01T00:00:00Z", pr=11)
    result = events_mod.ingest_comments(comments_with(late, early))
    assert [e["event_id"] for e in result.events] == ["evt-a-long01", "evt-b-long01"]


def test_ev003_stable_state_noise_rejected():
    noise = make_event("evt-noise01", "NO_CHANGE", pr=10, state="UNCHANGED")
    result = events_mod.ingest_comments(comments_with(noise))
    assert result.events == []
    # NO_CHANGE is not an allowed transition at all; either the schema enum or
    # the explicit noise filter rejects it.
    assert any("stable-state" in reason or "not one of" in reason
               for _m, reason in result.invalid)


def test_ev_invalid_schema_reported_not_fatal():
    bad = {"schema": "ATLAS_EVENT_V1", "event_id": "x"}  # missing required fields
    result = events_mod.ingest_comments(comments_with(bad))
    assert result.events == []
    assert len(result.invalid) == 1


# -- formal IV (IV-*) -------------------------------------------------------

def test_iv003_distinct_verifier_exact_head_eligible_and_gate_passes():
    env = base_env()
    env.comments = comments_with(make_receipt("rcpt-iv-ok"))
    snapshot = build_snapshot(make_client(env))
    node = node_for(snapshot, 10)
    assert node["formal_iv"] == "rcpt-iv-ok"
    assert node["gate"]["merge_gate"] == "PASS"
    assert node["state"] == "MERGE_ELIGIBLE"


def test_iv001_author_conflict_rejected():
    env = base_env()
    env.comments = comments_with(make_receipt("rcpt-iv-bad", author_conflict=True))
    snapshot = build_snapshot(make_client(env))
    node = node_for(snapshot, 10)
    assert node["formal_iv"] is None
    assert node["gate"]["merge_gate"] == "FAIL"
    assert any(r.startswith("FORMAL_IV_REJECTED") for r in node["gate"]["reasons"])


def test_iv002_same_session_fresh_worktree_rejected():
    env = base_env()
    env.comments = comments_with(make_receipt("rcpt-iv-sess", independence="FAIL"))
    snapshot = build_snapshot(make_client(env))
    node = node_for(snapshot, 10)
    assert node["formal_iv"] is None
    assert node["gate"]["merge_gate"] == "FAIL"


def test_iv004_stale_head_receipt_rejected_and_marked_history():
    env = base_env()
    env.comments = comments_with(make_receipt("rcpt-iv-old", head=H3, tree=T3))
    snapshot = build_snapshot(make_client(env))
    node = node_for(snapshot, 10)
    assert node["formal_iv"] is None
    assert node["gate"]["merge_gate"] == "FAIL"


def test_verifier_pool_undefined_fails_closed():
    env = base_env(with_pool=False)
    env.add_issue(body="no pool here")
    env.comments = comments_with(make_receipt("rcpt-iv-ok"))
    snapshot = build_snapshot(make_client(env))
    node = node_for(snapshot, 10)
    assert node["formal_iv"] is None
    assert node["gate"]["merge_gate"] == "FAIL"
    assert "VERIFIER_POOL_UNDEFINED" in node["rejected_receipts"]["rcpt-iv-ok"]


def test_unapproved_verifier_rejected():
    env = base_env()
    env.comments = comments_with(make_receipt("rcpt-iv-rogue", verifier="IV-ROGUE"))
    snapshot = build_snapshot(make_client(env))
    assert node_for(snapshot, 10)["formal_iv"] is None


# -- trusted identity (D-PR720 §3/§4) -----------------------------------------

def test_bare_label_pool_is_unbound_and_fails_closed():
    # Bare-string pool entries are DECLARED_BUT_UNBOUND: parseable, never formal IV.
    env = base_env()
    env.add_issue(body=BARE_POOL_BODY)
    env.comments = comments_with(make_receipt("rcpt-iv-bare"))
    snapshot = build_snapshot(make_client(env))
    node = node_for(snapshot, 10)
    assert node["formal_iv"] is None
    assert "VERIFIER_IDENTITY_UNBOUND" in node["rejected_receipts"]["rcpt-iv-bare"]


def test_receipt_from_untrusted_principal_rejected():
    # Schema-valid receipt posted by a GitHub login that is not the bound
    # principal must not impersonate the verifier.
    env = base_env()
    env.comments = comments_with(make_receipt("rcpt-iv-mallory"), author="mallory")
    snapshot = build_snapshot(make_client(env))
    node = node_for(snapshot, 10)
    assert node["formal_iv"] is None
    assert "PRINCIPAL_MISMATCH" in node["rejected_receipts"]["rcpt-iv-mallory"]


def test_unknown_candidate_tree_fails_closed():
    # Transient commit-lookup failure => tree None => receipt must be rejected,
    # never eligible via head-only identity.
    env = base_env()
    env.data["fail"].add(("commit", H1))
    env.comments = comments_with(make_receipt("rcpt-iv-ok"))
    snapshot = build_snapshot(make_client(env))
    node = node_for(snapshot, 10)
    assert node["tree"] is None
    assert node["formal_iv"] is None
    assert "CANDIDATE_TREE_UNKNOWN" in node["rejected_receipts"]["rcpt-iv-ok"]
    assert node["gate"]["merge_gate"] == "FAIL"


# -- CI gate (CI-*) ----------------------------------------------------------

def test_ci001_ci_pass_iv_missing_gate_fail():
    env = base_env()
    snapshot = build_snapshot(make_client(env))
    node = node_for(snapshot, 10)
    assert node["ci_status"] == "PASS"
    assert node["gate"]["merge_gate"] == "FAIL"
    assert "FORMAL_IV_MISSING" in node["gate"]["reasons"]


def test_ci002_claim_integrity_fail_gate_fail():
    env = base_env()
    env.comments = comments_with(
        make_receipt("rcpt-iv-ok"),
        make_event("evt-claim-fail", "CLAIM_INTEGRITY_CHANGED", pr=10, state="FAIL"),
    )
    snapshot = build_snapshot(make_client(env))
    node = node_for(snapshot, 10)
    assert node["claim_integrity"] == "FAIL"
    assert node["gate"]["merge_gate"] == "FAIL"
    assert any(r.startswith("CLAIM_INTEGRITY_NOT_PASS") for r in node["gate"]["reasons"])


def test_ci003_p1_open_gate_fail():
    env = base_env()
    env.comments = comments_with(make_receipt("rcpt-iv-p1", p1=1))
    snapshot = build_snapshot(make_client(env))
    node = node_for(snapshot, 10)
    assert node["gate"]["merge_gate"] == "FAIL"
    assert "P1_OPEN" in node["gate"]["reasons"]


def test_ci_fail_not_pass():
    env = base_env()
    env.runs[H1] = [{"id": 102, "created_at": "2026-09-01T11:00:00Z",
                     "status": "completed", "conclusion": "failure"}]
    snapshot = build_snapshot(make_client(env))
    node = node_for(snapshot, 10)
    assert node["ci_status"] == "FAIL"
    assert node["gate"]["merge_gate"] == "FAIL"


# -- merge guardian (MG-*) ----------------------------------------------------

def test_mg001_prospective_merge_receipt_not_accepted():
    env = base_env()
    env.comments = comments_with(
        make_receipt("rcpt-iv-ok"),
        make_event("evt-merged-fake", "MERGED", pr=10, state="MERGED"),
    )
    snapshot = build_snapshot(make_client(env))
    node = node_for(snapshot, 10)
    assert node["gate"]["merge_gate"] == "FAIL"
    assert any(r.startswith("MERGE_RECEIPT_UNPROVEN") for r in node["gate"]["reasons"])


def test_gate_fails_when_not_mergeable():
    env = base_env()
    env.data["prs"][0]["mergeable"] = "CONFLICTING"
    env.comments = comments_with(make_receipt("rcpt-iv-ok"))
    snapshot = build_snapshot(make_client(env))
    node = node_for(snapshot, 10)
    assert node["gate"]["merge_gate"] == "FAIL"
    assert any(r.startswith("MERGEABILITY") for r in node["gate"]["reasons"])


# -- frontier / lanes (DAG-*) --------------------------------------------------

def test_dag001_waiting_lane_does_not_block_runnable_lane():
    env = base_env()
    env.comments = comments_with(
        make_receipt("rcpt-iv-11-fail", pr=11, head=H2, tree=T2, result="FAIL"),
        make_event("evt-claim-11", "CLAIM_INTEGRITY_CHANGED", pr=11, state="FAIL"),
        make_event("evt-gate-11", "HUMAN_GATE_REQUIRED", pr=11, state="FROZEN"),
    )
    snapshot = build_snapshot(make_client(env))
    n10, n11 = node_for(snapshot, 10), node_for(snapshot, 11)
    assert n10["state"] in ("RUNNABLE_READONLY", "RUNNABLE_WRITE")
    assert n11["state"] == "FROZEN"
    assert "IV" in n11["waiting_on"]
    assert snapshot["safe_runnable_count"] >= 1


def test_dag005_head_move_invalidates_exact_head_evidence():
    env = base_env()
    # Candidate head moves H1 -> H3; old CI + old IV attach to H1 only.
    env.data["prs"][0]["headRefOid"] = H3
    env.commits[H3] = {"sha": H3, "commit": {"tree": {"sha": T3}}}
    env.comments = comments_with(make_receipt("rcpt-iv-old-head", head=H1, tree=T1))
    snapshot = build_snapshot(make_client(env))
    node = node_for(snapshot, 10)
    assert node["head"] == H3
    assert node["ci_status"] == "NONE"  # old-head CI demoted to predecessor
    assert node["formal_iv"] is None  # old-head IV demoted to predecessor
    assert "rcpt-iv-old-head" not in [node["formal_iv"]]
    assert node["gate"]["merge_gate"] == "FAIL"
    assert "evt" not in node["stale_events"]  # no events posted for pr 10 here
    assert node["gate"]["reasons"]  # deterministic non-empty reasons


def test_dag006_no_active_writer_inference_without_owner_event():
    env = base_env()
    snapshot = build_snapshot(make_client(env))
    for node in snapshot["nodes"]:
        assert node["owner"] is None  # worktree presence is not ownership evidence


def test_fail_closed_when_github_unavailable():
    env = base_env()
    env.data["fail"].add("default_branch")
    env.data["fail"].add(("branch", "main"))
    env.data["fail"].add("prs")
    env.data["dag_issue"] = None
    snapshot = build_snapshot(make_client(env))
    assert snapshot["main_head"] is None
    assert snapshot["nodes"] == []
    assert snapshot["safe_runnable_count"] == 0


def test_fail_closed_snapshot_still_validates_against_schema():
    from atlas_dag.events import validator_for
    env = base_env()
    env.data["fail"].add(("branch", "main"))
    snapshot = build_snapshot(make_client(env))
    errors = list(validator_for("dag_snapshot_v1.schema.json").iter_errors(snapshot))
    assert errors == [], "; ".join(
        f"{'/'.join(map(str, e.path))}: {e.message}" for e in errors
    )


# -- ownership (OWN-*) ----------------------------------------------------------

def test_own001_owner_claim_tracked_and_released():
    env = base_env()
    env.comments = comments_with(
        make_event("evt-claim", "OWNER_CLAIMED", pr=10, actor="agent-x"),
        make_event("evt-release", "OWNER_RELEASED", pr=10, actor="agent-x",
                   ts="2026-09-02T00:00:00Z"),
    )
    snapshot = build_snapshot(make_client(env))
    assert node_for(snapshot, 10)["owner"] is None


def test_own001b_active_owner_recorded():
    env = base_env()
    env.comments = comments_with(make_event("evt-claim", "OWNER_CLAIMED", pr=10, actor="agent-x"))
    snapshot = build_snapshot(make_client(env))
    assert node_for(snapshot, 10)["owner"] == "agent-x"
    assert node_for(snapshot, 10)["state"] == "RUNNABLE_WRITE"


def test_own002_unowned_node_is_claimable():
    env = base_env()
    snapshot = build_snapshot(make_client(env))
    assert node_for(snapshot, 10)["owner"] is None
    assert node_for(snapshot, 10)["state"] == "RUNNABLE_READONLY"


# -- determinism -----------------------------------------------------------------

def test_snapshot_deterministic_for_fixed_clock():
    env = base_env()
    env.comments = comments_with(
        make_event("evt-claim", "OWNER_CLAIMED", pr=10, actor="agent-x"),
        make_receipt("rcpt-iv-ok"),
    )
    client = make_client(env)
    clock = lambda: "2026-09-07T00:00:00Z"  # noqa: E731
    first = build_snapshot(client, clock=clock)
    second = build_snapshot(client, clock=clock)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_snapshot_conforms_to_dag_snapshot_schema():
    from atlas_dag.events import validator_for
    env = base_env()
    env.comments = comments_with(make_receipt("rcpt-iv-ok"))
    snapshot = build_snapshot(make_client(env))
    errors = list(validator_for("dag_snapshot_v1.schema.json").iter_errors(snapshot))
    assert errors == [], "; ".join(
        f"{'/'.join(map(str, e.path))}: {e.message}" for e in errors
    )


@pytest.mark.parametrize("status,conclusion,expected", [
    ("completed", "success", "PASS"),
    ("completed", "failure", "FAIL"),
    ("in_progress", None, "PENDING"),
])
def test_ci_status_reduction(status, conclusion, expected):
    from atlas_dag.model import ci_status_for_head
    result, _run_id = ci_status_for_head(
        [{"id": 1, "created_at": "2026-09-01T00:00:00Z", "status": status,
          "conclusion": conclusion}]
    )
    assert result == expected


def test_ci_hidden_failing_run_cannot_be_laundered_by_newer_pass():
    # Adversarial: newer passing workflow run must not hide an older failure
    # on the same exact head (live #720 scenario).
    from atlas_dag.model import ci_status_for_head
    runs = [
        {"id": 1, "created_at": "2026-09-01T10:00:00Z", "status": "completed",
         "conclusion": "failure"},
        {"id": 2, "created_at": "2026-09-01T11:00:00Z", "status": "completed",
         "conclusion": "success"},
    ]
    assert ci_status_for_head(runs)[0] == "FAIL"


def test_ci_pending_run_blocks_pass():
    from atlas_dag.model import ci_status_for_head
    runs = [
        {"id": 1, "created_at": "2026-09-01T10:00:00Z", "status": "completed",
         "conclusion": "success"},
        {"id": 2, "created_at": "2026-09-01T11:00:00Z", "status": "queued",
         "conclusion": None},
    ]
    assert ci_status_for_head(runs)[0] == "PENDING"


# -- stale-head events are history only (D-PR720 §5) ----------------------------

def test_stale_old_head_claim_pass_cannot_launder_new_candidate():
    env = base_env()
    # Head moves H1 -> H3; an old-head PASS claim and old-head freeze are history.
    env.data["prs"][0]["headRefOid"] = H3
    env.commits[H3] = {"sha": H3, "commit": {"tree": {"sha": T3}}}
    env.comments = comments_with(
        make_event("evt-old-claim-pass", "CLAIM_INTEGRITY_CHANGED", pr=10,
                   head=H1, state="PASS", ts="2026-09-01T00:00:00Z"),
        make_event("evt-old-freeze", "HUMAN_GATE_REQUIRED", pr=10,
                   head=H1, state="FROZEN", ts="2026-09-01T00:01:00Z"),
    )
    snapshot = build_snapshot(make_client(env))
    node = node_for(snapshot, 10)
    assert node["claim_integrity"] != "PASS"  # stale PASS is history only
    assert node["frozen"] is False  # stale freeze is history only
    assert node["gate"]["merge_gate"] == "FAIL"


def test_live_head_claim_event_applies_normally():
    env = base_env()
    env.comments = comments_with(
        make_event("evt-live-claim-pass", "CLAIM_INTEGRITY_CHANGED", pr=10,
                   head=H1, state="PASS"),
        make_receipt("rcpt-iv-ok"),
    )
    snapshot = build_snapshot(make_client(env))
    node = node_for(snapshot, 10)
    assert node["claim_integrity"] == "PASS"
    assert node["gate"]["merge_gate"] == "PASS"


# -- event bus pagination (D-PR720 §6) -------------------------------------------

def test_issue_comments_paginate_beyond_100():
    # 101 comments; the material OWNER_RELEASED arrives as comment 101.
    # gh api --paginate concatenates pages; all comments must be consumed.
    from atlas_dag.gh import GhClient
    from atlas_dag.model import ownership
    claim = make_event("evt-claim-p1", "OWNER_CLAIMED", pr=10, actor="agent-x")
    release = make_event("evt-release-p1", "OWNER_RELEASED", pr=10, actor="agent-x",
                         ts="2026-09-02T00:00:00Z")
    page1 = comments_with(*[dict(claim, event_id=f"evt-fill-{i:03d}") for i in range(100)])
    page2 = comments_with(release)
    all_comments = page1 + page2
    assert len(all_comments) == 101

    def paged_runner(argv, **_kw):
        args = [a for a in argv[1:] if a != "--paginate"]
        assert "--paginate" in argv[1:], "client must request pagination"
        if args[0] == "api" and "issues/1/comments" in args[1]:
            # simulate gh --paginate: concatenated JSON array pages
            pages = json.dumps(all_comments[:100]) + json.dumps(all_comments[100:])
            return _completed(argv, 0, pages, "")
        raise AssertionError(f"unexpected: {argv}")

    client = GhClient(repo=REPO, runner=paged_runner)
    fetched = client.issue_comments(1)
    assert len(fetched) == 101
    ingested = events_mod.ingest_comments(fetched)
    status, _actors = ownership(ingested.events, 10)
    assert status == "UNOWNED"  # the release after comment 100 was consumed


# -- ownership mutex (D-PR720 §7) --------------------------------------------------

def test_competing_owner_claims_fail_closed_and_commands_agree():
    from atlas_dag.model import ownership
    env = base_env()
    env.comments = comments_with(
        make_event("evt-claim-a", "OWNER_CLAIMED", pr=10, actor="AGENT_A"),
        make_event("evt-claim-b", "OWNER_CLAIMED", pr=10, actor="AGENT_B"),
    )
    events = events_mod.ingest_comments(env.comments).events
    status, actors = ownership(events, 10)
    assert status == "AMBIGUOUS"
    assert actors == ["AGENT_A", "AGENT_B"]

    snapshot = build_snapshot(make_client(env))
    node = node_for(snapshot, 10)
    assert node["ownership"] == "AMBIGUOUS"  # snapshot agrees with owners logic
    assert node["owner"] is None
    assert node["state"] != "RUNNABLE_WRITE"
    assert node["state"] == "RUNNABLE_READONLY"
