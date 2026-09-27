"""Hardening admission-review repairs: regression tests for the six blocking
authority-path defects found in the fresh source audit of PR-B."""

from __future__ import annotations

from pathlib import Path

import pytest

from controller.controller import Controller
from controller.grants import GrantError, GrantStore
from controller.state import StateError


def _controller(workspace, config, store, fake_docker, fake_github, fake_worker_manager,
    **grant_kw):
    controller = Controller(
        config=config, store=store, docker=fake_docker, github=fake_github,
        worker_manager=fake_worker_manager,
        sleeper=lambda *_: None,
    )
    grants = GrantStore(workspace / "state" / "grants.db")
    grants.issue(config.transport_grant_id, budget=1000)
    if grant_kw:
        grants.issue(**grant_kw)
    controller.attach_grants(grants)
    return controller, grants


def _binding(task_id="t-1", execution_id="ex-1", authority="g-atlas", **kw):
    doc = {
        "schema_version": 1,
        "task_id": task_id,
        "authority_reference": authority,
        "execution_id": execution_id,
        "repository": "atlas-owner/atlas-repo",
        "base_revision": "a" * 40,
        "executor_type": "claude",
    }
    doc.update(kw)
    return doc


# 1. authority omission / empty bypass ---------------------------------------

def test_omitted_authority_rejected(config, store, fake_docker, fake_github, fake_worker_manager,
    workspace):
    controller, _ = _controller(workspace, config, store, fake_docker, fake_github,
        fake_worker_manager)
    with pytest.raises(ValueError, match="authority_reference is mandatory"):
        controller.submit_task({"schema_version": 1, "task_id": "t-noauth", "execution_id": "ex-x"})


def test_empty_authority_rejected(config, store, fake_docker, fake_github, fake_worker_manager,
    workspace):
    controller, _ = _controller(workspace, config, store, fake_docker, fake_github,
        fake_worker_manager)
    with pytest.raises(ValueError, match="authority_reference is mandatory"):
        controller.submit_task(_binding(task_id="t-empty", authority="", execution_id="ex-e"))


# 2. normal task-schema mismatch ----------------------------------------------

def test_cli_schema_validation_rejects_nonconforming_binding(workspace):
    """The CLI admission path validates the binding against the schema; a
    binding that violates the normal contract is rejected before admission."""
    from controller.cli import _validate_task_schema
    from controller.config import ConfigError

    with pytest.raises(ConfigError):
        _validate_task_schema({"schema_version": 1, "task_id": "x"})  # missing required fields


# 3. queued-job authority bypass ----------------------------------------------

def test_queued_job_rejected_without_transport_grant(workspace, store, fake_docker, fake_github,
    fake_worker_manager):
    from conftest import make_config

    config = make_config(workspace, transport_grant_id=None)
    controller = Controller(
        config=config, store=store, docker=fake_docker, github=fake_github,
        worker_manager=fake_worker_manager,
        sleeper=lambda *_: None,
    )
    fake_github.queued = [
        {"id": 900, "run_attempt": 1, "jobs": [{"id": 1, "status": "queued", "labels":
            ["self-hosted"]}]}
    ]
    assert controller.admit_queued_jobs() == []
    assert store.find_execution_by_job(900, 1, 1) is None


def test_queued_job_rejected_with_unknown_transport_grant(
    workspace, store, fake_docker, fake_github, fake_worker_manager
):
    from conftest import make_config

    config = make_config(workspace, transport_grant_id="grant-missing")
    controller = Controller(
        config=config, store=store, docker=fake_docker, github=fake_github,
        worker_manager=fake_worker_manager,
        sleeper=lambda *_: None,
    )
    fake_github.queued = [
        {"id": 901, "run_attempt": 1, "jobs": [{"id": 1, "status": "queued", "labels":
            ["self-hosted"]}]}
    ]
    assert controller.admit_queued_jobs() == []
    assert store.find_execution_by_job(901, 1, 1) is None


# 4. immutable grant reissue ---------------------------------------------------

def test_grant_reissue_rejected(workspace, config):
    grants = GrantStore(workspace / "state" / "grants.db")
    grants.issue("g-immutable", scope={"task_id": "a"})
    with pytest.raises(GrantError, match="immutable"):
        grants.issue("g-immutable", scope={"task_id": "b"})
    assert grants.get("g-immutable")["scope_json"] == '{"task_id": "a"}'


# 5. canonical execution_id use -------------------------------------------------

def test_canonical_execution_id_used_verbatim(
    config, store, fake_docker, fake_github, fake_worker_manager, workspace
):
    controller, _ = _controller(
        workspace, config, store, fake_docker, fake_github, fake_worker_manager,
        grant_id="g-atlas", repository="atlas-owner/atlas-repo", executor_type="claude",
    )
    outcome, execution_id = controller.submit_task(_binding(execution_id="ex-canonical-1"),
        validate=False)
    assert outcome == "admitted"
    assert execution_id == "ex-canonical-1"
    assert store.get_execution("ex-canonical-1") is not None


def test_duplicate_canonical_execution_id_rejected(
    config, store, fake_docker, fake_github, fake_worker_manager, workspace
):
    controller, grants = _controller(
        workspace, config, store, fake_docker, fake_github, fake_worker_manager,
        grant_id="g-atlas", repository="atlas-owner/atlas-repo", executor_type="claude",
        budget=10,
    )
    controller.submit_task(_binding(task_id="t-dup-a", execution_id="ex-dup"), validate=False)
    with pytest.raises(StateError, match="already exists"):
        controller.submit_task(_binding(task_id="t-dup-b", execution_id="ex-dup"), validate=False)
    # atomic recovery: the failed second admission rejected its task and the
    # grant budget for the FAILED task was not consumed (one consume per success)
    row = grants.get("g-atlas")
    assert row["consumed"] == 1
    assert store.get_task("t-dup-b")["status"] == "REJECTED"


# 6. atomic admission / recovery -----------------------------------------------

def test_consume_failure_marks_task_rejected(
    config, store, fake_docker, fake_github, fake_worker_manager, workspace
):
    controller, grants = _controller(
        workspace, config, store, fake_docker, fake_github, fake_worker_manager,
        grant_id="g-atlas", repository="atlas-owner/atlas-repo", executor_type="claude",
    )

    def explode(grant_id, **kw):
        raise GrantError("simulated consume race")

    original = grants.consume
    grants.consume = explode  # type: ignore[assignment]
    try:
        with pytest.raises(GrantError, match="simulated consume race"):
            controller.submit_task(_binding(task_id="t-race", execution_id="ex-race"),
                validate=False)
    finally:
        grants.consume = original  # type: ignore[assignment]
    assert store.get_task("t-race")["status"] == "REJECTED"


def test_start_failure_refunds_grant(
    config, store, fake_docker, fake_github, fake_worker_manager, workspace
):
    controller, grants = _controller(
        workspace, config, store, fake_docker, fake_github, fake_worker_manager,
        grant_id="g-atlas", repository="atlas-owner/atlas-repo", executor_type="claude",
    )
    # occupy the canonical execution id so _start_execution fails after consume
    store.submit_task("t-occupier", {"task_id": "t-occupier"})
    store.create_execution(
        task_id="t-occupier", definition_hash="h", execution_id="ex-collide"
    )
    with pytest.raises(StateError):
        controller.submit_task(_binding(task_id="t-collide", execution_id="ex-collide"),
            validate=False)
    row = grants.get("g-atlas")
    assert row["consumed"] == 0, "grant must be refunded when execution start fails"
    assert store.get_task("t-collide")["status"] == "REJECTED"


def test_binding_validator_parity_with_published_schema() -> None:
    """The stdlib admission mirror must agree with the published JSON schema."""
    import json

    import jsonschema

    from controller.schemas import validate_task_binding

    schema_path = Path(__file__).resolve().parents[1] / "schemas" / "atlas-task-binding.schema.json"
    schema = json.loads(schema_path.read_text())
    docs = [
        _binding(),
        _binding(resource_class="small", execution={"prompt": "do a bounded thing"}),
        {"schema_version": 1, "task_id": "x"},  # missing required
        _binding(authority=""),  # empty authority
        _binding(execution_id=None),  # null execution id
        _binding(base_revision="nothex"),  # bad revision
        _binding(executor_type="nope"),  # unknown executor
        {**_binding(), "bogus_field": 1},  # additionalProperties
        _binding(execution={}),  # neither command nor prompt
    ]
    for doc in docs:
        stdlib_errors = validate_task_binding(doc)
        try:
            jsonschema.validate(doc, schema)
            schema_ok = True
        except jsonschema.ValidationError:
            schema_ok = False
        assert (not stdlib_errors) == schema_ok, f"parity mismatch for {doc}: {stdlib_errors}"
