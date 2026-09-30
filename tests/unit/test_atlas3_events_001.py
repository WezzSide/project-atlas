"""AT3-003 engineering event model."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.events import (
    EVENT_TYPES,
    ingest_existing_agent_event,
    normalize_engineering_event,
)

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_FIELDS = (
    "event_id",
    "project_id",
    "event_type",
    "source",
    "source_id",
    "observed_at",
    "valid_time",
    "actor",
    "object_refs",
    "evidence_refs",
    "content_hash",
    "authority_class",
    "schema_version",
)


def test_normalize_is_deterministic() -> None:
    first = normalize_engineering_event(
        project_id="harbor-api",
        kind="commit",
        source_plane="engineering",
        summary="Landed freshness fix",
        subject_id="abc123",
    )
    second = normalize_engineering_event(
        project_id="harbor-api",
        kind="commit",
        source_plane="engineering",
        summary="Landed freshness fix",
        subject_id="abc123",
    )
    assert first["event_id"] == second["event_id"]
    assert first["content_hash"].startswith("sha256:")
    assert first["honesty"]["full_live_demo_ready"] is False


def test_unknown_kind_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        normalize_engineering_event(
            project_id="harbor-api",
            kind="magic",
            source_plane="engineering",
            summary="nope",
        )
    assert exc.value.code == "UNKNOWN_EVENT_KIND"


def test_wrap_agent_event_does_not_mutate_source() -> None:
    raw = {"event_type": "decision", "event_id": "evt-1", "summary": "Keep Postgres 15"}
    wrapped = ingest_existing_agent_event(raw, project_id="harbor-api")
    assert wrapped["kind"] == "decision"
    assert wrapped["source_plane"] == "agent_event"
    assert wrapped["event_type"] == "DECISION_RECORDED"
    assert raw["event_type"] == "decision"


def test_canonical_event_types_and_schema() -> None:
    assert len(EVENT_TYPES) == 21
    event = normalize_engineering_event(
        project_id="harbor-api",
        event_type="COMMIT_CREATED",
        source="engineering",
        summary="Landed freshness fix",
        source_id="abc123",
        observed_at="2026-08-01T00:00:00Z",
        object_refs=["commit:abc123"],
        evidence_refs=["git:abc123"],
    )
    for field in REQUIRED_FIELDS:
        assert field in event
    schema = json.loads(
        (ROOT / "docs/atlas-3/contracts/engineering-event.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.validate(event, schema)


def test_kind_and_event_type_mismatch_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        normalize_engineering_event(
            project_id="harbor-api",
            kind="commit",
            event_type="PR_MERGED",
            source_plane="engineering",
            summary="mismatch",
        )
    assert exc.value.code == "EVENT_TYPE_KIND_MISMATCH"
