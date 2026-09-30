"""Fabric adapter against a fake GitHub port: the real loop hops, minus the network."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from project_atlas.orchestration.autonomy.dev_contracts import make_work
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
        clock=lambda: "x",
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
    with pytest.raises(AdapterError, match="sealed base"):
        ad.tick()
    assert gh.dispatches == []


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
    with pytest.raises(AdapterError, match="descend"):
        ad2.tick()


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
    with pytest.raises(AdapterError, match="moved"):
        ad.tick()
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
    with pytest.raises(RuntimeError):
        ad.tick()
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
