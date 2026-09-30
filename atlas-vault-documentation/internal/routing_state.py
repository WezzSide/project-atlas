"""Machine-readable routing state (AS-WP-003; FR-S011).

Human-facing pages are projections; ``routing/state/<project>.json`` is
the deterministic replay authority. Markdown parsing is never the
source of routing truth.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .safe_project import confined_file

STATE_SCHEMA_VERSION = 1


@dataclass
class RoutedEventRecord:
    """Everything a projection needs about one routed event.

    Pages render purely from these records; no Markdown re-parsing is
    required to rebuild any projection.
    """

    event_id: str
    normalized_sha256: str
    route_receipt: str
    routed_at: str
    work_package_id: str
    event_kind: str
    occurred_at: str
    title: str
    agent: str
    raw_sha256: str
    normalized_path: str  # vault-relative POSIX
    raw_path: str  # vault-relative POSIX
    status: str = "unknown"

    def as_dict(self) -> dict[str, Any]:
        return {
            "normalized_sha256": self.normalized_sha256,
            "route_receipt": self.route_receipt,
            "routed_at": self.routed_at,
            "work_package_id": self.work_package_id,
            "event_kind": self.event_kind,
            "occurred_at": self.occurred_at,
            "title": self.title,
            "agent": self.agent,
            "raw_sha256": self.raw_sha256,
            "normalized_path": self.normalized_path,
            "raw_path": self.raw_path,
            "status": self.status,
        }


@dataclass
class ProjectRoutingState:
    project_id: str
    routed_events: dict[str, RoutedEventRecord] = field(default_factory=dict)
    work_packages: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_successful_transaction: str | None = None
    projection_hash: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "project_id": self.project_id,
            "routed_events": {
                event_id: record.as_dict()
                for event_id, record in sorted(self.routed_events.items())
            },
            "work_packages": {
                wp: summary for wp, summary in sorted(self.work_packages.items())
            },
            "last_successful_transaction": self.last_successful_transaction,
            "current_projection_hash": self.projection_hash,
        }


def state_path(state_root: Path, project_id: str) -> Path:
    return confined_file(state_root, project_id, "{project_id}.json")


def load_state(state_root: Path, project_id: str) -> ProjectRoutingState:
    path = state_path(state_root, project_id)
    if not path.is_file():
        return ProjectRoutingState(project_id=project_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError(f"unsupported routing state schema in {path}")
    state = ProjectRoutingState(project_id=project_id)
    for event_id, record in data.get("routed_events", {}).items():
        state.routed_events[event_id] = RoutedEventRecord(
            event_id=event_id,
            normalized_sha256=record["normalized_sha256"],
            route_receipt=record["route_receipt"],
            routed_at=record["routed_at"],
            work_package_id=record["work_package_id"],
            event_kind=record["event_kind"],
            occurred_at=record["occurred_at"],
            title=record.get("title", ""),
            agent=record.get("agent", "unknown"),
            raw_sha256=record.get("raw_sha256", ""),
            normalized_path=record.get("normalized_path", ""),
            raw_path=record.get("raw_path", ""),
            status=record.get("status", "unknown"),
        )
    state.last_successful_transaction = data.get("last_successful_transaction")
    state.projection_hash = data.get("current_projection_hash")
    state.work_packages = dict(data.get("work_packages", {}))
    return state


def serialize_state(state: ProjectRoutingState) -> str:
    """Deterministic state serialization (sorted keys, stable layout)."""
    return json.dumps(state.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def check_replay(
    state: ProjectRoutingState, *, event_id: str, normalized_sha256: str
) -> str:
    """Classify an incoming event against routing state.

    Returns ``new``, ``replay`` (identical content already routed), or
    ``conflict`` (same event ID, different normalized content — the
    caller must fail closed).
    """
    record = state.routed_events.get(event_id)
    if record is None:
        return "new"
    if record.normalized_sha256 == normalized_sha256:
        return "replay"
    return "conflict"
