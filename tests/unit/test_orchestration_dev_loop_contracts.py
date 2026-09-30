"""AS-DEVLOOP-001 slice 2: sealed contracts, finding->repair, transport semantics, planner cycle.

The in-memory backend is a REFERENCE backend: these tests prove contract semantics, not a
cross-host cycle.
"""

from __future__ import annotations

import pytest

from project_atlas.orchestration.autonomy.dev_contracts import (
    ContractError,
    Finding,
    FindingCategory,
    FindingClass,
    Role,
    Verdict,
    classify_finding,
    make_result,
    make_verdict,
    make_verification_request,
    make_work,
    materialize_repair,
)
from project_atlas.orchestration.autonomy.dev_planner import Phase, Planner
from project_atlas.orchestration.autonomy.dev_queue import Category, QueueItem
from project_atlas.orchestration.autonomy.dev_transport import (
    Channel,
    InMemoryTransport,
    TransportError,
)

BASE = "a" * 40
REV1, TREE1 = "b" * 40, "c" * 40
REV2, TREE2 = "d" * 40, "e" * 40
IMPL, VER = "vps1-impl", "vps2-ver"


def work(**kw):
    d = dict(
        task_id="T1",
        execution_id="T1-E1",
        lineage_root="T1",
        repository="WezzSide/project-atlas",
        base_revision=BASE,
        authority_ref="AUTH-1",
        allowed_paths=("src/x",),
        forbidden_paths=("infra/atlas-runner/controller",),
        acceptance_contract=("tests pass",),
    )
    d.update(kw)
    return make_work(**d)


def result(w, rev=REV1, tree=TREE1, who=IMPL):
    return make_result(w, executor_identity=who, result_revision=rev, result_tree=tree)


def test_seal_detects_any_mutation():
    w = work()
    w.verify_seal()
    forged = w.model_copy(update={"authority_ref": "AUTH-EVIL"})
    with pytest.raises(ContractError):
        forged.verify_seal()


def test_work_requires_exact_base_sha():
    with pytest.raises(ContractError):
        work(base_revision="main")


def test_verification_requires_different_identity_and_exact_artifact():
    w = work()
    r = result(w)
    with pytest.raises(ContractError, match="differ"):
        make_verification_request(w, r, verifier_identity=IMPL)
    req = make_verification_request(w, r, verifier_identity=VER)
    assert (req.result_revision, req.result_tree) == (REV1, TREE1)
    assert req.result_seal == r.seal  # references the exact result, not a summary


def test_result_must_answer_the_same_work():
    w1, w2 = work(), work(task_id="T2", execution_id="T2-E1", lineage_root="T2")
    with pytest.raises(ContractError):
        make_verification_request(w2, result(w1), verifier_identity=VER)


def test_verdict_bound_to_exact_artifact_and_pass_has_no_findings():
    w = work()
    req = make_verification_request(w, result(w), verifier_identity=VER)
    with pytest.raises(ContractError, match="exact"):
        make_verdict(req, verdict=Verdict.PASS, result_revision=REV2)
    f = Finding(finding_id="F1", category=FindingCategory.DEFECT)
    with pytest.raises(ContractError):
        make_verdict(req, verdict=Verdict.PASS, findings=(f,))
    with pytest.raises(ContractError):
        make_verdict(req, verdict=Verdict.FAIL)


@pytest.mark.parametrize(
    ("cat", "paths", "expected"),
    [
        (FindingCategory.DEFECT, ("src/x/a.py",), FindingClass.REPAIRABLE_WITHIN_AUTHORITY),
        (FindingCategory.TEST_GAP, (), FindingClass.REPAIRABLE_WITHIN_AUTHORITY),
        (FindingCategory.SCOPE_VIOLATION, ("docs/z.md",), FindingClass.REPAIRABLE_WITHIN_AUTHORITY),
        (
            FindingCategory.DEFECT,
            ("infra/atlas-runner/controller/merge_gate.py",),
            FindingClass.OWNER_AUTHORITY_REQUIRED,
        ),
        (FindingCategory.DEFECT, ("src/other/b.py",), FindingClass.OWNER_AUTHORITY_REQUIRED),
        (FindingCategory.SECRET_REQUIRED, (), FindingClass.OWNER_AUTHORITY_REQUIRED),
        (FindingCategory.TRUST_ROOT, (), FindingClass.OWNER_AUTHORITY_REQUIRED),
        (FindingCategory.PRIVILEGE_EXPANSION, (), FindingClass.OWNER_AUTHORITY_REQUIRED),
        (FindingCategory.DESTRUCTIVE_ACTION, (), FindingClass.OWNER_AUTHORITY_REQUIRED),
        (FindingCategory.OWNER_MERGE, (), FindingClass.OWNER_AUTHORITY_REQUIRED),
        (FindingCategory.ARCHITECTURE, (), FindingClass.NON_REPAIRABLE),
    ],
)
def test_finding_classification(cat, paths, expected):
    assert classify_finding(Finding(finding_id="F", category=cat, paths=paths), work()) is expected


def _fail(w, r, *findings):
    req = make_verification_request(w, r, verifier_identity=VER)
    return make_verdict(req, verdict=Verdict.FAIL, findings=tuple(findings))


def test_repair_is_bounded_lineage_preserving_and_never_widens_authority():
    w = work()
    r = result(w)
    v = _fail(
        w, r, Finding(finding_id="F1", category=FindingCategory.DEFECT, paths=("src/x/a.py",))
    )
    d = materialize_repair(w, v, result=r)
    assert d.action == "REPAIR" and d.repair_work is not None
    rw = d.repair_work
    assert rw.lineage_root == "T1" and rw.parent_task_id == "T1" and rw.attempt == 2
    assert rw.base_revision == REV1  # continues from the failed artifact
    assert (rw.authority_ref, rw.repository, rw.allowed_paths, rw.forbidden_paths) == (
        w.authority_ref,
        w.repository,
        w.allowed_paths,
        w.forbidden_paths,
    )
    assert "RESOLVE:F1" in rw.acceptance_contract


def test_repair_stops_at_ceiling_owner_and_architecture():
    w = work(max_attempts=1)
    r = result(w)
    f = Finding(finding_id="F1", category=FindingCategory.DEFECT)
    assert materialize_repair(w, _fail(w, r, f), result=r).reason == "ATTEMPT_CEILING_EXHAUSTED"
    w = work()
    r = result(w)
    own = Finding(finding_id="F2", category=FindingCategory.SECRET_REQUIRED)
    assert materialize_repair(w, _fail(w, r, own), result=r).action == "OWNER_REQUIRED"
    arch = Finding(finding_id="F3", category=FindingCategory.ARCHITECTURE)
    assert materialize_repair(w, _fail(w, r, arch), result=r).action == "BLOCKED"
    # a mixed verdict never auto-repairs the repairable part while an owner finding is present
    assert materialize_repair(w, _fail(w, r, f, own), result=r).action == "OWNER_REQUIRED"


def test_repair_refuses_pass_and_foreign_verdicts():
    w = work()
    r = result(w)
    req = make_verification_request(w, r, verifier_identity=VER)
    with pytest.raises(ContractError):
        materialize_repair(w, make_verdict(req, verdict=Verdict.PASS), result=r)
    other = result(w, rev=REV2, tree=TREE2)
    v = _fail(w, r, Finding(finding_id="F", category=FindingCategory.DEFECT))
    with pytest.raises(ContractError):
        materialize_repair(w, v, result=other)


# ---- transport semantics -------------------------------------------------------------------


def test_transport_consume_once_idempotent_publish_and_role_channels():
    t = InMemoryTransport()
    w = work()
    assert t.publish(w) is True and t.publish(w) is False  # idempotent
    with pytest.raises(TransportError):
        t.claim(Channel.WORK, role=Role.VERIFIER, identity=VER)
    got = t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL)
    assert got is not None and got.seal == w.seal
    assert t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL) is None  # consume-once


def test_result_cannot_be_consumed_as_verdict():
    t = InMemoryTransport()
    w = work()
    t.publish(result(w))
    assert t.claim(Channel.VERDICT, role=Role.PLANNER, identity="p") is None
    assert t.claim(Channel.RESULT, role=Role.PLANNER, identity="p") is not None


def test_wire_tamper_is_detected_on_claim():
    t = InMemoryTransport()
    t.publish(work())
    t._tamper_next(Channel.WORK, ("AUTH-1", "AUTH-9"))
    with pytest.raises(ContractError):
        t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL)


def test_executor_cannot_claim_its_own_verification():
    t = InMemoryTransport()
    w = work()
    r = result(w)
    t.publish(make_verification_request(w, r, verifier_identity=VER))
    assert t.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity="someone-else") is None
    with pytest.raises(TransportError, match="own verification"):
        t.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity=IMPL)
    assert t.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity=VER) is not None


# ---- planner cycle (reference backend) -----------------------------------------------------


def qi(tid, **kw):
    kw.setdefault("category", Category.RELIABILITY)
    return QueueItem(task_id=tid, title=tid, **kw)


def run_role_implementer(t, revs):
    """Simulated implementation role: claim work, emit a result at the next scripted revision."""
    w = t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL)
    assert w is not None
    rev, tree = revs.pop(0)
    r = make_result(w, executor_identity=IMPL, result_revision=rev, result_tree=tree)
    t.publish(r)
    return w


def run_role_verifier(t, decide):
    req = t.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity=VER)
    assert req is not None
    verdict, findings = decide(req)
    t.publish(make_verdict(req, verdict=verdict, findings=findings))
    return req


def planner(t):
    return Planner(t, identity="vps3-plan", verifier_identities=(VER,))


FIELDS = dict(
    repository="WezzSide/project-atlas",
    base_revision=BASE,
    authority_ref="AUTH-1",
    allowed_paths=("src/x",),
    forbidden_paths=("infra/atlas-runner/controller",),
)


def test_first_cycle_fail_repair_reverify_pass_without_relay():
    t = InMemoryTransport()
    p = planner(t)
    item = qi("DEVQ-1", severity=2)
    assert p.select([item]).selected is not None
    p.dispatch(item, **FIELDS)
    revs = [(REV1, TREE1), (REV2, TREE2)]

    run_role_implementer(t, revs)
    p.pump()
    assert p.lineages["DEVQ-1"].phase is Phase.VERIFYING
    run_role_verifier(
        t,
        lambda req: (
            Verdict.FAIL,
            (Finding(finding_id="F1", category=FindingCategory.DEFECT, paths=("src/x/a.py",)),),
        ),
    )
    p.pump()
    st = p.lineages["DEVQ-1"]
    assert st.phase is Phase.REPAIR_DISPATCHED and st.work.attempt == 2

    run_role_implementer(t, revs)  # repair executes against the failed artifact
    p.pump()
    req2 = run_role_verifier(t, lambda req: (Verdict.PASS, ()))
    assert req2.result_revision == REV2
    p.pump()
    assert st.phase is Phase.INTEGRATION_READY
    assert "DEVQ-1" in p.completed
    assert st.history[0].startswith("DISPATCHED") and st.history[-1].startswith("INTEGRATION_READY")
    # every hop was record-based: nothing but transport calls moved state
    assert {c[0] for c in t.claims} == {
        Channel.WORK,
        Channel.RESULT,
        Channel.VERIFICATION,
        Channel.VERDICT,
    }


def test_owner_required_lane_is_blocked_and_program_selects_next_task():
    t = InMemoryTransport()
    p = planner(t)
    a, b = qi("A", severity=3), qi("B", severity=1)
    p.dispatch(a, **FIELDS)
    run_role_implementer(t, [(REV1, TREE1)])
    p.pump()
    run_role_verifier(
        t,
        lambda req: (
            Verdict.FAIL,
            (Finding(finding_id="S", category=FindingCategory.SECRET_REQUIRED),),
        ),
    )
    p.pump()
    assert p.lineages["A"].phase is Phase.OWNER_REQUIRED
    sel = p.select([a, b])
    assert sel.selected is not None and sel.selected.task_id == "B"
    assert dict(sel.skipped)["A"].startswith("BLOCKED:OWNER_REQUIRED")


def test_no_independent_verifier_blocks_instead_of_self_certifying():
    t = InMemoryTransport()
    p = Planner(t, identity="plan", verifier_identities=(IMPL,))
    p.dispatch(qi("A"), **FIELDS)
    run_role_implementer(t, [(REV1, TREE1)])
    p.pump()
    assert p.lineages["A"].phase is Phase.BLOCKED
    assert t.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity=IMPL) is None


def test_verdict_for_a_different_artifact_is_refused():
    t = InMemoryTransport()
    p = planner(t)
    p.dispatch(qi("A"), **FIELDS)
    run_role_implementer(t, [(REV1, TREE1)])
    p.pump()
    st = p.lineages["A"]
    req = t.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity=VER)
    assert req is not None
    # forge a verdict on a different artifact by re-sealing a modified request
    other = req.model_copy(update={"result_revision": REV2, "result_tree": TREE2}).sealed()
    t.publish(make_verdict(other, verdict=Verdict.PASS))
    with pytest.raises(ContractError):
        p.pump()
    assert st.phase is Phase.VERIFYING and "A" not in p.completed


def test_owner_only_or_inadmissible_task_cannot_be_dispatched():
    from project_atlas.orchestration.autonomy.dev_queue import OwnerInput

    p = planner(InMemoryTransport())
    with pytest.raises(ContractError):
        p.dispatch(qi("S", requires_owner=(OwnerInput.SECRET,)), **FIELDS)
