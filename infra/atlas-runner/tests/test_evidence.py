"""Evidence build/validate/write tests (spec section 31).

EVIDENCE != AUTHORITY: a valid evidence document proves a record was written,
nothing more. Schema parity with the published JSON schema is cross-checked
with the repo's jsonschema library.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from controller import evidence

SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "schemas" / "execution-evidence.schema.json"
)
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _valid_kwargs(**overrides):
    base = {
        "task_id": "task-1",
        "execution_id": "ex-1",
        "github_run_id": 101,
        "github_run_attempt": 1,
        "worker_id": "atlas-worker-ex-1",
        "runner_name": "atlas-ex-1",
        "runner_labels": ["self-hosted", "linux", "x64", "atlas", "executor"],
        "runner_image_digest": "sha256:" + "a" * 64,
        "repository": "owner/repo",
        "base_revision": "b" * 40,
        "result_revision": "c" * 40,
        "started_at": "2026-09-26T10:00:00Z",
        "finished_at": "2026-09-26T10:05:00Z",
        "terminal_status": "complete",
        "exit_code": 0,
        "tests": {"smoke": True},
        "artifacts": ["smoke-manifest.json"],
        "artifact_sha256": {"smoke-manifest.json": "d" * 64},
        "cleanup_status": "ok",
        "controller_version": "0.1.0",
        "source_revision": "e" * 40,
    }
    base.update(overrides)
    return base


def test_valid_evidence_passes_both_validators():
    doc = evidence.build_evidence(**_valid_kwargs())
    assert evidence.validate_evidence(doc) == []
    jsonschema.Draft202012Validator(SCHEMA).validate(doc)


def test_missing_field_rejected_by_both():
    doc = evidence.build_evidence(**_valid_kwargs())
    del doc["runner_name"]
    assert evidence.validate_evidence(doc)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(SCHEMA).validate(doc)


def test_extra_field_rejected():
    doc = evidence.build_evidence(**_valid_kwargs())
    doc["injected"] = True
    assert evidence.validate_evidence(doc)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(SCHEMA).validate(doc)


@pytest.mark.parametrize(
    "field,value",
    [
        ("terminal_status", "SUCCESS"),
        ("cleanup_status", "done"),
        ("github_run_id", "not-an-int"),
        ("duration_seconds", -1),
        ("artifact_sha256", {"x": "not-hex"}),
        ("started_at", "2026-09-26 10:00:00"),
        ("runner_labels", "self-hosted"),
        ("exit_code", "zero"),
    ],
)
def test_invalid_values_rejected(field, value):
    doc = evidence.build_evidence(**_valid_kwargs())
    doc[field] = value
    assert evidence.validate_evidence(doc)


def test_optional_null_fields_accepted():
    doc = evidence.build_evidence(
        **_valid_kwargs(
            base_revision=None, result_revision=None,
            runner_image_digest=None, exit_code=None,
        )
    )
    assert evidence.validate_evidence(doc) == []
    jsonschema.Draft202012Validator(SCHEMA).validate(doc)


def test_duration_computed():
    doc = evidence.build_evidence(
        **_valid_kwargs(started_at="2026-09-26T10:00:00Z", finished_at="2026-09-26T10:30:00Z")
    )
    assert doc["duration_seconds"] == 1800.0


def test_write_evidence_atomic_and_valid(tmp_path):
    doc = evidence.build_evidence(**_valid_kwargs())
    target = tmp_path / "jobs" / "ex-1" / "evidence.json"
    evidence.write_evidence(doc, target)
    on_disk = json.loads(target.read_text(encoding="utf-8"))
    assert on_disk == doc
    assert evidence.validate_evidence(on_disk) == []


def test_write_evidence_rejects_invalid(tmp_path):
    doc = evidence.build_evidence(**_valid_kwargs())
    del doc["cleanup_status"]
    with pytest.raises(evidence.EvidenceError):
        evidence.write_evidence(doc, tmp_path / "evidence.json")
    assert not (tmp_path / "evidence.json").exists()  # nothing half-written


def test_copy_evidence_to_log_dir(tmp_path):
    doc = evidence.build_evidence(**_valid_kwargs())
    out = evidence.copy_evidence(doc, tmp_path / "logs", "ex-1")
    assert out is not None and out.exists()
    assert out.name == "ex-1.evidence.json"


def test_sanitize_log_redacts_secrets():
    from controller.github import redact_secrets

    text = "registering with token ghp_FAKE_test_token_0123456789 now"
    out = redact_secrets(text, ["ghp_FAKE_test_token_0123456789"])
    assert "ghp_FAKE" not in out
    assert "***REDACTED***" in out
    assert evidence.sanitize_log(text, ["ghp_FAKE_test_token_0123456789"]) == out


def test_hash_correctness(tmp_path):
    import hashlib

    f = tmp_path / "f.bin"
    f.write_bytes(b"abc")
    assert evidence.sha256_file(f) == hashlib.sha256(b"abc").hexdigest()
