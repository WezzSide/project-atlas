"""AS-ORCH-TRUST-ID-F1 — origin identity ignores credential userinfo.

Independently reproduced on main b87b4a22: ``git remote get-url`` applies
insteadOf rewrites that inject ``user:token@host`` userinfo. The colon then
made ``normalize_repository_identity`` reject the canonical GitHub remote
as unsafe, so trust-check-before-merge failed closed on a real origin.
Synthetic credentials only. Raw userinfo must never appear in identity.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.orchestration.autonomy.models import CANONICAL_REPOSITORY_IDENTITY
from project_atlas.orchestration.autonomy.trust import (
    TrustError,
    normalize_repository_identity,
)


def test_normalize_strips_https_userinfo() -> None:
    identity = normalize_repository_identity(
        "https://x-access-token:fake-token-not-a-secret@github.com/B0LK13/project-atlas.git"
    )
    assert identity == CANONICAL_REPOSITORY_IDENTITY
    assert "fake-token" not in identity
    assert "x-access-token" not in identity


def test_normalize_strips_ssh_git_userinfo_style() -> None:
    identity = normalize_repository_identity(
        "ssh://git:fake-token-not-a-secret@github.com/b0lk13/project-atlas.git"
    )
    assert identity == CANONICAL_REPOSITORY_IDENTITY
    assert "fake-token" not in identity


def test_normalize_still_rejects_traversal_and_backslash() -> None:
    with pytest.raises(TrustError) as exc:
        normalize_repository_identity("https://github.com/../escape")
    assert exc.value.code == "REPO_IDENTITY_UNVERIFIABLE"
    with pytest.raises(TrustError) as exc:
        normalize_repository_identity(r"https://github.com/foo\\bar")
    assert exc.value.code == "REPO_IDENTITY_UNVERIFIABLE"


def test_live_observer_reads_configured_origin_not_rewritten_get_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from project_atlas.orchestration.autonomy.trust import LiveGitObserver

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    def fake_run(self: LiveGitObserver, *args: str) -> str:
        if args == ("config", "--get", "remote.origin.url"):
            return "https://github.com/B0LK13/project-atlas.git"
        if args == ("remote", "get-url", "origin"):
            return "https://x-access-token:fake-token-not-a-secret@github.com/B0LK13/project-atlas.git"
        raise AssertionError(args)

    monkeypatch.setattr(LiveGitObserver, "_run", fake_run)
    observer = LiveGitObserver(repo)
    assert observer.repository_identity() == CANONICAL_REPOSITORY_IDENTITY
