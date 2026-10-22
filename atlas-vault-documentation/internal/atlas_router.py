"""The Atlas router (AS-WP-003 Phases 3-8; AS-013, AS-014, AS-016, AS-017).

Turns one verified normalized event into canonical project projections
inside a per-project transaction:

    accept → identify → plan → stage → validate staged → promote → receipt

Projections are pure functions of routing state; replay renders
byte-identical content and produces no mutation. A duplicate event ID
with different normalized content fails closed. Raw and normalized
evidence are never modified.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from collections.abc import Callable
from pathlib import Path
from typing import Any

from internal import (
    atlas_links,
    event_reader,
    generated_regions,
    project_log,
    project_pages,
    project_identity,
    provenance,
    route_planner,
    routing_state,
    transaction,
    verification,
    work_package_projection,
)
from internal.project_identity import ProjectIdentity


@dataclass(frozen=True)
class RoutingSettings:
    projects_root: str = "projects"
    state_root: str = "routing/state"
    receipts_root: str = "routing/receipts"
    failures_root: str = "routing/failures"
    require_verified_normalization: bool = True
    event_placement: str = "reference"
    project_log_enabled: bool = True
    work_package_projection: bool = True
    project_index_projection: bool = True
    stale_lock_seconds: float = 300
    lock_wait_seconds: float = 30


@dataclass(frozen=True)
class RouteResult:
    ok: bool
    status: str  # routed | idempotent-replay | failed
    event_id: str
    project_id: str | None
    receipt_id: str | None
    plan_sha256: str | None
    transaction_id: str | None
    idempotent_replay: bool
    created: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()
    category: str | None = None
    message: str = ""
    problems: tuple[str, ...] = ()
    plan: dict[str, Any] | None = field(default=None, compare=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "event_id": self.event_id,
            "project_id": self.project_id,
            "receipt_id": self.receipt_id,
            "plan_sha256": self.plan_sha256,
            "transaction_id": self.transaction_id,
            "idempotent_replay": self.idempotent_replay,
            "created": list(self.created),
            "modified": list(self.modified),
            "category": self.category,
            "message": self.message,
            "problems": list(self.problems),
        }


def receipt_id_for(event: event_reader.RoutedEvent, project_id: str) -> str:
    digits = re.sub(r"[^0-9]", "", event.occurred_at)
    date = f"{digits[:8]}T{digits[8:14]}Z" if len(digits) >= 14 else "unknown"
    digest = hashlib.sha256(
        f"{event.event_id}|{event.normalized_sha256}".encode()
    ).hexdigest()[:8]
    return f"AR-{date}-{project_id}-{digest}"


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _yaml_lines(data: Any, indent: int) -> list[str]:
    pad = "  " * indent
    if isinstance(data, dict):
        lines: list[str] = []
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{pad}{key}:")
                lines.extend(_yaml_lines(value, indent + 1))
            else:
                lines.append(f"{pad}{key}: {_yaml_scalar(value)}")
        return lines
    if isinstance(data, list):
        if not data:
            return [f"{pad}[]"]
        lines = []
        for item in data:
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}-")
                lines.extend(_yaml_lines(item, indent + 1))
            else:
                lines.append(f"{pad}- {_yaml_scalar(item)}")
        return lines
    return [f"{pad}{_yaml_scalar(data)}"]


def render_receipt(receipt: dict[str, Any]) -> str:
    return "\n".join(_yaml_lines(receipt, 0)) + "\n"


def _write_failure(
    vault_root: Path,
    settings: RoutingSettings,
    *,
    event_id: str,
    project_id: str | None,
    category: str,
    message: str,
    redact: Callable[[str], str],
) -> Path:
    record = {
        "type": "routing-failure",
        "schema_version": 1,
        "event_id": event_id,
        "project_id": project_id,
        "category": category,
        "message": redact(message),
        "recorded_at": provenance.utc_timestamp(),
    }
    target = vault_root / settings.failures_root / f"{event_id}.{category}.json"
    # Fail closed: discovered routing.failures_root must not write outside
    # the vault. Success-path staging already calls ensure_inside_root;
    # the failure writer previously skipped that check.
    verification.ensure_inside_root(vault_root, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    provenance.atomic_replace(
        target, json.dumps(record, ensure_ascii=False, indent=2) + "\n"
    )
    return target


def _validate_staged(
    staged: dict[str, str], vault_root: Path
) -> list[str]:
    """Validate the staged tree before promotion: regions + links."""
    problems: list[str] = []
    for rel, content in staged.items():
        if rel.endswith(".md"):
            try:
                generated_regions.parse_regions(content)
            except generated_regions.RegionError as exc:
                problems.append(f"{rel}: {exc}")
            for link in atlas_links.extract_links(content):
                target = atlas_links.resolve_link(link, rel)
                if target.startswith("../") or target.startswith("/"):
                    problems.append(f"{rel}: link escapes vault: {link}")
                elif target not in staged and not (vault_root / target).is_file():
                    problems.append(f"{rel}: unresolved link: {link}")
    return problems


def route(
    event: event_reader.RoutedEvent,
    identity: ProjectIdentity,
    *,
    vault_root: Path,
    settings: RoutingSettings,
    redact: Callable[[str], str],
) -> RouteResult:
    """Route one accepted event inside a per-project transaction."""
    vault_root = vault_root.resolve()
    project_rel = f"{settings.projects_root}/{identity.project_id}"
    lock_path = vault_root / settings.state_root / f"{identity.project_id}.lock"

    receipt_id = receipt_id_for(event, identity.project_id)

    def fail(category: str, message: str, problems: tuple[str, ...] = ()) -> RouteResult:
        try:
            _write_failure(
                vault_root, settings, event_id=event.event_id,
                project_id=identity.project_id, category=category, message=message,
                redact=redact,
            )
        except ValueError:
            # Escaped failures_root must not write outside; still return failed.
            pass
        return RouteResult(
            ok=False, status="failed", event_id=event.event_id,
            project_id=identity.project_id, receipt_id=None, plan_sha256=None,
            transaction_id=None, idempotent_replay=False,
            category=category, message=message, problems=problems,
        )

    try:
        with transaction.ProjectLock(
            lock_path,
            stale_seconds=settings.stale_lock_seconds,
            wait_seconds=settings.lock_wait_seconds,
        ):
            state = routing_state.load_state(
                vault_root / settings.state_root, identity.project_id
            )
            replay = routing_state.check_replay(
                state, event_id=event.event_id,
                normalized_sha256=event.normalized_sha256,
            )
            if replay == "conflict":
                return fail(
                    "duplicate-conflict",
                    f"event {event.event_id} already routed with different content",
                )
            if replay == "replay":
                record = state.routed_events[event.event_id]
                return RouteResult(
                    ok=True, status="idempotent-replay", event_id=event.event_id,
                    project_id=identity.project_id, receipt_id=record.route_receipt,
                    plan_sha256=None, transaction_id=state.last_successful_transaction,
                    idempotent_replay=True, message="event already routed; no mutation",
                )

            plan = route_planner.build_plan(
                event, identity,
                projects_root=settings.projects_root,
                state_root=settings.state_root,
                receipts_root=settings.receipts_root,
                receipt_id=receipt_id,
                work_package_projection=settings.work_package_projection,
                project_index_projection=settings.project_index_projection,
            )
            plan_problems = route_planner.validate_plan(plan)
            if plan_problems:
                return fail("invalid-plan", "; ".join(plan_problems))

            plan_digest = route_planner.plan_hash(plan)
            transaction_id = (
                f"ATX-{provenance.utc_timestamp().replace('-', '').replace(':', '')[:15]}"
                f"-{plan_digest[:8]}"
            )
            routed_at = provenance.utc_timestamp()

            # --- stage state mutation ------------------------------------
            record = routing_state.RoutedEventRecord(
                event_id=event.event_id,
                normalized_sha256=event.normalized_sha256,
                route_receipt=receipt_id,
                routed_at=routed_at,
                work_package_id=event.work_package,
                event_kind=event.event_kind,
                occurred_at=event.occurred_at,
                title=event.title,
                agent=event.agent,
                raw_sha256=event.raw_event_hash,
                normalized_path=_rel(vault_root, event.normalized_path),
                raw_path=_rel(vault_root, event.raw_event_path),
                status=event.status,
            )
            state.routed_events[event.event_id] = record
            if event.work_package != "unknown":
                wp_events = [
                    r for r in state.routed_events.values()
                    if r.work_package_id == event.work_package
                ]
                state.work_packages[event.work_package] = (
                    work_package_projection.compute_work_package(wp_events)
                )
            state.last_successful_transaction = transaction_id

            # --- render staged pages --------------------------------------
            staged: dict[str, str] = {}
            log_rel = f"{project_rel}/project-log.md"
            index_rel = f"{project_rel}/index.md"
            event_rel = project_pages.event_page_rel(project_rel, record)

            try:
                staged[event_rel] = project_pages.render_event_page(
                    record, state=state, project_rel=project_rel,
                    from_file_rel=event_rel,
                )
                log_path = vault_root / log_rel
                staged[log_rel] = project_log.render_log_page(
                    state, display_name=identity.display_name,
                    from_file_rel=log_rel, project_rel=project_rel,
                    existing=_read(log_path),
                )
                if settings.work_package_projection and event.work_package != "unknown":
                    wp_rel = f"{project_rel}/work-packages/{event.work_package}.md"
                    wp_events = [
                        r for r in state.routed_events.values()
                        if r.work_package_id == event.work_package
                    ]
                    staged[wp_rel] = work_package_projection.render_work_package_page(
                        event.work_package,
                        state.work_packages[event.work_package],
                        wp_events,
                        state=state, project_rel=project_rel,
                        from_file_rel=wp_rel,
                        existing=_read(vault_root / wp_rel),
                    )
                if settings.project_index_projection:
                    staged[index_rel] = project_pages.render_index_page(
                        state, identity, project_rel=project_rel,
                        from_file_rel=index_rel,
                        existing=_read(vault_root / index_rel),
                    )
            except generated_regions.RegionError as exc:
                return fail(
                    "region-conflict",
                    f"generated region failure in {exc.region_id}: {exc}",
                    problems=(f"category={exc.category}",),
                )

            state_rel = f"{settings.state_root}/{identity.project_id}.json"
            staged[state_rel] = routing_state.serialize_state(state)

            receipt_rel = f"{settings.receipts_root}/{receipt_id}.yaml"

            # --- validate staged tree --------------------------------------
            staged_problems = _validate_staged(staged, vault_root)
            if staged_problems:
                return fail(
                    "staged-validation-failed",
                    "staged tree failed validation",
                    problems=tuple(staged_problems),
                )

            # --- receipt (promoted atomically with the transaction) ---------
            created = sorted(
                rel for rel in staged if rel != receipt_rel and not (vault_root / rel).exists()
            )
            modified = sorted(
                rel for rel in staged
                if rel != receipt_rel and (vault_root / rel).exists()
            )
            receipt = {
                "schema_version": 1,
                "receipt_type": "atlas-route",
                "receipt_id": receipt_id,
                "event": {
                    "event_id": event.event_id,
                    "event_type": event.event_kind,
                    "work_package_id": event.work_package,
                    "normalized_sha256": event.normalized_sha256,
                },
                "project": {
                    "project_id": identity.project_id,
                    "identity_source": identity.source,
                },
                "routing": {
                    "plan_sha256": plan_digest,
                    "transaction_id": transaction_id,
                    "idempotent_replay": False,
                },
                "updates": {
                    "created": [*created, receipt_rel],
                    "modified": modified,
                },
                "validation": {"status": "passed", "errors": 0, "warnings": 0},
                "source": {
                    "raw_event": record.raw_path,
                    "normalized_event": record.normalized_path,
                    "raw_sha256": event.raw_event_hash,
                },
                "sync_state": "synchronized",
                "blockers": [],
            }
            staged[receipt_rel] = render_receipt(receipt)

            # --- promote (skip byte-identical files: true no-mutation) ------
            txn = transaction.Transaction()
            changed_created: list[str] = []
            changed_modified: list[str] = []
            for rel in sorted(staged):
                path = vault_root / rel
                current = _read(path)
                if current == staged[rel]:
                    continue
                verification.ensure_inside_root(vault_root, path)
                txn.stage(path, staged[rel], current=current)
                (changed_modified if current is not None else changed_created).append(rel)

            try:
                txn.promote()
            except transaction.PreconditionError as exc:
                return fail("stale-transaction", str(exc))
            except OSError as exc:
                return fail("promotion-failed", f"{type(exc).__name__}: {exc}")

            return RouteResult(
                ok=True, status="routed", event_id=event.event_id,
                project_id=identity.project_id, receipt_id=receipt_id,
                plan_sha256=plan_digest, transaction_id=transaction_id,
                idempotent_replay=False,
                created=tuple(sorted(changed_created)),
                modified=tuple(sorted(changed_modified)),
                message="routed and projected",
                plan=plan,
            )
    except transaction.LockError as exc:
        return fail(exc.category, str(exc))


def _read(path: Path) -> str | None:
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _rel(vault_root: Path, path: Path) -> str:
    return path.resolve().relative_to(vault_root).as_posix()


def update_documentation_map(
    *,
    vault_root: Path,
    project_id: str,
    content: str,
    settings: RoutingSettings,
) -> tuple[bool, bool]:
    """Promote the AS-WP-004 documentation map through the router transaction.

    Returns ``(changed, no_op)``. The map is a router-owned projection even
    though its source data lives in ingestion state rather than event state.
    """
    if not project_identity.SAFE_PROJECT_ID.fullmatch(project_id):
        raise ValueError(f"unsafe project id: {project_id!r}")
    root = vault_root.resolve()
    rel = f"{settings.projects_root}/{project_id}/documentation-map.md"
    target = root / rel
    verification.ensure_inside_root(root, target)
    lock = transaction.ProjectLock(
        root / settings.state_root / f"{project_id}.lock",
        stale_seconds=settings.stale_lock_seconds,
        wait_seconds=settings.lock_wait_seconds,
    )
    with lock:
        current = _read(target)
        if current == content:
            return False, True
        txn = transaction.Transaction()
        txn.stage(target, content, current=current)
        txn.promote()
        return True, False


def update_derived_projection(
    *, vault_root: Path, project_id: str, relative_path: str, content: str,
    settings: RoutingSettings,
) -> tuple[bool, bool]:
    """Promote a derived Markdown projection under the per-project router lock."""
    if not project_identity.SAFE_PROJECT_ID.fullmatch(project_id):
        raise ValueError(f"unsafe project id: {project_id!r}")
    if not relative_path.startswith(f"{settings.projects_root}/{project_id}/") or not relative_path.endswith(".md"):
        raise ValueError(f"unsafe derived projection path: {relative_path!r}")
    root = vault_root.resolve()
    target = root / relative_path
    verification.ensure_inside_root(root, target)
    lock = transaction.ProjectLock(root / settings.state_root / f"{project_id}.lock", stale_seconds=settings.stale_lock_seconds, wait_seconds=settings.lock_wait_seconds)
    with lock:
        current = _read(target)
        if current == content:
            return False, True
        txn = transaction.Transaction()
        txn.stage(target, content, current=current)
        txn.promote()
        return True, False
