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


# ---- IV #1032 repairs: type safety + previously unpinned behaviour -------------------------


@pytest.mark.parametrize(
    "kw",
    [
        {"severity": True},
        {"severity": 1.5},
        {"max_attempts": True},
        {"attempts": 1.5},
        {"category": 99},
        {"depends_on": "b"},
        {"requires_owner": "SECRET"},
        {"bounded": 1},
    ],
)
def test_malformed_field_types_fail_closed(kw):
    with pytest.raises(QueueError):
        select_next([item("a", **kw), item("b")])


def test_non_str_task_id_fails_closed():
    with pytest.raises(QueueError):
        select_next([QueueItem(task_id=1, title="x", category=Category.RELIABILITY)])  # type: ignore[arg-type]


def test_completed_wins_over_blocked_and_unlocks_dependents():
    items = [item("a", severity=2), item("b", depends_on=("a",))]
    sel = select_next(items, completed={"a"}, blocked={"a": "X"})
    assert sel.selected is not None and sel.selected.task_id == "b"
    assert ("a", "ALREADY_COMPLETED") in sel.skipped


def test_attempt_ceiling_boundary_and_custom_max():
    ok = select_next([item("a", attempts=2, max_attempts=3)])
    assert ok.selected is not None
    assert select_next([item("a", attempts=3, max_attempts=3)]).selected is None
    assert select_next([item("a", attempts=1, max_attempts=1)]).selected is None
    assert select_next([item("a", attempts=1, max_attempts=2)]).selected is not None


def test_empty_lane_is_never_a_blocked_lane_and_empty_queue_is_none():
    assert select_next([item("a")], blocked_lanes={""}).selected is not None
    assert select_next([]).selected is None


def test_select_next_is_input_order_independent_and_non_mutating():
    import random

    base = [
        item("a", severity=1, roadmap_value=2),
        item("b", severity=1, roadmap_value=2),
        item("c", severity=1, category=Category.ROADMAP),
        item("d", severity=3, depends_on=("a",)),
        item("e", severity=0),
    ]
    blocked = {"e": "HOST_DOWN"}
    want = select_next(base, blocked=blocked)
    for seed in range(50):
        shuffled = base[:]
        random.Random(seed).shuffle(shuffled)
        assert select_next(iter(shuffled), blocked=blocked) == want
    assert blocked == {"e": "HOST_DOWN"}


def test_roadmap_value_sign_and_multi_dependency_reason_are_pinned():
    sel = select_next([item("a", roadmap_value=1), item("b", roadmap_value=5)])
    assert sel.selected is not None and sel.selected.task_id == "b"
    multi = select_next(
        [item("x"), item("y"), item("z", depends_on=("y", "x"))],
        blocked={"x": "B", "y": "B"},
    )
    assert ("z", "DEPENDENCY_UNSATISFIED:x,y") in multi.skipped


def test_owner_and_irreversible_reasons_when_sole_failure():
    a = select_next([item("a", requires_owner=(OwnerInput.SECRET,))])
    assert a.skipped == (("a", "OWNER_INPUT_REQUIRED:SECRET"),)
    b = select_next([item("b", reversible=False)])
    assert b.skipped == (("b", "IRREVERSIBLE"),)


def test_unhashable_task_id_raises_queue_error_not_type_error():
    with pytest.raises(QueueError):
        select_next([QueueItem(task_id=["x"], title="x", category=Category.RELIABILITY)])  # type: ignore[arg-type]


# ---- ATLAS-DEVQ-0006: in-flight exclusion ------------------------------------------------------


def test_in_flight_task_is_skipped_and_next_ranked_is_selected():
    from project_atlas.orchestration.autonomy.dev_queue import inadmissible_reason

    items = [item("a", severity=3), item("b", severity=2), item("c", severity=1)]
    sel = select_next(items, in_flight={"a"})
    assert sel.selected is not None and sel.selected.task_id == "b"
    assert sel.skipped == (("a", "IN_FLIGHT"),)
    assert sel.ranked == ("a", "b", "c")
    assert select_next(items, in_flight=("a", "b", "c")).selected is None
    why = inadmissible_reason(
        items[0], completed=frozenset(), blocked={}, blocked_lanes=frozenset()
    )
    assert why is None  # default: no in-flight notion


def test_in_flight_defaults_change_nothing_and_reason_order_is_stable():
    items = [
        item("a", severity=3),
        item("b", severity=2, depends_on=("a",)),
        item("c", severity=1, lane="gate"),
        item("d"),
    ]
    kw = dict(completed={"d"}, blocked={"c": "X"}, blocked_lanes={"gate"})
    assert select_next(items, **kw) == select_next(items, in_flight=(), **kw)
    assert select_next(items) == select_next(items, in_flight=frozenset())
    # ALREADY_COMPLETED wins over IN_FLIGHT; IN_FLIGHT wins over BLOCKED / lane / owner / deps
    both = select_next([item("a")], completed={"a"}, in_flight={"a"})
    assert both.skipped == (("a", "ALREADY_COMPLETED"),)
    flying = item(
        "f", lane="gate", requires_owner=(OwnerInput.SECRET,), bounded=False, depends_on=("a",)
    )
    sel = select_next(
        [item("a"), flying],
        blocked={"f": "X", "a": "Y"},
        blocked_lanes={"gate"},
        in_flight={"f"},
    )
    assert dict(sel.skipped)["f"] == "IN_FLIGHT"


def test_in_flight_task_does_not_satisfy_a_dependency():
    items = [item("base", severity=3), item("child", severity=2, depends_on=("base",))]
    sel = select_next(items, in_flight={"base"})
    assert sel.selected is None
    assert sel.skipped == (("base", "IN_FLIGHT"), ("child", "DEPENDENCY_UNSATISFIED:base"))
