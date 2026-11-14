"""Recovery / duplicate semantics, adapter part: a lost ledger is not a licence to dispatch.

Measured before this change: dispatch-once rested on ONE record, the DISPATCH line of the
adapter's crosswalk ledger. With the ledger (and the adapter's pending directory) lost and the
spool intact, a restarted adapter found its claimed work record, could not tell it from a
record claimed just before a crash (``_readopt_claimed``), adopted it and sent the workflow
dispatch a second time: a second executor attempt for one execution identity.

Now a dispatch starts by taking a dispatch mark for the work record in the transport
(exclusive creation), before the ledger record and before anything is sent. An adapter that
does not take the mark refuses the work item: a run may exist that it cannot correlate. The
same gate stops a second adapter instance on one spool.

What this does NOT cover is asserted at the end of the file rather than left unsaid: with the
ledger AND the transport's copy of the record lost, a record the planner publishes again is
dispatched again.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import project_atlas.orchestration.autonomy.dev_spool_transport as spool_mod
from project_atlas.orchestration.autonomy.dev_contracts import Role
from project_atlas.orchestration.autonomy.dev_crosswalk import Crosswalk
from project_atlas.orchestration.autonomy.dev_fabric_adapter import (
    DispatchRefused,
    FabricAdapter,
)
from project_atlas.orchestration.autonomy.dev_planner import Planner
from project_atlas.orchestration.autonomy.dev_queue import Category, QueueItem
from project_atlas.orchestration.autonomy.dev_spool_transport import SpoolTransport
from project_atlas.orchestration.autonomy.dev_transport import Channel, InMemoryTransport

BASE = "a" * 40
FIELDS = dict(
    repository="WezzSide/project-atlas",
    base_revision=BASE,
    authority_ref="AUTH-1",
    allowed_paths=("src/x/", "tests/unit/"),
    forbidden_paths=("infra/atlas-runner/controller",),
    acceptance_contract=("task tests pass",),
)


class Port:
    """Only what a dispatch touches; every send is counted."""

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    def dispatch_workflow(self, workflow, ref, inputs):
        self.sent.append(inputs)

    def branch_head(self, branch):
        return BASE if branch == "main" else None

    def list_runs(self, workflow, *, event, created_after):
        return []


def statement(_work):
    return ("Fix the thing.", ("python -m pytest tests/unit/test_a.py -q",))


def adapter(tmp_path: Path, port: Port, transport=None) -> FabricAdapter:
    return FabricAdapter(
        port,
        transport if transport is not None else SpoolTransport(tmp_path / "spool"),
        Crosswalk(tmp_path / "xw.jsonl"),
        pending_dir=tmp_path / "pending",
        clock=lambda: "2026-09-30T15:59:00Z",
        task_statement=statement,
        required_checks=frozenset({"quality"}),
    )


def planner(tmp_path: Path) -> Planner:
    return Planner(
        SpoolTransport(tmp_path / "spool"),
        identity="planner",
        verifier_identities=("github-hosted:atlas-runner-verify",),
    )


def dispatched_once(tmp_path: Path) -> tuple[Port, Planner, str]:
    port = Port()
    p = planner(tmp_path)
    work = p.dispatch(
        QueueItem(task_id="DEVQ-1", title="t", category=Category.RELIABILITY, severity=2),
        **FIELDS,
    )
    events = adapter(tmp_path, port).tick()
    assert "DISPATCHED:DEVQ-1" in events and len(port.sent) == 1
    return port, p, work.seal


def lose_the_ledger(tmp_path: Path) -> None:
    (tmp_path / "xw.jsonl").unlink()
    shutil.rmtree(tmp_path / "pending")


def test_a_restart_with_the_ledger_intact_does_not_dispatch_again(tmp_path):
    """Control: the existing guarantee, unchanged."""
    port, p, _ = dispatched_once(tmp_path)
    for _ in range(3):
        p.recover()
        adapter(tmp_path, port).tick()
    assert len(port.sent) == 1


def test_a_lost_ledger_does_not_become_a_second_dispatch(tmp_path):
    port, p, seal = dispatched_once(tmp_path)
    lose_the_ledger(tmp_path)
    events: list[str] = []
    for _ in range(3):  # restart after restart, with the planner recovering in between
        p.recover()
        events += adapter(tmp_path, port).tick()
    assert len(port.sent) == 1, "one execution identity, one workflow dispatch"
    refused = [e for e in events if e.startswith("EXECUTION_FAILED:DEVQ-1:")]
    assert len(refused) == 1 and "DISPATCH_ALREADY_MARKED" in refused[0]  # said once, not silent
    assert not [e for e in events if e.startswith("DISPATCHED:")]
    # the refused work is not picked up again by a later restart, and nothing is in flight
    last = adapter(tmp_path, port)
    assert last.tick() == [] and last.works == {}
    assert Crosswalk(tmp_path / "xw.jsonl").hop(seal, "DISPATCH") is None  # no invented history


def test_a_direct_dispatch_of_a_marked_work_item_is_refused_with_no_dispatch_recorded(tmp_path):
    port, p, seal = dispatched_once(tmp_path)
    work = p.lineages["DEVQ-1"].work
    lose_the_ledger(tmp_path)
    a = adapter(tmp_path, port)
    with pytest.raises(DispatchRefused, match="DISPATCH_ALREADY_MARKED"):
        a.dispatch(work)
    assert len(port.sent) == 1
    assert a.xw.hop(seal, "DISPATCH") is None


def one_work(tmp_path: Path) -> Planner:
    p = planner(tmp_path)
    p.dispatch(
        QueueItem(task_id="DEVQ-1", title="t", category=Category.RELIABILITY, severity=2),
        **FIELDS,
    )
    return p


def test_a_marked_work_item_is_refused_before_the_port_is_asked_anything(tmp_path):
    """The early look: no network call, and no waiting behind another unbound dispatch."""
    port, p, _seal = dispatched_once(tmp_path)
    work = p.lineages["DEVQ-1"].work
    lose_the_ledger(tmp_path)

    class Untouchable(Port):
        def branch_head(self, branch):
            raise AssertionError("the port was asked about a work item that is refused")

    quiet = Untouchable()
    with pytest.raises(DispatchRefused, match="DISPATCH_ALREADY_MARKED"):
        adapter(tmp_path, quiet).dispatch(work)
    assert quiet.sent == [] and len(port.sent) == 1


def test_the_mark_is_taken_before_the_ledger_and_the_send(tmp_path, monkeypatch):
    order: list[str] = []
    port = Port()
    one_work(tmp_path)
    a = adapter(tmp_path, port)
    real_bind, real_mark, real_send = a.xw.bind_dispatch, a.transport.mark_dispatched, port.sent

    def bind(*args, **kw):
        order.append("ledger")
        return real_bind(*args, **kw)

    def mark(*args, **kw):
        order.append("mark")
        return real_mark(*args, **kw)

    def send(workflow, ref, inputs):
        order.append("send")
        real_send.append(inputs)

    monkeypatch.setattr(a.xw, "bind_dispatch", bind)
    monkeypatch.setattr(a.transport, "mark_dispatched", mark)
    monkeypatch.setattr(port, "dispatch_workflow", send)
    a.tick()
    assert order == ["mark", "ledger", "send"]


def test_two_adapters_on_one_spool_send_once(tmp_path):
    """The hole a review found in a first version: the mark's result was not looked at.

    B has its own (empty) ledger and re-adopts the record A claimed. A dispatches inside B's
    window, after B looked for a mark and before B takes it. Only the exclusive creation can
    decide that; B must lose, record no DISPATCH and send nothing.
    """
    port = Port()
    one_work(tmp_path)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    spool = tmp_path / "spool"
    a = adapter(tmp_path / "a", port, SpoolTransport(spool))
    assert a.accept_work() is not None  # A claimed and persisted it, not dispatched yet
    events: dict[str, list[str]] = {}

    class WindowPort(Port):
        def __init__(self) -> None:
            super().__init__()
            self.sent = port.sent  # one counter for both adapters
            self.fired = False

        def branch_head(self, branch):
            if not self.fired:  # B has looked for the mark and has not taken it yet
                self.fired = True
                events["a"] = a.tick()
            return super().branch_head(branch)

    b = adapter(tmp_path / "b", WindowPort(), SpoolTransport(spool))
    events["b"] = b.tick()
    assert "WORK_READOPTED:DEVQ-1" in events["b"]  # B really was in the race
    assert events["a"] == ["DISPATCHED:DEVQ-1"]
    assert len(port.sent) == 1, "one seal, one send, whatever ledger each adapter holds"
    refused = [e for e in events["b"] if e.startswith("EXECUTION_FAILED:DEVQ-1:")]
    assert len(refused) == 1 and "DISPATCH_ALREADY_MARKED" in refused[0]
    seal = next(iter(a.works))
    assert b.xw.hop(seal, "DISPATCH") is None  # the loser left no DISPATCH behind
    assert b.xw.unbound_dispatches() == []  # so it holds up no other dispatch


def test_a_mark_that_cannot_be_written_sends_nothing_and_is_tried_again(tmp_path, monkeypatch):
    """Nothing is recorded or sent without the mark, and nothing is stranded either."""
    port = Port()
    one_work(tmp_path)
    a = adapter(tmp_path, port)

    def failing(*_a, **_k):
        raise OSError(28, "no space left on device")

    monkeypatch.setattr(a.transport, "mark_dispatched", failing)
    events = a.tick()
    assert port.sent == []
    assert any(e.startswith("TRANSIENT:DEVQ-1:") for e in events)
    seal = next(iter(a.works))  # the work is kept
    assert a.xw.hop(seal, "DISPATCH") is None and a.xw.unbound_dispatches() == []
    monkeypatch.undo()
    assert "DISPATCHED:DEVQ-1" in a.tick() and len(port.sent) == 1


def test_a_mark_reported_as_failed_that_was_written_is_not_dispatched_over(tmp_path, monkeypatch):
    """The other half of "may or may not exist": the retry meets the mark and refuses."""
    port = Port()
    one_work(tmp_path)
    a = adapter(tmp_path, port)
    real = a.transport.mark_dispatched

    def written_then_failing(*args, **kw):
        assert real(*args, **kw) is True
        raise OSError(5, "input/output error")

    monkeypatch.setattr(a.transport, "mark_dispatched", written_then_failing)
    assert any(e.startswith("TRANSIENT:DEVQ-1:") for e in a.tick())
    monkeypatch.undo()
    events = a.tick()
    assert port.sent == []
    assert any("DISPATCH_ALREADY_MARKED" in e for e in events)


def test_a_crash_between_mark_and_ledger_is_refused_not_sent(tmp_path, monkeypatch):
    """The price of taking the mark first, stated: nothing was sent, and it is not retried."""
    port = Port()
    one_work(tmp_path)
    a = adapter(tmp_path, port)

    class Died(BaseException):
        pass

    def die(*_a, **_k):
        raise Died

    monkeypatch.setattr(a.xw, "bind_dispatch", die)
    with pytest.raises(Died):
        a.dispatch(a.accept_work())
    monkeypatch.undo()
    events = adapter(tmp_path, port).tick()
    assert port.sent == []
    assert any("DISPATCH_ALREADY_MARKED" in e for e in events)


def test_a_restart_between_ledger_and_send_is_not_sent_later(tmp_path, monkeypatch):
    """Unchanged behaviour: the ledger DISPATCH stands, so the send is never made up for."""
    port = Port()
    one_work(tmp_path)
    a = adapter(tmp_path, port)

    class Died(BaseException):
        pass

    def die(*_a, **_k):
        raise Died

    monkeypatch.setattr(port, "dispatch_workflow", die)
    with pytest.raises(Died):
        a.dispatch(a.accept_work())
    monkeypatch.undo()
    adapter(tmp_path, port).tick()
    assert port.sent == []


def test_package_mode_takes_the_same_mark_and_refuses_the_same_way(tmp_path):
    """``dispatch_package`` is its own code path: the gate must be on it too."""
    import json

    from project_atlas.orchestration.autonomy import dev_package

    spec = dev_package.load_spec(
        json.dumps(
            {
                "task_id": "DEVQ-BIND-1",
                "execution_ordinal": 1,
                "lineage_root": "DEVQ-BIND-1",
                "repository": "WezzSide/project-atlas",
                "base_revision": BASE,
                "authority_ref": "AUTH-1",
                "allowed_paths": ["src/x/", "tests/unit/"],
                "forbidden_paths": list(dev_package.FORBIDDEN_FLOOR),
                "expected_outputs": ["dedicated atlas/agent-* branch with exact HEAD/TREE"],
                "acceptance_contract": ["example behaviour fixed"],
                "statement": "Fix the synthetic example defect in src/x/a.py.",
                "acceptance_commands": ["pytest tests/unit/test_a.py -q"],
                "attempt": 1,
                "max_attempts": 3,
                "attempt_kind": "implementation",
            }
        )
    )
    rendered = dev_package.render_package(dev_package.build_package(spec))
    sha = dev_package.package_sha256(rendered)
    work = dev_package.build_work(spec)
    port = Port()

    def pkg_adapter() -> FabricAdapter:
        return FabricAdapter(
            port,
            SpoolTransport(tmp_path / "spool"),
            Crosswalk(tmp_path / "xw.jsonl"),
            pending_dir=tmp_path / "pending",
            clock=lambda: "2026-09-30T15:59:00Z",
            package_source=lambda _w: (rendered, sha),
            required_checks=frozenset({"quality"}),
        )

    first = pkg_adapter()
    first.transport.publish(work)
    assert first.tick() == ["ACCEPTED:DEVQ-BIND-1", "DISPATCHED:DEVQ-BIND-1"]
    assert len(port.sent) == 1
    assert SpoolTransport(tmp_path / "spool").dispatch_marked(work) is True  # mark was taken
    lose_the_ledger(tmp_path)
    events = pkg_adapter().tick()
    assert len(port.sent) == 1
    assert any("DISPATCH_ALREADY_MARKED" in e for e in events)
    with pytest.raises(DispatchRefused, match="DISPATCH_ALREADY_MARKED"):
        pkg_adapter().dispatch_package(work, rendered, expected_package_sha256=sha)
    assert len(port.sent) == 1


def test_the_mark_works_on_a_spool_bound_to_a_store(tmp_path):
    store = "0123456789abcdef0123456789abcdef"
    bound = SpoolTransport.create(tmp_path / "home", store)
    port = Port()
    p = Planner(
        bound, identity="planner", verifier_identities=("github-hosted:atlas-runner-verify",)
    )
    work = p.dispatch(
        QueueItem(task_id="DEVQ-1", title="t", category=Category.RELIABILITY, severity=2),
        **FIELDS,
    )
    a = adapter(tmp_path, port, SpoolTransport.attach(tmp_path / "home", store))
    assert "DISPATCHED:DEVQ-1" in a.tick()
    mark = tmp_path / "home" / store / "WORK" / "claimed" / f"{work.seal}.dispatched"
    assert mark.is_file()
    lose_the_ledger(tmp_path)
    adapter(tmp_path, port, SpoolTransport.attach(tmp_path / "home", store)).tick()
    assert len(port.sent) == 1


def test_of_two_racing_marks_exactly_one_is_taken(tmp_path, monkeypatch):
    """Exclusive creation, not the look before it: the rival arrives after the look."""
    one_work(tmp_path)
    spool = SpoolTransport(tmp_path / "spool")
    rec = spool.claim(Channel.WORK, role=Role.IMPLEMENTER, identity="impl-a")
    real = spool_mod.os.link
    rival: list[bool] = []

    def rival_first(src, dst, *args, **kw):
        if str(dst).endswith(".dispatched") and not rival:
            monkeypatch.setattr(spool_mod.os, "link", real)
            rival.append(SpoolTransport(tmp_path / "spool").mark_dispatched(rec, identity="B"))
        return real(src, dst, *args, **kw)

    monkeypatch.setattr(spool_mod.os, "link", rival_first)
    assert spool.mark_dispatched(rec, identity="A") is False
    assert rival == [True]
    mark = tmp_path / "spool" / "WORK" / "claimed" / f"{rec.seal}.dispatched"
    assert '"identity": "B"' in mark.read_text(encoding="utf-8")  # the first stands, unchanged
    assert not list(mark.parent.glob(".tmp-*"))


def test_stress_racing_marks_have_one_winner(tmp_path):
    import threading

    one_work(tmp_path)
    rec = SpoolTransport(tmp_path / "spool").claim(
        Channel.WORK, role=Role.IMPLEMENTER, identity="impl-a"
    )
    for n in range(30):
        mark = tmp_path / "spool" / "WORK" / "claimed" / f"{rec.seal}.dispatched"
        mark.unlink(missing_ok=True)
        start = threading.Barrier(8)
        won: list[bool] = []

        def take(i, start=start, won=won):
            start.wait()
            won.append(SpoolTransport(tmp_path / "spool").mark_dispatched(rec, identity=f"i{i}"))

        threads = [threading.Thread(target=take, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(won) == [False] * 7 + [True], f"round {n}: {won}"


def test_the_mark_is_exclusive_durable_and_invisible_to_the_record_listings(tmp_path):
    _port, _p, seal = dispatched_once(tmp_path)
    spool = SpoolTransport(tmp_path / "spool")
    rec = spool.claimed_records(Channel.WORK, identity="fabric:vps-02-executor")
    assert [r.seal for r in rec] == [seal]  # the mark is not taken for a record
    assert [r.seal for r in spool.published(Channel.WORK)] == [seal]
    assert spool.dispatch_marked(rec[0]) is True
    assert spool.mark_dispatched(rec[0], identity="someone-else") is False  # the first stands
    mark = tmp_path / "spool" / "WORK" / "claimed" / f"{seal}.dispatched"
    assert "fabric:vps-02-executor" in mark.read_text(encoding="utf-8")
    assert not list((tmp_path / "spool" / "WORK" / "claimed").glob(".tmp-*"))


def test_an_unreadable_mark_slot_counts_as_marked(tmp_path):
    """Fail closed: whatever occupies the mark's name, the adapter does not dispatch over it."""
    port, p, seal = dispatched_once(tmp_path)
    mark = tmp_path / "spool" / "WORK" / "claimed" / f"{seal}.dispatched"
    mark.unlink()
    mark.mkdir()
    lose_the_ledger(tmp_path)
    p.recover()
    adapter(tmp_path, port).tick()
    assert len(port.sent) == 1


def test_the_mark_directory_is_synced(tmp_path, monkeypatch):
    """The mark is a no-replay record: its directory entry is flushed, not only its bytes."""
    synced: list[str] = []
    real = spool_mod.os.fsync

    def fsync(fd):
        with_path = Path(f"/proc/self/fd/{fd}")
        if with_path.exists():
            synced.append(str(with_path.resolve()))
        return real(fd)

    monkeypatch.setattr(spool_mod.os, "fsync", fsync)
    dispatched_once(tmp_path)
    if not Path("/proc/self/fd").exists():
        pytest.skip("no /proc: cannot observe which descriptor was synced")
    assert str((tmp_path / "spool" / "WORK" / "claimed").resolve()) in synced


def test_a_transport_without_marks_behaves_as_before(tmp_path):
    """The in-memory reference backend offers no mark: dispatch works, the ledger still guards."""
    port = Port()
    t = InMemoryTransport()
    p = Planner(t, identity="planner", verifier_identities=("github-hosted:atlas-runner-verify",))
    p.dispatch(
        QueueItem(task_id="DEVQ-1", title="t", category=Category.RELIABILITY, severity=2),
        **FIELDS,
    )
    a = adapter(tmp_path, port, t)
    assert "DISPATCHED:DEVQ-1" in a.tick()
    a.tick()
    assert len(port.sent) == 1


# -- the limit, pinned --------------------------------------------------------------------------
def test_limit_ledger_and_spool_record_both_lost_is_still_a_second_dispatch(tmp_path):
    """NOT fixed here, on purpose visible: both local memories of the dispatch are gone.

    The planner's journal still knows the lineage is DISPATCHED, cannot tell a record that was
    never published from one that was published, claimed and then lost with the spool, and
    publishes it again; the adapter has nothing that says it sent this before. Closing this
    needs something outside both stores (the run correlation token / dispatch grant work in
    the backlog). If this test starts failing, that limit moved: update the backlog entry.
    """
    port, p, _seal = dispatched_once(tmp_path)
    lose_the_ledger(tmp_path)
    shutil.rmtree(tmp_path / "spool" / "WORK")
    SpoolTransport(tmp_path / "spool")  # the layout comes back empty
    assert "REPUBLISHED:DEVQ-1:WORK" in p.recover()
    adapter(tmp_path, port).tick()
    assert len(port.sent) == 2


def test_a_temp_name_that_cannot_be_removed_does_not_turn_a_taken_mark_into_an_error(
    tmp_path, monkeypatch
):
    """Windows: someone holds the temp file open. The mark stands, so the answer is True."""
    one_work(tmp_path)
    spool = SpoolTransport(tmp_path / "spool")
    rec = spool.claim(Channel.WORK, role=Role.IMPLEMENTER, identity="impl-a")
    real = spool_mod.os.unlink

    def held_open(path, *args, **kw):
        if Path(path).name.startswith(".tmp-") and Path(path).parent.name == "claimed":
            raise PermissionError(13, "sharing violation", str(path))
        return real(path, *args, **kw)

    monkeypatch.setattr(spool_mod.os, "unlink", held_open)
    assert spool.mark_dispatched(rec, identity="impl-a") is True
    assert spool.dispatch_marked(rec) is True
