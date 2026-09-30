"""GE leftover unique P1s: git-status exec + failed git as GOLDEN.

P1-1: unsandboxed `git status` honors repo `core.fsmonitor` / filters and
can write outside the estate while still recommending GOLDEN.
P1-2: unreadable / empty / garbage `.git` was treated as clean git
(`dirty_worktree=False`, `inspection_complete=True`) and recommended GOLDEN.

CLOUD_FIXTURE != AUTHENTIC_D_DRIVE. DISCOVER_ONLY. source_mutations == 0.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from curator import INACCESSIBLE_REASON, curate  # noqa: E402
from estate import _init_repo, fingerprint  # noqa: E402


def _row(report: dict, name: str) -> dict:
    return next(item for item in report["inventory"] if item["name"] == name)


def _qual(report: dict, path: str) -> dict:
    return next(item for item in report["qualification"] if item["path"] == path)


def test_fsmonitor_hook_does_not_run_and_is_not_golden(tmp_path: Path) -> None:
    source = tmp_path / "estate"
    source.mkdir()
    project = source / "healthy"
    _init_repo(project, readme="# Healthy\n")
    outside = tmp_path / "OUTSIDE_ONLY_FSM"
    hook = tmp_path / "fsm-hook.sh"
    hook.write_text(f"#!/bin/sh\necho escaped > {outside}\n", encoding="utf-8")
    hook.chmod(0o755)
    subprocess.run(
        ["git", "-C", str(project), "config", "core.fsmonitor", str(hook)],
        check=True,
        capture_output=True,
        text=True,
    )
    dest = tmp_path / "fsm.json"
    before = fingerprint(source)
    report = curate(source, phase="RECOMMEND", output=dest)
    assert fingerprint(source) == before
    assert not outside.exists()
    row = _row(report, "healthy")
    qual = _qual(report, row["path"])
    assert row["inspection_complete"] is False
    assert qual["golden_candidate"] is False
    assert INACCESSIBLE_REASON in qual["blockers"]
    assert row["path"] not in report["recommendation"]["recommended_golden_set"]
    assert report["source_mutations"] == 0


def test_filter_clean_smudge_does_not_run_and_is_not_golden(tmp_path: Path) -> None:
    source = tmp_path / "estate"
    source.mkdir()
    project = source / "filtered"
    _init_repo(project, readme="# Filtered\n")
    outside = tmp_path / "OUTSIDE_ONLY_FILTER"
    hook = tmp_path / "filter-hook.sh"
    hook.write_text(f"#!/bin/sh\necho escaped > {outside}\ncat\n", encoding="utf-8")
    hook.chmod(0o755)
    subprocess.run(
        ["git", "-C", str(project), "config", "filter.hunt0050.clean", str(hook)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(project), "config", "filter.hunt0050.smudge", str(hook)],
        check=True,
        capture_output=True,
        text=True,
    )
    (project / ".gitattributes").write_text("* filter=hunt0050\n", encoding="utf-8")
    dest = tmp_path / "filter.json"
    before = fingerprint(source)
    report = curate(source, phase="RECOMMEND", output=dest)
    assert fingerprint(source) == before
    assert not outside.exists()
    row = _row(report, "filtered")
    qual = _qual(report, row["path"])
    assert row["inspection_complete"] is False
    assert qual["golden_candidate"] is False
    assert INACCESSIBLE_REASON in qual["blockers"]
    assert report["source_mutations"] == 0


def test_unreadable_gitdir_is_not_golden(tmp_path: Path) -> None:
    source = tmp_path / "estate"
    source.mkdir()
    project = source / "healthy2"
    _init_repo(project, readme="# Healthy\n")
    gitdir = project / ".git"
    dest = tmp_path / "chmod.json"
    before = fingerprint(source)
    os.chmod(gitdir, 0o000)
    try:
        report = curate(source, phase="RECOMMEND", output=dest)
    finally:
        os.chmod(gitdir, stat.S_IRWXU)
    assert fingerprint(source) == before
    row = _row(report, "healthy2")
    qual = _qual(report, row["path"])
    assert row["kind"] != "git" or row["inspection_complete"] is False
    assert row["inspection_complete"] is False
    assert row["dirty_worktree"] is False
    assert qual["golden_candidate"] is False
    assert INACCESSIBLE_REASON in qual["blockers"]
    assert row["path"] not in report["recommendation"]["recommended_golden_set"]
    assert any(item.get("reason") == INACCESSIBLE_REASON for item in report["exclusions"])
    assert report["source_mutations"] == 0


def test_empty_gitdir_is_not_golden(tmp_path: Path) -> None:
    source = tmp_path / "estate"
    source.mkdir()
    project = source / "empty-git"
    project.mkdir()
    (project / "README.md").write_text("# Empty git\n", encoding="utf-8")
    (project / ".git").mkdir()
    dest = tmp_path / "empty.json"
    report = curate(source, phase="RECOMMEND", output=dest)
    row = _row(report, "empty-git")
    qual = _qual(report, row["path"])
    assert row["kind"] == "non-git"
    assert row["inspection_complete"] is False
    assert qual["golden_candidate"] is False
    assert INACCESSIBLE_REASON in qual["blockers"]


def test_garbage_gitfile_is_not_golden(tmp_path: Path) -> None:
    source = tmp_path / "estate"
    source.mkdir()
    project = source / "bad-gitfile"
    project.mkdir()
    (project / "README.md").write_text("# Bad gitfile\n", encoding="utf-8")
    (project / ".git").write_text("not-a-gitdir\n", encoding="utf-8")
    dest = tmp_path / "garbage.json"
    report = curate(source, phase="RECOMMEND", output=dest)
    row = _row(report, "bad-gitfile")
    qual = _qual(report, row["path"])
    assert row["kind"] == "non-git"
    assert row["inspection_complete"] is False
    assert qual["golden_candidate"] is False
    assert INACCESSIBLE_REASON in qual["blockers"]


def test_include_path_filter_does_not_run(tmp_path: Path) -> None:
    """ADV leftover: include.path filter must not execute via status."""
    source = tmp_path / "estate"
    source.mkdir()
    project = source / "included"
    _init_repo(project, readme="# Included\n")
    outside = tmp_path / "OUTSIDE_ONLY_INCLUDE"
    hook = tmp_path / "include-hook.sh"
    hook.write_text(f"#!/bin/sh\necho escaped > {outside}\ncat\n", encoding="utf-8")
    hook.chmod(0o755)
    extra = tmp_path / "extra-included.gitconfig"
    extra.write_text(
        f'[filter "huntleftover"]\n\tclean = {hook}\n\tsmudge = {hook}\n',
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "-C", str(project), "config", "include.path", str(extra)],
        check=True,
        capture_output=True,
        text=True,
    )
    (project / ".gitattributes").write_text("* filter=huntleftover\n", encoding="utf-8")
    dest = tmp_path / "include.json"
    before = fingerprint(source)
    report = curate(source, phase="RECOMMEND", output=dest)
    assert fingerprint(source) == before
    assert not outside.exists()
    row = _row(report, "included")
    qual = _qual(report, row["path"])
    assert row["kind"] == "git"
    assert qual["golden_candidate"] is False
    assert report["source_mutations"] == 0


def test_padded_local_filter_does_not_run(tmp_path: Path) -> None:
    """ADV leftover: filter section past a 4KiB pad must not execute."""
    source = tmp_path / "estate"
    source.mkdir()
    project = source / "padded"
    _init_repo(project, readme="# Padded\n")
    outside = tmp_path / "OUTSIDE_ONLY_PAD"
    hook = tmp_path / "pad-hook.sh"
    hook.write_text(f"#!/bin/sh\necho escaped > {outside}\ncat\n", encoding="utf-8")
    hook.chmod(0o755)
    config = project / ".git" / "config"
    text = config.read_text(encoding="utf-8")
    pad = "# " + ("x" * 5000) + "\n"
    text += pad + f'[filter "huntleftover"]\n\tclean = {hook}\n\tsmudge = {hook}\n'
    config.write_text(text, encoding="utf-8")
    (project / ".gitattributes").write_text("* filter=huntleftover\n", encoding="utf-8")
    dest = tmp_path / "pad.json"
    report = curate(source, phase="RECOMMEND", output=dest)
    assert not outside.exists()
    row = _row(report, "padded")
    qual = _qual(report, row["path"])
    assert row["kind"] == "git"
    assert qual["golden_candidate"] is False
    assert report["source_mutations"] == 0


def test_healthy_contained_git_still_golden(tmp_path: Path) -> None:
    source = tmp_path / "estate"
    source.mkdir()
    _init_repo(source / "healthy", readme="# Healthy\n")
    dest = tmp_path / "ok.json"
    before = fingerprint(source)
    report = curate(source, phase="RECOMMEND", output=dest)
    assert fingerprint(source) == before
    row = _row(report, "healthy")
    qual = _qual(report, row["path"])
    assert row["kind"] == "git"
    assert row["inspection_complete"] is True
    assert row["dirty_worktree"] is False
    assert qual["golden_candidate"] is True
    assert row["path"] in report["recommendation"]["recommended_golden_set"]
    assert report["source_mutations"] == 0
