"""AT3-102 — isolated provider sync status."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.atlas3 import provider_sync as sync_mod
from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.memory import connector as connector_mod
from project_atlas.atlas3.provider_sync import PACKAGE_ID, compile_provider_sync_status


def test_default_matrix_is_honest_and_not_synced() -> None:
    report = compile_provider_sync_status()
    assert report["package_id"] == PACKAGE_ID
    assert report["status"] == "derived"
    assert report["live_full_history_sync"] is False
    assert report["synchronized"] is False
    assert report["incremental_sync"] == "EXTERNAL_BLOCKED"
    assert report["connected_is_synchronized"] is False
    assert report["new_cli_command"] is False
    assert report["merge_authorization"] == "NOT_GRANTED"
    for name, row in report["providers"].items():
        assert row["synchronized"] is False
        assert row["live_full_history_sync"] is False
        assert row["incremental_sync"] == "EXTERNAL_BLOCKED"
        assert name


def test_live_history_claim_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    original = connector_mod.DEFAULT_ADAPTERS["chatgpt"]

    def _caps(provider: str | None = None) -> dict[str, object]:
        del provider
        return {
            "package": "AT3-035",
            "providers": {
                "chatgpt": {**original, "live_full_history_sync": True, "synchronized": False}
            },
        }

    monkeypatch.setattr(sync_mod, "provider_capabilities", _caps)
    with pytest.raises(Atlas3Error) as exc:
        compile_provider_sync_status()
    assert exc.value.code == "LIVE_HISTORY_SYNC_CLAIMED"


def test_synchronized_claim_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    original = connector_mod.DEFAULT_ADAPTERS["chatgpt"]

    def _caps(provider: str | None = None) -> dict[str, object]:
        del provider
        return {
            "package": "AT3-035",
            "providers": {"chatgpt": {**original, "state": "CONNECTED", "synchronized": True}},
        }

    monkeypatch.setattr(sync_mod, "provider_capabilities", _caps)
    with pytest.raises(Atlas3Error) as exc:
        compile_provider_sync_status()
    assert exc.value.code == "SYNC_AUTHORITY_CLAIMED"


def test_module_does_not_write() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/provider_sync.py").read_text(encoding="utf-8")
    for name in (
        "write_json_atomic",
        "write_text(",
        "chatgpt_bridge",
        "from project_atlas.ingestion",
        "add_parser",
    ):
        assert name not in source
