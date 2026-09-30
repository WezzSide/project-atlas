"""AS-DEVLOOP-001 slice 1: ranked development queue and admissible-task selection."""

from __future__ import annotations

import pytest

from project_atlas.orchestration.autonomy.dev_queue import (
    Category,
    OwnerInput,
    QueueError,
    QueueItem,
    rank,
    select_next,
)


def item(tid: str, **kw) -> QueueItem:
    kw.setdefault("category", Category.RELIABILITY)
    return QueueItem(task_id=tid, title=tid, **kw)


def test_rank_severity_then_category_then_roadmap_then_id():
    items = [
        item("d", category=Category.ROADMAP, severity=1),
        item("c", category=Category.SECURITY_GOVERNANCE, severity=1),
        item("b", category=Category.INTEGRATION_BLOCKER, severity=0),
        item("a", category=Category.RELIABILITY, severity=3),
        item("e", category=Category.RELIABILITY, severity=0, roadmap_value=5),
        item("f", category=Category.RELIABILITY, severity=0, roadmap_value=5),
    ]
    assert [i.task_id for i in rank(items)] == ["a", "c", "d", "b", "e", "f"]


def test_rank_is_deterministic_and_input_order_independent():
    items = [item("x", severity=2), item("y", severity=2), item("z", severity=1)]
    assert rank(items) == rank(list(reversed(items)))


def test_selects_highest_admissible():
    sel = select_next([item("low"), item("high", severity=3)])
    assert sel.selected is not None and sel.selected.task_id == "high"


@pytest.mark.parametrize("owner", list(OwnerInput))
def test_owner_only_input_is_never_admissible(owner):
    sel = select_next([item("needs-owner", severity=3, requires_owner=(owner,)), item("ok")])
    assert sel.selected is not None and sel.selected.task_id == "ok"
    assert sel.skipped[0][0] == "needs-owner"
    assert sel.skipped[0][1].startswith("OWNER_INPUT_REQUIRED")


def test_only_owner_bound_work_selects_nothing_without_raising():
    sel = select_next([item("s", requires_owner=(OwnerInput.SECRET,))])
    assert sel.selected is None and sel.skipped


def test_dependency_gating_and_completion_unlocks():
    items = [item("base"), item("child", severity=3, depends_on=("base",))]
    first = select_next(items)
    assert first.selected is not None and first.selected.task_id == "base"
    assert ("child", "DEPENDENCY_UNSATISFIED:base") in first.skipped
    second = select_next(items, completed={"base"})
    assert second.selected is not None and second.selected.task_id == "child"


def test_blocked_lane_does_not_block_program():
    items = [
        item("gate-fix", severity=3, lane="gate"),
        item("gate-followup", severity=2, lane="gate"),
        item("queue", severity=1, lane="loop"),
    ]
    sel = select_next(items, blocked_lanes={"gate"})
    assert sel.selected is not None and sel.selected.task_id == "queue"
    assert {t for t, _ in sel.skipped} == {"gate-fix", "gate-followup"}


def test_blocked_dependency_blocks_dependants_only():
    items = [item("a", severity=3), item("b", severity=2, depends_on=("a",)), item("c")]
    sel = select_next(items, blocked={"a": "PLATFORM_PERMISSION_CLASSIFIER"})
    assert sel.selected is not None and sel.selected.task_id == "c"
    reasons = dict(sel.skipped)
    assert reasons["a"].startswith("BLOCKED:")
    assert reasons["b"].startswith("DEPENDENCY_UNSATISFIED")


def test_unbounded_irreversible_and_exhausted_are_not_admissible():
    items = [
        item("unbounded", severity=3, bounded=False),
        item("irreversible", severity=3, reversible=False),
        item("exhausted", severity=3, attempts=3),
        item("fine"),
    ]
    sel = select_next(items)
    assert sel.selected is not None and sel.selected.task_id == "fine"
    assert dict(sel.skipped) == {
        "unbounded": "UNBOUNDED",
        "irreversible": "IRREVERSIBLE",
        "exhausted": "ATTEMPT_CEILING_EXHAUSTED",
    }


def test_completed_is_not_reselected():
    sel = select_next([item("done", severity=3)], completed={"done"})
    assert sel.selected is None and sel.skipped == (("done", "ALREADY_COMPLETED"),)


def test_recompute_after_state_change_is_pure_recall():
    items = [item("a", severity=3), item("b", severity=1)]
    assert select_next(items).selected.task_id == "a"  # type: ignore[union-attr]
    assert select_next(items, blocked={"a": "X"}).selected.task_id == "b"  # type: ignore[union-attr]
    assert select_next(items).selected.task_id == "a"  # type: ignore[union-attr]  # no hidden state


@pytest.mark.parametrize(
    "bad",
    [
        [item("a"), item("a")],
        [item("a", depends_on=("a",))],
        [item("a", depends_on=("ghost",))],
        [item("a", depends_on=("b",)), item("b", depends_on=("a",))],
        [item("a", severity=9)],
        [item("a", max_attempts=0)],
        [item("")],
    ],
)
def test_invalid_queues_fail_closed(bad):
    with pytest.raises(QueueError):
        select_next(bad)
