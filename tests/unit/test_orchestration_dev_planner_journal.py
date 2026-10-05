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


@pytest.fixture(autouse=True)
def _short_adoption_wait(monkeypatch):
    """Adoption waits 2 s for a live writer in production; tests use a dead or absent one."""
    from project_atlas.orchestration.autonomy import dev_planner

    monkeypatch.setattr(dev_planner, "ADOPT_GRACE_TRIES", 3)
    monkeypatch.setattr(dev_planner, "ADOPT_GRACE_STEP", 0.001)


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
    # with event 1's acknowledgement rewritten to match the edit (outside the model: a writer
    # who rewrites journal and anchor), the chain still refuses it at event 2
    (tmp_path / "j.ack" / "000000000001.ack").write_text(
        hashlib.sha256(files[0].read_bytes()).hexdigest(), encoding="ascii"
    )
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
    owned = "executor" in body or body.get("event") == "REASSIGN"
    version = body.pop("v", 3 if owned else 2 if "handover" in body else 1)
    ev = {"v": version, "seq": seq, "prev": prev, "planner": "forger", **body}
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
    (j / f"{n + 1:012d}.json").unlink()  # replay acknowledged nothing: no ack to remove
    assert not (tmp_path / "j.ack" / f"{n + 1:012d}.ack").exists()
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
    # the assertion is recorded; it does not open the scope (ATLAS-DEVQ-0009)
    with pytest.raises(PlannerError, match=r"^SCOPE_RETAINED:A:src/x\|src/x$"):
        p.dispatch(qi("B"), **FIELDS)
    p.dispatch(qi("B"), **fields("src/y"))
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
            "repository_key": "wezzside/project-atlas",
            "base_revision": BASE,
            "allowed_paths": ("src/x",),
            "holds_scope": True,
            "scope_released": "",
            "handover": "",
            "handed_over_to": (),
            "executor": "",
            "epoch": 0,
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
            "repository_key": "wezzside/project-atlas",
            "base_revision": BASE,
            "allowed_paths": ("src/y",),
            "holds_scope": False,
            "scope_released": "",
            "handover": "",
            "handed_over_to": (),
            "executor": "",
            "epoch": 0,
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


def test_an_event_lost_or_replaced_before_its_acknowledgement_is_never_published(tmp_path):
    """Prevention, not only detection: linked, then lost or replaced, before the read-back."""

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
        # the test removes what is left of the unacknowledged event; left in place, a replaced
        # event would be validated and adopted by the next replay (see the adoption test below)
        (tmp_path / "j" / "000000000002.json").unlink(missing_ok=True)
    assert p.dispatch(qi("B"), **fields("src/y")).task_id == "B"  # admitted once it is removed
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
                except Exception as exc:  # pragma: no cover - reported below
                    errors.append(f"writer {i}: {type(exc).__name__}: {exc}")
                    return

    seen: list[int] = []

    def read():
        while not stop.is_set():
            try:
                seen.append(len(fleet_status(DirJournal(tmp_path / "j"))))
                planner(t, DirJournal(tmp_path / "j"), "reader").select([qi("X")])
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(f"reader: {type(exc).__name__}: {exc}")
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
    assert any(0 < k < n * 12 for k in seen)  # a reader saw the journal while it was growing
    rows = fleet_status(DirJournal(tmp_path / "j"))
    assert len(rows) == n * 12 and sorted(r["last_seq"] for r in rows) == list(range(1, n * 12 + 1))
    acks = sorted(a.name for a in (tmp_path / "j.ack").iterdir() if a.suffix == ".ack")
    assert acks == [f"{k:012d}.ack" for k in range(1, n * 12 + 1)]


def test_head_replaced_between_decide_and_append_is_caught_before_publication(tmp_path):
    class HeadSwapped(DirJournal):
        armed = False

        def append(self, seq, data):
            if self.armed:  # after this writer's replay and decision, before its link
                self.armed = False
                prev = self._path(seq - 1)
                prev.write_bytes(prev.read_bytes().replace(b"plan-1", b"plan-9"))
            return super().append(seq, data)

    t = SpoolTransport(tmp_path / "spool")
    j = HeadSwapped(tmp_path / "j")
    p = planner(t, j, "plan-1")
    p.dispatch(qi("A"), **FIELDS)
    j.armed = True
    with pytest.raises(JournalCorrupt, match="JOURNAL_DIVERGED:event 1 is not the event this"):
        p.dispatch(qi("B"), **fields("src/y"))
    assert set(p.lineages) == {"A"} and len(_published(tmp_path)) == 1
    assert not (tmp_path / "j.ack" / "000000000002.ack").exists()


def test_limit_an_event_lost_after_its_acknowledgement_is_published_then_everything_stops(
    tmp_path,
):
    """The one publication window: after the read-back, before the publish."""

    class LostAfterAck(DirJournal):
        armed = False

        def acknowledge(self, seq, digest):
            super().acknowledge(seq, digest)
            if self.armed:
                self.armed = False
                self._path(seq).unlink()

    t = SpoolTransport(tmp_path / "spool")
    j = LostAfterAck(tmp_path / "j")
    p = planner(t, j)
    p.dispatch(qi("A"), **FIELDS)
    j.armed = True
    p.dispatch(qi("B"), **fields("src/y"))  # acknowledged, then lost, then published
    assert len(_published(tmp_path)) == 2
    # no conflicting admission can follow: every later operation of every planner stops
    with pytest.raises(JournalCorrupt, match="JOURNAL_DIVERGED:event 2, which this planner"):
        p.dispatch(qi("C"), **fields("src/y"))
    with pytest.raises(JournalCorrupt, match="JOURNAL_TRUNCATED:event 2 is acknowledged"):
        planner(t, DirJournal(tmp_path / "j"), "plan-2")
    assert len(_published(tmp_path)) == 2


def test_limit_a_running_planner_does_not_notice_a_lost_event_below_its_head(tmp_path):
    """Pinned limit: only the head is re-checked by a planner that already applied the rest.

    Its replica is still the complete acknowledged history, so it refuses what collides; but
    it keeps appending to a journal that no new planner can open.
    """
    t, p1 = _two_holders(tmp_path)
    (tmp_path / "j" / "000000000001.json").unlink()
    p1.dispatch(qi("D"), **fields("src/d"))  # not noticed: admitted and published
    assert len(_published(tmp_path)) == 3
    with pytest.raises(PlannerError, match=r"^SCOPE_COLLISION:A:src/x\|src/x$"):
        p1.dispatch(qi("E"), **FIELDS)  # still decided against the full acknowledged history
    with pytest.raises(JournalCorrupt, match=r"directory is not events 1\.\.k: missing 1"):
        planner(t, DirJournal(tmp_path / "j"), "plan-2")
    with pytest.raises(JournalCorrupt):
        fleet_status(DirJournal(tmp_path / "j"))


def test_a_journal_without_its_anchor_does_not_replay_past_its_first_event(tmp_path):
    t, p1 = _two_holders(tmp_path)
    p1.dispatch(qi("E"), **fields("src/e"))  # events 1..3, acknowledgements 1..3
    acks = tmp_path / "j.ack"

    def refused(why, **kw):
        with pytest.raises(JournalCorrupt, match=why):
            planner(t, DirJournal(tmp_path / "j", **kw), "plan-2")
        with pytest.raises(JournalCorrupt, match=why):
            fleet_status(DirJournal(tmp_path / "j", **kw))

    # a planner pointed at a different (empty) anchor
    refused("JOURNAL_UNANCHORED:event 1 has a successor", anchor=tmp_path / "other-anchor")
    # one acknowledgement in the middle is gone
    saved = (acks / "000000000002.ack").read_bytes()
    (acks / "000000000002.ack").unlink()
    refused("JOURNAL_UNANCHORED:event 2 has a successor")
    # tail lost together with PART of the anchor: events 2, 3 and acknowledgement 2 gone
    (tmp_path / "j" / "000000000003.json").rename(tmp_path / "ev3")
    (tmp_path / "j" / "000000000002.json").rename(tmp_path / "ev2")
    refused("JOURNAL_TRUNCATED:event 3 is acknowledged but the journal ends at 1")
    (tmp_path / "ev2").rename(tmp_path / "j" / "000000000002.json")
    (tmp_path / "ev3").rename(tmp_path / "j" / "000000000003.json")
    (acks / "000000000002.ack").write_bytes(saved)
    # an acknowledgement far beyond the journal (non-contiguous anchor)
    (acks / "000000000009.ack").write_text("0" * 64, encoding="ascii")
    refused("JOURNAL_TRUNCATED:event 9 is acknowledged but the journal ends at 3")
    (acks / "000000000009.ack").unlink()
    # the whole anchor directory deleted: DirJournal re-creates it empty, replay refuses
    for a in acks.iterdir():
        a.unlink()
    acks.rmdir()
    refused("JOURNAL_UNANCHORED:event 1 has a successor")
    assert acks.is_dir() and list(acks.iterdir()) == []  # re-created, nothing acknowledged


def test_an_acknowledgement_is_created_exclusively_and_never_overwritten(tmp_path):
    class LateSight(DirJournal):
        blind = 0

        def acknowledged(self, seq):
            if self.blind:  # the rival's acknowledgement lands after this look
                self.blind -= 1
                return None
            return super().acknowledged(seq)

    j = LateSight(tmp_path / "j")
    assert j.append(1, b"{}") is True
    rival = "0" * 64
    j._ack(1).write_text(rival, encoding="ascii")
    j.blind = 1
    with pytest.raises(JournalCorrupt, match="acknowledged as a different event"):
        j.acknowledge(1, hashlib.sha256(b"{}").hexdigest())
    assert j._ack(1).read_text(encoding="ascii") == rival  # the first one stands
    assert [a.name for a in (tmp_path / "j.ack").iterdir()] == ["000000000001.ack"]


def test_an_acknowledgement_not_written_by_the_events_own_commit_is_a_recorded_repair(
    tmp_path,
):
    class DiesAfterLink(DirJournal):
        die = False

        def acknowledge(self, seq, digest):
            if self.die:
                raise KeyboardInterrupt
            super().acknowledge(seq, digest)

    t = SpoolTransport(tmp_path / "spool")
    j = DiesAfterLink(tmp_path / "j")
    p = planner(t, j, "plan-1")
    p.dispatch(qi("A"), **FIELDS)
    assert j.repairs() == ()  # an ordinary commit acknowledges its own event: no repair
    j.die = True
    with pytest.raises(KeyboardInterrupt):
        p.dispatch(qi("B"), **fields("src/y"))
    ack2 = tmp_path / "j.ack" / "000000000002.ack"
    assert not ack2.exists() and len(_published(tmp_path)) == 1
    rows = fleet_status(DirJournal(tmp_path / "j"))  # a projection reads it and writes nothing
    assert [r["lineage_root"] for r in rows] == ["A", "B"] and not ack2.exists()
    jq = DirJournal(tmp_path / "j")
    q = planner(t, jq, "plan-2")  # replaying writes nothing either
    assert q.in_flight() == {"A", "B"} and not ack2.exists() and jq.repairs() == ()
    # before it publishes for B (or appends after it) the planner adopts the event: it cannot
    # know whether B's writer died or B's acknowledgement was lost, so it says so, durably
    assert q.recover() == ["REPUBLISHED:B:WORK"] and len(_published(tmp_path)) == 2
    digest = hashlib.sha256((tmp_path / "j" / "000000000002.json").read_bytes()).hexdigest()
    assert jq.repairs() == ({"seq": 2, "kind": "ADOPTED", "by": "plan-2", "digest": digest},)
    assert ack2.read_text() == digest
    assert q.recover() == [] and len(jq.repairs()) == 1  # recorded once
    # the record survives restarts and later commits, and does not stop anything
    r = planner(t, DirJournal(tmp_path / "j"), "plan-3")
    r.dispatch(qi("C"), **fields("src/c"))
    assert [x["seq"] for x in DirJournal(tmp_path / "j").repairs()] == [2]


def test_a_lost_acknowledgement_of_the_head_is_restored_only_with_a_repair_record(tmp_path):
    _t, p1 = _two_holders(tmp_path)
    j = p1.journal
    ack2 = tmp_path / "j.ack" / "000000000002.ack"
    digest = ack2.read_text()
    ack2.unlink()  # p1 had seen this acknowledgement: for p1 this is a definite loss
    p1.select([qi("Z")])
    assert j.repairs() == ({"seq": 2, "kind": "RESTORED", "by": "plan-1", "digest": digest},)
    assert ack2.read_text() == digest
    p1.dispatch(qi("D"), **fields("src/d"))  # continuity re-established: it goes on
    assert len(j.repairs()) == 1
    # a replaced head is not repaired: nothing is recorded, nothing acknowledged
    ev3 = tmp_path / "j" / "000000000003.json"
    (tmp_path / "j.ack" / "000000000003.ack").unlink()
    ev3.write_bytes(ev3.read_bytes().replace(b"plan-1", b"plan-9"))
    with pytest.raises(JournalCorrupt, match="JOURNAL_DIVERGED:event 3 is not the event this"):
        p1.select([qi("Z")])
    assert len(j.repairs()) == 1 and not (tmp_path / "j.ack" / "000000000003.ack").exists()


def test_the_repair_record_is_written_before_the_acknowledgement(tmp_path):
    class DiesBeforeAck(DirJournal):
        armed = False

        def acknowledge(self, seq, digest):
            if self.armed:
                raise KeyboardInterrupt  # dies after the record, before the acknowledgement
            super().acknowledge(seq, digest)

    t = SpoolTransport(tmp_path / "spool")
    j = DiesBeforeAck(tmp_path / "j")
    p = planner(t, j, "plan-1")
    p.dispatch(qi("A"), **FIELDS)
    ack1 = tmp_path / "j.ack" / "000000000001.ack"
    ack1.unlink()
    j.armed = True
    with pytest.raises(KeyboardInterrupt):
        p.select([qi("Z")])
    assert not ack1.exists() and [r["kind"] for r in j.repairs()] == ["RESTORED"]
    j.armed = False
    p.select([qi("Z")])  # finished later; the first record stands, no second one
    assert ack1.exists() and len(j.repairs()) == 1

    class NoRecord(DirJournal):
        def record_repair(self, seq, digest, kind, by):
            raise OSError("anchor is read-only")

    t2 = SpoolTransport(tmp_path / "spool2")
    j2 = NoRecord(tmp_path / "j2")
    p2 = planner(t2, j2, "plan-1")
    p2.dispatch(qi("A"), **FIELDS)
    ack = tmp_path / "j2.ack" / "000000000001.ack"
    ack.unlink()
    with pytest.raises(OSError):  # no record, so no acknowledgement and no further step
        p2.dispatch(qi("B"), **fields("src/y"))
    assert not ack.exists() and len(list((tmp_path / "j2").iterdir())) == 1


def test_repair_records_are_part_of_the_anchor_and_are_validated(tmp_path):
    t, p1 = _two_holders(tmp_path)
    (tmp_path / "j.ack" / "000000000002.ack").unlink()
    p1.select([qi("Z")])
    anchor = tmp_path / "j.ack"
    assert sorted(a.name for a in anchor.iterdir()) == [
        "000000000001.ack",
        "000000000002.ack",
        "000000000002.repair",
    ]
    assert planner(t, DirJournal(tmp_path / "j"), "plan-2").state.seq == 2  # a full open accepts it
    (anchor / "000000000009.repair").write_text("{}")
    with pytest.raises(JournalCorrupt, match=r"repair record 000000000009\.repair"):
        DirJournal(tmp_path / "j").repairs()
    (anchor / "000000000009.repair").unlink()
    (anchor / "000000000002.repair.bak").write_text("x")
    with pytest.raises(JournalCorrupt, match="anchor holds something else"):
        fleet_status(DirJournal(tmp_path / "j"))
    assert MemoryJournal().repairs() == ()


def test_a_memory_journal_that_changes_under_its_planner_stops_it():
    j = MemoryJournal()
    t = InMemoryTransport()
    p = planner(t, j)
    p.dispatch(qi("A"), **FIELDS)
    p.dispatch(qi("B"), **fields("src/y"))
    j._events[1] = j._events[1].replace(b"vps3-plan", b"someone")
    with pytest.raises(JournalCorrupt, match="JOURNAL_DIVERGED:event 2"):
        p.dispatch(qi("C"), **fields("src/c"))
    j._events.pop()
    with pytest.raises(JournalCorrupt, match="JOURNAL_DIVERGED:event 2"):
        p.select([qi("C")])
    assert len(t._queues[Channel.WORK]) == 2


def test_a_planner_one_event_behind_a_lost_tail_stops_without_appending(tmp_path):
    t = SpoolTransport(tmp_path / "spool")
    p1 = planner(t, DirJournal(tmp_path / "j"), "plan-1")
    p1.dispatch(qi("A"), **FIELDS)
    stale = planner(t, DirJournal(tmp_path / "j"), "plan-2")  # at event 1
    p1.dispatch(qi("B"), **fields("src/y"))  # event 2, acknowledged; stale has not seen it
    (tmp_path / "j" / "000000000002.json").unlink()
    for attempt in (
        lambda: stale.dispatch(qi("C"), **fields("src/y")),  # over B's paths
        lambda: stale.select([qi("C")]),
        lambda: stale.recover(),
    ):
        with pytest.raises(JournalCorrupt, match="JOURNAL_TRUNCATED:event 2 is acknowledged"):
            attempt()
    assert _events(tmp_path) == ["000000000001.json"] and len(_published(tmp_path)) == 2
    assert set(stale.lineages) == {"A"}


def test_limit_a_failed_commit_leaves_an_event_that_the_next_commit_attempt_adopts(tmp_path):
    """Pinned: an unacknowledged stored event is applied by any replay, adopted (with a
    record) by the next commit attempt or recover, and published by recover."""

    class Replaced(DirJournal):
        armed = False

        def append(self, seq, data):
            ok = super().append(seq, data)
            if ok and self.armed:
                self.armed = False
                self._path(seq).write_bytes(data.replace(b"plan-1", b"plan-9"))
            return ok

    t = SpoolTransport(tmp_path / "spool")
    j = Replaced(tmp_path / "j")
    p = planner(t, j, "plan-1")
    p.dispatch(qi("A"), **FIELDS)
    j.armed = True
    with pytest.raises(JournalCorrupt, match="JOURNAL_DIVERGED:event 2"):
        p.dispatch(qi("B"), **fields("src/y"))
    assert set(p.lineages) == {"A"} and len(_published(tmp_path)) == 1
    p.select([qi("Z")])  # the same planner's next replay validates and applies it
    assert p.lineages["B"].dispatched_by == "plan-9" and p.state.seq == 2
    assert not (tmp_path / "j.ack" / "000000000002.ack").exists()  # replay writes nothing
    assert p.pump() == 0 and len(_published(tmp_path)) == 1  # not published by replay or pump
    with pytest.raises(PlannerError, match="already in use"):
        p.dispatch(qi("B"), **fields("src/y"))
    # that refused dispatch was a commit attempt: it adopted the event it would have built on
    assert [(r["seq"], r["kind"], r["by"]) for r in j.repairs()] == [(2, "ADOPTED", "plan-1")]
    assert (tmp_path / "j.ack" / "000000000002.ack").exists()
    assert p.recover() == ["REPUBLISHED:B:WORK"] and len(_published(tmp_path)) == 2


def test_the_loss_of_the_anchor_alone_under_a_running_planner_leaves_a_repair_record(tmp_path):
    """Outside the model. The planner at the head goes on, but the loss is on record."""
    t, p1 = _two_holders(tmp_path)
    for a in (tmp_path / "j.ack").iterdir():
        a.unlink()
    p1.dispatch(qi("D"), **fields("src/d"))  # continues, against its complete replica
    # ... but not silently: it had seen its head acknowledged, so it records the loss
    assert sorted(a.name for a in (tmp_path / "j.ack").iterdir()) == [
        "000000000002.ack",
        "000000000002.repair",
        "000000000003.ack",
    ]
    assert [r["kind"] for r in p1.journal.repairs()] == ["RESTORED"]
    with pytest.raises(PlannerError, match=r"^SCOPE_COLLISION:A:"):
        p1.dispatch(qi("E"), **FIELDS)
    with pytest.raises(JournalCorrupt, match="JOURNAL_UNANCHORED:event 1"):
        planner(t, DirJournal(tmp_path / "j"), "plan-2")
    # an anchor that cannot be written fails closed with an OSError: nothing published
    for a in (tmp_path / "j.ack").iterdir():
        a.unlink()
    (tmp_path / "j.ack").rmdir()
    with pytest.raises(OSError):
        p1.dispatch(qi("F"), **fields("src/f"))
    assert _events(tmp_path)[-1] == "000000000003.json" and len(_published(tmp_path)) == 3
    assert "F" not in p1.lineages


def test_limit_partial_anchor_loss_lets_a_stale_running_planner_admit_over_a_lost_lineage(
    tmp_path,
):
    """Pinned, outside the model: journal tail AND part of the anchor lost together."""
    t = SpoolTransport(tmp_path / "spool")
    p1 = planner(t, DirJournal(tmp_path / "j"), "plan-1")
    p1.dispatch(qi("A"), **FIELDS)
    stale = planner(t, DirJournal(tmp_path / "j"), "plan-2")  # at event 1
    p1.dispatch(qi("B"), **fields("src/y"))
    p1.dispatch(qi("E"), **fields("src/e"))
    for lost in ("j/000000000002.json", "j/000000000003.json", "j.ack/000000000002.ack"):
        (tmp_path / lost).unlink()  # acknowledgement 3 survives
    with pytest.raises(JournalCorrupt, match="JOURNAL_TRUNCATED:event 3"):
        planner(t, DirJournal(tmp_path / "j"), "plan-3")  # a full open lists the anchor
    stale.dispatch(qi("C"), **fields("src/y"))  # probes only acknowledgement 2: admitted
    assert len(_published(tmp_path)) == 4  # A, B, E and the colliding C
    with pytest.raises(JournalCorrupt):
        p1.select([qi("Z")])  # the planner whose head was lost stops


def test_adoption_records_before_it_acknowledges_and_waits_for_a_live_writer(tmp_path):
    from project_atlas.orchestration.autonomy import dev_planner

    class Orphan(DirJournal):
        die = False
        late_ack = None

        def acknowledge(self, seq, digest):
            if self.die:
                raise KeyboardInterrupt
            super().acknowledge(seq, digest)

        looks = 0

        def acknowledged(self, seq):
            out = super().acknowledged(seq)
            if self.late_ack:
                self.looks += 1
                if self.looks == 2:  # after the first look found nothing: during the wait
                    late, self.late_ack = self.late_ack, None
                    late()
            return out

    t = SpoolTransport(tmp_path / "spool")
    j = Orphan(tmp_path / "j")
    p = planner(t, j, "plan-1")
    p.dispatch(qi("A"), **FIELDS)
    j.die = True
    with pytest.raises(KeyboardInterrupt):
        p.dispatch(qi("B"), **fields("src/y"))  # event 2 linked, never acknowledged
    ack2 = tmp_path / "j.ack" / "000000000002.ack"
    digest = hashlib.sha256((tmp_path / "j" / "000000000002.json").read_bytes()).hexdigest()

    # (1) the adopter dies between its record and its acknowledgement: record without ack
    q = planner(t, j, "plan-2")
    with pytest.raises(KeyboardInterrupt):
        q.recover()
    assert [(r["seq"], r["kind"], r["by"]) for r in j.repairs()] == [(2, "ADOPTED", "plan-2")]
    assert not ack2.exists() and len(_published(tmp_path)) == 1
    # (2) the next adopter finishes; the FIRST record stands, it is not overwritten
    j.die = False
    r = planner(t, j, "plan-3")
    assert r.recover() == ["REPUBLISHED:B:WORK"] and ack2.read_text() == digest
    assert [(x["seq"], x["by"]) for x in j.repairs()] == [(2, "plan-2")]

    # (3) a writer that is merely slow is waited for: its own acknowledgement, no record
    t2 = SpoolTransport(tmp_path / "spool2")
    j2 = Orphan(tmp_path / "j2")
    w = planner(t2, j2, "plan-1")
    w.dispatch(qi("A"), **FIELDS)
    j2.die = True
    with pytest.raises(KeyboardInterrupt):
        w.dispatch(qi("B"), **fields("src/y"))
    j2.die = False
    d2 = hashlib.sha256((tmp_path / "j2" / "000000000002.json").read_bytes()).hexdigest()
    slow = planner(t2, j2, "plan-2")
    j2.late_ack = lambda: DirJournal.acknowledge(j2, 2, d2)
    assert slow.recover() == ["REPUBLISHED:B:WORK"] and j2.repairs() == ()
    assert j2.looks >= 2 and j2.late_ack is None  # it looked again instead of adopting at once
    assert dev_planner.ADOPT_GRACE_TRIES * dev_planner.ADOPT_GRACE_STEP < 0.1  # the fixture

    # (4) an acknowledgement for another digest is never adopted over
    t3 = SpoolTransport(tmp_path / "spool3")
    j3 = Orphan(tmp_path / "j3")
    x = planner(t3, j3, "plan-1")
    x.dispatch(qi("A"), **FIELDS)
    j3.die = True
    with pytest.raises(KeyboardInterrupt):
        x.dispatch(qi("B"), **fields("src/y"))
    j3.die = False
    y = planner(t3, j3, "plan-2")
    (tmp_path / "j3.ack" / "000000000002.ack").write_text("0" * 64, encoding="ascii")
    with pytest.raises(JournalCorrupt, match="JOURNAL_DIVERGED:event 2 is acknowledged as a diff"):
        y.recover()
    assert j3.repairs() == () and len(list((tmp_path / "spool3" / "WORK").glob("*.json"))) == 1


def test_the_production_adoption_wait_is_far_above_a_live_writers_gap():
    import ast
    import inspect

    from project_atlas.orchestration.autonomy import dev_planner

    # the module's own values as written in its source, not the ones the test fixture sets
    values = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in ast.parse(inspect.getsource(dev_planner)).body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id.startswith("ADOPT_GRACE_")
    }
    assert values["ADOPT_GRACE_TRIES"] * values["ADOPT_GRACE_STEP"] >= 2.0


def test_a_head_acknowledgement_lost_between_replay_and_append_is_restored_with_a_record(
    tmp_path,
):
    class LosesAckAfterSync(DirJournal):
        armed = False

        def acknowledged(self, seq):
            out = super().acknowledged(seq)
            if self.armed and out is not None:  # the replay saw it; then it disappears
                self.armed = False
                self._ack(seq).unlink()
            return out

    t = SpoolTransport(tmp_path / "spool")
    j = LosesAckAfterSync(tmp_path / "j")
    p = planner(t, j, "plan-1")
    p.dispatch(qi("A"), **FIELDS)
    j.armed = True
    p.dispatch(qi("B"), **fields("src/y"))  # the check right before the append reads the file
    assert [(r["seq"], r["kind"]) for r in j.repairs()] == [(1, "RESTORED")]
    assert sorted(a.name for a in (tmp_path / "j.ack").iterdir() if a.suffix == ".ack") == [
        "000000000001.ack",
        "000000000002.ack",
    ]
    assert planner(t, DirJournal(tmp_path / "j"), "plan-2").state.seq == 2  # still opens


def test_repair_records_do_not_count_as_acknowledgements(tmp_path):
    t, _p1 = _two_holders(tmp_path)
    j = DirJournal(tmp_path / "j")
    j.record_repair(9, "0" * 64, "ADOPTED", "someone")  # a record far beyond the journal
    assert j.high_water(2, full=True) == 2  # not an acknowledged event: no truncation alarm
    assert planner(t, DirJournal(tmp_path / "j"), "plan-2").state.seq == 2


def test_a_repair_record_that_is_not_there_after_the_write_stops_the_repair(tmp_path, monkeypatch):
    _, p1 = _two_holders(tmp_path)
    ack = tmp_path / "j.ack" / "000000000002.ack"
    ack.unlink()

    def phantom(src, dst, **kw):
        raise FileExistsError(dst)  # claims the record exists; it does not

    monkeypatch.setattr("os.link", phantom)
    with pytest.raises(JournalCorrupt, match="repair record 2 could not be written"):
        p1.select([qi("Z")])
    assert not ack.exists()  # no record, so no acknowledgement


def test_a_repair_record_must_name_its_own_sequence_number_and_a_digest(tmp_path):
    _two_holders(tmp_path)
    anchor = tmp_path / "j.ack"
    digest = (anchor / "000000000002.ack").read_text()
    good = {"v": 1, "seq": 2, "digest": digest, "kind": "RESTORED", "by": "plan-1"}
    for bad in ({"seq": 1}, {"digest": "not-a-digest"}, {"kind": "FIXED"}, {"by": 7}):
        (anchor / "000000000002.repair").write_text(json.dumps(good | bad))
        with pytest.raises(JournalCorrupt, match=r"repair record 000000000002\.repair"):
            DirJournal(tmp_path / "j").repairs()
    (anchor / "000000000002.repair").write_text(json.dumps(good))
    assert [r["seq"] for r in DirJournal(tmp_path / "j").repairs()] == [2]


# ---- verified scope handover (ATLAS-DEVQ-0009) ----------------------------------------------

BASE2 = "9" * 40


class Observer:
    """Reports a result in a base only for the pairs it was given; records every question."""

    identity = "observer:test"

    def __init__(self, *contained, fail=None, answer=None):
        self.contained, self.fail, self.answer, self.calls = set(contained), fail, answer, []

    def result_in_base(self, *, repository, result_revision, base_revision):
        self.calls.append((repository, result_revision, base_revision))
        if self.fail is not None:
            raise self.fail
        if self.answer is not None:
            return self.answer
        if (result_revision, base_revision) in self.contained:
            return {"merge_base": result_revision}
        return None


def observing(t, journal, observer, identity="vps3-plan"):
    return Planner(
        t, identity=identity, verifier_identities=(VER,), journal=journal, observer=observer
    )


def on(base, *paths):
    return {**fields(*paths), "base_revision": base}


def _entry(root="A", **kw):
    return {
        "root": root,
        "basis": "RESULT_IN_BASE",
        "result_revision": REV1,
        "base_revision": BASE2,
        "observer": "observer:test",
        "evidence": {"merge_base": REV1},
        **kw,
    }


def test_a_scope_is_handed_over_only_when_the_result_is_observed_in_the_new_base(tmp_path):
    t = InMemoryTransport()
    obs = Observer((REV1, BASE2))
    p = observing(t, DirJournal(tmp_path / "j"), obs)
    p.dispatch(qi("A"), **FIELDS)
    to_ready(t, p)
    # a work on A's own base: A's result is not observed in it, so A still owns the paths
    with pytest.raises(PlannerError, match=r"^SCOPE_COLLISION:A:src/x/b\|src/x$"):
        p.dispatch(qi("B"), **on(BASE, "src/x/b"))
    assert obs.calls == [("WezzSide/project-atlas", REV1, BASE)]
    wb = p.dispatch(qi("B"), **on(BASE2, "src/x/b"))
    a = p.lineages["A"]
    assert wb.base_revision == BASE2 and p.in_flight() == {"B"}
    assert a.phase is Phase.INTEGRATION_READY and a.scope_released == ""  # no assertion used
    assert a.handover == BASE2 and a.handed_over_to == ["B"]
    assert a.history[-1] == f"SCOPE_HANDED_OVER:B:{BASE2}" and a.last_seq == p.state.seq
    ev = json.loads((tmp_path / "j" / f"{p.state.seq:012d}.json").read_text())
    assert ev["event"] == "DISPATCH" and ev["handover"] == [_entry()] and ev["v"] == 2
    first = json.loads((tmp_path / "j" / "000000000001.json").read_text())
    assert first["v"] == 1  # an event without a handover is written as before
    # durable and derived from the journal alone: a planner without an observer replays it
    q = planner(InMemoryTransport(), DirJournal(tmp_path / "j"))
    assert q.lineages["A"].handover == BASE2 and q.in_flight() == {"B"}
    rows = {r["lineage_root"]: r for r in fleet_status(DirJournal(tmp_path / "j"))}
    assert rows["A"]["holds_scope"] is False and rows["A"]["handover"] == BASE2
    assert rows["A"]["handed_over_to"] == ("B",) and rows["B"]["handed_over_to"] == ()
    # a handed-over scope is not open: every later work over it needs its own observation
    with pytest.raises(PlannerError, match=r"^SCOPE_RETAINED:A:src/x/c\|src/x$"):
        q.dispatch(qi("C"), **on(BASE2, "src/x/c"))  # this planner observes nothing
    with pytest.raises(PlannerError, match=r"^SCOPE_RETAINED:A:src/x/c\|src/x$"):
        p.dispatch(qi("C"), **on(BASE, "src/x/c"))  # a base that does not contain the result
    p.dispatch(qi("C"), **on(BASE2, "src/x/c"))
    assert a.handed_over_to == ["B", "C"] and a.handover == BASE2
    # and the new owner is a holder like any other
    with pytest.raises(PlannerError, match=r"^SCOPE_COLLISION:B:src/x/b\|src/x/b$"):
        p.dispatch(qi("D"), **on(BASE2, "src/x/b"))


def test_one_dispatch_asks_the_observer_once_per_result_and_base(tmp_path):
    t = InMemoryTransport()
    obs = Observer((REV1, BASE2))
    p = observing(t, DirJournal(tmp_path / "j"), obs)
    p.dispatch(qi("A"), **FIELDS)
    to_ready(t, p)
    p.dispatch(qi("A2"), **fields("src/w"))
    to_ready(t, p)  # same result revision REV1 as A
    p.dispatch(qi("B"), **on(BASE2, "src/x", "src/w"))
    assert obs.calls == [("WezzSide/project-atlas", REV1, BASE2)]
    assert p.lineages["A"].handed_over_to == ["B"] == p.lineages["A2"].handed_over_to
    ev = json.loads((tmp_path / "j" / f"{p.state.seq:012d}.json").read_text())
    assert [h["root"] for h in ev["handover"]] == ["A", "A2"]


@pytest.mark.parametrize(
    "observer",
    [
        None,
        Observer(),
        Observer(fail=PlannerError("compare unavailable or truncated")),
        Observer(fail=OSError("network")),
        Observer(fail=TypeError("not a mapping")),
        Observer(answer={}),
        Observer(answer={"merge_base": 1}),
        Observer(answer="yes"),
        Observer(answer=7),
        Observer(answer={str(i): "x" for i in range(9)}),
        Observer(answer={"k": "v" * 256}),
        Observer(answer={"": ""}),
        # truthy things that are not a mapping must never be read as evidence
        Observer(answer=["no"]),
        Observer(answer=("no",)),
        Observer(answer={"no"}),
        Observer(answer=[("refused", "not an ancestor")]),
    ],
)
def test_without_an_established_observation_no_scope_is_handed_over(tmp_path, observer):
    t = InMemoryTransport()
    p = observing(t, DirJournal(tmp_path / "j"), observer)
    p.dispatch(qi("A"), **FIELDS)
    to_ready(t, p)
    seq = p.state.seq
    with pytest.raises(PlannerError, match=r"^SCOPE_COLLISION:A:src/x\|src/x$"):
        p.dispatch(qi("B"), **on(BASE2, "src/x"))
    p.release_scope("A", merged_revision=MERGED)  # an assertion changes the wording only
    with pytest.raises(PlannerError, match=r"^SCOPE_RETAINED:A:src/x\|src/x$"):
        p.dispatch(qi("B"), **on(BASE2, "src/x"))
    assert p.state.seq == seq + 1 and "B" not in p.lineages  # only the RELEASE was appended


def test_an_observer_that_raises_something_else_is_not_swallowed(tmp_path):
    p = observing(InMemoryTransport(), DirJournal(tmp_path / "j"), Observer(fail=RuntimeError("x")))
    t = p.transport
    p.dispatch(qi("A"), **FIELDS)
    to_ready(t, p)
    seq = p.state.seq
    with pytest.raises(RuntimeError):
        p.dispatch(qi("B"), **on(BASE2, "src/x"))
    assert p.state.seq == seq and len(list((tmp_path / "j").iterdir())) == seq


def test_an_observer_needs_an_identity(tmp_path):
    class Nameless:
        identity = ""

        def result_in_base(self, **kw):
            return {"merge_base": "x"}

    for bad in ("", " ", " x", "x" * 201, "a\nb", None, 5):
        Nameless.identity = bad
        with pytest.raises(PlannerError, match="observer needs a non-empty identity"):
            observing(InMemoryTransport(), DirJournal(tmp_path / "j"), Nameless())


def test_only_a_verified_result_can_be_handed_over_never_a_phase_or_a_report(tmp_path):
    """BLOCKED, OWNER_REQUIRED, executing and verifying lineages: the observer is not asked."""
    t = InMemoryTransport()
    obs = Observer(answer={"merge_base": REV1})  # would say yes to anything
    p = observing(t, DirJournal(tmp_path / "j"), obs)
    p.dispatch(qi("E"), **fields("src/e"))  # executing
    p.dispatch(qi("F"), **fields("src/f"))
    p.fail_execution("F", "runner lost")  # BLOCKED on a report
    with pytest.raises(PlannerError, match=r"^SCOPE_COLLISION:E:"):
        p.dispatch(qi("X"), **on(BASE2, "src/e"))
    with pytest.raises(PlannerError, match=r"^SCOPE_RETAINED:F:"):
        p.dispatch(qi("X"), **on(BASE2, "src/f"))
    implement(t)  # E's result
    p.pump()
    assert p.lineages["E"].phase is Phase.VERIFYING
    with pytest.raises(PlannerError, match=r"^SCOPE_COLLISION:E:"):
        p.dispatch(qi("X"), **on(BASE2, "src/e"))
    secret = (Finding(finding_id="S", category=FindingCategory.SECRET_REQUIRED),)
    verify(t, Verdict.FAIL, secret)
    p.pump()
    assert p.lineages["E"].phase is Phase.OWNER_REQUIRED
    with pytest.raises(PlannerError, match=r"^SCOPE_RETAINED:E:"):
        p.dispatch(qi("X"), **on(BASE2, "src/e"))
    assert obs.calls == [] and "X" not in p.lineages


def test_a_journalled_handover_is_checked_on_replay(tmp_path):
    from project_atlas.orchestration.autonomy.dev_contracts import make_work

    t = InMemoryTransport()
    p = planner(t, DirJournal(tmp_path / "j"))
    p.dispatch(qi("A"), **FIELDS)
    to_ready(t, p)  # A: INTEGRATION_READY with result REV1
    p.dispatch(qi("A2"), **fields("src/w"))
    to_ready(t, p)
    p.dispatch(qi("V"), **fields("src/v"))
    implement(t)
    p.pump()  # V: VERIFYING, it has a result but no verdict
    assert p.lineages["V"].phase is Phase.VERIFYING and p.lineages["V"].result is not None
    p.dispatch(qi("K"), **fields("src/k"))
    p.fail_execution("K", "runner lost")  # K: BLOCKED
    p.dispatch(qi("Z"), **fields("src/z"))  # Z: executing
    n, head = _head(tmp_path)
    j = tmp_path / "j"

    def w(base, *paths):
        return encode(
            make_work(
                task_id="B",
                execution_id="B-E1",
                lineage_root="B",
                acceptance_contract=("ok",),
                **on(base, *paths),
            )
        )

    def d(work, *handover):
        body = dict(event="DISPATCH", root="B", work=work)
        return body if not handover else body | {"handover": list(handover)}

    not_verified = "is not a verified release"
    cases = [
        (d(w(BASE2, "src/x")), "SCOPE_COLLISION:A:"),
        (d(w(BASE2, "src/k")), "SCOPE_RETAINED:K:"),
        # a retained lineage and a holder both overlap: the holder is named, as before
        (d(w(BASE2, "src/k", "src/z")), "SCOPE_COLLISION:Z:"),
        (d(w(BASE2, "src/k"), _entry("K")), f"handover of K {not_verified}"),
        (d(w(BASE2, "src/z"), _entry("Z")), f"handover of Z {not_verified}"),
        (d(w(BASE2, "src/v"), _entry("V")), f"handover of V {not_verified}"),
        (d(w(BASE2, "src/x"), _entry(result_revision=MERGED)), f"handover of A {not_verified}"),
        (d(w(BASE2, "src/x"), _entry(base_revision=BASE)), f"handover of A {not_verified}"),
        (d(w(BASE, "src/x"), _entry()), f"handover of A {not_verified}"),
        (d(w(BASE2, "src/x"), _entry(basis="MERGED")), f"handover of A {not_verified}"),
        (d(w(BASE2, "src/q"), _entry()), "does not collide with"),
        (d(w(BASE2, "src/x"), _entry(), _entry("NOPE")), "does not collide with"),
        (d(w(BASE2, "src/x"), _entry(), _entry()), "malformed or repeated"),
        (d(w(BASE2, "src/x", "src/w"), _entry("A2"), _entry()), "ordered by lineage root"),
        (d(w(BASE2, "src/x", "src/w"), _entry()), "SCOPE_COLLISION:A2:"),
        (d(w(BASE2, "src/x"), _entry(evidence={})), "malformed or repeated"),
        (d(w(BASE2, "src/x"), _entry(evidence={"k": 1})), "malformed or repeated"),
        (d(w(BASE2, "src/x"), _entry(observer="")), "malformed or repeated"),
        (d(w(BASE2, "src/x"), _entry() | {"extra": "1"}), "wrong fields"),
        (d(w(BASE2, "src/x"), "A"), "wrong fields"),
        (d(w(BASE2, "src/x")) | {"handover": {"root": "A"}}, "bounded list"),
        (d(w(BASE2, "src/x")) | {"handover": [_entry()] * 65}, "bounded list"),
        (d(w(BASE2, "src/x"), _entry(observer=" x")), "malformed or repeated"),
        (d(w(BASE2, "src/x"), _entry(observer="x" * 201)), "malformed or repeated"),
        # the version says whether a handover is inside: code from before the rule, which
        # would ignore the entry, refuses version 2 instead of replaying the journal
        (d(w(BASE2, "src/x"), _entry()) | {"v": 1}, "unsupported journal version 1"),
        (d(w(BASE2, "src/q")) | {"v": 2}, "unsupported journal version 2"),
        (dict(event="RELEASE", root="A", evidence=MERGED, handover=[]), "only a DISPATCH"),
    ]
    for body, why in cases:
        _forge(j, n + 1, head, **body)
        with pytest.raises(JournalCorrupt, match=why):
            planner(InMemoryTransport(), DirJournal(j))
        (j / f"{n + 1:012d}.json").unlink()
    # control: the same event with exactly the right entries replays
    _forge(j, n + 1, head, **d(w(BASE2, "src/x", "src/w"), _entry(), _entry("A2")))
    q = planner(InMemoryTransport(), DirJournal(j))
    assert q.lineages["A"].handed_over_to == ["B"] == q.lineages["A2"].handed_over_to
    assert q.in_flight() == {"B", "V", "Z"}


def test_a_released_scope_is_handed_over_on_the_observation_not_on_the_release(tmp_path):
    t = InMemoryTransport()
    p = observing(t, DirJournal(tmp_path / "j"), Observer((REV1, BASE2)))
    p.dispatch(qi("A"), **FIELDS)
    to_ready(t, p)
    p.release_scope("A", merged_revision=MERGED)
    with pytest.raises(PlannerError, match=r"^SCOPE_RETAINED:A:"):
        p.dispatch(qi("B"), **on(MERGED, "src/x"))  # the asserted revision as base: not observed
    p.dispatch(qi("B"), **on(BASE2, "src/x"))
    a = p.lineages["A"]
    assert a.scope_released == MERGED and a.handover == BASE2 and a.handed_over_to == ["B"]


# ---- carried from the ATLAS-DEVQ-0008 verification ------------------------------------------


def test_a_file_that_merely_has_a_repair_records_name_is_not_a_repair_record(tmp_path):
    """Pre-placed junk at the head's record name: no acknowledgement is written over it."""
    for junk in ("garbage", "[" * 200_000, json.dumps({"seq": True}), "{}"):
        root = tmp_path / str(len(junk))
        t = SpoolTransport(root / "spool")
        p1 = planner(t, DirJournal(root / "j"), "plan-1")
        p1.dispatch(qi("A"), **FIELDS)
        p1.dispatch(qi("B"), **fields("src/y"))
        anchor = root / "j.ack"
        (anchor / "000000000002.repair").write_text(junk)
        with pytest.raises(JournalCorrupt, match=r"repair record 000000000002\.repair"):
            DirJournal(root / "j").repairs()  # also for deeply nested JSON (RecursionError)
        (anchor / "000000000002.ack").unlink()
        with pytest.raises(JournalCorrupt, match=r"repair record 000000000002\.repair"):
            p1.select([qi("Z")])
        assert not (anchor / "000000000002.ack").exists()


def test_a_repair_record_of_another_event_does_not_cover_this_one(tmp_path):
    _, p1 = _two_holders(tmp_path)
    anchor = tmp_path / "j.ack"
    other = {"v": 1, "seq": 2, "digest": "0" * 64, "kind": "RESTORED", "by": "someone"}
    (anchor / "000000000002.repair").write_text(json.dumps(other))
    (anchor / "000000000002.ack").unlink()
    with pytest.raises(JournalCorrupt, match="repair record 2 is for a different event"):
        p1.select([qi("Z")])
    assert not (anchor / "000000000002.ack").exists()


def test_limit_a_second_loss_of_the_same_acknowledgement_leaves_no_second_record(tmp_path):
    _, p1 = _two_holders(tmp_path)
    anchor = tmp_path / "j.ack"
    ack, record = anchor / "000000000002.ack", anchor / "000000000002.repair"
    ack.unlink()
    p1.select([qi("Z")])
    first = record.read_bytes()
    ack.unlink()
    p1.select([qi("Z")])
    assert ack.exists() and record.read_bytes() == first
    assert [r["seq"] for r in DirJournal(tmp_path / "j").repairs()] == [2]


def test_after_adopting_a_planner_counts_the_head_as_seen_acknowledged(tmp_path):
    """After adopting, the planner counts the head as seen acknowledged."""
    t = SpoolTransport(tmp_path / "spool")
    planner(t, DirJournal(tmp_path / "j"), "plan-1").dispatch(qi("A"), **FIELDS)
    anchor = tmp_path / "j.ack"
    (anchor / "000000000001.ack").unlink()
    p2 = planner(t, DirJournal(tmp_path / "j"), "plan-2")
    p2.recover()  # adopts event 1 (never saw it acknowledged)
    assert [r["kind"] for r in DirJournal(tmp_path / "j").repairs()] == ["ADOPTED"]
    assert p2.state.acked == 1


def test_a_holder_is_reported_before_a_retained_lineage_and_entries_are_ordered(tmp_path):
    t = InMemoryTransport()
    obs = Observer((REV1, BASE2))
    p = observing(t, DirJournal(tmp_path / "j"), obs)
    p.dispatch(qi("A"), **fields("src/a"))
    p.fail_execution("A", "runner lost")  # A: retained, sorts before the holder
    p.dispatch(qi("H"), **fields("src/h"))  # H: executing holder
    with pytest.raises(PlannerError, match=r"^SCOPE_COLLISION:H:src/h\|src/h$"):
        p.dispatch(qi("X"), **on(BASE2, "src/a", "src/h"))
    assert obs.calls == []
    # a released lineage (retained) and a holder handed over in ONE dispatch: the planner
    # writes the entries in root order although it looked at the holder first
    p.dispatch(qi("R"), **fields("src/r"))
    implement(t)  # A's work was still published: its late result is refused by the journal
    implement(t)  # H's result
    implement(t)  # R's result
    p.pump()
    verify(t)
    verify(t)
    p.pump()
    assert {p.lineages[r].phase for r in ("H", "R")} == {Phase.INTEGRATION_READY}
    p.release_scope("H", merged_revision=MERGED)  # H: retained now; R: still a holder
    p.dispatch(qi("Y"), **on(BASE2, "src/h", "src/r"))
    ev = json.loads((tmp_path / "j" / f"{p.state.seq:012d}.json").read_text())
    assert [h["root"] for h in ev["handover"]] == ["H", "R"]


def test_the_first_handover_base_is_kept(tmp_path):
    base3 = "8" * 40
    t = InMemoryTransport()
    p = observing(t, DirJournal(tmp_path / "j"), Observer((REV1, BASE2), (REV1, base3)))
    p.dispatch(qi("A"), **FIELDS)
    to_ready(t, p)
    p.dispatch(qi("B"), **on(BASE2, "src/x/b"))
    p.dispatch(qi("C"), **on(base3, "src/x/c"))
    a = p.lineages["A"]
    assert a.handover == BASE2 and a.handed_over_to == ["B", "C"]
    assert a.history[-2:] == [f"SCOPE_HANDED_OVER:B:{BASE2}", f"SCOPE_HANDED_OVER:C:{base3}"]


def test_a_repair_record_needs_an_integer_sequence_number_and_a_string_digest(tmp_path):
    _two_holders(tmp_path)
    anchor = tmp_path / "j.ack"
    digest = (anchor / "000000000001.ack").read_text()
    good = {"v": 1, "seq": 1, "digest": digest, "kind": "RESTORED", "by": "plan-1"}
    for bad in ({"seq": True}, {"seq": 1.0}, {"digest": int("1" * 64)}):
        (anchor / "000000000001.repair").write_text(json.dumps(good | bad))
        with pytest.raises(JournalCorrupt, match=r"repair record 000000000001\.repair"):
            DirJournal(tmp_path / "j").repairs()
    (anchor / "000000000001.repair").write_text(json.dumps(good))
    assert [r["seq"] for r in DirJournal(tmp_path / "j").repairs()] == [1]


# ---- ATLAS-DEVQ-0010: repository identity on replay -----------------------------------------


def test_replay_applies_repository_identity_like_a_live_dispatch(tmp_path):
    from project_atlas.orchestration.autonomy.dev_contracts import make_work

    t = InMemoryTransport()
    p = planner(t, DirJournal(tmp_path / "j"))
    p.dispatch(qi("A"), **FIELDS)
    p.dispatch(qi("K"), **fields("src/k"))
    p.fail_execution("K", "runner lost")
    n, head = _head(tmp_path)
    j = tmp_path / "j"

    def d(repository, *paths):
        work = make_work(
            task_id="B",
            execution_id="B-E1",
            lineage_root="B",
            acceptance_contract=("ok",),
            **{**fields(*paths), "repository": repository},
        )
        return dict(event="DISPATCH", root="B", work=encode(work))

    repo = FIELDS["repository"]
    cases = [
        # a journal from before the rule that admitted another spelling over a held scope
        (d(repo + ".git", "src/x"), "SCOPE_COLLISION:A:"),
        (d(f"https://github.com/{repo}", "src/x"), "SCOPE_COLLISION:A:"),
        (d(f"git@github.com:{repo}.git", "src/k"), "SCOPE_RETAINED:K:"),
        # an identity that cannot be keyed, also where nothing overlaps
        (d(f"https://gitlab.com/{repo}", "src/q"), "REPOSITORY_UNSUPPORTED:"),
        (d(repo + ".git.git", "src/q"), "REPOSITORY_UNSUPPORTED:"),
    ]
    for body, why in cases:
        _forge(j, n + 1, head, **body)
        with pytest.raises(JournalCorrupt, match=why):
            planner(InMemoryTransport(), DirJournal(j))
        (j / f"{n + 1:012d}.json").unlink()
    _forge(j, n + 1, head, **d(repo + ".git", "src/q"))  # control: a disjoint scope replays
    q = planner(InMemoryTransport(), DirJournal(j))
    assert q.lineages["B"].work.repository == repo + ".git"
    rows = {r["lineage_root"]: r for r in fleet_status(DirJournal(j))}
    assert rows["B"]["repository"] == repo + ".git"
    assert rows["B"]["repository_key"] == rows["A"]["repository_key"] == repo.lower()


# ---- executor ownership (ATLAS-DEVQ-0011) ---------------------------------------------------

E1, E2 = "vps1-impl", "vps4-impl"
POOL = (E1, E2)


def _claim_work(t, who):
    return t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=who)


def _result(work, who, rev=REV1):
    return make_result(work, executor_identity=who, result_revision=rev, result_tree=TREE1)


def _event(tmp_path, seq):
    return json.loads((tmp_path / "j" / f"{seq:012d}.json").read_text())


def test_a_dispatch_assigns_the_execution_to_one_executor_of_the_pool(tmp_path):
    t = InMemoryTransport()
    p = planner(t, DirJournal(tmp_path / "j"))
    wa = p.dispatch(qi("A"), executor_pool=POOL, **FIELDS)
    wb = p.dispatch(qi("B"), executor_pool=POOL, **fields("src/y"))
    a, b = p.lineages["A"], p.lineages["B"]
    assert (a.executor, b.executor) == (E1, E2)  # least loaded, then by name
    assert a.history == ["DISPATCHED:A", f"ASSIGNED:A-E1:{E1}"] and a.epoch == 0
    ev = _event(tmp_path, 1)
    assert ev["v"] == 3 and ev["executor"] == E1 and "handover" not in ev
    assert p.executor_load() == {E1: 1, E2: 1}
    # every executor owns its limit: nothing is assigned, nothing is appended or published
    with pytest.raises(PlannerError, match=r"^EXECUTOR_BUSY:"):
        p.dispatch(qi("C"), executor_pool=POOL, **fields("src/z"))
    assert p.state.seq == 2 and "C" not in p.lineages
    assert p.dispatch(qi("C"), executor_pool=POOL, executor_limit=2, **fields("src/z"))
    assert p.lineages["C"].executor == E1
    rows = {r["lineage_root"]: r for r in fleet_status(DirJournal(tmp_path / "j"))}
    assert (rows["A"]["executor"], rows["B"]["executor"], rows["A"]["epoch"]) == (E1, E2, 0)
    # delivery: a work record goes only to the executor it is addressed to
    got = [_claim_work(t, E2), _claim_work(t, E2)]
    assert got[0].seal == wb.seal and got[1] is None
    mine = {_claim_work(t, E1).seal, _claim_work(t, E1).seal}
    assert mine == {wa.seal, p.lineages["C"].work.seal} and _claim_work(t, E1) is None


def test_only_the_owning_executor_s_result_is_accepted(tmp_path):
    t = InMemoryTransport()
    p = planner(t, DirJournal(tmp_path / "j"))
    wa = p.dispatch(qi("A"), executor_pool=POOL, **FIELDS)
    t.publish(_result(wa, E2))  # a well-formed result for the right work, from another executor
    p.pump()
    assert p.lineages["A"].phase is Phase.DISPATCHED and p.state.seq == 1
    assert "not from vps1-impl, the executor that owns this execution" in p.quarantined[-1][2]
    t.publish(_result(wa, E1))
    p.pump()
    assert p.lineages["A"].phase is Phase.VERIFYING
    # the owner keeps the lineage while it is being verified: it is not free for other work
    with pytest.raises(PlannerError, match=r"^EXECUTOR_BUSY:"):
        p.dispatch(qi("B"), executor_pool=(E1,), **fields("src/y"))
    verify(t)
    p.pump()
    assert p.lineages["A"].phase is Phase.INTEGRATION_READY and p.executor_load() == {}
    assert p.dispatch(qi("B"), executor_pool=(E1,), **fields("src/y"))


def test_a_lineage_without_an_assignment_behaves_as_before(tmp_path):
    t = InMemoryTransport()
    p = planner(t, DirJournal(tmp_path / "j"))
    wa = p.dispatch(qi("A"), **FIELDS)
    assert _event(tmp_path, 1)["v"] == 1 and "executor" not in _event(tmp_path, 1)
    assert p.lineages["A"].executor == "" and p.executor_load() == {}
    assert _claim_work(t, "anyone").seal == wa.seal
    t.publish(_result(wa, "anyone"))
    p.pump()
    assert p.lineages["A"].phase is Phase.VERIFYING


def test_a_repair_stays_with_the_lineages_executor(tmp_path):
    t = InMemoryTransport()
    p = planner(t, DirJournal(tmp_path / "j"))
    wa = p.dispatch(qi("A"), executor_pool=POOL, **FIELDS)
    p.reassign("A", executor_pool=(E2,), reason="runner replaced")
    w2 = _claim_work(t, E2)
    t.publish(_result(w2, E2))
    p.pump()
    verify(t, Verdict.FAIL, DEFECT)
    p.pump()
    st = p.lineages["A"]
    assert st.phase is Phase.REPAIR_DISPATCHED and st.work.task_id == "A-R1"
    assert st.executor == E2 and st.epoch == 0  # a new work item starts at epoch 0
    ev = _event(tmp_path, p.state.seq)
    assert ev["event"] == "REPAIR" and ev["executor"] == E2 and ev["v"] == 3
    assert _claim_work(t, E1).seal == wa.seal  # only the fenced first execution is left for E1
    assert _claim_work(t, E1) is None and _claim_work(t, E2).seal == st.work.seal


def test_reassignment_fences_the_earlier_execution(tmp_path):
    t = InMemoryTransport()
    p = planner(t, DirJournal(tmp_path / "j"))
    old = p.dispatch(qi("A"), executor_pool=POOL, **FIELDS)
    assert _claim_work(t, E1).seal == old.seal  # E1 is running the first execution
    new = p.reassign("A", executor_pool=(E2,), reason="lease expired; runner unreachable")
    st = p.lineages["A"]
    # the same work under the next execution id: nothing else differs
    assert new.execution_id == "A-E1-X1" and new.seal != old.seal
    assert new.model_dump(exclude={"seal", "execution_id"}) == old.model_dump(
        exclude={"seal", "execution_id"}
    )
    assert (st.executor, st.epoch, st.work.seal, st.phase) == (E2, 1, new.seal, Phase.DISPATCHED)
    assert st.history[-1] == f"REASSIGNED:A-E1-X1:{E2}:lease expired; runner unreachable"
    ev = _event(tmp_path, 2)
    assert (ev["event"], ev["v"], ev["executor"]) == ("REASSIGN", 3, E2)
    assert p.state.superseded == {old.seal: "A"} and p.in_flight() == {"A"}
    # E1 never stopped. Its result arrives late: it answers a work that is no longer current
    t.publish(_result(old, E1))
    p.pump()
    assert st.phase is Phase.DISPATCHED and p.state.seq == 2
    assert "does not answer the dispatched work" in p.quarantined[-1][2]
    # nor can E1 answer the new work: it is not the owner (and the record is not delivered to it)
    assert _claim_work(t, E1) is None
    t.publish(_result(new, E1, rev="d" * 40))
    p.pump()
    assert st.phase is Phase.DISPATCHED and "not from vps1-impl" not in p.quarantined[-1][2]
    assert f"not from {E2}" in p.quarantined[-1][2]
    # exactly one valid writer: the new owner under the new execution id
    assert _claim_work(t, E2).seal == new.seal
    t.publish(_result(new, E2))
    p.pump()
    assert st.phase is Phase.VERIFYING and st.result.execution_id == "A-E1-X1"
    # durable: a new planner replays to the same owner, epoch and fenced seal
    q = planner(InMemoryTransport(), DirJournal(tmp_path / "j"))
    assert (q.lineages["A"].executor, q.lineages["A"].epoch) == (E2, 1)
    assert q.state.superseded == {old.seal: "A"}
    row = fleet_status(DirJournal(tmp_path / "j"))[0]
    assert (row["executor"], row["epoch"], row["execution_id"]) == (E2, 1, "A-E1-X1")


def test_reassignment_is_bounded_and_only_for_an_executing_lineage(tmp_path):
    t = InMemoryTransport()
    p = planner(t, DirJournal(tmp_path / "j"))
    p.dispatch(qi("A"), executor_pool=(E1,), **FIELDS)
    # the same executor may take its own lineage again under a new epoch (a restart)
    assert p.reassign("A", executor_pool=(E1,), reason="restart").execution_id == "A-E1-X1"
    assert p.reassign("A", executor_pool=(E1,), reason="restart").execution_id == "A-E1-X2"
    assert p.reassign("A", executor_pool=(E2,), reason="move").execution_id == "A-E1-X3"
    assert p.lineages["A"].executor == E2 and p.lineages["A"].epoch == 3
    seq = p.state.seq
    with pytest.raises(PlannerError, match=r"^REASSIGN_LIMIT:A was reassigned 3 times"):
        p.reassign("A", executor_pool=POOL, reason="again")
    # the bound is the journal's own rule: a fourth REASSIGN does not replay either
    from project_atlas.orchestration.autonomy.dev_planner import next_epoch_work

    n, head = _head(tmp_path)
    fourth = next_epoch_work(p.lineages["A"].work, 4)
    _forge(
        tmp_path / "j",
        n + 1,
        head,
        event="REASSIGN",
        root="A",
        work=encode(fourth),
        executor=E1,
        reason="again",
    )
    with pytest.raises(JournalCorrupt, match="REASSIGN_LIMIT:A was reassigned 3 times"):
        planner(InMemoryTransport(), DirJournal(tmp_path / "j"))
    (tmp_path / "j" / f"{n + 1:012d}.json").unlink()
    for bad in ("", "x" * 201, "two\nlines"):
        with pytest.raises(PlannerError, match="REASSIGN"):
            p.reassign("A", executor_pool=POOL, reason=bad)
    with pytest.raises(PlannerError, match="unknown task"):
        p.reassign("NOPE", executor_pool=POOL, reason="x")
    with pytest.raises(PlannerError, match="non-empty executor pool"):
        p.reassign("A", executor_pool=(), reason="x")
    p.dispatch(qi("B"), executor_pool=(E1,), **fields("src/y"))
    with pytest.raises(PlannerError, match=r"^EXECUTOR_BUSY:"):
        p.reassign("B", executor_pool=(E2,), reason="x")  # E2 owns A
    assert p.state.seq == seq + 1
    w = _claim_work(t, E1)
    while w.task_id != "B":
        w = _claim_work(t, E1)
    t.publish(_result(w, E1))
    p.pump()
    with pytest.raises(PlannerError, match="reassign refused: B is not the executing work"):
        p.reassign("B", executor_pool=POOL, reason="x")  # it has a result: VERIFYING
    verify(t)
    p.pump()
    with pytest.raises(PlannerError, match="reassign refused"):
        p.reassign("B", executor_pool=POOL, reason="x")  # INTEGRATION_READY
    p.fail_execution("A", "gave up")
    with pytest.raises(PlannerError, match="reassign refused"):
        p.reassign("A", executor_pool=POOL, reason="x")  # BLOCKED


@pytest.mark.parametrize(
    "pool, limit, why",
    [
        ((E1, E1), 1, "duplicate"),
        ((E1, E1.upper()), 1, "duplicate"),
        (("",), 1, "invalid executor identity"),
        ((5,), 1, "invalid executor identity"),
        ((" spaced ",), 1, "invalid executor identity"),
        (E1, 1, "sequence of identities"),
        ((E1,), 0, "executor_limit must be"),
        ((E1,), True, "executor_limit must be"),
        ((E1,), "1", "the limit an integer"),
    ],
)
def test_an_executor_pool_is_validated_before_anything_is_decided(tmp_path, pool, limit, why):
    p = planner(InMemoryTransport(), DirJournal(tmp_path / "j"))
    with pytest.raises(PlannerError, match=why):
        p.dispatch(qi("A"), executor_pool=pool, executor_limit=limit, **FIELDS)
    assert p.state.seq == 0


def test_journalled_ownership_is_checked_on_replay(tmp_path):
    from project_atlas.orchestration.autonomy.dev_planner import next_epoch_work

    t = InMemoryTransport()
    p = planner(t, DirJournal(tmp_path / "j"))
    wa = p.dispatch(qi("A"), executor_pool=(E1,), **FIELDS)  # A: executing, owned by E1
    wv = p.dispatch(qi("V"), executor_pool=(E2,), **fields("src/v"))
    t.publish(_result(wv, E2))
    p.pump()  # V: VERIFYING
    wu = p.dispatch(qi("U"), **fields("src/u"))  # U: executing, not assigned
    n, head = _head(tmp_path)
    j = tmp_path / "j"
    req = encode(p.issued["V"])
    x1 = next_epoch_work(wa, 1)

    def result(work, who):
        from project_atlas.orchestration.autonomy.dev_contracts import make_verification_request

        res = _result(work, who)
        request = make_verification_request(work, res, verifier_identity=VER)
        return dict(
            event="RESULT", root=work.lineage_root, result=encode(res), request=encode(request)
        )

    def reassign(work, executor=E2, reason="r", root="A", **kw):
        return (
            dict(event="REASSIGN", root=root, work=encode(work), executor=executor, reason=reason)
            | kw
        )

    cases = [
        # a result for an owned execution from anyone but its owner
        (result(wa, E2), "RESULT is not from the executor that owns this execution"),
        (result(wa, "someone-else"), "RESULT is not from the executor that owns"),
        # the version says whether an executor is named
        (reassign(x1) | {"v": 1}, "unsupported journal version 1"),
        (reassign(x1) | {"v": 2}, "unsupported journal version 2"),
        (reassign(x1) | {"v": 3.0}, "unsupported journal version 3.0"),
        (
            dict(event="DISPATCH", root="B", work=encode(_work("B", "src/q")), v=3),
            "unsupported journal version 3",
        ),
        (
            dict(event="DISPATCH", root="B", work=encode(_work("B", "src/q")), executor=E1, v=1),
            "unsupported journal version 1",
        ),
        # who may be named, and where
        (
            dict(event="DISPATCH", root="B", work=encode(_work("B", "src/q")), executor=""),
            "invalid executor identity",
        ),
        (
            dict(event="DISPATCH", root="B", work=encode(_work("B", "src/q")), executor=5),
            "invalid executor identity",
        ),
        (
            dict(event="RELEASE", root="A", evidence=MERGED, executor=E1),
            "only a DISPATCH, REPAIR or REASSIGN",
        ),
        (
            dict(event="TERMINAL", root="A", phase="BLOCKED", reason="x", executor=E1),
            "only a DISPATCH, REPAIR or REASSIGN",
        ),
        (result(wu, E1) | {"executor": E1}, "only a DISPATCH, REPAIR or REASSIGN"),
        # a reassignment is the CURRENT work under its NEXT epoch, for an executing lineage
        (reassign(next_epoch_work(wa, 2)), "not the current work under its next epoch"),
        (reassign(wa), "not the current work under its next epoch"),
        (reassign(_work("A", "src/other")), "not the current work under its next epoch"),
        (reassign(next_epoch_work(wu, 1)), "not the current work under its next epoch"),
        (reassign(next_epoch_work(wv, 1), root="V"), "only an executing lineage can be reassigned"),
        (reassign(x1, root="NOPE"), "REASSIGN for an unknown lineage"),
        (reassign(x1, reason=""), "short printable reason"),
        (reassign(x1, reason="x" * 201), "short printable reason"),
        (reassign(x1, reason=5), "short printable reason"),
        ({k: v for k, v in reassign(x1).items() if k != "executor"}, "REASSIGN needs the executor"),
    ]
    for body, why in cases:
        _forge(j, n + 1, head, **body)
        with pytest.raises(JournalCorrupt, match=why):
            planner(InMemoryTransport(), DirJournal(j))
        (j / f"{n + 1:012d}.json").unlink()
    # control: the correct REASSIGN, and an unassigned lineage being assigned by one
    _forge(j, n + 1, head, **reassign(x1))
    q = planner(InMemoryTransport(), DirJournal(j))
    assert (q.lineages["A"].executor, q.lineages["A"].epoch) == (E2, 1)
    _forge(j, n + 1, head, **reassign(next_epoch_work(wu, 1), executor=E1, root="U"))
    q = planner(InMemoryTransport(), DirJournal(j))
    assert (q.lineages["U"].executor, q.lineages["U"].epoch) == (E1, 1)
    assert req  # V's request is untouched by any of this


def _work(task, *paths):
    from project_atlas.orchestration.autonomy.dev_contracts import make_work

    return make_work(
        task_id=task,
        execution_id=f"{task}-E1",
        lineage_root=task,
        acceptance_contract=("ok",),
        **fields(*paths),
    )


def test_a_journalled_repair_must_keep_the_executor(tmp_path):
    t = InMemoryTransport()
    p = planner(t, DirJournal(tmp_path / "j"))
    wa = p.dispatch(qi("A"), executor_pool=(E1,), **FIELDS)
    t.publish(_result(wa, E1))
    p.pump()
    verify(t, Verdict.FAIL, DEFECT)
    p.pump()
    n, _ = _head(tmp_path)
    j = tmp_path / "j"
    good = json.loads((j / f"{n:012d}.json").read_text())
    assert good["event"] == "REPAIR" and good["executor"] == E1
    prev = good["prev"]
    for change, why in (
        ({"executor": E2}, "REPAIR must keep the lineage's executor assignment"),
        ({"executor": None}, "invalid executor identity"),
    ):
        body = {k: v for k, v in good.items() if k not in ("v", "seq", "prev", "planner")}
        _forge(j, n, prev, **(body | change))
        with pytest.raises(JournalCorrupt, match=why):
            planner(InMemoryTransport(), DirJournal(j))
    body = {k: v for k, v in good.items() if k not in ("v", "seq", "prev", "planner", "executor")}
    _forge(j, n, prev, **body)  # the assignment silently dropped (and so version 1)
    with pytest.raises(JournalCorrupt, match="REPAIR must keep the lineage's executor"):
        planner(InMemoryTransport(), DirJournal(j))
