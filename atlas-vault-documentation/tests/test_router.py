"""Regression tests for deterministic Atlas routing (AS-WP-003)."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import capture_event
import pytest
from internal import (
    atlas_router,
    event_reader,
    generated_regions,
    provenance,
    router_validation,
    routing_state,
    transaction,
)
from internal.project_identity import ProjectIdentity


def _normalized_event(
    vault: Path,
    *,
    event_id: str = "AE-20260801T100000Z-project-atlas-route01",
    event_kind: str = "validation",
    status: str = "completed",
    occurred_at: str = "2026-08-01T10:00:00Z",
    title: str = "Routing validation",
) -> Path:
    assert capture_event.main([
        "--vault", str(vault),
        "--project-id", "PRJ-ATLAS",
        "--project-slug", "project-atlas",
        "--event-kind", event_kind,
        "--summary", title,
        "--agent", "test-agent",
        "--occurred-at", occurred_at,
        "--event-id", event_id,
        "--work-package", "AS-WP-003",
    ]) == 0
    raw = vault / "sources" / "agent-events" / "2026" / "08" / "01" / f"{event_id}.md"
    raw_hash = provenance.sha256_file(raw)
    normalized = raw.with_name(f"{event_id}.normalized.md")
    normalized.write_text(
        "---\n"
        "type: Agent Work Event\n"
        f"id: agent-event:{event_id}\n"
        f"event_kind: {event_kind}\n"
        f"status: {status}\n"
        f"occurred_at: {occurred_at}\n"
        "agent: test-agent\n"
        "session_id: unknown\n"
        "work_package: AS-WP-003\n"
        "project_id: PRJ-ATLAS\n"
        "project_slug: project-atlas\n"
        "repository: unknown\n"
        f"title: {title}\n"
        "atlas_provenance:\n"
        "  schema_version: 1\n"
        f"  raw_event_id: {event_id}\n"
        f"  raw_event_hash: sha256:{raw_hash}\n"
        "  verification_status: verified\n"
        "  normalized_at: 2026-08-01T10:01:00Z\n"
        "  tool: test\n"
        "  output_mode: sibling\n"
        f"  resource: {raw.name}\n"
        "---\n\n# Routing validation\n",
        encoding="utf-8",
    )
    return normalized


def _accepted(vault: Path, **kwargs: str) -> event_reader.RoutedEvent:
    event, problems = event_reader.read_event(
        _normalized_event(vault, **kwargs), vault_root=vault
    )
    assert not problems
    assert event is not None
    return event


def test_route_replay_and_validation_are_deterministic(vault: Path) -> None:
    event = _accepted(vault)
    identity = ProjectIdentity(
        project_id="project-atlas", display_name="Project Atlas",
        source="verified-event", confidence="authoritative",
    )
    settings = atlas_router.RoutingSettings()

    first = atlas_router.route(
        event, identity, vault_root=vault, settings=settings, redact=lambda text: text
    )
    assert first.ok
    assert first.status == "routed"
    assert (vault / "routing/state/project-atlas.json").is_file()
    before = {
        path: path.read_bytes()
        for path in vault.rglob("*") if path.is_file()
    }

    replay = atlas_router.route(
        event, identity, vault_root=vault, settings=settings, redact=lambda text: text
    )
    assert replay.ok
    assert replay.status == "idempotent-replay"
    after = {path: path.read_bytes() for path in vault.rglob("*") if path.is_file()}
    assert after == before

    report = router_validation.validate_project(vault, "project-atlas")
    assert report.ok, report.errors
    assert report.events_checked == 1
    assert report.receipts_checked == 1


def test_duplicate_event_with_changed_normalized_content_fails_closed(vault: Path) -> None:
    event = _accepted(vault)
    identity = ProjectIdentity(
        project_id="project-atlas", display_name="Project Atlas",
        source="verified-event", confidence="authoritative",
    )
    settings = atlas_router.RoutingSettings()
    assert atlas_router.route(
        event, identity, vault_root=vault, settings=settings, redact=lambda text: text
    ).ok

    changed = event.normalized_path.read_text(encoding="utf-8").replace(
        "# Routing validation", "# Changed validation"
    )
    event.normalized_path.write_text(changed, encoding="utf-8")
    changed_event, problems = event_reader.read_event(
        event.normalized_path, vault_root=vault
    )
    assert not problems
    assert changed_event is not None
    assert changed_event.normalized_sha256 != event.normalized_sha256

    result = atlas_router.route(
        changed_event, identity, vault_root=vault, settings=settings,
        redact=lambda text: text,
    )
    assert not result.ok
    assert result.category == "duplicate-conflict"
    state = (vault / "routing/state/project-atlas.json").read_text(encoding="utf-8")
    assert changed_event.normalized_sha256 not in state

    with ThreadPoolExecutor(max_workers=2) as executor:
        concurrent_results = list(executor.map(
            lambda candidate: atlas_router.route(
                candidate, identity, vault_root=vault,
                settings=atlas_router.RoutingSettings(lock_wait_seconds=5),
                redact=lambda text: text,
            ),
            [event, changed_event],
        ))
    assert sorted(result.status for result in concurrent_results) == [
        "failed", "idempotent-replay"
    ]
    assert sorted(result.category or "replay" for result in concurrent_results) == [
        "duplicate-conflict", "replay"
    ]


def test_fresh_vault_sequence_projects_completion_and_links(vault: Path) -> None:
    identity = ProjectIdentity(
        project_id="project-atlas", display_name="Project Atlas",
        source="verified-event", confidence="authoritative",
    )
    settings = atlas_router.RoutingSettings()
    events = [
        _accepted(
            vault,
            event_id="AE-20260801T100000Z-project-atlas-plan01",
            event_kind="implementation", status="in-progress",
            occurred_at="2026-08-01T10:00:00Z", title="Implementation",
        ),
        _accepted(
            vault,
            event_id="AE-20260801T110000Z-project-atlas-valid01",
            event_kind="validation", status="completed",
            occurred_at="2026-08-01T11:00:00Z", title="Validation",
        ),
        _accepted(
            vault,
            event_id="AE-20260801T120000Z-project-atlas-done01",
            event_kind="completion", status="completed",
            occurred_at="2026-08-01T12:00:00Z", title="Completion",
        ),
    ]
    for event in events:
        result = atlas_router.route(
            event, identity, vault_root=vault, settings=settings,
            redact=lambda text: text,
        )
        assert result.ok, result.message

    state = routing_state.load_state(vault / "routing" / "state", "project-atlas")
    assert len(state.routed_events) == 3
    assert state.work_packages["AS-WP-003"]["status"] == "completed"
    assert state.work_packages["AS-WP-003"]["validation_status"] == "passed"
    assert state.work_packages["AS-WP-003"]["completion_event"] == events[2].event_id

    report = router_validation.validate_project(vault, "project-atlas")
    assert report.ok, report.errors
    log = (vault / "projects/project-atlas/project-log.md").read_text(encoding="utf-8")
    index = (vault / "projects/project-atlas/index.md").read_text(encoding="utf-8")
    assert events[2].event_id in index
    assert log.index("Completion") < log.index("Validation") < log.index("Implementation")
    assert log.count("work-packages/AS-WP-003.md") == 3
    assert len(list((vault / "routing/receipts").glob("*.yaml"))) == 3


def test_transaction_failure_leaves_no_partial_state_and_records_failure(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = _accepted(vault)
    identity = ProjectIdentity(
        project_id="project-atlas", display_name="Project Atlas",
        source="verified-event", confidence="authoritative",
    )
    original = transaction.Transaction.promote

    def fail_before_promotion(self: transaction.Transaction) -> None:
        raise OSError("injected promotion failure")

    monkeypatch.setattr(transaction.Transaction, "promote", fail_before_promotion)
    result = atlas_router.route(
        event, identity, vault_root=vault, settings=atlas_router.RoutingSettings(),
        redact=lambda text: text,
    )
    assert not result.ok
    assert result.category == "promotion-failed"
    assert not (vault / "routing/state/project-atlas.json").is_file()
    assert not (vault / "projects/project-atlas/project-log.md").is_file()
    assert not list((vault / "routing/receipts").glob("*.yaml"))
    failures = list((vault / "routing/failures").glob("*.json"))
    assert len(failures) == 1

    monkeypatch.setattr(transaction.Transaction, "promote", original)
    retry = atlas_router.route(
        event, identity, vault_root=vault, settings=atlas_router.RoutingSettings(),
        redact=lambda text: text,
    )
    assert retry.ok


def test_transaction_rejects_stale_expected_hash(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_text("old", encoding="utf-8")
    txn = transaction.Transaction()
    txn.stage(target, "new", current="old")
    target.write_text("changed", encoding="utf-8")
    with pytest.raises(transaction.PreconditionError):
        txn.promote()
    assert target.read_text(encoding="utf-8") == "changed"


def test_concurrent_events_and_replays_are_unique(vault: Path) -> None:
    identity = ProjectIdentity(
        project_id="project-atlas", display_name="Project Atlas",
        source="verified-event", confidence="authoritative",
    )
    settings = atlas_router.RoutingSettings(lock_wait_seconds=5)
    events = [
        _accepted(
            vault,
            event_id=f"AE-20260801T10000{i}Z-project-atlas-conc0{i}",
            event_kind="implementation", status="in-progress",
            occurred_at=f"2026-08-01T10:00:0{i}Z", title=f"Concurrent {i}",
        )
        for i in (1, 2)
    ]

    def route_one(event: event_reader.RoutedEvent) -> atlas_router.RouteResult:
        return atlas_router.route(
            event, identity, vault_root=vault, settings=settings,
            redact=lambda text: text,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        different_results = list(executor.map(route_one, events))
    assert all(result.ok for result in different_results)

    same_event = _accepted(
        vault,
        event_id="AE-20260801T130000Z-project-atlas-conc03",
        event_kind="implementation", status="in-progress",
        occurred_at="2026-08-01T13:00:00Z", title="Same event",
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        replay_results = list(executor.map(route_one, [same_event, same_event]))
    assert sorted(result.status for result in replay_results) == [
        "idempotent-replay", "routed"
    ]
    report = router_validation.validate_project(vault, "project-atlas")
    assert report.ok, report.errors
    assert report.events_checked == 3


def test_generated_regions_preserve_human_text_and_fail_closed() -> None:
    original = (
        "human heading\n"
        "before\n"
        "<!-- ATLAS:BEGIN status schema=1 -->\nold\n<!-- ATLAS:END status -->\n"
        "after\n"
    )
    updated = generated_regions.update_regions(original, {"status": "new"})
    assert updated.startswith("human heading\nbefore\n")
    assert updated.endswith("\nafter\n")
    assert "new" in updated
    assert generated_regions.update_regions(updated, {"status": "new"}) == updated
    for malformed in (
        original.replace("ATLAS:END status", "ATLAS:END other"),
        original.replace("<!-- ATLAS:BEGIN status", "<!-- ATLAS:BEGIN status" , 1)
        + "<!-- ATLAS:BEGIN status schema=1 -->\nx\n<!-- ATLAS:END status -->\n",
    ):
        try:
            generated_regions.parse_regions(malformed)
        except generated_regions.RegionError:
            pass
        else:
            raise AssertionError("malformed generated region was accepted")


def test_escaped_failures_root_does_not_write_outside_vault(tmp_path: Path) -> None:
    """P1: discovered routing.failures_root must not write outside the vault.

    _write_failure previously skipped ensure_inside_root. A held lock
    triggers lock-unavailable, which used to materialize the failure
    JSON at vault/../outside/.
    """
    vault = (tmp_path / "vault").resolve()
    outside = (tmp_path / "outside").resolve()
    vault.mkdir()
    outside.mkdir()
    event = _accepted(vault)
    identity = ProjectIdentity(
        project_id="project-atlas",
        display_name="Project Atlas",
        source="verified-event",
        confidence="authoritative",
    )
    lock = vault / "routing" / "state" / "project-atlas.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("held\n", encoding="utf-8")
    rel_fail = os.path.relpath(outside, vault)
    result = atlas_router.route(
        event,
        identity,
        vault_root=vault,
        settings=atlas_router.RoutingSettings(
            failures_root=rel_fail,
            lock_wait_seconds=0.05,
            stale_lock_seconds=3600,
        ),
        redact=lambda text: text,
    )
    assert result.status == "failed"
    assert result.category == "lock-unavailable"
    assert list(outside.rglob("*")) == []
    assert not (vault / "routing" / "failures").exists()
