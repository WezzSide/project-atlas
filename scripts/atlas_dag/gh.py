"""Thin, fail-closed wrapper around the GitHub CLI (`gh`).

Every GitHub access goes through GhClient. Callers treat None / GhError as
UNKNOWN (D-006: unavailable GitHub data => UNKNOWN, not invented).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any, Callable

DAG_ISSUE_TITLE = "Atlas Autonomous DAG Control"

Runner = Callable[..., subprocess.CompletedProcess]


class GhError(RuntimeError):
    """A `gh` invocation failed or `gh` is unavailable."""


def default_runner() -> Runner:
    return subprocess.run


class GhClient:
    """Read-only GitHub access via the authenticated `gh` CLI."""

    def __init__(self, repo: str | None = None, runner: Runner | None = None) -> None:
        self._repo = repo
        self._runner = runner or default_runner()

    # -- plumbing -------------------------------------------------------
    def run_gh(self, args: list[str]) -> str:
        if shutil.which("gh") is None and self._runner is default_runner():
            raise GhError("gh CLI not found on PATH")
        proc = self._runner(["gh", *args], capture_output=True, text=True, timeout=60,
                            encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise GhError(f"gh {' '.join(args)} failed: {proc.stderr.strip()[:400]}")
        return proc.stdout

    def gh_json(self, args: list[str]) -> Any:
        return json.loads(self.run_gh(args) or "null")

    def gh_json_concat(self, args: list[str]) -> list:
        """Decode `gh api --paginate` output: consecutive JSON arrays concatenated
        into one stream (gh prints each page's body back-to-back)."""
        out = self.run_gh(args)
        decoder = json.JSONDecoder()
        values, idx = [], 0
        while idx < len(out):
            while idx < len(out) and out[idx] in " \r\n\t":
                idx += 1
            if idx >= len(out):
                break
            value, idx = decoder.raw_decode(out, idx)
            values.extend(value if isinstance(value, list) else [value])
        return values

    @property
    def repo(self) -> str | None:
        if self._repo:
            return self._repo
        try:
            out = self.run_gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
            self._repo = out.strip() or None
        except GhError:
            self._repo = None
        return self._repo

    # -- repository truth ------------------------------------------------
    def default_branch(self) -> str | None:
        repo = self.repo
        if not repo:
            return None
        try:
            return self.run_gh(["api", f"repos/{repo}", "--jq", ".default_branch"]).strip() or None
        except GhError:
            return None

    def branch_head(self, branch: str) -> dict | None:
        repo = self.repo
        if not repo:
            return None
        try:
            data = self.gh_json(["api", f"repos/{repo}/branches/{branch}"])
            return {"sha": data["commit"]["sha"], "tree": data["commit"]["commit"]["tree"]["sha"]}
        except (GhError, KeyError, TypeError):
            return None

    def commit(self, sha: str) -> dict | None:
        repo = self.repo
        if not repo:
            return None
        try:
            data = self.gh_json(["api", f"repos/{repo}/commits/{sha}"])
            return {"sha": data["sha"], "tree": data["commit"]["tree"]["sha"]}
        except (GhError, KeyError, TypeError):
            return None

    def open_prs(self) -> list[dict]:
        repo = self.repo
        if not repo:
            return []
        try:
            return self.gh_json(
                [
                    "pr", "list", "--repo", repo, "--state", "open", "--limit", "100",
                    "--json", "number,title,author,isDraft,headRefName,headRefOid,baseRefName,"
                    "mergeable,url,updatedAt",
                ]
            ) or []
        except GhError:
            return []

    def runs_for_head(self, sha: str) -> list[dict]:
        repo = self.repo
        if not repo:
            return []
        try:
            data = self.gh_json(
                ["api", f"repos/{repo}/actions/runs?head_sha={sha}&per_page=50"]
            )
            return data.get("workflow_runs", []) if isinstance(data, dict) else []
        except GhError:
            return []

    def review_comments(self, pr: int) -> list[dict]:
        repo = self.repo
        if not repo:
            return []
        try:
            return self.gh_json(
                ["api", f"repos/{repo}/pulls/{pr}/comments?per_page=100"]
            ) or []
        except GhError:
            return []

    # -- event bus (D-002) ------------------------------------------------
    def dag_issue(self) -> dict | None:
        repo = self.repo
        if not repo:
            return None
        try:
            issues = self.gh_json(
                [
                    "issue", "list", "--repo", repo, "--state", "all",
                    "--search", f'"{DAG_ISSUE_TITLE}" in:title', "--limit", "10",
                    "--json", "number,title,state",
                ]
            ) or []
        except GhError:
            return None
        for issue in issues:
            if issue.get("title", "").strip() == DAG_ISSUE_TITLE:
                return issue
        return None

    def issue_body(self, issue_number: int) -> str | None:
        repo = self.repo
        if not repo:
            return None
        try:
            return self.run_gh(
                ["api", f"repos/{repo}/issues/{issue_number}", "--jq", ".body"]
            )
        except GhError:
            return None

    def issue_comments(self, issue_number: int) -> list[dict]:
        repo = self.repo
        if not repo:
            return []
        try:
            # The event bus is append-only; a partial first page would silently
            # drop later transitions. --paginate fetches every page.
            return self.gh_json_concat(
                ["api", "--paginate",
                 f"repos/{repo}/issues/{issue_number}/comments?per_page=100"]
            ) or []
        except GhError:
            return []
