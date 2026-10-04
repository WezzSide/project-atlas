"""ATLAS-DEVQ-0007: the planner's coordination journal.

Durable scope ownership, restart reconstruction, cross-planner admission by exclusive creation,
fail-closed replay, the guarded ``fail_execution`` and the explicit ``release_scope``. These are
local, single-host tests: they prove the journal's semantics, not a live multi-agent run.
"""

from __future__ import annotations

import hashlib
import json
import threading

import pytest

from project_atlas.orchestration.autonomy.dev_contracts import (
    Finding,
    FindingCategory,
    Role,
    Verdict,
    make_result,
    make_verdict,
)
from project_atlas.orchestration.autonomy.dev_planner import (
    MAX_COMMIT_RETRIES,
    DirJournal,
    JournalContended,
    JournalCorrupt,
    MemoryJournal,
    Phase,
    Planner,
    PlannerError,
    fleet_status,
)
from project_atlas.orchestration.autonomy.dev_queue import Category, QueueItem
from project_atlas.orchestration.autonomy.dev_spool_transport import SpoolTransport
from project_atlas.orchestration.autonomy.dev_transport import Channel, InMemoryTransport, encode

BASE = "a" * 40
REV1, TREE1 = "b" * 40, "c" * 40
MERGED = "f" * 40
IMPL, VER = "vps1-impl", "vps2-ver"
FIELDS = dict(
    repository="WezzSide/project-atlas",
    base_revision=BASE,
    authority_ref="AUTH-1",
    allowed_paths=("src/x",),
    forbidden_paths=("infra/atlas-runner/controller",),
)


def fields(*paths):
    return {**FIELDS, "allowed_paths": tuple(paths)}


def qi(tid, **kw):
    return QueueItem(task_id=tid, title=tid, category=Category.RELIABILITY, **kw)


def planner(t, journal, identity="vps3-plan"):
    return Planner(t, identity=identity, verifier_identities=(VER,), journal=journal)


def implement(t):
    w = t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL)
    assert w is not None
    t.publish(make_result(w, executor_identity=IMPL, result_revision=REV1, result_tree=TREE1))
    return w


def verify(t, verdict=Verdict.PASS, findings=()):
    req = t.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity=VER)
    assert req is not None
    t.publish(make_verdict(req, verdict=verdict, findings=findings))


def to_ready(t, p):
    implement(t)
    p.pump()
    verify(t)
    p.pump()


DEFECT = (Finding(finding_id="F1", category=FindingCategory.DEFECT, paths=("src/x/a.py",)),)


def view(p):
    return {
        r: (st.phase, st.work.seal, st.reason, tuple(st.history), st.scope_released)
        for r, st in p.lineages.items()
    }


# ---- restart reconstruction -----------------------------------------------------------------


def test_a_new_planner_on_the_same_journal_reconstructs_the_whole_state(tmp_path):
    t = SpoolTransport(tmp_path / "spool")
    j = DirJournal(tmp_path / "journal")
    p = planner(t, j)
    p.dispatch(qi("A"), **FIELDS)
    p.dispatch(qi("B"), **fields("src/y"))
    p.dispatch(qi("C"), **fields("src/z"))
    implement(t)
    implement(t)
    implement(t)
    p.pump()
    reqs = {}
    for _ in range(3):
        req = t.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity=VER)
        reqs[req.task_id] = req
    keep = reqs["C"]  # C stays VERIFYING: its verdict is published only after the restart
    t.publish(make_verdict(reqs["A"], verdict=Verdict.FAIL, findings=DEFECT))
    t.publish(make_verdict(reqs["B"], verdict=Verdict.PASS))
    p.pump()
    assert {r: s.phase for r, s in p.lineages.items()} == {
        "A": Phase.REPAIR_DISPATCHED,
        "B": Phase.INTEGRATION_READY,
        "C": Phase.VERIFYING,
    }

    q = planner(t, DirJournal(tmp_path / "journal"))  # "restart": nothing but the journal
    assert view(q) == view(p)
    assert q.completed == p.completed == {"B"} and q.blocked == p.blocked == {}
    assert q._by_task == p._by_task
    assert {k: w.seal for k, w in q._works.items()} == {k: w.seal for k, w in p._works.items()}
    assert {r: s.result and s.result.seal for r, s in q.lineages.items()} == {
        r: s.result and s.result.seal for r, s in p.lineages.items()
    }
    assert {k: v.seal for k, v in q.issued.items()} == {k: v.seal for k, v in p.issued.items()}
    assert q.in_flight() == {"A", "B", "C"}
    assert [w.seal for w in q.scope_holders()] == [w.seal for w in p.scope_holders()]
    # the restarted planner keeps refusing what the dead one would have refused ...
    with pytest.raises(PlannerError, match=r"^SCOPE_COLLISION:A:src/x/new\|src/x$"):
        q.dispatch(qi("D"), **fields("src/x/new"))
    # ... and can finish a lineage it never dispatched: C's verdict arrives after the restart
    t.publish(make_verdict(keep, verdict=Verdict.PASS))
    assert q.pump() == 1 and q.lineages["C"].phase is Phase.INTEGRATION_READY
    assert not q.quarantined


def test_identical_operations_write_identical_journal_bytes(tmp_path):
    def run(name):
        j = DirJournal(tmp_path / name)
        t = InMemoryTransport()
        p = planner(t, j)
        p.dispatch(qi("A"), **FIELDS)
        to_ready(t, p)
        p.release_scope("A", merged_revision=MERGED)
        return [f.read_bytes() for f in sorted((tmp_path / name).iterdir())]

    one, two = run("one"), run("two")
    assert one == two and len(one) == 4
    events = [json.loads(b) for b in one]
    assert [e["event"] for e in events] == ["DISPATCH", "RESULT", "READY", "RELEASE"]
    assert [e["seq"] for e in events] == [1, 2, 3, 4]
    assert events[0]["prev"] == ""
    assert [e["prev"] for e in events[1:]] == [hashlib.sha256(b).hexdigest() for b in one[:-1]]
    assert all(e["planner"] == "vps3-plan" for e in events)


# ---- admission across planners --------------------------------------------------------------


def test_a_second_planner_is_refused_by_a_holder_it_never_saw(tmp_path):
    t = InMemoryTransport()
    p1 = planner(t, DirJournal(tmp_path / "j"), "plan-1")
    p2 = planner(t, DirJournal(tmp_path / "j"), "plan-2")  # opened before A exists
    p1.dispatch(qi("A"), **FIELDS)
    assert p2.lineages == {}
    with pytest.raises(PlannerError, match=r"^SCOPE_COLLISION:A:src/x\|src/x$"):
        p2.dispatch(qi("B"), **FIELDS)
    assert set(p2.lineages) == {"A"} and len(t._queues[Channel.WORK]) == 1
    with pytest.raises(PlannerError, match="lineage/task id already in use"):
        p2.dispatch(qi("A"), **fields("src/elsewhere"))
    p2.dispatch(qi("B"), **fields("src/y"))  # a compatible lineage is admitted
    assert p1.select([qi("A"), qi("B"), qi("C")]).selected.task_id == "C"  # select syncs
    assert p1.in_flight() == {"A", "B"}
    assert p1.lineages["B"].dispatched_by == "plan-2"


class Interleaved:
    """Journal that lets ``other`` commit once, right between this writer's decide and append."""

    def __init__(self, inner, other):
        self.inner, self.other, self.fired = inner, other, False

    def read(self, after):
        return self.inner.read(after)

    def __getattr__(self, name):  # acknowledgement and continuity: the inner journal's
        return getattr(self.inner, name)

    def append(self, seq, data):
        if not self.fired:
            self.fired = True
            self.other()
        return self.inner.append(seq, data)


def test_losing_the_race_for_a_sequence_number_decides_again_against_the_winner():
    t = InMemoryTransport()
    inner = MemoryJournal()
    rival = planner(t, inner, "plan-2")
    j = Interleaved(inner, lambda: rival.dispatch(qi("R"), **FIELDS))
    p = planner(t, j, "plan-1")
    with pytest.raises(PlannerError, match=r"^SCOPE_COLLISION:R:src/x\|src/x$"):
        p.dispatch(qi("A"), **FIELDS)  # admissible when decided, colliding once R won seq 1
    assert set(p.lineages) == {"R"} and len(inner.read(0)) == 1
    assert len(t._queues[Channel.WORK]) == 1  # only R was published

    inner2 = MemoryJournal()
    rival2 = planner(t, inner2, "plan-2")
    j2 = Interleaved(inner2, lambda: rival2.dispatch(qi("R"), **fields("src/r")))
    p2 = planner(InMemoryTransport(), j2, "plan-1")
    w = p2.dispatch(qi("A"), **FIELDS)  # compatible: retried and admitted as event 2
    assert w.task_id == "A" and p2.state.seq == 2 and p2.in_flight() == {"A", "R"}


def test_a_journal_that_never_accepts_is_given_up_on_and_nothing_is_published():
    class Never(MemoryJournal):
        calls = 0

        def append(self, seq, data):
            Never.calls += 1
            return False

    t = InMemoryTransport()
    p = planner(t, Never())
    with pytest.raises(JournalContended, match="JOURNAL_CONTENDED"):
        p.dispatch(qi("A"), **FIELDS)
    assert Never.calls == MAX_COMMIT_RETRIES
    assert p.lineages == {} and len(t._queues[Channel.WORK]) == 0


def test_concurrent_planners_admit_exactly_one_of_several_colliding_lineages(tmp_path):
    n = 8
    t = SpoolTransport(tmp_path / "spool")
    planners = [planner(t, DirJournal(tmp_path / "j"), f"plan-{i}") for i in range(n)]
    start = threading.Barrier(n)
    out: list[str] = [""] * n

    def go(i):
        start.wait()
        try:
            planners[i].dispatch(qi(f"T{i}"), **fields(f"src/shared/part{i % 2}", "src/shared"))
            out[i] = "ADMITTED"
        except PlannerError as exc:
            out[i] = str(exc).split(":")[0]

    threads = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sorted(out) == ["ADMITTED"] + ["SCOPE_COLLISION"] * (n - 1)
    rows = fleet_status(DirJournal(tmp_path / "j"))
    assert len(rows) == 1 and rows[0]["holds_scope"] is True
    assert rows[0]["dispatched_by"] == f"plan-{out.index('ADMITTED')}"
    assert len(list((tmp_path / "spool" / "WORK").glob("*.json"))) == 1


def test_concurrent_planners_admit_every_one_of_several_compatible_lineages(tmp_path):
    n = 8
    t = SpoolTransport(tmp_path / "spool")
    planners = [planner(t, DirJournal(tmp_path / "j"), f"plan-{i}") for i in range(n)]
    start = threading.Barrier(n)
    errors: list[str] = []

    def go(i):
        start.wait()
        try:
            planners[i].dispatch(qi(f"T{i}"), **fields(f"src/lane{i}"))
        except PlannerError as exc:  # pragma: no cover - the assertion below reports it
            errors.append(str(exc))

    threads = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    rows = fleet_status(DirJournal(tmp_path / "j"))
    assert [r["lineage_root"] for r in rows] == sorted(f"T{i}" for i in range(n))
    assert sorted(r["last_seq"] for r in rows) == list(range(1, n + 1))  # one event each, no gap
    assert all(r["holds_scope"] for r in rows)


def test_another_planner_can_carry_a_lineage_forward(tmp_path):
    t = InMemoryTransport()
    p1 = planner(t, DirJournal(tmp_path / "j"), "plan-1")
    p2 = planner(t, DirJournal(tmp_path / "j"), "plan-2")
    p1.dispatch(qi("A"), **FIELDS)
    implement(t)
    assert p2.pump() == 1  # p2 never dispatched A; it replays, then accepts the result
    assert p2.lineages["A"].phase is Phase.VERIFYING and not p2.quarantined
    verify(t, Verdict.FAIL, DEFECT)
    assert p1.pump() == 1  # and p1 takes the verdict for a request p2 issued
    assert p1.lineages["A"].phase is Phase.REPAIR_DISPATCHED and not p1.quarantined
    assert [json.loads(b)["planner"] for b in DirJournal(tmp_path / "j").read(0)] == [
        "plan-1",
        "plan-2",
        "plan-1",
    ]


# ---- fail-closed replay ---------------------------------------------------------------------


def _journal_with(tmp_path):
    t = InMemoryTransport()
    p = planner(t, DirJournal(tmp_path / "j"))
    p.dispatch(qi("A"), **FIELDS)
    to_ready(t, p)
    return sorted((tmp_path / "j").iterdir())


def _open(tmp_path):
    return planner(InMemoryTransport(), DirJournal(tmp_path / "j"))


def test_an_edited_event_breaks_the_chain_and_the_planner_refuses_to_start(tmp_path):
    files = _journal_with(tmp_path)
    ev = json.loads(files[0].read_bytes())
    ev["planner"] = "someone-else"
    files[0].write_bytes(json.dumps(ev, sort_keys=True).encode())
    # the anchor notices first: event 1 is no longer the event that was acknowledged
    with pytest.raises(JournalCorrupt, match="JOURNAL_DIVERGED:event 1 is acknowledged as a diff"):
        _open(tmp_path)
    with pytest.raises(JournalCorrupt, match="JOURNAL_DIVERGED"):
        fleet_status(DirJournal(tmp_path / "j"))
    # and without any anchor the chain alone still refuses the edit
    for ack in (tmp_path / "j.ack").iterdir():
        ack.unlink()
    with pytest.raises(JournalCorrupt, match="hash chain is broken"):
        _open(tmp_path)
    with pytest.raises(JournalCorrupt, match="hash chain is broken"):
        fleet_status(DirJournal(tmp_path / "j"))


def test_a_missing_middle_event_or_a_stray_file_is_refused(tmp_path):
    files = _journal_with(tmp_path)
    middle = files[1].read_bytes()
    files[1].unlink()
    with pytest.raises(JournalCorrupt, match=r"JOURNAL_CORRUPT:directory is not events 1\.\.k"):
        _open(tmp_path)
    files[1].write_bytes(middle)
    assert _open(tmp_path).lineages["A"].phase is Phase.INTEGRATION_READY
    for stray in ("notes.txt", "000000000004.json.bak", "x000000000004.json", "4.json"):
        (tmp_path / "j" / stray).write_text("x")
        with pytest.raises(JournalCorrupt, match=r"JOURNAL_CORRUPT:directory is not events 1\.\.k"):
            _open(tmp_path)
        (tmp_path / "j" / stray).unlink()
    assert _open(tmp_path).state.seq == 3


def test_garbage_and_non_object_events_are_refused(tmp_path):
    files = _journal_with(tmp_path)
    good = files[0].read_bytes()
    for bad, why in (
        (b"{not json", "JOURNAL_CORRUPT:event 1"),
        (b"[1, 2]", "event is not an object"),
        (b"{}", "unsupported journal version None"),
    ):
        files[0].write_bytes(bad)
        with pytest.raises(PlannerError, match=why):
            _open(tmp_path)
    files[0].write_bytes(good)
    assert _open(tmp_path).state.seq == 3
    n, head = _head(tmp_path)
    for body, why in (
        (dict(v=2), "unsupported journal version 2"),
        (dict(seq=float(n + 1)), "sequence or hash chain is broken"),
        (dict(seq=n + 2), "sequence or hash chain is broken"),
    ):
        ev = {"v": 1, "seq": n + 1, "prev": head, "planner": "x", "event": "RELEASE", "root": "A"}
        ev.update(evidence=MERGED, **body)
        (tmp_path / "j" / f"{n + 1:012d}.json").write_bytes(json.dumps(ev, sort_keys=True).encode())
        with pytest.raises(PlannerError, match=why):
            _open(tmp_path)
    ev.update(v=1, seq=n + 1)  # the same event, well-formed, is a legal RELEASE
    (tmp_path / "j" / f"{n + 1:012d}.json").write_bytes(json.dumps(ev, sort_keys=True).encode())
    assert _open(tmp_path).lineages["A"].scope_released == MERGED


def _forge(path, seq, prev, **body):
    ev = {"v": 1, "seq": seq, "prev": prev, "planner": "forger", **body}
    raw = json.dumps(ev, sort_keys=True).encode()
    (path / f"{seq:012d}.json").write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _head(tmp_path):
    files = sorted((tmp_path / "j").iterdir())
    return len(files), hashlib.sha256(files[-1].read_bytes()).hexdigest()


def test_a_well_chained_event_that_is_not_a_legal_transition_is_refused(tmp_path):
    from project_atlas.orchestration.autonomy.dev_contracts import make_work

    _journal_with(tmp_path)
    n, head = _head(tmp_path)
    j = tmp_path / "j"

    def w(task, paths, root=None, **kw):
        return make_work(
            task_id=task,
            execution_id=f"{task}-E1",
            lineage_root=root or task,
            acceptance_contract=("ok",),
            **{**fields(*paths), **kw},
        )

    cases = [
        # a second holder over a held scope
        (dict(event="DISPATCH", root="B", work=encode(w("B", ("src/x/a",)))), "SCOPE_COLLISION:A:"),
        # a lineage root that already exists
        (dict(event="DISPATCH", root="A", work=encode(w("A", ("src/q",)))), "already in use"),
        # root field that is not the work's own root
        (dict(event="DISPATCH", root="B", work=encode(w("C", ("src/q",)))), "not the root work"),
        # a root in the namespace reserved for repair tasks
        (dict(event="DISPATCH", root="B-R1", work=encode(w("B-R1", ("src/q",)))), "reserved"),
        # terminal on an already terminal (INTEGRATION_READY) lineage: no silent scope release
        (
            dict(event="TERMINAL", root="A", phase="BLOCKED", reason="x", evidence=""),
            "already terminal",
        ),
        # a terminal phase that is not a refusal phase
        (
            dict(event="TERMINAL", root="A", phase="DISPATCHED", reason="x", evidence=""),
            "must be BLOCKED or OWNER_REQUIRED",
        ),
        # release without a merge revision
        (dict(event="RELEASE", root="A", evidence="main"), "40-hex merge revision"),
        (dict(event="RELEASE", root="A", evidence=MERGED + "\n"), "40-hex merge revision"),
        (dict(event="RELEASE", root="nope", evidence=MERGED), "unknown lineage"),
        (dict(event="NONSENSE", root="A"), "unknown journal event"),
    ]
    t2 = InMemoryTransport()  # a second, still executing lineage for the TERMINAL evidence case
    p2 = planner(t2, DirJournal(tmp_path / "j"))
    p2.dispatch(qi("Z"), **fields("src/zz"))
    n, head = _head(tmp_path)
    cases.append(
        (
            dict(event="TERMINAL", root="Z", phase="BLOCKED", reason="x", evidence=5),
            "TERMINAL evidence must be a string",
        )
    )
    for body, why in cases:
        _forge(j, n + 1, head, **body)
        with pytest.raises(PlannerError, match=why):
            _open(tmp_path)
        (j / f"{n + 1:012d}.json").unlink()
    # control: a legal event at the same position is accepted, so the refusals above are real
    _forge(j, n + 1, head, event="DISPATCH", root="B", work=encode(w("B", ("src/q",))))
    assert _open(tmp_path).in_flight() == {"A", "B", "Z"}
    (j / f"{n + 1:012d}.json").unlink()
    (tmp_path / "j.ack" / f"{n + 1:012d}.ack").unlink()  # the control event was acknowledged
    # a record whose seal does not verify
    wire = json.loads(encode(w("B", ("src/q",))))
    wire["body"]["allowed_paths"] = ["src/x"]
    _forge(j, n + 1, head, event="DISPATCH", root="B", work=json.dumps(wire))
    with pytest.raises(PlannerError, match="seal mismatch"):
        _open(tmp_path)


def test_a_journalled_repair_may_not_widen_or_move_the_lineage_scope(tmp_path):
    t = InMemoryTransport()
    p = planner(t, DirJournal(tmp_path / "j"))
    p.dispatch(qi("A"), **FIELDS)
    implement(t)
    p.pump()
    req = t.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity=VER)
    ver = make_verdict(req, verdict=Verdict.FAIL, findings=DEFECT)
    t.publish(ver)
    p.pump()
    files = sorted((tmp_path / "j").iterdir())
    honest = json.loads(files[-1].read_bytes())
    assert honest["event"] == "REPAIR"
    rw = p.lineages["A"].work
    files[-1].unlink()
    n, head = _head(tmp_path)
    for change in ({"allowed_paths": ("src/x", "src/other")}, {"repository": "WezzSide/other"}):
        wide = rw.model_copy(update=change).sealed()
        _forge(tmp_path / "j", n + 1, head, event="REPAIR", root="A", verdict=honest["verdict"],
               work=encode(wide))  # fmt: skip
        with pytest.raises(PlannerError, match="may not change the lineage's repository or scope"):
            _open(tmp_path)
        (tmp_path / "j" / f"{n + 1:012d}.json").unlink()


# ---- fail_execution guard -------------------------------------------------------------------


def test_fail_execution_is_refused_unless_it_names_the_executing_work():
    t = InMemoryTransport()
    p = planner(t, None)
    p.dispatch(qi("A"), **FIELDS)
    implement(t)
    p.pump()  # VERIFYING: a result exists, the execution did not fail
    with pytest.raises(PlannerError, match="fail_execution refused"):
        p.fail_execution("A", "late")
    verify(t, Verdict.FAIL, DEFECT)
    p.pump()
    assert p.lineages["A"].work.task_id == "A-R1"
    with pytest.raises(PlannerError, match="fail_execution refused"):
        p.fail_execution("A", "late")  # superseded work of the lineage
    with pytest.raises(PlannerError, match="unknown task"):
        p.fail_execution("nope", "x")
    assert p.in_flight() == {"A"} and p.blocked == {}
    p.fail_execution("A-R1", "runner lost")  # the executing repair work: accepted
    assert p.lineages["A"].phase is Phase.BLOCKED and p.in_flight() == frozenset()
    assert p.blocked == {"A": "BLOCKED:EXECUTION_FAILED:runner lost"}
    with pytest.raises(PlannerError, match="fail_execution refused"):
        p.fail_execution("A-R1", "again")  # already terminal


def test_fail_execution_cannot_release_an_integration_ready_scope():
    t = InMemoryTransport()
    p = planner(t, None)
    p.dispatch(qi("A"), **FIELDS)
    to_ready(t, p)
    seq = p.state.seq
    with pytest.raises(PlannerError, match="fail_execution refused"):
        p.fail_execution("A", "late")
    assert p.lineages["A"].phase is Phase.INTEGRATION_READY and p.state.seq == seq
    assert p.completed == {"A"} and p.blocked == {} and p.in_flight() == {"A"}
    with pytest.raises(PlannerError, match=r"^SCOPE_COLLISION:A:"):
        p.dispatch(qi("B"), **FIELDS)


# ---- release_scope --------------------------------------------------------------------------


def test_release_scope_needs_integration_ready_and_a_merge_revision(tmp_path):
    t = InMemoryTransport()
    j = DirJournal(tmp_path / "j")
    p = planner(t, j)
    p.dispatch(qi("A"), **FIELDS)
    with pytest.raises(PlannerError, match="only an unreleased INTEGRATION_READY"):
        p.release_scope("A", merged_revision=MERGED)  # still executing
    to_ready(t, p)
    for bad in ("main", "F" * 40, MERGED[:39], ""):
        with pytest.raises(PlannerError, match="40-hex merge revision"):
            p.release_scope("A", merged_revision=bad)
    with pytest.raises(PlannerError, match="unknown lineage"):
        p.release_scope("B", merged_revision=MERGED)
    assert p.in_flight() == {"A"}
    p.release_scope("A", merged_revision=MERGED)
    st = p.lineages["A"]
    assert st.phase is Phase.INTEGRATION_READY and st.scope_released == MERGED
    assert st.history[-1] == f"SCOPE_RELEASED:{MERGED}"
    assert p.in_flight() == frozenset() and p.scope_holders() == () and p.completed == {"A"}
    with pytest.raises(PlannerError, match="only an unreleased INTEGRATION_READY"):
        p.release_scope("A", merged_revision=MERGED)
    p.dispatch(qi("B"), **FIELDS)  # the same scope is admitted again
    q = planner(InMemoryTransport(), DirJournal(tmp_path / "j"))  # and the release is durable
    assert q.in_flight() == {"B"} and q.lineages["A"].scope_released == MERGED
    with pytest.raises(PlannerError, match="ALREADY_COMPLETED"):
        q.dispatch(qi("A"), **fields("src/q"))  # a released lineage does not free its task id


# ---- fleet status ---------------------------------------------------------------------------


def test_fleet_status_is_derived_from_the_journal_alone(tmp_path):
    t = InMemoryTransport()
    p = planner(t, DirJournal(tmp_path / "j"))
    assert fleet_status(DirJournal(tmp_path / "j")) == ()
    wa = p.dispatch(qi("A"), **FIELDS)
    wb = p.dispatch(qi("B"), **fields("src/y"))
    p.fail_execution("B", "runner lost")
    rows = fleet_status(DirJournal(tmp_path / "j"))  # no planner object involved
    assert rows == p.fleet_status()
    assert rows == (
        {
            "lineage_root": "A",
            "phase": "DISPATCHED",
            "reason": "",
            "task_id": "A",
            "execution_id": "A-E1",
            "work_seal": wa.seal,
            "repository": "WezzSide/project-atlas",
            "base_revision": BASE,
            "allowed_paths": ("src/x",),
            "holds_scope": True,
            "scope_released": "",
            "dispatched_by": "vps3-plan",
            "last_seq": 1,
        },
        {
            "lineage_root": "B",
            "phase": "BLOCKED",
            "reason": "EXECUTION_FAILED:runner lost",
            "task_id": "B",
            "execution_id": "B-E1",
            "work_seal": wb.seal,
            "repository": "WezzSide/project-atlas",
            "base_revision": BASE,
            "allowed_paths": ("src/y",),
            "holds_scope": False,
            "scope_released": "",
            "dispatched_by": "vps3-plan",
            "last_seq": 3,
        },
    )
    # the planner's in-memory replica is not consulted: tampering with it changes nothing
    p.lineages["A"].phase = Phase.BLOCKED
    assert p.fleet_status()[0]["phase"] == "DISPATCHED"


# ---- crash windows --------------------------------------------------------------------------


def test_a_dispatch_that_was_journalled_but_not_published_is_published_by_recover(tmp_path):
    class Flaky(InMemoryTransport):
        down = True

        def publish(self, record):
            if self.down:
                raise OSError("disk gone between journal and spool")
            return super().publish(record)

    t = Flaky()
    p = planner(t, DirJournal(tmp_path / "j"))
    with pytest.raises(OSError):
        p.dispatch(qi("A"), **FIELDS)
    # write-ahead: the scope is owned although nothing reached the transport
    assert p.lineages["A"].phase is Phase.DISPATCHED and len(t._queues[Channel.WORK]) == 0
    q = planner(t, DirJournal(tmp_path / "j"))
    with pytest.raises(PlannerError, match=r"^SCOPE_COLLISION:A:"):
        q.dispatch(qi("B"), **FIELDS)
    t.down = False
    assert q.recover() == ["REPUBLISHED:A:WORK"]
    assert len(t._queues[Channel.WORK]) == 1
    assert q.recover() == []  # idempotent
    implement(t)
    q.pump()
    t.down = True
    assert q.lineages["A"].phase is Phase.VERIFYING


def test_recover_republishes_an_unpublished_verification_request_and_repair(tmp_path):
    class Flaky(InMemoryTransport):
        refuse: tuple[str, ...] = ()

        def publish(self, record):
            if record.KIND.value in self.refuse:
                raise OSError("down")
            return super().publish(record)

    t = Flaky()
    p = planner(t, DirJournal(tmp_path / "j"))
    p.dispatch(qi("A"), **FIELDS)
    implement(t)
    t.refuse = ("VERIFICATION_REQUEST",)
    with pytest.raises(OSError):
        p.pump()
    assert p.lineages["A"].phase is Phase.VERIFYING and not t._queues[Channel.VERIFICATION]
    t.refuse = ()
    q = planner(t, DirJournal(tmp_path / "j"))
    assert q.recover() == ["REPUBLISHED:A:VERIFICATION_REQUEST"]
    t.refuse = ("WORK",)
    verify(t, Verdict.FAIL, DEFECT)
    with pytest.raises(OSError):
        q.pump()
    assert q.lineages["A"].phase is Phase.REPAIR_DISPATCHED and not t._queues[Channel.WORK]
    t.refuse = ()
    r = planner(t, DirJournal(tmp_path / "j"))
    assert r.recover() == ["REPUBLISHED:A:WORK"]
    assert implement(t).task_id == "A-R1"


def test_recover_replays_a_result_that_was_claimed_but_never_journalled(tmp_path):
    t = SpoolTransport(tmp_path / "spool")
    p = planner(t, DirJournal(tmp_path / "j"))
    p.dispatch(qi("A"), **FIELDS)
    implement(t)
    # the planner process dies right after the consume-once claim, before its journal append
    res = t.claim(Channel.RESULT, role=Role.PLANNER, identity="vps3-plan")
    assert res is not None
    q = planner(t, DirJournal(tmp_path / "j"))
    assert q.pump() == 0 and q.lineages["A"].phase is Phase.DISPATCHED  # the record is gone
    assert q.recover() == [f"REPLAYED:RESULT:{res.seal}"]
    assert q.lineages["A"].phase is Phase.VERIFYING and not q.quarantined
    assert q.recover() == [] and not q.quarantined  # journalled now: never fed twice
    verify(t)
    ver = t.claim(Channel.VERDICT, role=Role.PLANNER, identity="vps3-plan")  # same crash, later
    r = planner(t, DirJournal(tmp_path / "j"))
    assert r.recover() == [f"REPLAYED:VERDICT:{ver.seal}"]
    assert r.lineages["A"].phase is Phase.INTEGRATION_READY and r.completed == {"A"}
    assert r.recover() == [] and not r.quarantined
    # records claimed by a different planner identity are not this planner's to replay
    other = planner(t, DirJournal(tmp_path / "j2"), "other-plan")
    assert other.recover() == []


def test_a_corrupt_journal_makes_pump_raise_instead_of_quarantining_records(tmp_path):
    t = InMemoryTransport()
    p = planner(t, DirJournal(tmp_path / "j"))
    p.dispatch(qi("A"), **FIELDS)
    implement(t)
    _forge(tmp_path / "j", 2, "0" * 64, event="NONSENSE", root="A")
    with pytest.raises(JournalCorrupt, match="hash chain"):
        p.pump()
    assert len(t._queues[Channel.RESULT]) == 1 and not p.quarantined  # nothing was consumed


def test_a_journal_that_stops_replaying_mid_pump_keeps_the_claimed_record(tmp_path):
    class Late(DirJournal):
        poison_at = 0
        reads = 0

        def read(self, after):
            self.reads += 1
            if self.reads == self.poison_at:
                _forge(self.root, after + 1, "0" * 64, event="NONSENSE", root="A")
            return super().read(after)

    t = InMemoryTransport()
    j = Late(tmp_path / "j")
    p = planner(t, j)
    p.dispatch(qi("A"), **FIELDS)
    implement(t)
    j.reads, j.poison_at = 0, 2  # read 1 is pump's own replay, read 2 the handler's commit
    with pytest.raises(JournalCorrupt):
        p.pump()
    assert not p.quarantined and not t._queues[Channel.RESULT]
    assert [c for c, _ in p.deferred] == [Channel.RESULT]  # claimed, undecided, kept
    (tmp_path / "j" / "000000000002.json").unlink()  # the operator removes the bad event
    assert p.pump() == 1 and p.lineages["A"].phase is Phase.VERIFYING and p.deferred == []


def test_the_default_journal_is_volatile_and_per_planner():
    t = InMemoryTransport()
    p = planner(t, None)
    p.dispatch(qi("A"), **FIELDS)
    assert isinstance(p.journal, MemoryJournal) and p.state.seq == 1
    q = planner(t, None)  # no shared journal: no shared ownership (documented limit)
    q.dispatch(qi("B"), **FIELDS)
    assert q.in_flight() == {"B"} and p.in_flight() == {"A"}


# ---- replay re-checks record bindings -------------------------------------------------------


def _two_verifying(tmp_path):
    """A and B both VERIFYING; returns (transport, planner, their requests by task id)."""
    t = InMemoryTransport()
    p = planner(t, DirJournal(tmp_path / "j"))
    p.dispatch(qi("A"), **FIELDS)
    p.dispatch(qi("B"), **fields("src/y"))
    implement(t)
    implement(t)
    p.pump()
    reqs = {}
    for _ in range(2):
        req = t.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity=VER)
        reqs[req.task_id] = req
    return t, p, reqs


def test_replay_refuses_a_verdict_event_that_is_not_bound_to_its_lineage(tmp_path):
    _, p, reqs = _two_verifying(tmp_path)
    j = tmp_path / "j"
    n, head = _head(tmp_path)
    pass_b = make_verdict(reqs["B"], verdict=Verdict.PASS)
    fail_a = make_verdict(reqs["A"], verdict=Verdict.FAIL, findings=DEFECT)
    pass_a = make_verdict(reqs["A"], verdict=Verdict.PASS)
    other_artifact = pass_a.model_copy(update={"result_revision": "9" * 40}).sealed()
    other_verifier = pass_a.model_copy(update={"verifier_identity": "someone"}).sealed()
    repair = p.lineages["A"].work.model_copy(update={"task_id": "A-R1", "attempt": 2}).sealed()
    cases = [
        # B's genuine PASS used to complete A
        (dict(event="READY", root="A", verdict=encode(pass_b)), "without the outstanding"),
        (dict(event="READY", root="A", verdict=encode(fail_a)), "READY needs a PASS verdict"),
        (dict(event="READY", root="A", verdict=encode(other_artifact)), "assigned verifier"),
        (dict(event="READY", root="A", verdict=encode(other_verifier)), "assigned verifier"),
        (
            dict(event="REPAIR", root="A", verdict=encode(pass_a), work=encode(repair)),
            "REPAIR needs a non-PASS verdict",
        ),
        # a repair work that is not what the verdict materialises (same scope, hand-made)
        (
            dict(event="REPAIR", root="A", verdict=encode(fail_a), work=encode(repair)),
            "not the repair this verdict materialises",
        ),
    ]
    for body, why in cases:
        _forge(j, n + 1, head, **body)
        with pytest.raises(PlannerError, match=why):
            _open(tmp_path)
        (j / f"{n + 1:012d}.json").unlink()
    _forge(j, n + 1, head, event="READY", root="A", verdict=encode(pass_a))  # control: legal
    assert _open(tmp_path).completed == {"A"}


def test_replay_refuses_a_result_event_that_does_not_answer_the_work(tmp_path):
    from project_atlas.orchestration.autonomy.dev_contracts import make_verification_request

    t = InMemoryTransport()
    p = planner(t, DirJournal(tmp_path / "j"))
    wa = p.dispatch(qi("A"), **FIELDS)
    wb = p.dispatch(qi("B"), **fields("src/y"))
    j = tmp_path / "j"
    n, head = _head(tmp_path)

    def res(w, **kw):
        r = make_result(w, executor_identity=IMPL, result_revision=REV1, result_tree=TREE1)
        return r.model_copy(update=kw).sealed() if kw else r

    def ev(result, request_for=None, work=None):
        req = make_verification_request(work or wa, request_for or result, verifier_identity=VER)
        return dict(event="RESULT", root="A", result=encode(result), request=encode(req))

    good = res(wa)
    cases = [
        (ev(res(wb), work=wb), "does not answer the lineage's dispatched work"),
        (ev(res(wa, base_revision="9" * 40)), "identity/repository/base"),
        (ev(good, request_for=res(wa, result_tree="9" * 40)), "does not cover the journalled"),
    ]
    for body, why in cases:
        _forge(j, n + 1, head, **body)
        with pytest.raises(PlannerError, match=why):
            _open(tmp_path)
        (j / f"{n + 1:012d}.json").unlink()
    # the executor as its own verifier cannot even be sealed into a request
    with pytest.raises(Exception, match=r"(?i)verif"):
        make_verification_request(wa, good, verifier_identity=IMPL)
    _forge(j, n + 1, head, **ev(good))  # control: legal
    assert _open(tmp_path).lineages["A"].phase is Phase.VERIFYING


# ---- a commit that could not be written does not lose the record ----------------------------


def test_a_contended_or_failed_append_defers_the_claimed_record_instead_of_dropping_it():
    class Shaky(MemoryJournal):
        mode = "ok"

        def append(self, seq, data):
            if self.mode == "contended":
                return False
            if self.mode == "io":
                raise OSError(28, "No space left on device")
            return super().append(seq, data)

    t = InMemoryTransport()
    j = Shaky()
    p = planner(t, j)
    p.dispatch(qi("A"), **FIELDS)
    implement(t)
    j.mode = "contended"
    assert p.pump() == 1
    assert p.lineages["A"].phase is Phase.DISPATCHED and not p.quarantined
    assert [c for c, _ in p.deferred] == [Channel.RESULT]
    j.mode = "io"
    with pytest.raises(OSError):
        p.pump()
    assert len(p.deferred) == 1 and not p.quarantined
    j.mode = "ok"
    assert p.pump() == 1  # the record consumed three pumps ago is decided now
    assert p.lineages["A"].phase is Phase.VERIFYING and p.deferred == [] and not p.quarantined
    assert p.pump() == 0


def test_every_deferred_record_survives_an_io_error_on_one_of_them():
    class Shaky(MemoryJournal):
        mode = "ok"

        def append(self, seq, data):
            if self.mode == "contended":
                return False
            if self.mode == "io":
                self.mode = "ok"  # one IO error, then healthy
                raise OSError(5, "Input/output error")
            return super().append(seq, data)

    t = InMemoryTransport()
    j = Shaky()
    p = planner(t, j)
    for root, path in (("A", "src/a"), ("B", "src/b"), ("C", "src/c")):
        p.dispatch(qi(root), **fields(path))
        implement(t)
    j.mode = "contended"
    assert p.pump() == 3 and len(p.deferred) == 3
    j.mode = "io"
    with pytest.raises(OSError):
        p.pump()
    assert len(p.deferred) == 3 and not p.quarantined  # none dropped by the failed retry
    assert p.pump() == 3 and p.deferred == [] and not p.quarantined
    assert {r: s.phase for r, s in p.lineages.items()} == dict.fromkeys("ABC", Phase.VERIFYING)
    assert p.pump() == 0 and p.state.seq == 6  # each result journalled exactly once


def test_a_publish_failure_after_the_commit_does_not_defer_the_record():
    class Flaky(InMemoryTransport):
        down = False

        def publish(self, record):
            if self.down and record.KIND.value == "VERIFICATION_REQUEST":
                raise OSError("down")
            return super().publish(record)

    t = Flaky()
    p = planner(t, None)
    p.dispatch(qi("A"), **FIELDS)
    implement(t)
    t.down = True
    with pytest.raises(OSError):
        p.pump()
    # journalled (VERIFYING), so the result is decided: not deferred, never fed again
    assert p.lineages["A"].phase is Phase.VERIFYING and p.deferred == []
    t.down = False
    assert p.pump() == 0 and not p.quarantined
    assert p.recover() == ["REPUBLISHED:A:VERIFICATION_REQUEST"]


def test_a_result_that_ends_in_no_independent_verifier_is_not_fed_again(tmp_path):
    def only_impl():
        return Planner(
            t, identity="vps3-plan", verifier_identities=(IMPL,), journal=DirJournal(tmp_path / "j")
        )

    t = SpoolTransport(tmp_path / "spool")
    p = only_impl()
    p.dispatch(qi("A"), **FIELDS)
    implement(t)
    assert p.pump() == 1 and p.blocked == {"A": "BLOCKED:NO_INDEPENDENT_VERIFIER"}
    q = only_impl()
    assert q.recover() == [] and not q.quarantined  # its seal is journalled as evidence


def test_append_refuses_a_sequence_number_whose_predecessor_is_missing(tmp_path):
    j = DirJournal(tmp_path / "j")
    assert j.append(2, b"{}") is False and list((tmp_path / "j").iterdir()) == []
    assert j.append(1, b"{}") is True and j.append(1, b"{}") is False
    assert j.append(3, b"{}") is False and j.append(2, b"{}") is True
    assert [p.name for p in sorted((tmp_path / "j").iterdir())] == [
        "000000000001.json",
        "000000000002.json",
    ]
    (tmp_path / "j" / ".tmp-crashed").write_bytes(b"half")  # a crashed writer's temp file
    assert len(DirJournal(tmp_path / "j").read(0)) == 2  # is ignored
    (tmp_path / "j" / "000000000000.json").write_bytes(b"{}")  # not an event number
    with pytest.raises(JournalCorrupt):
        DirJournal(tmp_path / "j").read(0)


def test_recover_does_not_feed_a_journalled_repair_verdict_again(tmp_path):
    t = SpoolTransport(tmp_path / "spool")
    p = planner(t, DirJournal(tmp_path / "j"))
    p.dispatch(qi("A"), **FIELDS)
    implement(t)
    p.pump()
    verify(t, Verdict.FAIL, DEFECT)
    assert p.pump() == 1 and p.lineages["A"].phase is Phase.REPAIR_DISPATCHED
    q = planner(t, DirJournal(tmp_path / "j"))
    assert q.recover() == [] and not q.quarantined


def test_an_append_that_raises_after_linking_still_gets_its_record_published(tmp_path):
    class LinkThenRaise(DirJournal):
        fail = False

        def append(self, seq, data):
            ok = super().append(seq, data)
            if self.fail and ok:
                self.fail = False
                raise OSError("unlink of the temp file failed")
            return ok

    t = InMemoryTransport()
    j = LinkThenRaise(tmp_path / "j")
    p = planner(t, j)
    p.dispatch(qi("A"), **FIELDS)
    implement(t)
    j.fail = True
    with pytest.raises(OSError):
        p.pump()
    assert len(p.deferred) == 1 and not t._queues[Channel.VERIFICATION]
    assert p.pump() == 0  # replay finds the event; the deferred record is already journalled
    assert p.lineages["A"].phase is Phase.VERIFYING and p.deferred == [] and not p.quarantined
    assert len(t._queues[Channel.VERIFICATION]) == 1  # and its request went out


# ---- loss or replacement of acknowledged history --------------------------------------------
#
# Storage / failure model these tests pin: event files can be lost or replaced (a deleted tail,
# a restored older copy of the journal directory, a rival history written after such a loss)
# while the acknowledgement anchor survives. Out of the model: journal and anchor rolled back
# together, and a writer who rewrites both.


def _two_holders(tmp_path, anchor=None):
    """Planner with A and B dispatched (events 1, 2, both acknowledged and published)."""
    t = SpoolTransport(tmp_path / "spool")
    p = planner(t, DirJournal(tmp_path / "j", anchor=anchor), "plan-1")
    p.dispatch(qi("A"), **FIELDS)
    p.dispatch(qi("B"), **fields("src/y"))
    return t, p


def _published(tmp_path):
    return sorted(p.name for p in (tmp_path / "spool" / "WORK").glob("*.json"))


def _events(tmp_path):
    return sorted(p.name for p in (tmp_path / "j").iterdir())


def test_tail_loss_under_a_running_planner_admits_nothing_from_either_planner(tmp_path):
    t, p1 = _two_holders(tmp_path)
    published = _published(tmp_path)
    (tmp_path / "j" / "000000000002.json").unlink()  # the journal loses its acknowledged tail

    # a planner started now would see a well-formed journal [1] in which B never existed and
    # src/y is free. It does not get that far: the anchor says event 2 is acknowledged.
    with pytest.raises(JournalCorrupt, match="JOURNAL_TRUNCATED:event 2 is acknowledged"):
        planner(t, DirJournal(tmp_path / "j"), "plan-2")
    with pytest.raises(JournalCorrupt, match="JOURNAL_TRUNCATED"):
        fleet_status(DirJournal(tmp_path / "j"))

    # the running planner still holds [1, 2] in memory. Every operation that could admit or
    # publish stops at the continuity check, before it decides anything.
    for attempt in (
        lambda: p1.dispatch(qi("D"), **fields("src/d")),  # compatible with its own view
        lambda: p1.dispatch(qi("C"), **fields("src/y")),
        lambda: p1.select([qi("D")]),
        lambda: p1.pump(),
        lambda: p1.recover(),
        lambda: p1.fail_execution("A", "x"),
    ):
        with pytest.raises(JournalCorrupt, match="JOURNAL_DIVERGED:event 2, which this planner"):
            attempt()
    assert _events(tmp_path) == ["000000000001.json"]  # nothing was appended
    assert _published(tmp_path) == published  # nothing was published
    assert set(p1.lineages) == {"A", "B"} and p1.state.seq == 2  # and nothing changed in memory


def test_a_replaced_tail_is_refused_by_the_running_and_by_every_new_planner(tmp_path):
    t, p1 = _two_holders(tmp_path)
    published = _published(tmp_path)
    j = tmp_path / "j"
    (j / "000000000002.json").unlink()
    # the rival history of the reported case: a lineage C over B's paths, as event 2, written
    # by something that did not go through the anchor (a planner would have been refused above)
    from project_atlas.orchestration.autonomy.dev_contracts import make_work

    c = make_work(
        task_id="C",
        execution_id="C-E1",
        lineage_root="C",
        acceptance_contract=("ok",),
        **fields("src/y"),
    )
    head1 = hashlib.sha256((j / "000000000001.json").read_bytes()).hexdigest()
    _forge(j, 2, head1, event="DISPATCH", root="C", work=encode(c))

    with pytest.raises(JournalCorrupt, match="JOURNAL_DIVERGED:event 2 is acknowledged as a diff"):
        planner(t, DirJournal(j), "plan-2")
    with pytest.raises(JournalCorrupt, match="JOURNAL_DIVERGED:event 2 is not the event this"):
        p1.dispatch(qi("D"), **fields("src/d"))
    with pytest.raises(JournalCorrupt, match="JOURNAL_DIVERGED"):
        p1.recover()
    assert _events(tmp_path) == ["000000000001.json", "000000000002.json"]
    assert _published(tmp_path) == published  # C's work was never published by anyone


def test_history_replaced_between_append_and_publish_is_caught_before_publication(tmp_path):
    """Detection is not enough: the event is linked, then lost, BEFORE the work is published."""

    class LostAfterLink(DirJournal):
        mode = ""

        def append(self, seq, data):
            ok = super().append(seq, data)
            if ok and self.mode == "lost":
                self._path(seq).unlink()
            elif ok and self.mode == "replaced":
                self._path(seq).write_bytes(data.replace(b"plan-1", b"plan-9"))
            return ok

    t = SpoolTransport(tmp_path / "spool")
    j = LostAfterLink(tmp_path / "j")
    p = planner(t, j, "plan-1")
    p.dispatch(qi("A"), **FIELDS)
    for mode, why in (("lost", "is gone"), ("replaced", "is not the event this planner holds")):
        j.mode = mode
        with pytest.raises(JournalCorrupt, match=f"JOURNAL_DIVERGED:event 2.*{why}"):
            p.dispatch(qi("B"), **fields("src/y"))
        j.mode = ""
        # the event existed on disk for a moment; it was never acknowledged, never applied to
        # the replica, and its work was never published
        assert set(p.lineages) == {"A"} and p.state.seq == 1
        assert len(_published(tmp_path)) == 1
        assert not (tmp_path / "j.ack" / "000000000002.ack").exists()
        (tmp_path / "j" / "000000000002.json").unlink(missing_ok=True)
    assert p.dispatch(qi("B"), **fields("src/y")).task_id == "B"  # healthy again: admitted
    assert len(_published(tmp_path)) == 2


def test_an_acknowledgement_by_a_rival_stops_the_writer_before_it_publishes(tmp_path):
    class RivalWins(DirJournal):
        armed = False

        def acknowledge(self, seq, digest):
            if self.armed:  # between this writer's link and its acknowledgement
                self.armed = False
                self._ack(seq).write_text("0" * 64, encoding="ascii")
            super().acknowledge(seq, digest)

    t = SpoolTransport(tmp_path / "spool")
    j = RivalWins(tmp_path / "j")
    p = planner(t, j)
    p.dispatch(qi("A"), **FIELDS)
    j.armed = True
    with pytest.raises(JournalCorrupt, match="JOURNAL_DIVERGED:event 2 is acknowledged as a diff"):
        p.dispatch(qi("B"), **fields("src/y"))
    assert set(p.lineages) == {"A"} and len(_published(tmp_path)) == 1


def test_an_unacknowledged_tail_event_may_be_lost_because_nothing_was_published_for_it(tmp_path):
    class DiesAfterLink(DirJournal):
        die = False

        def acknowledge(self, seq, digest):
            if self.die:
                raise KeyboardInterrupt  # the process dies between link and acknowledgement
            super().acknowledge(seq, digest)

    t = SpoolTransport(tmp_path / "spool")
    j = DiesAfterLink(tmp_path / "j")
    p = planner(t, j)
    p.dispatch(qi("A"), **FIELDS)
    j.die = True
    with pytest.raises(KeyboardInterrupt):
        p.dispatch(qi("B"), **fields("src/y"))
    assert _events(tmp_path)[-1] == "000000000002.json" and len(_published(tmp_path)) == 1
    (tmp_path / "j" / "000000000002.json").unlink()  # that unacknowledged event is then lost
    q = planner(t, DirJournal(tmp_path / "j"))  # continuity holds: the anchor ends at 1
    assert set(q.lineages) == {"A"}
    q.dispatch(qi("C"), **fields("src/y"))  # B was never acknowledged, so nothing conflicts
    assert len(_published(tmp_path)) == 2
    # had the event survived instead, the next planner would have acknowledged and published it
    # (covered by test_a_dispatch_that_was_journalled_but_not_published_is_published_by_recover)


def test_the_anchor_can_live_on_separate_storage_and_catches_a_restored_old_journal(tmp_path):
    anchor = tmp_path / "elsewhere" / "anchor"
    t, _p1 = _two_holders(tmp_path, anchor=anchor)
    assert sorted(a.name for a in anchor.iterdir()) == ["000000000001.ack", "000000000002.ack"]
    assert not (tmp_path / "j.ack").exists()
    old = (tmp_path / "j" / "000000000001.json").read_bytes()
    for f in (tmp_path / "j").iterdir():  # the journal directory is restored from an old copy
        f.unlink()
    (tmp_path / "j" / "000000000001.json").write_bytes(old)
    with pytest.raises(JournalCorrupt, match="JOURNAL_TRUNCATED"):
        planner(t, DirJournal(tmp_path / "j", anchor=anchor), "plan-2")
    for stray in ("notes.txt", "000000000003.ack.bak", "000000000000.ack"):
        (anchor / stray).write_text("0" * 64)
        with pytest.raises(JournalCorrupt, match="anchor holds something else"):
            fleet_status(DirJournal(tmp_path / "j", anchor=anchor))
        (anchor / stray).unlink()
    (anchor / "000000000001.ack").write_text("not a digest")
    with pytest.raises(JournalCorrupt, match="acknowledgement 1 is not a digest"):
        fleet_status(DirJournal(tmp_path / "j", anchor=anchor))


def test_limit_journal_and_anchor_rolled_back_together_are_not_detected_by_a_new_planner(
    tmp_path,
):
    """Outside the supported model, pinned so the limit is a fact and not a hope.

    When the anchor is lost together with the journal tail, a NEW planner has nothing that says
    event 2 ever existed and admits against the shorter history. The planner that was running
    is still stopped, because it holds event 2 itself.
    """
    t, p1 = _two_holders(tmp_path)
    (tmp_path / "j" / "000000000002.json").unlink()
    (tmp_path / "j.ack" / "000000000002.ack").unlink()
    p2 = planner(t, DirJournal(tmp_path / "j"), "plan-2")
    p2.dispatch(qi("C"), **fields("src/y"))  # collides with B, which p2 cannot know about
    assert len(_published(tmp_path)) == 3
    with pytest.raises(JournalCorrupt, match="JOURNAL_DIVERGED:event 2 is not the event this"):
        p1.dispatch(qi("D"), **fields("src/d"))
    assert len(_published(tmp_path)) == 3 and _events(tmp_path)[-1] == "000000000002.json"


def test_readers_and_writers_racing_never_see_a_truncated_journal(tmp_path):
    """An acknowledgement ahead of a reader's last read is progress, not loss."""
    n = 6
    t = SpoolTransport(tmp_path / "spool")
    stop = threading.Event()
    errors: list[str] = []

    def write(i):
        p = planner(t, DirJournal(tmp_path / "j"), f"plan-{i}")
        for k in range(12):
            for _ in range(50):
                try:
                    p.dispatch(qi(f"T{i}-{k}"), **fields(f"src/w{i}/k{k}"))
                    break
                except JournalContended:
                    continue
                except PlannerError as exc:  # pragma: no cover - reported below
                    errors.append(f"writer {i}: {exc}")
                    return

    def read():
        while not stop.is_set():
            try:
                fleet_status(DirJournal(tmp_path / "j"))
                planner(t, DirJournal(tmp_path / "j"), "reader").select([qi("X")])
            except PlannerError as exc:  # pragma: no cover - reported below
                errors.append(f"reader: {exc}")
                return

    writers = [threading.Thread(target=write, args=(i,)) for i in range(n)]
    readers = [threading.Thread(target=read) for _ in range(3)]
    for th in readers + writers:
        th.start()
    for th in writers:
        th.join()
    stop.set()
    for th in readers:
        th.join()
    assert errors == []
    rows = fleet_status(DirJournal(tmp_path / "j"))
    assert len(rows) == n * 12 and sorted(r["last_seq"] for r in rows) == list(range(1, n * 12 + 1))
    assert len(list((tmp_path / "j.ack").iterdir())) == n * 12
