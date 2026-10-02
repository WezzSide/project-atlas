"""EXECUTOR_RESULT_DURABILITY: a successful agent result must never be stranded.

Regression for live run 36865220709 (E6): the Claude agent step succeeded (19 turns, real
implementation), but the generic infra-runner pytest suite then failed on executor-environment
assumptions (executable fixtures under noexec /tmp, no sudo, no deploy-host archive) and the
commit/push step was skipped, leaving the result only in an ephemeral workspace. The push now
runs right after the agent; the infra suite is corroboration only: non-gating, recorded.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
TEXT = (ROOT / ".github/workflows/atlas-agent-execute.yml").read_text(encoding="utf-8")
WORKFLOW = yaml.safe_load(TEXT)
STEPS = WORKFLOW["jobs"]["execute"]["steps"]
NAMES = [s.get("name", s.get("uses")) for s in STEPS]
AGENT = "Run agent (bounded prompt, restricted tools)"
DIAG = "Claude SDK failure diagnostics (sanitized, failure-only)"
UPLOAD_DIAG = "Upload sanitized SDK diagnostic"
PUSH = "Commit and push dedicated branch"
INFRA = "Infra runner suite (corroboration only, non-gating, outcome recorded)"
SMOKE = "Smoke workload (evidence fragment)"
EVIDENCE = "Upload executor evidence"


def _step(name: str) -> dict:
    return STEPS[NAMES.index(name)]


def test_push_runs_immediately_after_the_agent_with_nothing_gating_in_between():
    i = NAMES.index(AGENT)
    assert NAMES[i + 1 : i + 4] == [DIAG, UPLOAD_DIAG, PUSH]
    # the only steps between agent and push are the failure-only diagnostics (skipped on success)
    for name in (DIAG, UPLOAD_DIAG):
        assert _step(name)["if"] == "${{ failure() && steps.agent.outcome == 'failure' }}"
    push = _step(PUSH)
    assert "if" not in push and "continue-on-error" not in push


def test_infra_suite_is_non_gating_corroboration_after_the_push():
    assert NAMES.index(PUSH) < NAMES.index(INFRA) < NAMES.index(SMOKE) < NAMES.index(EVIDENCE)
    step = _step(INFRA)
    assert step["continue-on-error"] is True and step["id"] == "infra_corroboration"
    run = step["run"]
    assert "infra/atlas-runner/tests" in run
    assert "evidence/infra-corroboration.json" in run and '"gating":false' in run
    assert "secrets." not in yaml.safe_dump(step)


def test_agent_step_and_worker_hardening_are_not_weakened():
    assert "continue-on-error" not in _step(AGENT)
    assert WORKFLOW["permissions"] == {"contents": "write"}
    for forbidden in ("sudo", "docker.sock", "noexec", "--privileged", "chmod +x", "mount "):
        assert forbidden not in "\n".join(s.get("run", "") for s in STEPS), forbidden
    assert _step(AGENT)["with"]["github_token"] == "${{ secrets.GITHUB_TOKEN }}"


def test_only_agent_push_and_envelope_persist_steps_use_the_job_token():
    users = [
        n for n, s in zip(NAMES, STEPS, strict=True) if "secrets.GITHUB_TOKEN" in yaml.safe_dump(s)
    ]
    persist = "Persist envelope-rejected result on the dedicated branch (candidate only)"
    assert sorted(users) == sorted([AGENT, PUSH, persist])


_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="executes the Linux worker bash run blocks (the executor is Linux-only)",
)


def _run_infra(tmp_path: Path, pytest_rc: int) -> tuple[subprocess.CompletedProcess, Path]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "python"
    fake.write_text(f"#!/bin/sh\necho 'fake pytest output'\nexit {pytest_rc}\n")
    fake.chmod(0o755)
    summary = tmp_path / "summary.md"
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "GITHUB_STEP_SUMMARY": str(summary)}
    proc = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", _step(INFRA)["run"]],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    return proc, tmp_path / "evidence"


@_POSIX_ONLY
def test_failing_infra_suite_records_its_exit_code_and_does_not_hide_it(tmp_path):
    proc, ev = _run_infra(tmp_path, 126)
    assert proc.returncode == 126  # visible as a (non-gating) failed step, never swallowed
    rec = json.loads((ev / "infra-corroboration.json").read_text(encoding="utf-8"))
    assert rec == {
        "suite": "infra-runner",
        "gating": False,
        "sufficient_for_acceptance": False,
        "exit_code": 126,
    }
    assert "fake pytest output" in (ev / "infra-corroboration.log").read_text(encoding="utf-8")


@_POSIX_ONLY
def test_passing_infra_suite_records_zero(tmp_path):
    proc, ev = _run_infra(tmp_path, 0)
    assert proc.returncode == 0
    assert json.loads((ev / "infra-corroboration.json").read_text())["exit_code"] == 0


# -- the push step itself: untracked-only output must be persisted, token must not linger ----------
def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _run_push(
    tmp_path: Path, files: dict[str, str]
) -> tuple[subprocess.CompletedProcess, Path, Path]:
    bare = tmp_path / "remote.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    _git(
        work,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "base",
    )
    (work / "tracked.txt").write_text("base\n")
    _git(work, "add", "tracked.txt")
    _git(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base2")
    for rel, content in files.items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(content)
    url = "https://x-access-token:SENTINEL_TOKEN@github.com/o/r.git"
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp_path),
        "GH_TOKEN_PUSH": "SENTINEL_TOKEN",
        "GH_REPOSITORY": "o/r",
        "RUN_ID": "1",
        "RUN_ATTEMPT": "1",
        "AGENT_BRANCH": "atlas/agent-1-1",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"url.{bare}.insteadOf",
        "GIT_CONFIG_VALUE_0": url,
    }
    _git(work, "remote", "add", "origin", "https://example.invalid/placeholder.git")
    proc = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", _step(PUSH)["run"]],
        cwd=work,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    return proc, work, bare


@_POSIX_ONLY
def test_untracked_only_agent_output_is_pushed(tmp_path):
    proc, _work, bare = _run_push(tmp_path, {"tests/unit/test_new.py": "def test_x():\n    pass\n"})
    assert proc.returncode == 0, proc.stderr
    assert "nothing to push" not in proc.stdout
    shown = _git(bare, "show", "atlas/agent-1-1:tests/unit/test_new.py")
    assert "def test_x" in shown


@_POSIX_ONLY
def test_modified_tracked_output_is_pushed_and_token_does_not_linger(tmp_path):
    proc, work, bare = _run_push(tmp_path, {"tracked.txt": "changed\n"})
    assert proc.returncode == 0, proc.stderr
    assert _git(bare, "show", "atlas/agent-1-1:tracked.txt") == "changed"
    assert "SENTINEL_TOKEN" not in _git(work, "remote", "get-url", "origin")
    assert "SENTINEL_TOKEN" not in (work / ".git" / "config").read_text()


@_POSIX_ONLY
def test_no_changes_pushes_nothing(tmp_path):
    proc, _work, bare = _run_push(tmp_path, {})
    assert proc.returncode == 0 and "nothing to push" in proc.stdout
    refs = subprocess.run(
        ["git", "for-each-ref"], cwd=bare, capture_output=True, text=True, check=True
    ).stdout
    assert "atlas/agent-1-1" not in refs
