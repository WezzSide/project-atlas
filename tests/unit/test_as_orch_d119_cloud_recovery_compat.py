"""D-119 authentic Run.git shape + exact Cloud run recovery matrix."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from project_atlas.orchestration.sdk.cloud_run_recovery import (
    CloudRunRecoveryClass,
    is_get_run_reconnect_miss,
    recover_exact_cloud_run,
)
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
    RunGitInfo,
    RunMutationBaseline,
    bind_terminal_git_repository,
    extract_terminal_run_git,
    mint_cloud_run_baseline,
)
from project_atlas.orchestration.sdk.security_gates import CANONICAL_BRANCH

PIN = "7e797468a2eca37c959920912b1fa264df4be638"
H1 = "1111111111111111111111111111111111111111"
AGENT = "bc-d119cloudagent0001"
BRANCH = CANONICAL_BRANCH


def _baseline(**kw: object) -> RunMutationBaseline:
    base = dict(
        run_id="run-d119",
        agent_id=AGENT,
        runtime=AgentRuntime.CLOUD,
        repository=CANONICAL_REPO_URL,
        base_main=PIN,
        remote_branch=BRANCH,
        remote_pre_head=PIN,
        dag_generation=119,
        lease_id="lease-d119",
        package_id=PACKAGE_ID,
    )
    base.update(kw)
    return RunMutationBaseline(**base)  # type: ignore[arg-type]


def test_extract_direct_run_result_git_repo_url() -> None:
    src = SimpleNamespace(git=SimpleNamespace(repo_url=CANONICAL_REPO_URL, branches=[BRANCH]))
    info = extract_terminal_run_git(src)
    assert info is not None
    assert info.repo_url == CANONICAL_REPO_URL
    assert info.branches == (BRANCH,)


def test_extract_nested_run_git_repo_url() -> None:
    src = SimpleNamespace(
        run=SimpleNamespace(git=SimpleNamespace(repo_url=CANONICAL_REPO_URL, branches=[BRANCH]))
    )
    info = extract_terminal_run_git(src)
    assert info is not None
    assert info.repo_url == CANONICAL_REPO_URL


def test_extract_dict_run_git_shape() -> None:
    src = {"run": {"git": {"repo_url": CANONICAL_REPO_URL, "branches": [BRANCH]}}}
    info = extract_terminal_run_git(src)
    assert info is not None
    assert info.repo_url == CANONICAL_REPO_URL
    assert info.branches == (BRANCH,)


def test_extract_repo_url_on_branch_entry_empty_parent() -> None:
    """Authentic Lane-G shape: repo_url nested under git.branches[]."""
    src = SimpleNamespace(
        git=SimpleNamespace(
            repo_url=None,
            branches=[SimpleNamespace(name=BRANCH, repo_url=CANONICAL_REPO_URL)],
        )
    )
    info = extract_terminal_run_git(src)
    assert info is not None
    assert info.repo_url == CANONICAL_REPO_URL
    assert info.branches == (BRANCH,)


def test_bind_repo_omitted_valid_launch_baseline() -> None:
    git = RunGitInfo(repo_url=None, branches=(BRANCH,))
    bound = bind_terminal_git_repository(git, attribution=_baseline())
    assert bound.repo_url == CANONICAL_REPO_URL


def test_bind_repo_omitted_missing_baseline_pre_head() -> None:
    git = RunGitInfo(repo_url=None, branches=(BRANCH,))
    with pytest.raises(SdkRuntimeError) as exc:
        bind_terminal_git_repository(git, attribution=_baseline(remote_pre_head=None))
    assert exc.value.code == "REMOTE_ATTRIBUTION_UNDETERMINED"


def test_bind_repo_omitted_foreign_baseline() -> None:
    git = RunGitInfo(repo_url=None, branches=(BRANCH,))
    with pytest.raises(SdkRuntimeError) as exc:
        bind_terminal_git_repository(
            git,
            attribution=_baseline(repository="https://gitlab.com/B0LK13/project-atlas"),
        )
    assert exc.value.code == "REMOTE_ATTRIBUTION_UNDETERMINED"


def test_bind_repo_present_foreign_host() -> None:
    git = RunGitInfo(repo_url="https://gitlab.com/B0LK13/project-atlas", branches=(BRANCH,))
    with pytest.raises(SdkRuntimeError) as exc:
        bind_terminal_git_repository(git, attribution=_baseline())
    assert "foreign" in str(exc.value).casefold()


def test_bind_nested_foreign_repo_url() -> None:
    payload = {
        "run": {
            "git": {
                "repo_url": "https://evil.com/B0LK13/project-atlas",
                "branches": [BRANCH],
            }
        }
    }
    info = extract_terminal_run_git(payload)
    assert info is not None
    with pytest.raises(SdkRuntimeError):
        bind_terminal_git_repository(info, attribution=_baseline())


def test_cloud_provider_accepts_omitted_repo_with_baseline() -> None:
    provider = CloudRemoteGitAttributionProvider(
        resolve_remote_head=lambda _r, _b: H1,
        resolve_remote_diff=lambda _r, _a, _b: ["src/project_atlas/orchestration/sdk/backend.py"],
    )
    paths = provider.collect_changed_paths(
        root=Path("."),
        attribution=_baseline(),
        terminal_git=RunGitInfo(repo_url=None, branches=(BRANCH,)),
        local_pre_head=None,
    )
    assert paths == ["src/project_atlas/orchestration/sdk/backend.py"]


def test_cloud_provider_rejects_ambiguous_branches() -> None:
    provider = CloudRemoteGitAttributionProvider(
        resolve_remote_head=lambda _r, _b: H1,
        resolve_remote_diff=lambda *_a: ["x.py"],
    )
    with pytest.raises(SdkRuntimeError) as exc:
        provider.collect_changed_paths(
            root=Path("."),
            attribution=_baseline(),
            terminal_git=RunGitInfo(repo_url=CANONICAL_REPO_URL, branches=(BRANCH, "other")),
            local_pre_head=None,
        )
    assert exc.value.code == "REMOTE_ATTRIBUTION_UNDETERMINED"


def test_cloud_provider_rejects_missing_branches() -> None:
    """Empty Run.git branches with no lineage/snapshot still fail closed."""
    provider = CloudRemoteGitAttributionProvider(
        resolve_remote_head=lambda _r, _b: H1,
        resolve_remote_diff=lambda *_a: ["x.py"],
    )
    with pytest.raises(SdkRuntimeError) as exc:
        provider.collect_changed_paths(
            root=Path("."),
            attribution=_baseline(remote_branch=None, remote_auto_branches_pre=None),
            terminal_git=RunGitInfo(repo_url=CANONICAL_REPO_URL, branches=()),
            local_pre_head=None,
        )
    assert exc.value.code == "REMOTE_ATTRIBUTION_UNDETERMINED"


def test_cloud_provider_rejects_unexpected_branch_switch() -> None:
    provider = CloudRemoteGitAttributionProvider(
        resolve_remote_head=lambda _r, _b: H1,
        resolve_remote_diff=lambda *_a: ["x.py"],
    )
    with pytest.raises(SdkRuntimeError) as exc:
        provider.collect_changed_paths(
            root=Path("."),
            attribution=_baseline(),
            terminal_git=RunGitInfo(repo_url=CANONICAL_REPO_URL, branches=("feat/wrong-branch",)),
            local_pre_head=None,
        )
    assert exc.value.code == "REMOTE_ATTRIBUTION_UNDETERMINED"


def test_is_get_run_reconnect_miss() -> None:
    assert is_get_run_reconnect_miss(Exception("invalid_argument: Run run-xyz not found"))
    assert not is_get_run_reconnect_miss(Exception("permission denied"))


class _FakeClient:
    def __init__(
        self,
        *,
        get_run_results: list[object] | None = None,
        runs: list[Any] | None = None,
    ) -> None:
        self._get_run_results = list(get_run_results or [])
        self._runs = runs or []
        self.agents = self
        self.get_run_options_calls: list[object] = []
        self.list_runs_options_calls: list[object] = []

    async def resume(self, agent_id: str, options: object = None) -> object:
        del options
        return SimpleNamespace(id=agent_id)

    async def get_run(self, run_id: str, options: object = None) -> object:
        self.get_run_options_calls.append(options)
        if not self._get_run_results:
            return SimpleNamespace(id=run_id, agent_id=AGENT, status="FINISHED", git=None)
        item = self._get_run_results.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def list_runs(
        self, agent_id: str, options: object = None, cursor: str | None = None
    ) -> object:
        del agent_id, cursor
        self.list_runs_options_calls.append(options)
        return SimpleNamespace(items=list(self._runs), next_cursor="")


def _agent() -> AgentRecord:
    return AgentRecord(
        agent_id=AGENT,
        runtime=AgentRuntime.CLOUD,
        role=AgentRole.REMEDIATOR,
        package_id=PACKAGE_ID,
        base_main=PIN,
        branch=BRANCH,
        created_at=_utc_now(),
        state=AgentState.BUSY,
        worker_backend="cursor_sdk",
        workspace="/tmp",
        repository=CANONICAL_REPO_URL,
        creation_generation=119,
        creation_sequence=1,
    )


def _run(run_id: str = "run-d119") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        agent_id=AGENT,
        cycle_id="C-D119",
        package_id=PACKAGE_ID,
        lease_id="lease-d119",
        role=AgentRole.REMEDIATOR,
        prompt_digest="a" * 64,
        idempotency_key=f"idemp-{run_id}",
        status=RunStatus.RUNNING,
        started_at=_utc_now(),
        candidate_head=PIN,
        candidate_tree="0" * 40,
        node_id="REMEDIATE-D119",
        dag_generation=119,
    )


def test_recover_direct_get_run_ok() -> None:
    client = _FakeClient()
    recovered = asyncio.run(
        recover_exact_cloud_run(
            client=client,
            agent=_agent(),
            run=_run(),
            agent_id=AGENT,
            run_id="run-d119",
            resume=lambda _a: asyncio.sleep(0),
        )
    )
    assert recovered.classification == CloudRunRecoveryClass.DIRECT_GET_RUN_OK


def test_recover_get_run_miss_list_exact_match() -> None:
    miss = Exception("invalid_argument: Run run-d119 not found")
    listed = SimpleNamespace(id="run-d119", agent_id=AGENT, status="FINISHED")
    ok = SimpleNamespace(id="run-d119", agent_id=AGENT, status="FINISHED", git=None)
    client = _FakeClient(get_run_results=[miss, ok], runs=[listed])
    recovered = asyncio.run(
        recover_exact_cloud_run(
            client=client,
            agent=_agent(),
            run=_run(),
            agent_id=AGENT,
            run_id="run-d119",
            resume=lambda _a: asyncio.sleep(0),
        )
    )
    assert recovered.classification == CloudRunRecoveryClass.REATTACHED_GET_RUN_OK


def test_recover_get_run_miss_list_empty() -> None:
    miss = Exception("Run run-d119 not found")
    client = _FakeClient(get_run_results=[miss], runs=[])
    with pytest.raises(SdkRuntimeError) as exc:
        asyncio.run(
            recover_exact_cloud_run(
                client=client,
                agent=_agent(),
                run=_run(),
                agent_id=AGENT,
                run_id="run-d119",
                resume=lambda _a: asyncio.sleep(0),
            )
        )
    assert exc.value.code == CloudRunRecoveryClass.CLOUD_RUN_RECOVERY_UNDETERMINED.value


def test_recover_get_run_miss_only_other_run() -> None:
    miss = Exception("Run run-d119 not found")
    other = SimpleNamespace(id="run-other", agent_id=AGENT, status="FINISHED")
    client = _FakeClient(get_run_results=[miss], runs=[other])
    with pytest.raises(SdkRuntimeError) as exc:
        asyncio.run(
            recover_exact_cloud_run(
                client=client,
                agent=_agent(),
                run=_run(),
                agent_id=AGENT,
                run_id="run-d119",
                resume=lambda _a: asyncio.sleep(0),
            )
        )
    assert exc.value.code == CloudRunRecoveryClass.CLOUD_RUN_RECOVERY_UNDETERMINED.value


def test_recover_rejects_wrong_agent_on_listed_run() -> None:
    miss = Exception("not found")
    listed = SimpleNamespace(id="run-d119", agent_id="bc-other", status="FINISHED")
    client = _FakeClient(get_run_results=[miss, miss], runs=[listed])
    with pytest.raises(SdkRuntimeError) as exc:
        asyncio.run(
            recover_exact_cloud_run(
                client=client,
                agent=_agent(),
                run=_run(),
                agent_id=AGENT,
                run_id="run-d119",
                resume=lambda _a: asyncio.sleep(0),
            )
        )
    assert exc.value.code == CloudRunRecoveryClass.FOREIGN_RECOVERED_RUN.value


def test_mint_baseline_helper_still_binds(tmp_path: Path) -> None:
    baselined = mint_cloud_run_baseline(
        root=tmp_path,
        run_id="run-d119",
        agent_id=AGENT,
        base_main=PIN,
        branch=BRANCH,
        dag_generation=119,
        lease_id="lease-d119",
    )
    assert baselined.repository == CANONICAL_REPO_URL
    assert baselined.runtime == AgentRuntime.CLOUD


def test_recover_passes_api_key_to_get_run_and_list_runs() -> None:
    """ORCH-SDK-CLOUD-GET-RUN-OPTIONS-001: api_key must reach get_run/list_runs."""
    api_key = "test-api-key-g107-get-run-options"
    miss = Exception("invalid_argument: Run run-d119 not found")
    listed = SimpleNamespace(id="run-d119", agent_id=AGENT, status="FINISHED")
    ok = SimpleNamespace(id="run-d119", agent_id=AGENT, status="FINISHED", git=None)
    client = _FakeClient(get_run_results=[miss, ok], runs=[listed])
    recovered = asyncio.run(
        recover_exact_cloud_run(
            client=client,
            agent=_agent(),
            run=_run(),
            agent_id=AGENT,
            run_id="run-d119",
            api_key=api_key,
            resume=lambda _a: asyncio.sleep(0),
        )
    )
    assert recovered.classification == CloudRunRecoveryClass.REATTACHED_GET_RUN_OK
    assert client.get_run_options_calls
    for opts in client.get_run_options_calls:
        assert isinstance(opts, dict)
        assert opts.get("api_key") == api_key
        assert opts.get("runtime") == "cloud"
        assert opts.get("agent_id") == AGENT
    assert client.list_runs_options_calls
    for opts in client.list_runs_options_calls:
        assert isinstance(opts, dict)
        assert opts.get("api_key") == api_key
        assert opts.get("runtime") == "cloud"


def test_cloud_get_run_requires_agent_id_and_runtime() -> None:
    """ORCH-SDK-CLOUD-GET-RUN-AGENTID-RUNTIME-001: Cloud GetRun needs both."""
    from project_atlas.orchestration.sdk.cloud_run_recovery import cloud_get_run_options

    opts = cloud_get_run_options(agent_id=AGENT, api_key="k")
    assert opts == {"runtime": "cloud", "agent_id": AGENT, "api_key": "k"}
    opts_no_key = cloud_get_run_options(agent_id=AGENT)
    assert opts_no_key == {"runtime": "cloud", "agent_id": AGENT}
    assert "api_key" not in opts_no_key

    ok = SimpleNamespace(id="run-d119", agent_id=AGENT, status="FINISHED", git=None)
    client = _FakeClient(get_run_results=[ok], runs=[])
    recovered = asyncio.run(
        recover_exact_cloud_run(
            client=client,
            agent=_agent(),
            run=_run(),
            agent_id=AGENT,
            run_id="run-d119",
            api_key=None,
            resume=lambda _a: asyncio.sleep(0),
        )
    )
    assert recovered.classification == CloudRunRecoveryClass.DIRECT_GET_RUN_OK
    assert client.get_run_options_calls[0].get("runtime") == "cloud"
    assert client.get_run_options_calls[0].get("agent_id") == AGENT


def test_list_runs_always_sets_cloud_runtime_even_without_api_key() -> None:
    """D-121 probe: list_runs without runtime=cloud raises AgentNotFoundError."""
    miss = Exception("Run run-d119 not found")
    listed = SimpleNamespace(id="run-d119", agent_id=AGENT, status="FINISHED")
    client = _FakeClient(get_run_results=[miss, miss], runs=[listed])
    recovered = asyncio.run(
        recover_exact_cloud_run(
            client=client,
            agent=_agent(),
            run=_run(),
            agent_id=AGENT,
            run_id="run-d119",
            api_key=None,
            resume=lambda _a: asyncio.sleep(0),
        )
    )
    assert recovered.classification == CloudRunRecoveryClass.LIST_RUNS_EXACT_MATCH
    assert client.list_runs_options_calls
    for opts in client.list_runs_options_calls:
        assert isinstance(opts, dict)
        assert opts.get("runtime") == "cloud"


def test_backend_get_run_status_passes_api_key_options(tmp_path: Path) -> None:
    """Backend get_run_status must forward Cloud agent_id+runtime+api_key options."""
    from project_atlas.orchestration.sdk.backend import CursorSDKExecutionBackend
    from project_atlas.orchestration.sdk.registries import CloudAgentRegistry, RunRegistry
    from project_atlas.orchestration.sdk.role_pool import AgentRolePool

    api_key = "test-api-key-g107-backend-get-run"
    client = _FakeClient()
    agents = CloudAgentRegistry(tmp_path)
    agents.upsert(_agent())
    backend = CursorSDKExecutionBackend(
        root=tmp_path,
        agents_reg=agents,
        runs_reg=RunRegistry(tmp_path),
        pool=AgentRolePool(agents),
        api_key=api_key,
    )
    backend._client = client
    status = asyncio.run(backend.get_run_status("run-d119", agent_id=AGENT))
    assert status == RunStatus.FINISHED
    assert len(client.get_run_options_calls) == 1
    opts = client.get_run_options_calls[0]
    assert isinstance(opts, dict)
    assert opts.get("api_key") == api_key
    assert opts.get("runtime") == "cloud"
    assert opts.get("agent_id") == AGENT


def test_empty_run_git_branch_uses_expected_lineage() -> None:
    """ORCH-SDK-CLOUD-RUN-GIT-EMPTY-BRANCH-001: expected_branch fills omitted name."""
    from project_atlas.orchestration.sdk.mutation_attribution import (
        select_canonical_remote_branch,
    )

    branch = select_canonical_remote_branch(
        RunGitInfo(repo_url=CANONICAL_REPO_URL, branches=()),
        expected_branch="cursor/smoke-v2-dev-run1-test",
    )
    assert branch == "cursor/smoke-v2-dev-run1-test"


def test_empty_run_git_branch_discovers_unique_new_auto_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty Run.git branch + unique new cursor/* head since mint → bind it."""
    import project_atlas.orchestration.sdk.mutation_attribution as ma

    monkeypatch.setattr(
        ma,
        "list_remote_auto_branches",
        lambda _repo: ("cursor/smoke-v2-dev-run1-new", "cursor/old"),
    )
    branch = ma.select_canonical_remote_branch(
        RunGitInfo(repo_url=CANONICAL_REPO_URL, branches=()),
        expected_branch=None,
        auto_branches_pre=("cursor/old",),
        repository=CANONICAL_REPO_URL,
    )
    assert branch == "cursor/smoke-v2-dev-run1-new"


def test_empty_run_git_branch_ambiguous_new_auto_branches_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import project_atlas.orchestration.sdk.mutation_attribution as ma

    monkeypatch.setattr(
        ma,
        "list_remote_auto_branches",
        lambda _repo: ("cursor/a", "cursor/b", "cursor/old"),
    )
    with pytest.raises(SdkRuntimeError) as exc:
        ma.select_canonical_remote_branch(
            RunGitInfo(repo_url=CANONICAL_REPO_URL, branches=()),
            expected_branch=None,
            auto_branches_pre=("cursor/old",),
            repository=CANONICAL_REPO_URL,
        )
    assert exc.value.code == "REMOTE_ATTRIBUTION_UNDETERMINED"
    assert "ambiguous" in str(exc.value).casefold()


def test_cloud_provider_first_parent_fallback_when_base_main_diverges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty Run.git + auto-branch: Cloud start SHA may differ from base_main."""
    import project_atlas.orchestration.sdk.mutation_attribution as ma

    parent = "cccccccccccccccccccccccccccccccccccccccc"
    tip = "dddddddddddddddddddddddddddddddddddddddd"
    calls: list[tuple[str, str]] = []

    def fake_head(_repo: str, _branch: str) -> str:
        return tip

    def fake_diff(repo: str, pre: str, post: str, *, work_root: Path) -> list[str] | None:
        calls.append((pre, post))
        if pre == PIN:
            raise SdkRuntimeError(
                "remote terminal head is not a descendant of baseline",
                code="REMOTE_ATTRIBUTION_UNDETERMINED",
            )
        if pre == parent and post == tip:
            return ["tests/unit/smoke-v2-dev/x/run-1.txt"]
        return None

    monkeypatch.setattr(ma, "resolve_commit_first_parent", lambda *_a, **_k: parent)
    provider = CloudRemoteGitAttributionProvider(
        resolve_remote_head=fake_head,
        resolve_remote_diff=None,
    )
    monkeypatch.setattr(ma, "default_resolve_remote_diff", fake_diff)
    monkeypatch.setattr(
        ma,
        "list_remote_auto_branches",
        lambda _r: ("cursor/smoke-v2-dev-run1-x",),
    )
    attr = _baseline(
        remote_branch=None,
        remote_auto_branches_pre=(),
        remote_pre_head=PIN,
    )
    paths = provider.collect_changed_paths(
        root=tmp_path,
        attribution=attr,
        terminal_git=RunGitInfo(repo_url=CANONICAL_REPO_URL, branches=()),
        local_pre_head=None,
    )
    assert paths == ["tests/unit/smoke-v2-dev/x/run-1.txt"]
    assert attr.remote_pre_head == parent
    assert attr.remote_post_head == tip
    assert attr.remote_branch == "cursor/smoke-v2-dev-run1-x"
    assert (PIN, tip) in calls
    assert (parent, tip) in calls


def test_remote_diff_returns_none_when_objects_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Incomplete fetch must be undetermined, not false non-descendant."""
    import subprocess as sp

    import project_atlas.orchestration.sdk.mutation_attribution as ma

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(cmd))
        from types import SimpleNamespace

        if cmd[:2] == ["git", "fetch"]:
            assert "--depth=0" not in cmd
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["git", "cat-file", "-e"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        if "merge-base" in cmd:
            raise AssertionError("merge-base must not run when objects missing")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sp, "run", fake_run)
    result = ma.default_resolve_remote_diff(
        "https://github.com/B0LK13/project-atlas",
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        work_root=tmp_path,
    )
    assert result is None
    assert any(c[:2] == ["git", "fetch"] for c in calls)
    assert not any("--depth=0" in c for c in calls)


def test_extract_omitted_branch_name_from_authentic_wire_shape() -> None:
    """Wire shape: git.branches[{repoUrl}] with no branch field → branches=()."""
    info = extract_terminal_run_git(
        {
            "git": {
                "branches": [{"repoUrl": "github.com/B0LK13/project-atlas"}],
            }
        }
    )
    assert info is not None
    assert info.repo_url == "github.com/B0LK13/project-atlas"
    assert info.branches == ()
