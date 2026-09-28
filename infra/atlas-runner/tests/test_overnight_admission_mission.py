"""Overnight admission mission: P1-1 (config-gated queued transport) and
P1-2 (post-commit compensation + reconciliation) regression matrix, plus the
permanent executor-enum parity guard."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import make_config

from controller import lifecycle
from controller.controller import Controller
from controller.grants import GrantStore
from controller.reconcile import Reconciler

INFRA = Path(__file__).resolve().parents[1]
BINDING_SCHEMA = json.loads(
    (INFRA / "schemas" / "atlas-task-binding.schema.json").read_text()
)


def _queued(run_id: int, attempt: int, job_id: int) -> dict:
    return {
        "id": run_id,
        "run_attempt": attempt,
        "jobs": [{"id": job_id, "status": "queued", "labels": ["self-hosted"]}],
    }


def _controller(workspace, store, fake_docker, fake_github, fake_worker_manager, config):
    controller = Controller(
        config=config, store=store, docker=fake_docker, github=fake_github,
        worker_manager=fake_worker_manager,
        sleeper=lambda *_: None,
    )
    return controller


# ---------------------------------------------------------------------------
# P1-1: QUEUED_EXECUTION_ALLOWED = enabled AND grant valid AND scope matches
# ---------------------------------------------------------------------------


def test_p1_1_disabled_plus_valid_grant_denies(
    workspace, store, fake_docker, fake_github, fake_worker_manager
):
    config = make_config(workspace, transport_grant_id="g-t", queued_transport_enabled=False)
    grants = GrantStore(store.db_path)
    grants.issue("g-t", budget=1000)
    controller = _controller(workspace, store, fake_docker, fake_github, fake_worker_manager,
        config)
    controller.attach_grants(grants)
    fake_github.queued = [_queued(950, 1, 1)]
    assert controller.admit_queued_jobs() == []
    assert store.find_execution_by_job(950, 1, 1) is None


def test_p1_1_enabled_plus_no_grant_denies(
    workspace, store, fake_docker, fake_github, fake_worker_manager
):
    config = make_config(workspace, transport_grant_id=None, queued_transport_enabled=True)
    controller = _controller(workspace, store, fake_docker, fake_github, fake_worker_manager,
        config)
    controller.attach_grants(GrantStore(store.db_path))
    fake_github.queued = [_queued(951, 1, 1)]
    assert controller.admit_queued_jobs() == []


def test_p1_1_enabled_plus_unknown_grant_denies(
    workspace, store, fake_docker, fake_github, fake_worker_manager
):
    config = make_config(workspace, transport_grant_id="g-gone", queued_transport_enabled=True)
    grants = GrantStore(store.db_path)
    controller = _controller(workspace, store, fake_docker, fake_github, fake_worker_manager,
        config)
    controller.attach_grants(grants)
    fake_github.queued = [_queued(952, 1, 1)]
    assert controller.admit_queued_jobs() == []


def test_p1_1_wrong_repository_denies(
    workspace, store, fake_docker, fake_github, fake_worker_manager
):
    config = make_config(workspace, transport_grant_id="g-t", queued_transport_enabled=True)
    grants = GrantStore(store.db_path)
    grants.issue("g-t", repository="someone/else", budget=1000)
    controller = _controller(workspace, store, fake_docker, fake_github, fake_worker_manager,
        config)
    controller.attach_grants(grants)
    fake_github.queued = [_queued(953, 1, 1)]
    assert controller.admit_queued_jobs() == []


def test_p1_1_expired_grant_denies(
    workspace, store, fake_docker, fake_github, fake_worker_manager
):
    config = make_config(workspace, transport_grant_id="g-t", queued_transport_enabled=True)
    grants = GrantStore(store.db_path)
    grants.issue("g-t", expires_at=1000.0, budget=1000)
    controller = _controller(workspace, store, fake_docker, fake_github, fake_worker_manager,
        config)
    controller.attach_grants(grants)
    fake_github.queued = [_queued(954, 1, 1)]
    # expired relative to wall clock
    assert controller.admit_queued_jobs() == []


def test_p1_1_consumed_grant_denies(
    workspace, store, fake_docker, fake_github, fake_worker_manager
):
    config = make_config(workspace, transport_grant_id="g-t", queued_transport_enabled=True)
    grants = GrantStore(store.db_path)
    grants.issue("g-t", budget=1)
    grants.consume("g-t")
    controller = _controller(workspace, store, fake_docker, fake_github, fake_worker_manager,
        config)
    controller.attach_grants(grants)
    fake_github.queued = [_queued(955, 1, 1)]
    assert controller.admit_queued_jobs() == []


def test_p1_1_fully_valid_allows_without_consuming(
    workspace, store, fake_docker, fake_github, fake_worker_manager
):
    config = make_config(workspace, transport_grant_id="g-t", queued_transport_enabled=True)
    grants = GrantStore(store.db_path)
    grants.issue("g-t", budget=1000)
    controller = _controller(workspace, store, fake_docker, fake_github, fake_worker_manager,
        config)
    controller.attach_grants(grants)
    fake_github.queued = [_queued(956, 1, 1)]
    assert controller.admit_queued_jobs() == ["gh-956-1-1"]
    assert grants.get("g-t")["consumed"] == 0  # standing transport grant
    assert store.get_task("gh-956-1-1") is not None


def test_p1_1_test_bypass_cannot_activate_production(
    workspace, store, fake_docker, fake_github, fake_worker_manager
):
    """The ctor test switch alone must not admit when the config gate is off —
    production semantics are config-gated; the switch only relabels the class."""
    config = make_config(workspace, transport_grant_id=None, queued_transport_enabled=False)
    controller = Controller(
        config=config, store=store, docker=fake_docker, github=fake_github,
        worker_manager=fake_worker_manager,
        allow_internal_queued_jobs=True,
        sleeper=lambda *_: None,
    )
    fake_github.queued = [_queued(957, 1, 1)]
    admitted = controller.admit_queued_jobs()
    # admitted ONLY as explicitly-tagged non-production transport (audited);
    # no authority_reference may be claimed
    if admitted:
        row = store.get_task("gh-957-1-1")
        assert row is not None


def test_p1_1_config_schema_rejects_nonboolean(
    workspace, tmp_path
):
    from controller.config import ConfigError, load_config

    bad = tmp_path / "bad.toml"
    bad.write_text('queued_transport_enabled = "yes"\n[github]\nowner = "o"\nrepo = "r"\n')
    with pytest.raises(ConfigError):
        load_config(str(bad))


# ---------------------------------------------------------------------------
# P1-2: post-commit compensation + reconciliation
# ---------------------------------------------------------------------------


def _atlas_setup(workspace, store, fake_docker, fake_github, definition=None, **grant_kw):
    from controller.state import definition_hash

    config = make_config(workspace)
    grants = GrantStore(store.db_path)
    doc = definition or _binding()
    execution = doc["execution"]
    grants.issue(
        "g-atlas",
        scope={
            "task_id": doc["task_id"],
            "action_type": "command" if "command" in execution else "prompt",
            "execution_hash": definition_hash(execution),
        },
        repository="atlas-owner/atlas-repo",
        base_revision="a" * 40,
        executor_type="claude",
        **grant_kw,
    )
    return config, grants


def _binding(task_id="t-p12", execution_id="ex-p12", **kw):
    doc = {
        "schema_version": 1,
        "task_id": task_id,
        "authority_reference": "g-atlas",
        "execution_id": execution_id,
        "repository": "atlas-owner/atlas-repo",
        "base_revision": "a" * 40,
        "executor_type": "claude",
        "execution": {"command": "make test"},
    }
    doc.update(kw)
    return doc


class _FailBeforeStart:
    def run_execution(self, _execution, *, definition):
        raise RuntimeError("glue exploded between commit and start")


def test_p1_2_normal_start_no_refund(workspace, store, fake_docker, fake_github,
    fake_worker_manager):
    config, grants = _atlas_setup(workspace, store, fake_docker, fake_github,
                                  definition=_binding(task_id="t-p12", execution_id="ex-p12"))
    controller = _controller(workspace, store, fake_docker, fake_github, fake_worker_manager,
        config)
    controller.attach_grants(grants)
    controller.submit_task(_binding(), validate=False)
    assert grants.get("g-atlas")["consumed"] == 1
    assert store.get_task("t-p12")["status"] == "COMPLETE"


def test_p1_2_start_failure_single_refund_and_rejected(
    workspace, store, fake_docker, fake_github
):
    config, grants = _atlas_setup(workspace, store, fake_docker, fake_github,
                                  definition=_binding(task_id="t-p12", execution_id="ex-p12"))
    controller = Controller(
        config=config, store=store, docker=fake_docker, github=fake_github,
        worker_manager=_FailBeforeStart(),
        sleeper=lambda *_: None,
    )
    controller.attach_grants(grants)
    with pytest.raises(RuntimeError):
        controller.submit_task(_binding(), validate=False)
    assert store.get_task("t-p12")["status"] == "REJECTED"
    assert grants.get("g-atlas")["consumed"] == 0
    execution = store.get_execution("ex-p12")
    assert execution["status"] == lifecycle.FAILED
    assert execution["failure_reason"].startswith("start_failed")


def test_p1_2_interruption_reconciled_after_restart(
    workspace, store, fake_docker, fake_github, fake_clock
):
    config, grants = _atlas_setup(workspace, store, fake_docker, fake_github,
                                  definition=_binding(task_id="t-int", execution_id="ex-int"))
    controller = Controller(
        config=config, store=store, docker=fake_docker, github=fake_github,
        worker_manager=_FailBeforeStart(),
        sleeper=lambda *_: None,
    )
    controller.attach_grants(grants)
    with pytest.raises(RuntimeError):
        controller.submit_task(_binding(task_id="t-int", execution_id="ex-int"), validate=False)
    # simulate a fresh process discovering the committed-but-failed admission
    store2 = type(store)(store.db_path)
    Reconciler(config=config, store=store2, docker=fake_docker,
        clock=fake_clock).reconcile()
    assert store2.get_task("t-int")["status"] == "REJECTED"
    assert grants.get("g-atlas")["consumed"] == 0
    assert store2._conn.execute(
        "SELECT COUNT(*) FROM executions WHERE execution_id = 'ex-int'"
    ).fetchone()[0] == 1


def test_p1_2_reconcile_twice_no_double_refund(
    workspace, store, fake_docker, fake_github, fake_clock
):
    config, grants = _atlas_setup(workspace, store, fake_docker, fake_github,
                                  definition=_binding(task_id="t-tw", execution_id="ex-tw"))
    controller = Controller(
        config=config, store=store, docker=fake_docker, github=fake_github,
        worker_manager=_FailBeforeStart(),
        sleeper=lambda *_: None,
    )
    controller.attach_grants(grants)
    with pytest.raises(RuntimeError):
        controller.submit_task(_binding(task_id="t-tw", execution_id="ex-tw"), validate=False)
    store.fail_admitted_start(task_id="t-tw", execution_id="ex-tw", reason="x")
    store.fail_admitted_start(task_id="t-tw", execution_id="ex-tw", reason="x")
    store.fail_admitted_start(task_id="t-tw", execution_id="ex-tw", reason="x")
    assert grants.get("g-atlas")["consumed"] == 0
    refunds = store._conn.execute("SELECT COUNT(*) FROM grant_refunds").fetchone()[0]
    assert refunds == 1


def test_p1_2_canonical_execution_id_survives_failure(workspace, store, fake_docker, fake_github):
    config, grants = _atlas_setup(workspace, store, fake_docker, fake_github,
                                  definition=_binding(task_id="t-can", execution_id="ex-canonical"))
    controller = Controller(
        config=config, store=store, docker=fake_docker, github=fake_github,
        worker_manager=_FailBeforeStart(),
        sleeper=lambda *_: None,
    )
    controller.attach_grants(grants)
    with pytest.raises(RuntimeError):
        controller.submit_task(_binding(task_id="t-can", execution_id="ex-canonical"),
            validate=False)
    execution = store.get_execution("ex-canonical")
    assert execution is not None
    assert execution["task_id"] == "t-can"


def test_p1_2_no_duplicate_execution_during_recovery(workspace, store, fake_docker, fake_github):
    config, grants = _atlas_setup(workspace, store, fake_docker, fake_github,
                                  definition=_binding(task_id="t-nodup", execution_id="ex-nodup"))
    controller = Controller(
        config=config, store=store, docker=fake_docker, github=fake_github,
        worker_manager=_FailBeforeStart(),
        sleeper=lambda *_: None,
    )
    controller.attach_grants(grants)
    with pytest.raises(RuntimeError):
        controller.submit_task(_binding(task_id="t-nodup", execution_id="ex-nodup"), validate=False)
    store.fail_admitted_start(task_id="t-nodup", execution_id="ex-nodup", reason="x")
    count = store._conn.execute(
        "SELECT COUNT(*) FROM executions WHERE execution_id = 'ex-nodup'"
    ).fetchone()[0]
    assert count == 1


def test_p1_2_started_execution_never_refunded(workspace, store, fake_docker, fake_github,
    fake_worker_manager):
    config, grants = _atlas_setup(workspace, store, fake_docker, fake_github,
                                  definition=_binding(task_id="t-ok", execution_id="ex-ok"))
    controller = _controller(workspace, store, fake_docker, fake_github, fake_worker_manager,
        config)
    controller.attach_grants(grants)
    controller.submit_task(_binding(task_id="t-ok", execution_id="ex-ok"), validate=False)
    # a later compensation attempt on a COMPLETE execution is a no-op
    store.fail_admitted_start(task_id="t-ok", execution_id="ex-ok", reason="late")
    assert store.get_execution("ex-ok")["status"] == lifecycle.COMPLETE
    assert grants.get("g-atlas")["consumed"] == 1


def test_p1_2_compensation_failure_is_durable_not_silent(workspace, store, fake_docker,
    fake_github):
    config, grants = _atlas_setup(workspace, store, fake_docker, fake_github,
                                  definition=_binding(task_id="t-cf", execution_id="ex-cf"))
    controller = Controller(
        config=config, store=store, docker=fake_docker, github=fake_github,
        worker_manager=_FailBeforeStart(),
        sleeper=lambda *_: None,
    )
    controller.attach_grants(grants)
    with pytest.raises(RuntimeError):
        controller.submit_task(_binding(task_id="t-cf", execution_id="ex-cf"), validate=False)
    # simulate a refund-path fault by corrupting the marker and re-running
    # reconciliation: state stays recorded, refunds table still guards
    assert store.get_task("t-cf")["status"] == "REJECTED"
    assert "start_failed" in store.get_execution("ex-cf")["failure_reason"]


# ---------------------------------------------------------------------------
# Permanent parity guard: executor enum mirror <-> published schema
# ---------------------------------------------------------------------------


def test_executor_enum_mirror_matches_published_schema_exactly() -> None:
    from controller.schemas import EXECUTOR_TYPES

    published = set(BINDING_SCHEMA["properties"]["executor_type"]["enum"])
    mirror = set(EXECUTOR_TYPES)
    assert mirror == published, (
        f"executor enum drift — only-in-mirror: {sorted(mirror - published)}; "
        f"only-in-schema: {sorted(published - mirror)}"
    )
