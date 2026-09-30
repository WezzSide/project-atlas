"""Live REST implementation of ``GitHubPort`` (AS-DEVLOOP-001, slice 3 glue).

Only endpoints the existing execution path needs. The token is read by the caller and passed in; it
is sent solely to api.github.com. Artifact downloads follow the 302 to blob storage WITHOUT the
Authorization header. No method here merges, approves, deletes or edits anything except: dispatch
of one workflow, and opening a draft evidence PR.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from typing import Any

from project_atlas.orchestration.autonomy.dev_fabric_adapter import (
    AdapterError,
    CompareInfo,
    RunInfo,
)

API = "https://api.github.com"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a: Any, **k: Any) -> None:
        return None


def _run(d: dict[str, Any]) -> RunInfo:
    return RunInfo(
        run_id=int(d["id"]),
        attempt=int(d.get("run_attempt", 1)),
        workflow=str(d.get("path", "")).rsplit("/", 1)[-1],
        event=str(d["event"]),
        status=str(d["status"]),
        conclusion=d.get("conclusion"),
        head_branch=str(d.get("head_branch") or ""),
        created_at=str(d["created_at"]),
    )


class GitHubRestPort:
    def __init__(self, repo: str, token: str, *, owner: str | None = None) -> None:
        self.repo, self._token = repo, token
        self.owner = owner or repo.split("/", 1)[0]

    # seam for tests: (method, path, body) -> (status, json)
    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{API}/repos/{self.repo}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return 404, None
            raise AdapterError(f"github {method} {path} -> {exc.code}") from exc

    def dispatch_workflow(self, workflow: str, ref: str, inputs: dict[str, str]) -> None:
        st, _ = self._request(
            "POST", f"/actions/workflows/{workflow}/dispatches", {"ref": ref, "inputs": inputs}
        )
        if st != 204:
            raise AdapterError(f"dispatch returned {st}")

    def list_runs(self, workflow: str, *, event: str, created_after: str) -> list[RunInfo]:
        q = urllib.parse.urlencode(
            {"event": event, "created": f">={created_after}", "per_page": 30}
        )
        _, d = self._request("GET", f"/actions/workflows/{workflow}/runs?{q}")
        return [_run(r) for r in (d or {}).get("workflow_runs", [])]

    def get_run(self, run_id: int) -> RunInfo:
        st, d = self._request("GET", f"/actions/runs/{run_id}")
        if st == 404 or d is None:
            raise AdapterError(f"run {run_id} not found")
        return _run(d)

    def branch_head(self, branch: str) -> str | None:
        st, d = self._request("GET", f"/git/ref/heads/{branch}")
        if st == 404 or d is None:
            return None
        if d.get("ref") != f"refs/heads/{branch}" or d["object"]["type"] != "commit":
            raise AdapterError("unexpected ref shape")
        return str(d["object"]["sha"])

    def commit_tree(self, sha: str) -> str:
        _, d = self._request("GET", f"/git/commits/{sha}")
        if d is None:
            raise AdapterError("commit not found")
        return str(d["tree"]["sha"])

    def compare(self, base: str, head: str) -> CompareInfo:
        _, d = self._request("GET", f"/compare/{base}...{head}")
        if d is None or d.get("total_commits", 0) > len(d.get("commits", [])):
            raise AdapterError("compare unavailable or truncated")
        if len(d.get("files", [])) >= 300:
            raise AdapterError("compare file list may be truncated")
        return CompareInfo(
            merge_base=str(d["merge_base_commit"]["sha"]),
            files=tuple(sorted(f["filename"] for f in d.get("files", []))),
        )

    def _artifact(self, run_id: int, name: str) -> dict[str, Any]:
        _, d = self._request("GET", f"/actions/runs/{run_id}/artifacts")
        for a in (d or {}).get("artifacts", []):
            if a["name"] == name and not a.get("expired"):
                return dict(a)
        raise AdapterError(f"artifact {name} not found for run {run_id}")

    def artifact_digests(self, run_id: int, name: str) -> tuple[str, ...]:
        digest = str(self._artifact(run_id, name).get("digest", ""))
        if not digest.startswith("sha256:") or len(digest) != 7 + 64:
            raise AdapterError("artifact has no sha256 digest")
        return (digest[7:],)

    def read_json_artifact(self, run_id: int, name: str, member: str) -> dict[str, object]:
        art = self._artifact(run_id, name)
        opener = urllib.request.build_opener(_NoRedirect)
        req = urllib.request.Request(
            f"{API}/repos/{self.repo}/actions/artifacts/{art['id']}/zip",
            headers={"Authorization": f"Bearer {self._token}"},
        )
        try:
            opener.open(req, timeout=30)
            raise AdapterError("expected a redirect to artifact storage")
        except urllib.error.HTTPError as exc:
            loc = exc.headers.get("Location") if exc.code in (301, 302, 307) else None
            if not loc or not loc.startswith("https://"):
                raise AdapterError("artifact download redirect missing") from exc
        with urllib.request.urlopen(loc, timeout=60) as r:
            blob = r.read()
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            return dict(json.loads(z.read(member)))

    def check_runs(self, sha: str) -> dict[str, tuple[str, str | None]]:
        _, d = self._request("GET", f"/commits/{sha}/check-runs?per_page=100")
        return {
            c["name"]: (c["status"], c.get("conclusion")) for c in (d or {}).get("check_runs", [])
        }

    def ensure_draft_pr(self, head_branch: str, base: str, title: str, body: str) -> int:
        q = urllib.parse.urlencode({"head": f"{self.owner}:{head_branch}", "state": "all"})
        _, d = self._request("GET", f"/pulls?{q}")
        if d:
            return int(d[0]["number"])
        _, made = self._request(
            "POST",
            "/pulls",
            {"title": title, "head": head_branch, "base": base, "body": body, "draft": True},
        )
        return int(made["number"])
