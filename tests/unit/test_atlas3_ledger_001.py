"""AT3-014 universal event ledger."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.events import normalize_engineering_event
from project_atlas.atlas3.ledger import append_event, list_events, query_events


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    return vault


def test_append_replay_and_no_ops_events_write(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    event = normalize_engineering_event(
        project_id="harbor-api",
        kind="failure",
        source_plane="engineering",
        summary="CI failed on kdiff",
    )
    first = append_event(vault, "harbor-api", event)
    second = append_event(vault, "harbor-api", event)
    assert first["idempotency"] == "appended"
    assert second["idempotency"] == "replay"
    assert len(list_events(vault, "harbor-api")) == 1
    assert not (vault / "generated" / "ops" / "events").exists()
    assert (vault / "generated" / "ops" / "atlas3" / "ledger" / "harbor-api.jsonl").is_file()
    typed = query_events(vault, project_id="harbor-api", event_type="AGENT_FAILED")
    assert len(typed) == 1
    assert typed[0]["event_type"] == "AGENT_FAILED"


def test_append_same_event_id_altered_payload_fails_closed(tmp_path: Path) -> None:
    """AT3-014-F2: append-path collision is not a silent replay."""
    vault = _vault(tmp_path)
    event = normalize_engineering_event(
        project_id="harbor-api",
        kind="failure",
        source_plane="engineering",
        summary="CI failed on kdiff",
    )
    append_event(vault, "harbor-api", event)
    collided = dict(event)
    collided["summary"] = "CI failed on kdiff ALTERED"
    collided["content_hash"] = "sha256:" + ("ab" * 32)
    with pytest.raises(Atlas3Error) as exc:
        append_event(vault, "harbor-api", collided)
    assert exc.value.code == "EVENT_ID_COLLISION"
    assert len(list_events(vault, "harbor-api")) == 1
    assert list_events(vault, "harbor-api")[0]["summary"] == "CI failed on kdiff"


def test_query_filters_observed_time_and_corrupt_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    append_event(
        vault,
        "harbor-api",
        event_type="TEST_PASSED",
        source_plane="engineering",
        summary="unit ok",
        observed_at="2026-01-01T00:00:00Z",
    )
    append_event(
        vault,
        "harbor-api",
        event_type="TEST_FAILED",
        source_plane="engineering",
        summary="iv failed",
        observed_at="2026-08-01T00:00:00Z",
    )
    later = query_events(
        vault,
        project_id="harbor-api",
        observed_from="2026-07-01T00:00:00Z",
    )
    assert [item["event_type"] for item in later] == ["TEST_FAILED"]
    path = vault / "generated" / "ops" / "atlas3" / "ledger" / "harbor-api.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "not-json\n", encoding="utf-8")
    with pytest.raises(Atlas3Error) as exc:
        list_events(vault, "harbor-api")
    assert exc.value.code == "LEDGER_CORRUPT"
