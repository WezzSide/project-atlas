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
    ResultRecord,
    Role,
    Verdict,
    VerdictRecord,
    VerificationRequest,
    classify_finding,
    make_result,
    make_verdict,
    make_verification_request,
    make_work,
    materialize_repair,
)
from project_atlas.orchestration.autonomy.dev_planner import Phase, Planner, PlannerError
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
    # the executor is never handed its own verification (and is not wedged either)
    assert t.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity=IMPL) is None
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
    p.pump()  # the forged verdict is quarantined, never applied, and never aborts the pass
    assert p.quarantined and p.quarantined[0][0] == "VERDICT"
    assert st.phase is Phase.VERIFYING and "A" not in p.completed


def test_owner_only_or_inadmissible_task_cannot_be_dispatched():
    from project_atlas.orchestration.autonomy.dev_queue import OwnerInput

    p = planner(InMemoryTransport())
    with pytest.raises(ContractError):
        p.dispatch(qi("S", requires_owner=(OwnerInput.SECRET,)), **FIELDS)


# ---- IV #1033 repairs ------------------------------------------------------------------------


def _verifying(t, tid="A"):
    p = planner(t)
    p.dispatch(qi(tid), **FIELDS)
    run_role_implementer(t, [(REV1, TREE1)])
    p.pump()
    return p, t.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity=VER)


def test_planner_refuses_self_or_unissued_verdict_even_if_resealed():
    from project_atlas.orchestration.autonomy.dev_contracts import VerdictRecord

    t = InMemoryTransport()
    p, req = _verifying(t)
    forged = VerdictRecord(
        task_id="A",
        execution_id=req.execution_id,
        verifier_identity=IMPL,  # the executor "verifies" itself
        verdict=Verdict.PASS,
        result_revision=req.result_revision,
        result_tree=req.result_tree,
        request_seal="x" * 8,
    ).sealed()
    t.publish(forged)
    p.pump()
    assert p.lineages["A"].phase is Phase.VERIFYING and p.quarantined
    other = VerdictRecord(
        task_id="A",
        execution_id=req.execution_id,
        verifier_identity="some-other-verifier",
        verdict=Verdict.PASS,
        result_revision=req.result_revision,
        result_tree=req.result_tree,
        request_seal=req.seal,
    ).sealed()
    t.publish(other)
    p.pump()
    assert p.lineages["A"].phase is Phase.VERIFYING and len(p.quarantined) == 2
    t.publish(make_verdict(req, verdict=Verdict.PASS))
    p.pump()
    assert p.lineages["A"].phase is Phase.INTEGRATION_READY


@pytest.mark.parametrize("variant", ["Impl-A", "impl-a ", " IMPL-A", "IMPL-A"])
def test_identity_variants_are_the_same_identity(variant):
    w = work()
    r = result(w, who="impl-a")
    with pytest.raises(ContractError):
        make_verification_request(w, r, verifier_identity=variant)


def test_non_canonical_identities_are_rejected_at_record_level():
    w = work()
    for bad in ("impl a", "impl-a\n", "", " x"):
        with pytest.raises(ContractError):
            make_result(w, executor_identity=bad, result_revision=REV1, result_tree=TREE1)


def test_transport_treats_identity_variants_as_the_same_identity():
    t = InMemoryTransport()
    w = work()
    r = result(w, who="vps1-impl")
    t.publish(make_verification_request(w, r, verifier_identity="vps2-ver"))
    assert t.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity="VPS1-IMPL ") is None
    assert t.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity=" VPS2-VER") is not None


@pytest.mark.parametrize(
    "path",
    ["src/x/../secret/k", "src/x/../../etc/p", "/etc/passwd", "src\\x\\a.py", "src/xy/a.py", ""],
)
def test_escaping_or_lookalike_paths_never_classify_as_repairable(path):
    w = work(allowed_paths=("src/x",), forbidden_paths=("src/secret",))
    f = Finding(finding_id="F", category=FindingCategory.DEFECT, paths=(path,))
    assert classify_finding(f, w) is FindingClass.OWNER_AUTHORITY_REQUIRED


def test_empty_allowed_scope_allows_no_finding_path():
    w = work(allowed_paths=())
    f = Finding(finding_id="F", category=FindingCategory.DEFECT, paths=("src/x/a.py",))
    assert classify_finding(f, w) is FindingClass.OWNER_AUTHORITY_REQUIRED
    ok = Finding(finding_id="F", category=FindingCategory.DEFECT)
    assert classify_finding(ok, w) is FindingClass.REPAIRABLE_WITHIN_AUTHORITY


def test_result_replay_and_phase_regression_are_refused():
    t = InMemoryTransport()
    p, req = _verifying(t)
    t.publish(make_verdict(req, verdict=Verdict.PASS))
    p.pump()
    st = p.lineages["A"]
    assert st.phase is Phase.INTEGRATION_READY
    w = st.work
    t.publish(make_result(w, executor_identity=IMPL, result_revision=REV2, result_tree=TREE2))
    p.pump()
    assert st.phase is Phase.INTEGRATION_READY and "A" in p.completed
    assert any(q[0] == "RESULT" for q in p.quarantined)


def test_one_bad_record_does_not_abort_the_pass_for_other_lineages():
    t = InMemoryTransport()
    p = planner(t)
    p.dispatch(qi("A"), **FIELDS)
    # B gets a disjoint scope: two live lineages may not share a write scope (ATLAS-DEVQ-0006)
    p.dispatch(qi("B"), **{**FIELDS, "allowed_paths": ("src/y",)})
    stray = make_work(
        **{
            **dict(task_id="ZZ", execution_id="ZZ-E1", lineage_root="ZZ"),
            **{k: v for k, v in FIELDS.items()},
        }
    )
    t.publish(make_result(stray, executor_identity=IMPL, result_revision=REV1, result_tree=TREE1))
    wb = p.lineages["B"].work
    t.publish(make_result(wb, executor_identity=IMPL, result_revision=REV2, result_tree=TREE2))
    p.pump()
    assert p.lineages["B"].phase is Phase.VERIFYING
    assert len(p.quarantined) == 1


def test_attempt_beyond_ceiling_is_not_a_valid_work_item():
    with pytest.raises(ContractError):
        work(attempt=5, max_attempts=3)


def test_sha_fields_reject_a_trailing_newline():
    with pytest.raises(ContractError):
        work(base_revision=BASE + "\n")


# ---- IV #1033 round 2 ------------------------------------------------------------------------


def test_stale_pass_for_a_superseded_work_item_cannot_promote_a_noop_repair():
    t = InMemoryTransport()
    p, req = _verifying(t)
    stale_pass = make_verdict(req, verdict=Verdict.PASS)
    t.publish(
        make_verdict(
            req,
            verdict=Verdict.FAIL,
            findings=(Finding(finding_id="F1", category=FindingCategory.DEFECT),),
        )
    )
    p.pump()
    st = p.lineages["A"]
    assert st.phase is Phase.REPAIR_DISPATCHED
    # a no-op repair returns the SAME revision/tree
    rw = t.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=IMPL)
    assert rw is not None and rw.task_id != "A"
    t.publish(make_result(rw, executor_identity=IMPL, result_revision=REV1, result_tree=TREE1))
    p.pump()
    assert st.phase is Phase.VERIFYING
    t.publish(stale_pass)  # the old verifier PASS for the ORIGINAL request arrives late
    p.pump()
    assert st.phase is Phase.VERIFYING and "A" not in p.completed
    assert any(q[0] == "VERDICT" for q in p.quarantined)


def test_repair_task_id_collision_blocks_the_lineage_instead_of_corrupting_state():
    t = InMemoryTransport()
    p = planner(t)
    p.dispatch(qi("A"), **FIELDS)
    p._by_task["A-R1"] = "A-R1"  # defence in depth: dispatch refuses -R<n>, state may still clash
    run_role_implementer(t, [(REV1, TREE1)])
    p.pump()
    req = t.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity=VER)
    assert req is not None
    fail = Finding(finding_id="F1", category=FindingCategory.DEFECT)
    t.publish(make_verdict(req, verdict=Verdict.FAIL, findings=(fail,)))
    p.pump()
    assert p.lineages["A"].phase is Phase.BLOCKED
    assert "COLLISION" in p.lineages["A"].reason
    with pytest.raises(PlannerError):
        p.dispatch(qi("A"), **FIELDS)


@pytest.mark.parametrize("entry", ["secrets\\", "/secrets", "secrets/*", "a/../b", "x[ab]"])
def test_malformed_scope_entries_are_rejected_at_the_work_item(entry):
    with pytest.raises(ContractError):
        work(forbidden_paths=(entry,))
    with pytest.raises(ContractError):
        work(allowed_paths=(entry,))


def test_path_matching_is_case_and_unicode_normalised():
    w = work(allowed_paths=("Src/X",), forbidden_paths=("secrets",))
    for path in ("Secrets/a", "SECRETS/a"):
        f = Finding(finding_id="F", category=FindingCategory.DEFECT, paths=(path,))
        assert classify_finding(f, w) is FindingClass.OWNER_AUTHORITY_REQUIRED
    ok = Finding(finding_id="F", category=FindingCategory.DEFECT, paths=("src/x/a.py",))
    assert classify_finding(ok, w) is FindingClass.REPAIRABLE_WITHIN_AUTHORITY


def test_a_poisoned_wire_is_rejected_once_and_does_not_wedge_the_pump():
    t = InMemoryTransport()
    p = planner(t)
    p.dispatch(qi("A"), **FIELDS)
    w = p.lineages["A"].work
    t.publish(make_result(w, executor_identity=IMPL, result_revision=REV1, result_tree=TREE1))
    t._tamper_next(Channel.RESULT, ("vps1-impl", "vps1-evil"))
    p.pump()
    assert t.rejected and any(q[1] == "UNDECODABLE" for q in p.quarantined)
    p.pump()  # nothing left at the head of the channel; no repeated failure
    t.publish(make_result(w, executor_identity=IMPL, result_revision=REV2, result_tree=TREE2))
    p.pump()
    assert p.lineages["A"].phase is Phase.VERIFYING


def test_planner_rejects_non_canonical_identities_up_front():
    t = InMemoryTransport()
    with pytest.raises(PlannerError):
        Planner(t, identity="planner", verifier_identities=("ver a",))
    with pytest.raises(PlannerError):
        Planner(t, identity=" planner", verifier_identities=("ver-a",))


# ---- round-3 IV regressions ---------------------------------------------------------------


def test_dual_role_identity_is_not_wedged_by_a_request_addressed_to_someone_else():
    t = InMemoryTransport()
    wa = work(task_id="A", execution_id="A-E1", lineage_root="A")
    wb = work(task_id="B", execution_id="B-E1", lineage_root="B")
    t.publish(make_verification_request(wa, result(wa, who="V"), verifier_identity="W"))
    t.publish(make_verification_request(wb, result(wb, who=IMPL), verifier_identity="V"))
    got = t.claim(Channel.VERIFICATION, role=Role.VERIFIER, identity="V")
    assert got is not None and got.task_id == "B"


def test_sharp_s_is_not_ss_in_scope_matching():
    w = work(allowed_paths=("strasse",))
    f = Finding(finding_id="F", category=FindingCategory.DEFECT, paths=("straße/x",))
    assert classify_finding(f, w) is FindingClass.OWNER_AUTHORITY_REQUIRED


def test_quarantine_and_rejected_are_bounded_and_pump_does_not_spin():
    from project_atlas.orchestration.autonomy import dev_planner, dev_transport

    class Stuck:
        calls = 0

        def publish(self, record):
            return True

        def claim(self, channel, *, role, identity):
            Stuck.calls += 1
            raise TransportError("never consumes")

    p = Planner(Stuck(), identity="vps3-plan", verifier_identities=(VER,))
    p.pump()
    assert Stuck.calls <= 2 * dev_planner.MAX_RAISES_PER_PASS
    for i in range(dev_planner.MAX_QUARANTINE + 5):
        p._quarantine("RESULT", f"s{i}", "x")
    assert len(p.quarantined) == dev_planner.MAX_QUARANTINE
    assert p.quarantined[-1][1] == f"s{dev_planner.MAX_QUARANTINE + 4}"
    assert dev_transport.MAX_REJECTED > 0


def test_repair_suffix_and_reserved_fields_are_refused_at_dispatch():
    p = planner(InMemoryTransport())
    with pytest.raises(PlannerError, match="reserved"):
        p.dispatch(qi("X-R1"), **FIELDS)
    with pytest.raises(PlannerError, match="reserved"):
        p.dispatch(qi("Y"), task_id="other", **FIELDS)


def test_planner_verifier_list_rejects_duplicates_and_self():
    t = InMemoryTransport()
    with pytest.raises(PlannerError, match="duplicate"):
        Planner(t, identity="vps3-plan", verifier_identities=("v1", "V1"))
    with pytest.raises(PlannerError, match="own verifiers"):
        Planner(t, identity="vps3-plan", verifier_identities=("VPS3-plan",))


def test_round4_direct_sealed_records_keep_their_invariants():
    w = work()
    r = result(w)
    with pytest.raises(Exception, match="differ"):
        VerificationRequest(
            task_id=w.task_id,
            execution_id=w.execution_id,
            repository=w.repository,
            base_revision=BASE,
            result_revision=REV1,
            result_tree=TREE1,
            executor_identity=IMPL,
            verifier_identity=IMPL.upper(),
            result_seal=r.seal,
        ).sealed()
    req = make_verification_request(w, r, verifier_identity=VER)
    base = dict(
        task_id=req.task_id,
        execution_id=req.execution_id,
        verifier_identity=VER,
        result_revision=REV1,
        result_tree=TREE1,
        request_seal=req.seal,
    )
    secret = Finding(finding_id="F", category=FindingCategory.SECRET_REQUIRED)
    with pytest.raises(Exception, match="PASS cannot"):
        VerdictRecord(verdict=Verdict.PASS, findings=(secret,), **base).sealed()
    with pytest.raises(Exception, match="FAIL requires"):
        VerdictRecord(verdict=Verdict.FAIL, **base).sealed()


def test_result_with_foreign_execution_repository_or_base_is_quarantined():
    t = InMemoryTransport()
    p = planner(t)
    p.dispatch(qi("A"), **FIELDS)
    w = p.lineages["A"].work
    bad = ResultRecord(
        task_id="A",
        execution_id="OTHER",
        executor_identity=IMPL,
        repository=w.repository,
        base_revision=w.base_revision,
        result_revision=REV1,
        result_tree=TREE1,
        work_seal=w.seal,
    ).sealed()
    t.publish(bad)
    p.pump()
    assert p.lineages["A"].phase is Phase.DISPATCHED
    assert any("does not match" in q[2] for q in p.quarantined)


def test_poisoned_result_flood_does_not_starve_the_verdict_channel():
    class Flood:
        def __init__(self):
            self.left = {Channel.RESULT: 100, Channel.VERDICT: 3}

        def publish(self, record):
            return True

        def claim(self, channel, *, role, identity):
            if self.left[channel] > 0:
                self.left[channel] -= 1
                raise TransportError("poison")
            return None

    f = Flood()
    p = Planner(f, identity="vps3-plan", verifier_identities=(VER,))
    p.pump()
    assert f.left[Channel.VERDICT] == 0
    with pytest.raises(PlannerError):
        Planner(f, identity="vps3-plan", verifier_identities="abc")


def test_planner_copies_verifiers_and_rejects_non_string_identities():
    t = InMemoryTransport()
    vs = ["v1", "v2"]
    p = Planner(t, identity="plan", verifier_identities=tuple(vs))
    vs.append("plan")
    assert p.verifiers == ("v1", "v2")
    for bad in ([1], ("v1", 1)):
        with pytest.raises(PlannerError):
            Planner(t, identity="plan", verifier_identities=bad)


def test_planner_pump_survives_an_oserror_from_the_transport_without_spinning():
    class Broken:
        calls = 0

        def publish(self, record):
            return True

        def claim(self, channel, *, role, identity):
            Broken.calls += 1
            raise IsADirectoryError("claimed slot blocked")

    p = Planner(Broken(), identity="vps3-plan", verifier_identities=(VER,))
    p.pump()
    assert Broken.calls <= 2 * 64 and p.quarantined


def test_execution_ordinal_defaults_to_e1_and_yields_a_distinct_sealed_identity():
    p1, p2 = planner(InMemoryTransport()), planner(InMemoryTransport())
    w1 = p1.dispatch(qi("A"), **FIELDS)
    w2 = p2.dispatch(qi("A"), execution_ordinal=2, **FIELDS)
    assert w1.execution_id == "A-E1" and w2.execution_id == "A-E2"
    assert w1.seal != w2.seal and w1.task_id == w2.task_id == "A"


def test_execution_ordinal_below_one_is_refused():
    with pytest.raises(PlannerError):
        planner(InMemoryTransport()).dispatch(qi("A"), execution_ordinal=0, **FIELDS)


# ---- ATLAS-DEVQ-0006: write-scope collision admission ------------------------------------------


def _fields(*paths, **kw):
    d = dict(FIELDS)
    d["allowed_paths"] = tuple(paths)
    d.update(kw)
    return d


def scoped(t):
    return Planner(t, identity="vps3-plan", verifier_identities=(VER,))


def _work_records(t):
    return len(t._queues[Channel.WORK])


def _to_verifying(t, p):
    run_role_implementer(t, [(REV1, TREE1)])
    p.pump()


def _defect(req):
    return (
        Verdict.FAIL,
        (Finding(finding_id="F1", category=FindingCategory.DEFECT, paths=("src/x/a.py",)),),
    )


def test_scope_overlap_directory_boundary_semantics():
    from project_atlas.orchestration.autonomy.dev_contracts import scope_overlap

    assert scope_overlap(("src/a",), ("src/a/b.py",)) == (("src/a", "src/a/b.py"),)
    assert scope_overlap(("src/a/b.py",), ("src/a",)) == (("src/a/b.py", "src/a"),)
    assert scope_overlap(("src/a",), ("src/a",)) == (("src/a", "src/a"),)
    assert scope_overlap(("src/a",), ("src/ab",)) == ()
    assert scope_overlap(("src/a",), ("src/ab/a",)) == ()
    # trailing slash and redundant separators are the same entry
    assert scope_overlap(("src/a/",), ("src/a",)) == (("src/a", "src/a"),)
    assert scope_overlap(("src//a/",), ("./src/a/b",)) == (("src/a", "src/a/b"),)
    # case: lower() as in _matches -- variants collide, but sharp s is not "ss"
    assert scope_overlap(("SRC/A",), ("src/a/B.py",)) == (("src/a", "src/a/b.py"),)
    assert scope_overlap(("src/straße",), ("src/strasse",)) == ()
    # unicode: NFC as in norm_path -- composed and decomposed spellings are one entry
    assert scope_overlap(("src/café",), ("src/café/x",)) == (("src/café", "src/café/x"),)
    # empty scopes overlap nothing
    assert scope_overlap((), ()) == ()
    assert scope_overlap((), ("src/a",)) == ()
    assert scope_overlap(("src/a",), ()) == ()


def test_scope_overlap_is_symmetric_deduplicated_and_order_independent():
    from project_atlas.orchestration.autonomy.dev_contracts import scope_overlap

    a = ("src/b", "docs", "src/a/", "SRC/A", "tests/unit/t.py")
    b = ("src/a/x.py", "docs/adr/1.md", "src", "tests/unit", "other")
    ab = scope_overlap(a, b)
    assert ab == (
        ("docs", "docs/adr/1.md"),
        ("src/a", "src"),
        ("src/a", "src/a/x.py"),
        ("src/b", "src"),
        ("tests/unit/t.py", "tests/unit"),
    )
    assert scope_overlap(tuple(reversed(a)), tuple(reversed(b))) == ab
    assert scope_overlap(b, a) == tuple(sorted((y, x) for x, y in ab))


@pytest.mark.parametrize("bad", ["", "/abs", "../x", "a/../b", "src/*", "a\\b", "a\x00b", "/", "."])
def test_scope_overlap_malformed_entry_raises_never_no_overlap(bad):
    from project_atlas.orchestration.autonomy.dev_contracts import scope_overlap

    for a, b in (((bad,), ("src/a",)), (("src/a",), (bad,)), ((bad,), ()), ((), (bad,))):
        with pytest.raises(ContractError):
            scope_overlap(a, b)
    with pytest.raises(ContractError):
        scope_overlap((1,), ("src/a",))  # type: ignore[arg-type]


def test_works_collide_lineage_repository_and_seal_rules():
    from project_atlas.orchestration.autonomy.dev_contracts import works_collide

    a = work(task_id="A", execution_id="A-E1", lineage_root="A", allowed_paths=("src/x",))
    b = work(task_id="B", execution_id="B-E1", lineage_root="B", allowed_paths=("src/x/y.py",))
    assert works_collide(a, b) == (("src/x", "src/x/y.py"),)
    assert works_collide(b, a) == (("src/x/y.py", "src/x"),)
    # a different repository never collides
    other = work(task_id="B", execution_id="B-E1", lineage_root="B", repository="WezzSide/other")
    assert works_collide(a, other) == ()
    # a repair never collides with its own lineage (same scope by construction)
    r = result(a)
    f = Finding(finding_id="F1", category=FindingCategory.DEFECT, paths=("src/x/a.py",))
    rep = materialize_repair(a, _fail(a, r, f), result=r).repair_work
    assert rep is not None and rep.allowed_paths == a.allowed_paths
    assert works_collide(a, rep) == () and works_collide(rep, a) == ()
    # ...but that repair work still collides with a foreign lineage
    assert works_collide(rep, b) == (("src/x", "src/x/y.py"),)
    # the scope compared is allowed_paths, not forbidden_paths
    c = work(
        task_id="C",
        execution_id="C-E1",
        lineage_root="C",
        allowed_paths=("docs",),
        forbidden_paths=("src/x",),
    )
    assert works_collide(a, c) == ()


def test_works_collide_with_a_tampered_seal_raises():
    from project_atlas.orchestration.autonomy.dev_contracts import works_collide

    a = work(task_id="A", execution_id="A-E1", lineage_root="A")
    b = work(task_id="B", execution_id="B-E1", lineage_root="B", allowed_paths=("docs",))
    tampered = b.model_copy(update={"allowed_paths": ("other",)})  # seal no longer matches
    unsealed = b.model_copy(update={"seal": ""})
    for bad in (tampered, unsealed):
        with pytest.raises(ContractError):
            works_collide(a, bad)
        with pytest.raises(ContractError):
            works_collide(bad, a)
    # also when repository / lineage would short-circuit to "no collision"
    foreign = a.model_copy(update={"repository": "WezzSide/other"})
    with pytest.raises(ContractError):
        works_collide(a, foreign)
    with pytest.raises(ContractError):
        works_collide(a, a.model_copy(update={"task_id": "A2"}))


def test_planner_select_skips_in_flight_and_returns_next_ranked():
    t = InMemoryTransport()
    p = scoped(t)
    a, b = qi("A", severity=3), qi("B", severity=1)
    assert p.in_flight() == frozenset() and p.scope_holders() == ()
    wa = p.dispatch(a, **FIELDS)
    assert p.in_flight() == {"A"} and p.scope_holders() == (wa,)
    sel = p.select([a, b])
    assert sel.selected is not None and sel.selected.task_id == "B"
    assert sel.skipped == (("A", "IN_FLIGHT"),)


def test_second_dispatch_with_overlapping_scope_is_refused_and_nothing_published():
    t = InMemoryTransport()
    p = scoped(t)
    p.dispatch(qi("A", severity=3), **_fields("src/x", "docs/a"))
    before = (dict(p._by_task), dict(p._works), set(p.completed), dict(p.blocked))
    with pytest.raises(PlannerError) as exc:
        p.dispatch(qi("B"), **_fields("docs/a/n.md", "SRC/X/", "tests/t.py"))
    assert str(exc.value) == "SCOPE_COLLISION:A:docs/a/n.md|docs/a,src/x|src/x"
    assert _work_records(t) == 1
    assert len(p.lineages) == 1 and "B" not in p.lineages
    assert (dict(p._by_task), dict(p._works), set(p.completed), dict(p.blocked)) == before


def test_child_file_and_parent_directory_of_an_in_flight_scope_are_refused():
    t = InMemoryTransport()
    p = scoped(t)
    p.dispatch(qi("DIR"), **_fields("src/pkg"))
    with pytest.raises(PlannerError, match=r"^SCOPE_COLLISION:DIR:src/pkg/mod\.py\|src/pkg$"):
        p.dispatch(qi("CHILD"), **_fields("src/pkg/mod.py"))
    p.dispatch(qi("SIBLING"), **_fields("src/pkgs"))  # not on a directory boundary: admitted
    assert set(p.lineages) == {"DIR", "SIBLING"} and _work_records(t) == 2


def test_parent_directory_of_an_in_flight_file_is_refused():
    t = InMemoryTransport()
    p = scoped(t)
    p.dispatch(qi("FILE"), **_fields("src/pkg/mod.py"))
    with pytest.raises(PlannerError, match=r"^SCOPE_COLLISION:FILE:src\|src/pkg/mod\.py$"):
        p.dispatch(qi("PARENT"), **_fields("src"))
    assert _work_records(t) == 1 and set(p.lineages) == {"FILE"}


def test_collision_is_checked_on_the_sealed_work_scope_not_queue_metadata():
    t = InMemoryTransport()
    p = scoped(t)
    p.dispatch(qi("A", lane="one"), **_fields("src/x"))
    # queue metadata says "unrelated" (other lane, other title); the sealed scope overlaps
    with pytest.raises(PlannerError, match=r"^SCOPE_COLLISION:A:"):
        p.dispatch(qi("B", lane="two"), **_fields("src/x/deep/f.py"))
    # same lane, same category, disjoint sealed scope: admitted
    w = p.dispatch(qi("C", lane="one"), **_fields("src/y"))
    w.verify_seal()
    assert [h.task_id for h in p.scope_holders()] == ["A", "C"]
    assert _work_records(t) == 2


def test_first_colliding_holder_is_reported_in_lineage_root_order():
    t = InMemoryTransport()
    p = scoped(t)
    p.dispatch(qi("Z"), **_fields("src/z"))
    p.dispatch(qi("M"), **_fields("src/m"))
    with pytest.raises(PlannerError) as exc:
        p.dispatch(qi("N"), **_fields("src/z/a", "src/m/b", "src/m/a"))
    assert str(exc.value) == "SCOPE_COLLISION:M:src/m/a|src/m,src/m/b|src/m"


def test_collision_refusal_leaves_the_task_id_reusable_and_a_failure_report_opens_no_scope():
    t = InMemoryTransport()
    p = scoped(t)
    p.dispatch(qi("A"), **FIELDS)
    with pytest.raises(PlannerError, match=r"^SCOPE_COLLISION:A:src/x\|src/x$"):
        p.dispatch(qi("B"), **FIELDS)
    assert "B" not in p._by_task and "B" not in p._works and "B" not in p.blocked
    p.fail_execution("A", "runner lost")
    assert p.lineages["A"].phase is Phase.BLOCKED
    assert p.in_flight() == frozenset() and p.scope_holders() == ()
    # a failure report ends the lineage; it is not evidence that its executor stopped writing
    with pytest.raises(PlannerError, match=r"^SCOPE_RETAINED:A:src/x\|src/x$"):
        p.dispatch(qi("B"), **FIELDS)
    wb = p.dispatch(qi("B"), **_fields("src/y"))  # the task id itself stayed usable
    assert wb.task_id == "B" and p.lineages["B"].phase is Phase.DISPATCHED
    assert _work_records(t) == 2


def test_verifying_and_repair_dispatched_lineages_still_hold_scope():
    t = InMemoryTransport()
    p = scoped(t)
    p.dispatch(qi("A"), **FIELDS)
    _to_verifying(t, p)
    assert p.lineages["A"].phase is Phase.VERIFYING and p.in_flight() == {"A"}
    with pytest.raises(PlannerError, match=r"^SCOPE_COLLISION:A:"):
        p.dispatch(qi("B"), **FIELDS)
    run_role_verifier(t, _defect)
    p.pump()
    st = p.lineages["A"]
    assert st.phase is Phase.REPAIR_DISPATCHED and st.work.task_id == "A-R1"
    # the holder is the CURRENT (repair) work, reported under its lineage root
    assert p.scope_holders() == (st.work,) and p.in_flight() == {"A"}
    with pytest.raises(PlannerError, match=r"^SCOPE_COLLISION:A:src/x/n\.py\|src/x$"):
        p.dispatch(qi("B"), **_fields("src/x/n.py"))
    assert set(p.lineages) == {"A"}


def test_integration_ready_lineage_still_holds_scope():
    t = InMemoryTransport()
    p = scoped(t)
    a = qi("A", severity=3)
    p.dispatch(a, **FIELDS)
    _to_verifying(t, p)
    run_role_verifier(t, lambda req: (Verdict.PASS, ()))
    p.pump()
    assert p.lineages["A"].phase is Phase.INTEGRATION_READY and "A" in p.completed
    assert p.in_flight() == {"A"} and len(p.scope_holders()) == 1
    with pytest.raises(PlannerError, match=r"^SCOPE_COLLISION:A:src/x\|src/x$"):
        p.dispatch(qi("B"), **FIELDS)
    # selection keeps reporting it as completed (existing reason order), and B is selectable
    sel = p.select([a, qi("B")])
    assert sel.skipped == (("A", "ALREADY_COMPLETED"),)
    assert sel.selected is not None and sel.selected.task_id == "B"


def test_blocked_and_owner_required_lineages_keep_their_scope_closed():
    t = InMemoryTransport()
    p = scoped(t)
    p.dispatch(qi("A"), **FIELDS)
    _to_verifying(t, p)
    run_role_verifier(
        t,
        lambda req: (
            Verdict.FAIL,
            (Finding(finding_id="S", category=FindingCategory.SECRET_REQUIRED),),
        ),
    )
    p.pump()
    assert p.lineages["A"].phase is Phase.OWNER_REQUIRED
    assert p.in_flight() == frozenset() and p.scope_holders() == ()
    with pytest.raises(PlannerError, match=r"^SCOPE_RETAINED:A:src/x\|src/x$"):
        p.dispatch(qi("B"), **FIELDS)  # a verdict ended A; nothing verified a release
    p.dispatch(qi("B"), **_fields("src/y"))
    p.fail_execution("B", "boom")
    assert p.lineages["B"].phase is Phase.BLOCKED and p.in_flight() == frozenset()
    with pytest.raises(PlannerError, match=r"^SCOPE_RETAINED:B:src/y\|src/y$"):
        p.dispatch(qi("C"), **_fields("src/y"))
    p.dispatch(qi("C"), **_fields("src/z"))
    assert p.in_flight() == {"C"}


def test_different_repository_never_collides_in_the_planner():
    t = InMemoryTransport()
    p = scoped(t)
    p.dispatch(qi("A"), **FIELDS)
    p.dispatch(qi("B"), **_fields("src/x", repository="WezzSide/other"))
    assert p.in_flight() == {"A", "B"} and _work_records(t) == 2


def test_repository_case_variant_is_the_same_repository_and_collides():
    t = InMemoryTransport()
    p = scoped(t)
    p.dispatch(qi("A"), **FIELDS)
    with pytest.raises(PlannerError, match="SCOPE_COLLISION:A:"):
        p.dispatch(qi("B"), **_fields("src/x", repository=FIELDS["repository"].upper()))
    assert p.in_flight() == {"A"} and _work_records(t) == 1


def test_a_holder_with_a_broken_seal_fails_closed():
    t = InMemoryTransport()
    p = scoped(t)
    p.dispatch(qi("A"), **FIELDS)
    st = p.lineages["A"]
    st.work = st.work.model_copy(update={"allowed_paths": ("elsewhere",)})  # seal now stale
    with pytest.raises(ContractError) as exc:
        p.dispatch(qi("B"), **FIELDS)
    assert "seal mismatch" in str(exc.value)
    assert _work_records(t) == 1 and set(p.lineages) == {"A"}


def test_two_disjoint_scope_lineages_are_dispatched_and_proceed_independently():
    t = InMemoryTransport()
    p = scoped(t)
    a, b = qi("A", severity=3), qi("B", severity=1)
    first = p.select([a, b]).selected
    assert first is not None and first.task_id == "A"
    p.dispatch(first, **_fields("src/x"))
    second = p.select([a, b]).selected
    assert second is not None and second.task_id == "B"
    p.dispatch(second, **_fields("src/y"))
    assert _work_records(t) == 2 and p.in_flight() == {"A", "B"}
    assert p.select([a, b]).selected is None

    revs = [(REV1, TREE1), (REV2, TREE2)]
    wa = run_role_implementer(t, revs)
    wb = run_role_implementer(t, revs)
    assert (wa.task_id, wb.task_id) == ("A", "B")
    assert p.pump() == 2
    assert {r: s.phase for r, s in p.lineages.items()} == {
        "A": Phase.VERIFYING,
        "B": Phase.VERIFYING,
    }
    # A passes, B fails with an owner-only finding: neither outcome touches the other lineage
    by_task = {
        "A": (Verdict.PASS, ()),
        "B": (
            Verdict.FAIL,
            (Finding(finding_id="S", category=FindingCategory.SECRET_REQUIRED),),
        ),
    }
    run_role_verifier(t, lambda req: by_task[req.task_id])
    run_role_verifier(t, lambda req: by_task[req.task_id])
    assert p.pump() == 2
    assert p.lineages["A"].phase is Phase.INTEGRATION_READY
    assert p.lineages["B"].phase is Phase.OWNER_REQUIRED
    assert p.completed == {"A"} and set(p.blocked) == {"B"}
    assert p.in_flight() == {"A"} and not p.quarantined


def test_scope_admission_cannot_be_switched_off():
    t = InMemoryTransport()
    p = planner(t)  # the ordinary constructor: there is no opt-out
    p.dispatch(qi("A"), **FIELDS)
    with pytest.raises(PlannerError, match=r"^SCOPE_COLLISION:A:"):
        p.dispatch(qi("B"), **FIELDS)
    assert _work_records(t) == 1 and p.in_flight() == {"A"}
    with pytest.raises(PlannerError, match="lineage/task id already in use"):
        p.dispatch(qi("A"), **FIELDS)  # repeated dispatch keeps its existing refusal
    with pytest.raises(TypeError):
        Planner(t, identity="vps3-plan", verifier_identities=(VER,), scope_admission=False)  # type: ignore[call-arg]


# ---- ATLAS-DEVQ-0010: repository identity ---------------------------------------------------

_SPELLINGS = (
    "WezzSide/project-atlas",
    "wezzside/PROJECT-ATLAS",
    "WezzSide/project-atlas/",
    "WezzSide/project-atlas.git",
    "WezzSide/project-atlas.git/",
    "github.com/WezzSide/project-atlas",
    "GitHub.com/WezzSide/project-atlas.git",
    "https://github.com/WezzSide/project-atlas",
    "https://github.com/WezzSide/project-atlas/",
    "https://github.com/WezzSide/project-atlas.git",
    "HTTPS://GITHUB.COM/WezzSide/project-atlas.git/",
    "http://github.com/WezzSide/project-atlas",
    "ssh://git@github.com/WezzSide/project-atlas.git",
    "git@github.com:WezzSide/project-atlas.git",
    "git@github.com:WezzSide/project-atlas",
)

_UNSUPPORTED = (
    "",
    " WezzSide/project-atlas",
    "WezzSide/project-atlas ",
    "WezzSide/project-atlas\n",
    "WezzSide/project-atlas//",
    "WezzSide//project-atlas",
    "/WezzSide/project-atlas",
    "WezzSide",
    "WezzSide/",
    "WezzSide/project-atlas/tree/main",
    "WezzSide/project-atlas.git.git",
    "WezzSide/project-atlas.GIT",
    "WezzSide/.git",
    "WezzSide/.",
    "WezzSide/..",
    "WezzSide/project atlas",
    "WezzSide/project-atlas?x=1",
    "WezzSide/project-atlas#readme",
    "Wezz.Side/project-atlas",
    "-WezzSide/project-atlas",
    "WezzSidé/project-atlas",
    "https://gitlab.com/WezzSide/project-atlas",
    "https://github.com.evil.example/WezzSide/project-atlas",
    "https://evil.example/github.com/WezzSide/project-atlas",
    "https://github.com:443/WezzSide/project-atlas",
    "https://user@github.com/WezzSide/project-atlas",
    "https://user:pw@github.com/WezzSide/project-atlas",
    "https://www.github.com/WezzSide/project-atlas",
    "https://githubxcom/WezzSide/project-atlas",
    "git@github-com:WezzSide/project-atlas",
    "https://api.github.com/repos/WezzSide/project-atlas",
    "https://github.com/WezzSide/project-atlas/pull/1",
    "https://github.com/WezzSide",
    "https:/github.com/WezzSide/project-atlas",
    "ftp://github.com/WezzSide/project-atlas",
    "git://github.com/WezzSide/project-atlas",
    "ssh://github.com/WezzSide/project-atlas",
    "ssh://git@github.com:22/WezzSide/project-atlas",
    "ssh://root@github.com/WezzSide/project-atlas",
    "git@github.com/WezzSide/project-atlas",
    "git@gitlab.com:WezzSide/project-atlas",
    "root@github.com:WezzSide/project-atlas",
    "github.com:WezzSide/project-atlas",
    "a" * 40 + "/project-atlas",
    "WezzSide/" + "a" * 101,
)


def test_every_supported_spelling_of_a_repository_has_one_key():
    from project_atlas.orchestration.autonomy.dev_contracts import repository_key

    assert {repository_key(s) for s in _SPELLINGS} == {"wezzside/project-atlas"}
    assert repository_key("a/b") == "a/b" and repository_key("A/b.c_d-e") == "a/b.c_d-e"
    assert repository_key("WezzSide/other") != repository_key("WezzSide/project-atlas")
    assert repository_key("o/git") == "o/git" and repository_key("o/x.github") == "o/x.github"
    assert repository_key("a" * 39 + "/" + "b" * 100) == "a" * 39 + "/" + "b" * 100


@pytest.mark.parametrize("spelling", [*_UNSUPPORTED, None, 5, b"WezzSide/project-atlas"])
def test_an_unsupported_repository_identity_fails_closed(spelling):
    from project_atlas.orchestration.autonomy.dev_contracts import repository_key

    with pytest.raises(ContractError, match="unsupported repository identity"):
        repository_key(spelling)


def test_a_repository_spelling_is_not_an_independent_collision_domain():
    from project_atlas.orchestration.autonomy.dev_contracts import works_collide

    def w(task, repository):
        return make_work(
            task_id=task,
            execution_id=f"{task}-E1",
            lineage_root=task,
            **_fields("src/x", repository=repository),
        )

    a = w("A", _SPELLINGS[0])
    for i, spelling in enumerate(_SPELLINGS):
        b = w(f"B{i}", spelling)
        assert works_collide(a, b) == (("src/x", "src/x"),) == works_collide(b, a)
        assert b.repository == spelling  # the sealed field keeps what was written
    assert works_collide(a, w("C", "WezzSide/other")) == ()
    # an identity that cannot be keyed is an error, never "another repository"
    odd = w("D", "https://gitlab.com/WezzSide/project-atlas")
    for pair in ((a, odd), (odd, a)):
        with pytest.raises(ContractError, match="unsupported repository identity"):
            works_collide(*pair)


def test_the_repository_key_does_not_change_a_sealed_work_item():
    """The sealed field and the seal are what they were: the key is derived, never stored."""
    from project_atlas.orchestration.autonomy.dev_contracts import WorkItem

    assert "repository_key" not in WorkItem.model_fields
    a = make_work(task_id="A", execution_id="A-E1", lineage_root="A", **FIELDS)
    b = make_work(
        task_id="A",
        execution_id="A-E1",
        lineage_root="A",
        **{**FIELDS, "repository": FIELDS["repository"] + ".git"},
    )
    assert a.seal != b.seal and b.repository.endswith(".git")  # two records, one repository


def test_the_planner_treats_every_spelling_as_the_same_repository():
    t = InMemoryTransport()
    p = scoped(t)
    p.dispatch(qi("A"), **FIELDS)
    for i, spelling in enumerate(_SPELLINGS):
        with pytest.raises(PlannerError, match=r"^SCOPE_COLLISION:A:src/x\|src/x$"):
            p.dispatch(qi(f"B{i}"), **_fields("src/x", repository=spelling))
    p.fail_execution("A", "runner lost")
    for i, spelling in enumerate(_SPELLINGS):
        with pytest.raises(PlannerError, match=r"^SCOPE_RETAINED:A:src/x\|src/x$"):
            p.dispatch(qi(f"B{i}"), **_fields("src/x", repository=spelling))
    assert _work_records(t) == 1 and set(p.lineages) == {"A"}
    # a disjoint scope in another spelling is still admitted, and keeps its own spelling
    w = p.dispatch(qi("C"), **_fields("src/y", repository=_SPELLINGS[9]))
    assert w.repository == _SPELLINGS[9]


def test_the_planner_refuses_a_repository_identity_it_cannot_key():
    t = InMemoryTransport()
    p = scoped(t)
    for bad in ("https://gitlab.com/WezzSide/project-atlas", "WezzSide/project-atlas.git.git"):
        with pytest.raises(PlannerError, match=r"^REPOSITORY_UNSUPPORTED:"):
            p.dispatch(qi("A"), **_fields("src/x", repository=bad))  # no earlier lineage
    p.dispatch(qi("A"), **FIELDS)
    with pytest.raises(PlannerError, match=r"^REPOSITORY_UNSUPPORTED:"):
        p.dispatch(qi("B"), **_fields("src/q", repository=" WezzSide/project-atlas"))
    assert _work_records(t) == 1 and p.state.seq == 1 and set(p.lineages) == {"A"}
