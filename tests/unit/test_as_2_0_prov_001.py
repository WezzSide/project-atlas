"""AS-2.0-PROV-001 provider adapter registry and quarantine tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.provider_adapters import (
    ProviderAdapter,
    ProviderError,
    build_adapter_registry,
    quarantine_provider_output,
)
from project_atlas.schema import available_schemas, validate_record

ROOT = Path(__file__).resolve().parents[2]


def test_provider_registry_disabled_by_default(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    report = build_adapter_registry(
        vault,
        adapters=[
            ProviderAdapter(
                adapter_id="openai-assist",
                provider="openai",
                capabilities=("classify-assist", "summarize"),
            )
        ],
    )
    assert report["adapters_enabled"] is False
    assert report["adapters"][0]["enabled"] is False
    validate_record(report, "provider-adapter-registry")
    assert (vault / "generated" / "ops" / "provider-adapter-registry.json").is_file()


def test_provider_forbids_dangerous_capabilities(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(ProviderError, match="capability-forbidden"):
        build_adapter_registry(
            vault,
            adapters=[
                ProviderAdapter(
                    adapter_id="bad",
                    provider="mcp",
                    capabilities=("promote",),  # type: ignore[arg-type]
                )
            ],
        )


def test_quarantine_rejects_when_adapters_disabled(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    report = quarantine_provider_output(
        vault,
        envelope_id="env-1",
        adapter_id="openai-assist",
        payload_text="hello world",
        adapters_enabled=False,
    )
    assert report["status"] == "rejected-disabled"
    assert report["secret_scan"]["content_redacted"] is True
    validate_record(report, "provider-quarantine-envelope")


def test_quarantine_rejects_secrets_when_enabled(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    secret = "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
    report = quarantine_provider_output(
        vault,
        envelope_id="env-2",
        adapter_id="openai-assist",
        payload_text=f"api_key = '{secret}'",
        adapters_enabled=True,
    )
    assert report["status"] == "rejected-secret"
    assert report["secret_scan"]["findings_count"] >= 1
    assert "api-key-assignment" in report["secret_scan"]["finding_kinds"]
    # CODEX-SEC-006: metadata-only evidence; raw secret ABSENT.
    assert secret not in json.dumps(report, sort_keys=True)
    blob = (vault / "generated" / "ops" / "provider-quarantine" / "env-2.json").read_text(
        encoding="utf-8"
    )
    assert secret not in blob
    assert "payload_text" not in report


def test_quarantine_accepts_clean_payload_when_enabled(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    report = quarantine_provider_output(
        vault,
        envelope_id="env-3",
        adapter_id="local-model",
        payload_text='{"summary":"ok"}',
        payload_kind="structured-json",
        adapters_enabled=True,
    )
    assert report["status"] == "quarantined"
    assert report["secret_scan"]["findings_count"] == 0
    assert len(report["payload_sha256"]) == 64


def test_prov_docs_and_schemas() -> None:
    assert "provider-adapter-registry" in available_schemas()
    assert "provider-quarantine-envelope" in available_schemas()
    assert (ROOT / "docs" / "AS-2.0-PROV-001.md").is_file()
