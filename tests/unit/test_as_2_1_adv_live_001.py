"""AS-2.1 ADV live adversarial suite (non-pilot)."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.ask_atlas_live import AskAtlasLiveError, ask_atlas_live
from project_atlas.authz import AuthzError, default_operator, elevated_operator
from project_atlas.obs_live import build_live_observability_receipt
from project_atlas.openai_responses_poc import run_openai_responses_poc
from project_atlas.scheduler_live import SchedulerLiveError, dispatch_supervised_job


def test_adv_scheduler_dispatch_requires_arm(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    elev = elevated_operator("sched-op", extra={"scheduler.dispatch"})
    with pytest.raises(SchedulerLiveError, match="not-armed"):
        dispatch_supervised_job(vault, arm_id="missing", job="version", operator=elev)


def test_adv_ask_query_bounds(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(AskAtlasLiveError):
        ask_atlas_live(vault, query="")
    with pytest.raises(AskAtlasLiveError):
        ask_atlas_live(vault, query="x" * 300)


def test_adv_default_denies_vault_write() -> None:
    with pytest.raises(AuthzError, match=r"vault\.write"):
        default_operator().require("vault.write")


def test_adv_obs_includes_poc_surface(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    (vault / "generated" / "ops" / "oai-responses-poc").mkdir(parents=True)
    receipt = build_live_observability_receipt(vault, receipt_id="obs-adv")
    assert receipt["surfaces"]["oai_responses_poc"] is True
    assert receipt["rollup"] == "unknown"
    assert receipt["authority_plane"] == "none"


def test_adv_oai_poc_rate_limit_status_honest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")

    def boom(*, prompt: str, model: str, timeout_s: float = 60.0) -> None:
        from project_atlas.openai_responses_poc import OpenAIResponsesPocError

        _ = (prompt, model, timeout_s)
        raise OpenAIResponsesPocError("oai-poc-http:429")

    monkeypatch.setattr(
        "project_atlas.openai_responses_poc._call_responses_api",
        boom,
    )
    report = run_openai_responses_poc(
        vault,
        run_id="poc-rl",
        prompt="health check with read-only tools",
    )
    assert report["smoke_status"] == "LIVE_SMOKE_RATE_LIMITED"
    assert report["live_smoke"] is False
    assert report["llm_authority"] is False
    assert report["release_blocking"] is False
