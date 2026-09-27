"""Integration-hardening tests: grants, execution identity, queued-job dedupe.

Bands B/E/F/G of the integration hardening directive. AUTHORITY != STRING:
an authority_reference must resolve to a durable grant record; transport
records (workflow_dispatch, run ids) are never authority.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from controller.controller import Controller
from controller.grants import (
    GrantConsumedError,
    GrantExpiredError,
    GrantScopeError,
    GrantStore,
    GrantUnknownError,
)

INFRA = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((INFRA / "schemas" / "atlas-task-binding.schema.json").read_text())


def _grant_store(tmp_path: Path) -> GrantStore:
    return GrantStore(tmp_path / "state" / "grants.db")


def test_grant_unknown_rejected(tmp_path: Path) -> None:
    grants = _grant_store(tmp_path)
    with pytest.raises(GrantUnknownError):
        grants.validate("grant-does-not-exist", task_id="t-1")


def test_grant_expired_rejected(tmp_path: Path) -> None:
    grants = _grant_store(tmp_path)
    grants.issue("g-exp", expires_at=1000.0)
    with pytest.raises(GrantExpiredError):
        grants.validate("g-exp", now=2000.0)


def test_grant_scope_mismatch_rejected(tmp_path: Path) -> None:
    grants = _grant_store(tmp_path)
    grants.issue("g-scope", repository="WezzSide/project-atlas", executor_type="claude")
    with pytest.raises(GrantScopeError):
        grants.validate(
            "g-scope", repository="other/repo", executor_type="claude"
        )
    with pytest.raises(GrantScopeError):
        grants.validate(
            "g-scope", repository="WezzSide/project-atlas", executor_type="codex"
        )


def test_grant_budget_one_shot(tmp_path: Path) -> None:
    grants = _grant_store(tmp_path)
    grants.issue("g-one", budget=1)
    grants.validate("g-one")
    grants.consume("g-one")
    with pytest.raises(GrantConsumedError):
        grants.validate("g-one")


def test_grant_task_scope_binding(tmp_path: Path) -> None:
    grants = _grant_store(tmp_path)
    grants.issue("g-task", scope={"task_id": "task-a"})
    grants.validate("g-task", task_id="task-a")
    with pytest.raises(GrantScopeError):
        grants.validate("g-task", task_id="task-b")


def _controller_with_grants(tmp_path, config, store, fake_docker, fake_github, fake_worker_manager):
    controller = Controller(
        config=config, store=store, docker=fake_docker, github=fake_github,
        worker_manager=fake_worker_manager,
        sleeper=lambda *_: None,
    )
    grants = _grant_store(tmp_path)
    grants.issue(config.transport_grant_id, budget=1000)
    controller.attach_grants(grants)
    return controller


def test_controller_rejects_task_without_resolvable_grant(
    config, store, fake_docker, fake_github, fake_worker_manager, workspace
):
    controller = _controller_with_grants(
        workspace, config, store, fake_docker, fake_github, fake_worker_manager
    )
    with pytest.raises(GrantUnknownError):
        controller.submit_task(
            {
                "task_id": "t-x",
                "authority_reference": "grant-nope",
                "executor_type": "claude",
                "execution_id": "ex-nope",
            }
        )


def test_controller_consumes_grant_on_admission(
    config, store, fake_docker, fake_github, fake_worker_manager, workspace
):
    grants = _grant_store(workspace)
    grants.issue(
        "g-real",
        repository="atlas-owner/atlas-repo",
        executor_type="claude",
        budget=1,
    )
    controller = Controller(
        config=config, store=store, docker=fake_docker, github=fake_github,
        worker_manager=fake_worker_manager,
        sleeper=lambda *_: None,
    )
    controller.attach_grants(grants)
    definition = {
        "task_id": "t-real",
        "authority_reference": "g-real",
        "repository": "atlas-owner/atlas-repo",
        "executor_type": "claude",
        "execution_id": "ex-real",
    }
    outcome, _ = controller.submit_task(definition, validate=False)
    assert outcome == "admitted"
    # one-shot: a second admission against the same grant fails closed
    # one-shot: a NEW task against the consumed grant fails closed ...
    with pytest.raises(GrantConsumedError):
        controller.submit_task(
            {**definition, "task_id": "t-real-2", "execution_id": "ex-real-2"}, validate=False
        )
    # ... while re-submitting the SAME task idempotently does not re-consume
    outcome, _ = controller.submit_task(definition, validate=False)
    assert outcome == "existing_terminal"


def _binding(**overrides) -> dict:
    doc = {
        "schema_version": 1,
        "task_id": "t-1",
        "repository": "o/r",
        "base_revision": "a" * 40,
        "executor_type": "claude",
        "authority_reference": "grant-9",
        "execution_id": "ex-1",
        "execution": {"command": "make test"},
    }
    doc.update(overrides)
    return doc


def test_schema_execution_id_required_for_atlas_tasks() -> None:
    """Band F: authority-bearing production tasks carry an explicit execution_id."""
    jsonschema.validate(_binding(), SCHEMA)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(_binding(execution_id=None), SCHEMA)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {k: v for k, v in _binding().items() if k != "execution_id"}, SCHEMA
        )


def _queued(run_id: int, attempt: int, job_id: int) -> dict:
    return {
        "id": run_id,
        "run_attempt": attempt,
        "jobs": [{"id": job_id, "status": "queued", "labels": ["self-hosted"]}],
    }


def test_same_run_attempt_distinct_jobs_both_admitted(
    config, store, fake_docker, fake_github, fake_worker_manager, workspace
):
    controller = Controller(
        config=config, store=store, docker=fake_docker, github=fake_github,
        worker_manager=fake_worker_manager,
        sleeper=lambda *_: None,
    )
    grants = _grant_store(workspace)
    grants.issue(config.transport_grant_id, budget=1000)
    controller.attach_grants(grants)
    fake_github.queued = [_queued(700, 1, 1), _queued(700, 1, 2)]
    admitted = controller.admit_queued_jobs()
    assert sorted(admitted) == ["gh-700-1-1", "gh-700-1-2"]
    # same job re-polled -> duplicate suppressed, no second execution
    admitted2 = controller.admit_queued_jobs()
    assert admitted2 == []
    assert store.find_execution_by_job(700, 1, 1) is not None
    assert store.find_execution_by_job(700, 1, 2) is not None


def test_rerun_attempt_is_separate_identity(
    config, store, fake_docker, fake_github, fake_worker_manager, workspace
):
    controller = Controller(
        config=config, store=store, docker=fake_docker, github=fake_github,
        worker_manager=fake_worker_manager,
        sleeper=lambda *_: None,
    )
    grants = _grant_store(workspace)
    grants.issue(config.transport_grant_id, budget=1000)
    controller.attach_grants(grants)
    fake_github.queued = [_queued(701, 1, 5)]
    assert controller.admit_queued_jobs() == ["gh-701-1-5"]
    fake_github.queued = [_queued(701, 2, 5)]
    assert controller.admit_queued_jobs() == ["gh-701-2-5"]
