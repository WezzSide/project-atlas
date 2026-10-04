"""Ranked development queue and admissible-task selection (AS-DEVLOOP-001, slice 1).

Pure and deterministic: no I/O, no clock, no randomness. VPS3 (planner) can run it offline.

Invariants (documented, tested):
  * ADMISSIBLE != AUTHORIZED: this module only decides what MAY start autonomously; it never grants
    an owner gate (see ``owner_gates``) and never dispatches anything.
  * Anything needing an owner-only input (secret, trust-root activation, privilege expansion,
    destructive action, owner merge) is NEVER admissible for autonomous start.
  * One blocked lane does not block the program: blocked lanes and their dependants are skipped and
    the next admissible item is selected (recompute is a pure re-call).
  * Unbounded or irreversible work, or work with an exhausted attempt ceiling, is not admissible.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import IntEnum, StrEnum

MAX_ATTEMPTS_DEFAULT = 3


class Category(IntEnum):
    """Lower value ranks first (after severity)."""

    INTEGRATION_BLOCKER = 0
    SECURITY_GOVERNANCE = 1
    RELIABILITY = 2
    ARCHITECTURAL_DEPENDENCY = 3
    TEST_CI = 4
    TECH_DEBT = 5
    ROADMAP = 6


class OwnerInput(StrEnum):
    SECRET = "SECRET"
    TRUST_ROOT_ACTIVATION = "TRUST_ROOT_ACTIVATION"
    PRIVILEGE_EXPANSION = "PRIVILEGE_EXPANSION"
    DESTRUCTIVE_ACTION = "DESTRUCTIVE_ACTION"
    OWNER_MERGE = "OWNER_MERGE"


class QueueError(ValueError):
    code = "DEV_QUEUE_INVALID"


@dataclass(frozen=True, slots=True)
class QueueItem:
    task_id: str
    title: str
    category: Category
    severity: int = 0  # 0..3, higher is more urgent
    roadmap_value: int = 0
    depends_on: tuple[str, ...] = ()
    lane: str = ""
    requires_owner: tuple[OwnerInput, ...] = ()
    bounded: bool = True
    reversible: bool = True
    attempts: int = 0
    max_attempts: int = MAX_ATTEMPTS_DEFAULT


@dataclass(frozen=True, slots=True)
class Selection:
    selected: QueueItem | None
    ranked: tuple[str, ...]
    skipped: tuple[tuple[str, str], ...]  # (task_id, reason), rank order


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _check_types(i: QueueItem) -> None:
    """Fail closed on malformed field types (bool/float ints, str dependency lists, bad enums)."""
    if (
        not isinstance(i.task_id, str)
        or not isinstance(i.title, str)
        or not isinstance(i.lane, str)
    ):
        raise QueueError("task_id/title/lane must be str")
    if not isinstance(i.category, Category):
        raise QueueError(f"{i.task_id}: category must be a Category")
    for name in ("severity", "roadmap_value", "attempts", "max_attempts"):
        if not _is_int(getattr(i, name)):
            raise QueueError(f"{i.task_id}: {name} must be an int")
    if not isinstance(i.depends_on, tuple) or not all(isinstance(d, str) for d in i.depends_on):
        raise QueueError(f"{i.task_id}: depends_on must be a tuple of str")
    if not isinstance(i.requires_owner, tuple) or not all(
        isinstance(o, OwnerInput) for o in i.requires_owner
    ):
        raise QueueError(f"{i.task_id}: requires_owner must be a tuple of OwnerInput")
    if not isinstance(i.bounded, bool) or not isinstance(i.reversible, bool):
        raise QueueError(f"{i.task_id}: bounded/reversible must be bool")


def validate(items: Iterable[QueueItem]) -> tuple[QueueItem, ...]:
    """Reject duplicate ids, self/unknown dependencies, malformed severity and dependency cycles."""
    seq = tuple(items)
    for i in seq:
        _check_types(i)  # first: unhashable/odd ids must raise QueueError, not TypeError
    ids = [i.task_id for i in seq]
    if len(set(ids)) != len(ids):
        raise QueueError("duplicate task_id")
    known = set(ids)
    for i in seq:
        _check_types(i)
        if not i.task_id:
            raise QueueError("empty task_id")
        if not 0 <= i.severity <= 3:
            raise QueueError(f"{i.task_id}: severity out of range")
        if i.max_attempts < 1 or i.attempts < 0:
            raise QueueError(f"{i.task_id}: bad attempt counters")
        for d in i.depends_on:
            if d == i.task_id:
                raise QueueError(f"{i.task_id}: depends on itself")
            if d not in known:
                raise QueueError(f"{i.task_id}: unknown dependency {d}")
    _assert_acyclic(seq)
    return seq


def _assert_acyclic(seq: tuple[QueueItem, ...]) -> None:
    deps = {i.task_id: i.depends_on for i in seq}
    state: dict[str, int] = {}  # 1 visiting, 2 done

    def visit(node: str) -> None:
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            raise QueueError("dependency cycle")
        state[node] = 1
        for d in deps[node]:
            visit(d)
        state[node] = 2

    for t in deps:
        visit(t)


def rank(items: Iterable[QueueItem]) -> tuple[QueueItem, ...]:
    """Severity desc, category asc, roadmap value desc, then task_id (stable tie-break)."""
    return tuple(
        sorted(
            validate(items),
            key=lambda i: (-i.severity, int(i.category), -i.roadmap_value, i.task_id),
        )
    )


def inadmissible_reason(
    item: QueueItem,
    *,
    completed: frozenset[str],
    blocked: Mapping[str, str],
    blocked_lanes: frozenset[str],
    in_flight: frozenset[str] = frozenset(),
) -> str | None:
    """First reason an item may not start autonomously, or None when admissible.

    ``in_flight`` (default empty: no effect) names task ids that are already being worked on; such
    an item is skipped with ``IN_FLIGHT`` so the next ranked item can be selected. It is checked
    right after ``ALREADY_COMPLETED`` and before every other reason.
    """
    if item.task_id in completed:
        return "ALREADY_COMPLETED"
    if item.task_id in in_flight:
        return "IN_FLIGHT"
    if item.task_id in blocked:
        return f"BLOCKED:{blocked[item.task_id]}"
    if item.lane and item.lane in blocked_lanes:
        return f"LANE_BLOCKED:{item.lane}"
    if item.requires_owner:
        return "OWNER_INPUT_REQUIRED:" + ",".join(sorted(str(o) for o in item.requires_owner))
    if not item.bounded:
        return "UNBOUNDED"
    if not item.reversible:
        return "IRREVERSIBLE"
    if item.attempts >= item.max_attempts:
        return "ATTEMPT_CEILING_EXHAUSTED"
    missing = sorted(d for d in item.depends_on if d not in completed)
    if missing:
        return "DEPENDENCY_UNSATISFIED:" + ",".join(missing)
    return None


def select_next(
    items: Iterable[QueueItem],
    *,
    completed: Iterable[str] = (),
    blocked: Mapping[str, str] | None = None,
    blocked_lanes: Iterable[str] = (),
    in_flight: Iterable[str] = (),
) -> Selection:
    """Pick the highest-ranked admissible item; never raises for a merely-blocked program.

    A blocked task, a blocked lane, an unsatisfied dependency (including a dependency that is itself
    blocked) only removes that item from consideration; the scheduler continues with the rest.
    An ``in_flight`` task id (default: none) is skipped the same way, with reason ``IN_FLIGHT``;
    an in-flight task does NOT satisfy a dependency (only ``completed`` does).
    """
    ordered = rank(items)
    done = frozenset(completed)
    blk = dict(blocked or {})
    lanes = frozenset(blocked_lanes)
    flying = frozenset(in_flight)
    skipped: list[tuple[str, str]] = []
    chosen: QueueItem | None = None
    for it in ordered:
        why = inadmissible_reason(
            it, completed=done, blocked=blk, blocked_lanes=lanes, in_flight=flying
        )
        if why is None:
            chosen = it
            break
        skipped.append((it.task_id, why))
    return Selection(
        selected=chosen,
        ranked=tuple(i.task_id for i in ordered),
        skipped=tuple(skipped),
    )
