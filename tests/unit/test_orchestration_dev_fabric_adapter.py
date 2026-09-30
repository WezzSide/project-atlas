"""Fabric adapter against a fake GitHub port: the real loop hops, minus the network."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from project_atlas.orchestration.autonomy.dev_contracts import Role, make_work
from project_atlas.orchestration.autonomy.dev_crosswalk import Crosswalk
from project_atlas.orchestration.autonomy.dev_fabric_adapter import (
    AdapterError,
    CheckRun,
    CompareInfo,
    FabricAdapter,
    RunInfo,
    build_dispatch_payload,
)
from project_atlas.orchestration.autonomy.dev_planner import Phase, Planner
from project_atlas.orchestration.autonomy.dev_queue import Category, QueueItem
from project_atlas.orchestration.autonomy.dev_spool_transport import SpoolTransport
from project_atlas.orchestration.autonomy.dev_transport import Channel, encode

BASE = "a" * 40
R1, T1, R2, T2 = "b" * 40, "c" * 40, "d" * 40, "e" * 40
FIELDS = dict(
    repository="WezzSide/project-atlas",
    base_revision=BASE,
    authority_ref="AUTH-1",
    allowed_paths=("src/x/", "tests/unit/"),
    forbidden_paths=("infra/atlas-runner/controller",),
    acceptance_contract=("task tests pass",),
)


@dataclass
class FakeGitHub:
    branches: dict[str, str] = field(default_factory=lambda: {"main": BASE})
    trees: dict[str, str] = field(default_factory=dict)
    runs: list[RunInfo] = field(default_factory=list)
    dispatches: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)
    files: tuple[str, ...] = ("src/x/a.py", "tests/unit/test_a.py")
    merge_base: str = BASE
    checks: dict[str, dict[str, tuple[str, str | None]]] = field(default_factory=dict)
    verify_reports: dict[int, dict[str, object]] = field(default_factory=dict)
    verify_runs: list[RunInfo] = field(default_factory=list)
    prs: list[str] = field(default_factory=list)
    n: int = 100

    def dispatch_workflow(self, workflow, ref, inputs):
        self.dispatches.append((workflow, ref, inputs))

    def list_runs(self, workflow, *, event, created_after):
        return list(self.verify_runs if event == "workflow_run" else self.runs)

    def get_run(self, run_id):
        return next(r for r in self.runs if r.run_id == run_id)

    def branch_head(self, branch):
        return self.branches.get(branch)

    def commit_tree(self, sha):
        return self.trees[sha]

    def compare(self, base, head):
        return CompareInfo(self.merge_base, self.files)

    def artifact_digests(self, run_id, name):
        return ("f" * 64,)

    def read_json_artifact(self, run_id, name, member):
        if run_id not in self.verify_reports:
            raise AdapterError("no artifact")
        return self.verify_reports[run_id]

    def check_runs(self, sha):
        v = self.checks.get(sha, {})
        if isinstance(v, list):
            return v
        return [CheckRun(n, st, c) for n, (st, c) in v.items()]

    def ensure_draft_pr(self, head_branch, base, title, body):
        if head_branch not in self.prs:
            self.prs.append(head_branch)
        return len(self.prs)

    # scenario helpers ---------------------------------------------------------------
    def executor_finishes(self, rev, tree, *, conclusion="success"):
        self.n += 1
        rid = self.n
        self.runs.append(
            RunInfo(
                rid,
                1,
                "atlas-agent-execute.yml",
                "workflow_dispatch",
                "completed",
                conclusion,
                "main",
                "2026-09-30T16:00:00Z",
            )
        )
        if rev:
            self.branches[f"atlas/agent-{rid}-1"] = rev
            self.trees[rev] = tree
        return rid

    def verifier_runs(self, source, verdict):
        vid = source + 1000
        self.verify_runs.append(
            RunInfo(
                vid,
                1,
                "atlas-runner-verify.yml",
                "workflow_run",
                "completed",
                "success",
                "main",
                "2026-09-30T16:05:00Z",
            )
        )
        self.verify_reports[vid] = {"source_run_id": source, "verdict": verdict}


def statement(w):
    return ("Fix the thing.", ("PYTHONPATH=src python -m pytest tests/unit/test_a.py -q --no-cov",))


def build(tmp_path, gh):
    spool = SpoolTransport(tmp_path / "spool")
    xw = Crosswalk(tmp_path / "xw.jsonl")
    adapter = FabricAdapter(
        gh,
        spool,
        xw,
        pending_dir=tmp_path / "pending",
        clock=lambda: "2026-09-30T15:59:00Z",
        task_statement=statement,
        required_checks=frozenset({"quality"}),
    )
    planner = Planner(spool, identity="planner", verifier_identities=(adapter.verifier_identity,))
    item = QueueItem(task_id="DEVQ-1", title="t", category=Category.RELIABILITY, severity=2)
    planner.dispatch(item, **FIELDS)
    return spool, xw, adapter, planner


def test_payload_is_deterministic_bounded_and_demands_task_specific_tests():
    w = make_work(task_id="T", execution_id="T-E1", lineage_root="T", **FIELDS)
    p1 = build_dispatch_payload(
        w, base_branch="main", task_statement="s", acceptance_commands=("c",)
    )
    p2 = build_dispatch_payload(
        w, base_branch="main", task_statement="s", acceptance_commands=("c",)
    )
    assert p1 == p2 and p1.sha256() == p2.sha256()
    prompt = p1.inputs["task_prompt"]
    assert w.seal in prompt and BASE in prompt and "run: c" in prompt
    assert "NOT sufficient" in prompt and "infra/atlas-runner/controller" in prompt
    assert p1.workflow == "atlas-agent-execute.yml" and p1.ref == "main"
    with pytest.raises(AdapterError):
        build_dispatch_payload(w, base_branch="../x", task_statement="s", acceptance_commands=())


def test_first_pass_full_cycle_to_integration_ready(tmp_path):
    gh = FakeGitHub()
    _spool, _xw, ad, pl = build(tmp_path, gh)
    assert ad.tick() == ["ACCEPTED:DEVQ-1", "DISPATCHED:DEVQ-1"]
    assert len(gh.dispatches) == 1
    assert ad.tick() == []  # run not visible yet: pending, never re-dispatched
    rid = gh.executor_finishes(R1, T1)
    assert "RESULT:DEVQ-1" in ad.tick()
    pl.pump()
    assert pl.lineages["DEVQ-1"].phase is Phase.VERIFYING
    assert "VERIFICATION_ACCEPTED" in ad.tick()  # verify + CI still pending
    gh.verifier_runs(rid, "VERIFIED")
    gh.checks[R1] = {"quality": ("completed", "success"), "control-plane": ("completed", "success")}
    assert "VERDICT:DEVQ-1" in ad.tick()
    pl.pump()
    assert pl.lineages["DEVQ-1"].phase is Phase.INTEGRATION_READY
    assert gh.prs == [f"atlas/agent-{rid}-1"] and len(gh.dispatches) == 1


def test_ci_failure_becomes_repairable_finding_then_repair_runs_on_result_branch(tmp_path):
    gh = FakeGitHub()
    _spool, _xw, ad, pl = build(tmp_path, gh)
    ad.tick()
    rid = gh.executor_finishes(R1, T1)
    ad.tick()
    pl.pump()
    ad.tick()
    gh.verifier_runs(rid, "VERIFIED")
    gh.checks[R1] = {"quality": ("completed", "failure")}
    assert "VERDICT:DEVQ-1" in ad.tick()
    pl.pump()
    st = pl.lineages["DEVQ-1"]
    assert st.phase is Phase.REPAIR_DISPATCHED and st.work.attempt == 2
    gh.branches[f"atlas/agent-{rid}-1"] = R1
    ev = ad.tick()  # accepts the repair work and dispatches it from the failed result branch
    assert "ACCEPTED:DEVQ-1-R1" in ev
    _wf, ref, inputs = gh.dispatches[-1]
    assert ref == "main" and inputs["base_branch"] == f"atlas/agent-{rid}-1"
    assert "RESOLVE:CI-quality" in inputs["task_prompt"]
    gh.merge_base = R1
    rid2 = gh.executor_finishes(R2, T2)
    gh.runs[-1] = RunInfo(
        rid2,
        1,
        "atlas-agent-execute.yml",
        "workflow_dispatch",
        "completed",
        "success",
        "main",
        "2026-09-30T16:10:00Z",
    )
    assert "RESULT:DEVQ-1-R1" in ad.tick()
    pl.pump()
    ad.tick()
    gh.verifier_runs(rid2, "VERIFIED")
    gh.checks[R2] = {"quality": ("completed", "success")}
    assert "VERDICT:DEVQ-1-R1" in ad.tick()
    pl.pump()
    assert st.phase is Phase.INTEGRATION_READY


def test_dispatch_is_at_most_once_even_across_adapter_restart(tmp_path):
    gh = FakeGitHub()
    spool, _xw, ad, _pl = build(tmp_path, gh)
    ad.tick()
    ad2 = FabricAdapter(
        gh,
        spool,
        Crosswalk(tmp_path / "xw.jsonl"),
        pending_dir=tmp_path / "pending",
        clock=lambda: "2026-09-30T15:59:00Z",
        task_statement=statement,
        required_checks=frozenset({"quality"}),
    )
    ad2.tick()
    assert len(gh.dispatches) == 1
    with pytest.raises(AdapterError, match="already dispatched"):
        ad2.dispatch(next(iter(ad2.works.values())))


def test_base_drift_blocks_dispatch(tmp_path):
    gh = FakeGitHub(branches={"main": "9" * 40})
    _, _, ad, _ = build(tmp_path, gh)
    events = ad.tick()  # terminal for this work item, never an exception that wedges the tick
    assert any(e.startswith("EXECUTION_FAILED:DEVQ-1") and "sealed base" in e for e in events)
    assert gh.dispatches == [] and ad.works == {}
    assert ad.tick() == []


def test_ambiguous_run_is_refused_and_failed_run_blocks_only_that_lineage(tmp_path):
    gh = FakeGitHub()
    _, _, ad, _pl = build(tmp_path, gh)
    ad.tick()
    gh.executor_finishes(None, "")
    gh.executor_finishes(None, "")
    assert any("EXECUTION_FAILED:DEVQ-1:ambiguous" in e for e in ad.tick())
    gh2 = FakeGitHub()
    _, _, ad2, _ = build(tmp_path / "b", gh2)
    ad2.tick()
    gh2.executor_finishes(None, "", conclusion="failure")
    assert any(e.startswith("EXECUTION_FAILED") for e in ad2.tick())


def test_success_without_result_branch_or_foreign_ancestry_is_not_a_result(tmp_path):
    gh = FakeGitHub()
    _, _, ad, _ = build(tmp_path, gh)
    ad.tick()
    gh.executor_finishes(None, "")
    assert any("no result branch" in e for e in ad.tick())
    gh2 = FakeGitHub(merge_base="8" * 40)
    _, _, ad2, _ = build(tmp_path / "b", gh2)
    ad2.tick()
    gh2.executor_finishes(R1, T1)
    assert any("EXECUTION_FAILED:DEVQ-1" in e and "descend" in e for e in ad2.tick())


def test_out_of_scope_change_never_becomes_a_result_and_blocks_only_that_lineage(tmp_path):
    gh = FakeGitHub(files=("src/x/a.py", "src/other.py"))
    _spool, _, ad, _pl = build(tmp_path, gh)
    ad.tick()
    gh.executor_finishes(R1, T1)
    events = ad.tick()
    assert any(e.startswith("EXECUTION_FAILED:DEVQ-1") and "allowed_paths" in e for e in events)
    assert ad.xw.hop(next(iter(ad.xw._rows)), "RESULT") is None
    assert ad.works == {}


def test_unestablished_or_pending_evidence_never_yields_a_verdict(tmp_path):
    gh = FakeGitHub()
    _spool, _xw, ad, pl = build(tmp_path, gh)
    ad.tick()
    rid = gh.executor_finishes(R1, T1)
    ad.tick()
    pl.pump()
    ad.tick()
    gh.checks[R1] = {"quality": ("completed", "success")}
    assert "VERDICT:DEVQ-1" not in ad.tick()  # verifier run absent
    gh.verifier_runs(rid, "UNESTABLISHED")
    assert "VERDICT:DEVQ-1" not in ad.tick()
    pl.pump()
    assert pl.lineages["DEVQ-1"].phase is Phase.VERIFYING


def test_rejected_verifier_evidence_fails_even_with_green_ci_and_moved_branch_is_refused(tmp_path):
    gh = FakeGitHub()
    _spool, _xw, ad, pl = build(tmp_path, gh)
    ad.tick()
    rid = gh.executor_finishes(R1, T1)
    ad.tick()
    pl.pump()
    ad.tick()
    gh.checks[R1] = {"quality": ("completed", "success")}
    gh.branches[f"atlas/agent-{rid}-1"] = R2  # artifact identity changed after ingestion
    assert any("VERDICT_ERROR:DEVQ-1" in e and "moved" in e for e in ad.tick())
    gh.branches[f"atlas/agent-{rid}-1"] = R1
    gh.verifier_runs(rid, "REJECTED")
    assert "VERDICT:DEVQ-1" in ad.tick()
    pl.pump()
    assert pl.lineages["DEVQ-1"].phase is Phase.REPAIR_DISPATCHED


# ---- IV #1034 repairs ------------------------------------------------------------------------


def _to_verifying(tmp_path, gh):
    spool, xw, ad, pl = build(tmp_path, gh)
    ad.tick()
    rid = gh.executor_finishes(R1, T1)
    ad.tick()
    pl.pump()
    ad.tick()
    return spool, xw, ad, pl, rid


def test_partial_check_set_never_yields_a_verdict(tmp_path):
    gh = FakeGitHub()
    _s, _x, ad, _pl, rid = _to_verifying(tmp_path, gh)
    gh.verifier_runs(rid, "VERIFIED")
    gh.checks[R1] = {"lint": ("completed", "success")}  # required 'quality' not reported yet
    assert "VERDICT:DEVQ-1" not in ad.tick()
    gh.checks[R1] = {"quality": ("in_progress", None)}
    assert "VERDICT:DEVQ-1" not in ad.tick()


def test_check_name_collision_cannot_mask_a_failure(tmp_path):
    gh = FakeGitHub()
    _s, _x, ad, pl, rid = _to_verifying(tmp_path, gh)
    gh.verifier_runs(rid, "VERIFIED")
    gh.checks[R1] = [
        CheckRun("quality", "completed", "failure"),
        CheckRun("quality", "completed", "success"),
    ]
    assert "VERDICT:DEVQ-1" in ad.tick()
    pl.pump()
    assert pl.lineages["DEVQ-1"].phase is Phase.REPAIR_DISPATCHED


def test_required_check_must_be_success_not_merely_skipped(tmp_path):
    gh = FakeGitHub()
    _s, _x, ad, pl, rid = _to_verifying(tmp_path, gh)
    gh.verifier_runs(rid, "VERIFIED")
    gh.checks[R1] = [CheckRun("quality", "completed", "skipped")]
    assert "VERDICT:DEVQ-1" in ad.tick()
    pl.pump()
    assert pl.lineages["DEVQ-1"].phase is Phase.REPAIR_DISPATCHED


def test_crash_between_result_bind_and_publish_is_recovered(tmp_path, monkeypatch):
    gh = FakeGitHub()
    spool, _xw, ad, pl = build(tmp_path, gh)
    ad.tick()
    gh.executor_finishes(R1, T1)
    orig = spool.publish
    calls = {"n": 0}

    def flaky(rec):
        if rec.KIND.value == "RESULT" and calls["n"] == 0:
            calls["n"] += 1
            raise RuntimeError("crash")
        return orig(rec)

    monkeypatch.setattr(spool, "publish", flaky)
    assert any(e.startswith("UNEXPECTED:DEVQ-1:RuntimeError") for e in ad.tick())
    monkeypatch.undo()
    ad2 = FabricAdapter(
        gh,
        spool,
        Crosswalk(tmp_path / "xw.jsonl"),
        pending_dir=tmp_path / "pending",
        clock=lambda: "2026-09-30T15:59:00Z",
        task_statement=statement,
        required_checks=frozenset({"quality"}),
    )
    assert "RESULT_REPUBLISHED:DEVQ-1" in ad2.tick()
    pl.pump()
    assert pl.lineages["DEVQ-1"].phase is Phase.VERIFYING


def test_second_work_is_deferred_until_the_first_is_bound_to_a_run(tmp_path):
    gh = FakeGitHub()
    _spool, _xw, ad, pl = build(tmp_path, gh)
    item = QueueItem(task_id="DEVQ-2", title="t", category=Category.RELIABILITY, severity=1)
    pl.dispatch(item, **FIELDS)
    ev = ad.tick()
    assert ev.count("DISPATCHED:DEVQ-1") + ev.count("DISPATCHED:DEVQ-2") == 1
    assert len(gh.dispatches) == 1
    gh.executor_finishes(None, "", conclusion="failure")  # first run appears -> bound
    ad.tick()
    ad.tick()
    assert len(gh.dispatches) == 2  # the deferred one went out after the binding


def test_dispatch_api_failure_blocks_only_that_lineage_and_is_never_retried(tmp_path):
    gh = FakeGitHub()

    def boom(*a):
        raise AdapterError("503")

    gh.dispatch_workflow = boom
    _s, _xw, ad, _pl = build(tmp_path, gh)
    ev = ad.tick()
    assert any(e.startswith("EXECUTION_FAILED:DEVQ-1") and "dispatch failed" in e for e in ev)
    assert ad.works == {}
    assert ad.tick() == []


def test_verification_request_must_match_the_crosswalked_result(tmp_path):
    gh = FakeGitHub()
    _spool, _xw, ad, _pl, rid = _to_verifying(tmp_path, gh)
    req = ad.pending_verifications()[0]
    forged = req.model_copy(update={"result_seal": "z" * 16}).sealed()
    gh.verifier_runs(rid, "VERIFIED")
    gh.checks[R1] = {"quality": ("completed", "success")}
    with pytest.raises(AdapterError, match="crosswalked"):
        ad.collect_verdict(next(iter(ad.works.values())), forged)


def test_string_source_run_id_matches_and_conflicting_reports_are_refused(tmp_path):
    gh = FakeGitHub()
    _s, _x, ad, _pl, rid = _to_verifying(tmp_path, gh)
    gh.verifier_runs(rid, "VERIFIED")
    gh.verify_reports[rid + 1000]["source_run_id"] = str(rid)
    gh.checks[R1] = {"quality": ("completed", "success")}
    assert "VERDICT:DEVQ-1" in ad.tick()


def test_run_already_bound_to_a_failed_lineage_is_never_adopted_by_the_next_dispatch(tmp_path):
    gh = FakeGitHub()
    _spool, xw, ad, pl = build(tmp_path, gh)
    pl.dispatch(QueueItem(task_id="DEVQ-2", title="t", category=Category.RELIABILITY), **FIELDS)
    ad.tick()  # dispatches exactly one work item (serialised)
    first = next(iter(ad.works))
    gh.executor_finishes(None, "", conclusion="failure")  # its run fails fast
    ev = ad.tick()  # first lineage fails; the other dispatches in the same tick
    assert any(e.startswith("EXECUTION_FAILED") for e in ev)
    ad.tick()
    bound = [xw.hop(w.seal, "RUN") for w in ad.works.values() if xw.hop(w.seal, "RUN") is not None]
    assert first not in ad.works and bound == []  # the survivor did NOT adopt the failed run
    ids = [h["run_id"] for h in map(lambda r: r, [xw.hop(first, "RUN")]) if h]
    assert len(ids) == 1
    with pytest.raises(Exception, match="already bound"):
        xw.bind_run(next(iter(ad.works)), run_id=ids[0], run_attempt=1, branch="atlas/agent-1-1")


def test_lost_dispatch_times_out_instead_of_blocking_the_fabric_forever(tmp_path):
    gh = FakeGitHub()
    now = {"t": "2026-09-30T15:59:00Z"}
    spool = SpoolTransport(tmp_path / "spool")
    xw = Crosswalk(tmp_path / "xw.jsonl")
    ad = FabricAdapter(
        gh,
        spool,
        xw,
        pending_dir=tmp_path / "pending",
        clock=lambda: now["t"],
        task_statement=statement,
        required_checks=frozenset({"quality"}),
    )
    pl = Planner(spool, identity="planner", verifier_identities=(ad.verifier_identity,))
    pl.dispatch(QueueItem(task_id="DEVQ-1", title="t", category=Category.RELIABILITY), **FIELDS)
    ad.tick()
    assert ad.tick() == []  # still within the deadline
    now["t"] = "2026-09-30T16:30:00Z"  # 31 minutes later, no run ever appeared
    assert any("no workflow run appeared" in e for e in ad.tick())
    assert ad.works == {}


def test_stale_or_unvalidated_verdict_file_is_never_republished(tmp_path):
    gh = FakeGitHub()
    _spool, _xw, ad, pl, _rid = _to_verifying(tmp_path, gh)
    req = ad.pending_verifications()[0]
    from project_atlas.orchestration.autonomy.dev_contracts import Verdict, make_verdict
    from project_atlas.orchestration.autonomy.dev_transport import encode

    v = make_verdict(req, verdict=Verdict.PASS)
    (tmp_path / "pending" / f"verdict-{v.seal}.json").write_text(encode(v))
    assert any(e.startswith("VERDICT_DISCARDED") for e in ad.tick())
    assert pl.lineages["DEVQ-1"].phase is Phase.VERIFYING
    pl.pump()
    assert pl.lineages["DEVQ-1"].phase is Phase.VERIFYING


# ---- round-3 IV regressions ---------------------------------------------------------------


def _late(iso):
    return lambda: iso


def test_dropped_lineage_keeps_blocking_so_its_late_run_is_never_adopted(tmp_path):
    class Flaky(FakeGitHub):
        def dispatch_workflow(self, workflow, ref, inputs):
            super().dispatch_workflow(workflow, ref, inputs)
            if len(self.dispatches) == 1:
                raise AdapterError("timeout after GitHub already accepted it")

    gh = Flaky()
    _, _, ad, pl = build(tmp_path, gh)
    pl.dispatch(QueueItem(task_id="DEVQ-2", title="t", category=Category.RELIABILITY), **FIELDS)
    ev = ad.tick()
    assert any(e.startswith("EXECUTION_FAILED:DEVQ-1") for e in ev)
    assert len(gh.dispatches) == 1  # DEVQ-2 deferred: the ledger still has an unbound dispatch
    gh.executor_finishes(R1, T1)  # W1's late run surfaces
    assert not any(e.startswith("DISPATCHED") for e in ad.tick())
    assert ad.xw.bound_run_ids() == frozenset()  # and nobody adopted it
    ad.clock = _late("2026-09-30T16:30:00Z")  # dispatch deadline elapsed
    assert any(e.startswith("DISPATCHED:DEVQ-2") for e in ad.tick())


def test_a_run_is_never_located_in_the_tick_that_dispatched_it(tmp_path):
    gh = FakeGitHub()
    gh.executor_finishes(R1, T1)  # a pre-existing foreign run is visible immediately
    _, _, ad, _ = build(tmp_path, gh)
    assert ad.tick() == ["ACCEPTED:DEVQ-1", "DISPATCHED:DEVQ-1"]
    assert ad.xw.bound_run_ids() == frozenset()


def test_transient_port_error_keeps_the_work_and_does_not_wedge_other_works(tmp_path):
    class Down(FakeGitHub):
        up = False

        def list_runs(self, workflow, *, event, created_after):
            if not self.up and event == "workflow_dispatch":
                raise AdapterError("GitHub 502")
            return super().list_runs(workflow, event=event, created_after=created_after)

    gh = Down()
    _, _, ad, _ = build(tmp_path, gh)
    ad.tick()
    assert any(e.startswith("TRANSIENT:DEVQ-1") for e in ad.tick())
    assert len(ad.works) == 1
    gh.up = True
    gh.executor_finishes(R1, T1)
    assert "RESULT:DEVQ-1" in ad.tick()


def test_rerun_of_the_executor_run_is_not_accepted(tmp_path):
    gh = FakeGitHub()
    _, _, ad, _ = build(tmp_path, gh)
    ad.tick()
    gh.runs.append(
        RunInfo(
            101,
            1,
            "atlas-agent-execute.yml",
            "workflow_dispatch",
            "in_progress",
            None,
            "main",
            "2026-09-30T16:00:00Z",
        )
    )
    gh.branches["atlas/agent-101-1"] = R1
    gh.trees[R1] = T1
    assert ad.tick() == []  # bound at attempt 1, still running
    gh.runs[0] = RunInfo(
        101,
        2,
        "atlas-agent-execute.yml",
        "workflow_dispatch",
        "completed",
        "success",
        "main",
        "2026-09-30T16:00:00Z",
    )
    assert any("EXECUTION_FAILED" in e and "re-run" in e for e in ad.tick())


def test_latest_verifier_report_wins_over_an_earlier_unestablished_one(tmp_path):
    gh = FakeGitHub()
    _, _, ad, pl = build(tmp_path, gh)
    ad.tick()
    rid = gh.executor_finishes(R1, T1)
    ad.tick()
    pl.pump()
    ad.tick()
    gh.checks[R1] = {"quality": ("completed", "success")}
    gh.verifier_runs(rid, "UNESTABLISHED")
    assert "VERDICT:DEVQ-1" not in ad.tick()
    vid2 = rid + 2000
    gh.verify_runs.append(
        RunInfo(
            vid2,
            1,
            "atlas-runner-verify.yml",
            "workflow_run",
            "completed",
            "success",
            "main",
            "2026-09-30T16:06:00Z",
        )
    )
    gh.verify_reports[vid2] = {"source_run_id": rid, "verdict": "VERIFIED"}
    assert "VERDICT:DEVQ-1" in ad.tick()
    pl.pump()
    assert pl.lineages["DEVQ-1"].phase is Phase.INTEGRATION_READY


def test_stray_or_unknown_pending_files_are_parked_not_wedging(tmp_path):
    gh = FakeGitHub()
    _, _, ad, _ = build(tmp_path, gh)
    ad.tick()
    (ad.pending / "verdict-stray.json").write_text("{not json", encoding="utf-8")
    (ad.pending / "result-stray.json").write_text("{}", encoding="utf-8")
    ev = ad.tick()
    assert any(e.startswith("VERDICT_FILE_REJECTED") for e in ev)
    assert any(e.startswith("RESULT_FILE_REJECTED") for e in ev)
    assert ad.tick() == []


def test_shift_normalises_offsets_and_naive_clocks_to_utc():
    from datetime import timedelta

    from project_atlas.orchestration.autonomy.dev_fabric_adapter import _shift

    d = timedelta(seconds=120)
    assert _shift("2026-09-30T18:00:00+02:00", d) == "2026-09-30T15:58:00Z"
    assert _shift("2026-09-30T16:00:00", d) == "2026-09-30T15:58:00Z"


# ---- round-4 IV regressions ---------------------------------------------------------------


def test_non_adapter_errors_never_wedge_the_tick_or_later_verifications(tmp_path):
    class Boom(FakeGitHub):
        def read_json_artifact(self, run_id, name, member):
            raise KeyError("verification-report.json")  # a raw, unwrapped port failure

    gh = Boom()
    _, _, ad, pl = build(tmp_path, gh)
    ad.tick()
    rid = gh.executor_finishes(R1, T1)
    ad.tick()
    pl.pump()
    ad.tick()
    gh.checks[R1] = {"quality": ("completed", "success")}
    gh.verifier_runs(rid, "VERIFIED")
    ev = ad.tick()  # must not raise
    assert any(e.startswith("VERDICT_UNEXPECTED:DEVQ-1") for e in ev)
    assert "VERDICT:DEVQ-1" not in ev


def test_rerun_after_ingestion_cannot_vouch_for_the_old_head(tmp_path):
    gh = FakeGitHub()
    _, _, ad, pl = build(tmp_path, gh)
    ad.tick()
    rid = gh.executor_finishes(R1, T1)
    ad.tick()
    pl.pump()
    ad.tick()
    gh.runs[0] = RunInfo(
        rid,
        2,
        "atlas-agent-execute.yml",
        "workflow_dispatch",
        "completed",
        "success",
        "main",
        "2026-09-30T16:00:00Z",
    )
    gh.checks[R1] = {"quality": ("completed", "success")}
    gh.verifier_runs(rid, "VERIFIED")
    ev = ad.tick()
    assert any(e.startswith("VERDICT_ERROR:DEVQ-1") and "re-run" in e for e in ev)
    assert "VERDICT:DEVQ-1" not in ev


def test_claimed_but_unpersisted_work_is_readopted_after_a_crash(tmp_path):
    gh = FakeGitHub()
    spool, _xw, ad, _pl = build(tmp_path, gh)
    w = spool.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=ad.executor_identity)
    assert w is not None  # crash right after the claim: nothing persisted
    ad2 = FabricAdapter(
        gh,
        spool,
        Crosswalk(tmp_path / "xw.jsonl"),
        pending_dir=tmp_path / "pending",
        clock=lambda: "2026-09-30T15:59:00Z",
        task_statement=statement,
        required_checks=frozenset({"quality"}),
    )
    assert ad2.works == {}
    ev = ad2.tick()
    assert "WORK_READOPTED:DEVQ-1" in ev and "DISPATCHED:DEVQ-1" in ev
    assert ad2.tick() == []  # re-adoption is idempotent: never a second dispatch
    assert len(gh.dispatches) == 1


# ---- round-5 IV regressions ---------------------------------------------------------------


def test_duplicate_execution_id_is_refused_once_and_never_wedges_later_ticks(tmp_path):
    gh = FakeGitHub()
    spool, _xw, ad, _pl = build(tmp_path, gh)
    ad.tick()  # DEVQ-1 accepted + dispatched
    dup = make_work(
        task_id="OTHER",
        execution_id="DEVQ-1-E1",
        lineage_root="OTHER",
        **{**FIELDS, "authority_ref": "AUTH-2"},
    )
    spool.publish(dup)
    for _ in range(3):  # must not raise, now or later
        ad.tick()
    assert len(gh.dispatches) == 1 and all(w.task_id == "DEVQ-1" for w in ad.works.values())


def test_poisoned_verification_file_does_not_abort_the_tick(tmp_path):
    gh = FakeGitHub()
    _spool, _xw, ad, _pl = build(tmp_path, gh)
    ad.tick()
    d = tmp_path / "spool" / "VERIFICATION"
    d.mkdir(parents=True, exist_ok=True)
    (d / "garbage.json").write_text("{not json", encoding="utf-8")
    ev = ad.tick()
    assert any(e.startswith("VERIFICATION_REFUSED") for e in ev)
    assert (d / "rejected" / "garbage.json").exists() and ad.tick() == []


def test_work_claimed_without_claim_meta_is_still_readopted(tmp_path):
    gh = FakeGitHub()
    spool, _xw, ad, _pl = build(tmp_path, gh)
    w = spool.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=ad.executor_identity)
    assert w is not None
    (tmp_path / "spool" / "WORK" / "claimed" / f"{w.seal}.claim.json").unlink()  # meta write lost
    ad2 = FabricAdapter(
        gh,
        spool,
        Crosswalk(tmp_path / "xw.jsonl"),
        pending_dir=tmp_path / "pending",
        clock=lambda: "2026-09-30T15:59:00Z",
        task_statement=statement,
        required_checks=frozenset({"quality"}),
    )
    assert "WORK_READOPTED:DEVQ-1" in ad2.tick()


def test_persist_before_bind_crash_window_is_closed_on_load(tmp_path):
    gh = FakeGitHub()
    spool, _xw, ad, _pl = build(tmp_path, gh)
    w = spool.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=ad.executor_identity)
    (tmp_path / "pending" / f"work-{w.seal}.json").write_text(encode(w), encoding="utf-8")
    ad2 = FabricAdapter(
        gh,
        spool,
        Crosswalk(tmp_path / "xw.jsonl"),
        pending_dir=tmp_path / "pending",
        clock=lambda: "2026-09-30T15:59:00Z",
        task_statement=statement,
        required_checks=frozenset({"quality"}),
    )
    ev = ad2.tick()
    assert "DISPATCHED:DEVQ-1" in ev and not any(e.startswith("EXECUTION_FAILED") for e in ev)


def test_recover_trouble_is_reported_not_raised(tmp_path, monkeypatch):
    gh = FakeGitHub()
    _spool, _xw, ad, _pl = build(tmp_path, gh)
    monkeypatch.setattr(ad, "recover", lambda: (_ for _ in ()).throw(RuntimeError("disk")))
    assert any(e.startswith("RECOVER_ERROR") for e in ad.tick())


# ---- round-6 IV regressions ---------------------------------------------------------------


@pytest.mark.parametrize("channel", ["WORK", "VERIFICATION"])
@pytest.mark.parametrize("kind", ["deep", "binary", "dir", "empty"])
def test_hostile_spool_entries_never_wedge_or_spin_the_tick(tmp_path, channel, kind):
    gh = FakeGitHub()
    _spool, _xw, ad, _pl = build(tmp_path, gh)
    d = tmp_path / "spool" / channel
    d.mkdir(parents=True, exist_ok=True)
    name = "0" * 64 + ".json"  # sorts before any real seal
    if kind == "deep":
        (d / name).write_text("[" * 200000, encoding="utf-8")
    elif kind == "binary":
        (d / name).write_bytes(b"\xff\xfe\x00bad")
    elif kind == "dir":
        (d / name).mkdir()
    else:
        (d / name).write_text("", encoding="utf-8")
    for _ in range(3):
        ad.tick()  # returns (no infinite loop), never raises
    assert len(gh.dispatches) == 1  # the legitimate work still got through


def test_readopt_persist_failure_is_retried_and_never_loses_the_work(tmp_path, monkeypatch):
    from project_atlas.orchestration.autonomy import dev_fabric_adapter as fa

    gh = FakeGitHub()
    spool, _xw, ad, _pl = build(tmp_path, gh)
    w = spool.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=ad.executor_identity)
    assert w is not None
    real = fa._atomic_write
    calls = {"n": 0}

    def flaky(path, text):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(28, "ENOSPC")
        return real(path, text)

    monkeypatch.setattr(fa, "_atomic_write", flaky)
    assert any(e.startswith("WORK_READOPT_DEFERRED") for e in ad.tick())
    assert "DISPATCHED:DEVQ-1" in ad.tick()  # retried next tick; ledger untouched until persisted
    assert len(gh.dispatches) == 1


def test_torn_claim_meta_still_counts_as_unowned_and_is_readopted(tmp_path):
    gh = FakeGitHub()
    spool, _xw, ad, _pl = build(tmp_path, gh)
    w = spool.claim(Channel.WORK, role=Role.IMPLEMENTER, identity=ad.executor_identity)
    assert w is not None
    (tmp_path / "spool" / "WORK" / "claimed" / f"{w.seal}.claim.json").write_text(
        "", encoding="utf-8"
    )
    ad2 = FabricAdapter(
        gh,
        spool,
        Crosswalk(tmp_path / "xw.jsonl"),
        pending_dir=tmp_path / "pending",
        clock=lambda: "2026-09-30T15:59:00Z",
        task_statement=statement,
        required_checks=frozenset({"quality"}),
    )
    assert "WORK_READOPTED:DEVQ-1" in ad2.tick()


def test_ledger_tolerates_a_torn_tail_and_does_not_grow_on_idempotent_binds(tmp_path):
    gh = FakeGitHub()
    _spool, xw, ad, _pl = build(tmp_path, gh)
    ad.tick()
    before = (tmp_path / "xw.jsonl").read_text(encoding="utf-8")
    ad.xw.bind_work(next(iter(ad.works.values())))
    assert (tmp_path / "xw.jsonl").read_text(encoding="utf-8") == before  # no duplicate WORK row
    (tmp_path / "xw.jsonl").write_text(before + '{"event": "RES', encoding="utf-8")  # torn append
    reopened = Crosswalk(tmp_path / "xw.jsonl")
    assert reopened.unbound_dispatches() == xw.unbound_dispatches()
