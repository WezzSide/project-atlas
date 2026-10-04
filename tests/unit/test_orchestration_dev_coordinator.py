"""ATLAS-DEVQ-0008: store identity, transport witness and the Coordinator tick.

Local tests of preparation, tracking and recovery over a durable store. Nothing here causes a
workflow run; the one test that uses the fabric adapter does so with a fake port to show that
recovery re-publishes the same record and the adapter's ledger refuses a second dispatch.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from project_atlas.orchestration.autonomy.dev_contracts import (
    Role,
    Verdict,
    make_result,
    make_verdict,
)
from project_atlas.orchestration.autonomy.dev_crosswalk import Crosswalk
from project_atlas.orchestration.autonomy.dev_fabric_adapter import FabricAdapter
from project_atlas.orchestration.autonomy.dev_planner import (
    SAME_FILESYSTEM,
    SEPARATE_FILESYSTEM,
    STORE_MARKER,
    Coordinator,
    DirJournal,
    JournalCorrupt,
    MemoryJournal,
    Phase,
    Planner,
    PlannerError,
    StoreJournal,
    fleet_status,
)
from project_atlas.orchestration.autonomy.dev_queue import Category, QueueItem
from project_atlas.orchestration.autonomy.dev_spool_transport import SpoolTransport
from project_atlas.orchestration.autonomy.dev_transport import Channel, InMemoryTransport

BASE = "a" * 40
REV1, TREE1 = "b" * 40, "c" * 40
IMPL, VER = "vps1-impl", "vps2-ver"


def cand(task, *paths, **kw):
    item = QueueItem(task_id=task, title=task, category=Category.RELIABILITY, **kw)
    return item, dict(
        repository="WezzSide/project-atlas",
        base_revision=BASE,
        authority_ref="AUTH-1",
        allowed_paths=tuple(paths),
        forbidden_paths=("infra/atlas-runner/controller",),
    )


def store(tmp_path, create=True):
    j, a = tmp_path / "state" / "journal", tmp_path / "witness" / "anchor"
    return StoreJournal.create(j, a) if create else StoreJournal.attach(j, a)


def coordinator(tmp_path, journal=None, identity="coord-1", **kw):
    kw.setdefault("accept_same_filesystem", True)
    return Coordinator(
        journal or store(tmp_path, create=False),
        SpoolTransport(tmp_path / "spool"),
        identity=identity,
        verifier_identities=(VER,),
        status_path=tmp_path / "status" / "status.json",
        **kw,
    )


def implement(tmp_path):
    t = SpoolTransport(tmp_path / "spool")
    w = t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL)
    assert w is not None
    t.publish(make_result(w, executor_identity=IMPL, result_revision=REV1, result_tree=TREE1))
    return w


def verify(tmp_path, verdict=Verdict.PASS):
    t = SpoolTransport(tmp_path / "spool")
    req = t.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity=VER)
    assert req is not None
    t.publish(make_verdict(req, verdict=verdict))


def works(tmp_path):
    return sorted(
        p.name for p in (tmp_path / "spool" / "WORK").rglob("*.json") if "claim." not in p.name
    )


def tree(tmp_path):
    """Content of every file under the store, anchor and spool (for 'nothing changed')."""
    out = {}
    for top in ("state", "witness", "spool"):
        for p in sorted((tmp_path / top).rglob("*")):
            if p.is_file():
                out[str(p.relative_to(tmp_path))] = p.read_bytes()
    return out


# ---- store identity -------------------------------------------------------------------------


def test_a_store_is_created_once_and_then_only_attached(tmp_path):
    j = store(tmp_path)
    markers = [json.loads((d / STORE_MARKER).read_text()) for d in (j.root, j.anchor)]
    assert [m["role"] for m in markers] == ["journal", "anchor"]
    assert markers[0]["store"] == markers[1]["store"] == j.store_id and len(j.store_id) == 32
    assert store(tmp_path, create=False).store_id == j.store_id
    with pytest.raises(PlannerError, match="STORE_EXISTS"):
        store(tmp_path)  # never re-created over what is there
    with pytest.raises(PlannerError, match="STORE_LAYOUT"):
        StoreJournal.create(tmp_path / "x", tmp_path / "x" / "inner")
    with pytest.raises(PlannerError, match="STORE_LAYOUT"):
        StoreJournal.create(tmp_path / "y", tmp_path / "y")
    assert not (tmp_path / "x").exists() and not (tmp_path / "y").exists()
    (tmp_path / "used").mkdir()
    (tmp_path / "used" / "file").write_text("x")
    with pytest.raises(PlannerError, match="STORE_EXISTS"):
        StoreJournal.create(tmp_path / "fresh", tmp_path / "used")
    assert not (tmp_path / "fresh").exists()


def test_attach_creates_nothing_and_refuses_a_missing_or_foreign_part(tmp_path):
    with pytest.raises(JournalCorrupt, match=r"STORE_IDENTITY:journal directory .* has no store"):
        store(tmp_path, create=False)
    assert not (tmp_path / "state").exists() and not (tmp_path / "witness").exists()
    j = store(tmp_path)
    other = StoreJournal.create(tmp_path / "other" / "journal", tmp_path / "other" / "anchor")
    with pytest.raises(JournalCorrupt, match="STORE_IDENTITY:anchor directory belongs to store"):
        StoreJournal.attach(j.root, other.anchor)
    with pytest.raises(JournalCorrupt, match="has no store marker"):
        StoreJournal.attach(j.root, tmp_path / "nowhere")
    assert not (tmp_path / "nowhere").exists()
    bads = (
        "{",
        "[]",
        json.dumps({"v": 2, "role": "anchor", "store": j.store_id}),
        json.dumps({"v": 1, "role": "journal", "store": j.store_id}),
        json.dumps({"v": 1, "role": "anchor", "store": "xyz"}),
    )
    for bad in bads:
        good = (j.anchor / STORE_MARKER).read_text()
        (j.anchor / STORE_MARKER).write_text(bad)
        with pytest.raises(JournalCorrupt, match="STORE_IDENTITY"):
            store(tmp_path, create=False)
        (j.anchor / STORE_MARKER).write_text(good)
    # a plain DirJournal cannot be pointed at a store by accident: the marker is not an event
    with pytest.raises(JournalCorrupt, match="directory is not events"):
        fleet_status(DirJournal(j.root, anchor=j.anchor))


def test_a_lost_anchor_stops_a_running_planner_and_is_never_recreated(tmp_path):
    j = store(tmp_path)
    t = SpoolTransport(tmp_path / "spool")
    p = Planner(t, identity="coord-1", verifier_identities=(VER,), journal=j)
    item, fields = cand("A", "src/a")
    p.dispatch(item, **fields)  # a ONE-event journal: the case a bare DirJournal cannot detect
    for f in j.anchor.iterdir():
        f.unlink()
    j.anchor.rmdir()
    before = tree(tmp_path)
    item2, fields2 = cand("B", "src/b")
    for attempt in (
        lambda: p.dispatch(item2, **fields2),
        lambda: p.select([item2]),
        lambda: p.recover(),
        lambda: p.pump(),
        lambda: store(tmp_path, create=False),
        lambda: fleet_status(j),
    ):
        with pytest.raises(
            JournalCorrupt, match=r"STORE_IDENTITY:anchor directory .* has no store"
        ):
            attempt()
    assert tree(tmp_path) == before and not j.anchor.exists()  # nothing appended, nothing made
    # an empty directory put back in its place is still not this store's anchor
    j.anchor.mkdir()
    with pytest.raises(JournalCorrupt, match="STORE_IDENTITY"):
        p.dispatch(item2, **fields2)
    with pytest.raises(PlannerError, match="STORE_EXISTS"):
        StoreJournal.create(j.root, j.anchor)  # and the journal cannot be "started fresh"


def test_boundary_reports_whether_journal_and_anchor_share_a_filesystem(tmp_path):
    j = store(tmp_path)
    assert j.boundary == SAME_FILESYSTEM  # two directories under one tmp_path
    shm = Path("/dev/shm")
    if not shm.is_dir() or os.stat(shm).st_dev == os.stat(tmp_path).st_dev:
        pytest.skip("no second filesystem available on this host")
    anchor = shm / f"atlas-anchor-{os.getpid()}-{tmp_path.name}"
    try:
        far = StoreJournal.create(tmp_path / "far" / "journal", anchor)
        assert far.boundary == SEPARATE_FILESYSTEM
        c = coordinator(tmp_path, far, accept_same_filesystem=False)
        st = c.tick([cand("A", "src/a")])
        assert st["continuity_boundary"] == SEPARATE_FILESYSTEM
        # a different st_dev is reported, never promoted to "adequate"
        assert st["live_conflicting_work_boundary"] == "NOT_ESTABLISHED"
    finally:
        for f in anchor.glob("*"):
            f.unlink()
        if anchor.exists():
            anchor.rmdir()


# ---- what a coordinator refuses to run on ---------------------------------------------------


def test_a_coordinator_has_no_in_memory_or_unanchored_fallback(tmp_path):
    j = store(tmp_path)
    spool = SpoolTransport(tmp_path / "spool")
    kw = dict(identity="coord-1", verifier_identities=(VER,), status_path=tmp_path / "s.json")
    for journal in (MemoryJournal(), DirJournal(tmp_path / "plain"), None):
        with pytest.raises(PlannerError, match="COORDINATOR_NEEDS_STORE"):
            Coordinator(journal, spool, accept_same_filesystem=True, **kw)
    with pytest.raises(PlannerError, match="COORDINATOR_NEEDS_DURABLE_TRANSPORT"):
        Coordinator(j, InMemoryTransport(), accept_same_filesystem=True, **kw)
    with pytest.raises(PlannerError, match="STORE_BOUNDARY:journal and anchor are on the same"):
        Coordinator(j, spool, **kw)  # the reduced boundary must be accepted explicitly
    for bad in (0, -1, True, 1.5):
        with pytest.raises(PlannerError, match="max_live"):
            Coordinator(j, spool, accept_same_filesystem=True, max_live=bad, **kw)
    assert not (tmp_path / "s.json").exists()


# ---- prepare, track, recover ----------------------------------------------------------------


def test_tick_prepares_compatible_lineages_and_defers_what_collides(tmp_path):
    store(tmp_path)
    c = coordinator(tmp_path, max_live=3)
    st = c.tick(
        [
            cand("A", "src/a", severity=3),
            cand("B", "src/a/sub", severity=2),  # nested under A
            cand("C", "src/c", severity=1),
            cand("D", "src/d"),
            cand("E", "src/e"),
        ]
    )
    assert st["admitted"] == ["A", "C", "D"] and st["live"] == ["A", "C", "D"]
    assert st["deferred"] == [["B", "SCOPE_COLLISION:A:src/a/sub|src/a"]]  # E: no capacity left
    assert st["max_live"] == 3 and st["journal"]["seq"] == 3 and len(works(tmp_path)) == 3
    assert st["continuity_boundary"] == SAME_FILESYSTEM and st["state"] == "OK"
    assert st["live_conflicting_work_boundary"] == "NOT_ESTABLISHED"
    assert [r["lineage_root"] for r in st["lineages"]] == ["A", "C", "D"]
    assert all(r["holds_scope"] and r["dispatched_by"] == "coord-1" for r in st["lineages"])
    on_disk = json.loads((tmp_path / "status" / "status.json").read_text())
    assert on_disk == json.loads(json.dumps(st))  # the file is exactly what tick returned
    assert not any(k in on_disk for k in ("at", "time", "timestamp", "updated"))
    # proposing the same candidates again changes nothing: live ones are skipped, B still collides
    again = c.tick([cand("A", "src/a"), cand("B", "src/a/sub")])
    assert again["admitted"] == [] and again["journal"]["seq"] == 3
    assert again["deferred"] == []  # capacity is full: nothing was even attempted


def test_tracking_runs_a_lineage_to_integration_ready_and_keeps_its_scope(tmp_path):
    store(tmp_path)
    c = coordinator(tmp_path, max_live=1)
    assert c.tick([cand("A", "src/a"), cand("B", "src/b")])["admitted"] == ["A"]
    implement(tmp_path)
    st = c.tick()
    assert st["records_processed"] == 1 and st["lineages"][0]["phase"] == "VERIFYING"
    verify(tmp_path)
    st = c.tick([cand("B", "src/b"), cand("X", "src/a/x")])
    by = {r["lineage_root"]: r for r in st["lineages"]}
    assert by["A"]["phase"] == "INTEGRATION_READY" and by["A"]["holds_scope"] is True
    # A no longer counts as live, so B is prepared; X over A's unmerged scope stays out, and
    # nothing in the coordinator ever releases that scope
    assert st["admitted"] == ["B"] and st["live"] == ["B"]
    st = c.tick([cand("X", "src/a/x")])
    assert st["deferred"] == [] and st["admitted"] == []  # capacity 1 is taken by B
    implement(tmp_path)
    c.tick()
    verify(tmp_path)
    c.tick()
    st = c.tick([cand("X", "src/a/x")])
    assert st["deferred"] == [["X", "SCOPE_COLLISION:A:src/a/x|src/a"]]
    assert {r["lineage_root"]: r["holds_scope"] for r in st["lineages"]} == {"A": True, "B": True}


def test_a_new_coordinator_on_the_same_store_continues_where_the_last_one_stopped(tmp_path):
    store(tmp_path)
    c1 = coordinator(tmp_path, max_live=2)
    c1.tick([cand("A", "src/a"), cand("B", "src/b")])
    implement(tmp_path)
    c1.tick()
    del c1
    c2 = coordinator(tmp_path, identity="coord-2", max_live=2)  # attach, replay, witness
    st = c2.tick()
    assert st["recovered"] == [] and st["journal"]["seq"] == 3
    assert sorted(r["phase"] for r in st["lineages"]) == ["DISPATCHED", "VERIFYING"]
    verify(tmp_path)
    assert c2.tick()["records_processed"] == 1
    assert Phase.INTEGRATION_READY in {s.phase for s in c2.planner.lineages.values()}


def test_a_tick_that_died_between_journal_and_transport_is_finished_by_the_next(tmp_path):
    store(tmp_path)

    class Down(SpoolTransport):
        def publish(self, record):
            raise OSError("spool volume gone")

    j = store(tmp_path, create=False)
    c = Coordinator(
        j,
        Down(tmp_path / "spool"),
        identity="coord-1",
        verifier_identities=(VER,),
        status_path=tmp_path / "status" / "status.json",
        accept_same_filesystem=True,
    )
    with pytest.raises(OSError):
        c.tick([cand("A", "src/a")])
    assert works(tmp_path) == [] and not (tmp_path / "status" / "status.json").exists()
    st = coordinator(tmp_path).tick()
    assert st["recovered"] == ["REPUBLISHED:A:WORK"] and len(works(tmp_path)) == 1
    assert coordinator(tmp_path).tick()["recovered"] == []


# ---- the transport as a witness -------------------------------------------------------------


def test_published_work_the_journal_does_not_know_stops_every_coordinator(tmp_path):
    """Journal AND anchor rolled back together: the anchor cannot see it, the transport can."""
    j = store(tmp_path)
    c1 = coordinator(tmp_path, max_live=4)
    c1.tick([cand("A", "src/a")])
    stale = coordinator(tmp_path, identity="coord-2", max_live=4)  # at event 1
    c1.tick([cand("B", "src/b")])
    (j.root / "000000000002.json").unlink()
    (j.anchor / "000000000002.ack").unlink()
    before = tree(tmp_path)
    over_b = [cand("C", "src/b")]
    with pytest.raises(JournalCorrupt, match="JOURNAL_BEHIND_TRANSPORT:1 published work"):
        coordinator(tmp_path, identity="coord-3")  # a new one does not start
    with pytest.raises(JournalCorrupt, match="JOURNAL_BEHIND_TRANSPORT:1 published work"):
        stale.tick(over_b)  # the one whose head survived: the case the anchor alone misses
    with pytest.raises(JournalCorrupt, match="JOURNAL_DIVERGED"):
        c1.tick(over_b)  # the one whose head was lost
    assert tree(tmp_path) == before  # nothing appended, acknowledged or published
    status = json.loads((tmp_path / "status" / "status.json").read_text())
    assert status["state"] == "HALTED" and status["reason"].startswith("JOURNAL_DIVERGED")
    assert set(status) == {
        "v",
        "state",
        "reason",
        "store",
        "continuity_boundary",
        "live_conflicting_work_boundary",
        "repairs",
    }
    assert status["repairs"] == []
    # contrast: the planner alone, without the witness, admits over the lost lineage
    p = Planner(
        SpoolTransport(tmp_path / "spool"),
        identity="bare",
        verifier_identities=(VER,),
        journal=store(tmp_path, create=False),
    )
    p.dispatch(*over_b[0][:1], **over_b[0][1])
    assert len(works(tmp_path)) == 3


def test_a_foreign_transport_is_refused(tmp_path):
    store(tmp_path)
    coordinator(tmp_path).tick([cand("A", "src/a")])
    other = StoreJournal.create(tmp_path / "o" / "journal", tmp_path / "o" / "anchor")
    with pytest.raises(JournalCorrupt, match="JOURNAL_BEHIND_TRANSPORT"):
        coordinator(tmp_path, other)  # another store's journal on this store's spool


# ---- recovery is not a new attempt ----------------------------------------------------------


class FakePort:
    def __init__(self):
        self.dispatches = []

    def dispatch_workflow(self, workflow, ref, inputs):
        self.dispatches.append((workflow, ref, dict(inputs)))

    def list_runs(self, workflow, *, event, created_after):
        return []

    def branch_head(self, branch):
        return BASE if branch == "main" else None


def test_recovery_republishes_the_same_record_and_the_adapter_ledger_refuses_a_second_dispatch(
    tmp_path,
):
    store(tmp_path)
    port = FakePort()

    def adapter():
        return FabricAdapter(
            port,
            SpoolTransport(tmp_path / "spool"),
            Crosswalk(tmp_path / "xw.jsonl"),
            pending_dir=tmp_path / "pending",
            clock=lambda: "2026-09-30T15:59:00Z",
            task_statement=lambda w: ("Fix the thing.", ("python -m pytest -q",)),
            required_checks=frozenset({"quality"}),
        )

    c = coordinator(tmp_path)
    c.tick([cand("A", "src/a")])
    work = c.planner.lineages["A"].work
    assert adapter().tick() == ["ACCEPTED:A", "DISPATCHED:A"] and len(port.dispatches) == 1
    # an ordinary restart: the record is still in the transport (claimed), nothing is published
    assert coordinator(tmp_path).tick()["recovered"] == [] and adapter().tick() == []
    assert len(port.dispatches) == 1
    # worst case: the transport lost the claimed record, so recovery publishes it again ...
    claimed = tmp_path / "spool" / "WORK" / "claimed"
    for f in claimed.iterdir():
        f.unlink()
    st = coordinator(tmp_path, identity="coord-2").tick()
    assert st["recovered"] == ["REPUBLISHED:A:WORK"]
    again = SpoolTransport(tmp_path / "spool").published(Channel.WORK)
    # ... as the SAME sealed record: same seal, execution id and attempt, not a new execution
    assert [(r.seal, r.execution_id, r.attempt) for r in again] == [
        (work.seal, work.execution_id, work.attempt)
    ]
    events = adapter().tick()
    assert len(port.dispatches) == 1  # the adapter's ledger refuses a second dispatch
    assert events == ["ACCEPTED:A"]  # accepted again, not dispatched again
    assert coordinator(tmp_path).tick()["lineages"][0]["phase"] == "DISPATCHED"  # unchanged
    # pinned limit: that protection is the adapter's crosswalk ledger. Lose the transport's
    # record AND the ledger, and the republished record is dispatched a second time.
    for f in claimed.iterdir():
        f.unlink()
    (tmp_path / "xw.jsonl").unlink()
    for f in (tmp_path / "pending").iterdir():
        f.unlink()
    assert coordinator(tmp_path).tick()["recovered"] == ["REPUBLISHED:A:WORK"]
    assert adapter().tick() == ["ACCEPTED:A", "DISPATCHED:A"] and len(port.dispatches) == 2
    assert port.dispatches[0] == port.dispatches[1]  # the same payload, not a new work item


# ---- several coordinators -------------------------------------------------------------------


def test_coordinators_ticking_together_prepare_each_compatible_lineage_exactly_once(tmp_path):
    store(tmp_path)
    n = 6
    cs = [
        Coordinator(
            store(tmp_path, create=False),
            SpoolTransport(tmp_path / "spool"),
            identity=f"coord-{i}",
            verifier_identities=(VER,),
            status_path=tmp_path / "status" / f"status-{i}.json",
            max_live=4,
            accept_same_filesystem=True,
        )
        for i in range(n)
    ]
    proposals = [
        cand("A", "src/shared"),
        cand("A2", "src/shared/part"),  # collides with A
        cand("B", "src/b"),
        cand("C", "src/c"),
        cand("D", "src/d"),
        cand("E", "src/e"),
    ]
    start = threading.Barrier(n)
    errors: list[str] = []

    def go(i):
        start.wait()
        try:
            for _ in range(3):
                cs[i].tick(proposals)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(f"{i}: {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    rows = fleet_status(store(tmp_path, create=False))
    roots = [r["lineage_root"] for r in rows]
    assert len(roots) == len(set(roots)) == len(works(tmp_path))  # each prepared once
    assert not {"A", "A2"} <= set(roots)  # never both sides of a collision
    # the bound is decided in the journal commit: six coordinators, never more than four live
    assert len(roots) == 4
    final = cs[0].tick(proposals)
    assert sorted(final["live"]) == sorted(roots) and final["admitted"] == []


def test_the_live_limit_is_part_of_the_dispatch_decision(tmp_path):
    j = store(tmp_path)
    t = SpoolTransport(tmp_path / "spool")
    p = Planner(t, identity="coord-1", verifier_identities=(VER,), journal=j)
    a, fa = cand("A", "src/a")
    b, fb = cand("B", "src/b")
    p.dispatch(a, live_limit=1, **fa)
    with pytest.raises(PlannerError, match=r"^LIVE_LIMIT:1 lineages are live, limit 1$"):
        p.dispatch(b, live_limit=1, **fb)
    assert p.state.seq == 1 and len(works(tmp_path)) == 1 and "B" not in p.lineages
    implement(tmp_path)
    p.pump()  # VERIFYING still counts as live
    with pytest.raises(PlannerError, match=r"^LIVE_LIMIT:"):
        p.dispatch(b, live_limit=1, **fb)
    verify(tmp_path)
    p.pump()  # INTEGRATION_READY does not
    assert p.dispatch(b, live_limit=1, **fb).task_id == "B"
    c, fc = cand("C", "src/c")
    assert p.dispatch(c, **fc).task_id == "C"  # no limit given: unchanged behaviour


# ---- continuity inside a tick ---------------------------------------------------------------


def test_another_coordinators_dispatch_is_never_mistaken_for_lost_history(tmp_path):
    """The witness lists the transport before it replays, so a faster peer is not an alarm."""
    store(tmp_path)
    c1 = coordinator(tmp_path, identity="coord-1", max_live=8)
    c2 = coordinator(tmp_path, identity="coord-2", max_live=8)
    c1.tick([cand("A", "src/a")])  # c2's replica has not seen A
    assert c2.tick()["journal"]["seq"] == 1

    class Interleaved(SpoolTransport):
        hook = None

        def published(self, channel):
            if self.hook:
                hook, self.hook = self.hook, None
                hook()  # a peer commits and publishes after this coordinator's last replay
            return super().published(channel)  # ... and the listing already shows that work

    t3 = Interleaved(tmp_path / "spool")
    c3 = Coordinator(
        store(tmp_path, create=False),
        t3,
        identity="coord-3",
        verifier_identities=(VER,),
        status_path=tmp_path / "status" / "s3.json",
        max_live=8,
        accept_same_filesystem=True,
    )
    c1.tick([cand("B", "src/b")])  # after c3's last replay
    t3.hook = lambda: c1.tick([cand("C", "src/c")])
    # listing-then-replay: C is listed and the replay that follows learns it. With the order
    # reversed (replay, then list) C would be listed but unknown: JOURNAL_BEHIND_TRANSPORT.
    st = c3.tick([cand("D", "src/d")])
    assert st["state"] == "OK" and st["admitted"] == ["D"]
    assert [r["lineage_root"] for r in st["lineages"]] == ["A", "B", "C", "D"]


def test_a_running_coordinator_notices_a_lost_event_below_its_head_before_preparing(tmp_path):
    j = store(tmp_path)
    c = coordinator(tmp_path, max_live=8)
    c.tick([cand("A", "src/a"), cand("B", "src/b"), cand("C", "src/c")])
    for lost, why in (
        (j.root / "000000000001.json", "missing 1"),
        (j.anchor / "000000000001.ack", "JOURNAL_UNANCHORED:event 1"),
    ):
        saved = lost.read_bytes()
        lost.unlink()
        before = tree(tmp_path)
        with pytest.raises(JournalCorrupt, match=why):
            c.tick([cand("D", "src/d")])
        assert tree(tmp_path) == before  # D was not appended or published; nothing else changed
        status = json.loads((tmp_path / "status" / "status.json").read_text())
        assert status["state"] == "HALTED" and why in status["reason"]
        lost.write_bytes(saved)
    assert c.tick([cand("D", "src/d")])["admitted"] == ["D"]  # restored: it goes on


def test_identity_lost_between_append_and_acknowledgement_is_never_published(tmp_path):
    class LosesAnchor(StoreJournal):
        armed = False

        def append(self, seq, data):
            ok = super().append(seq, data)
            if ok and self.armed:
                (self.anchor / STORE_MARKER).unlink()
            return ok

    base = store(tmp_path)
    j = LosesAnchor(base.root, base.anchor, base.store_id)
    p = Planner(
        SpoolTransport(tmp_path / "spool"), identity="c", verifier_identities=(VER,), journal=j
    )
    j.armed = True
    item, fields = cand("A", "src/a")
    with pytest.raises(JournalCorrupt, match="STORE_IDENTITY:anchor directory"):
        p.dispatch(item, **fields)
    # the event was linked before the loss; it is not acknowledged, applied or published
    assert [f.name for f in sorted(j.root.iterdir())] == ["000000000001.json", STORE_MARKER]
    assert list(j.anchor.iterdir()) == [] and works(tmp_path) == [] and p.lineages == {}
    # each store operation checks identity on its own, not only through a preceding read
    for call in (lambda: j.append(2, b"{}"), lambda: j.acknowledge(1, "0" * 64), lambda: j.read(0)):
        with pytest.raises(JournalCorrupt, match="STORE_IDENTITY"):
            call()


def test_the_witness_also_counts_work_records_that_were_already_claimed(tmp_path):
    j = store(tmp_path)
    coordinator(tmp_path).tick([cand("A", "src/a")])
    implement(tmp_path)  # A's WORK record is now in claimed/
    spool = SpoolTransport(tmp_path / "spool")
    assert [r.task_id for r in spool.published(Channel.WORK)] == ["A"]
    assert not list((tmp_path / "spool" / "WORK").glob("*.json"))
    (j.root / "000000000001.json").unlink()
    (j.anchor / "000000000001.ack").unlink()
    with pytest.raises(JournalCorrupt, match="JOURNAL_BEHIND_TRANSPORT:1 published work"):
        coordinator(tmp_path, identity="coord-2")


def test_limit_the_witness_does_not_see_a_lost_later_event_of_a_published_lineage(tmp_path):
    """Pinned: only DISPATCH/REPAIR loss is witnessed; the lineage replays to an earlier phase."""
    j = store(tmp_path)
    c = coordinator(tmp_path)
    c.tick([cand("A", "src/a")])
    implement(tmp_path)
    c.tick()
    verify(tmp_path)
    assert c.tick()["lineages"][0]["phase"] == "INTEGRATION_READY"
    (j.root / "000000000003.json").unlink()
    (j.anchor / "000000000003.ack").unlink()
    st = coordinator(tmp_path, identity="coord-2").tick([cand("X", "src/a/x")])
    assert st["lineages"][0]["phase"] == "VERIFYING"  # earlier, and still holding its scope
    assert st["deferred"] == [["X", "SCOPE_COLLISION:A:src/a/x|src/a"]]


# ---- no scope handover ----------------------------------------------------------------------


def test_the_coordinator_admits_nothing_over_the_scope_of_a_terminal_lineage(tmp_path):
    from project_atlas.orchestration.autonomy.dev_contracts import Finding, FindingCategory

    store(tmp_path)
    c = coordinator(tmp_path, max_live=4)
    c.tick([cand("A", "src/a"), cand("B", "src/b")])
    w1, w2 = implement(tmp_path), implement(tmp_path)
    c.tick()
    spool = SpoolTransport(tmp_path / "spool")
    for _ in range(2):
        req = spool.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity=VER)
        finding = Finding(finding_id="S", category=FindingCategory.SECRET_REQUIRED)
        spool.publish(make_verdict(req, verdict=Verdict.FAIL, findings=(finding,)))
    st = c.tick([cand("X", "src/a/x"), cand("Y", "src/y")])
    assert {r["lineage_root"]: r["phase"] for r in st["lineages"]} == {
        "A": "OWNER_REQUIRED",
        "B": "OWNER_REQUIRED",
        "Y": "DISPATCHED",
    }
    # the verdict ended lineage A; it did not prove A's executor or result branch is gone
    assert st["deferred"] == [["X", "SCOPE_RETAINED:A:src/a/x|src/a"]] and st["admitted"] == ["Y"]
    assert {w1.task_id, w2.task_id} == {"A", "B"}
    # the planner's own default is unchanged: asked directly, it admits over a terminal scope
    item, fields = cand("X", "src/a/x")
    with pytest.raises(PlannerError, match=r"^SCOPE_RETAINED:A:src/a/x\|src/a$"):
        c.planner.dispatch(item, retain_terminal_scopes=True, **fields)
    assert c.planner.dispatch(item, **fields).task_id == "X"


def test_blocked_and_caller_released_scopes_stay_closed_to_the_coordinator(tmp_path):
    store(tmp_path)
    c = coordinator(tmp_path, max_live=4)
    c.tick([cand("A", "src/a"), cand("B", "src/b")])
    # A: another caller reports its execution failed -> BLOCKED. A report is not a fence.
    other = Planner(
        SpoolTransport(tmp_path / "spool"),
        identity="operator",
        verifier_identities=(VER,),
        journal=store(tmp_path, create=False),
    )
    other.fail_execution("A", "runner lost")
    # B: runs to INTEGRATION_READY, then a caller releases it on a revision it merely asserts
    spool = SpoolTransport(tmp_path / "spool")
    for _ in range(2):
        w = spool.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL)
        if w.task_id == "B":
            spool.publish(
                make_result(w, executor_identity=IMPL, result_revision=REV1, result_tree=TREE1)
            )
    c.tick()
    verify(tmp_path)
    c.tick()
    st = c.tick([cand("XB", "src/b/x")])
    assert st["deferred"] == [["XB", "SCOPE_COLLISION:B:src/b/x|src/b"]]  # still a holder
    other.release_scope("B", merged_revision="d" * 40)
    st = c.tick([cand("XA", "src/a/x"), cand("XB", "src/b/x"), cand("Y", "src/y")])
    phases = {r["lineage_root"]: (r["phase"], r["holds_scope"]) for r in st["lineages"]}
    assert phases["A"] == ("BLOCKED", False) and phases["B"] == ("INTEGRATION_READY", False)
    assert sorted(st["deferred"]) == [
        ["XA", "SCOPE_RETAINED:A:src/a/x|src/a"],
        ["XB", "SCOPE_RETAINED:B:src/b/x|src/b"],
    ]
    assert st["admitted"] == ["Y"]
    # a second coordinator and a restart reach the same decision from the journal
    st2 = coordinator(tmp_path, identity="coord-2", max_live=4).tick([cand("XB", "src/b/x")])
    assert st2["deferred"] == [["XB", "SCOPE_RETAINED:B:src/b/x|src/b"]]


def test_a_repaired_acknowledgement_keeps_every_later_status_degraded(tmp_path):
    """Loss of acknowledged continuity stays observable: recovered, never plain OK again."""
    j = store(tmp_path)
    c = coordinator(tmp_path, max_live=8)
    healthy = c.tick([cand("A", "src/a"), cand("B", "src/b")])
    assert healthy["state"] == "OK" and healthy["acknowledgement_continuity"] == "INTACT"
    assert healthy["repairs"] == []
    digest = (j.anchor / "000000000002.ack").read_text()
    (j.anchor / "000000000002.ack").unlink()  # only the head's acknowledgement file is lost
    st = c.tick([cand("Z", "src/z")])
    # continuity could be re-established (the event is unchanged), so the tick proceeds ...
    assert st["admitted"] == ["Z"] and st["journal"]["seq"] == 3
    # ... and says what happened
    assert st["state"] == "DEGRADED" and st["acknowledgement_continuity"] == "REPAIRED"
    assert st["repairs"] == [{"seq": 2, "kind": "RESTORED", "by": "coord-1", "digest": digest}]
    assert (j.anchor / "000000000002.repair").is_file()
    assert (j.anchor / "000000000002.ack").read_text() == digest
    # durable: later ticks, other coordinators and restarts all still report it
    assert c.tick()["state"] == "DEGRADED"
    again = coordinator(tmp_path, identity="coord-2").tick()
    assert again["state"] == "DEGRADED" and again["repairs"] == st["repairs"]
    on_disk = json.loads((tmp_path / "status" / "status.json").read_text())
    assert on_disk["acknowledgement_continuity"] == "REPAIRED" and on_disk != healthy


def test_a_new_coordinator_adopting_an_unacknowledged_head_reports_it(tmp_path):
    j = store(tmp_path)
    coordinator(tmp_path).tick([cand("A", "src/a")])
    (j.anchor / "000000000001.ack").unlink()
    # a coordinator that never saw the acknowledgement cannot tell loss from a writer that
    # died before acknowledging; either way the acknowledgement it writes is a repair
    st = coordinator(tmp_path, identity="coord-2").tick([cand("B", "src/b")])
    assert st["state"] == "DEGRADED" and st["admitted"] == ["B"]
    assert [(r["seq"], r["kind"], r["by"]) for r in st["repairs"]] == [(1, "ADOPTED", "coord-2")]


def test_continuity_that_cannot_be_reestablished_halts_and_keeps_the_repair_evidence(tmp_path):
    j = store(tmp_path)
    c = coordinator(tmp_path, max_live=8)
    c.tick([cand("A", "src/a"), cand("B", "src/b"), cand("C", "src/c")])
    for lost in ("000000000002.ack", "000000000003.ack"):
        (j.anchor / lost).unlink()
    before = works(tmp_path)
    with pytest.raises(JournalCorrupt, match="JOURNAL_UNANCHORED:event 2"):
        c.tick([cand("Z", "src/z")])
    status = json.loads((tmp_path / "status" / "status.json").read_text())
    # the head's acknowledgement was restored (recorded), the one below it cannot be: halt
    assert status["state"] == "HALTED" and [r["seq"] for r in status["repairs"]] == [3]
    assert works(tmp_path) == before and not (j.root / "000000000004.json").exists()


def test_a_continuity_failure_in_the_constructor_also_leaves_a_halted_status(tmp_path):
    j = store(tmp_path)
    coordinator(tmp_path).tick([cand("A", "src/a")])
    (j.root / "000000000001.json").unlink()
    with pytest.raises(JournalCorrupt, match="JOURNAL_TRUNCATED"):
        coordinator(tmp_path, identity="coord-2")
    status = json.loads((tmp_path / "status" / "status.json").read_text())
    assert status["state"] == "HALTED" and status["reason"].startswith("JOURNAL_TRUNCATED")

    class NoStatus(Coordinator):
        def _write_status(self, status):
            raise OSError("status disk full")

    with pytest.raises(JournalCorrupt, match="JOURNAL_TRUNCATED"):  # not masked by the OSError
        NoStatus(
            store(tmp_path, create=False),
            SpoolTransport(tmp_path / "spool"),
            identity="coord-3",
            verifier_identities=(VER,),
            status_path=tmp_path / "status" / "status.json",
            accept_same_filesystem=True,
        )
    (tmp_path / "adir").mkdir()
    with pytest.raises(PlannerError, match=r"STATUS_PATH:.* is a directory"):
        Coordinator(
            store(tmp_path, create=False),
            SpoolTransport(tmp_path / "spool"),
            identity="coord-4",
            verifier_identities=(VER,),
            status_path=tmp_path / "adir",
            accept_same_filesystem=True,
        )


# ---- candidates and status ------------------------------------------------------------------


def test_a_malformed_candidate_list_raises_before_anything_is_read_or_written(tmp_path):
    j = store(tmp_path)
    c = coordinator(tmp_path)
    c.tick([cand("A", "src/a")])
    implement(tmp_path)  # a RESULT is pending: a pump would journal it and publish a request
    before = tree(tmp_path)
    item, fields = cand("Z", "src/z")
    pair = "a candidate is a .QueueItem, work fields. pair"
    bad_lists = [
        ([item], pair),
        ([(item,)], pair),
        ([("Z", fields)], pair),
        ([(item, None)], pair),
        ([(item, {1: "x"})], "work field names must be strings"),
        ([(item, {**fields, "live_limit": 99})], "work fields may not set"),
        ([(item, {**fields, "retain_terminal_scopes": False})], "work fields may not set"),
        ([(item, {**fields, "execution_ordinal": 5})], "work fields may not set"),
        ([(item, {**fields, "item": 1})], "work fields may not set"),
        ([(item, {**fields, "self": 1})], "work fields may not set"),
        ([(item, fields), (item, fields)], "duplicate task_id"),
        ([cand("Z", "src/z", severity=9)], "severity out of range"),
        (
            [
                (
                    QueueItem(
                        task_id="Z", title="Z", category=Category.RELIABILITY, depends_on=("Q",)
                    ),
                    fields,
                )
            ],
            "unknown dependency",
        ),
    ]
    for bad, why in bad_lists:
        with pytest.raises((PlannerError, ValueError), match=why):
            c.tick(bad)
    assert tree(tmp_path) == before  # the pending result was not pumped: nothing was written
    assert c.planner.lineages["A"].phase is Phase.DISPATCHED
    # ... and nothing was read either: on a journal that no longer replays, the candidate
    # error is what surfaces, not the continuity failure
    (j.root / "000000000001.json").write_bytes(b"{}")
    with pytest.raises(PlannerError, match=pair):
        c.tick([item])
    with pytest.raises(JournalCorrupt):
        c.tick([(item, fields)])


def test_a_candidate_that_dispatch_refuses_is_reported_and_the_tick_goes_on(tmp_path):
    store(tmp_path)
    c = coordinator(tmp_path, max_live=4)
    dep = QueueItem(task_id="B", title="B", category=Category.RELIABILITY, depends_on=("A",))
    st = c.tick(
        [
            cand("A", "src/a", severity=3),
            (dep, cand("B", "src/b")[1]),
            (cand("C-R1", "src/c")[0], cand("C-R1", "src/c")[1]),  # reserved repair suffix
            (cand("D", "src/d")[0], {**cand("D", "src/d")[1], "task_id": "other"}),
            cand("E", "../outside"),
            cand("F", "src/f"),
        ]
    )
    assert st["admitted"] == ["A", "F"] and st["state"] == "OK"
    refused = dict(st["deferred"])
    assert set(refused) == {"C-R1", "D", "E"} and all(
        v.startswith("REFUSED:") for v in refused.values()
    )
    # B waits for A (an in-flight task does not satisfy a dependency); once A is done, the
    # planner validates B on its own and refuses it: dependencies are not supported yet
    implement(tmp_path)
    implement(tmp_path)
    c.tick()
    verify(tmp_path)
    verify(tmp_path)
    c.tick()
    st = c.tick([cand("A", "src/a"), (dep, cand("B", "src/b")[1])])
    assert st["admitted"] == [] and st["deferred"][0][0] == "B"
    assert "unknown dependency" in st["deferred"][0][1]


def test_contention_is_reported_as_deferred_and_ends_the_preparation_step(tmp_path):
    class Busy(StoreJournal):
        busy = False

        def append(self, seq, data):
            return False if self.busy else super().append(seq, data)

    base = store(tmp_path)
    j = Busy(base.root, base.anchor, base.store_id)
    c = coordinator(tmp_path, j, max_live=4)
    j.busy = True
    st = c.tick([cand("A", "src/a", severity=3), cand("B", "src/b")])
    assert st["admitted"] == [] and st["deferred"] == [["A", "JOURNAL_CONTENDED"]]  # B not tried
    j.busy = False
    assert c.tick([cand("A", "src/a", severity=3), cand("B", "src/b")])["admitted"] == ["A", "B"]


def test_the_status_file_may_not_live_inside_the_store_or_the_transport(tmp_path):
    j = store(tmp_path)
    spool = SpoolTransport(tmp_path / "spool")
    for inside in (
        j.root / STORE_MARKER,
        j.root / "000000000001.json",
        j.root / "status.json",
        j.anchor / "status.json",
        tmp_path / "spool" / "WORK" / "status.json",
    ):
        with pytest.raises(PlannerError, match="STATUS_PATH"):
            Coordinator(
                j,
                spool,
                identity="c",
                verifier_identities=(VER,),
                status_path=inside,
                accept_same_filesystem=True,
            )
    assert json.loads((j.root / STORE_MARKER).read_text())["store"] == j.store_id


def test_a_continuity_failure_during_preparation_halts_instead_of_being_reported_as_refused(
    tmp_path,
):
    class LosesHead(StoreJournal):
        armed = False

        def append(self, seq, data):
            if self.armed:  # the head is replaced after the tick's continuity step
                self.armed = False
                prev = self._path(seq - 1)
                prev.write_bytes(prev.read_bytes().replace(b"coord-1", b"coord-9"))
            return super().append(seq, data)

    base = store(tmp_path)
    j = LosesHead(base.root, base.anchor, base.store_id)
    c = coordinator(tmp_path, j, max_live=8)
    c.tick([cand("A", "src/a")])
    j.armed = True
    with pytest.raises(JournalCorrupt, match="JOURNAL_DIVERGED:event 1"):
        c.tick([cand("B", "src/b"), cand("C", "src/c")])
    status = json.loads((tmp_path / "status" / "status.json").read_text())
    assert status["state"] == "HALTED" and len(works(tmp_path)) == 1  # B was not published


def test_a_capacity_filled_by_a_peer_ends_the_preparation_step_quietly(tmp_path):
    store(tmp_path)
    c = coordinator(tmp_path, max_live=1)
    peer = coordinator(tmp_path, identity="coord-2", max_live=1)
    peer.tick([cand("P", "src/p")])
    c.live = lambda: frozenset()  # this coordinator's view is stale: it still sees capacity
    st = c.tick([cand("A", "src/a"), cand("B", "src/b")])
    # the journal commit refused with LIVE_LIMIT; that is not a property of the candidate
    assert st["admitted"] == [] and st["deferred"] == [] and st["journal"]["seq"] == 1


def test_published_lists_only_records_that_belong_to_the_channel(tmp_path):
    store(tmp_path)
    c = coordinator(tmp_path)
    c.tick([cand("A", "src/a")])
    implement(tmp_path)
    c.tick()  # A's verification request is now in the spool; A's WORK record is in claimed/
    spool = SpoolTransport(tmp_path / "spool")
    work_dir = tmp_path / "spool" / "WORK"
    real = next((work_dir / "claimed").glob("[0-9a-f]*[0-9a-f].json"))
    (work_dir / ("0" * 64 + ".json")).write_bytes(real.read_bytes())  # name is not its seal
    (work_dir / "junk.json").write_text("{not json")
    request = next((tmp_path / "spool" / "VERIFICATION").glob("*.json"))
    (work_dir / request.name).write_bytes(request.read_bytes())  # a record of another channel
    assert [r.task_id for r in spool.published(Channel.WORK)] == ["A"]
    assert c.tick()["state"] == "OK"  # none of these is a published work unknown to the journal
