"""AS-2.1-API-OBS-F1 — LIVE_API GET /v1/obs must not persist."""

from __future__ import annotations

from pathlib import Path

from project_atlas.obs_live import build_live_observability_receipt


def test_obs_receipt_persist_false_does_not_write(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    payload = build_live_observability_receipt(
        vault, receipt_id="api-obs", persist=False
    )
    assert payload["receipt_id"] == "api-obs"
    assert payload["authority_plane"] == "none"
    assert not (vault / "generated" / "ops" / "obs").exists()


def test_obs_receipt_default_persist_still_writes(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    build_live_observability_receipt(vault, receipt_id="ops-obs")
    assert (vault / "generated" / "ops" / "obs" / "ops-obs-live.json").is_file()
