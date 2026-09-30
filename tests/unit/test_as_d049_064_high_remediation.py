"""D-064 HIGH remediations: symlink-loop resilience + git-remote secret hygiene.

Trace: D-PROJECT-ATLAS-CLOUD-D049-OVERNIGHT-064-FINAL
Invalidates frozen tip 9c71cc2 / tree 10539a86 for these HIGHs:
  - SYMLINK_LOOP_UNBOUNDED (RuntimeError crash)
  - GIT_REMOTE_PASSWORD_ECHO (credential userinfo in fingerprints/reports)
"""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.estate_discovery import (
    discover_estate,
    sanitize_git_remote_url,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_sanitize_git_remote_url_strips_userinfo() -> None:
    assert (
        sanitize_git_remote_url("https://user:SECRETKEY@example.com/repo.git")
        == "https://example.com/repo.git"
    )
    assert (
        sanitize_git_remote_url("https://ghp_tokenOnly@github.com/org/repo.git")
        == "https://github.com/org/repo.git"
    )
    assert (
        sanitize_git_remote_url("https://example.com/clean.git") == "https://example.com/clean.git"
    )
    # scp-like form left unchanged (no password embedding convention)
    assert sanitize_git_remote_url("git@github.com:org/repo.git") == "git@github.com:org/repo.git"


def test_discover_estate_does_not_echo_git_remote_password(tmp_path: Path) -> None:
    """HIGH: credential-bearing remotes must not appear in discovery reports."""
    estate = tmp_path / "estate"
    proj = estate / "cred-svc"
    secret = "SECRETKEY_D064_PLANTED"
    remote = f"https://user:{secret}@example.com/cred-svc.git"
    _write(proj / "README.md", "# cred-svc\n")
    _write(proj / "package.json", '{"name":"cred-svc"}\n')
    (proj / ".git").mkdir(parents=True)
    _write(proj / ".git" / "config", f'[remote "origin"]\n\turl = {remote}\n')

    report = discover_estate(estate, include_knowledge=False)
    blob = json.dumps(report, sort_keys=True)
    assert secret not in blob
    assert f"user:{secret}" not in blob
    # Sanitized remote may still appear as fingerprint evidence.
    projects = report["candidates"]["projects"]
    assert len(projects) == 1
    remote_fp = projects[0]["fingerprint"].get("git_remote")
    assert remote_fp == "https://example.com/cred-svc.git"


def test_discover_estate_survives_mutual_symlink_loop(tmp_path: Path) -> None:
    """HIGH: mutual dir symlinks must not crash discover_estate."""
    estate = tmp_path / "estate"
    a = estate / "loop-a"
    b = estate / "loop-b"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    _write(a / "README.md", "# loop-a\n")
    _write(b / "README.md", "# loop-b\n")
    (a / "to-b").symlink_to(b, target_is_directory=True)
    (b / "to-a").symlink_to(a, target_is_directory=True)
    # Real project alongside the loop to ensure scan still produces candidates.
    real = estate / "real-proj"
    _write(real / "README.md", "# real\n")
    (real / ".git").mkdir()
    _write(real / ".git" / "config", '[remote "origin"]\n\turl = https://example.com/real.git\n')

    report = discover_estate(estate, include_knowledge=False)
    assert report["scan"]["scan_complete"] is True or report["scan"].get("truncation_reason")
    paths = {c["path"] for c in report["candidates"]["projects"]}
    assert any("real-proj" in p for p in paths)
    # Must not raise; loop edges counted as escapes / ignored, never allowed.
    security = report.get("security") or {}
    assert int(security.get("unsafe_path_escapes_allowed", 0)) == 0
