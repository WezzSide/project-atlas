"""D-ATLAS-RSI-GOVERNED-LOOP-001 / D-ATLAS-AUTONOMY-LADDER-001: governed loop gate.

Pins the owner-controlled gate in ``autonomy/tools/preflight.py`` against
``autonomy/policy.md`` sections 4, 4.4, 5 and 8.1 (scopes per role and autonomy level, role
separation, ledger rules and retry cap, verifier certs and the local lane fallback), and fails
CI if the ``loop.yaml`` pin drifts from the policy bytes.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "autonomy" / "tools" / "preflight.py"
BASE_SHA = "b87b4a226f4aa8b2f669edf112aa3476454f754f"
NEEDS_GIT = pytest.mark.skipif(shutil.which("git") is None, reason="git executable not available")


def _load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("autonomy_preflight", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve string annotations here
    spec.loader.exec_module(module)
    return module


pf = _load_tool()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _grant_text(sha: str, *, iteration: int = 1) -> str:
    return (
        "---\n"
        f"grant: G-{iteration}\n"
        f"iteration: {iteration}\n"
        "issued_by: owner\n"
        f"policy_sha: {sha}\n"
        f"base_sha: {BASE_SHA}\n"
        f"directive: autonomy/directives/D-ATLAS-ITER-{iteration}.md\n"
        "---\n\nOwner grant.\n"
    )


@pytest.fixture
def loop_root(tmp_path: Path) -> Path:
    """A granted iteration-1 loop tree built from the real policy, loop.yaml and ledger."""
    for rel in (pf.POLICY_PATH, pf.LOOP_PATH, pf.LEDGER_PATH):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / rel, tmp_path / rel)
    _write(tmp_path / pf.grant_path(1), _grant_text(pf.policy_sha(tmp_path)))
    _write(tmp_path / "autonomy/directives/D-ATLAS-ITER-1.md", "# D-ATLAS-ITER-1\n")
    return tmp_path


def _repin(root: Path) -> None:
    sha = pf.policy_sha(root)
    loop = root / pf.LOOP_PATH
    pinned = re.sub(r"^policy_sha: .*$", f"policy_sha: {sha}", loop.read_text("utf-8"), flags=re.M)
    _write(loop, pinned)
    _write(root / pf.grant_path(1), _grant_text(sha))


def _grant(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "iteration": 1,
        "policy_sha": "0" * 64,
        "base_sha": BASE_SHA,
        "directive": "autonomy/directives/D-ATLAS-ITER-1.md",
    }
    fields.update(overrides)
    return pf.Grant(**fields)


def _change(path: str, status: str = "M", added: int = 1, deleted: int = 0) -> Any:
    return pf.Change(path, status, added, deleted)


def _verdict(iteration: int, verdict: str) -> dict[str, Any]:
    return {"event": "verdict", "iteration": iteration, "verdict": verdict}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=loop-test",
            "-c",
            "user.email=loop-test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.autocrlf=false",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_glob_semantics_keep_single_star_within_a_segment() -> None:
    assert pf.matches_any("src/project_atlas/cli.py", ["src/**"])
    assert not pf.matches_any("srcx/cli.py", ["src/**"])
    assert pf.matches_any("key.pem", ["**/*.pem"])
    assert pf.matches_any("a/b/key.pem", ["**/*.pem"])
    assert not pf.matches_any("a/b.py", ["*.py"])
    assert not pf.matches_any("src/project_atlas/secrets.py", ["**/*.key", "**/.env"])


def test_repo_loop_yaml_pins_the_current_policy_bytes() -> None:
    assert pf.load_loop(REPO_ROOT)["policy_sha"] == pf.policy_sha(REPO_ROOT)
    assert pf.load_loop(REPO_ROOT)["max_iterations_per_grant"] == 1


def test_repo_policy_starts_at_level_0_with_role_floors_closed() -> None:
    policy = pf.load_policy(REPO_ROOT)
    assert policy.level == 0
    assert set(pf.NEVER_WRITABLE + pf.EXECUTOR_NEVER) <= set(policy.forbidden)
    assert not set(policy.allowed) & set(policy.forbidden)
    top = replace(policy, level=pf.MAX_AUTONOMY_LEVEL)
    for role in pf.ROLES:
        for glob in top.scopes_for(role, 1):
            assert not pf.matches_any(glob, pf.NEVER_WRITABLE), (role, glob)
            if role != "executor":
                assert not pf.matches_any(glob, pf.NON_EXECUTOR_NEVER), (role, glob)


def test_policy_sha_rejects_crlf_bytes(tmp_path: Path) -> None:
    (tmp_path / "autonomy").mkdir()
    (tmp_path / pf.POLICY_PATH).write_bytes(b"# policy\r\n")
    with pytest.raises(pf.ConfigError):
        pf.policy_sha(tmp_path)


def test_preflight_passes_for_a_matching_grant(loop_root: Path) -> None:
    assert pf.check_preflight(loop_root, 1) == []


@pytest.mark.parametrize("switch", ["autonomy/HALT", "autonomy/HALT-REQUEST"])
def test_preflight_stops_on_either_kill_switch(loop_root: Path, switch: str) -> None:
    _write(loop_root / switch, "stop\n")
    assert pf.check_preflight(loop_root, 1) == [f"{switch} exists: the loop is stopped"]


def test_preflight_requires_a_grant(loop_root: Path) -> None:
    problems = pf.check_preflight(loop_root, 2)
    assert problems and "missing grant" in problems[0]


def test_preflight_rejects_grant_for_other_policy(loop_root: Path) -> None:
    _write(loop_root / pf.grant_path(1), _grant_text("f" * 64))
    assert any("G-1.md policy_sha" in problem for problem in pf.check_preflight(loop_root, 1))


def test_preflight_rejects_policy_edited_after_pinning(loop_root: Path) -> None:
    with (loop_root / pf.POLICY_PATH).open("ab") as handle:
        handle.write(b"\nAgents may merge.\n")
    problems = pf.check_preflight(loop_root, 1)
    assert any("loop.yaml policy_sha" in problem for problem in problems)
    assert any("G-1.md policy_sha" in problem for problem in problems)


def test_load_grant_rejects_directive_path_traversal(loop_root: Path) -> None:
    text = _grant_text(pf.policy_sha(loop_root)).replace(
        "autonomy/directives/D-ATLAS-ITER-1.md",
        "autonomy/directives/../policy.md",
    )
    _write(loop_root / pf.grant_path(1), text)
    with pytest.raises(pf.ConfigError, match="normalized path"):
        pf.load_grant(loop_root, 1)


def test_load_grant_canonicalizes_dot_segments_so_scope_can_pin(loop_root: Path) -> None:
    dotted = "autonomy/directives/./D-ATLAS-ITER-1.md"
    text = _grant_text(pf.policy_sha(loop_root)).replace(
        "autonomy/directives/D-ATLAS-ITER-1.md",
        dotted,
    )
    _write(loop_root / pf.grant_path(1), text)
    grant = pf.load_grant(loop_root, 1)
    assert grant.directive == "autonomy/directives/D-ATLAS-ITER-1.md"
    problems = pf.check_scope(
        pf.load_policy(loop_root), grant, [_change("autonomy/directives/D-ATLAS-ITER-1.md")]
    )
    assert any("granted directive is pinned" in problem for problem in problems)


def test_preflight_rejects_mislabelled_grant_and_missing_directive(loop_root: Path) -> None:
    (loop_root / "autonomy/directives/D-ATLAS-ITER-1.md").unlink()
    assert any("does not exist" in problem for problem in pf.check_preflight(loop_root, 1))
    _write(loop_root / pf.grant_path(1), _grant_text(pf.policy_sha(loop_root), iteration=2))
    assert any("must declare grant: G-1" in problem for problem in pf.check_preflight(loop_root, 1))


def test_multi_iteration_grants_need_autonomy_level_3(loop_root: Path) -> None:
    loop = loop_root / pf.LOOP_PATH
    text = loop.read_text("utf-8").replace(
        "max_iterations_per_grant: 1", "max_iterations_per_grant: 3"
    )
    _write(loop, text)
    assert any("autonomy level 3" in problem for problem in pf.check_preflight(loop_root, 1))
    policy = loop_root / pf.POLICY_PATH
    raised = policy.read_text("utf-8").replace("autonomy_level: 0\n", "autonomy_level: 3\n", 1)
    _write(policy, raised)
    _repin(loop_root)
    assert pf.load_policy(loop_root).level == 3
    assert pf.check_preflight(loop_root, 1) == []


@pytest.mark.parametrize(
    "content",
    [
        b"not json\n",
        b'{"event": "packet"}',
        b'{"iteration": 1}\n',
        b'{"event": "verdict", "iteration": 1, "verdict": "MERGE"}\n',
        b'{"event": "verdict", "iteration": "1", "verdict": "CONTINUE"}\n',
        b'{"event": "packet"}\n\n{"event": "packet"}\n',
        b'{"event": "packet"}\r\n',
    ],
)
def test_preflight_treats_a_malformed_ledger_as_tampering(loop_root: Path, content: bytes) -> None:
    (loop_root / pf.LEDGER_PATH).write_bytes(content)
    problems = pf.check_preflight(loop_root, 1)
    assert len(problems) == 1 and pf.LEDGER_PATH in problems[0]


@pytest.mark.parametrize(("redesigns", "capped"), [(0, False), (1, False), (2, False), (3, True)])
def test_retry_cap_allows_two_redesign_reworks_then_stops(redesigns: int, capped: bool) -> None:
    problems = pf.check_ledger([_verdict(1, "REDESIGN")] * redesigns, 1)
    assert bool(problems) is capped
    assert all("retry cap" in problem and "HALT-REQUEST" in problem for problem in problems)


def test_preflight_enforces_the_retry_cap_from_the_ledger_file(loop_root: Path) -> None:
    lines = "".join(json.dumps(_verdict(1, "REDESIGN")) + "\n" for _ in range(3))
    _write(loop_root / pf.LEDGER_PATH, lines)
    assert any("retry cap" in problem for problem in pf.check_preflight(loop_root, 1))


def test_ledger_closes_pauses_and_sequences_iterations() -> None:
    closed = pf.check_ledger([_verdict(1, "CONTINUE")], 1)
    assert closed == ["iteration 1 is already closed by verdict CONTINUE"]
    paused = [_verdict(1, "REDESIGN"), _verdict(1, "OWNER_DECISION_REQUIRED")]
    assert pf.check_ledger(paused, 1) == ["iteration 1 is paused awaiting an owner decision"]
    answered = [_verdict(1, "OWNER_DECISION_REQUIRED"), _verdict(1, "REDESIGN")]
    assert pf.check_ledger(answered, 1) == []
    assert any("iteration 1 has no CONTINUE" in p for p in pf.check_ledger([], 2))
    assert any("iteration 1 has no CONTINUE" in p for p in pf.check_ledger(paused, 2))
    for verdict in pf.NEXT_OPENING_VERDICTS:
        assert pf.check_ledger([_verdict(1, verdict)], 2) == []


def test_stop_verdict_holds_until_an_owner_resume_event() -> None:
    stopped = [_verdict(1, "STOP")]
    assert any("loop is stopped" in problem for problem in pf.check_ledger(stopped, 2))
    resumed = [*stopped, {"event": "resume", "by": "owner"}]
    assert not any("loop is stopped" in problem for problem in pf.check_ledger(resumed, 2))
    assert pf.check_ledger(resumed, 1) == ["iteration 1 is already closed by verdict STOP"]
    assert pf.check_ledger(resumed, 2) == []


def test_scope_allows_granted_work_and_kill_switch_creation() -> None:
    policy = pf.load_policy(REPO_ROOT)
    changes = [
        _change("src/project_atlas/ask2.py"),
        _change("tests/unit/test_new.py", "A"),
        _change("autonomy/packets/RP-1.md", "A"),
        _change("autonomy/ledger.jsonl"),
        _change(pf.HALT_PATH, "A"),
        _change(pf.HALT_REQUEST_PATH, "A"),
    ]
    assert pf.check_scope(policy, _grant(), changes) == []


@pytest.mark.parametrize(
    "path",
    [
        "autonomy/policy.md",
        "autonomy/loop.yaml",
        "autonomy/tools/preflight.py",
        ".github/workflows/ci.yml",
        "autonomy/grants/G-2.md",
        "autonomy/verdicts/V-1.md",
        "autonomy/certs/C-1.md",
        "autonomy/drift/D-1.md",
    ],
)
def test_scope_executor_floor_cannot_be_opened_by_level_or_grant(path: str) -> None:
    policy = replace(pf.load_policy(REPO_ROOT), level=pf.MAX_AUTONOMY_LEVEL)
    grant = _grant(scope_exceptions=(path, "autonomy/**", ".github/**", "**"))
    assert pf.check_scope(policy, grant, [_change(path)])


def test_scope_forbidden_and_unlisted_paths_need_an_owner_exception() -> None:
    policy = pf.load_policy(REPO_ROOT)
    assert pf.check_scope(policy, _grant(), [_change("pyproject.toml")])
    assert pf.check_scope(policy, _grant(), [_change("README.md")])
    opened = _grant(scope_exceptions=("pyproject.toml", "README.md"))
    assert pf.check_scope(policy, opened, [_change("pyproject.toml"), _change("README.md")]) == []


@pytest.mark.parametrize("switch", ["autonomy/HALT", "autonomy/HALT-REQUEST"])
@pytest.mark.parametrize("role", ["executor", "verifier", "instrument", "supervisor"])
def test_scope_protects_kill_switches_and_ledger_history(switch: str, role: str) -> None:
    policy = pf.load_policy(REPO_ROOT)
    assert pf.check_scope(policy, _grant(), [_change(switch, "A")], role) == []
    assert pf.check_scope(policy, _grant(), [_change(switch, "D", 0, 1)], role)
    assert pf.check_scope(policy, _grant(), [_change(switch, "M", 1, 1)], role)
    assert pf.check_scope(policy, _grant(), [_change(pf.LEDGER_PATH, "M", 1, 1)], role)


@pytest.mark.parametrize(
    ("path", "level"),
    [
        ("autonomy/instruments/skills/x.md", 1),
        ("autonomy/instruments/directive-template.md", 2),
        ("autonomy/instruments/verify-checklist.md", 3),
    ],
)
def test_scope_autonomy_level_unlocks_instruments_one_rung_at_a_time(path: str, level: int) -> None:
    policy = pf.load_policy(REPO_ROOT)
    assert pf.check_scope(replace(policy, level=level - 1), _grant(), [_change(path)])
    assert pf.check_scope(replace(policy, level=level), _grant(), [_change(path)]) == []


def test_scope_budget_counts_files_and_lines_with_grant_override() -> None:
    policy = pf.load_policy(REPO_ROOT)
    too_many = [_change(f"src/f{i}.py") for i in range(policy.budget["max_files_touched"] + 1)]
    problems = pf.check_scope(policy, _grant(), too_many)
    assert any("max_files_touched" in problem for problem in problems)
    big = [_change("src/big.py", added=policy.budget["max_diff_lines"] + 1)]
    assert any("max_diff_lines" in problem for problem in pf.check_scope(policy, _grant(), big))
    raised = _grant(budget={"max_diff_lines": policy.budget["max_diff_lines"] * 2})
    assert pf.check_scope(policy, raised, big) == []


def test_scope_verifier_and_instrument_write_only_their_own_outputs() -> None:
    policy = pf.load_policy(REPO_ROOT)
    assert (
        pf.check_scope(policy, _grant(), [_change("autonomy/certs/C-1.md", "A")], "verifier") == []
    )
    for path in ("autonomy/certs/C-2.md", "autonomy/packets/RP-1.md", "src/project_atlas/cli.py"):
        assert pf.check_scope(policy, _grant(), [_change(path, "A")], "verifier"), path
    drift = [_change("autonomy/drift/D-1.md", "A")]
    assert pf.check_scope(policy, _grant(), drift, "instrument") == []
    assert pf.check_scope(policy, _grant(), [_change("autonomy/certs/C-1.md", "A")], "instrument")


def test_scope_supervisor_issues_grants_from_level_2_and_never_edits_code() -> None:
    policy = pf.load_policy(REPO_ROOT)
    verdict = [_change("autonomy/verdicts/V-1.md", "A")]
    grant_file = [_change("autonomy/grants/G-2.md", "A")]
    assert pf.check_scope(policy, _grant(), verdict, "supervisor") == []
    assert pf.check_scope(replace(policy, level=1), _grant(), grant_file, "supervisor")
    assert pf.check_scope(replace(policy, level=2), _grant(), grant_file, "supervisor") == []
    widened = replace(policy, role_scopes={"supervisor": {0: ("**",)}})
    for path in ("src/project_atlas/cli.py", "tests/unit/test_x.py", "autonomy/tools/preflight.py"):
        assert pf.check_scope(widened, _grant(), [_change(path)], "supervisor"), path
    with pytest.raises(pf.ConfigError):
        pf.check_scope(policy, _grant(), verdict, "owner")


def test_ledger_appends_are_role_limited() -> None:
    packet = b'{"event": "packet", "iteration": 1}\n'
    verdict = (json.dumps(_verdict(1, "CONTINUE")) + "\n").encode()
    resume = b'{"event": "resume", "by": "owner"}\n'
    assert pf.check_ledger_append(packet, "executor") == []
    assert pf.check_ledger_append(verdict, "executor")
    assert pf.check_ledger_append(verdict, "supervisor") == []
    assert pf.check_ledger_append(resume, "supervisor")
    assert pf.check_ledger_append(b"{broken\n", "supervisor")
    assert pf.check_ledger_append(b'{"event": "packet"}', "executor")
    assert pf.check_ledger_append(b'{"event": "packet"}\n\n{"event": "packet"}\n', "executor")


@NEEDS_GIT
def test_git_preflight_fetches_stale_origin_before_judging_halt(tmp_path: Path) -> None:
    work = tmp_path / "work"
    remote = tmp_path / "remote.git"
    owner = tmp_path / "owner"
    work.mkdir()
    for rel in (pf.POLICY_PATH, pf.LOOP_PATH, pf.LEDGER_PATH):
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / rel, work / rel)
    _git(work, "init", "-q")
    _git(work, "checkout", "-q", "-b", "main")
    _write(work / "README.md", "seed\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "seed")
    base = _git(work, "rev-parse", "HEAD").strip()
    sha = pf.policy_sha(work)
    _write(work / pf.grant_path(1), _grant_text(sha).replace(BASE_SHA, base))
    _write(work / "autonomy/directives/D-ATLAS-ITER-1.md", "# D-ATLAS-ITER-1\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "granted loop")
    subprocess.run(["git", "clone", "--bare", "-q", str(work), str(remote)], check=True)
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "fetch", "origin")
    subprocess.run(["git", "clone", "-q", str(remote), str(owner)], check=True)
    _write(owner / "autonomy/HALT", "owner halt\n")
    _git(owner, "add", "autonomy/HALT")
    _git(owner, "commit", "-q", "-m", "owner HALT")
    _git(owner, "push", "origin", "main")
    stale = _git(work, "rev-parse", "origin/main").strip()
    remote_tip = _git(remote, "rev-parse", "HEAD").strip()
    assert stale != remote_tip
    grant = pf.load_grant(work, 1)
    grant = replace(grant, base_sha=base)
    problems = pf.check_git_preflight(work, pf.load_policy(work), grant, "origin/main")
    assert any("HALT exists on refs/remotes/origin/main" in problem for problem in problems)
    assert _git(work, "rev-parse", "refs/remotes/origin/main").strip() == remote_tip
    _git(work, "branch", "origin/main", stale)
    shadowed = pf.check_git_preflight(work, pf.load_policy(work), grant, "origin/main")
    assert any("HALT exists on refs/remotes/origin/main" in problem for problem in shadowed)
    fullref = pf.check_git_preflight(work, pf.load_policy(work), grant, "refs/remotes/origin/main")
    assert any("HALT exists on refs/remotes/origin/main" in problem for problem in fullref)


@NEEDS_GIT
def test_scope_refreshes_grant_ref_so_local_origin_main_cannot_empty_diff(
    tmp_path: Path,
) -> None:
    """AS-AUTONOMY-P1-SCOPE-GRANT-REF-001: scope must refresh like preflight.

    A local ``refs/heads/origin/main`` at HEAD makes raw ``origin/main``
    merge-base equal HEAD, so the change list is empty and never-writable
    floors are skipped. Scope must fetch and judge ``refs/remotes/…``.
    """
    work = tmp_path / "work"
    remote = tmp_path / "remote.git"
    work.mkdir()
    for rel in (pf.POLICY_PATH, pf.LOOP_PATH, pf.LEDGER_PATH):
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / rel, work / rel)
    _git(work, "init", "-q")
    _git(work, "checkout", "-q", "-b", "main")
    _write(work / "README.md", "seed\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "seed")
    base = _git(work, "rev-parse", "HEAD").strip()
    sha = pf.policy_sha(work)
    _write(work / pf.grant_path(1), _grant_text(sha).replace(BASE_SHA, base))
    _write(work / "autonomy/directives/D-ATLAS-ITER-1.md", "# D-ATLAS-ITER-1\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "granted loop")
    subprocess.run(["git", "clone", "--bare", "-q", str(work), str(remote)], check=True)
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "fetch", "origin")
    (work / "autonomy" / "tools").mkdir(parents=True, exist_ok=True)
    (work / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    _write(work / "autonomy/tools/preflight.py", "# executor rewrite\n")
    _write(work / ".github/workflows/evil.yml", "name: evil\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "iteration rewrite")
    control = pf.main(["--root", str(work), "scope", "--iteration", "1", "--role", "executor"])
    assert control == 1
    _git(work, "branch", "origin/main", "HEAD")
    shadowed = pf.main(["--root", str(work), "scope", "--iteration", "1", "--role", "executor"])
    assert shadowed == 1
    _git(work, "update-ref", "refs/remotes/origin/main", "HEAD")
    poisoned = pf.main(["--root", str(work), "scope", "--iteration", "1", "--role", "executor"])
    assert poisoned == 1


@NEEDS_GIT
@pytest.mark.parametrize("switch", ["autonomy/HALT", "autonomy/HALT-REQUEST"])
def test_scope_fails_closed_when_kill_switch_exists_on_refreshed_grant_ref(
    tmp_path: Path, switch: str
) -> None:
    """AS-AUTONOMY-P1-SCOPE-HALT-001: scope must see grant-ref kill switches.

    Preflight already refreshes and refuses HALT on the grant ref. Scope is the
    post-work emit gate and must stop too, even when the worktree is clean and
    the only in-scope change is an allowed src/ edit.
    """
    work = tmp_path / "work"
    remote = tmp_path / "remote.git"
    owner = tmp_path / "owner"
    work.mkdir()
    for rel in (pf.POLICY_PATH, pf.LOOP_PATH, pf.LEDGER_PATH):
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / rel, work / rel)
    _git(work, "init", "-q")
    _git(work, "checkout", "-q", "-b", "main")
    _write(work / "README.md", "seed\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "seed")
    base = _git(work, "rev-parse", "HEAD").strip()
    sha = pf.policy_sha(work)
    _write(work / pf.grant_path(1), _grant_text(sha).replace(BASE_SHA, base))
    _write(work / "autonomy/directives/D-ATLAS-ITER-1.md", "# D-ATLAS-ITER-1\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "granted loop")
    subprocess.run(["git", "clone", "--bare", "-q", str(work), str(remote)], check=True)
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "fetch", "origin")
    subprocess.run(["git", "clone", "-q", str(remote), str(owner)], check=True)
    _write(owner / switch, "owner halt\n")
    _git(owner, "add", switch)
    _git(owner, "commit", "-q", "-m", "owner kill switch")
    _git(owner, "push", "origin", "main")
    stale = _git(work, "rev-parse", "origin/main").strip()
    remote_tip = _git(remote, "rev-parse", "HEAD").strip()
    assert stale != remote_tip
    _write(work / "src/ok.py", "ok = 1\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "feat: in-scope\n\nAtlas-Role: executor\n")
    assert not (work / switch).exists()
    scope = pf.main(["--root", str(work), "scope", "--iteration", "1", "--role", "executor"])
    assert scope == 1
    preflight = pf.main(["--root", str(work), "preflight", "--iteration", "1"])
    assert preflight == 1
    refreshed = _git(work, "rev-parse", "refs/remotes/origin/main").strip()
    assert refreshed == remote_tip


@NEEDS_GIT
def test_scope_fails_closed_when_grant_ref_remote_is_missing(tmp_path: Path) -> None:
    """AS-AUTONOMY-P1-GRANT-REF-REMOTE-MISSING-001.

    Removing ``origin`` does not dirty porcelain. Combined with a local
    ``origin/main`` at HEAD, refresh must not collapse onto that branch and
    skip never-writable floors.
    """
    work = tmp_path / "work"
    remote = tmp_path / "remote.git"
    work.mkdir()
    for rel in (pf.POLICY_PATH, pf.LOOP_PATH, pf.LEDGER_PATH):
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / rel, work / rel)
    _git(work, "init", "-q")
    _git(work, "checkout", "-q", "-b", "main")
    _write(work / "README.md", "seed\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "seed")
    base = _git(work, "rev-parse", "HEAD").strip()
    sha = pf.policy_sha(work)
    _write(work / pf.grant_path(1), _grant_text(sha).replace(BASE_SHA, base))
    _write(work / "autonomy/directives/D-ATLAS-ITER-1.md", "# D-ATLAS-ITER-1\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "granted loop")
    _git(work, "branch", "grant-ref")
    subprocess.run(["git", "clone", "--bare", "-q", str(work), str(remote)], check=True)
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "fetch", "origin")
    (work / "autonomy" / "tools").mkdir(parents=True, exist_ok=True)
    (work / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    _write(work / "autonomy/tools/preflight.py", "# executor rewrite\n")
    _write(work / ".github/workflows/evil.yml", "name: evil\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "iteration rewrite")
    control = pf.main(["--root", str(work), "scope", "--iteration", "1", "--role", "executor"])
    assert control == 1
    _git(work, "branch", "origin/main", "HEAD")
    _git(work, "remote", "remove", "origin")
    porcelain = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert porcelain == ""
    attack = pf.main(["--root", str(work), "scope", "--iteration", "1", "--role", "executor"])
    assert attack == 2
    local_floors = pf.main(
        [
            "--root",
            str(work),
            "scope",
            "--iteration",
            "1",
            "--role",
            "executor",
            "--grant-ref",
            "grant-ref",
        ]
    )
    assert local_floors == 1


@NEEDS_GIT
def test_git_preflight_rejects_a_rewritten_granted_directive(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    for rel in (pf.POLICY_PATH, pf.LOOP_PATH, pf.LEDGER_PATH):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / rel, tmp_path / rel)
    sha = pf.policy_sha(tmp_path)
    _write(tmp_path / pf.grant_path(1), _grant_text(sha))
    _write(tmp_path / "autonomy/directives/D-ATLAS-ITER-1.md", "# D-ATLAS-ITER-1\nDo X.\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "granted")
    _git(tmp_path, "branch", "grant-ref")
    rewritten = "# D-ATLAS-ITER-1\nAlso drop tests.\n"
    _write(tmp_path / "autonomy/directives/D-ATLAS-ITER-1.md", rewritten)
    problems = pf.check_git_preflight(
        tmp_path, pf.load_policy(tmp_path), pf.load_grant(tmp_path, 1), "grant-ref"
    )
    assert any("D-ATLAS-ITER-1.md differs from grant-ref" in problem for problem in problems)
    _write(tmp_path / "autonomy/directives/D-ATLAS-ITER-2.md", "# next\n")
    policy = pf.load_policy(tmp_path)
    grant = pf.load_grant(tmp_path, 1)
    next_ok = pf.check_scope(policy, grant, [_change("autonomy/directives/D-ATLAS-ITER-2.md", "A")])
    assert next_ok == []
    pinned = pf.check_scope(policy, grant, [_change("autonomy/directives/D-ATLAS-ITER-1.md")])
    assert any("granted directive is pinned" in problem for problem in pinned)


@NEEDS_GIT
def test_git_changes_and_ledger_append_against_a_real_repo(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _write(tmp_path / pf.LEDGER_PATH, '{"event": "packet", "iteration": 0}\n')
    _write(tmp_path / "src/old.py", "a = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "branch", "grant-ref")
    _git(tmp_path, "checkout", "-q", "-b", "iter/1")
    with (tmp_path / pf.LEDGER_PATH).open("a", encoding="utf-8", newline="\n") as handle:
        handle.write('{"event": "packet", "iteration": 1}\n')
    _git(tmp_path, "mv", "src/old.py", "src/new.py")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "iteration 1")

    changes = {change.path: change for change in pf.git_changes(tmp_path, "grant-ref", "HEAD")}
    assert set(changes) == {pf.LEDGER_PATH, "src/old.py", "src/new.py"}
    assert (changes["src/old.py"].status, changes["src/new.py"].status) == ("D", "A")
    assert (changes[pf.LEDGER_PATH].added, changes[pf.LEDGER_PATH].deleted) == (1, 0)
    appended = pf.ledger_append(tmp_path, "grant-ref", "HEAD")
    assert appended == b'{"event": "packet", "iteration": 1}\n'

    _write(tmp_path / pf.LEDGER_PATH, '{"event": "packet", "iteration": 1}\n')
    _git(tmp_path, "commit", "-q", "-am", "rewrite history")
    assert pf.ledger_append(tmp_path, "grant-ref", "HEAD") is None


@NEEDS_GIT
def test_role_trailers_keep_one_role_per_branch(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _write(tmp_path / "README.md", "base\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "branch", "grant-ref")
    _write(tmp_path / "src/a.py", "a = 1\n")
    _git(tmp_path, "add", "-A")
    message = "feat: a\n\nAtlas-Role: executor\nCo-Authored-By: x <x@example.invalid>\n"
    _git(tmp_path, "commit", "-q", "-m", message)

    records = pf.commit_roles(tmp_path, "grant-ref", "HEAD")
    assert [roles for _, roles in records] == [("executor",)]
    assert pf.check_roles(records, "executor") == []
    assert pf.check_roles(records, "supervisor")

    _write(tmp_path / "autonomy/verdicts/V-1.md", "CONTINUE\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "verdict\n\nAtlas-Role: supervisor\n")
    _write(tmp_path / "src/b.py", "b = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "no trailer")

    problems = pf.check_roles(pf.commit_roles(tmp_path, "grant-ref", "HEAD"), "executor")
    assert len(problems) == 2
    assert any("declares Atlas-Role supervisor" in problem for problem in problems)
    assert any("declares Atlas-Role none" in problem for problem in problems)


@NEEDS_GIT
def test_role_trailers_include_merge_commits_that_introduce_files(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _write(tmp_path / "README.md", "base\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "branch", "grant-ref")
    _git(tmp_path, "checkout", "-q", "-b", "subject")
    _write(tmp_path / "src/a.py", "a = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "feat a\n\nAtlas-Role: executor\n")
    _git(tmp_path, "checkout", "-q", "grant-ref")
    _git(tmp_path, "checkout", "-q", "-b", "other")
    _write(tmp_path / "src/b.py", "b = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "feat b\n\nAtlas-Role: executor\n")
    _git(tmp_path, "checkout", "-q", "subject")
    subprocess.run(
        ["git", "merge", "--no-ff", "--no-commit", "other"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
    )
    _write(tmp_path / "src/evil.py", "evil = 1\n")
    _git(tmp_path, "add", "src/evil.py")
    _git(tmp_path, "commit", "-q", "-m", "merge other and sneak evil.py")
    records = pf.commit_roles(tmp_path, "grant-ref", "HEAD")
    problems = pf.check_roles(records, "executor")
    assert any("declares Atlas-Role none" in problem for problem in problems)
    changed = {change.path for change in pf.git_changes(tmp_path, "grant-ref", "HEAD")}
    assert "src/evil.py" in changed


CERT_HEAD = "c32e17c8fdc3cc3317764ed9d691aa33fae36438"
INFRA_RED_RUN = "https://github.com/B0LK13/project-atlas/actions/runs/35013626943"
HOST = {
    "hostname": "verify-host",
    "os": "Windows 11 10.0.28000",
    "python": "3.12.10",
    "git": "2.55",
}


def _cert(
    mode: str = "local", *, head: str = CERT_HEAD, exits: tuple[int, int] = (0, 0)
) -> dict[str, Any]:
    lanes: dict[str, Any] = {}
    for name, code in zip(pf.CERT_LANES, exits, strict=True):
        lane: dict[str, Any] = {
            "commands": [{"cmd": "python -m pytest", "exit": code, "duration_seconds": 812.5}]
        }
        if mode == "ci":
            lane["run_url"] = f"{INFRA_RED_RUN}/job/104531452958"
        else:
            lane["host"] = dict(HOST)
        lanes[name] = lane
    cert: dict[str, Any] = {
        "cert": "C-1",
        "iteration": 1,
        "role": "verifier",
        "head_sha": head,
        "lane_mode": mode,
        "result": "PASS" if all(code == 0 for code in exits) else "FAIL",
        "lanes": lanes,
    }
    if mode == "local":
        cert["expires"] = "ci_available"
        cert["ci_unavailable_evidence"] = INFRA_RED_RUN
    return cert


def _write_cert(root: Path, cert: dict[str, Any], *, ci_recert: bool = False) -> None:
    body = yaml.safe_dump(cert, sort_keys=False)
    _write(root / pf.cert_path(1, ci_recert=ci_recert), f"---\n{body}---\n\nVerifier notes.\n")


@pytest.fixture
def cert_root(tmp_path: Path) -> Path:
    (tmp_path / "autonomy").mkdir()
    shutil.copyfile(REPO_ROOT / pf.POLICY_PATH, tmp_path / pf.POLICY_PATH)
    return tmp_path


def test_repo_policy_declares_required_lanes_and_the_local_fallback() -> None:
    policy = pf.load_policy(REPO_ROOT)
    assert set(policy.lanes) == set(pf.REQUIRED_LANES)
    assert policy.fallback is not None
    assert {key: policy.fallback[key] for key in pf.FALLBACK_RULE} == pf.FALLBACK_RULE


def test_policy_rejects_a_fallback_that_bends_the_rule(tmp_path: Path) -> None:
    (tmp_path / "autonomy").mkdir()
    text = (REPO_ROOT / pf.POLICY_PATH).read_text("utf-8")
    _write(tmp_path / pf.POLICY_PATH, text.replace("run_by: verifier", "run_by: executor", 1))
    with pytest.raises(pf.ConfigError, match=r"lanes\.fallback"):
        pf.load_policy(tmp_path)


@pytest.mark.parametrize("mode", ["local", "ci"])
def test_well_formed_certs_pass(mode: str) -> None:
    assert pf.check_cert(pf.load_policy(REPO_ROOT), _cert(mode), 1, "C-1.md") == []


def test_local_cert_needs_the_policy_fallback() -> None:
    no_fallback = replace(pf.load_policy(REPO_ROOT), fallback=None)
    problems = pf.check_cert(no_fallback, _cert("local"), 1, "C-1.md")
    assert any("lanes.fallback is not set" in problem for problem in problems)
    assert pf.check_cert(no_fallback, _cert("ci"), 1, "C-1.md") == []


@pytest.mark.parametrize(
    ("mode", "mutate", "message"),
    [
        ("local", lambda c: c.pop("expires"), "expires: ci_available"),
        ("local", lambda c: c.pop("ci_unavailable_evidence"), "ci_unavailable_evidence"),
        ("local", lambda c: c.update(ci_unavailable_evidence="CI was down"), "ci_unavailable"),
        ("local", lambda c: c["lanes"]["linux"]["host"].pop("hostname"), "lanes.linux.host"),
        ("local", lambda c: c["lanes"]["windows-native"].pop("host"), "windows-native.host"),
        ("local", lambda c: c["lanes"]["linux"]["commands"][0].pop("duration_seconds"), "duration"),
        ("local", lambda c: c["lanes"]["linux"]["commands"][0].pop("cmd"), "exact command"),
        ("local", lambda c: c["lanes"]["linux"]["commands"][0].update(exit="0"), "integer exit"),
        ("local", lambda c: c["lanes"]["linux"]["commands"][0].update(exit=1), "does not match"),
        ("local", lambda c: c.update(result="FAIL"), "does not match"),
        ("local", lambda c: c["lanes"].pop("windows-native"), "lanes must certify exactly"),
        ("local", lambda c: c["lanes"]["linux"].update(commands=[]), "at least one command"),
        ("local", lambda c: c.update(role="executor"), "role: verifier"),
        ("local", lambda c: c.update(head_sha=1234567), "head_sha"),
        ("local", lambda c: c.update(lane_mode="manual"), "lane_mode must be"),
        ("local", lambda c: c.update(cert="C-2"), "cert: C-1"),
        ("ci", lambda c: c["lanes"]["linux"].pop("run_url"), "lanes.linux.run_url"),
        ("ci", lambda c: c["lanes"]["linux"].update(run_url="green locally"), "run_url"),
    ],
)
def test_cert_records_every_required_field(mode: str, mutate: Any, message: str) -> None:
    cert = _cert(mode)
    mutate(cert)
    problems = pf.check_cert(pf.load_policy(REPO_ROOT), cert, 1, "C-1.md")
    assert any(message in problem for problem in problems), problems


def test_certification_accepts_a_passing_fallback_cert_on_the_head(cert_root: Path) -> None:
    policy = pf.load_policy(cert_root)
    assert pf.check_certification(cert_root, policy, 1, None, None)[1] == [
        "missing cert autonomy/certs/C-1.md: no gate without a verifier cert"
    ]
    _write_cert(cert_root, _cert("local"))
    assert pf.check_certification(cert_root, policy, 1, CERT_HEAD, None) == ("local", [])
    mode, problems = pf.check_certification(cert_root, policy, 1, "d" * 40, None)
    assert any(f"not {'d' * 40}" in problem for problem in problems)
    mode, problems = pf.check_certification(cert_root, policy, 1, CERT_HEAD, "ci")
    assert mode == "local" and any("C-1-ci.md required" in problem for problem in problems)


def test_a_failing_cert_certifies_nothing(cert_root: Path) -> None:
    _write_cert(cert_root, _cert("local", exits=(0, 1)))
    mode, problems = pf.check_certification(cert_root, pf.load_policy(cert_root), 1, None, None)
    assert mode == "local" and any("the head is not certified" in p for p in problems)


def test_ci_recertification_of_the_same_head_supersedes_the_fallback(cert_root: Path) -> None:
    policy = pf.load_policy(cert_root)
    _write_cert(cert_root, _cert("local"))
    _write_cert(cert_root, _cert("ci"), ci_recert=True)
    assert pf.check_certification(cert_root, policy, 1, CERT_HEAD, "ci") == ("ci", [])


@pytest.mark.parametrize(
    ("original", "recert", "message"),
    [
        (_cert("local"), _cert("ci", head="e" * 40), "re-certifies"),
        (_cert("local"), _cert("ci", exits=(1, 0)), "the head is not certified"),
        (_cert("local"), _cert("local"), "must declare lane_mode: ci"),
        (_cert("ci"), _cert("ci"), "only a lane_mode: local cert is re-certified"),
    ],
)
def test_ci_recertification_that_disagrees_leaves_the_head_uncertified(
    cert_root: Path, original: dict[str, Any], recert: dict[str, Any], message: str
) -> None:
    _write_cert(cert_root, original)
    _write_cert(cert_root, recert, ci_recert=True)
    _, problems = pf.check_certification(cert_root, pf.load_policy(cert_root), 1, None, None)
    assert any(message in problem for problem in problems), problems


def test_cert_command_reports_lane_mode_and_fails_closed(
    cert_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert pf.main(["--root", str(cert_root), "cert", "--iteration", "1"]) == 1
    assert "FAIL missing cert" in capsys.readouterr().out
    _write_cert(cert_root, _cert("local"))
    assert pf.main(["--root", str(cert_root), "cert", "--iteration", "1"]) == 0
    assert capsys.readouterr().out.splitlines() == ["lane_mode local", "PASS"]
    assert pf.main(["--root", str(cert_root), "cert", "--iteration", "1", "--require", "ci"]) == 1


def test_repo_policy_requires_every_ci_check_and_certifies_only_host_lanes() -> None:
    """Codex/required-checks finding: `compat` and `control-plane` must be gated too."""
    policy = pf.load_policy(REPO_ROOT)
    assert policy.lanes["linux-compat"] == "quality (ubuntu-latest, 3.13, compat)"
    assert policy.lanes["control-plane"] == "control-plane"
    assert set(pf.CERT_LANES) < set(pf.REQUIRED_LANES)
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text("utf-8")
    for suite in ("full", "windows", "compat"):
        assert f"suite: {suite}" in workflow
    assert "\n  control-plane:" in workflow


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ('    linux-compat: "quality (ubuntu-latest, 3.13, compat)"\n', "lanes.required"),
        ("  cert_lanes: [linux, windows-native]\n", "cert_lanes"),
    ],
)
def test_policy_rejects_dropping_a_required_lane_or_cert_lane(
    tmp_path: Path, mutation: str, message: str
) -> None:
    (tmp_path / "autonomy").mkdir()
    text = (REPO_ROOT / pf.POLICY_PATH).read_text("utf-8")
    assert mutation in text
    _write(tmp_path / pf.POLICY_PATH, text.replace(mutation, "", 1))
    with pytest.raises(pf.ConfigError, match=message):
        pf.load_policy(tmp_path)


def test_cert_lanes_are_the_two_host_lanes_only() -> None:
    policy = pf.load_policy(REPO_ROOT)
    cert = _cert("local")
    cert["lanes"]["control-plane"] = cert["lanes"]["linux"]
    assert any("certify exactly" in p for p in pf.check_cert(policy, cert, 1, "C-1.md"))


@pytest.fixture
def granted_clone(tmp_path: Path) -> tuple[Path, Path]:
    """An owner repo holding the grant, and the executor's clone of it."""
    owner = tmp_path / "owner"
    owner.mkdir()
    _git(owner, "init", "-q", "-b", "main")
    for rel in (pf.POLICY_PATH, pf.LOOP_PATH, pf.LEDGER_PATH):
        (owner / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / rel, owner / rel)
    _write(owner / "autonomy/directives/D-ATLAS-ITER-1.md", "# D-ATLAS-ITER-1\n\nOwner target.\n")
    _git(owner, "add", "-A")
    _git(owner, "commit", "-q", "-m", "base")
    base_sha = _git(owner, "rev-parse", "HEAD").strip()
    sha = pf.policy_sha(owner)
    grant = _grant_text(sha).replace(BASE_SHA, base_sha)
    _write(owner / pf.grant_path(1), grant)
    _git(owner, "add", "-A")
    _git(owner, "commit", "-q", "-m", "grant G-1")

    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(owner), str(clone))
    return clone, owner


def _owner_grant(clone: Path) -> Any:
    return pf.load_grant(clone, 1)


@NEEDS_GIT
def test_preflight_refreshes_the_grant_ref_before_reading_halt(
    granted_clone: tuple[Path, Path],
) -> None:
    """Codex P1: a HALT pushed after the executor's last fetch must still stop the loop."""
    clone, owner = granted_clone
    policy, grant = pf.load_policy(clone), _owner_grant(clone)
    assert pf.check_git_preflight(clone, policy, grant, "origin/main") == []

    _write(owner / pf.HALT_PATH, "owner stop\n")
    _git(owner, "add", "-A")
    _git(owner, "commit", "-q", "-m", "halt")

    assert pf.check_grant_ref_kill_switches(clone, "origin/main") == [], (
        "precondition: the stale remote-tracking ref cannot see the pushed HALT"
    )
    fresh = pf.check_git_preflight(clone, policy, grant, "origin/main")
    assert any(f"{pf.HALT_PATH} exists on" in problem for problem in fresh)


@NEEDS_GIT
def test_preflight_fails_closed_when_the_grant_ref_cannot_be_refreshed(
    granted_clone: tuple[Path, Path],
) -> None:
    clone, owner = granted_clone
    policy, grant = pf.load_policy(clone), _owner_grant(clone)
    _git(clone, "remote", "set-url", "origin", str(owner.parent / "owner-is-gone"))
    with pytest.raises(pf.ConfigError, match="fetch"):
        pf.check_git_preflight(clone, policy, grant, "origin/main")
    _git(clone, "remote", "remove", "origin")
    with pytest.raises(pf.ConfigError, match="not configured"):
        pf.refresh_grant_ref(clone, "origin/main")


@NEEDS_GIT
def test_the_granted_directive_is_pinned_to_the_owner_ref(
    granted_clone: tuple[Path, Path],
) -> None:
    """Codex P1: an executor must not retroactively rewrite the directive it was granted."""
    clone, _ = granted_clone
    policy, grant = pf.load_policy(clone), _owner_grant(clone)
    _write(
        clone / grant.directive,
        "# D-ATLAS-ITER-1\n\nTest removals authorized: all.\nScope exceptions: pyproject.toml\n",
    )
    problems = pf.check_git_preflight(clone, policy, grant, "origin/main")
    assert any(grant.directive in problem and "differs from" in problem for problem in problems)

    _write(clone / "autonomy/directives/D-ATLAS-ITER-2.md", "# D-ATLAS-ITER-2\n")
    _git(clone, "checkout", "-q", "--", grant.directive)
    assert pf.check_git_preflight(clone, policy, grant, "origin/main") == []


@pytest.mark.parametrize(
    ("appended", "message"),
    [
        (b'{"event": "packet", "iteration": 1}', "LF-terminated"),
        (b'{"event": "packet", "iteration": 1}\r\n', "LF-only"),
        ('{"event": "packet", "note": "café"}\n'.encode("latin-1"), "not UTF-8"),
        (b'{"event": "packet"\n', "is invalid"),
        (b'["packet"]\n', "is invalid"),
        (b'{"iteration": 1}\n', "is invalid"),
        (b'{"event": "verdict", "iteration": 1, "verdict": "MERGE"}\n', "is invalid"),
    ],
)
def test_appended_ledger_bytes_must_satisfy_the_stored_contract(
    appended: bytes, message: str
) -> None:
    """Codex P2: the append-only rule forbids repairing bad bytes, so refuse them at the gate."""
    problems = pf.check_ledger_append(appended, "supervisor")
    assert any(message in problem for problem in problems), problems


def test_appended_blank_lines_are_refused_at_the_gate_that_produced_them() -> None:
    """Verifier finding: the gate accepted an interior blank line that load_ledger rejects.

    That turned a clean gate rejection into a later "ledger tampering suspected" lockout on
    an append-only file nobody is allowed to repair.
    """
    appended = b'{"event": "packet", "iteration": 1}\n\n{"event": "packet", "iteration": 2}\n'
    problems = pf.check_ledger_append(appended, "executor")
    assert any("line 2 is blank" in problem for problem in problems), problems


def test_the_append_gate_accepts_exactly_what_load_ledger_accepts(tmp_path: Path) -> None:
    """The append-time contract and the stored contract must agree on every sample."""
    samples = [
        b'{"event": "packet", "iteration": 1}\n',
        b'{"event": "packet", "iteration": 1}\n{"event": "packet", "iteration": 2}\n',
        b'{"event": "packet", "iteration": 1}\n\n{"event": "packet", "iteration": 2}\n',
        b'{"event": "packet", "iteration": 1}',
        b'{"event": "packet"\n',
        b'{"event": "packet", "iteration": 1}\r\n',
        b'["packet"]\n',
    ]
    for index, sample in enumerate(samples):
        root = tmp_path / f"sample{index}"
        (root / "autonomy").mkdir(parents=True)
        (root / pf.LEDGER_PATH).write_bytes(sample)
        try:
            pf.load_ledger(root)
            stored_ok = True
        except pf.ConfigError:
            stored_ok = False
        append_ok = not pf.check_ledger_append(sample, "supervisor")
        assert append_ok is stored_ok, (sample, append_ok, stored_ok)


def test_valid_appends_still_pass_and_stay_role_limited() -> None:
    assert pf.check_ledger_append(b"", "executor") == []
    packet = b'{"event": "packet", "iteration": 1}\n'
    assert pf.check_ledger_append(packet, "executor") == []
    assert pf.check_ledger_append(packet + packet, "supervisor") == []
    assert pf.check_ledger_append(b'{"event": "resume", "by": "owner"}\n', "supervisor")


def test_owner_resume_opens_the_iteration_after_the_one_a_stop_closed() -> None:
    """Bugbot b3f1d16c: after STOP plus resume the loop had no legal next step.

    The stricter reading wins: STOP keeps iteration n closed (policy section 8.1), and the
    owner resume opens n+1 rather than un-closing n.
    """
    stopped = [_verdict(1, "STOP")]
    resumed = [*stopped, {"event": "resume", "by": "owner", "reason": "fixed"}]
    assert pf.check_ledger(stopped, 2), "without a resume the loop stays stopped"
    assert pf.check_ledger(resumed, 1) == ["iteration 1 is already closed by verdict STOP"]
    assert pf.check_ledger(resumed, 2) == []
    restopped = [*resumed, _verdict(2, "STOP")]
    assert any("loop is stopped" in problem for problem in pf.check_ledger(restopped, 3))


def test_resume_lifts_only_the_stop_it_follows() -> None:
    """Supervisor finding: a resume must not reopen or reset anything except a STOP.

    Each case records a binding verdict BEFORE an unrelated owner resume; the rule has to
    survive the resume. A ledger-wide cutoff (the first attempt at this fix) passes every
    assertion in test_owner_resume_reopens_the_iteration_a_stop_closed and fails these.
    """
    resume: dict[str, Any] = {"event": "resume", "by": "owner", "reason": "unrelated"}
    closed = [_verdict(1, "CONTINUE"), resume]
    assert pf.check_ledger(closed, 1) == ["iteration 1 is already closed by verdict CONTINUE"]
    for verdict in ("ACCELERATE", "DEFER"):
        assert pf.check_ledger([_verdict(1, verdict), resume], 1)
    paused = [_verdict(2, "CONTINUE"), _verdict(3, "OWNER_DECISION_REQUIRED"), resume]
    assert pf.check_ledger(paused, 3) == ["iteration 3 is paused awaiting an owner decision"]
    capped = [*[_verdict(2, "REDESIGN")] * 3, resume, _verdict(1, "CONTINUE")]
    assert any("retry cap" in problem for problem in pf.check_ledger(capped, 2))


def test_resume_opens_only_the_next_iteration_not_the_one_after() -> None:
    """A resume opens exactly one step: n+1, never n+2, and never re-opens n itself."""
    resume: dict[str, Any] = {"event": "resume", "by": "owner"}
    events = [_verdict(1, "CONTINUE"), _verdict(2, "STOP"), resume]
    assert pf.check_ledger(events, 2) == ["iteration 2 is already closed by verdict STOP"]
    assert pf.check_ledger(events, 3) == []
    assert any("iteration 3 has no CONTINUE" in problem for problem in pf.check_ledger(events, 4))


def test_the_gate_has_no_switch_that_skips_the_grant_ref_refresh(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Supervisor finding: --no-fetch was an unrestricted HALT bypass on the gate itself."""
    source = (REPO_ROOT / "autonomy/tools/preflight.py").read_text("utf-8")
    assert "--no-fetch" not in source
    with pytest.raises(SystemExit) as exit_info:
        pf.main(["--root", str(REPO_ROOT), "preflight", "--iteration", "1", "--no-fetch"])
    assert exit_info.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_resume_does_not_erase_rules_for_later_verdicts() -> None:
    resumed: list[dict[str, Any]] = [_verdict(1, "STOP"), {"event": "resume", "by": "owner"}]
    capped = [*resumed, *[_verdict(2, "REDESIGN")] * 3]
    assert any("retry cap" in problem for problem in pf.check_ledger(capped, 2))
    assert any(
        "loop is stopped" in problem
        for problem in pf.check_ledger([*resumed, _verdict(2, "STOP")], 2)
    )
    paused = [*resumed, _verdict(2, "OWNER_DECISION_REQUIRED")]
    assert pf.check_ledger(paused, 2) == ["iteration 2 is paused awaiting an owner decision"]


def test_verifier_may_write_the_ci_recertification_only_for_its_iteration() -> None:
    policy = pf.load_policy(REPO_ROOT)
    ok = [_change("autonomy/certs/C-1.md", "A"), _change("autonomy/certs/C-1-ci.md", "A")]
    assert pf.check_scope(policy, _grant(), ok, "verifier") == []
    assert pf.check_scope(policy, _grant(), [_change("autonomy/certs/C-2-ci.md", "A")], "verifier")
