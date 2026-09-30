"""AS-2.0-OAI-IMPORT-001 OpenAI importer fixture harness tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.openai_importer_fixtures import (
    OpenAIImportFixtureError,
    build_openai_import_fixture_receipt,
    default_sample_path,
    parse_chat_export_file,
)
from project_atlas.schema import available_schemas, validate_record

ROOT = Path(__file__).resolve().parents[2]


def test_parse_shipped_sample_chat_export() -> None:
    sample = default_sample_path(repo_root=ROOT)
    turns = parse_chat_export_file(sample)
    assert len(turns) == 2
    assert turns[0].role == "user"
    assert turns[1].role == "assistant"


def test_openai_import_receipt_quarantines_without_live_api(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    report = build_openai_import_fixture_receipt(
        vault,
        receipt_id="sample-1",
        sample_path=default_sample_path(repo_root=ROOT),
        adapters_enabled=True,
    )
    assert report["live_api"] is False
    assert report["status"] == "parsed-quarantined"
    assert report["turn_count"] == 2
    assert report["quarantine_envelope"]["package_id"] == "AS-2.0-PROV-001"
    assert report["quarantine_envelope"]["status"] == "quarantined"
    validate_record(report, "openai-import-fixture-receipt")
    assert (vault / "generated" / "ops" / "openai-import-fixtures" / "sample-1.json").is_file()
    assert (
        vault / "generated" / "ops" / "provider-quarantine" / "oai-import-sample-1.json"
    ).is_file()


def test_openai_import_rejects_secret_payload(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    dirty = tmp_path / "dirty.md"
    secret = "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
    dirty.write_text(
        f"# dirty\n\n```text\nUser: api_key = '{secret}'\nAssistant: redacted\n```\n",
        encoding="utf-8",
    )
    report = build_openai_import_fixture_receipt(
        vault,
        receipt_id="dirty-1",
        sample_path=dirty,
        adapters_enabled=True,
    )
    assert report["status"] == "parsed-rejected-secret"
    assert report["secret_scan"]["findings_count"] >= 1
    assert report["live_api"] is False
    # CODEX-SEC-006: raw secret must be ABSENT from receipt + vault artifacts.
    assert secret not in json.dumps(report, sort_keys=True)
    receipt_blob = (
        vault / "generated" / "ops" / "openai-import-fixtures" / "dirty-1.json"
    ).read_text(encoding="utf-8")
    assert secret not in receipt_blob
    assert all(turn["text"] == "[redacted]" for turn in report["turns"])
    qblob = (
        vault / "generated" / "ops" / "provider-quarantine" / "oai-import-dirty-1.json"
    ).read_text(encoding="utf-8")
    assert secret not in qblob


def test_openai_import_parse_only_skips_quarantine(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    report = build_openai_import_fixture_receipt(
        vault,
        receipt_id="parse-only",
        sample_path=default_sample_path(repo_root=ROOT),
        quarantine=False,
    )
    assert report["status"] == "parsed-only"
    assert "quarantine_envelope" not in report


def test_openai_import_empty_turns_fail_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    empty = tmp_path / "empty.md"
    empty.write_text("# no turns\n", encoding="utf-8")
    with pytest.raises(OpenAIImportFixtureError, match="turns-empty"):
        build_openai_import_fixture_receipt(
            vault,
            receipt_id="empty",
            sample_path=empty,
            quarantine=False,
        )


def test_oai_docs_and_schema() -> None:
    assert "openai-import-fixture-receipt" in available_schemas()
    assert (ROOT / "docs" / "AS-2.0-OAI-IMPORT-001.md").is_file()
    readme = (ROOT / "docs" / "atlas-2.0" / "fixtures" / "openai-importer" / "README.md").read_text(
        encoding="utf-8"
    )
    assert "No live API" in readme or "no live API" in readme.lower()
    expected = (
        ROOT
        / "docs"
        / "atlas-2.0"
        / "fixtures"
        / "openai-importer"
        / "expected-fixture-receipt.json"
    )
    # Docs sketch may use a placeholder digest; harness output is schema-valid.
    sketch = json.loads(expected.read_text(encoding="utf-8"))
    assert sketch["live_api"] is False
    assert sketch["package_id"] == "AS-2.0-OAI-IMPORT-001"
