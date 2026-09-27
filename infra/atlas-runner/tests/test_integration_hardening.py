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
    GrantConflictError,
    GrantConsumedError,
    GrantExpiredError,
    GrantMissingError,
    GrantScopeError,
    GrantStore,
    GrantUnknownError,
)
from controller.state import ExecutionConflictError, definition_hash

INFRA = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((INFRA / "schemas" / "atlas-task-binding.schema.json").read_text())
WORKER_SCHEMA = json.loads((INFRA / "schemas" / "worker-task.schema.json").read_text())


def _grant_store(tmp_path: Path) -> GrantStore:
    return GrantStore(tmp_path / "state" / "test.db")


def _atlas_grant_scope(definition: dict) -> dict:
    execution = definition["execution"]
    return {
        "task_id": definition["task_id"],
        "action_type": "command" if "command" in execution else "prompt",
        "execution_hash": definition_hash(execution),
    }


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


def test_reissuing_grant_id_cannot_reset_consumed_budget(tmp_path: Path) -> None:
    grants = _grant_store(tmp_path)
    grants.issue("g-immutable", repository="o/r", budget=1)
    grants.consume("g-immutable")

    with pytest.raises(GrantConflictError):
        grants.issue("g-immutable", repository="o/other", budget=5)
    grants.issue("g-immutable", repository="o/r", budget=1)
    assert grants.get("g-immutable")["consumed"] == 1
    with pytest.raises(GrantConsumedError):
        grants.validate("g-immutable")


def test_reissuing_revoked_or_expired_grant_does_not_reactivate_it(tmp_path: Path) -> None:
    grants = _grant_store(tmp_path)
    grants.issue("g-revoked", repository="o/r", budget=1)
    grants.revoke("g-revoked")
    grants.issue("g-revoked", repository="o/r", budget=1)
    assert grants.get("g-revoked")["status"] == "revoked"

    grants.issue("g-expired", repository="o/r", expires_at=10.0)
    grants.issue("g-expired", repository="o/r", expires_at=10.0)
    with pytest.raises(GrantExpiredError):
        grants.validate("g-expired", now=10.0)


def _controller_with_grants(tmp_path, config, store, fake_docker, fake_github, fake_worker_manager):
    controller = Controller(
        config=config, store=store, docker=fake_docker, github=fake_github,
        worker_manager=fake_worker_manager,
        allow_internal_queued_jobs=True,
        sleeper=lambda *_: None,
    )
    controller.attach_grants(GrantStore(store.db_path))
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
                "schema_version": 1, "task_id": "t-x", "execution_id": "ex-x",
                "authority_reference": "grant-nope", "repository": "o/r",
                "base_revision": "a" * 40, "executor_type": "claude",
                "execution": {"command": "make test"},
            }
        )


@pytest.mark.parametrize("authority", [None, ""])
def test_controller_rejects_atlas_binding_without_authority(
    config, store, fake_docker, fake_github, fake_worker_manager, workspace, authority
):
    controller = _controller_with_grants(
        workspace, config, store, fake_docker, fake_github, fake_worker_manager
    )
    definition = {
        "schema_version": 1,
        "task_id": "atlas-task-no-grant",
        "execution_id": "atlas-execution-no-grant",
        "repository": "o/r",
        "base_revision": "a" * 40,
        "executor_type": "claude",
        "execution": {"command": "make test"},
        "authority_reference": authority,
    }
    with pytest.raises(GrantMissingError):
        controller.submit_task(definition, validate=False)
    assert store.get_task(definition["task_id"]) is None


def test_atlas_execution_id_is_used_exactly(
    config, store, fake_docker, fake_github, fake_worker_manager, workspace
):
    grants = GrantStore(store.db_path)
    grants.issue(
        "g-exec-id",
        scope={
            "task_id": "atlas-task-exec-id",
            "action_type": "command",
            "execution_hash": definition_hash({"command": "make test"}),
        },
        repository="o/r",
        base_revision="a" * 40,
        executor_type="claude",
    )
    controller = Controller(
        config=config, store=store, docker=fake_docker, github=fake_github,
        worker_manager=fake_worker_manager,
        allow_internal_queued_jobs=True,
    )
    controller.attach_grants(grants)
    definition = {
        "schema_version": 1,
        "task_id": "atlas-task-exec-id",
        "execution_id": "atlas-execution-exact",
        "authority_reference": "g-exec-id",
        "repository": "o/r",
        "base_revision": "a" * 40,
        "executor_type": "claude",
        "execution": {"command": "make test"},
    }
    outcome, execution_id = controller.submit_task(definition, validate=False)
    assert outcome == "admitted"
    assert execution_id == definition["execution_id"]
    assert store.get_execution(execution_id)["task_id"] == definition["task_id"]


@pytest.mark.parametrize("failure", ["expired", "consumed", "scope", "execution"])
def test_atlas_admission_rejects_invalid_grant_without_partial_state(
    config, store, fake_docker, fake_github, fake_worker_manager, failure
):
    grants = GrantStore(store.db_path)
    definition = {
        "schema_version": 1,
        "task_id": "strict-task",
        "execution_id": "strict-execution",
        "authority_reference": "strict-grant",
        "repository": "o/r",
        "base_revision": "a" * 40,
        "executor_type": "claude",
        "execution": {"command": "make test"},
    }
    scope_definition = dict(definition)
    if failure == "scope":
        scope_definition["task_id"] = "different-task"
    if failure == "execution":
        scope_definition["execution"] = {"command": "make deploy"}
    grants.issue(
        "strict-grant",
        scope=_atlas_grant_scope(scope_definition),
        repository="o/r",
        base_revision="a" * 40,
        executor_type="claude",
        expires_at=1.0 if failure == "expired" else None,
    )
    if failure == "consumed":
        grants.consume("strict-grant")
    controller = Controller(
        config=config, store=store, docker=fake_docker, github=fake_github,
        worker_manager=fake_worker_manager,
    )
    controller.attach_grants(grants)
    expected = {
        "expired": GrantExpiredError,
        "consumed": GrantConsumedError,
        "scope": GrantScopeError,
        "execution": GrantScopeError,
    }[failure]
    with pytest.raises(expected):
        controller.submit_task(definition, validate=False)
    assert store.get_task("strict-task") is None
    assert store._conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 0
    assert grants.get("strict-grant")["consumed"] == (1 if failure == "consumed" else 0)


@pytest.mark.parametrize(
    "fault_stage",
    [
        "after_grant_validation",
        "after_grant_reservation",
        "after_task_persistence",
        "before_execution_creation",
        "after_execution_creation",
    ],
)
def test_atlas_admission_faults_roll_back_every_authority_record(
    config, store, workspace, fault_stage
):
    grants = GrantStore(store.db_path)
    definition = {
        "schema_version": 1,
        "task_id": f"fault-{fault_stage}",
        "execution_id": f"exec-{fault_stage}",
        "authority_reference": f"grant-{fault_stage}",
        "repository": "o/r",
        "base_revision": "a" * 40,
        "executor_type": "claude",
        "execution": {"command": "make test"},
    }
    grants.issue(
        definition["authority_reference"],
        scope=_atlas_grant_scope(definition),
        repository=definition["repository"],
        base_revision=definition["base_revision"],
        executor_type=definition["executor_type"],
    )

    def fail_at(stage):
        if stage == fault_stage:
            raise RuntimeError("injected admission interruption")

    with pytest.raises(RuntimeError, match="injected"):
        store.admit_atlas_task(
            grants=grants,
            task_id=definition["task_id"],
            definition=definition,
            fault_hook=fail_at,
        )
    reopened = type(store)(store.db_path)
    assert grants.get(definition["authority_reference"])["consumed"] == 0
    assert reopened.get_task(definition["task_id"]) is None
    assert reopened._conn.execute(
        "SELECT 1 FROM executions WHERE execution_id = ?", (definition["execution_id"],)
    ).fetchone() is None

    assert reopened.admit_atlas_task(
        grants=grants, task_id=definition["task_id"], definition=definition
    ) == ("admitted", definition["execution_id"])
    assert reopened.admit_atlas_task(
        grants=grants, task_id=definition["task_id"], definition=definition
    ) == ("existing_active", definition["execution_id"])
    assert grants.get(definition["authority_reference"])["consumed"] == 1
    reopened.close()


def test_atlas_duplicate_execution_id_rejected_without_consuming_second_grant(
    config, store
):
    grants = GrantStore(store.db_path)
    first = {
        "schema_version": 1, "task_id": "task-first", "execution_id": "exec-shared",
        "authority_reference": "grant-first", "repository": "o/r",
        "base_revision": "a" * 40, "executor_type": "claude",
        "execution": {"command": "make test"},
    }
    second = {**first, "task_id": "task-second", "authority_reference": "grant-second"}
    for definition in (first, second):
        grants.issue(
            definition["authority_reference"],
            scope=_atlas_grant_scope(definition),
            repository=definition["repository"], base_revision=definition["base_revision"],
            executor_type=definition["executor_type"],
        )
    store.admit_atlas_task(grants=grants, task_id=first["task_id"], definition=first)
    with pytest.raises(ExecutionConflictError, match="already in use"):
        store.admit_atlas_task(grants=grants, task_id=second["task_id"], definition=second)
    assert grants.get("grant-second")["consumed"] == 0
    assert store.get_task("task-second") is None


def test_committed_atlas_admission_resumes_after_controller_restart(
    config, store, fake_docker, fake_github, fake_worker_manager
):
    grants = GrantStore(store.db_path)
    definition = {
        "schema_version": 1,
        "task_id": "restart-task",
        "execution_id": "restart-execution",
        "authority_reference": "restart-grant",
        "repository": "o/r",
        "base_revision": "a" * 40,
        "executor_type": "claude",
        "execution": {"command": "make test"},
    }
    grants.issue(
        "restart-grant", scope=_atlas_grant_scope(definition), repository="o/r",
        base_revision="a" * 40, executor_type="claude",
    )

    class FailBeforeWorkerStarts:
        def run_execution(self, _execution, *, definition):
            raise RuntimeError("controller stopped after admission commit")

    first = Controller(
        config=config, store=store, docker=fake_docker, github=fake_github,
        worker_manager=FailBeforeWorkerStarts(),
    )
    first.attach_grants(grants)
    with pytest.raises(RuntimeError, match="after admission commit"):
        first.submit_task(definition, validate=False)
    assert store.get_execution("restart-execution")["status"] == "ADMITTED"

    restarted = Controller(
        config=config, store=store, docker=fake_docker, github=fake_github,
        worker_manager=fake_worker_manager,
    )
    restarted.attach_grants(grants)
    outcome, execution_id = restarted.submit_task(definition, validate=False)
    assert (outcome, execution_id) == ("existing_active", "restart-execution")
    assert store.get_execution("restart-execution")["status"] == "COMPLETE"
    assert grants.get("restart-grant")["consumed"] == 1
    assert store._conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 1


def test_cli_production_contract_is_atlas_binding_not_legacy_worker_task():
    from controller.schemas import validate_atlas_task_binding, validate_worker_task

    legacy = {"schema_version": 1, "task_id": "legacy"}
    assert validate_worker_task(legacy) == []
    jsonschema.validate(legacy, WORKER_SCHEMA)
    assert validate_atlas_task_binding(legacy) == [
        "missing field: authority_reference",
        "missing field: base_revision",
        "missing field: execution",
        "missing field: execution_id",
        "missing field: executor_type",
        "missing field: repository",
    ]
    binding = _binding()
    assert validate_atlas_task_binding(binding) == []
    jsonschema.validate(binding, SCHEMA)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {key: value for key, value in binding.items() if key != "authority_reference"},
            SCHEMA,
        )


def test_controller_consumes_grant_on_admission(
    config, store, fake_docker, fake_github, fake_worker_manager, workspace
):
    grants = GrantStore(store.db_path)
    grants.issue(
        "g-real",
        scope={
            "task_id": "t-real",
            "action_type": "command",
            "execution_hash": definition_hash({"command": "make test"}),
        },
        repository="atlas-owner/atlas-repo",
        base_revision="a" * 40,
        executor_type="claude",
        budget=1,
    )
    controller = Controller(
        config=config, store=store, docker=fake_docker, github=fake_github,
        worker_manager=fake_worker_manager,
        allow_internal_queued_jobs=True,
        sleeper=lambda *_: None,
    )
    controller.attach_grants(grants)
    definition = {
        "schema_version": 1,
        "task_id": "t-real",
        "execution_id": "ex-t-real",
        "authority_reference": "g-real",
        "repository": "atlas-owner/atlas-repo",
        "base_revision": "a" * 40,
        "executor_type": "claude",
        "execution": {"command": "make test"},
    }
    outcome, _ = controller.submit_task(definition, validate=False)
    assert outcome == "admitted"
    # one-shot: a second admission against the same grant fails closed
    with pytest.raises(GrantConsumedError):
        controller.submit_task(
            {**definition, "task_id": "t-real-2", "execution_id": "ex-t-real-2"},
            validate=False,
        )


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
        allow_internal_queued_jobs=True,
        sleeper=lambda *_: None,
    )
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
        allow_internal_queued_jobs=True,
        sleeper=lambda *_: None,
    )
    fake_github.queued = [_queued(701, 1, 5)]
    assert controller.admit_queued_jobs() == ["gh-701-1-5"]
    fake_github.queued = [_queued(701, 2, 5)]
    assert controller.admit_queued_jobs() == ["gh-701-2-5"]
