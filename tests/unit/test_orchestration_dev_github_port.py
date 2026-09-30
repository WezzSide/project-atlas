"""REST port mapping, with the HTTP seam stubbed (no network)."""

from __future__ import annotations

import pytest

from project_atlas.orchestration.autonomy.dev_fabric_adapter import AdapterError
from project_atlas.orchestration.autonomy.dev_github_port import GitHubRestPort


class Stub(GitHubRestPort):
    def __init__(self, routes):
        super().__init__("o/r", "TOKEN-NOT-USED")
        self.routes, self.calls = routes, []

    def _request(self, method, path, body=None):
        self.calls.append((method, path, body))
        for (m, prefix), resp in self.routes.items():
            if m == method and path.startswith(prefix):
                return resp
        return 404, None


def test_dispatch_needs_204_and_sends_exact_body():
    p = Stub({("POST", "/actions/workflows/w.yml/dispatches"): (204, None)})
    p.dispatch_workflow("w.yml", "main", {"a": "b"})
    assert p.calls == [
        ("POST", "/actions/workflows/w.yml/dispatches", {"ref": "main", "inputs": {"a": "b"}})
    ]
    q = Stub({("POST", "/actions/workflows/w.yml/dispatches"): (200, {})})
    with pytest.raises(AdapterError):
        q.dispatch_workflow("w.yml", "main", {})


def test_branch_head_validates_ref_shape_and_missing_is_none():
    sha = "a" * 40
    ok = Stub(
        {
            ("GET", "/git/ref/heads/b"): (
                200,
                {"ref": "refs/heads/b", "object": {"type": "commit", "sha": sha}},
            )
        }
    )
    assert ok.branch_head("b") == sha
    assert Stub({}).branch_head("b") is None
    bad = Stub(
        {
            ("GET", "/git/ref/heads/b"): (
                200,
                {"ref": "refs/heads/other", "object": {"type": "commit", "sha": sha}},
            )
        }
    )
    with pytest.raises(AdapterError):
        bad.branch_head("b")


def test_compare_refuses_truncation_and_reads_merge_base():
    d = {
        "total_commits": 1,
        "commits": [1],
        "merge_base_commit": {"sha": "a" * 40},
        "files": [{"filename": "z"}, {"filename": "a"}],
    }
    c = Stub({("GET", "/compare/"): (200, d)}).compare("a" * 40, "b" * 40)
    assert c.merge_base == "a" * 40 and c.files == ("a", "z")
    with pytest.raises(AdapterError):
        Stub({("GET", "/compare/"): (200, {**d, "total_commits": 5})}).compare("a" * 40, "b" * 40)


def test_artifact_digest_requires_sha256_and_expired_is_ignored():
    art = {"artifacts": [{"id": 1, "name": "n", "digest": "sha256:" + "f" * 64}]}
    assert Stub({("GET", "/actions/runs/9/artifacts"): (200, art)}).artifact_digests(9, "n") == (
        "f" * 64,
    )
    bad = {"artifacts": [{"id": 1, "name": "n", "digest": "md5:xx"}]}
    with pytest.raises(AdapterError):
        Stub({("GET", "/actions/runs/9/artifacts"): (200, bad)}).artifact_digests(9, "n")
    gone = {"artifacts": [{"id": 1, "name": "n", "expired": True, "digest": "sha256:" + "f" * 64}]}
    with pytest.raises(AdapterError):
        Stub({("GET", "/actions/runs/9/artifacts"): (200, gone)}).artifact_digests(9, "n")


def test_ensure_draft_pr_is_idempotent():
    have = Stub({("GET", "/pulls?"): (200, [{"number": 7}])})
    assert have.ensure_draft_pr("b", "main", "t", "x") == 7
    assert [c[0] for c in have.calls] == ["GET"]
    new = Stub({("GET", "/pulls?"): (200, []), ("POST", "/pulls"): (201, {"number": 8})})
    assert new.ensure_draft_pr("b", "main", "t", "x") == 8
    assert new.calls[-1][2]["draft"] is True


def test_check_runs_and_run_mapping():
    p = Stub(
        {
            ("GET", "/commits/"): (
                200,
                {"check_runs": [{"name": "q", "status": "completed", "conclusion": "success"}]},
            ),
            ("GET", "/actions/runs/5"): (
                200,
                {
                    "id": 5,
                    "run_attempt": 2,
                    "path": ".github/workflows/x.yml",
                    "event": "workflow_dispatch",
                    "status": "completed",
                    "conclusion": "success",
                    "head_branch": "main",
                    "created_at": "t",
                },
            ),
        }
    )
    assert p.check_runs("a" * 40) == {"q": ("completed", "success")}
    r = p.get_run(5)
    assert (r.run_id, r.attempt, r.workflow, r.conclusion) == (5, 2, "x.yml", "success")
