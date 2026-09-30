"""Runtime-aware mutation path attribution (D-116).

LOCAL workers prove deltas from the governed local worktree.
CLOUD workers prove deltas from remote Git pushed by the Cloud run.
LOCAL repository state must never be accepted as proof of a CLOUD mutation.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final, Protocol

from pydantic import BaseModel, ConfigDict, Field

from project_atlas.orchestration.sdk.models import (
    CANONICAL_REPO_URL,
    PACKAGE_ID,
    STATE_DIR_RELATIVE,
    AgentRuntime,
    SdkRuntimeError,
)
from project_atlas.orchestration.sdk.security_gates import collect_actual_changed_paths

ATTRIBUTION_STORE_NAME: Final[str] = "run-mutation-attribution.json"
AGENT_REMOTE_HIGH_WATER_NAME: Final[str] = "agent-remote-high-water.json"
FINDING_ID: Final[str] = "ORCH-SDK-CLOUD-PATH-ATTRIBUTION-001"
# Sentinel: prior remote post known undetermined — mint must not fall back to base_main.
REMOTE_HIGH_WATER_UNDETERMINED: Final[str] = "__REMOTE_HIGH_WATER_UNDETERMINED__"

# Exact host/path identity only. Never use search()/suffix match — foreign hosts that
# embed "github.com/..." as a path segment must not collide with canonical.
_REPO_HOST_PATH = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com[/:](?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?/?$",
    re.IGNORECASE,
)
_OWNER_REPO_ONLY = re.compile(
    r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$",
)


class RunGitInfo(BaseModel):
    """Terminal Run.git snapshot from Cursor SDK. Evidence only.

    ``head_sha`` is optional run-scoped post evidence when the SDK surfaces it.
    Prefer it over branch-tip ``ls-remote`` (ORCH-SDK-CLOUD-POST-HEAD-BRANCH-TIP-TOCTOU-001).
    """

    model_config = ConfigDict(extra="forbid")

    repo_url: str | None = None
    branches: tuple[str, ...] = ()
    head_sha: str | None = None


class RunMutationBaseline(BaseModel):
    """Durable per-run remote attribution state. Not authority."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    agent_id: str
    runtime: AgentRuntime
    repository: str = CANONICAL_REPO_URL
    base_main: str
    remote_branch: str | None = None
    remote_pre_head: str | None = None
    remote_post_head: str | None = None
    # Snapshot of remote ``cursor/*`` heads at mint time. Used when authentic
    # Cloud Run.git omits branch names (ORCH-SDK-CLOUD-RUN-GIT-EMPTY-BRANCH-001).
    remote_auto_branches_pre: tuple[str, ...] | None = None
    dag_generation: int = Field(ge=0, le=1_000_000)
    lease_id: str | None = None
    package_id: str = PACKAGE_ID


class MutationAttributionProvider(Protocol):
    """Trust boundary: each execution backend must attribute its own mutations."""

    def collect_changed_paths(
        self,
        *,
        root: Path,
        attribution: RunMutationBaseline,
        terminal_git: RunGitInfo | None,
        local_pre_head: str | None,
    ) -> list[str] | None: ...


_PRE_TRANSFER_GITHUB_OWNERS: Final[frozenset[str]] = frozenset({"b0lk13", "bolkdev"})
_LIVE_GITHUB_OWNER: Final[str] = "WezzSide"
_LIVE_GITHUB_REPO: Final[str] = "project-atlas"


def _live_github_identity(owner: str, repo: str) -> str:
    """Map prior GitHub owners onto the live owner (WezzSide)."""
    repo_name = repo.removesuffix(".git")
    if (
        owner.casefold() in _PRE_TRANSFER_GITHUB_OWNERS
        and repo_name.casefold() == _LIVE_GITHUB_REPO
    ):
        owner = _LIVE_GITHUB_OWNER
    return f"https://github.com/{owner}/{repo_name}".casefold()


def normalize_repo_identity(raw: str | None) -> str | None:
    """Normalize repo URLs to a comparable identity.

    Accepts only:
    - canonical GitHub host forms (``github.com/owner/repo`` with optional scheme)
    - bare ``owner/repo`` (no foreign host labels)

    Rejects foreign hosts (gitlab/evil/etc.) and suffix tricks such as
    ``evil.com/github.com/WezzSide/project-atlas`` that previously matched via
    ``re.search``. Pre-transfer ``github.com/B0LK13/project-atlas``
    identities alias to the live owner so old remotes still attribute.
    """
    if raw is None:
        return None
    text = raw.strip().replace("\\", "/")
    if not text:
        return None
    # git@github.com:owner/repo
    if text.casefold().startswith("git@"):
        _, remainder = text.split("@", 1)
        text = remainder.replace(":", "/", 1)
    for prefix in ("https://", "http://", "ssh://", "git://"):
        if text.casefold().startswith(prefix):
            text = text[len(prefix) :]
            break
    text = text.strip("/")
    match = _REPO_HOST_PATH.fullmatch(text)
    if match is not None:
        owner = match.group("owner")
        repo = match.group("repo")
        return _live_github_identity(owner, repo)
    # Bare owner/repo only — reject anything with an extra host/path segment.
    bare = _OWNER_REPO_ONLY.fullmatch(text)
    if bare is not None and "." not in bare.group("owner"):
        owner = bare.group("owner")
        repo = bare.group("repo")
        return _live_github_identity(owner, repo)
    # Foreign or malformed: return a stable non-canonical identity when a host
    # is present so callers raise "foreign repository"; otherwise None.
    parts = [p for p in text.split("/") if p]
    if len(parts) >= 3 and "." in parts[0]:
        host, owner, repo = parts[0], parts[1], parts[2].removesuffix(".git")
        return f"https://{host}/{owner}/{repo}".casefold()
    if len(parts) == 2 and "." in parts[0]:
        # host/repo without owner — not a valid GitHub identity
        return text.rstrip("/").casefold()
    return None


def canonical_repo_identity() -> str:
    return normalize_repo_identity(CANONICAL_REPO_URL) or CANONICAL_REPO_URL.casefold()


def attribution_store_path(root: Path) -> Path:
    return root / STATE_DIR_RELATIVE / ATTRIBUTION_STORE_NAME


def agent_remote_high_water_path(root: Path) -> Path:
    return root / STATE_DIR_RELATIVE / AGENT_REMOTE_HIGH_WATER_NAME


def persist_run_mutation_baseline(root: Path, baseline: RunMutationBaseline) -> None:
    path = attribution_store_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError) as exc:
            raise SdkRuntimeError(
                "corrupt run mutation attribution store",
                code="REMOTE_ATTRIBUTION_UNDETERMINED",
            ) from exc
    data[baseline.run_id] = baseline.model_dump(mode="json")
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    tmp.replace(path)


def load_run_mutation_baseline(root: Path, run_id: str) -> RunMutationBaseline | None:
    path = attribution_store_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SdkRuntimeError(
            "corrupt run mutation attribution store",
            code="REMOTE_ATTRIBUTION_UNDETERMINED",
        ) from exc
    if not isinstance(data, dict):
        raise SdkRuntimeError(
            "corrupt run mutation attribution store",
            code="REMOTE_ATTRIBUTION_UNDETERMINED",
        )
    row = data.get(run_id)
    if row is None:
        return None
    try:
        return RunMutationBaseline.model_validate(row)
    except ValueError as exc:
        raise SdkRuntimeError(
            "corrupt run mutation attribution row",
            code="REMOTE_ATTRIBUTION_UNDETERMINED",
        ) from exc


def load_agent_remote_high_water(root: Path, agent_id: str) -> str | None:
    path = agent_remote_high_water_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SdkRuntimeError(
            "corrupt agent remote high-water store",
            code="REMOTE_ATTRIBUTION_UNDETERMINED",
        ) from exc
    if not isinstance(data, dict):
        raise SdkRuntimeError(
            "corrupt agent remote high-water store",
            code="REMOTE_ATTRIBUTION_UNDETERMINED",
        )
    value = data.get(agent_id)
    if value is None:
        return None
    text = str(value).strip()
    return text if len(text) >= 7 else None


def persist_agent_remote_high_water(root: Path, agent_id: str, sha: str) -> None:
    path = agent_remote_high_water_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError) as exc:
            raise SdkRuntimeError(
                "corrupt agent remote high-water store",
                code="REMOTE_ATTRIBUTION_UNDETERMINED",
            ) from exc
    data[agent_id] = sha
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    tmp.replace(path)


def mark_agent_remote_high_water_undetermined(root: Path, agent_id: str) -> None:
    """Record that prior remote post is undetermined (disconnect / failed enforce).

    ORCH-SDK-CLOUD-DISCONNECT-HW-GAP-001: subsequent mint must not silently
    re-baseline to base_main.
    """
    persist_agent_remote_high_water(root, agent_id, REMOTE_HIGH_WATER_UNDETERMINED)


def agent_remote_high_water_is_undetermined(value: str | None) -> bool:
    return value == REMOTE_HIGH_WATER_UNDETERMINED


def _agent_has_incomplete_cloud_baseline(root: Path, agent_id: str) -> bool:
    """True when a prior CLOUD baseline for this agent never recorded post_head."""
    path = attribution_store_path(root)
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    if not isinstance(data, dict):
        return True
    for row in data.values():
        if not isinstance(row, dict):
            continue
        if str(row.get("agent_id") or "") != agent_id:
            continue
        if str(row.get("runtime") or "") != AgentRuntime.CLOUD.value:
            continue
        post = row.get("remote_post_head")
        if post is None or (isinstance(post, str) and len(post.strip()) < 7):
            return True
    return False


def _mapping_or_attr(obj: Any, *names: str) -> Any:
    """Read an explicit field from a typed object or mapping. No recursion."""
    if obj is None:
        return None
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return None


def _locate_terminal_git_object(source: Any) -> Any | None:
    """Bounded D-119 shapes only: source.git | source.run.git | dict forms."""
    if source is None:
        return None
    git_obj = _mapping_or_attr(source, "git")
    if git_obj is not None:
        return git_obj
    run_obj = _mapping_or_attr(source, "run")
    if run_obj is not None:
        return _mapping_or_attr(run_obj, "git")
    return None


def _read_repo_url_from_git_object(git_obj: Any) -> str | None:
    """Explicit git.* fields only (plus observed branch-entry repo_url on git)."""
    repo = _mapping_or_attr(git_obj, "repo_url", "repoUrl")
    if repo is not None:
        return str(repo)
    nested = _mapping_or_attr(git_obj, "repo", "repository")
    if nested is not None:
        nested_url = _mapping_or_attr(nested, "url", "repo_url", "repoUrl")
        if nested_url is not None:
            return str(nested_url)
    # Authentic cursor_sdk: repo_url may live on git.branches[] entries.
    branches_raw = _mapping_or_attr(git_obj, "branches")
    if isinstance(branches_raw, (list, tuple)):
        for item in branches_raw:
            if isinstance(item, str):
                continue
            item_repo = _mapping_or_attr(item, "repo_url", "repoUrl", "url")
            if item_repo is not None:
                return str(item_repo)
    return None


def _read_head_sha_from_git_object(git_obj: Any) -> str | None:
    head_sha = _mapping_or_attr(
        git_obj, "head_sha", "headSha", "commit_sha", "commitSha", "sha"
    )
    branches_raw = _mapping_or_attr(git_obj, "branches")
    if head_sha is None and isinstance(branches_raw, (list, tuple)):
        for item in branches_raw:
            if isinstance(item, str):
                continue
            head_sha = _mapping_or_attr(
                item, "head_sha", "headSha", "commit_sha", "commitSha", "sha"
            )
            if head_sha is not None:
                break
    if head_sha is None:
        return None
    text = str(head_sha).strip()
    return text if len(text) >= 7 else None


def _read_branches_from_git_object(git_obj: Any) -> tuple[str, ...]:
    branches_raw = _mapping_or_attr(git_obj, "branches")
    branches: list[str] = []
    if isinstance(branches_raw, (list, tuple)):
        for item in branches_raw:
            if isinstance(item, str):
                name = item.strip()
            else:
                name = str(
                    _mapping_or_attr(item, "name", "branch") or ""
                ).strip()
            if name:
                branches.append(name)
    return tuple(branches)


def extract_terminal_run_git(source: Any) -> RunGitInfo | None:
    """D-119 bounded adapter: locate Run.git then read explicit fields only.

    Allowed traversal: ``source.git``, ``source.run.git``, and dict equivalents.
    No arbitrary recursive walk / substring search / prose parsing.
    """
    git_obj = _locate_terminal_git_object(source)
    if git_obj is None:
        return None
    return RunGitInfo(
        repo_url=_read_repo_url_from_git_object(git_obj),
        branches=_read_branches_from_git_object(git_obj),
        head_sha=_read_head_sha_from_git_object(git_obj),
    )


def extract_run_git(result: Any) -> RunGitInfo | None:
    """Compatibility alias for :func:`extract_terminal_run_git`."""
    return extract_terminal_run_git(result)


def bind_terminal_git_repository(
    terminal_git: RunGitInfo,
    *,
    attribution: RunMutationBaseline,
) -> RunGitInfo:
    """Apply trusted launch binding vs terminal SDK echo (D-119).

    - ``REPO_URL_PRESENT_AND_FOREIGN`` → fail closed
    - ``REPO_URL_ABSENT`` + valid canonical launch baseline → supply from baseline
    - ``REPO_URL_ABSENT`` without valid baseline → fail closed
    """
    canon = canonical_repo_identity()
    base_id = normalize_repo_identity(attribution.repository)
    if terminal_git.repo_url is not None:
        echo_id = normalize_repo_identity(terminal_git.repo_url)
        if (
            echo_id is None
            or canon is None
            or echo_id != canon
            or base_id != canon
            or echo_id != base_id
        ):
            raise SdkRuntimeError(
                "foreign repository in terminal Run.git",
                code="REMOTE_ATTRIBUTION_UNDETERMINED",
            )
        return terminal_git
    # Omitted repo_url: launch baseline may supply ONLY under strict binding.
    if attribution.runtime != AgentRuntime.CLOUD:
        raise SdkRuntimeError(
            "terminal Run.git missing repository",
            code="REMOTE_ATTRIBUTION_UNDETERMINED",
        )
    if attribution.package_id != PACKAGE_ID:
        raise SdkRuntimeError(
            "launch baseline package_id mismatch",
            code="REMOTE_ATTRIBUTION_UNDETERMINED",
        )
    if base_id != canon:
        raise SdkRuntimeError(
            "launch baseline repository is not canonical Atlas",
            code="REMOTE_ATTRIBUTION_UNDETERMINED",
        )
    if not attribution.remote_pre_head or len(attribution.remote_pre_head) < 7:
        raise SdkRuntimeError(
            "launch baseline remote_pre_head invalid",
            code="REMOTE_ATTRIBUTION_UNDETERMINED",
        )
    if not attribution.run_id or not attribution.agent_id:
        raise SdkRuntimeError(
            "launch baseline missing run/agent binding",
            code="REMOTE_ATTRIBUTION_UNDETERMINED",
        )
    return terminal_git.model_copy(update={"repo_url": attribution.repository})


def list_remote_auto_branches(repository: str) -> tuple[str, ...]:
    """List remote ``cursor/*`` branch names via git ls-remote. Fail soft → ()."""
    import subprocess

    try:
        completed = subprocess.run(
            ["git", "ls-remote", "--heads", repository],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if completed.returncode != 0:
        return ()
    names: list[str] = []
    for line in (completed.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        ref = parts[1].strip()
        prefix = "refs/heads/"
        if not ref.startswith(prefix):
            continue
        name = ref[len(prefix) :]
        if name.startswith("cursor/"):
            names.append(name)
    return tuple(sorted(set(names)))


def discover_new_remote_auto_branch(
    repository: str, *, pre_branches: tuple[str, ...] | None
) -> str:
    """Bind the unique new ``cursor/*`` branch created after mint snapshot.

    Authentic Cloud GetRun often returns ``git.branches[].repoUrl`` with an
    omitted/empty ``branch`` field. Fail closed unless exactly one new
    ``cursor/*`` head appeared since ``remote_auto_branches_pre``.
    """
    if pre_branches is None:
        raise SdkRuntimeError(
            "terminal Run.git missing branches and no auto-branch mint snapshot",
            code="REMOTE_ATTRIBUTION_UNDETERMINED",
        )
    post = set(list_remote_auto_branches(repository))
    pre = set(pre_branches)
    new = sorted(post - pre)
    if len(new) == 1:
        return new[0]
    if not new:
        raise SdkRuntimeError(
            "terminal Run.git missing branches and no new cursor/* auto-branch",
            code="REMOTE_ATTRIBUTION_UNDETERMINED",
        )
    raise SdkRuntimeError(
        "ambiguous new cursor/* auto-branches after empty Run.git branch",
        code="REMOTE_ATTRIBUTION_UNDETERMINED",
    )


def select_canonical_remote_branch(
    terminal_git: RunGitInfo,
    *,
    expected_branch: str | None,
    auto_branches_pre: tuple[str, ...] | None = None,
    repository: str | None = None,
) -> str:
    """Fail closed unless exactly one canonical Atlas branch can be bound."""
    repo_id = normalize_repo_identity(terminal_git.repo_url)
    if repo_id is None:
        raise SdkRuntimeError(
            "terminal Run.git missing repository",
            code="REMOTE_ATTRIBUTION_UNDETERMINED",
        )
    if repo_id != canonical_repo_identity():
        raise SdkRuntimeError(
            "foreign repository in terminal Run.git",
            code="REMOTE_ATTRIBUTION_UNDETERMINED",
        )
    branches = [b for b in terminal_git.branches if b.strip()]
    if not branches:
        # ORCH-SDK-CLOUD-RUN-GIT-EMPTY-BRANCH-001: Cloud may omit branch names.
        if expected_branch and expected_branch.strip():
            return expected_branch.strip()
        repo = repository or terminal_git.repo_url or CANONICAL_REPO_URL
        return discover_new_remote_auto_branch(repo, pre_branches=auto_branches_pre)
    if len(branches) > 1:
        raise SdkRuntimeError(
            "ambiguous Atlas branches in Run.git",
            code="REMOTE_ATTRIBUTION_UNDETERMINED",
        )
    branch = branches[0]
    if expected_branch and branch != expected_branch:
        raise SdkRuntimeError(
            "cloud branch changed unexpectedly vs lineage",
            code="REMOTE_ATTRIBUTION_UNDETERMINED",
        )
    return branch


def resolve_commit_first_parent(
    repository: str, commit: str, *, work_root: Path
) -> str | None:
    """Return first parent SHA of ``commit`` after fetching into ``work_root``."""
    import subprocess

    try:
        fetched = subprocess.run(
            ["git", "fetch", "--no-tags", repository, commit],
            cwd=str(work_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if fetched.returncode != 0:
        pass
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", f"{commit}^"],
            cwd=str(work_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if probe.returncode != 0:
        return None
    parent = (probe.stdout or "").strip()
    return parent if len(parent) >= 7 else None


def default_resolve_remote_head(repository: str, branch: str) -> str | None:
    """Resolve remote branch HEAD via git ls-remote. None => undetermined."""
    import subprocess

    try:
        completed = subprocess.run(
            ["git", "ls-remote", repository, f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    for line in (completed.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].strip():
            sha = parts[0].strip()
            if len(sha) >= 7:
                return sha
    return None


def default_resolve_remote_diff(
    repository: str, pre_head: str, post_head: str, *, work_root: Path
) -> list[str] | None:
    """Fetch remote SHAs into object DB and diff names. Never uses worktree content."""
    import subprocess

    def _fetch() -> subprocess.CompletedProcess[str]:
        # Avoid --depth=0 (invalid/flaky). Fetch the two SHAs fully so ancestry
        # checks cannot false-positive as "not a descendant".
        return subprocess.run(
            ["git", "fetch", "--no-tags", repository, pre_head, post_head],
            cwd=str(work_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )

    try:
        fetched = _fetch()
    except (OSError, subprocess.TimeoutExpired):
        try:
            fetched = _fetch()
        except (OSError, subprocess.TimeoutExpired):
            return None
    if fetched.returncode != 0:
        # Objects may already exist locally from a prior fetch.
        pass

    def _commit_exists(sha: str) -> bool:
        try:
            probe = subprocess.run(
                ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
                cwd=str(work_root),
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return probe.returncode == 0

    if not _commit_exists(pre_head) or not _commit_exists(post_head):
        # Incomplete object DB — undetermined, not a positive non-descendant claim.
        return None

    try:
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", pre_head, post_head],
            cwd=str(work_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if ancestor.returncode != 0:
        raise SdkRuntimeError(
            "remote terminal head is not a descendant of baseline",
            code="REMOTE_ATTRIBUTION_UNDETERMINED",
        )
    try:
        diff = subprocess.run(
            ["git", "diff", "--name-only", "--find-renames", pre_head, post_head],
            cwd=str(work_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if diff.returncode != 0:
        return None
    paths = sorted(
        {line.strip().strip('"') for line in (diff.stdout or "").splitlines() if line.strip()}
    )
    return paths


class LocalWorktreeAttributionProvider:
    def collect_changed_paths(
        self,
        *,
        root: Path,
        attribution: RunMutationBaseline,
        terminal_git: RunGitInfo | None,
        local_pre_head: str | None,
    ) -> list[str] | None:
        del attribution, terminal_git
        return collect_actual_changed_paths(root, pre_head=local_pre_head)


class CloudRemoteGitAttributionProvider:
    def __init__(
        self,
        *,
        resolve_remote_head: Callable[[str, str], str | None] | None = None,
        resolve_remote_diff: Callable[[str, str, str], list[str] | None] | None = None,
    ) -> None:
        self._resolve_head = resolve_remote_head or default_resolve_remote_head
        self._resolve_diff = resolve_remote_diff

    def collect_changed_paths(
        self,
        *,
        root: Path,
        attribution: RunMutationBaseline,
        terminal_git: RunGitInfo | None,
        local_pre_head: str | None,
    ) -> list[str] | None:
        # Explicitly ignore local worktree state for CLOUD attribution.
        del local_pre_head
        if terminal_git is None:
            raise SdkRuntimeError(
                "terminal Run.git absent for CLOUD mutating run",
                code="REMOTE_ATTRIBUTION_UNDETERMINED",
            )
        if not attribution.remote_pre_head or len(attribution.remote_pre_head) < 7:
            raise SdkRuntimeError(
                "missing CLOUD remote baseline",
                code="REMOTE_ATTRIBUTION_UNDETERMINED",
            )
        # D-119: bind trusted launch repository when Run.git omits repo_url;
        # foreign present echo always fails closed.
        bound_git = bind_terminal_git_repository(
            terminal_git, attribution=attribution
        )
        branch = select_canonical_remote_branch(
            bound_git,
            expected_branch=attribution.remote_branch,
            auto_branches_pre=attribution.remote_auto_branches_pre,
            repository=attribution.repository,
        )
        if (
            attribution.remote_branch
            and branch != attribution.remote_branch
        ):
            raise SdkRuntimeError(
                "cloud branch changed unexpectedly vs lineage",
                code="REMOTE_ATTRIBUTION_UNDETERMINED",
            )
        # Prefer run-scoped SHA when SDK provides it (closes branch-tip TOCTOU
        # for that run). Current cursor_sdk RunGitInfo has no SHA field — tip
        # resolution remains a documented residual hazard covered by regression
        # tests that prove SHA preference when present.
        post: str | None = None
        if bound_git.head_sha and len(bound_git.head_sha) >= 7:
            post = bound_git.head_sha.strip()
        else:
            post = self._resolve_head(attribution.repository, branch)
        if post is None or len(post) < 7:
            raise SdkRuntimeError(
                "remote terminal HEAD cannot be resolved",
                code="REMOTE_ATTRIBUTION_UNDETERMINED",
            )
        pre = attribution.remote_pre_head
        paths: list[str] | None
        try:
            if self._resolve_diff is not None:
                paths = self._resolve_diff(attribution.repository, pre, post)
            else:
                paths = default_resolve_remote_diff(
                    attribution.repository,
                    pre,
                    post,
                    work_root=root,
                )
        except SdkRuntimeError as exc:
            # ORCH-SDK-CLOUD-STARTING-REF-PRE-MISMATCH-001: authentic Cloud may
            # parent auto-branches on a SHA other than schedule base_main while
            # Run.git omits branch names. Bind the single-commit delta via the
            # tip's first parent — fail closed when branches were explicit.
            if (
                self._resolve_diff is not None
                or exc.code != "REMOTE_ATTRIBUTION_UNDETERMINED"
                or "not a descendant" not in str(exc).casefold()
                or bound_git.branches
                or attribution.remote_auto_branches_pre is None
            ):
                raise
            parent = resolve_commit_first_parent(
                attribution.repository, post, work_root=root
            )
            if parent is None or parent.casefold() == post.casefold():
                raise
            paths = default_resolve_remote_diff(
                attribution.repository,
                parent,
                post,
                work_root=root,
            )
            if paths is None:
                raise SdkRuntimeError(
                    "remote changed-path diff undetermined after first-parent fallback",
                    code="REMOTE_ATTRIBUTION_UNDETERMINED",
                ) from exc
            attribution.remote_pre_head = parent
        if paths is None:
            raise SdkRuntimeError(
                "remote changed-path diff undetermined",
                code="REMOTE_ATTRIBUTION_UNDETERMINED",
            )
        # Persist terminal head onto the mutable baseline copy for caller.
        attribution.remote_branch = branch
        attribution.remote_post_head = post
        return paths


def collect_run_changed_paths(
    root: Path,
    *,
    runtime: AgentRuntime,
    attribution: RunMutationBaseline | None,
    terminal_git: RunGitInfo | None,
    local_pre_head: str | None,
    cloud_provider: CloudRemoteGitAttributionProvider | None = None,
    local_provider: LocalWorktreeAttributionProvider | None = None,
) -> list[str] | None:
    """Dispatch attribution by runtime. CLOUD never falls back to local root."""
    if runtime == AgentRuntime.LOCAL:
        local_impl = local_provider or LocalWorktreeAttributionProvider()
        baseline = attribution or RunMutationBaseline(
            run_id="local",
            agent_id="local",
            runtime=AgentRuntime.LOCAL,
            base_main=local_pre_head or "0" * 40,
            dag_generation=0,
        )
        return local_impl.collect_changed_paths(
            root=root,
            attribution=baseline,
            terminal_git=terminal_git,
            local_pre_head=local_pre_head,
        )
    if runtime == AgentRuntime.CLOUD:
        if attribution is None:
            raise SdkRuntimeError(
                "missing CLOUD run mutation baseline",
                code="REMOTE_ATTRIBUTION_UNDETERMINED",
            )
        if attribution.runtime != AgentRuntime.CLOUD:
            raise SdkRuntimeError(
                "attribution runtime mismatch",
                code="REMOTE_ATTRIBUTION_UNDETERMINED",
            )
        cloud_impl = cloud_provider or CloudRemoteGitAttributionProvider()
        return cloud_impl.collect_changed_paths(
            root=root,
            attribution=attribution,
            terminal_git=terminal_git,
            local_pre_head=None,
        )
    raise SdkRuntimeError(
        f"unsupported attribution runtime {runtime}",
        code="REMOTE_ATTRIBUTION_UNDETERMINED",
    )


def mint_cloud_run_baseline(
    *,
    root: Path,
    run_id: str,
    agent_id: str,
    base_main: str,
    branch: str | None,
    dag_generation: int,
    lease_id: str | None,
    package_id: str = PACKAGE_ID,
    repository: str = CANONICAL_REPO_URL,
) -> RunMutationBaseline:
    """First Cloud run baselines at base_main; follow-ups chain prior post head.

    ORCH-SDK-CLOUD-DISCONNECT-HW-GAP-001: refuse silent re-baseline to base_main
    when prior remote high-water is undetermined or a prior CLOUD baseline never
    recorded remote_post_head.
    """
    prior = load_agent_remote_high_water(root, agent_id)
    if agent_remote_high_water_is_undetermined(prior):
        raise SdkRuntimeError(
            "prior CLOUD remote high-water undetermined; refuse silent rebase to base_main",
            code="REMOTE_ATTRIBUTION_UNDETERMINED",
        )
    if prior is None and _agent_has_incomplete_cloud_baseline(root, agent_id):
        raise SdkRuntimeError(
            "incomplete prior CLOUD remote post; refuse silent rebase to base_main",
            code="REMOTE_ATTRIBUTION_UNDETERMINED",
        )
    remote_pre = prior if prior else base_main
    if not remote_pre or len(remote_pre) < 7:
        raise SdkRuntimeError(
            "invalid CLOUD remote baseline",
            code="REMOTE_ATTRIBUTION_UNDETERMINED",
        )
    auto_pre = list_remote_auto_branches(repository)
    baseline = RunMutationBaseline(
        run_id=run_id,
        agent_id=agent_id,
        runtime=AgentRuntime.CLOUD,
        repository=repository,
        base_main=base_main,
        remote_branch=branch,
        remote_pre_head=remote_pre,
        remote_post_head=None,
        remote_auto_branches_pre=auto_pre,
        dag_generation=dag_generation,
        lease_id=lease_id,
        package_id=package_id,
    )
    persist_run_mutation_baseline(root, baseline)
    return baseline
