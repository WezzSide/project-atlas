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
    by = {"A": (Verdict.FAIL, DEFECT), "B": (Verdict.PASS, ())}
    for _ in range(2):
        req = t.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity=VER)
        if req.task_id == "C":  # leave C verifying: put its request back by re-reading later
            keep = req
            req = t.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity=VER)
        v, f = by[req.task_id]
        t.publish(make_verdict(req, verdict=v, findings=f))
    p.pump()
    assert {r: s.phase for r, s in p.lineages.items()} == {
        "A": Phase.REPAIR_DISPATCHED,
        "B": Phase.INTEGRATION_READY,
        "C": Phase.VERIFYING,
    }

    q = planner(t, DirJournal(tmp_path / "journal"))  # "restart": nothing but the journal
    assert view(q) == view(p)
    assert q.completed == p.completed == {"B"} and q.blocked == p.blocked == {}
    assert q._by_task == p._by_task and set(q._works) == set(p._works)
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
    assert not any("at" in e or "time" in e for e in events)  # no wall clock in the journal


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
    with pytest.raises(PlannerError, match="JOURNAL_CONTENDED"):
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


def _journal_with(tmp_path, n_ready=1):
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
    with pytest.raises(PlannerError, match="hash chain is broken"):
        _open(tmp_path)
    with pytest.raises(PlannerError, match="hash chain is broken"):
        fleet_status(DirJournal(tmp_path / "j"))


def test_a_missing_middle_event_or_a_stray_file_is_refused(tmp_path):
    files = _journal_with(tmp_path)
    middle = files[1].read_bytes()
    files[1].unlink()
    with pytest.raises(PlannerError, match=r"JOURNAL_CORRUPT:directory is not events 1\.\.k"):
        _open(tmp_path)
    files[1].write_bytes(middle)
    assert _open(tmp_path).lineages["A"].phase is Phase.INTEGRATION_READY
    (tmp_path / "j" / "notes.txt").write_text("x")
    with pytest.raises(PlannerError, match=r"JOURNAL_CORRUPT:directory is not events 1\.\.k"):
        _open(tmp_path)


def test_garbage_and_non_object_events_are_refused(tmp_path):
    files = _journal_with(tmp_path)
    good = files[0].read_bytes()
    for bad in (b"{not json", b"[1, 2]", b"{}", b'{"v": 2}'):
        files[0].write_bytes(bad)
        with pytest.raises(PlannerError):
            _open(tmp_path)
    files[0].write_bytes(good)
    assert _open(tmp_path).state.seq == 3


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
        dict(event="DISPATCH", root="B", work=encode(w("B", ("src/x/a",)))),
        # a lineage root that already exists
        dict(event="DISPATCH", root="A", work=encode(w("A", ("src/q",)))),
        # root field that is not the work's own root
        dict(event="DISPATCH", root="B", work=encode(w("C", ("src/q",)))),
        # terminal on an already terminal (INTEGRATION_READY) lineage: no silent scope release
        dict(event="TERMINAL", root="A", phase="BLOCKED", reason="x", evidence=""),
        # a terminal phase that is not a refusal phase
        dict(event="TERMINAL", root="A", phase="DISPATCHED", reason="x", evidence=""),
        # release without a merge revision
        dict(event="RELEASE", root="A", evidence="main"),
        dict(event="RELEASE", root="nope", evidence=MERGED),
        dict(event="NONSENSE", root="A"),
    ]
    for body in cases:
        _forge(j, n + 1, head, **body)
        with pytest.raises(PlannerError):
            _open(tmp_path)
        (j / f"{n + 1:012d}.json").unlink()
    # a record whose seal does not verify
    wire = json.loads(encode(w("B", ("src/q",))))
    wire["body"]["allowed_paths"] = ["src/x"]
    _forge(j, n + 1, head, event="DISPATCH", root="B", work=json.dumps(wire))
    with pytest.raises(Exception, match="seal"):
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
    assert q.recover() == []  # journalled now: never fed twice
    verify(t)
    ver = t.claim(Channel.VERDICT, role=Role.PLANNER, identity="vps3-plan")  # same crash, later
    r = planner(t, DirJournal(tmp_path / "j"))
    assert r.recover() == [f"REPLAYED:VERDICT:{ver.seal}"]
    assert r.lineages["A"].phase is Phase.INTEGRATION_READY and r.completed == {"A"}
    assert r.recover() == []
    # records claimed by a different planner identity are not this planner's to replay
    other = planner(t, DirJournal(tmp_path / "j2"), "other-plan")
    assert other.recover() == []


def test_a_corrupt_journal_makes_pump_raise_instead_of_quarantining_records(tmp_path):
    t = InMemoryTransport()
    p = planner(t, DirJournal(tmp_path / "j"))
    p.dispatch(qi("A"), **FIELDS)
    implement(t)
    _forge(tmp_path / "j", 2, "0" * 64, event="NONSENSE", root="A")
    with pytest.raises(PlannerError, match=r"JOURNAL_CORRUPT|hash chain"):
        p.pump()
    assert len(t._queues[Channel.RESULT]) == 1 and not p.quarantined  # nothing was consumed


def test_the_default_journal_is_volatile_and_per_planner():
    t = InMemoryTransport()
    p = planner(t, None)
    p.dispatch(qi("A"), **FIELDS)
    assert isinstance(p.journal, MemoryJournal) and p.state.seq == 1
    q = planner(t, None)  # no shared journal: no shared ownership (documented limit)
    q.dispatch(qi("B"), **FIELDS)
    assert q.in_flight() == {"B"} and p.in_flight() == {"A"}
