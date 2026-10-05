"""Durable spool backend: same contract as the reference backend + durability/race properties."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from project_atlas.orchestration.autonomy.dev_contracts import (
    ContractError,
    Role,
    Verdict,
    make_result,
    make_verdict,
    make_verification_request,
    make_work,
)
from project_atlas.orchestration.autonomy.dev_planner import Phase, Planner
from project_atlas.orchestration.autonomy.dev_queue import Category, QueueItem
from project_atlas.orchestration.autonomy.dev_spool_transport import SpoolTransport
from project_atlas.orchestration.autonomy.dev_transport import Channel, TransportError

BASE = "a" * 40
REV1, TREE1 = "b" * 40, "c" * 40
IMPL, VER = "vps1-impl", "vps2-ver"


def work(tid="T1"):
    return make_work(
        task_id=tid,
        execution_id=f"{tid}-E1",
        lineage_root=tid,
        repository="WezzSide/project-atlas",
        base_revision=BASE,
        authority_ref="AUTH-1",
        allowed_paths=("src/x",),
        forbidden_paths=("infra/atlas-runner/controller",),
        acceptance_contract=("tests pass",),
    )


def test_publish_idempotent_claim_consume_once_and_durable_across_instances(tmp_path):
    a = SpoolTransport(tmp_path)
    w = work()
    assert a.publish(w) is True and a.publish(w) is False
    b = SpoolTransport(tmp_path)  # a different process/host view of the same medium
    assert b.publish(w) is False
    got = b.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL)
    assert got is not None and got.seal == w.seal
    assert a.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL) is None
    assert a.publish(w) is False  # consumed records are not republished
    claim = json.loads((tmp_path / "WORK/claimed" / f"{w.seal}.claim.json").read_text())
    assert claim == {"identity": IMPL, "role": "IMPLEMENTER"}


def test_role_channel_rules_and_no_self_verification(tmp_path):
    t = SpoolTransport(tmp_path)
    w = work()
    r = make_result(w, executor_identity=IMPL, result_revision=REV1, result_tree=TREE1)
    with pytest.raises(TransportError):
        t.claim(Channel.WORK, role=Role.VERIFIER, identity=VER)
    t.publish(r)
    assert t.claim(Channel.VERDICT, role=Role.PLANNER, identity="p") is None
    t.publish(make_verification_request(w, r, verifier_identity=VER))
    assert t.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity="other") is None
    # the executor is not handed its own verification, and is not wedged by it either
    assert t.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity=IMPL) is None
    assert t.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity=VER) is not None


def test_tampered_file_is_refused_and_not_consumed(tmp_path):
    t = SpoolTransport(tmp_path)
    w = work()
    t.publish(w)
    f = tmp_path / "WORK" / f"{w.seal}.json"
    f.write_text(f.read_text().replace("AUTH-1", "AUTH-9"))
    with pytest.raises(ContractError):
        t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL)
    assert not f.exists() and (tmp_path / "WORK" / "rejected" / f.name).exists()
    assert t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL) is None  # not wedged


def test_renamed_file_is_refused(tmp_path):
    t = SpoolTransport(tmp_path)
    w1, w2 = work("T1"), work("T2")
    t.publish(w1)
    (tmp_path / "WORK" / f"{w1.seal}.json").rename(tmp_path / "WORK" / f"{w2.seal}.json")
    with pytest.raises(TransportError, match="seal"):
        t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL)


def test_concurrent_claimers_get_a_record_exactly_once(tmp_path):
    SpoolTransport(tmp_path).publish(work())
    wins: list[str] = []

    def go(i):
        r = SpoolTransport(tmp_path).claim(
            Channel.WORK, role=Role.IMPLEMENTER, identity=f"impl-{i}"
        )
        if r is not None:
            wins.append(r.seal)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [x.start() for x in ts]
    [x.join() for x in ts]
    assert len(wins) == 1


def test_full_cycle_over_spool_with_separate_role_instances(tmp_path):
    """planner / implementer / verifier each hold their OWN transport handle on the medium."""
    plan_t, impl_t, ver_t = (SpoolTransport(tmp_path) for _ in range(3))
    p = Planner(plan_t, identity="vps3-plan", verifier_identities=(VER,))
    item = QueueItem(task_id="DEVQ-1", title="t", category=Category.RELIABILITY, severity=2)
    p.dispatch(
        item,
        repository="WezzSide/project-atlas",
        base_revision=BASE,
        authority_ref="AUTH-1",
        allowed_paths=("src/x",),
        forbidden_paths=(),
    )
    w = impl_t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL)
    impl_t.publish(make_result(w, executor_identity=IMPL, result_revision=REV1, result_tree=TREE1))
    p.pump()
    req = ver_t.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity=VER)
    assert req is not None and req.result_revision == REV1
    ver_t.publish(make_verdict(req, verdict=Verdict.PASS, findings=()))
    p.pump()
    assert p.lineages["DEVQ-1"].phase is Phase.INTEGRATION_READY


def test_misnamed_record_is_rejected_once_and_does_not_wedge_the_channel(tmp_path):
    t = SpoolTransport(tmp_path)
    w = work()
    t.publish(w)
    d = tmp_path / "WORK"
    (d / f"{w.seal}.json").rename(d / "0000.json")  # valid record, wrong file name, sorts first
    with pytest.raises(TransportError, match="does not match"):
        t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL)
    assert (d / "rejected" / "0000.json").exists()
    assert t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL) is None


# -- Windows sharing violations are transient, never evidence of a hostile record ------------------


def _no_sleep(monkeypatch):
    import project_atlas.orchestration.autonomy.dev_spool_transport as mod

    naps: list[float] = []
    monkeypatch.setattr(mod.time, "sleep", naps.append)
    return mod, naps


def _claimed_link_faults(mod, monkeypatch, make_fault):
    """Wrap ``os.link`` so only the claim (into ``claimed/``) can be faulted, never publish."""
    real, calls = mod.os.link, {"n": 0}

    def wrapped(src, dst, *a, **k):
        if Path(dst).parent.name == "claimed":
            calls["n"] += 1
            make_fault(calls["n"], src, dst)
        return real(src, dst, *a, **k)

    monkeypatch.setattr(mod.os, "link", wrapped)
    return calls


def test_transient_link_permission_error_is_retried_not_parked(tmp_path, monkeypatch):
    mod, naps = _no_sleep(monkeypatch)
    t = SpoolTransport(tmp_path)
    w = work()
    t.publish(w)

    def fault(n, src, dst):
        if n <= 3:  # a concurrent reader still holds the file (WinError 32)
            raise PermissionError(13, "sharing violation", str(src))

    calls = _claimed_link_faults(mod, monkeypatch, fault)
    got = t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL)
    assert got is not None and got.seal == w.seal
    assert calls["n"] == 4 and len(naps) == 3
    assert not list((tmp_path / "WORK" / "rejected").glob("*")), (
        "a valid record must never be parked"
    )
    assert (tmp_path / "WORK" / "claimed" / f"{w.seal}.json").exists()
    assert not (tmp_path / "WORK" / f"{w.seal}.json").exists()


def test_transient_read_permission_error_is_retried_not_parked(tmp_path, monkeypatch):
    mod, naps = _no_sleep(monkeypatch)
    t = SpoolTransport(tmp_path)
    w = work()
    t.publish(w)
    real, calls = mod.Path.read_text, {"n": 0}

    def flaky(self, *a, **k):
        if self.name == f"{w.seal}.json" and self.parent.name == "WORK":
            calls["n"] += 1
            if calls["n"] == 1:
                raise PermissionError(13, "sharing violation", str(self))
        return real(self, *a, **k)

    monkeypatch.setattr(mod.Path, "read_text", flaky)
    got = t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL)
    assert got is not None and got.seal == w.seal and len(naps) == 1
    assert not list((tmp_path / "WORK" / "rejected").glob("*"))


def test_persistent_link_permission_error_is_bounded_and_never_rejects_a_valid_record(
    tmp_path, monkeypatch
):
    mod, naps = _no_sleep(monkeypatch)
    t = SpoolTransport(tmp_path)
    w = work()
    t.publish(w)
    state = {"deny": True}

    def fault(n, src, dst):
        if state["deny"]:
            raise PermissionError(13, "denied", str(src))

    calls = _claimed_link_faults(mod, monkeypatch, fault)
    assert t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL) is None
    assert calls["n"] == mod._TRANSIENT_TRIES, "retries are bounded"
    assert len(naps) == mod._TRANSIENT_TRIES - 1
    pending = tmp_path / "WORK" / f"{w.seal}.json"
    assert pending.exists() and not list((tmp_path / "WORK" / "rejected").glob("*"))
    state["deny"] = False  # contention over: the record is still there and is claimed exactly once
    got = t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL)
    assert got is not None and got.seal == w.seal
    assert t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL) is None


def test_blocked_claimed_slot_is_parked_once_and_not_retried(tmp_path, monkeypatch):
    mod, naps = _no_sleep(monkeypatch)
    t = SpoolTransport(tmp_path)
    w = work()
    t.publish(w)
    (tmp_path / "WORK" / "claimed" / f"{w.seal}.json").mkdir()  # slot occupied by a directory
    calls = _claimed_link_faults(mod, monkeypatch, lambda *_: None)
    with pytest.raises(TransportError, match="could not be claimed"):
        t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL)
    assert calls["n"] == 1 and naps == [], "a blocked destination is never retried"
    assert (tmp_path / "WORK" / "rejected" / f"{w.seal}.json").exists()
    assert t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL) is None  # parked once


# ---- ATLAS-DEVQ-0011: addressed delivery ----------------------------------------------------


def test_an_addressed_record_is_delivered_only_to_its_addressee(tmp_path):
    t = SpoolTransport(tmp_path)
    a, b, c = work("A"), work("B"), work("C")
    assert t.publish(a, to="vps1-impl") and t.publish(b, to="vps4-impl") and t.publish(c)
    assert (tmp_path / "WORK" / f"{a.seal}.to").read_text() == "vps1-impl"
    assert not (tmp_path / "WORK" / f"{c.seal}.to").exists()

    def claim(who):
        return SpoolTransport(tmp_path).claim(Channel.WORK, role=Role.IMPLEMENTER, identity=who)

    # another identity gets only what is addressed to nobody in particular
    assert claim("someone").seal == c.seal and claim("someone") is None
    assert claim("VPS4-IMPL").seal == b.seal and claim("vps4-impl") is None  # identity rules
    assert claim("vps1-impl").seal == a.seal and claim("vps1-impl") is None
    # the address is not a record: it is never listed, claimed or parked
    assert {r.seal for r in t.published(Channel.WORK)} == {a.seal, b.seal, c.seal}
    assert not (tmp_path / "WORK" / "rejected").exists()


def test_a_record_is_addressed_once(tmp_path):
    t = SpoolTransport(tmp_path)
    a, c = work("A"), work("C")
    assert t.publish(a, to="vps1-impl") is True
    assert t.publish(a, to="vps1-impl") is False  # idempotent with the same address
    for other in ("vps4-impl", None):
        with pytest.raises(TransportError, match="already published with a different address"):
            t.publish(a, to=other)
    assert t.publish(c) is True
    with pytest.raises(TransportError, match="already published with a different address"):
        t.publish(c, to="vps1-impl")  # an open record cannot be narrowed afterwards
    for bad in ("", " x ", 5):
        with pytest.raises(TransportError, match="invalid addressee"):
            t.publish(work("D"), to=bad)
    assert not (tmp_path / "WORK" / f"{work('D').seal}.json").exists()
    # still so after the record was claimed
    assert t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity="vps1-impl").seal == a.seal
    assert t.publish(a, to="vps1-impl") is False
    with pytest.raises(TransportError, match="already published with a different address"):
        t.publish(a, to="vps4-impl")


def test_an_unreadable_address_delivers_to_nobody_and_does_not_wedge_the_channel(tmp_path):
    t = SpoolTransport(tmp_path)
    a, c = work("A"), work("C")
    t.publish(a, to="vps1-impl")
    t.publish(c)
    (tmp_path / "WORK" / f"{a.seal}.to").write_bytes(b"\xff\xfe not an identity \n")
    got = t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity="vps1-impl")
    assert got.seal == c.seal  # the open record behind it is still delivered
    assert t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity="vps1-impl") is None
    assert (tmp_path / "WORK" / f"{a.seal}.json").exists()  # left in place, not parked
    with pytest.raises(TransportError):
        t.publish(a, to="vps1-impl")


def test_an_address_written_before_a_crash_is_never_widened(tmp_path):
    """The address is written before the record: a record never appears without it."""
    t = SpoolTransport(tmp_path)
    a = work("A")
    (tmp_path / "WORK" / f"{a.seal}.to").write_text("vps1-impl")  # crash before the record
    with pytest.raises(TransportError, match="already published with a different address"):
        t.publish(a)
    assert t.publish(a, to="vps1-impl") is True
    assert t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity="other") is None


def test_the_in_memory_backend_addresses_the_same_way():
    from project_atlas.orchestration.autonomy.dev_transport import InMemoryTransport

    t = InMemoryTransport()
    a, c = work("A"), work("C")
    assert t.publish(a, to="vps1-impl") and t.publish(c)
    assert t.publish(a, to="vps1-impl") is False
    with pytest.raises(TransportError, match="different address"):
        t.publish(a)
    with pytest.raises(TransportError, match="different address"):
        t.publish(c, to="vps1-impl")
    with pytest.raises(TransportError, match="invalid addressee"):
        t.publish(work("D"), to="")
    assert t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity="other").seal == c.seal
    assert t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity="other") is None
    assert t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity="vps1-impl").seal == a.seal
    assert InMemoryTransport.addressed is True and SpoolTransport.addressed is True
