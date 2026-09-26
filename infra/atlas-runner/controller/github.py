"""GitHub REST client + token providers for the atlas-runner controller.

Contract: outbound-only GitHub API access via urllib.request (no inbound
ports). Token providers are a small class hierarchy with fakes for tests.
AUTH_TOKEN != AUTHORITY: possession of a registration token grants runner
registration only, never repository authority. Secrets are redacted from all
logged material before it leaves this module's callers.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass


class GitHubError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


class AuthError(GitHubError):
    """401/403 from the GitHub API."""


class TokenProvider:
    """Base class: return a bearer token or raise."""

    def get_token(self) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def redaction_values(self) -> list[str]:
        return []


class StaticTokenProvider(TokenProvider):
    """Reads ATLAS_GITHUB_TOKEN (set via systemd EnvironmentFile, mode 0600)."""

    def __init__(self, env_var: str = "ATLAS_GITHUB_TOKEN"):
        self.env_var = env_var

    def get_token(self) -> str:
        token = os.environ.get(self.env_var, "").strip()
        if not token:
            raise AuthError(f"{self.env_var} is not set")
        return token

    def redaction_values(self) -> list[str]:
        token = os.environ.get(self.env_var, "").strip()
        return [token] if token else []


class GhCliTokenProvider(TokenProvider):
    """Fallback: mint a token via the `gh` CLI (only when configured)."""

    def __init__(self, args: tuple[str, ...] = ("auth", "token")):
        self.args = args
        self._cached: str | None = None

    def get_token(self) -> str:
        if self._cached:
            return self._cached
        proc = subprocess.run(
            ["gh", *self.args], capture_output=True, text=True, timeout=30, check=False
        )
        if proc.returncode != 0:
            raise AuthError(f"gh token provider failed: {proc.stderr.strip()[:200]}")
        self._cached = proc.stdout.strip()
        return self._cached

    def redaction_values(self) -> list[str]:
        return [self._cached] if self._cached else []


@dataclass(frozen=True)
class QueuedJob:
    run_id: int
    run_attempt: int
    job_id: int
    job_name: str
    labels: tuple[str, ...]


def redact_secrets(text: str, secrets: list[str]) -> str:
    """Replace every occurrence of known secret values. Fail closed on None."""
    if text is None:
        return ""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***REDACTED***")
    return text


class GitHubClient:
    """Minimal GitHub REST client; all methods raise GitHubError on failure."""

    def __init__(
        self,
        *,
        owner: str,
        repo: str,
        api_url: str = "https://api.github.com",
        token_provider: TokenProvider | None = None,
        timeout_seconds: float = 30.0,
    ):
        self.owner = owner
        self.repo = repo
        self.api_url = api_url.rstrip("/")
        self.token_provider = token_provider
        self.timeout_seconds = timeout_seconds

    # -- plumbing -----------------------------------------------------------
    def _request(self, method: str, path: str, body: dict | None = None) -> dict | list:
        url = f"{self.api_url}{path}"
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=payload, method=method)
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        if payload is not None:
            request.add_header("Content-Type", "application/json")
        if self.token_provider is not None:
            request.add_header("Authorization", f"Bearer {self.token_provider.get_token()}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise AuthError(
                    f"GitHub API auth failed ({exc.code}) for {method} {path}",
                    status=exc.code,
                ) from exc
            raise GitHubError(
                f"GitHub API error {exc.code} for {method} {path}",
                status=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise GitHubError(f"GitHub API unreachable for {method} {path}: {exc.reason}") from exc
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def repo_path(self, suffix: str) -> str:
        return f"/repos/{self.owner}/{self.repo}{suffix}"

    # -- API surface used by the controller ---------------------------------
    def check_reachable(self) -> tuple[bool, int | None]:
        """GET /repos/{owner}/{repo}; returns (ok, status)."""
        try:
            self._request("GET", self.repo_path(""))
            return True, 200
        except GitHubError as exc:
            return False, exc.status

    def list_queued_runs(self) -> list[dict]:
        data = self._request(
            "GET", self.repo_path("/actions/runs?status=queued&per_page=50")
        )
        return list(data.get("workflow_runs", [])) if isinstance(data, dict) else []

    def list_run_jobs(self, run_id: int) -> list[dict]:
        data = self._request(
            "GET", self.repo_path(f"/actions/runs/{run_id}/jobs?per_page=100")
        )
        return list(data.get("jobs", [])) if isinstance(data, dict) else []

    def queued_jobs(self, *, admitted_labels: frozenset[str]) -> list[QueuedJob]:
        """All queued jobs whose labels are a subset of our label set."""
        out: list[QueuedJob] = []
        for run in self.list_queued_runs():
            run_id = int(run["id"])
            run_attempt = int(run.get("run_attempt", 1))
            for job in self.list_run_jobs(run_id):
                if job.get("status") != "queued":
                    continue
                labels = tuple(str(label) for label in job.get("labels", []))
                if admitted_labels.issuperset(labels):
                    out.append(
                        QueuedJob(
                            run_id=run_id,
                            run_attempt=run_attempt,
                            job_id=int(job["id"]),
                            job_name=str(job.get("name", "")),
                            labels=labels,
                        )
                    )
        return out

    def generate_jitconfig(self, *, name: str, labels: list[str], work_folder: str) -> dict:
        """POST generate-jitconfig; returns the decoded config payload."""
        return dict(
            self._request(
                "POST",
                self.repo_path("/actions/runners/generate-jitconfig"),
                {"name": name, "labels": labels, "work_folder": work_folder, "runner_group_id": 1},
            )
        )

    def generate_registration_token(self) -> str:
        data = dict(
            self._request("POST", self.repo_path("/actions/runners/registration-token"))
        )
        token = data.get("token")
        if not token:
            raise GitHubError("registration-token response missing 'token'")
        return str(token)

    def list_runners(self) -> list[dict]:
        data = self._request("GET", self.repo_path("/actions/runners?per_page=100"))
        return list(data.get("runners", [])) if isinstance(data, dict) else []

    def delete_runner(self, runner_id: int) -> None:
        self._request("DELETE", self.repo_path(f"/actions/runners/{runner_id}"))
