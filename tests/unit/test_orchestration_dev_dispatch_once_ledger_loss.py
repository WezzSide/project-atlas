"""Recovery / duplicate semantics, adapter part: a lost ledger is not a licence to dispatch.

Measured before this change: dispatch-once rested on ONE record, the DISPATCH line of the
adapter's crosswalk ledger. With the ledger (and the adapter's pending directory) lost and the
spool intact, a restarted adapter found its claimed work record, could not tell it from a
record claimed just before a crash (``_readopt_claimed``), adopted it and sent the workflow
dispatch a second time: a second executor attempt for one execution identity.

Now the adapter leaves a dispatch mark for the work record in the transport, after the ledger
record and before anything is sent. A work item whose mark exists while the ledger has no
DISPATCH for it is refused: a run may exist that this adapter cannot correlate.

What this does NOT cover is asserted at the end of the file rather than left unsaid: with the
ledger AND the transport's copy of the record lost, a record the planner publishes again is
dispatched again.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import project_atlas.orchestration.autonomy.dev_spool_transport as spool_mod
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
    assert len(refused) == 1 and "DISPATCH_LEDGER_LOST" in refused[0]  # said once, not silent
    assert not [e for e in events if e.startswith("DISPATCHED:")]
    # the refused work is not picked up again by a later restart, and nothing is in flight
    last = adapter(tmp_path, port)
    assert last.tick() == [] and last.works == {}
    assert Crosswalk(tmp_path / "xw.jsonl").hop(seal, "DISPATCH") is None  # no invented history


def test_a_direct_dispatch_of_a_marked_work_item_is_refused_before_anything_is_written(tmp_path):
    port, p, seal = dispatched_once(tmp_path)
    work = p.lineages["DEVQ-1"].work
    lose_the_ledger(tmp_path)
    a = adapter(tmp_path, port)
    with pytest.raises(DispatchRefused, match="DISPATCH_LEDGER_LOST"):
        a.dispatch(work)
    assert len(port.sent) == 1
    assert a.xw.hop(seal, "DISPATCH") is None


def test_the_mark_is_written_after_the_ledger_and_before_the_send(tmp_path, monkeypatch):
    order: list[str] = []
    port = Port()
    p = planner(tmp_path)
    p.dispatch(
        QueueItem(task_id="DEVQ-1", title="t", category=Category.RELIABILITY, severity=2),
        **FIELDS,
    )
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
    assert order == ["ledger", "mark", "send"]


def test_a_mark_that_cannot_be_written_stops_the_dispatch_before_the_send(tmp_path, monkeypatch):
    """No send without both records: the ledger alone must not be the only memory of a send."""
    port = Port()
    p = planner(tmp_path)
    p.dispatch(
        QueueItem(task_id="DEVQ-1", title="t", category=Category.RELIABILITY, severity=2),
        **FIELDS,
    )
    a = adapter(tmp_path, port)

    def failing(*_a, **_k):
        raise OSError(28, "no space left on device")

    monkeypatch.setattr(a.transport, "mark_dispatched", failing)
    events = a.tick()
    assert port.sent == []
    assert any(e.startswith("EXECUTION_FAILED:DEVQ-1:") for e in events)
    monkeypatch.undo()
    assert adapter(tmp_path, port).tick() == [] and port.sent == []  # never retried


def test_a_crash_between_ledger_and_mark_leaves_the_ledger_in_charge(tmp_path, monkeypatch):
    """The ledger says DISPATCH, no mark, nothing sent: not dispatched again (as before)."""
    port = Port()
    p = planner(tmp_path)
    p.dispatch(
        QueueItem(task_id="DEVQ-1", title="t", category=Category.RELIABILITY, severity=2),
        **FIELDS,
    )
    a = adapter(tmp_path, port)

    class Died(BaseException):
        pass

    def die(*_a, **_k):
        raise Died

    monkeypatch.setattr(a.transport, "mark_dispatched", die)
    with pytest.raises(Died):
        a.dispatch(next(iter(a.works.values())) if a.works else a.accept_work())
    monkeypatch.undo()
    b = adapter(tmp_path, port)
    b.tick()
    assert port.sent == []


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
