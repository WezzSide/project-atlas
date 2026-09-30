"""CODEX-SEC-006 — rejected-secret import never persists/prints raw secrets.

Required flow: DETECT → ABORT/REDACT → METADATA-ONLY EVIDENCE.
Anti-pattern: DETECT → persist raw turns → label content_redacted=true.
Synthetic test credentials only.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from project_atlas.openai_import_real import OpenAIRealImportError, import_openai_export
from project_atlas.openai_importer_fixtures import build_openai_import_fixture_receipt
from project_atlas.provider_adapters import quarantine_provider_output
from project_atlas.secrets import REDACTED_PLACEHOLDER, redact_text, scan_text

# Synthetic credential — never a real secret (CODEX-SEC-006).
_SYNTH_TOKEN = "sk_test_SYNTHETIC_CODEX_SEC_006_DO_NOT_USE_ABCDEF12"
_SYNTH_LINE = f"api_key = '{_SYNTH_TOKEN}'"


def _assert_secret_absent(blob: str, *, label: str) -> None:
    assert _SYNTH_TOKEN not in blob, f"CODEX-SEC-006 leak in {label}"
    assert _SYNTH_LINE not in blob, f"CODEX-SEC-006 leak in {label}"


def test_scan_text_never_returns_matched_secret() -> None:
    findings = scan_text(_SYNTH_LINE)
    assert findings
    dumped = json.dumps([finding.__dict__ for finding in findings], sort_keys=True)
    _assert_secret_absent(dumped, label="scan_text findings")
    assert all(finding.redacted_hint == "content redacted" for finding in findings)


def test_redact_text_removes_matched_secret_span() -> None:
    redacted = redact_text(f"use {_SYNTH_LINE} please")
    _assert_secret_absent(redacted, label="redact_text output")
    assert REDACTED_PLACEHOLDER in redacted


def test_openai_fixture_rejected_secret_turns_are_redacted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    dirty = tmp_path / "dirty.md"
    dirty.write_text(
        f"# dirty\n\n```text\nUser: {_SYNTH_LINE}\nAssistant: acknowledged\n```\n",
        encoding="utf-8",
    )
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        report = build_openai_import_fixture_receipt(
            vault,
            receipt_id="sec006-dirty",
            sample_path=dirty,
            adapters_enabled=True,
        )
    assert report["status"] == "parsed-rejected-secret"
    assert report["secret_scan"]["content_redacted"] is True
    assert report["secret_scan"]["findings_count"] >= 1
    assert all(turn["text"] == REDACTED_PLACEHOLDER for turn in report["turns"])

    receipt_path = vault / "generated" / "ops" / "openai-import-fixtures" / "sec006-dirty.json"
    receipt_blob = receipt_path.read_text(encoding="utf-8")
    _assert_secret_absent(receipt_blob, label="fixture receipt file")
    _assert_secret_absent(json.dumps(report, sort_keys=True), label="fixture receipt dict")
    _assert_secret_absent(stdout.getvalue(), label="stdout")

    qpath = vault / "generated" / "ops" / "provider-quarantine" / "oai-import-sec006-dirty.json"
    assert qpath.is_file()
    qblob = qpath.read_text(encoding="utf-8")
    _assert_secret_absent(qblob, label="quarantine envelope")
    qdata = json.loads(qblob)
    assert qdata["status"] == "rejected-secret"
    assert "payload_text" not in qdata
    assert "turns" not in qdata


def test_provider_quarantine_rejected_secret_is_metadata_only(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        report = quarantine_provider_output(
            vault,
            envelope_id="sec006-env",
            adapter_id="openai-assist",
            payload_text=_SYNTH_LINE,
            adapters_enabled=True,
        )
    assert report["status"] == "rejected-secret"
    assert report["secret_scan"]["content_redacted"] is True
    out = vault / "generated" / "ops" / "provider-quarantine" / "sec006-env.json"
    blob = out.read_text(encoding="utf-8")
    _assert_secret_absent(blob, label="provider quarantine file")
    _assert_secret_absent(json.dumps(report, sort_keys=True), label="provider quarantine dict")
    _assert_secret_absent(stdout.getvalue(), label="stdout")
    assert "payload_text" not in report


def test_real_openai_export_aborts_before_persist(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    export = tmp_path / "export.md"
    export.write_text(
        f"User: hello\nAssistant: {_SYNTH_LINE}\n",
        encoding="utf-8",
    )
    with pytest.raises(OpenAIRealImportError, match="secret-findings"):
        import_openai_export(vault, export, import_id="sec006-real")
    out_dir = vault / "generated" / "ops" / "openai-import"
    if out_dir.exists():
        for path in out_dir.rglob("*"):
            if path.is_file():
                _assert_secret_absent(
                    path.read_text(encoding="utf-8"),
                    label=str(path),
                )
