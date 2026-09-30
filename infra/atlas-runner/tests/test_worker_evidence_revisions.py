"""Revision provenance / conflict semantics for controller evidence."""

from __future__ import annotations

import json
from pathlib import Path

from controller import lifecycle
from controller.dockerctl import worker_container_name
from controller.worker import WorkerManager


def _manager(config, store, fake_docker, fake_github, fake_clock):
    return WorkerManager(
        config=config,
        store=store,
        docker=fake_docker,
        github=fake_github,
        token_provider=fake_github.token_provider,
        source_revision="f" * 40,
        clock=fake_clock,
    )


def _definition(**overrides):
    base = {
        "schema_version": 1,
        "task_id": "task-r",
        "github_run_id": 555,
        "github_run_attempt": 1,
        "runner_name": "atlas-task-r",
    }
    base.update(overrides)
    return base


def _submit_and_run(
    *,
    config,
    store,
    fake_docker,
    fake_github,
    fake_clock,
    definition: dict,
    fragment: dict | None = None,
    artifact_names: list[str] | None = None,
) -> tuple[dict, dict | None]:
    store.submit_task(definition["task_id"], definition)
    execution_id = store.create_execution(
        task_id=definition["task_id"],
        definition_hash="h",
        github_run_id=definition.get("github_run_id"),
        github_run_attempt=definition.get("github_run_attempt", 1),
    )
    store.transition(execution_id, lifecycle.ADMITTED)
    manager = _manager(config, store, fake_docker, fake_github, fake_clock)
    workspace = manager.workspace_for(execution_id)
    evidence_dir = workspace / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    if artifact_names:
        for name in artifact_names:
            target = workspace / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"artifact:{name}\n", encoding="utf-8")
    if fragment is not None:
        (evidence_dir / "fragment.json").write_text(json.dumps(fragment), encoding="utf-8")
    worker = worker_container_name(execution_id)
    fake_docker.inspect_seq[worker] = [{"Running": False, "ExitCode": 0}]
    manager.run_execution(store.get_execution(execution_id), definition=definition)
    row = store.get_execution(execution_id)
    doc = None
    if row and row.get("evidence_path"):
        doc = json.loads(Path(row["evidence_path"]).read_text(encoding="utf-8"))
    return row, doc


def test_rich_fragment_result_revision_is_propagated_to_controller_evidence(
    config, store, fake_docker, fake_github, fake_clock
) -> None:
    fragment = {
        "task_id": "task-r",
        "result_revision": "a" * 40,
        "tests": {"acceptance_fixture_monotonic": True},
        "artifacts": ["acceptance.patch"],
        "artifact_sha256": {"acceptance.patch": "b" * 64},
    }
    row, doc = _submit_and_run(
        config=config,
        store=store,
        fake_docker=fake_docker,
        fake_github=fake_github,
        fake_clock=fake_clock,
        definition=_definition(task_id="task-r1"),
        fragment=fragment,
        artifact_names=["acceptance.patch"],
    )
    assert row["status"] == lifecycle.COMPLETE
    assert doc is not None
    assert doc["result_revision"] == fragment["result_revision"]
    assert doc["tests"] == fragment["tests"]
    assert "acceptance.patch" in doc["artifacts"]
    assert doc["artifact_sha256"]["acceptance.patch"]


def test_fragment_base_revision_used_when_definition_has_none(
    config, store, fake_docker, fake_github, fake_clock
) -> None:
    fragment = {"task_id": "task-r2", "base_revision": "c" * 40}
    row, doc = _submit_and_run(
        config=config,
        store=store,
        fake_docker=fake_docker,
        fake_github=fake_github,
        fake_clock=fake_clock,
        definition=_definition(task_id="task-r2"),
        fragment=fragment,
    )
    assert row["status"] == lifecycle.COMPLETE
    assert doc is not None
    assert doc["base_revision"] == fragment["base_revision"]


def test_matching_definition_and_fragment_revisions_are_accepted(
    config, store, fake_docker, fake_github, fake_clock
) -> None:
    same = "d" * 40
    fragment = {"task_id": "task-r3", "base_revision": same, "result_revision": same}
    row, doc = _submit_and_run(
        config=config,
        store=store,
        fake_docker=fake_docker,
        fake_github=fake_github,
        fake_clock=fake_clock,
        definition=_definition(task_id="task-r3", base_revision=same, result_revision=same),
        fragment=fragment,
    )
    assert row["status"] == lifecycle.COMPLETE
    assert doc is not None
    assert doc["base_revision"] == same
    assert doc["result_revision"] == same


def test_revision_conflict_fails_closed_for_base_revision(
    config, store, fake_docker, fake_github, fake_clock
) -> None:
    row, _doc = _submit_and_run(
        config=config,
        store=store,
        fake_docker=fake_docker,
        fake_github=fake_github,
        fake_clock=fake_clock,
        definition=_definition(task_id="task-r4", base_revision="a" * 40),
        fragment={"task_id": "task-r4", "base_revision": "b" * 40},
    )
    assert row["status"] == lifecycle.FAILED
    assert row["failure_reason"] == "evidence_base_revision_conflict"
    assert row["status"] != lifecycle.COMPLETE


def test_revision_conflict_fails_closed_for_result_revision(
    config, store, fake_docker, fake_github, fake_clock
) -> None:
    row, _doc = _submit_and_run(
        config=config,
        store=store,
        fake_docker=fake_docker,
        fake_github=fake_github,
        fake_clock=fake_clock,
        definition=_definition(task_id="task-r5", result_revision="a" * 40),
        fragment={"task_id": "task-r5", "result_revision": "b" * 40},
    )
    assert row["status"] == lifecycle.FAILED
    assert row["failure_reason"] == "evidence_result_revision_conflict"
    assert row["status"] != lifecycle.COMPLETE


def test_malformed_fragment_result_revision_fails_closed_validation(
    config, store, fake_docker, fake_github, fake_clock
) -> None:
    row, _doc = _submit_and_run(
        config=config,
        store=store,
        fake_docker=fake_docker,
        fake_github=fake_github,
        fake_clock=fake_clock,
        definition=_definition(task_id="task-r6"),
        fragment={"task_id": "task-r6", "result_revision": "not-a-revision"},
    )
    assert row["status"] == lifecycle.FAILED
    assert row["failure_reason"] == "evidence_validation"
    assert row["status"] != lifecycle.COMPLETE


def test_no_result_commit_keeps_result_revision_null(
    config, store, fake_docker, fake_github, fake_clock
) -> None:
    row, doc = _submit_and_run(
        config=config,
        store=store,
        fake_docker=fake_docker,
        fake_github=fake_github,
        fake_clock=fake_clock,
        definition=_definition(task_id="task-r7"),
        fragment={"task_id": "task-r7", "tests": {"acceptance_fixture_monotonic": True}},
    )
    assert row["status"] == lifecycle.COMPLETE
    assert doc is not None
    assert doc["result_revision"] is None
