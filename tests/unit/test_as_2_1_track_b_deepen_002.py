"""AS-2.1 Track B deepen-002: collab close + provider ADV."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.authz import AuthzError, elevated_operator
from project_atlas.collab_live import (
    CollabError,
    append_collab_action,
    close_collab_session,
    open_collab_session,
)
from project_atlas.provider_live import ProviderLiveError, run_local_model_adapter


def test_collab_close_blocks_further_actions(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    open_collab_session(vault, session_id="sess-b", subject="item-1")
    closed = close_collab_session(vault, session_id="sess-b")
    assert closed["closed"] is True
    with pytest.raises(CollabError, match="closed"):
        append_collab_action(vault, session_id="sess-b", action_name="note", detail="late")


def test_collab_comment_thread_still_disabled(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(CollabError, match="not-enabled"):
        open_collab_session(
            vault,
            session_id="sess-c",
            kind="comment-thread",  # type: ignore[arg-type]
            subject="x",
        )


def test_provider_live_requires_capability(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(AuthzError):
        run_local_model_adapter(vault, run_id="r1", prompt="hello")


def test_provider_live_rejects_empty_and_secrets(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    op = elevated_operator("prov", extra={"provider.live"})
    with pytest.raises(ProviderLiveError, match="prompt-invalid"):
        run_local_model_adapter(vault, run_id="r1", prompt="  ", operator=op)
    with pytest.raises(ProviderLiveError, match="secret"):
        run_local_model_adapter(
            vault,
            run_id="r2",
            prompt="token AKIAIOSFODNN7EXAMPLE leak",
            operator=op,
        )


def test_provider_live_quarantines(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    op = elevated_operator("prov", extra={"provider.live"})
    report = run_local_model_adapter(
        vault, run_id="r3", prompt="summarize vault health", operator=op
    )
    assert report["llm_authority"] is False
    assert report["quarantine"]["status"] == "quarantined"
