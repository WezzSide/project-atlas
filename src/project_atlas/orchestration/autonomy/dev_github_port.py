"""Live REST implementation of ``GitHubPort`` (AS-DEVLOOP-001, slice 3 glue).

Only endpoints the existing execution path needs. The token is read by the caller and passed in; it
is sent solely to api.github.com. Artifact downloads follow the 302 to blob storage WITHOUT the
Authorization header. No method here merges, approves, deletes or edits anything except: dispatch
of one workflow, and opening a draft evidence PR.
"""

from __future__ import annotations

import functools
import http.client
import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from collections.abc import Callable
from typing import Any

from project_atlas.orchestration.autonomy.dev_fabric_adapter import (
    AdapterError,
    CheckRun,
    CompareInfo,
    RunInfo,
)

API = "https://api.github.com"
_REPO = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")
_BRANCH = re.compile(r"[A-Za-z0-9._/-]+")
_ARTIFACT_HOSTS = (".blob.core.windows.net", ".githubusercontent.com")
_FILE_STATUS = frozenset({"added", "modified", "removed", "renamed", "copied", "changed"})
MAX_BLOB = 5 * 1024 * 1024
MAX_MEMBER = 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a: Any, **k: Any) -> None:
        return None


def _origin(url: str) -> tuple[str, str, int | None] | None:
    parts = urllib.parse.urlsplit(url)
    scheme = parts.scheme.lower()
    try:
        port = parts.port or {"http": 80, "https": 443}.get(scheme)
    except ValueError:
        return None
    return scheme, (parts.hostname or "").lower(), port


class _CrossOriginRedirect(Exception):
    """Carries nothing: the refused Location must never reach a message or cause."""


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects only within the API origin, so Authorization never leaves it."""

    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> urllib.request.Request | None:
        api = _origin(API)
        if api is None or _origin(newurl) != api:
            fp.close()
            raise _CrossOriginRedirect
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _SawResponse(urllib.request.BaseHandler):
    """Records that the server answered, so a later ValueError is known to be redirect handling."""

    seen = False

    def http_response(self, request: Any, response: Any) -> Any:
        self.seen = True
        return response

    https_response = http_response


# Response headers that can name another URL (redirect target, signed query, alternate location).
_URL_HEADERS = frozenset({"location", "uri", "content-location", "link"})


def _names_url(headers: Any) -> bool:
    """True if any URL-bearing header is present, whatever the mapping type or name case."""
    return headers is not None and any(str(k).lower() in _URL_HEADERS for k in headers)


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


MAX_RUN_PAGES = 10


class GitHubRestPort:
    def __init__(self, repo: str, token: str, *, owner: str | None = None) -> None:
        if not _REPO.fullmatch(repo):
            raise AdapterError("invalid repository name")
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
        answered = _SawResponse()
        opener = urllib.request.build_opener(_SameOriginRedirect, answered)
        # Errors that may carry a redirect Location (or the token) are raised after the except
        # block, detached (no __cause__/__context__), so nothing can leak via the chain.
        detached: str | None = None
        try:
            with opener.open(req, timeout=30) as r:
                status, raw = r.status, r.read()
        except _CrossOriginRedirect:
            detached = f"github {method} {path} refused cross-origin redirect"
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return 404, None
            if not _names_url(exc.headers) and exc.filename == req.full_url:
                raise AdapterError(f"github {method} {path} -> {exc.code}") from exc
            detached = f"github {method} {path} -> {exc.code}"
        except (urllib.error.URLError, TimeoutError) as exc:
            raise AdapterError(f"github {method} {path} unreachable: {exc}") from exc
        except ValueError:
            # After a response, urllib is rejecting a malformed Location (e.g. invalid bracketed
            # host). Before any response it is our own request that could not be sent (e.g. a
            # token that is not a valid header value, a non-ASCII path); that ValueError can
            # quote the Authorization value, so it is detached too and only the cause is named.
            what = "URL" if answered.seen else "request"
            detached = f"github {method} {path} rejected invalid {what}"
        if detached is not None:
            raise AdapterError(detached) from None
        return status, (json.loads(raw) if raw else None)

    def dispatch_workflow(self, workflow: str, ref: str, inputs: dict[str, str]) -> None:
        st, _ = self._request(
            "POST", f"/actions/workflows/{workflow}/dispatches", {"ref": ref, "inputs": inputs}
        )
        if st != 204:
            raise AdapterError(f"dispatch returned {st}")

    def list_runs(self, workflow: str, *, event: str, created_after: str) -> list[RunInfo]:
        out: list[RunInfo] = []
        for page_no in range(1, MAX_RUN_PAGES + 1):
            q = urllib.parse.urlencode(
                {
                    "event": event,
                    "created": f">={created_after}",
                    "per_page": 100,
                    "page": page_no,
                }
            )
            _, d = self._request("GET", f"/actions/workflows/{workflow}/runs?{q}")
            batch = (d or {}).get("workflow_runs", [])
            out.extend(_run(r) for r in batch)
            if len(batch) < 100:
                return out
        raise AdapterError("too many workflow runs to list safely; refusing to guess")

    def get_run(self, run_id: int) -> RunInfo:
        st, d = self._request("GET", f"/actions/runs/{run_id}")
        if st == 404 or d is None:
            raise AdapterError(f"run {run_id} not found")
        return _run(d)

    def branch_head(self, branch: str) -> str | None:
        if not _BRANCH.fullmatch(branch) or ".." in branch:
            raise AdapterError("unsafe branch name")
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
        names: set[str] = set()
        for f in d.get("files", []):
            if f.get("status") not in _FILE_STATUS:
                raise AdapterError(f"unknown file status {f.get('status')!r}")
            names.add(str(f["filename"]))
            if f.get("previous_filename"):  # a rename/copy also touches its source path
                names.add(str(f["previous_filename"]))
        return CompareInfo(
            merge_base=str(d["merge_base_commit"]["sha"]), files=tuple(sorted(names))
        )

    def _artifact(self, run_id: int, name: str) -> dict[str, Any]:
        _, d = self._request("GET", f"/actions/runs/{run_id}/artifacts")
        live = [
            a for a in (d or {}).get("artifacts", []) if a["name"] == name and not a.get("expired")
        ]
        if live:
            return dict(max(live, key=lambda a: int(a["id"])))  # latest upload wins
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
        # Same rule as _request: an error that can carry the token (invalid header value), the
        # signed Location or its query is raised after the except block, detached. The messages
        # are the ones callers already saw (the last two are what _guarded produced).
        loc: str | None = None
        redirect = False
        blob = b""
        detached: str | None = None
        try:
            opener.open(req, timeout=30)
            detached = "expected a redirect to artifact storage"
        except urllib.error.HTTPError as exc:
            loc = exc.headers.get("Location") if exc.code in (301, 302, 307) else None
            redirect = True
        except (ValueError, http.client.HTTPException) as exc:
            detached = f"github port read_json_artifact failed: {type(exc).__name__}"
        if redirect:  # validated outside the handler: a malformed Location raises ValueError
            try:
                host = urllib.parse.urlparse(loc or "").hostname or ""
            except ValueError:
                detached = "github port read_json_artifact failed: ValueError"
            else:
                if not loc or not loc.startswith("https://") or not host.endswith(_ARTIFACT_HOSTS):
                    detached = "artifact download redirect missing or untrusted"
        if detached is None and loc is not None:
            try:
                with urllib.request.urlopen(loc, timeout=60) as r:
                    blob = r.read(MAX_BLOB + 1)
            except (OSError, ValueError, http.client.HTTPException) as exc:
                detached = f"github port read_json_artifact failed: {type(exc).__name__}"
        if detached is not None:
            raise AdapterError(detached) from None
        if len(blob) > MAX_BLOB:
            raise AdapterError("artifact too large")
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            info = z.getinfo(member)
            if info.file_size > MAX_MEMBER:
                raise AdapterError("artifact member too large")
            return dict(json.loads(z.read(info)))

    def check_runs(self, sha: str) -> list[CheckRun]:
        out: list[CheckRun] = []
        for page in range(1, 11):
            _, d = self._request("GET", f"/commits/{sha}/check-runs?per_page=100&page={page}")
            runs = (d or {}).get("check_runs", [])
            out += [CheckRun(c["name"], c["status"], c.get("conclusion")) for c in runs]
            if len(runs) < 100:
                return out
        raise AdapterError("too many check runs to page through")

    def ensure_draft_pr(self, head_branch: str, base: str, title: str, body: str) -> int:
        q = urllib.parse.urlencode({"head": f"{self.owner}:{head_branch}", "state": "open"})
        _, d = self._request("GET", f"/pulls?{q}")
        if d:
            return int(d[0]["number"])
        _, made = self._request(
            "POST",
            "/pulls",
            {"title": title, "head": head_branch, "base": base, "body": body, "draft": True},
        )
        return int(made["number"])


def _guarded(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Every parse/IO failure of the real port surfaces as AdapterError (never a raw KeyError)."""

    @functools.wraps(fn)
    def wrapper(*a: Any, **kw: Any) -> Any:
        try:
            return fn(*a, **kw)
        except AdapterError:
            raise
        except (
            KeyError,
            ValueError,
            TypeError,
            IndexError,
            AttributeError,
            OSError,  # includes URLError/HTTPError/TimeoutError
            zipfile.BadZipFile,
            zipfile.LargeZipFile,
            zlib.error,
            NotImplementedError,  # unsupported zip compression
            http.client.HTTPException,  # IncompleteRead & co
            RecursionError,  # hostile deeply-nested JSON in an artifact
        ) as exc:
            raise AdapterError(f"github port {fn.__name__} failed: {type(exc).__name__}") from exc

    return wrapper


for _name in (
    "dispatch_workflow",
    "list_runs",
    "get_run",
    "branch_head",
    "commit_tree",
    "compare",
    "artifact_digests",
    "read_json_artifact",
    "check_runs",
    "ensure_draft_pr",
):
    setattr(GitHubRestPort, _name, _guarded(getattr(GitHubRestPort, _name)))
