"""Durable spool backend: same contract as the reference backend + durability/race properties."""

from __future__ import annotations

import json
import threading

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
    with pytest.raises(TransportError, match="own verification"):
        t.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity=IMPL)
    assert t.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity=VER) is not None


def test_tampered_file_is_refused_and_not_consumed(tmp_path):
    t = SpoolTransport(tmp_path)
    w = work()
    t.publish(w)
    f = tmp_path / "WORK" / f"{w.seal}.json"
    f.write_text(f.read_text().replace("AUTH-1", "AUTH-9"))
    with pytest.raises(ContractError):
        t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL)
    assert f.exists()  # left in place as evidence, never handed out


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
