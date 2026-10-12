"""Locally observed acceptance. The worker's own report is never consulted.

Every check here is run by the supervisor, in the workspace, after the worker
has finished. That separation is the whole point: a worker that says "done" and
a workspace that satisfies the acceptance conditions are two different claims,
and only the second one is evidence.

  WORKER_REPORTED_COMPLETION != ACCEPTANCE
  ADAPTER_EXIT_ZERO != ACCEPTANCE_PASSED
  ACCEPTANCE != INDEPENDENT_VERIFICATION

Acceptance passing means "this task's declared conditions hold now". It does
not mean CI passed, that anyone independent looked at it, or that anything may
be merged.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from project_atlas.orchestration.program.adapters.base import build_child_env
from project_atlas.orchestration.program.models import (
    AcceptanceCheck,
    AcceptanceKind,
    ProgramError,
    ProgramTask,
)
from project_atlas.orchestration.program.profiles import AgentProfile

#: Bound on captured output per check, so a runaway command cannot blow up the
#: evidence file it is being recorded in.
_MAX_CAPTURE = 8192


class AcceptanceError(ProgramError):
    code = "ACCEPTANCE_ERROR"


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    kind: AcceptanceKind
    passed: bool
    detail: str
    exit_status: int | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "kind": self.kind.value,
            "passed": self.passed,
            "detail": self.detail[:_MAX_CAPTURE],
            "exit_status": self.exit_status,
        }


@dataclass(frozen=True)
class AcceptanceResult:
    passed: bool
    checks: tuple[CheckResult, ...]

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": [check.to_public_dict() for check in self.checks],
        }


def _resolve_inside(workspace: Path, relative: str) -> Path:
    """Resolve a workspace-relative path and refuse anything that escapes.

    The model already rejects ``..`` segments, but a symlink inside the
    workspace can still point outside it, and an acceptance check that reads
    or matches a file outside the workspace is not checking this task's work.
    """
    base = workspace.resolve()
    target = (base / relative).resolve()
    if not target.is_relative_to(base):
        raise AcceptanceError(
            f"acceptance path {relative!r} resolves outside the workspace",
            code="ACCEPTANCE_PATH_ESCAPE",
        )
    return target


def _run_command(
    check: AcceptanceCheck,
    *,
    workspace: Path,
    profile: AgentProfile,
) -> CheckResult:
    try:
        completed = subprocess.run(
            list(check.argv),
            cwd=str(workspace),
            env=build_child_env(profile),
            capture_output=True,
            text=True,
            timeout=check.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            check_id=check.check_id,
            kind=check.kind,
            passed=False,
            detail=f"timed out after {check.timeout_seconds}s",
            exit_status=None,
        )
    except (OSError, ValueError) as exc:
        return CheckResult(
            check_id=check.check_id,
            kind=check.kind,
            passed=False,
            detail=f"could not run acceptance command: {exc}",
            exit_status=None,
        )
    tail = ((completed.stdout or "") + (completed.stderr or ""))[-_MAX_CAPTURE:]
    return CheckResult(
        check_id=check.check_id,
        kind=check.kind,
        passed=completed.returncode == 0,
        detail=tail,
        exit_status=completed.returncode,
    )


def _git_tree_changed(workspace: Path, profile: AgentProfile) -> CheckResult:
    """Did the worker change anything tracked, at all?

    A weak condition on purpose: it is the answer to "did this run do
    literally nothing", not to "did it do the right thing". It is available as
    an acceptance kind because a task whose real conditions are checked
    elsewhere still benefits from failing loudly when the workspace is
    untouched.
    """
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(workspace),
            env=build_child_env(profile),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return CheckResult(
            check_id="git_tree_changed",
            kind=AcceptanceKind.GIT_TREE_CHANGED,
            passed=False,
            detail=f"git status failed: {exc}",
        )
    if completed.returncode != 0:
        return CheckResult(
            check_id="git_tree_changed",
            kind=AcceptanceKind.GIT_TREE_CHANGED,
            passed=False,
            detail=(completed.stderr or "")[-_MAX_CAPTURE:] or "git status failed",
            exit_status=completed.returncode,
        )
    changed = [line for line in (completed.stdout or "").splitlines() if line.strip()]
    return CheckResult(
        check_id="git_tree_changed",
        kind=AcceptanceKind.GIT_TREE_CHANGED,
        passed=bool(changed),
        detail=f"{len(changed)} changed path(s)",
        exit_status=0,
    )


def evaluate_check(
    check: AcceptanceCheck,
    *,
    workspace: Path,
    profile: AgentProfile,
) -> CheckResult:
    if check.kind is AcceptanceKind.COMMAND:
        return _run_command(check, workspace=workspace, profile=profile)

    if check.kind is AcceptanceKind.GIT_TREE_CHANGED:
        return _git_tree_changed(workspace, profile)

    assert check.path is not None  # guaranteed by the model validator
    try:
        target = _resolve_inside(workspace, check.path)
    except AcceptanceError as exc:
        return CheckResult(
            check_id=check.check_id, kind=check.kind, passed=False, detail=str(exc)
        )

    if check.kind is AcceptanceKind.FILE_EXISTS:
        exists = target.is_file()
        return CheckResult(
            check_id=check.check_id,
            kind=check.kind,
            passed=exists,
            detail=f"{check.path} {'exists' if exists else 'is missing'}",
        )

    # FILE_MATCHES
    if not target.is_file():
        return CheckResult(
            check_id=check.check_id,
            kind=check.kind,
            passed=False,
            detail=f"{check.path} is missing",
        )
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return CheckResult(
            check_id=check.check_id,
            kind=check.kind,
            passed=False,
            detail=f"could not read {check.path}: {exc}",
        )
    assert check.pattern is not None
    matched = re.search(check.pattern, content) is not None
    return CheckResult(
        check_id=check.check_id,
        kind=check.kind,
        passed=matched,
        detail=(
            f"{check.path} {'matches' if matched else 'does not match'} the pattern"
        ),
    )


def evaluate_task(
    task: ProgramTask,
    *,
    workspace: Path,
    profile: AgentProfile,
) -> AcceptanceResult:
    """Run every acceptance check for a task. All must pass.

    Every check runs even after one has failed: a partial picture of why a
    task did not pass is worse than a complete one, and the checks are read
    operations over a workspace the worker has already finished with.
    """
    results = tuple(
        evaluate_check(check, workspace=workspace, profile=profile)
        for check in task.acceptance
    )
    return AcceptanceResult(
        passed=all(result.passed for result in results), checks=results
    )


def progress_fingerprint(workspace: Path, profile: AgentProfile) -> str:
    """A digest of what is observably different about the workspace.

    Used for the no-progress check. Deliberately built from tracked-file
    status and the HEAD commit, not from a count of commits or messages: a
    worker that commits three times without changing the final tree has made
    no progress, and a worker that says a great deal has made none either.
    Returns a stable sentinel digest when the workspace is not a git
    repository, so "cannot measure" never masquerades as "unchanged".
    """
    parts: list[str] = []
    for argv in (["git", "rev-parse", "HEAD"], ["git", "status", "--porcelain"]):
        try:
            completed = subprocess.run(
                argv,
                cwd=str(workspace),
                env=build_child_env(profile),
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return "UNMEASURABLE"
        if completed.returncode != 0:
            return "UNMEASURABLE"
        parts.append(completed.stdout or "")
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest
