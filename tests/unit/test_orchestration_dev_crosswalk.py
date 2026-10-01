from __future__ import annotations

import pytest

from project_atlas.orchestration.autonomy.dev_contracts import (
    Verdict,
    make_result,
    make_verdict,
    make_verification_request,
    make_work,
)
from project_atlas.orchestration.autonomy.dev_crosswalk import (
    Crosswalk,
    CrosswalkError,
    RemoteExecutionReport,
    VerifierProfile,
    derive_dispatch_id,
    derive_lease_id,
    ingest_report,
)

BASE, R1, T1, R2, T2 = "a" * 40, "b" * 40, "c" * 40, "d" * 40, "e" * 40
IMPL, VER = "vps1-impl", "gh-verifier"


def work(tid="T1", eid="T1-E1", base=BASE):
    return make_work(
        task_id=tid,
        execution_id=eid,
        lineage_root="T1",
        repository="WezzSide/project-atlas",
        base_revision=base,
        authority_ref="AUTH-1",
        allowed_paths=("src/x",),
        forbidden_paths=("infra/atlas-runner/controller",),
    )


def report(w, **kw):
    d = dict(
        workflow_conclusion="success",
        task_id=w.task_id,
        execution_id=w.execution_id,
        base_revision=w.base_revision,
        executor_identity=IMPL,
        result_revision=R1,
        result_tree=T1,
        artifact_digests=("f" * 64,),
        changed_paths=("src/x/a.py",),
    )
    d.update(kw)
    return RemoteExecutionReport(**d)


def test_ids_are_deterministic_and_one_to_one(tmp_path):
    w1, w2 = work(), work("T1-R2", "T1-R2-E1")
    assert derive_dispatch_id(w1) == derive_dispatch_id(w1)
    assert derive_dispatch_id(w1) != derive_dispatch_id(w2)
    assert derive_lease_id(w1) != derive_dispatch_id(w1)
    xw = Crosswalk(tmp_path / "xw.jsonl")
    d, ls = xw.bind_work(w1)
    assert xw.bind_work(w1) == (d, ls)  # idempotent
    assert xw.resolve("dispatch_id", d)["work_seal"] == w1.seal
    assert xw.resolve("lease_id", ls)["execution_id"] == "T1-E1"


def test_execution_id_reuse_for_different_work_is_refused(tmp_path):
    xw = Crosswalk(tmp_path / "xw.jsonl")
    xw.bind_work(work())
    with pytest.raises(CrosswalkError, match="execution_id"):
        xw.bind_work(work(base="9" * 40))  # same execution_id, different sealed work


def test_ledger_is_durable_and_replayed_and_corruption_fails_closed(tmp_path):
    p = tmp_path / "xw.jsonl"
    w = work()
    a = Crosswalk(p)
    a.bind_work(w)
    res = make_result(w, executor_identity=IMPL, result_revision=R1, result_tree=T1)
    a.bind_result(res)
    b = Crosswalk(p)
    assert b.resolve("work_seal", w.seal)["hops"][0]["result_revision"] == R1
    p.write_text(p.read_text() + "not json\n")
    with pytest.raises(CrosswalkError, match="corrupt"):
        Crosswalk(p)


def test_result_conflicts_and_foreign_results_are_refused(tmp_path):
    xw = Crosswalk(tmp_path / "xw.jsonl")
    w = work()
    xw.bind_work(w)
    r1 = make_result(w, executor_identity=IMPL, result_revision=R1, result_tree=T1)
    xw.bind_result(r1)
    xw.bind_result(r1)  # idempotent
    r2 = make_result(w, executor_identity=IMPL, result_revision=R2, result_tree=T2)
    with pytest.raises(CrosswalkError, match="conflicting"):
        xw.bind_result(r2)
    other = work("T9", "T9-E1")
    with pytest.raises(CrosswalkError, match="does not match"):
        xw.bind_result(
            make_result(other, executor_identity=IMPL, result_revision=R1, result_tree=T1)
        )


def test_verdict_must_judge_the_crosswalked_artifact(tmp_path):
    xw = Crosswalk(tmp_path / "xw.jsonl")
    w = work()
    xw.bind_work(w)
    r1 = make_result(w, executor_identity=IMPL, result_revision=R1, result_tree=T1)
    xw.bind_result(r1)
    req = make_verification_request(w, r1, verifier_identity=VER)
    xw.bind_verification(w.seal, req)
    xw.bind_verdict(w.seal, make_verdict(req, verdict=Verdict.PASS, findings=()))
    w2 = work("T2", "T2-E1")
    xw.bind_work(w2)
    r_other = make_result(w2, executor_identity=IMPL, result_revision=R2, result_tree=T2)
    xw.bind_result(r_other)
    req2 = make_verification_request(w2, r_other, verifier_identity=VER)
    v_bad = make_verdict(req2, verdict=Verdict.PASS, findings=())
    with pytest.raises(CrosswalkError):
        xw.bind_verdict(w.seal, v_bad)  # judges another artifact than w's result


def test_ingest_success_binds_exact_result(tmp_path):
    xw = Crosswalk(tmp_path / "xw.jsonl")
    w = work()
    xw.bind_work(w)
    res = ingest_report(report(w), w, xw)
    assert (res.result_revision, res.result_tree, res.work_seal) == (R1, T1, w.seal)


@pytest.mark.parametrize(
    "kw,match",
    [
        ({"workflow_conclusion": "failure"}, "did not succeed"),
        ({"result_revision": None}, "exact result"),
        ({"result_tree": None}, "exact result"),
        ({"result_revision": BASE}, "no artifact"),
        ({"artifact_digests": ()}, "artifact digest"),
        ({"base_revision": "9" * 40}, "base revision"),
        ({"task_id": "OTHER"}, "identity"),
        ({"execution_id": "OTHER-E1"}, "identity"),
        ({"changed_paths": ("infra/atlas-runner/controller/merge_gate.py",)}, "forbidden"),
        ({"changed_paths": ("src/x/a.py", "src/y/b.py")}, "outside allowed_paths"),
    ],
)
def test_workflow_success_alone_is_never_a_result(tmp_path, kw, match):
    xw = Crosswalk(tmp_path / "xw.jsonl")
    w = work()
    xw.bind_work(w)
    with pytest.raises(CrosswalkError, match=match):
        ingest_report(report(w, **kw), w, xw)


def test_ingest_requires_crosswalked_dispatch(tmp_path):
    xw = Crosswalk(tmp_path / "xw.jsonl")
    w = work()
    with pytest.raises(CrosswalkError, match="unresolvable"):
        ingest_report(report(w), w, xw)


def test_verifier_profiles_share_contracts_and_exclude_executor():
    gh = VerifierProfile("github_hosted", ("gh-verifier",))
    vps = VerifierProfile("vps2_independent", ("vps2-ver", IMPL))
    assert gh.for_executor(IMPL) == ("gh-verifier",)
    assert vps.for_executor(IMPL) == ("vps2-ver",)
    with pytest.raises(CrosswalkError):
        VerifierProfile("vps9", ("x",))
    with pytest.raises(CrosswalkError):
        VerifierProfile("github_hosted", ())


# -- crash recovery: a torn tail must not brick the ledger on the next open ---------------

STAMP, PSHA = "2026-10-01T00:00:00Z", "f" * 64


def test_torn_tail_is_healed_before_the_next_append(tmp_path):
    p = tmp_path / "xw.jsonl"
    xw = Crosswalk(p)
    w = work()
    xw.bind_work(w)
    xw.bind_dispatch(w.seal, dispatched_at=STAMP, payload_sha256=PSHA)
    p.write_bytes(p.read_bytes()[:-15])  # crash in the middle of appending the DISPATCH line
    again = Crosswalk(p)  # torn tail is ignored in memory...
    assert again.hop(w.seal, "DISPATCH") is None
    again.bind_dispatch(w.seal, dispatched_at=STAMP, payload_sha256=PSHA)  # ...and re-bindable
    healed = Crosswalk(p)  # must NOT raise "corrupt crosswalk ledger line 2"
    assert healed.hop(w.seal, "DISPATCH") == {
        "event": "DISPATCH",
        "dispatched_at": STAMP,
        "payload_sha256": PSHA,
    }
    assert p.read_bytes().endswith(b"\n") and len(p.read_bytes().splitlines()) == 2
    assert (tmp_path / "xw.jsonl.torn").read_bytes().strip(), "torn bytes are kept as evidence"


def test_torn_only_line_is_healed_too(tmp_path):
    p = tmp_path / "xw.jsonl"
    p.write_bytes(b'{"event": "WORK", "work_s')  # first append never completed
    xw = Crosswalk(p)
    w = work()
    xw.bind_work(w)
    assert Crosswalk(p).knows_work(w.seal)
    assert (tmp_path / "xw.jsonl.torn").read_bytes().startswith(b'{"event": "WORK"')


def test_complete_ledger_is_never_touched_by_healing(tmp_path):
    p = tmp_path / "xw.jsonl"
    xw = Crosswalk(p)
    w = work()
    xw.bind_work(w)
    xw.bind_dispatch(w.seal, dispatched_at=STAMP, payload_sha256=PSHA)
    assert not (tmp_path / "xw.jsonl.torn").exists()
    assert len(Crosswalk(p).lineage("T1")) == 1
