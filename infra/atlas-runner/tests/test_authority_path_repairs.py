"""Adversarial regressions for the repaired Atlas admission boundary."""

from __future__ import annotations

import pytest

from controller.controller import Controller
from controller.grants import GrantMissingError, GrantStore


def _binding(**overrides):
    binding = {
        "schema_version": 1,
        "task_id": "atlas-task-1",
        "execution_id": "atlas-execution-1",
        "authority_reference": "grant-1",
        "repository": "atlas-owner/atlas-repo",
        "base_revision": "a" * 40,
        "executor_type": "claude",
        "execution": {"command": "make test"},
    }
    binding.update(overrides)
    return binding


@pytest.mark.parametrize("authority", [None, ""])
def test_atlas_submit_rejects_missing_or_empty_authority(
    config, store, fake_docker, fake_github, fake_worker_manager, authority
):
    controller = Controller(
        config=config, store=store, docker=fake_docker, github=fake_github,
        worker_manager=fake_worker_manager,
    )
    controller.attach_grants(GrantStore(store.db_path))
    with pytest.raises(GrantMissingError):
        controller.submit_task(_binding(authority_reference=authority), validate=False)
    assert store.get_task("atlas-task-1") is None
    assert store._conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 0


def test_controller_boundary_rejects_incomplete_binding_even_without_cli_validation(
    config, store, fake_docker, fake_github, fake_worker_manager
):
    controller = Controller(
        config=config, store=store, docker=fake_docker, github=fake_github,
        worker_manager=fake_worker_manager,
    )
    controller.attach_grants(GrantStore(store.db_path))
    with pytest.raises(ValueError, match="invalid Atlas task binding"):
        controller.submit_task(
            {"schema_version": 1, "task_id": "incomplete", "execution_id": "ex-x",
             "authority_reference": "grant-1"},
            validate=False,
        )
    assert store.get_task("incomplete") is None


def test_queued_github_job_is_transport_not_authority(config, store, fake_docker, fake_github,
    fake_worker_manager):
    """P1-1 owner disposition: queued transport is SUPPORTED, explicitly enabled,
    and still requires a valid transport grant. The grant is validated (standing)
    but the queued job is transport, not authority: budget is not consumed."""
    grants = GrantStore(store.db_path)
    grants.issue(config.transport_grant_id, budget=1000)
    controller = Controller(
        config=config, store=store, docker=fake_docker, github=fake_github,
        worker_manager=fake_worker_manager,
    )
    controller.attach_grants(grants)
    fake_github.queued = [
        {"id": 900, "run_attempt": 1,
         "jobs": [{"id": 1, "status": "queued", "labels": ["self-hosted"]}]}
    ]
    assert controller.admit_queued_jobs() == ["gh-900-1-1"]
    assert store.get_task("gh-900-1-1") is not None
    assert grants.get(config.transport_grant_id)["consumed"] == 0
    # and with the transport gate disabled, the same job is rejected
    from conftest import make_config

    config2 = make_config(store.db_path.parent.parent, transport_grant_id=config.transport_grant_id,
                          queued_transport_enabled=False)
    store2 = store
    controller2 = Controller(
        config=config2, store=store2, docker=fake_docker, github=fake_github,
        worker_manager=fake_worker_manager,
    )
    controller2.attach_grants(grants)
    fake_github.queued = [
        {"id": 901, "run_attempt": 1,
         "jobs": [{"id": 1, "status": "queued", "labels": ["self-hosted"]}]}
    ]
    assert controller2.admit_queued_jobs() == []
    assert store.find_execution_by_job(901, 1, 1) is None


def test_normal_submit_contract_is_atlas_binding():
    from controller.cli import _validate_task_schema
    from controller.config import ConfigError

    with pytest.raises(ConfigError):
        _validate_task_schema({"schema_version": 1, "task_id": "legacy-only"})
