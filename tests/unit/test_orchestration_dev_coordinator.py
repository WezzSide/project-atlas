"""ATLAS-DEVQ-0008: store identity, transport witness and the Coordinator tick.

Local tests of preparation, tracking and recovery over a durable store. Nothing here causes a
workflow run; the one test that uses the fabric adapter does so with a fake port to show that
recovery re-publishes the same record and the adapter's ledger refuses a second dispatch.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
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
    Activation,
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
from project_atlas.orchestration.autonomy.dev_transport import (
    Channel,
    InMemoryTransport,
    TransportError,
    TransportUnavailable,
)

BASE = "a" * 40
REV1, TREE1 = "b" * 40, "c" * 40
IMPL, VER = "vps1-impl", "vps2-ver"


@pytest.fixture(autouse=True)
def _short_adoption_wait(monkeypatch):
    """Adoption waits 2 s for a live writer in production; tests use a dead or absent one."""
    from project_atlas.orchestration.autonomy import dev_planner

    monkeypatch.setattr(dev_planner, "ADOPT_GRACE_TRIES", 3)
    monkeypatch.setattr(dev_planner, "ADOPT_GRACE_STEP", 0.001)


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
    kw.setdefault("accept_unbound_transport", True)  # these tests use an unbound spool
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
    markers = [json.loads((d / STORE_MARKER).read_text()) for d in (j.home, j.anchor_home)]
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
        StoreJournal.attach(j.home, other.anchor_home)
    with pytest.raises(JournalCorrupt, match="has no store marker"):
        StoreJournal.attach(j.home, tmp_path / "nowhere")
    assert not (tmp_path / "nowhere").exists()
    bads = (
        "{",
        "[]",
        json.dumps({"v": 1, "role": "anchor", "store": j.store_id}),  # the earlier layout
        json.dumps({"v": 3, "role": "anchor", "store": j.store_id}),
        json.dumps({"v": 2, "role": "journal", "store": j.store_id}),
        json.dumps({"v": 2, "role": "anchor", "store": "xyz"}),
    )
    for bad in bads:
        good = (j.anchor_home / STORE_MARKER).read_text()
        (j.anchor_home / STORE_MARKER).write_text(bad)
        with pytest.raises(JournalCorrupt, match="STORE_IDENTITY"):
            store(tmp_path, create=False)
        (j.anchor_home / STORE_MARKER).write_text(good)
    # a plain DirJournal cannot be pointed at a store by accident: the marker is not an event
    with pytest.raises(JournalCorrupt, match="directory is not events"):
        fleet_status(DirJournal(j.root, anchor=j.anchor))


def test_a_lost_anchor_stops_a_running_planner_and_is_never_recreated(tmp_path):
    j = store(tmp_path)
    t = SpoolTransport(tmp_path / "spool")
    p = Planner(t, identity="coord-1", verifier_identities=(VER,), journal=j)
    item, fields = cand("A", "src/a")
    p.dispatch(item, **fields)  # a ONE-event journal: the case a bare DirJournal cannot detect
    shutil.rmtree(j.anchor_home)
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
    assert tree(tmp_path) == before and not j.anchor_home.exists()  # nothing appended or made
    # an empty directory put back in its place is still not this store's anchor
    j.anchor_home.mkdir()
    with pytest.raises(JournalCorrupt, match="STORE_IDENTITY"):
        p.dispatch(item2, **fields2)
    with pytest.raises(PlannerError, match="STORE_EXISTS"):
        StoreJournal.create(j.home, j.anchor_home)  # and the journal cannot be "started fresh"


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
        shutil.rmtree(anchor, ignore_errors=True)


# ---- what a coordinator refuses to run on ---------------------------------------------------


def test_a_coordinator_has_no_in_memory_or_unanchored_fallback(tmp_path):
    j = store(tmp_path)
    spool = SpoolTransport(tmp_path / "spool")
    kw = dict(identity="coord-1", verifier_identities=(VER,), status_path=tmp_path / "s.json")
    for journal in (MemoryJournal(), DirJournal(tmp_path / "plain"), None):
        with pytest.raises(PlannerError, match="COORDINATOR_NEEDS_STORE"):
            Coordinator(
                journal, spool, accept_same_filesystem=True, accept_unbound_transport=True, **kw
            )
    with pytest.raises(PlannerError, match="COORDINATOR_NEEDS_DURABLE_TRANSPORT"):
        Coordinator(
            j, InMemoryTransport(), accept_same_filesystem=True, accept_unbound_transport=True, **kw
        )
    with pytest.raises(PlannerError, match="STORE_BOUNDARY:journal and anchor are on the same"):
        Coordinator(j, spool, **kw)  # the reduced boundary must be accepted explicitly
    for bad in (0, -1, True, 1.5):
        with pytest.raises(PlannerError, match="max_live"):
            Coordinator(
                j,
                spool,
                accept_same_filesystem=True,
                accept_unbound_transport=True,
                max_live=bad,
                **kw,
            )
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
        accept_unbound_transport=True,
    )
    with pytest.raises(OSError):
        c.tick([cand("A", "src/a")])
    assert works(tmp_path) == []
    died = json.loads((tmp_path / "status" / "status.json").read_text())
    assert died["state"] == "HALTED" and died["reason"].startswith("IO_ERROR:")  # never OK
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
        "transport_store",
        "continuity_boundary",
        "live_conflicting_work_boundary",
        "repairs",
        "scope_handover_observer",
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
            accept_unbound_transport=True,
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
        accept_unbound_transport=True,
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
                (self.anchor_home / STORE_MARKER).unlink()
            return ok

    base = store(tmp_path)
    j = LosesAnchor(base.home, base.anchor_home, base.store_id)
    p = Planner(
        SpoolTransport(tmp_path / "spool"), identity="c", verifier_identities=(VER,), journal=j
    )
    j.armed = True
    item, fields = cand("A", "src/a")
    with pytest.raises(JournalCorrupt, match="STORE_IDENTITY:anchor directory"):
        p.dispatch(item, **fields)
    # the event was linked before the loss; it is not acknowledged, applied or published
    assert [f.name for f in sorted(j.root.iterdir())] == ["000000000001.json", STORE_MARKER]
    assert [f.name for f in j.anchor.iterdir()] == [STORE_MARKER]  # no acknowledgement
    assert works(tmp_path) == [] and p.lineages == {}
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
    # it is the journal's rule, not an option of the coordinator: asked directly, the planner
    # refuses as well (ATLAS-DEVQ-0009)
    item, fields = cand("X", "src/a/x")
    with pytest.raises(PlannerError, match=r"^SCOPE_RETAINED:A:src/a/x\|src/a$"):
        c.planner.dispatch(item, **fields)
    assert "X" not in c.planner.lineages


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
            accept_unbound_transport=True,
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
            accept_unbound_transport=True,
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
    j = Busy(base.home, base.anchor_home, base.store_id)
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
        j.home / STORE_MARKER,
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
                accept_unbound_transport=True,
            )
    assert json.loads((j.home / STORE_MARKER).read_text())["store"] == j.store_id


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
    j = LosesHead(base.home, base.anchor_home, base.store_id)
    c = coordinator(tmp_path, j, max_live=8)
    c.tick([cand("A", "src/a")])
    j.armed = True
    with pytest.raises(JournalCorrupt, match="JOURNAL_DIVERGED:event 1"):
        c.tick([cand("B", "src/b")])  # one candidate: no later step that would raise instead
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
    # a sealed work the journal never saw, stored under a name that is not its seal: not a
    # record of this channel as far as a claim is concerned, so not a witness either
    from project_atlas.orchestration.autonomy.dev_contracts import make_work
    from project_atlas.orchestration.autonomy.dev_transport import encode

    foreign = make_work(
        task_id="F",
        execution_id="F-E1",
        lineage_root="F",
        acceptance_contract=("ok",),
        **cand("F", "src/f")[1],
    )
    (work_dir / ("1" * 64 + ".json")).write_text(encode(foreign))
    (work_dir / "junk.json").write_text("{not json")
    request = next((tmp_path / "spool" / "VERIFICATION").glob("*.json"))
    (work_dir / request.name).write_bytes(request.read_bytes())  # a record of another channel
    assert [r.task_id for r in spool.published(Channel.WORK)] == ["A"]
    assert c.tick()["state"] == "OK"  # none of these is a published work unknown to the journal


def test_unreadable_repair_evidence_stops_the_tick_before_it_acts(tmp_path):
    j = store(tmp_path)
    c = coordinator(tmp_path, max_live=8)
    c.tick([cand("A", "src/a")])
    (j.anchor / "000000000001.repair").write_text("garbage")
    before = tree(tmp_path)
    with pytest.raises(JournalCorrupt, match=r"repair record 000000000001\.repair"):
        c.tick([cand("N", "src/n")])
    assert tree(tmp_path) == before  # N was not appended or published
    status = json.loads((tmp_path / "status" / "status.json").read_text())
    assert status["state"] == "HALTED" and "repairs" not in status  # they could not be read
    with pytest.raises(JournalCorrupt, match="repair record"):
        coordinator(tmp_path, identity="coord-2")  # and no new coordinator starts on it


def test_unreadable_store_directories_at_construction_leave_a_halted_status(tmp_path):
    j = store(tmp_path)
    coordinator(tmp_path).tick([cand("A", "src/a")])
    attached = store(tmp_path, create=False)
    shutil.rmtree(j.anchor_home)
    with pytest.raises(JournalCorrupt, match="STORE_IDENTITY:store directories are not readable"):
        coordinator(tmp_path, attached, identity="coord-2")
    status = json.loads((tmp_path / "status" / "status.json").read_text())
    assert status["state"] == "HALTED" and status["continuity_boundary"] == "UNKNOWN"


def test_an_anchor_directory_removed_under_a_running_coordinator_leaves_a_halted_status(tmp_path):
    j = store(tmp_path)
    c = coordinator(tmp_path, max_live=8)
    assert c.tick([cand("A", "src/a")])["state"] == "OK"
    for f in j.anchor.iterdir():
        f.unlink()
    j.anchor.rmdir()
    before = tree(tmp_path)
    with pytest.raises(JournalCorrupt, match="STORE_IDENTITY"):
        c.tick([cand("N", "src/n")])
    assert tree(tmp_path) == before and not j.anchor.exists()  # nothing appended or re-created
    status = json.loads((tmp_path / "status" / "status.json").read_text())
    assert status["state"] == "HALTED" and status["reason"].startswith("STORE_IDENTITY")


def test_a_repair_record_that_cannot_be_written_never_leaves_an_ok_status(tmp_path, monkeypatch):
    j = store(tmp_path)
    c = coordinator(tmp_path, max_live=8)
    assert c.tick([cand("A", "src/a")])["state"] == "OK"
    ack = j.anchor / "000000000001.ack"
    ack.unlink()

    def read_only(self, seq, digest, kind, by):
        raise OSError(30, "anchor is read-only")

    monkeypatch.setattr(DirJournal, "record_repair", read_only)
    before = tree(tmp_path)
    with pytest.raises(OSError, match="read-only"):
        c.tick([cand("N", "src/n")])
    assert tree(tmp_path) == before and not ack.exists()  # no record, so no acknowledgement
    status = json.loads((tmp_path / "status" / "status.json").read_text())
    assert status["state"] == "HALTED" and status["reason"].startswith("IO_ERROR:OSError")


def test_a_repair_record_is_never_written_into_an_anchor_of_another_store(tmp_path):
    j = store(tmp_path)
    coordinator(tmp_path).tick([cand("A", "src/a")])
    digest = (j.anchor / "000000000001.ack").read_text()
    other = StoreJournal.create(tmp_path / "o" / "journal", tmp_path / "o" / "anchor")
    marker = j.anchor_home / "STORE.json"
    marker.write_bytes((other.anchor_home / "STORE.json").read_bytes())  # the anchor is now foreign
    with pytest.raises(JournalCorrupt, match="STORE_IDENTITY"):
        j.record_repair(1, digest, "RESTORED", "coord-1")
    with pytest.raises(JournalCorrupt, match="STORE_IDENTITY"):
        j.repairs()
    assert not list(j.anchor.glob("*.repair"))


# ---- verified scope handover (ATLAS-DEVQ-0009) ----------------------------------------------

BASE2 = "9" * 40


def cand2(task, *paths):
    """A candidate that starts from BASE2 instead of BASE."""
    item, work = cand(task, *paths)
    return item, work | {"base_revision": BASE2}


class Observer:
    identity = "observer:test"

    def __init__(self, *contained):
        self.contained, self.calls = set(contained), []

    def result_in_base(self, *, repository, result_revision, base_revision):
        self.calls.append((result_revision, base_revision))
        if (result_revision, base_revision) in self.contained:
            return {"merge_base": result_revision}
        return None


def test_the_coordinator_hands_a_scope_over_only_on_its_observers_evidence(tmp_path):
    store(tmp_path)
    obs = Observer((REV1, BASE2))
    c = coordinator(tmp_path, max_live=4, observer=obs)
    st = c.tick([cand("A", "src/a")])
    assert st["scope_handover_observer"] == "observer:test"
    implement(tmp_path)
    c.tick()
    verify(tmp_path)
    assert c.tick()["lineages"][0]["phase"] == "INTEGRATION_READY"
    # the same base: nothing observed, A still owns src/a
    st = c.tick([cand("X", "src/a/x")])
    assert st["deferred"] == [["X", "SCOPE_COLLISION:A:src/a/x|src/a"]] and st["admitted"] == []
    # a base the observer saw A's result in
    st = c.tick([cand2("X", "src/a/x")])
    assert st["admitted"] == ["X"] and st["deferred"] == []
    rows = {r["lineage_root"]: r for r in st["lineages"]}
    assert rows["A"]["holds_scope"] is False and rows["A"]["handover"] == BASE2
    assert rows["A"]["handed_over_to"] == ("X",) and rows["X"]["holds_scope"] is True
    assert obs.calls == [(REV1, BASE), (REV1, BASE2)]
    # a coordinator without an observer reads the same journal and hands nothing over
    plain = coordinator(tmp_path, identity="coord-2", max_live=4)
    st = plain.tick([cand2("Y", "src/a/y")])
    assert st["scope_handover_observer"] == "NONE"
    assert st["deferred"] == [["Y", "SCOPE_RETAINED:A:src/a/y|src/a"]]
    on_disk = json.loads((tmp_path / "status" / "status.json").read_text())
    assert on_disk["lineages"] == json.loads(json.dumps(st["lineages"]))
    assert on_disk["lineages"][0]["handed_over_to"] == ["X"]


def test_an_io_error_in_the_constructor_leaves_a_halted_status(tmp_path, monkeypatch):
    store(tmp_path)
    assert coordinator(tmp_path).tick([cand("A", "src/a")])["state"] == "OK"

    def unreadable(self, channel):
        raise PermissionError(13, "spool is not readable")

    monkeypatch.setattr(SpoolTransport, "published", unreadable)
    with pytest.raises(PermissionError):
        coordinator(tmp_path, identity="coord-2")
    status = json.loads((tmp_path / "status" / "status.json").read_text())
    assert status["state"] == "HALTED"
    assert status["reason"].startswith("IO_ERROR:PermissionError")


def test_a_pathological_repair_record_halts_instead_of_leaving_an_old_ok(tmp_path):
    j = store(tmp_path)
    c = coordinator(tmp_path)
    assert c.tick([cand("A", "src/a")])["state"] == "OK"
    (j.anchor / "000000000001.repair").write_text("[" * 200_000)
    before = tree(tmp_path)
    with pytest.raises(JournalCorrupt, match=r"repair record 000000000001\.repair"):
        c.tick([cand("N", "src/n")])
    assert tree(tmp_path) == before
    status = json.loads((tmp_path / "status" / "status.json").read_text())
    assert status["state"] == "HALTED"


def test_limit_repair_records_are_trusted_as_files(tmp_path):
    """Pinned limits: a record is not checked against the journal, and deleting it hides it."""
    j = store(tmp_path)
    c = coordinator(tmp_path)
    c.tick([cand("A", "src/a")])
    fabricated = {"v": 1, "seq": 999, "digest": "f" * 64, "kind": "ADOPTED", "by": "nobody"}
    record = j.anchor / "000000000999.repair"
    record.write_text(json.dumps(fabricated))
    st = c.tick()
    assert st["state"] == "DEGRADED" and [r["seq"] for r in st["repairs"]] == [999]
    record.unlink()
    st = c.tick()
    assert st["state"] == "OK" and st["repairs"] == []


def test_a_coordinator_checks_its_observer_before_anything_else(tmp_path):
    class Nameless:
        def result_in_base(self, **kw):
            return {"merge_base": "x"}

    store(tmp_path)
    assert coordinator(tmp_path).tick()["state"] == "OK"
    with pytest.raises(PlannerError, match="observer needs a non-empty identity"):
        coordinator(tmp_path, identity="coord-2", observer=Nameless())
    status = json.loads((tmp_path / "status" / "status.json").read_text())
    assert status["state"] == "OK"  # a configuration refusal writes no status
    # also when the store is unusable: the refusal, not an error from writing a status
    attached = store(tmp_path, create=False)
    for f in attached.anchor.iterdir():
        f.unlink()
    attached.anchor.rmdir()
    with pytest.raises(PlannerError, match="observer needs a non-empty identity"):
        coordinator(tmp_path, attached, identity="coord-3", observer=Nameless())


# ---- executor ownership (ATLAS-DEVQ-0011) ---------------------------------------------------

E1, E2 = "vps1-impl", "vps4-impl"


def _run(tmp_path, who):
    """``who`` claims the next work addressed to it and publishes a result."""
    t = SpoolTransport(tmp_path / "spool")
    w = t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=who)
    if w is not None:
        t.publish(make_result(w, executor_identity=who, result_revision=REV1, result_tree=TREE1))
    return w


def test_the_coordinator_assigns_each_lineage_to_one_executor_and_reports_who_owns_what(tmp_path):
    store(tmp_path)
    c = coordinator(tmp_path, max_live=4, executors=(E1, E2))
    st = c.tick([cand("A", "src/a"), cand("B", "src/b"), cand("C", "src/c")])
    assert st["admitted"] == ["A", "B"] and st["deferred"] == [["C", "EXECUTOR_BUSY"]]
    assert st["executors"] == {E1: ["A"], E2: ["B"]} and st["executor_limit"] == 1
    rows = {r["lineage_root"]: r for r in st["lineages"]}
    assert (rows["A"]["executor"], rows["B"]["executor"]) == (E1, E2)
    # each executor receives its own lineage only
    assert _run(tmp_path, E2).task_id == "B" and _run(tmp_path, E2) is None
    assert _run(tmp_path, "intruder") is None
    assert _run(tmp_path, E1).task_id == "A"
    st = c.tick([cand("C", "src/c")])
    # both are being verified: their executors still own them
    assert st["executors"] == {E1: ["A"], E2: ["B"]} and st["deferred"] == [["C", "EXECUTOR_BUSY"]]
    verify(tmp_path)
    verify(tmp_path)
    st = c.tick([cand("C", "src/c")])
    assert st["admitted"] == ["C"] and st["executors"] == {E1: ["C"], E2: []}
    on_disk = json.loads((tmp_path / "status" / "status.json").read_text())
    assert on_disk["executors"] == {E1: ["C"], E2: []}
    # a second coordinator with another pool reads the same owners from the journal
    other = coordinator(tmp_path, identity="coord-2", max_live=4, executors=("vps9-impl",))
    assert other.tick()["executors"] == {E1: ["C"], "vps9-impl": []}
    # and one without a pool assigns nothing but still reports them
    plain = coordinator(tmp_path, identity="coord-3", max_live=4)
    st = plain.tick([cand("D", "src/d")])
    assert st["executors"] == {E1: ["C"]} and st["admitted"] == ["D"]
    assert {r["lineage_root"]: r["executor"] for r in st["lineages"]}["D"] == ""


def test_a_reassigned_execution_is_recovered_and_tracked_without_a_second_valid_writer(tmp_path):
    j = store(tmp_path)
    c = coordinator(tmp_path, max_live=4, executors=(E1, E2))
    c.tick([cand("A", "src/a")])
    spool = SpoolTransport(tmp_path / "spool")
    old = spool.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=E1)  # E1 starts working
    new = c.planner.reassign("A", executor_pool=(E2,), reason="runner unreachable")
    events = len(list(j.root.glob("*.json")))
    # the fenced work record is still in the spool: the journal knows it, the witness is quiet
    st = c.tick()
    assert st["state"] == "OK" and st["executors"] == {E1: [], E2: ["A"]}
    assert st["lineages"][0]["epoch"] == 1 and st["lineages"][0]["execution_id"] == "A-E1-X1"
    # the record of the new execution is lost before E2 took it: recovery re-publishes the
    # SAME record with the SAME address; it is not another execution
    (tmp_path / "spool" / "WORK" / f"{new.seal}.json").unlink()
    st = c.tick()
    assert st["recovered"] == ["REPUBLISHED:A:WORK"]
    assert len(list(j.root.glob("*.json"))) == events + 0 and st["lineages"][0]["epoch"] == 1
    assert spool.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=E1) is None
    # E1 finishes the fenced execution late; E2 finishes the current one
    spool.publish(make_result(old, executor_identity=E1, result_revision=REV1, result_tree=TREE1))
    st = c.tick()
    assert st["lineages"][0]["phase"] == "DISPATCHED" and st["records_quarantined"] == 1
    assert _run(tmp_path, E2).seal == new.seal
    st = c.tick()
    assert st["lineages"][0]["phase"] == "VERIFYING" and st["state"] == "OK"
    # a restart replays to the same owner
    again = coordinator(tmp_path, identity="coord-2", max_live=4).tick()
    assert again["executors"] == {E2: ["A"]} and again["lineages"][0]["epoch"] == 1


def test_executors_need_a_transport_that_delivers_to_the_addressee(tmp_path):
    class Unaddressed(SpoolTransport):
        addressed = 1  # truthy is not enough: the attribute must be exactly True

    store(tmp_path)
    attached = store(tmp_path, create=False)
    common = dict(
        identity="coord-1",
        verifier_identities=(VER,),
        status_path=tmp_path / "status" / "status.json",
        accept_same_filesystem=True,
        accept_unbound_transport=True,
    )
    with pytest.raises(PlannerError, match="COORDINATOR_NEEDS_ADDRESSED_TRANSPORT"):
        Coordinator(attached, Unaddressed(tmp_path / "spool"), executors=(E1,), **common)
    for pool, limit, why in (((E1, E1), 1, "duplicate"), ((E1,), 0, "executor_limit")):
        with pytest.raises(PlannerError, match=why):
            coordinator(tmp_path, executors=pool, executor_limit=limit)
    assert not (tmp_path / "status" / "status.json").exists()  # configuration: no status
    Coordinator(attached, Unaddressed(tmp_path / "spool"), **common)  # no pool: no requirement
    item, fields = cand("Z", "src/z")
    for key in ("executor_pool", "executor_limit"):
        with pytest.raises(PlannerError, match="work fields may not set"):
            coordinator(tmp_path).tick([(item, {**fields, key: (E1,)})])


def test_the_executor_limit_is_the_coordinators_and_a_busy_pool_ends_the_step(tmp_path):
    store(tmp_path)
    c = coordinator(tmp_path, max_live=8, executors=(E1, E2), executor_limit=2)
    cands = [cand(n, f"src/{n.lower()}") for n in ("A", "B", "C", "D", "F", "G")]
    st = c.tick(cands)
    assert st["admitted"] == ["A", "B", "C", "D"] and st["executor_limit"] == 2
    assert st["executors"] == {E1: ["A", "C"], E2: ["B", "D"]}
    assert st["deferred"] == [["F", "EXECUTOR_BUSY"]]  # G was not tried: the pool is full


def test_an_address_that_cannot_be_read_halts_the_coordinator(tmp_path):
    from project_atlas.orchestration.autonomy.dev_transport import TransportError

    store(tmp_path)
    c = coordinator(tmp_path, max_live=4, executors=(E1,))
    st = c.tick([cand("A", "src/a")])
    assert st["state"] == "OK"
    seal = st["lineages"][0]["work_seal"]
    (tmp_path / "spool" / "WORK" / f"{seal}.to").write_text("vps1-impl\n")
    before = tree(tmp_path)
    with pytest.raises(TransportError, match="invalid addressee"):
        c.tick([cand("N", "src/n")])
    status = json.loads((tmp_path / "status" / "status.json").read_text())
    assert status["state"] == "HALTED" and status["reason"].startswith("TRANSPORT_ERROR:")
    assert tree(tmp_path) == before  # nothing appended, published or re-addressed
    with pytest.raises(TransportError):
        coordinator(tmp_path, identity="coord-2", executors=(E1,)).tick()


def test_a_record_the_transport_refuses_after_its_event_is_not_reported_as_a_refusal(tmp_path):
    from project_atlas.orchestration.autonomy.dev_contracts import make_work
    from project_atlas.orchestration.autonomy.dev_transport import TransportError

    store(tmp_path)
    c = coordinator(tmp_path, max_live=4, executors=(E1,))
    c.tick()
    item, fields = cand("A", "src/a")
    work = make_work(
        task_id="A",
        execution_id="A-E1",
        lineage_root="A",
        required_role=Role.IMPLEMENTER,
        max_attempts=item.max_attempts,
        **fields,
    )
    # the spool already holds another address for exactly this record
    (tmp_path / "spool" / "WORK" / f"{work.seal}.to").write_text("someone-else")
    with pytest.raises(TransportError, match="different address"):
        c.tick([(item, fields)])
    status = json.loads((tmp_path / "status" / "status.json").read_text())
    assert status["state"] == "HALTED" and status["reason"].startswith("TRANSPORT_ERROR:")
    # the DISPATCH is journalled and owned; it is not delivered until the address is repaired
    assert c.planner.lineages["A"].executor == E1 and works(tmp_path) == []
    (tmp_path / "spool" / "WORK" / f"{work.seal}.to").unlink()
    st = c.tick()
    assert st["state"] == "OK" and st["recovered"] == ["REPUBLISHED:A:WORK"]
    assert works(tmp_path) == [f"{work.seal}.json"]


def test_a_transport_error_in_the_constructor_leaves_a_halted_status(tmp_path, monkeypatch):
    from project_atlas.orchestration.autonomy.dev_transport import TransportError

    store(tmp_path)
    assert coordinator(tmp_path).tick([cand("A", "src/a")])["state"] == "OK"

    def refused(self, channel):
        raise TransportError("listing refused")

    monkeypatch.setattr(SpoolTransport, "published", refused)
    with pytest.raises(TransportError, match="listing refused"):
        coordinator(tmp_path, identity="coord-2")
    status = json.loads((tmp_path / "status" / "status.json").read_text())
    assert status["state"] == "HALTED" and status["reason"] == "TRANSPORT_ERROR:listing refused"


# --- ATLAS-DEVQ-0013: the store id is the directory every store write goes into -------------


def _two_stores(tmp_path):
    a = StoreJournal.create(tmp_path / "a" / "journal", tmp_path / "a" / "anchor")
    b = StoreJournal.create(tmp_path / "b" / "journal", tmp_path / "b" / "anchor")
    return a, b


def _files(directory):
    return {
        str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob("*") if p.is_file()
    }


def test_events_and_acknowledgements_live_under_the_store_id(tmp_path):
    j = store(tmp_path)
    assert j.root == j.home / j.store_id and j.anchor == j.anchor_home / j.store_id
    assert j.root.is_dir() and j.anchor.is_dir()
    p = Planner(
        SpoolTransport(tmp_path / "spool"), identity="c", verifier_identities=(VER,), journal=j
    )
    item, fields = cand("A", "src/a")
    p.dispatch(item, **fields)
    assert sorted(f.name for f in j.home.iterdir()) == sorted([j.store_id, STORE_MARKER])
    assert sorted(f.name for f in j.anchor_home.iterdir()) == sorted([j.store_id, STORE_MARKER])
    assert sorted(f.name for f in j.root.iterdir()) == ["000000000001.json", STORE_MARKER]
    assert sorted(f.name for f in j.anchor.iterdir()) == ["000000000001.ack", STORE_MARKER]
    assert (j.anchor / STORE_MARKER).read_bytes() == (j.anchor_home / STORE_MARKER).read_bytes()
    # a store whose data directory is gone is not this store, and attach never makes one
    shutil.rmtree(j.anchor)
    with pytest.raises(JournalCorrupt, match=r"STORE_IDENTITY:anchor directory .* has no data"):
        store(tmp_path, create=False)
    with pytest.raises(JournalCorrupt, match=r"STORE_IDENTITY:anchor directory .* has no data"):
        p.dispatch(cand("B", "src/b")[0], **cand("B", "src/b")[1])
    assert not j.anchor.exists()
    with pytest.raises(JournalCorrupt, match="store id is not 32 hex"):
        StoreJournal(j.home, j.anchor_home, "../" + j.store_id)
    # neither an empty directory with the right name nor a link to another store's data is it
    j.anchor.mkdir()
    with pytest.raises(JournalCorrupt, match=r"STORE_IDENTITY:anchor directory .* no store marker"):
        store(tmp_path, create=False)
    j.anchor.rmdir()
    other = StoreJournal.create(tmp_path / "o" / "journal", tmp_path / "o" / "anchor")
    try:
        j.anchor.symlink_to(other.anchor, target_is_directory=True)
    except OSError:  # no symlinks here (Windows without the privilege)
        return
    with pytest.raises(JournalCorrupt, match=r"STORE_IDENTITY:anchor directory .* has no data"):
        store(tmp_path, create=False)
    assert sorted(f.name for f in other.anchor.iterdir()) == [STORE_MARKER]


@pytest.mark.parametrize("swap", ["marker", "directory"])
def test_a_write_after_the_identity_changed_never_lands_in_the_other_store(tmp_path, swap):
    """The window between the identity check and the write: the path decides, not the check."""
    a, b = _two_stores(tmp_path)

    class SwapsAfterCheck(StoreJournal):
        armed = False

        def check_store(self):
            super().check_store()
            if self.armed:  # the check passed; now the anchor stops being this store's
                type(self).armed = False
                if swap == "marker":
                    (self.anchor_home / STORE_MARKER).write_bytes(
                        (b.anchor_home / STORE_MARKER).read_bytes()
                    )
                else:
                    os.rename(self.anchor_home, tmp_path / "a" / "anchor.away")
                    shutil.copytree(b.anchor_home, self.anchor_home)

    j = SwapsAfterCheck(a.home, a.anchor_home, a.store_id)
    p = Planner(
        SpoolTransport(tmp_path / "spool"), identity="c", verifier_identities=(VER,), journal=j
    )
    item, fields = cand("A", "src/a")
    p.dispatch(item, **fields)
    foreign_before = _files(b.anchor_home)
    digest = (j.anchor / "000000000001.ack").read_text()
    SwapsAfterCheck.armed = True
    if swap == "marker":
        j.record_repair(1, digest, "RESTORED", "c")  # written under this store's own id
        assert (a.anchor_home / a.store_id / "000000000001.repair").is_file()
    else:
        with pytest.raises(OSError):
            j.record_repair(1, digest, "RESTORED", "c")  # no directory of this store there
    # store B's anchor is as it was, and nothing was written under B's id in A's directories
    assert _files(b.anchor_home) == foreign_before
    if swap == "directory":  # the foreign anchor now in A's place holds only what B had
        assert _files(a.anchor_home) == foreign_before
    else:
        assert not list((tmp_path / "a").rglob(f"{b.store_id}/*"))
    with pytest.raises(JournalCorrupt, match="STORE_IDENTITY"):
        j.read(0)  # and the next check stops this store


def test_a_bound_spool_is_created_once_then_attached_and_never_recreated(tmp_path):
    a, _b = _two_stores(tmp_path)
    home = tmp_path / "spool"
    with pytest.raises(TransportError, match="SPOOL_BINDING"):
        SpoolTransport.attach(home, a.store_id)  # attach creates nothing
    assert not home.exists()
    t = SpoolTransport.create(home, a.store_id)
    assert t.store_id == a.store_id and t.home == home and t.root == home / a.store_id
    assert SpoolTransport(home / "plain").store_id is None
    with pytest.raises(TransportError, match="SPOOL_EXISTS"):
        SpoolTransport.create(home, a.store_id)
    for bad in ("", "xyz", a.store_id.upper(), "../" + a.store_id, a.store_id + "0"):
        for make in (SpoolTransport.create, SpoolTransport.attach):
            with pytest.raises(TransportError, match="SPOOL_BINDING"):
                make(home, bad)
    assert sorted(p.name for p in home.iterdir()) == sorted([a.store_id, "plain"])
    with pytest.raises(TransportError, match="SPOOL_BINDING"):
        SpoolTransport.attach(home, None)  # attach never gives an unbound transport
    racers: list[object] = []

    def race():
        try:
            racers.append(SpoolTransport.create(home, "f" * 32))
        except TransportError as exc:
            racers.append(str(exc)[:12])

    threads = [threading.Thread(target=race) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sorted(str(r) for r in racers if isinstance(r, str)) == ["SPOOL_EXISTS"] * 7
    shutil.rmtree(home / ("f" * 32))
    again = SpoolTransport(home, store_id=a.store_id)  # the constructor attaches, too
    item, fields = cand("A", "src/a")
    w = Planner(again, identity="c", verifier_identities=(VER,), journal=a).dispatch(item, **fields)
    assert [r.seal for r in t.published(Channel.WORK)] == [w.seal]
    # the store's directories gone: an error on every call, never "no records", never re-made
    shutil.rmtree(home / a.store_id / "WORK")
    for call in (
        lambda: t.publish(w),
        lambda: t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL),
        lambda: t.withdraw(w),
        lambda: t.published(Channel.WORK),
        lambda: t.claimed_records(Channel.WORK, identity=IMPL),
        lambda: SpoolTransport.attach(home, a.store_id),
    ):
        with pytest.raises(TransportError, match="SPOOL_BINDING"):
            call()
    assert not (home / a.store_id / "WORK").exists()
    # a link to another store's directories is not this store's spool
    shutil.rmtree(home / a.store_id)
    SpoolTransport.create(home, _b.store_id)
    try:
        (home / a.store_id).symlink_to(home / _b.store_id, target_is_directory=True)
    except OSError:
        return
    with pytest.raises(TransportError, match="SPOOL_BINDING"):
        SpoolTransport.attach(home, a.store_id)


def test_two_stores_given_one_spool_directory_never_touch_each_others_records(tmp_path):
    a, b = _two_stores(tmp_path)
    home = tmp_path / "spool"
    ta, tb = SpoolTransport.create(home, a.store_id), SpoolTransport.create(home, b.store_id)
    item, fields = cand("A", "src/a")
    w = Planner(ta, identity="c", verifier_identities=(VER,), journal=a).dispatch(item, **fields)
    assert tb.published(Channel.WORK) == []
    assert tb.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL) is None
    assert tb.claimed_records(Channel.WORK, identity=IMPL) == []
    assert tb.withdraw(w) is True  # store B takes back "its" copy: there is none
    assert tb.publish(w) is False
    got = ta.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL)
    assert got is not None and got.seal == w.seal  # store A's record is untouched by all that
    # all store B wrote is its own tombstone, under its own id
    assert [p.parent.name for p in (home / b.store_id).rglob(f"{w.seal}.json")] == ["withdrawn"]
    # each coordinator sees only its own store's work: no JOURNAL_BEHIND_TRANSPORT
    common = dict(verifier_identities=(VER,), accept_same_filesystem=True)
    ca = Coordinator(a, ta, identity="ca", status_path=tmp_path / "sa" / "s.json", **common)
    cb = Coordinator(b, tb, identity="cb", status_path=tmp_path / "sb" / "s.json", **common)
    assert cb.tick([cand("B", "src/a")])["admitted"] == ["B"]  # same paths, another store
    sa, sb = ca.tick([]), cb.tick([])
    assert (sa["state"], sb["state"]) == ("OK", "OK")
    assert sa["transport_store"] == a.store_id and sb["transport_store"] == b.store_id
    # contrast: two stores on one UNBOUND spool; the second coordinator stops on the first's work
    plain = SpoolTransport(tmp_path / "plain")
    kw = common | {"accept_unbound_transport": True}
    a2, b2 = (
        StoreJournal.create(tmp_path / n / "journal", tmp_path / n / "anchor") for n in ("c", "d")
    )
    c1 = Coordinator(a2, plain, identity="c1", status_path=tmp_path / "s1" / "s.json", **kw)
    c2 = Coordinator(b2, plain, identity="c2", status_path=tmp_path / "s2" / "s.json", **kw)
    assert c1.tick([cand("A", "src/a")])["transport_store"] == "UNBOUND"
    with pytest.raises(JournalCorrupt, match="JOURNAL_BEHIND_TRANSPORT"):
        c2.tick([])


def test_a_coordinator_publishes_only_into_a_transport_bound_to_its_store(tmp_path):
    a, b = _two_stores(tmp_path)
    home = tmp_path / "spool"
    ta, tb = SpoolTransport.create(home, a.store_id), SpoolTransport.create(home, b.store_id)
    kw = dict(
        identity="c",
        verifier_identities=(VER,),
        status_path=tmp_path / "status" / "s.json",
        accept_same_filesystem=True,
    )
    for flag in (False, True):  # another store's transport is refused whatever is accepted
        with pytest.raises(PlannerError, match="STORE_BINDING"):
            Coordinator(a, tb, accept_unbound_transport=flag, **kw)
    with pytest.raises(PlannerError, match="STORE_BINDING"):
        Coordinator(a, SpoolTransport(tmp_path / "plain"), **kw)  # unbound: explicit only
    assert not (tmp_path / "status").exists() and tb.published(Channel.WORK) == []
    with pytest.raises(PlannerError, match="STATUS_PATH"):
        Coordinator(a, ta, **(kw | {"status_path": home / "s.json"}))  # inside the spool home
    c = Coordinator(a, ta, **kw)
    assert c.tick([cand("A", "src/a")])["admitted"] == ["A"]
    # the spool of this store goes missing under the running coordinator: HALTED, not "empty"
    shutil.rmtree(home / a.store_id)
    with pytest.raises(TransportError, match="SPOOL_BINDING"):
        c.tick([cand("B", "src/b")])
    status = json.loads((tmp_path / "status" / "s.json").read_text())
    assert status["state"] == "HALTED" and status["reason"].startswith(
        "TRANSPORT_ERROR:SPOOL_BINDING"
    )
    assert not (home / a.store_id).exists()
    assert sorted(f.name for f in a.root.iterdir()) == ["000000000001.json", STORE_MARKER]


def test_a_spool_missing_when_pump_claims_halts_the_tick_and_is_not_quarantined(tmp_path):
    """The transport being gone is not a bad record: pump raises, the status is HALTED.

    (Directories that go missing inside the tick's last transport call are a known limit.)
    """
    a, _b = _two_stores(tmp_path)
    home = tmp_path / "spool"

    class Vanishes(SpoolTransport):
        armed = False

        def claim(self, channel, *, role, identity):
            if self.armed:  # after this tick's continuity check and recovery
                shutil.rmtree(self.root, ignore_errors=True)
            return super().claim(channel, role=role, identity=identity)

    SpoolTransport.create(home, a.store_id)
    t = Vanishes(home, store_id=a.store_id)
    c = Coordinator(
        a,
        t,
        identity="c",
        verifier_identities=(VER,),
        status_path=tmp_path / "status" / "s.json",
        accept_same_filesystem=True,
    )
    assert c.tick([cand("A", "src/a")])["state"] == "OK"
    t.armed = True
    with pytest.raises(TransportUnavailable, match="SPOOL_BINDING"):
        c.tick([])  # a tick that dispatches nothing
    status = json.loads((tmp_path / "status" / "s.json").read_text())
    assert status["state"] == "HALTED" and "SPOOL_BINDING" in status["reason"]
    assert c.planner.quarantined == [] and not home.joinpath(a.store_id).exists()
    # the planner alone does not swallow it either
    with pytest.raises(TransportUnavailable):
        c.planner.pump()


# --- ATLAS-DEVQ-0014: operator-declared activation ------------------------------------------


def _declared(tmp_path, **kw):
    """A created store with a bound spool and a declared activation; returns (journal, record)."""
    j = store(tmp_path)
    SpoolTransport.create(tmp_path / "spool", j.store_id)
    record = tmp_path / "ops" / "activation.json"
    kw.setdefault("accept_same_filesystem", True)
    Activation.declare(
        record,
        store_id=j.store_id,
        journal=j.home,
        anchor=j.anchor_home,
        spool=tmp_path / "spool",
        **kw,
    )
    return j, record


def _open(tmp_path, record, identity="coord-1", **kw):
    return Activation.open(
        record,
        identity=identity,
        verifier_identities=(VER,),
        status_path=tmp_path / "status" / "status.json",
        **kw,
    )


def _status(tmp_path):
    return json.loads((tmp_path / "status" / "status.json").read_text())


def test_an_activation_is_declared_once_and_opened_without_creating_anything(tmp_path):
    j, record = _declared(tmp_path)
    raw = json.loads(record.read_text())
    assert raw == {
        "v": 1,
        "store": j.store_id,
        "journal": str(j.home.resolve()),
        "anchor": str(j.anchor_home.resolve()),
        "spool": str((tmp_path / "spool").resolve()),
        "accept_same_filesystem": True,
        "seq": 0,
        "head": "",
    }
    before = tree(tmp_path)
    a = _open(tmp_path, record)
    assert tree(tmp_path) == before  # opening wrote nothing into store or spool
    assert a.coordinator.journal.store_id == j.store_id
    assert a.coordinator.planner.transport.store_id == j.store_id
    st = a.tick([cand("A", "src/a")])
    assert st["state"] == "OK" and st["admitted"] == ["A"] and st["transport_store"] == j.store_id
    # the floor is the head the tick saw: sequence number and digest of the stored event
    raw = json.loads(record.read_text())
    event = (j.root / "000000000001.json").read_bytes()
    assert (raw["seq"], raw["head"]) == (1, hashlib.sha256(event).hexdigest()) == a.floor
    assert a.tick([])["state"] == "OK" and json.loads(record.read_text()) == raw
    # declared once: never overwritten, whatever is declared the second time
    with pytest.raises(PlannerError, match=r"ACTIVATION_RECORD:.* exists"):
        Activation.declare(
            record,
            store_id=j.store_id,
            journal=j.home,
            anchor=j.anchor_home,
            spool=tmp_path / "spool",
            accept_same_filesystem=True,
        )
    assert json.loads(record.read_text()) == raw


def test_nothing_is_activated_without_a_valid_record(tmp_path):
    _j, record = _declared(tmp_path)
    good = record.read_text()
    before = tree(tmp_path)
    with pytest.raises(PlannerError, match=r"ACTIVATION_RECORD:.* does not exist"):
        _open(tmp_path, tmp_path / "ops" / "nothing.json")
    raw = json.loads(good)
    bads = [
        "{",
        "[]",
        json.dumps(raw | {"v": 2}),
        json.dumps(raw | {"v": 1.0}),
        json.dumps(raw | {"extra": 1}),
        json.dumps({k: v for k, v in raw.items() if k != "spool"}),
        json.dumps(raw | {"store": "xyz"}),
        json.dumps(raw | {"journal": "relative/journal"}),
        json.dumps(raw | {"accept_same_filesystem": 1}),
        json.dumps(raw | {"seq": -1}),
        json.dumps(raw | {"seq": 1}),  # a floor without its digest
        json.dumps(raw | {"head": "0" * 64}),  # a digest without a floor
        json.dumps(raw | {"seq": True}),
    ]
    for bad in bads:
        record.write_text(bad)
        with pytest.raises(PlannerError, match="ACTIVATION_RECORD"):
            _open(tmp_path, record)
    record.write_text(good)
    # a configuration refusal: nothing written anywhere, no status, no fallback of any kind
    assert tree(tmp_path) == before and not (tmp_path / "status").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ops", "spool", "state", "witness"]
    with pytest.raises(PlannerError, match="STATUS_PATH"):
        Activation.open(record, identity="c", verifier_identities=(VER,), status_path=record)
    assert record.read_text() == good


def test_declare_needs_the_existing_store_and_spool_it_names(tmp_path):
    j = store(tmp_path)
    record = tmp_path / "ops" / "activation.json"
    kw = dict(store_id=j.store_id, journal=j.home, anchor=j.anchor_home, spool=tmp_path / "spool")
    with pytest.raises(TransportError, match="SPOOL_BINDING"):
        Activation.declare(record, accept_same_filesystem=True, **kw)  # no spool yet
    assert not (tmp_path / "spool").exists() and not record.exists()
    SpoolTransport.create(tmp_path / "spool", j.store_id)
    before = tree(tmp_path)
    other = StoreJournal.create(tmp_path / "o" / "journal", tmp_path / "o" / "anchor")
    refusals = [
        (PlannerError, "STORE_BOUNDARY", kw),  # the reduced boundary is declared, not assumed
        (JournalCorrupt, "STORE_IDENTITY", kw | {"store_id": other.store_id}),
        (JournalCorrupt, "STORE_IDENTITY", kw | {"anchor": other.anchor_home}),
        (JournalCorrupt, "STORE_IDENTITY", kw | {"journal": tmp_path / "nowhere"}),
        (TransportError, "SPOOL_BINDING", kw | {"spool": tmp_path / "elsewhere"}),
        (PlannerError, "ACTIVATION_RECORD", kw | {"store_id": "XYZ"}),
    ]
    for error, code, args in refusals:
        with pytest.raises(error, match=code):
            Activation.declare(record, accept_same_filesystem=code != "STORE_BOUNDARY", **args)
    for inside in (j.home / "a.json", j.anchor / "a.json", tmp_path / "spool" / "a.json"):
        with pytest.raises(PlannerError, match=r"ACTIVATION_RECORD:.* is inside"):
            Activation.declare(inside, accept_same_filesystem=True, **kw)
    assert tree(tmp_path) == before and not record.exists()
    assert not (tmp_path / "nowhere").exists() and not (tmp_path / "elsewhere").exists()


def test_a_store_taken_back_as_a_whole_is_refused_by_its_activation(tmp_path):
    """Journal, anchor and spool restored together: the store itself looks consistent."""
    j, record = _declared(tmp_path)
    a = _open(tmp_path, record)
    a.tick([cand("A", "src/a")])
    for top in ("state", "witness", "spool"):  # one snapshot of everything, at event 1
        shutil.copytree(tmp_path / top, tmp_path / "snapshot" / top)
    a.tick([cand("B", "src/b")])
    assert json.loads(record.read_text())["seq"] == 2
    assert len(list((tmp_path / "spool" / j.store_id / "WORK").glob("*.json"))) == 2

    def restore():
        for top in ("state", "witness", "spool"):
            shutil.rmtree(tmp_path / top)
            shutil.copytree(tmp_path / "snapshot" / top, tmp_path / top)

    restore()
    # the coordinator alone cannot see it: the anchor and the spool went back with the journal
    plain = Coordinator(
        StoreJournal.attach(j.home, j.anchor_home),
        SpoolTransport.attach(tmp_path / "spool", j.store_id),
        identity="plain",
        verifier_identities=(VER,),
        status_path=tmp_path / "status" / "status.json",
        accept_same_filesystem=True,
    )
    assert plain.tick([])["state"] == "OK"
    restore()
    before = tree(tmp_path)
    # the activation does, when it is opened ...
    with pytest.raises(JournalCorrupt, match=r"ACTIVATION_ROLLED_BACK:.* event 2"):
        _open(tmp_path, record, identity="coord-2")
    st = _status(tmp_path)
    assert st["state"] == "HALTED" and st["reason"].startswith("ACTIVATION_ROLLED_BACK")
    assert st["store"] == j.store_id and st["activation"] == str(record)
    # ... and when it is already running; nothing is admitted over the lost lineage B
    (tmp_path / "status" / "status.json").write_text(json.dumps({"state": "OK"}))
    with pytest.raises(JournalCorrupt, match="ACTIVATION_ROLLED_BACK"):
        a.tick([cand("B2", "src/b")])
    assert _status(tmp_path)["state"] == "HALTED"
    assert tree(tmp_path) == before and json.loads(record.read_text())["seq"] == 2
    # a store that went on from the snapshot with other events holds another event 2
    plain2 = Planner(
        SpoolTransport.attach(tmp_path / "spool", j.store_id),
        identity="p",
        verifier_identities=(VER,),
        journal=StoreJournal.attach(j.home, j.anchor_home),
    )
    plain2.dispatch(cand("C", "src/c")[0], **cand("C", "src/c")[1])
    with pytest.raises(JournalCorrupt, match="ACTIVATION_ROLLED_BACK"):
        _open(tmp_path, record, identity="coord-3")


def test_an_activation_halts_on_a_missing_or_foreign_store_or_spool(tmp_path):
    j, record = _declared(tmp_path)
    _open(tmp_path, record).tick([cand("A", "src/a")])
    other = StoreJournal.create(tmp_path / "o" / "journal", tmp_path / "o" / "anchor")
    damages = [
        ("spool", lambda: shutil.rmtree(tmp_path / "spool" / j.store_id), TransportError),
        ("anchor", lambda: shutil.rmtree(j.anchor_home), JournalCorrupt),
        (
            "foreign",
            lambda: (j.anchor_home / STORE_MARKER).write_bytes(
                (other.anchor_home / STORE_MARKER).read_bytes()
            ),
            JournalCorrupt,
        ),
    ]
    for top in ("state", "witness", "spool"):
        shutil.copytree(tmp_path / top, tmp_path / "good" / top)
    for name, damage, error in damages:
        for top in ("state", "witness", "spool"):
            shutil.rmtree(tmp_path / top)
            shutil.copytree(tmp_path / "good" / top, tmp_path / top)
        (tmp_path / "status" / "status.json").write_text(json.dumps({"state": "OK"}))
        damage()
        before = tree(tmp_path)
        with pytest.raises(error):
            _open(tmp_path, record, identity=f"coord-{name}")
        st = _status(tmp_path)
        assert st["state"] == "HALTED" and st["store"] == j.store_id, name
        assert tree(tmp_path) == before, name  # nothing re-created, nothing appended
    assert st["reason"].startswith("STORE_IDENTITY")


def test_a_running_activation_follows_its_record(tmp_path):
    _j, record = _declared(tmp_path)
    a1 = _open(tmp_path, record, max_live=5)
    a2 = _open(tmp_path, record, identity="coord-2", max_live=5)
    a1.tick([cand("A", "src/a")])
    assert a2.floor == (0, "")  # as opened; the record has moved on
    assert a2.tick([cand("B", "src/b")])["admitted"] == ["B"]
    assert json.loads(record.read_text())["seq"] == 2 == a2.floor[0]
    assert a1.tick([])["state"] == "OK"  # a floor another coordinator raised is still held
    # the record is taken away or re-pointed under a running activation: HALTED, not OK
    good = record.read_text()
    raw = json.loads(good)
    for changed in (None, json.dumps(raw | {"spool": str(tmp_path / "elsewhere")}), "{"):
        if changed is None:
            record.unlink()
        else:
            record.write_text(changed)
        with pytest.raises(JournalCorrupt, match=r"ACTIVATION_RECORD|ACTIVATION_CHANGED"):
            a1.tick([cand("C", "src/c")])
        assert _status(tmp_path)["state"] == "HALTED"
        record.write_text(good)
    assert a1.tick([cand("C", "src/c")])["admitted"] == ["C"]
    assert json.loads(record.read_text())["seq"] == 3
    assert not (tmp_path / "elsewhere").exists()


def test_a_halted_activation_never_writes_its_status_into_the_store(tmp_path):
    """ATLAS-DEVQ-0014: the status path is checked before the refusal can be reported."""
    j, record = _declared(tmp_path)
    a = _open(tmp_path, record)
    a.tick([cand("A", "src/a")])
    shutil.rmtree(tmp_path / "spool" / j.store_id)  # the store is now refused at open
    before = tree(tmp_path)
    inside = (
        j.root / "000000000002.json",
        j.root / "000000000001.json",
        j.anchor_home / STORE_MARKER,
        tmp_path / "spool" / "newdir" / "status.json",
    )
    for path in inside:
        with pytest.raises(PlannerError, match=r"STATUS_PATH:.* is inside"):
            Activation.open(record, identity="x", verifier_identities=(VER,), status_path=path)
    assert tree(tmp_path) == before and not (tmp_path / "spool" / "newdir").exists()
    with pytest.raises(TransportError, match="SPOOL_BINDING"):
        _open(tmp_path, record, identity="x")  # outside: refused and reported
    assert _status(tmp_path)["state"] == "HALTED" and tree(tmp_path) == before


def test_an_unreadable_floor_event_or_marker_halts_instead_of_escaping(tmp_path):
    j, record = _declared(tmp_path)
    a = _open(tmp_path, record)
    a.tick([cand("A", "src/a")])
    status = tmp_path / "status" / "status.json"
    event = j.root / "000000000001.json"
    kept = event.read_bytes()
    event.unlink()
    event.mkdir()  # not a file: reading it is an OSError, not a missing event
    status.write_text(json.dumps({"state": "OK"}))
    with pytest.raises(JournalCorrupt, match=r"IO_ERROR|ACTIVATION_ROLLED_BACK"):
        a.tick([cand("B", "src/b")])
    assert _status(tmp_path)["state"] == "HALTED"
    event.rmdir()
    event.write_bytes(kept)
    marker = j.home / STORE_MARKER
    good = marker.read_bytes()
    marker.write_text("[" * 200_000)  # json raises RecursionError on this one
    status.write_text(json.dumps({"state": "OK"}))
    with pytest.raises(JournalCorrupt, match=r"STORE_IDENTITY:journal marker .* is unusable"):
        _open(tmp_path, record, identity="coord-2")
    assert _status(tmp_path)["state"] == "HALTED"
    status.write_text(json.dumps({"state": "OK"}))
    with pytest.raises(JournalCorrupt, match=r"STORE_IDENTITY:journal marker .* is unusable"):
        a.tick([cand("B", "src/b")])  # and under the running activation
    assert _status(tmp_path)["state"] == "HALTED"
    marker.write_bytes(good)
    assert a.tick([])["state"] == "OK"
    with pytest.raises(PlannerError, match="STATUS_PATH:not a usable path"):
        Activation.open(
            record, identity="x", verifier_identities=(VER,), status_path="/a\x00b/status.json"
        )
    # a record whose paths cannot be paths is a bad record, not a crash
    raw = json.loads(record.read_text())
    record.write_text(json.dumps(raw | {"journal": "/a\u0000b"}))
    with pytest.raises(PlannerError, match="ACTIVATION_RECORD"):
        _open(tmp_path, record, identity="coord-3")


def test_a_record_repointed_during_a_tick_is_not_given_this_stores_floor(tmp_path):
    _j, record = _declared(tmp_path)
    a = _open(tmp_path, record)
    other = StoreJournal.create(tmp_path / "o" / "journal", tmp_path / "o" / "anchor")
    SpoolTransport.create(tmp_path / "o" / "spool", other.store_id)
    raw = json.loads(record.read_text())
    moved = raw | {
        "store": other.store_id,
        "journal": str(other.home.resolve()),
        "anchor": str(other.anchor_home.resolve()),
        "spool": str((tmp_path / "o" / "spool").resolve()),
    }
    real = a.coordinator.tick

    def tick_then_repoint(candidates=()):
        out = real(candidates)
        record.write_text(json.dumps(moved))  # between the tick and the floor update
        return out

    a.coordinator.tick = tick_then_repoint
    with pytest.raises(JournalCorrupt, match="ACTIVATION_RECORD:the floor could not be written"):
        a.tick([cand("A", "src/a")])
    assert json.loads(record.read_text()) == moved  # still seq 0: nothing of this store in it
    assert _status(tmp_path)["state"] == "HALTED"
