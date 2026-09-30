"""D-116 ADV matrix: CLOUD remote path attribution (no local fallback)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from project_atlas.orchestration.sdk.backend import CursorSDKExecutionBackend
from project_atlas.orchestration.sdk.lease_registry import persist_durable_lease
from project_atlas.orchestration.sdk.models import (
    CANONICAL_REPO_URL,
    PACKAGE_ID,
    AgentRecord,
    AgentRole,
    AgentRuntime,
    AgentState,
    RunRecord,
    RunStatus,
    SdkRuntimeError,
    _utc_now,
)
from project_atlas.orchestration.sdk.mutation_attribution import (
    CloudRemoteGitAttributionProvider,
    load_agent_remote_high_water,
    load_run_mutation_baseline,
    mint_cloud_run_baseline,
    normalize_repo_identity,
    persist_agent_remote_high_water,
)
from project_atlas.orchestration.sdk.registries import CloudAgentRegistry, RunRegistry
from project_atlas.orchestration.sdk.role_pool import AgentRolePool
from project_atlas.orchestration.sdk.security_gates import (
    CANONICAL_BRANCH,
    GovernorLease,
    WorkerBackend,
    persist_run_pre_head,
)

PIN = "7e797468a2eca37c959920912b1fa264df4be638"
H1 = "1111111111111111111111111111111111111111"
H2 = "2222222222222222222222222222222222222222"
H3 = "3333333333333333333333333333333333333333"
LEASE_ID = "lease-d116-sdk-test-01"
AGENT = "bc-d116cloudagent0001"
BRANCH = CANONICAL_BRANCH


def _backend(root: Path) -> CursorSDKExecutionBackend:
    return CursorSDKExecutionBackend(
        root=root,
        agents_reg=CloudAgentRegistry(root),
        runs_reg=RunRegistry(root),
        pool=AgentRolePool(CloudAgentRegistry(root)),
    )


def _lease(root: Path) -> None:
    persist_durable_lease(
        root,
        GovernorLease(
            lease_id=LEASE_ID,
            package_id=PACKAGE_ID,
            role=AgentRole.REMEDIATOR,
            dag_generation=116,
            allowed_paths=(
                "src/project_atlas/orchestration/sdk",
                "tests/unit",
                "scripts",
            ),
            worktree=str(root),
            candidate_head=PIN,
            candidate_tree="0" * 40,
            mutation_authorized=True,
            branch=BRANCH,
        ),
    )


def _agent(root: Path) -> AgentRecord:
    return AgentRecord(
        agent_id=AGENT,
        runtime=AgentRuntime.CLOUD,
        role=AgentRole.REMEDIATOR,
        package_id=PACKAGE_ID,
        base_main=PIN,
        branch=BRANCH,
        created_at=_utc_now(),
        state=AgentState.BUSY,
        worker_backend=WorkerBackend.CURSOR_SDK.value,
        workspace=str(root.resolve()),
        repository=CANONICAL_REPO_URL,
        creation_generation=116,
        creation_sequence=1,
    )


def _run(run_id: str) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        agent_id=AGENT,
        cycle_id="C-D116",
        package_id=PACKAGE_ID,
        lease_id=LEASE_ID,
        role=AgentRole.REMEDIATOR,
        prompt_digest="a" * 64,
        idempotency_key=f"idemp-{run_id}",
        status=RunStatus.RUNNING,
        started_at=_utc_now(),
        candidate_head=PIN,
        candidate_tree="0" * 40,
        node_id="REMEDIATE-D116",
        dag_generation=116,
    )


class _GitWaitHandle:
    def __init__(self, *, repo: str, branches: list[str]) -> None:
        self._repo = repo
        self._branches = branches

    async def wait(self) -> SimpleNamespace:
        return SimpleNamespace(
            status="finished",
            result="ok",
            usage=None,
            git=SimpleNamespace(repo_url=self._repo, branches=list(self._branches)),
        )


def _provider(
    *,
    heads: dict[str, str],
    diffs: dict[tuple[str, str], list[str]],
) -> CloudRemoteGitAttributionProvider:
    def resolve_head(repository: str, branch: str) -> str | None:
        del repository
        return heads.get(branch)

    def resolve_diff(repository: str, pre: str, post: str) -> list[str] | None:
        del repository
        return diffs.get((pre, post))

    return CloudRemoteGitAttributionProvider(
        resolve_remote_head=resolve_head,
        resolve_remote_diff=resolve_diff,
    )


def test_normalize_repo_identity_strips_scheme() -> None:
    assert normalize_repo_identity("github.com/B0LK13/project-atlas") == normalize_repo_identity(
        CANONICAL_REPO_URL
    )


def test_cloud_attr_iv_allowed_remote_mutation(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    _lease(tmp_path)
    backend.agents_reg.upsert(_agent(tmp_path))
    mint_cloud_run_baseline(
        root=tmp_path,
        run_id="run-iv",
        agent_id=AGENT,
        base_main=PIN,
        branch=BRANCH,
        dag_generation=116,
        lease_id=LEASE_ID,
    )
    backend.register_cloud_attribution_provider(
        _provider(
            heads={BRANCH: H1},
            diffs={(PIN, H1): ["src/project_atlas/orchestration/sdk/backend.py"]},
        )
    )
    backend.runs_reg.upsert(_run("run-iv"))
    backend._handles["run:run-iv"] = _GitWaitHandle(
        repo="github.com/B0LK13/project-atlas", branches=[BRANCH]
    )
    updated = asyncio.run(backend.wait_run("run-iv", agent_id=AGENT))
    assert updated.status == RunStatus.FINISHED
    assert load_agent_remote_high_water(tmp_path, AGENT) == H1


def test_cloud_attr_escape_rejects_before_terminal(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    _lease(tmp_path)
    backend.agents_reg.upsert(_agent(tmp_path))
    mint_cloud_run_baseline(
        root=tmp_path,
        run_id="run-escape",
        agent_id=AGENT,
        base_main=PIN,
        branch=BRANCH,
        dag_generation=116,
        lease_id=LEASE_ID,
    )
    backend.register_cloud_attribution_provider(
        _provider(heads={BRANCH: H1}, diffs={(PIN, H1): ["docs/evil.md"]})
    )
    backend.runs_reg.upsert(_run("run-escape"))
    backend._handles["run:run-escape"] = _GitWaitHandle(repo=CANONICAL_REPO_URL, branches=[BRANCH])
    with pytest.raises(SdkRuntimeError) as exc:
        asyncio.run(backend.wait_run("run-escape", agent_id=AGENT))
    assert exc.value.code == "REJECTED_SCOPE_ESCAPE"
    stored = backend.runs_reg.get("run-escape")
    assert stored is not None
    assert stored.status == RunStatus.RUNNING


def test_cloud_attr_local_decoy_ignored(tmp_path: Path) -> None:
    """Local allowed decoy must not mask a forbidden remote Cloud mutation."""
    backend = _backend(tmp_path)
    _lease(tmp_path)
    backend.agents_reg.upsert(_agent(tmp_path))
    mint_cloud_run_baseline(
        root=tmp_path,
        run_id="run-decoy",
        agent_id=AGENT,
        base_main=PIN,
        branch=BRANCH,
        dag_generation=116,
        lease_id=LEASE_ID,
    )
    # Local decoy: only allowed paths dirty in worktree.
    allowed = tmp_path / "src" / "project_atlas" / "orchestration" / "sdk"
    allowed.mkdir(parents=True)
    (allowed / "ok.py").write_text("ok\n", encoding="utf-8")
    persist_run_pre_head(tmp_path, "run-decoy", PIN)
    backend.register_cloud_attribution_provider(
        _provider(heads={BRANCH: H1}, diffs={(PIN, H1): ["docs/evil.md"]})
    )
    backend.runs_reg.upsert(_run("run-decoy"))
    backend._handles["run:run-decoy"] = _GitWaitHandle(repo=CANONICAL_REPO_URL, branches=[BRANCH])
    with pytest.raises(SdkRuntimeError) as exc:
        asyncio.run(backend.wait_run("run-decoy", agent_id=AGENT))
    assert exc.value.code == "REJECTED_SCOPE_ESCAPE"


def test_cloud_attr_followup_chains_baselines(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    _lease(tmp_path)
    backend.agents_reg.upsert(_agent(tmp_path))
    mint_cloud_run_baseline(
        root=tmp_path,
        run_id="run-1",
        agent_id=AGENT,
        base_main=PIN,
        branch=BRANCH,
        dag_generation=116,
        lease_id=LEASE_ID,
    )
    backend.register_cloud_attribution_provider(
        _provider(
            heads={BRANCH: H1},
            diffs={(PIN, H1): ["src/project_atlas/orchestration/sdk/a.py"]},
        )
    )
    backend.runs_reg.upsert(_run("run-1"))
    backend._handles["run:run-1"] = _GitWaitHandle(repo=CANONICAL_REPO_URL, branches=[BRANCH])
    asyncio.run(backend.wait_run("run-1", agent_id=AGENT))
    assert load_agent_remote_high_water(tmp_path, AGENT) == H1

    b2 = mint_cloud_run_baseline(
        root=tmp_path,
        run_id="run-2",
        agent_id=AGENT,
        base_main=PIN,
        branch=BRANCH,
        dag_generation=116,
        lease_id=LEASE_ID,
    )
    assert b2.remote_pre_head == H1
    seen: dict[str, object] = {}

    def resolve_diff(repository: str, pre: str, post: str) -> list[str] | None:
        del repository
        seen["pre"] = pre
        seen["post"] = post
        return ["src/project_atlas/orchestration/sdk/b.py"]

    backend.register_cloud_attribution_provider(
        CloudRemoteGitAttributionProvider(
            resolve_remote_head=lambda _r, _b: H2,
            resolve_remote_diff=resolve_diff,
        )
    )
    backend.runs_reg.upsert(_run("run-2"))
    backend._handles["run:run-2"] = _GitWaitHandle(repo=CANONICAL_REPO_URL, branches=[BRANCH])
    asyncio.run(backend.wait_run("run-2", agent_id=AGENT))
    assert seen["pre"] == H1
    assert seen["post"] == H2
    assert load_agent_remote_high_water(tmp_path, AGENT) == H2


def test_cloud_attr_restart_recovers_baseline(tmp_path: Path) -> None:
    mint_cloud_run_baseline(
        root=tmp_path,
        run_id="run-restart",
        agent_id=AGENT,
        base_main=PIN,
        branch=BRANCH,
        dag_generation=116,
        lease_id=LEASE_ID,
    )
    loaded = load_run_mutation_baseline(tmp_path, "run-restart")
    assert loaded is not None
    assert loaded.remote_pre_head == PIN
    persist_agent_remote_high_water(tmp_path, AGENT, H1)
    again = mint_cloud_run_baseline(
        root=tmp_path,
        run_id="run-restart-2",
        agent_id=AGENT,
        base_main=PIN,
        branch=BRANCH,
        dag_generation=116,
        lease_id=LEASE_ID,
    )
    assert again.remote_pre_head == H1


def test_cloud_attr_foreign_repo_fail_closed(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    _lease(tmp_path)
    backend.agents_reg.upsert(_agent(tmp_path))
    mint_cloud_run_baseline(
        root=tmp_path,
        run_id="run-foreign",
        agent_id=AGENT,
        base_main=PIN,
        branch=BRANCH,
        dag_generation=116,
        lease_id=LEASE_ID,
    )
    backend.register_cloud_attribution_provider(
        _provider(
            heads={BRANCH: H1},
            diffs={(PIN, H1): ["src/project_atlas/orchestration/sdk/x.py"]},
        )
    )
    backend.runs_reg.upsert(_run("run-foreign"))
    backend._handles["run:run-foreign"] = _GitWaitHandle(
        repo="https://github.com/evil/other", branches=[BRANCH]
    )
    with pytest.raises(SdkRuntimeError) as exc:
        asyncio.run(backend.wait_run("run-foreign", agent_id=AGENT))
    assert exc.value.code == "REMOTE_ATTRIBUTION_UNDETERMINED"


def test_cloud_attr_ambiguous_branch_fail_closed(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    _lease(tmp_path)
    backend.agents_reg.upsert(_agent(tmp_path))
    mint_cloud_run_baseline(
        root=tmp_path,
        run_id="run-ambig",
        agent_id=AGENT,
        base_main=PIN,
        branch=BRANCH,
        dag_generation=116,
        lease_id=LEASE_ID,
    )
    backend.register_cloud_attribution_provider(
        _provider(heads={BRANCH: H1}, diffs={(PIN, H1): []})
    )
    backend.runs_reg.upsert(_run("run-ambig"))
    backend._handles["run:run-ambig"] = _GitWaitHandle(
        repo=CANONICAL_REPO_URL, branches=[BRANCH, "feat/other-atlas-branch"]
    )
    with pytest.raises(SdkRuntimeError) as exc:
        asyncio.run(backend.wait_run("run-ambig", agent_id=AGENT))
    assert exc.value.code == "REMOTE_ATTRIBUTION_UNDETERMINED"


def test_cloud_attr_divergence_fail_closed(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    _lease(tmp_path)
    backend.agents_reg.upsert(_agent(tmp_path))
    mint_cloud_run_baseline(
        root=tmp_path,
        run_id="run-div",
        agent_id=AGENT,
        base_main=PIN,
        branch=BRANCH,
        dag_generation=116,
        lease_id=LEASE_ID,
    )

    def resolve_diff(repository: str, pre: str, post: str) -> list[str] | None:
        del repository, pre, post
        raise SdkRuntimeError(
            "remote terminal head is not a descendant of baseline",
            code="REMOTE_ATTRIBUTION_UNDETERMINED",
        )

    backend.register_cloud_attribution_provider(
        CloudRemoteGitAttributionProvider(
            resolve_remote_head=lambda _r, _b: H3,
            resolve_remote_diff=resolve_diff,
        )
    )
    backend.runs_reg.upsert(_run("run-div"))
    backend._handles["run:run-div"] = _GitWaitHandle(repo=CANONICAL_REPO_URL, branches=[BRANCH])
    with pytest.raises(SdkRuntimeError) as exc:
        asyncio.run(backend.wait_run("run-div", agent_id=AGENT))
    assert exc.value.code == "REMOTE_ATTRIBUTION_UNDETERMINED"


def test_cloud_attr_rename_delete_included(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    _lease(tmp_path)
    backend.agents_reg.upsert(_agent(tmp_path))
    mint_cloud_run_baseline(
        root=tmp_path,
        run_id="run-rename",
        agent_id=AGENT,
        base_main=PIN,
        branch=BRANCH,
        dag_generation=116,
        lease_id=LEASE_ID,
    )
    backend.register_cloud_attribution_provider(
        _provider(
            heads={BRANCH: H1},
            diffs={
                (PIN, H1): [
                    "src/project_atlas/orchestration/sdk/old.py",
                    "src/project_atlas/orchestration/sdk/new.py",
                ]
            },
        )
    )
    backend.runs_reg.upsert(_run("run-rename"))
    backend._handles["run:run-rename"] = _GitWaitHandle(repo=CANONICAL_REPO_URL, branches=[BRANCH])
    updated = asyncio.run(backend.wait_run("run-rename", agent_id=AGENT))
    assert updated.status == RunStatus.FINISHED


def test_cloud_attr_missing_git_fail_closed(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    _lease(tmp_path)
    backend.agents_reg.upsert(_agent(tmp_path))
    mint_cloud_run_baseline(
        root=tmp_path,
        run_id="run-nogit",
        agent_id=AGENT,
        base_main=PIN,
        branch=BRANCH,
        dag_generation=116,
        lease_id=LEASE_ID,
    )
    backend.register_cloud_attribution_provider(
        _provider(heads={BRANCH: H1}, diffs={(PIN, H1): []})
    )
    backend.runs_reg.upsert(_run("run-nogit"))

    class _NoGit:
        async def wait(self) -> SimpleNamespace:
            return SimpleNamespace(status="finished", result="ok", usage=None)

    backend._handles["run:run-nogit"] = _NoGit()
    with pytest.raises(SdkRuntimeError) as exc:
        asyncio.run(backend.wait_run("run-nogit", agent_id=AGENT))
    assert exc.value.code == "REMOTE_ATTRIBUTION_UNDETERMINED"


# --- G107 ADV matrix expansion (foreign host, recovery, TOCTOU, HW gap) ---

FOREIGN_URLS = [
    "https://evil.com/github.com/B0LK13/project-atlas",
    "evil.com/B0LK13/project-atlas",
    "https://gitlab.com/B0LK13/project-atlas",
    "notgithub.com/B0LK13/project-atlas",
    "https://github.evil.com/B0LK13/project-atlas",
    "https://github.com.evil/B0LK13/project-atlas",
]


@pytest.mark.parametrize("url", FOREIGN_URLS)
def test_normalize_rejects_foreign_host_urls(url: str) -> None:
    assert normalize_repo_identity(url) != normalize_repo_identity(CANONICAL_REPO_URL)


def test_normalize_accepts_ssh_and_git_suffix() -> None:
    assert normalize_repo_identity(
        "git@github.com:B0LK13/project-atlas.git"
    ) == normalize_repo_identity(CANONICAL_REPO_URL)


def test_normalize_accepts_bare_owner_repo() -> None:
    assert normalize_repo_identity("B0LK13/project-atlas") == normalize_repo_identity(
        CANONICAL_REPO_URL
    )


def test_normalize_rejects_embedded_github_suffix() -> None:
    spoofed = normalize_repo_identity("https://evil.com/github.com/B0LK13/project-atlas")
    assert spoofed != normalize_repo_identity(CANONICAL_REPO_URL)


@pytest.mark.parametrize("url", FOREIGN_URLS)
def test_select_canonical_branch_rejects_foreign(url: str) -> None:
    from project_atlas.orchestration.sdk.mutation_attribution import (
        RunGitInfo,
        select_canonical_remote_branch,
    )

    with pytest.raises(SdkRuntimeError) as exc:
        select_canonical_remote_branch(
            RunGitInfo(repo_url=url, branches=(BRANCH,)),
            expected_branch=BRANCH,
        )
    assert exc.value.code == "REMOTE_ATTRIBUTION_UNDETERMINED"


def test_e2e_gitlab_spoof_fail_closed(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    _lease(tmp_path)
    backend.agents_reg.upsert(_agent(tmp_path))
    mint_cloud_run_baseline(
        root=tmp_path,
        run_id="run-gitlab-spoof",
        agent_id=AGENT,
        base_main=PIN,
        branch=BRANCH,
        dag_generation=116,
        lease_id=LEASE_ID,
    )
    backend.register_cloud_attribution_provider(
        _provider(
            heads={BRANCH: H1},
            diffs={(PIN, H1): ["src/project_atlas/orchestration/sdk/ok.py"]},
        )
    )
    backend.runs_reg.upsert(_run("run-gitlab-spoof"))
    backend._handles["run:run-gitlab-spoof"] = _GitWaitHandle(
        repo="https://gitlab.com/B0LK13/project-atlas",
        branches=[BRANCH],
    )
    with pytest.raises(SdkRuntimeError) as exc:
        asyncio.run(backend.wait_run("run-gitlab-spoof", agent_id=AGENT))
    assert exc.value.code == "REMOTE_ATTRIBUTION_UNDETERMINED"
    assert backend.runs_reg.get("run-gitlab-spoof").status == RunStatus.RUNNING  # type: ignore[union-attr]


def test_e2e_evil_embedded_github_fail_closed(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    _lease(tmp_path)
    backend.agents_reg.upsert(_agent(tmp_path))
    mint_cloud_run_baseline(
        root=tmp_path,
        run_id="run-embed",
        agent_id=AGENT,
        base_main=PIN,
        branch=BRANCH,
        dag_generation=116,
        lease_id=LEASE_ID,
    )
    backend.register_cloud_attribution_provider(
        _provider(
            heads={BRANCH: H1},
            diffs={(PIN, H1): ["src/project_atlas/orchestration/sdk/ok.py"]},
        )
    )
    backend.runs_reg.upsert(_run("run-embed"))
    backend._handles["run:run-embed"] = _GitWaitHandle(
        repo="https://evil.com/github.com/B0LK13/project-atlas",
        branches=[BRANCH],
    )
    with pytest.raises(SdkRuntimeError) as exc:
        asyncio.run(backend.wait_run("run-embed", agent_id=AGENT))
    assert exc.value.code == "REMOTE_ATTRIBUTION_UNDETERMINED"


def test_branch_switch_fail_closed(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    _lease(tmp_path)
    backend.agents_reg.upsert(_agent(tmp_path))
    mint_cloud_run_baseline(
        root=tmp_path,
        run_id="run-switch",
        agent_id=AGENT,
        base_main=PIN,
        branch=BRANCH,
        dag_generation=116,
        lease_id=LEASE_ID,
    )
    backend.register_cloud_attribution_provider(
        _provider(heads={BRANCH: H1, "other": H1}, diffs={(PIN, H1): []})
    )
    backend.runs_reg.upsert(_run("run-switch"))
    backend._handles["run:run-switch"] = _GitWaitHandle(repo=CANONICAL_REPO_URL, branches=["other"])
    with pytest.raises(SdkRuntimeError) as exc:
        asyncio.run(backend.wait_run("run-switch", agent_id=AGENT))
    assert exc.value.code == "REMOTE_ATTRIBUTION_UNDETERMINED"


def test_missing_baseline_fail_closed(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    _lease(tmp_path)
    backend.agents_reg.upsert(_agent(tmp_path))
    backend.register_cloud_attribution_provider(
        _provider(heads={BRANCH: H1}, diffs={(PIN, H1): []})
    )
    backend.runs_reg.upsert(_run("run-nobase"))
    backend._handles["run:run-nobase"] = _GitWaitHandle(repo=CANONICAL_REPO_URL, branches=[BRANCH])
    with pytest.raises(SdkRuntimeError) as exc:
        asyncio.run(backend.wait_run("run-nobase", agent_id=AGENT))
    assert exc.value.code == "REMOTE_ATTRIBUTION_UNDETERMINED"


def test_corrupt_baseline_store_fail_closed(tmp_path: Path) -> None:
    from project_atlas.orchestration.sdk.mutation_attribution import attribution_store_path

    path = attribution_store_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(SdkRuntimeError) as exc:
        load_run_mutation_baseline(tmp_path, "run-x")
    assert exc.value.code == "REMOTE_ATTRIBUTION_UNDETERMINED"


def test_rename_outside_lease_rejected(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    _lease(tmp_path)
    backend.agents_reg.upsert(_agent(tmp_path))
    mint_cloud_run_baseline(
        root=tmp_path,
        run_id="run-ren-out",
        agent_id=AGENT,
        base_main=PIN,
        branch=BRANCH,
        dag_generation=116,
        lease_id=LEASE_ID,
    )
    backend.register_cloud_attribution_provider(
        _provider(
            heads={BRANCH: H1},
            diffs={(PIN, H1): ["docs/secret.md", "README.md"]},
        )
    )
    backend.runs_reg.upsert(_run("run-ren-out"))
    backend._handles["run:run-ren-out"] = _GitWaitHandle(repo=CANONICAL_REPO_URL, branches=[BRANCH])
    with pytest.raises(SdkRuntimeError) as exc:
        asyncio.run(backend.wait_run("run-ren-out", agent_id=AGENT))
    assert exc.value.code == "REJECTED_SCOPE_ESCAPE"
    assert backend.runs_reg.get("run-ren-out").status == RunStatus.RUNNING  # type: ignore[union-attr]


def test_delete_outside_lease_rejected(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    _lease(tmp_path)
    backend.agents_reg.upsert(_agent(tmp_path))
    mint_cloud_run_baseline(
        root=tmp_path,
        run_id="run-del-out",
        agent_id=AGENT,
        base_main=PIN,
        branch=BRANCH,
        dag_generation=116,
        lease_id=LEASE_ID,
    )
    backend.register_cloud_attribution_provider(
        _provider(
            heads={BRANCH: H1},
            diffs={(PIN, H1): [".github/workflows/ci.yml"]},
        )
    )
    backend.runs_reg.upsert(_run("run-del-out"))
    backend._handles["run:run-del-out"] = _GitWaitHandle(repo=CANONICAL_REPO_URL, branches=[BRANCH])
    with pytest.raises(SdkRuntimeError) as exc:
        asyncio.run(backend.wait_run("run-del-out", agent_id=AGENT))
    assert exc.value.code == "REJECTED_SCOPE_ESCAPE"


def test_cloud_never_dispatches_local_provider(tmp_path: Path) -> None:
    from project_atlas.orchestration.sdk.mutation_attribution import (
        RunGitInfo,
        RunMutationBaseline,
        collect_run_changed_paths,
    )

    local = tmp_path / "src" / "project_atlas" / "orchestration" / "sdk"
    local.mkdir(parents=True)
    (local / "local_only.py").write_text("x\n", encoding="utf-8")
    baseline = RunMutationBaseline(
        run_id="r",
        agent_id=AGENT,
        runtime=AgentRuntime.CLOUD,
        base_main=PIN,
        remote_branch=BRANCH,
        remote_pre_head=PIN,
        dag_generation=116,
        lease_id=LEASE_ID,
    )
    cloud = CloudRemoteGitAttributionProvider(
        resolve_remote_head=lambda _r, _b: H1,
        resolve_remote_diff=lambda _r, _pre, _post: ["docs/evil.md"],
    )
    paths = collect_run_changed_paths(
        tmp_path,
        runtime=AgentRuntime.CLOUD,
        attribution=baseline,
        terminal_git=RunGitInfo(repo_url=CANONICAL_REPO_URL, branches=(BRANCH,)),
        local_pre_head=PIN,
        cloud_provider=cloud,
    )
    assert paths == ["docs/evil.md"]


def test_run_scoped_sha_preferred_over_branch_tip(tmp_path: Path) -> None:
    """ORCH-SDK-CLOUD-POST-HEAD-BRANCH-TIP-TOCTOU-001: prefer SDK head_sha."""
    from project_atlas.orchestration.sdk.mutation_attribution import (
        RunGitInfo,
        RunMutationBaseline,
        collect_run_changed_paths,
    )

    tip_calls: list[tuple[str, str]] = []

    def resolve_head(repository: str, branch: str) -> str | None:
        tip_calls.append((repository, branch))
        return H2  # stale tip that must NOT win over run-scoped SHA

    baseline = RunMutationBaseline(
        run_id="run-sha",
        agent_id=AGENT,
        runtime=AgentRuntime.CLOUD,
        base_main=PIN,
        remote_branch=BRANCH,
        remote_pre_head=PIN,
        dag_generation=116,
        lease_id=LEASE_ID,
    )
    cloud = CloudRemoteGitAttributionProvider(
        resolve_remote_head=resolve_head,
        resolve_remote_diff=lambda _r, pre, post: (
            ["src/project_atlas/orchestration/sdk/x.py"] if (pre, post) == (PIN, H1) else None
        ),
    )
    paths = collect_run_changed_paths(
        tmp_path,
        runtime=AgentRuntime.CLOUD,
        attribution=baseline,
        terminal_git=RunGitInfo(repo_url=CANONICAL_REPO_URL, branches=(BRANCH,), head_sha=H1),
        local_pre_head=None,
        cloud_provider=cloud,
    )
    assert paths == ["src/project_atlas/orchestration/sdk/x.py"]
    assert tip_calls == []
    assert baseline.remote_post_head == H1


def test_branch_tip_used_when_run_scoped_sha_absent(tmp_path: Path) -> None:
    from project_atlas.orchestration.sdk.mutation_attribution import (
        RunGitInfo,
        RunMutationBaseline,
        collect_run_changed_paths,
    )

    baseline = RunMutationBaseline(
        run_id="run-tip",
        agent_id=AGENT,
        runtime=AgentRuntime.CLOUD,
        base_main=PIN,
        remote_branch=BRANCH,
        remote_pre_head=PIN,
        dag_generation=116,
        lease_id=LEASE_ID,
    )
    cloud = CloudRemoteGitAttributionProvider(
        resolve_remote_head=lambda _r, _b: H1,
        resolve_remote_diff=lambda _r, _pre, _post: ["src/project_atlas/orchestration/sdk/x.py"],
    )
    paths = collect_run_changed_paths(
        tmp_path,
        runtime=AgentRuntime.CLOUD,
        attribution=baseline,
        terminal_git=RunGitInfo(repo_url=CANONICAL_REPO_URL, branches=(BRANCH,)),
        local_pre_head=None,
        cloud_provider=cloud,
    )
    assert paths is not None
    assert baseline.remote_post_head == H1


def test_mint_refuses_undetermined_high_water(tmp_path: Path) -> None:
    from project_atlas.orchestration.sdk.mutation_attribution import (
        mark_agent_remote_high_water_undetermined,
    )

    mark_agent_remote_high_water_undetermined(tmp_path, AGENT)
    with pytest.raises(SdkRuntimeError) as exc:
        mint_cloud_run_baseline(
            root=tmp_path,
            run_id="run-undetermined",
            agent_id=AGENT,
            base_main=PIN,
            branch=BRANCH,
            dag_generation=116,
            lease_id=LEASE_ID,
        )
    assert exc.value.code == "REMOTE_ATTRIBUTION_UNDETERMINED"


def test_mint_refuses_incomplete_prior_baseline_without_hw(tmp_path: Path) -> None:
    mint_cloud_run_baseline(
        root=tmp_path,
        run_id="run-incomplete",
        agent_id=AGENT,
        base_main=PIN,
        branch=BRANCH,
        dag_generation=116,
        lease_id=LEASE_ID,
    )
    with pytest.raises(SdkRuntimeError) as exc:
        mint_cloud_run_baseline(
            root=tmp_path,
            run_id="run-follow-incomplete",
            agent_id=AGENT,
            base_main=PIN,
            branch=BRANCH,
            dag_generation=116,
            lease_id=LEASE_ID,
        )
    assert exc.value.code == "REMOTE_ATTRIBUTION_UNDETERMINED"


def test_recovery_cannot_bypass_cloud_attribution(tmp_path: Path) -> None:
    """ORCH-SDK-CLOUD-RECOVERY-ATTRIBUTION-BYPASS-001."""
    from project_atlas.orchestration.sdk.mutation_attribution import (
        REMOTE_HIGH_WATER_UNDETERMINED,
        load_agent_remote_high_water,
    )
    from project_atlas.orchestration.sdk.recovery import recover_runtime

    backend = _backend(tmp_path)
    _lease(tmp_path)
    backend.agents_reg.upsert(_agent(tmp_path))
    mint_cloud_run_baseline(
        root=tmp_path,
        run_id="run-rec-bypass",
        agent_id=AGENT,
        base_main=PIN,
        branch=BRANCH,
        dag_generation=116,
        lease_id=LEASE_ID,
    )
    backend.register_cloud_attribution_provider(
        _provider(heads={BRANCH: H1}, diffs={(PIN, H1): ["docs/evil.md"]})
    )
    backend.runs_reg.upsert(_run("run-rec-bypass"))
    backend._handles["run:run-rec-bypass"] = _GitWaitHandle(
        repo=CANONICAL_REPO_URL, branches=[BRANCH]
    )

    class _StatusBackend:
        """Wrap so get_run_status reports FINISHED while wait_run still enforces."""

        def __init__(self, inner: CursorSDKExecutionBackend) -> None:
            self._inner = inner
            self.root = inner.root

        async def resume_agent(self, agent_id: str) -> AgentRecord:
            stored = self._inner.agents_reg.get(agent_id)
            if stored is None:
                raise SdkRuntimeError("unknown agent", code="UNKNOWN_AGENT")
            return stored

        async def get_run_status(self, run_id: str, *, agent_id: str) -> RunStatus:
            del run_id, agent_id
            return RunStatus.FINISHED

        async def wait_run(self, run_id: str, *, agent_id: str) -> RunRecord:
            return await self._inner.wait_run(run_id, agent_id=agent_id)

    report = asyncio.run(
        recover_runtime(
            backend=_StatusBackend(backend),  # type: ignore[arg-type]
            agents=backend.agents_reg,
            runs=backend.runs_reg,
            root=tmp_path,
        )
    )
    stored = backend.runs_reg.get("run-rec-bypass")
    assert stored is not None
    assert stored.status == RunStatus.RUNNING
    assert "run-rec-bypass" not in report.ingested_runs
    assert "run-rec-bypass" in report.still_active_runs
    assert report.safety_stop is True
    assert load_agent_remote_high_water(tmp_path, AGENT) == REMOTE_HIGH_WATER_UNDETERMINED


def test_recovery_preserves_nonterminal_on_wait_exception(tmp_path: Path) -> None:
    from project_atlas.orchestration.sdk.recovery import recover_runtime

    backend = _backend(tmp_path)
    _lease(tmp_path)
    backend.agents_reg.upsert(_agent(tmp_path))
    backend.runs_reg.upsert(_run("run-rec-exc"))

    class _Boom:
        root = tmp_path

        async def resume_agent(self, agent_id: str) -> AgentRecord:
            del agent_id
            return _agent(tmp_path)

        async def get_run_status(self, run_id: str, *, agent_id: str) -> RunStatus:
            del run_id, agent_id
            return RunStatus.FINISHED

        async def wait_run(self, run_id: str, *, agent_id: str) -> RunRecord:
            del run_id, agent_id
            raise SdkRuntimeError("attribution failed", code="REMOTE_ATTRIBUTION_UNDETERMINED")

    report = asyncio.run(
        recover_runtime(
            backend=_Boom(),  # type: ignore[arg-type]
            agents=backend.agents_reg,
            runs=backend.runs_reg,
            root=tmp_path,
        )
    )
    stored = backend.runs_reg.get("run-rec-exc")
    assert stored is not None
    assert stored.status == RunStatus.RUNNING
    assert report.ingested_runs == []
    assert report.safety_stop is True


def test_hw_only_after_successful_enforce(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    _lease(tmp_path)
    backend.agents_reg.upsert(_agent(tmp_path))
    mint_cloud_run_baseline(
        root=tmp_path,
        run_id="run-hw-gap",
        agent_id=AGENT,
        base_main=PIN,
        branch=BRANCH,
        dag_generation=116,
        lease_id=LEASE_ID,
    )
    backend.register_cloud_attribution_provider(
        _provider(heads={BRANCH: H1}, diffs={(PIN, H1): ["docs/evil.md"]})
    )
    backend.runs_reg.upsert(_run("run-hw-gap"))
    backend._handles["run:run-hw-gap"] = _GitWaitHandle(repo=CANONICAL_REPO_URL, branches=[BRANCH])
    with pytest.raises(SdkRuntimeError):
        asyncio.run(backend.wait_run("run-hw-gap", agent_id=AGENT))
    assert load_agent_remote_high_water(tmp_path, AGENT) is None


def test_extract_run_git_reads_branch_repo_url() -> None:
    from project_atlas.orchestration.sdk.mutation_attribution import extract_run_git

    result = SimpleNamespace(
        git=SimpleNamespace(
            branches=[
                SimpleNamespace(
                    repo_url="github.com/B0LK13/project-atlas",
                    branch=BRANCH,
                    head_sha=H1,
                )
            ]
        )
    )
    info = extract_run_git(result)
    assert info is not None
    assert normalize_repo_identity(info.repo_url) == normalize_repo_identity(CANONICAL_REPO_URL)
    assert BRANCH in info.branches
    assert info.head_sha == H1


def test_escape_leaves_status_running_ordering(tmp_path: Path) -> None:
    """D115 ordering: REJECTED_SCOPE_ESCAPE before durable terminal."""
    backend = _backend(tmp_path)
    _lease(tmp_path)
    backend.agents_reg.upsert(_agent(tmp_path))
    mint_cloud_run_baseline(
        root=tmp_path,
        run_id="run-order",
        agent_id=AGENT,
        base_main=PIN,
        branch=BRANCH,
        dag_generation=116,
        lease_id=LEASE_ID,
    )
    backend.register_cloud_attribution_provider(
        _provider(heads={BRANCH: H1}, diffs={(PIN, H1): ["secrets/key.pem"]})
    )
    backend.runs_reg.upsert(_run("run-order"))
    backend._handles["run:run-order"] = _GitWaitHandle(repo=CANONICAL_REPO_URL, branches=[BRANCH])
    with pytest.raises(SdkRuntimeError) as exc:
        asyncio.run(backend.wait_run("run-order", agent_id=AGENT))
    assert exc.value.code == "REJECTED_SCOPE_ESCAPE"
    assert backend.runs_reg.get("run-order").status == RunStatus.RUNNING  # type: ignore[union-attr]
