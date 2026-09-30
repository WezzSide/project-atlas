"""Unit tests for AS-INGEST-MANIFEST-001 pure merge helpers."""

from __future__ import annotations

from project_atlas.ingestion import (
    merge_discovery_manifest,
    merge_ingestion_report,
    merge_injection_findings,
    merge_secret_findings,
)


def test_merge_discovery_manifest_retains_prior_and_upserts_incoming() -> None:
    prior = {
        "schema_version": 1,
        "source_root": "/vault-a",
        "inventory_sha256": "a" * 64,
        "duplicates": {},
        "agent_events": [],
        "sources": [
            {
                "source_id": "source-keep",
                "path": "alpha/README.md",
                "sha256": "1" * 64,
                "likely_project": "alpha",
            },
            {
                "source_id": "source-update",
                "path": "beta/old.md",
                "sha256": "2" * 64,
                "likely_project": "beta",
            },
            {
                "source_id": "source-deleted",
                "path": "beta/gone.md",
                "sha256": "3" * 64,
                "likely_project": "beta",
            },
        ],
    }
    incoming = {
        "schema_version": 1,
        "source_root": "/vault-b",
        "inventory_sha256": "b" * 64,
        "duplicates": {},
        "agent_events": [{"event_id": "evt-1"}],
        "sources": [
            {
                "source_id": "source-update",
                "path": "beta/new.md",
                "sha256": "4" * 64,
                "likely_project": "beta",
            },
            {
                "source_id": "source-new",
                "path": "beta/extra.md",
                "sha256": "5" * 64,
                "likely_project": "beta",
            },
        ],
    }

    merged = merge_discovery_manifest(prior, incoming, deleted_source_ids={"source-deleted"})

    by_id = {item["source_id"]: item for item in merged["sources"]}
    assert set(by_id) == {"source-keep", "source-update", "source-new"}
    assert by_id["source-keep"]["path"] == "alpha/README.md"
    assert by_id["source-update"]["path"] == "beta/new.md"
    assert by_id["source-update"]["sha256"] == "4" * 64
    assert merged["source_root"] == "/vault-b"
    assert merged["agent_events"] == [{"event_id": "evt-1"}]
    assert merged["last_batch_inventory_sha256"] == "b" * 64
    assert isinstance(merged["inventory_sha256"], str)
    assert len(merged["inventory_sha256"]) == 64
    assert merged["inventory_sha256"] != "b" * 64


def test_merge_discovery_manifest_idempotent_for_identical_incoming() -> None:
    incoming = {
        "schema_version": 1,
        "source_root": "/root",
        "inventory_sha256": "c" * 64,
        "duplicates": {},
        "agent_events": [],
        "sources": [
            {
                "source_id": "source-a",
                "path": "a.md",
                "sha256": "1" * 64,
                "likely_project": "p",
            }
        ],
    }
    first = merge_discovery_manifest(None, incoming, deleted_source_ids=set())
    second = merge_discovery_manifest(first, incoming, deleted_source_ids=set())
    assert first["sources"] == second["sources"]
    assert first["inventory_sha256"] == second["inventory_sha256"]
    assert first["duplicates"] == second["duplicates"]


def test_merge_reports_and_findings_drop_deleted_and_upsert() -> None:
    merged_manifest = {
        "inventory_sha256": "m" * 64,
        "duplicates": {},
        "sources": [
            {"source_id": "source-keep"},
            {"source_id": "source-new"},
        ],
    }
    prior_report = {
        "schema_version": 1,
        "classifications": {
            "source-keep": {"type": "architecture", "method": "deterministic"},
            "source-deleted": {"type": "unknown", "method": "deterministic"},
        },
        "documents_ingested": 2,
        "security_findings": 0,
        "injection_findings": 0,
        "duplicates": {},
    }
    current_report = {
        "schema_version": 1,
        "classifications": {
            "source-new": {"type": "roadmap", "method": "deterministic"},
        },
        "documents_ingested": 1,
        "security_findings": 0,
        "injection_findings": 0,
        "duplicates": {},
    }
    report = merge_ingestion_report(
        prior_report,
        current_report,
        merged_manifest=merged_manifest,
        deleted_source_ids={"source-deleted"},
    )
    assert set(report["classifications"]) == {"source-keep", "source-new"}
    assert report["documents_ingested"] == 1
    assert report["inventory_sha256"] == "m" * 64

    secrets = merge_secret_findings(
        [
            {"source_id": "source-keep", "pattern": "aws", "hint": "x"},
            {"source_id": "source-deleted", "pattern": "aws", "hint": "y"},
        ],
        [{"source_id": "source-new", "pattern": "token", "hint": "z"}],
        deleted_source_ids={"source-deleted"},
        active_source_ids={"source-keep", "source-new"},
    )
    assert {(item["source_id"], item["pattern"]) for item in secrets} == {
        ("source-keep", "aws"),
        ("source-new", "token"),
    }

    injections = merge_injection_findings(
        {
            "schema_version": 1,
            "findings": [
                {"source_id": "source-keep", "rule": "r1"},
                {"source_id": "source-deleted", "rule": "r2"},
            ],
        },
        [{"source_id": "source-new", "rule": "r3"}],
        deleted_source_ids={"source-deleted"},
        active_source_ids={"source-keep", "source-new"},
    )
    assert {(item["source_id"], item["rule"]) for item in injections} == {
        ("source-keep", "r1"),
        ("source-new", "r3"),
    }
