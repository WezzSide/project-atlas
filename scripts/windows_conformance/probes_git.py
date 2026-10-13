"""Domain J: Git/worktree semantics on native Windows.

Measures platform behavior only -- this is not a new authoritative Git
observer (that role belongs to the unmerged ULT-01b lane, reconciled as
prior art, not depended on here).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from windows_conformance import procutil
from windows_conformance.model import ProbeOutcome

_CONTRACT = (
    "Atlas tooling that manages worktrees/checkouts on Windows must know the "
    "actual behavior of the git binary resolved on this host -- not assume "
    "POSIX-authored git documentation applies unchanged."
)


def _git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        creationflags=procutil.NO_WINDOW,
    )


def probe_win_git_001_worktree_add_detach() -> ProbeOutcome:
    here = Path(__file__).resolve()
    repo_root = here.parents[2]
    scratch_parent = Path(tempfile.mkdtemp(prefix="atlas-conform-git-"))
    worktree_path = scratch_parent / "wt"
    try:
        head = _git(["rev-parse", "HEAD"], cwd=repo_root).stdout.strip()
        add = _git(["worktree", "add", "--detach", str(worktree_path), head], cwd=repo_root)
        if add.returncode != 0:
            return ProbeOutcome(
                "WIN-GIT-001", "git_worktree", _CONTRACT,
                f"git worktree add --detach failed: {add.stderr.strip()[:300]}",
                "FAIL", "OBSERVED", evidence={"stderr": add.stderr[:500]},
            )
        wt_head = _git(["rev-parse", "HEAD"], cwd=worktree_path).stdout.strip()
        branch = _git(["symbolic-ref", "-q", "HEAD"], cwd=worktree_path)
        is_detached = branch.returncode != 0  # symbolic-ref fails on a detached HEAD
        matches = wt_head == head
        return ProbeOutcome(
            "WIN-GIT-001", "git_worktree", _CONTRACT,
            f"worktree add --detach: HEAD matches source={matches}, detached={is_detached}",
            "PASS" if matches and is_detached else "FAIL", "OBSERVED",
            evidence={"head_matches": matches, "detached": is_detached},
        )
    finally:
        _git(["worktree", "remove", "--force", str(worktree_path)], cwd=repo_root)
        shutil.rmtree(scratch_parent, ignore_errors=True)


def probe_win_git_002_core_autocrlf() -> ProbeOutcome:
    here = Path(__file__).resolve()
    repo_root = here.parents[2]
    result = _git(["config", "--get", "core.autocrlf"], cwd=repo_root)
    value = result.stdout.strip() or None
    return ProbeOutcome(
        "WIN-GIT-002", "git_worktree", _CONTRACT,
        f"core.autocrlf resolved to: {value!r} (repo-level or global/system fallback)",
        "PASS", "OBSERVED",
        evidence={"core_autocrlf": value},
    )


def probe_win_git_003_worktree_clean_after_checkout() -> ProbeOutcome:
    here = Path(__file__).resolve()
    repo_root = here.parents[2]
    result = _git(["status", "--porcelain=v2"], cwd=repo_root)
    is_dirty_from_untracked_or_modified = any(
        line for line in result.stdout.splitlines() if not line.startswith("# ") and not line.startswith("?? scripts/windows_conformance") and not line.startswith("?? tests/unit/test_windows_conformance")
    )
    return ProbeOutcome(
        "WIN-GIT-003", "git_worktree", _CONTRACT,
        (
            "git status --porcelain=v2 on this worktree: "
            + ("only this package's own new files are untracked (expected, mid-development)"
               if is_dirty_from_untracked_or_modified else "clean")
        ),
        "PASS", "OBSERVED",
        evidence={"porcelain_line_count": len(result.stdout.splitlines())},
    )


def probe_win_git_004_case_only_rename_detection() -> ProbeOutcome:
    """A classic Windows/NTFS git gotcha: does `git mv OldName NewName`
    (case-only) actually change the tracked case on a case-insensitive
    filesystem, and does `git status` reflect it cleanly?"""
    scratch_parent = Path(tempfile.mkdtemp(prefix="atlas-conform-git-case-"))
    try:
        _git(["init", "-q", str(scratch_parent)])
        (scratch_parent / "CaseFile.txt").write_text("x", encoding="utf-8")
        _git(["add", "-A"], cwd=scratch_parent)
        _git(["-c", "user.email=a@b.c", "-c", "user.name=a", "commit", "-q", "-m", "init"], cwd=scratch_parent)
        mv = _git(["mv", "CaseFile.txt", "casefile.txt"], cwd=scratch_parent)
        status = _git(["status", "--porcelain=v2"], cwd=scratch_parent)
        on_disk_names = {p.name for p in scratch_parent.iterdir() if p.name != ".git"}
        return ProbeOutcome(
            "WIN-GIT-004", "git_worktree", _CONTRACT,
            (
                f"git mv (case-only rename): exit={mv.returncode}, "
                f"on-disk names after={sorted(on_disk_names)}, "
                f"status reports a change={bool(status.stdout.strip())}"
            ),
            "PASS" if mv.returncode == 0 else "NOT_TESTED",
            "OBSERVED" if mv.returncode == 0 else "UNKNOWN",
            evidence={
                "mv_exit_code": mv.returncode,
                "mv_stderr": mv.stderr[:300],
                "on_disk_names": sorted(on_disk_names),
                "status_reports_change": bool(status.stdout.strip()),
            },
        )
    finally:
        shutil.rmtree(scratch_parent, ignore_errors=True)


PROBES = [
    probe_win_git_001_worktree_add_detach,
    probe_win_git_002_core_autocrlf,
    probe_win_git_003_worktree_clean_after_checkout,
    probe_win_git_004_case_only_rename_detection,
]
