"""Unit tests for JSON schema validation (B-007)."""

from __future__ import annotations

import pytest

from project_atlas.domain import (
    Claim,
    ConceptRecord,
    ConflictRecord,
    ProjectRecord,
    ProvenanceReference,
    SourceRecord,
    ValidationFinding,
)
from project_atlas.schema import (
    SchemaValidationError,
    available_schemas,
    load_schema,
    validate_record,
)


def test_all_expected_schemas_available() -> None:
    assert available_schemas() == [
        "adv-release-cert-report",
        "agent-eval-shadow-receipt",
        "agent-result-envelope",
        "agentos-phase-transition",
        "agentos-session-envelope",
        "api-surface-registry",
        "ask-atlas-2-answer",
        "authority-record",
        "autonomy-governor-report",
        "autonomy-lease",
        "autonomy-level-catalog",
        "autonomy-loop-state",
        "autonomy-trusted-anchor",
        "autonomy-work-node",
        "backup-manifest",
        "backup-meta",
        "backup-receipt",
        "bitemporal-as-of-result",
        "chatgpt-capture-receipt",
        "claim",
        "claim-alias",
        "claim-lifecycle",
        "claim-validity-catalog",
        "claim-validity-window",
        "collaboration-stub-registry",
        "compatibility-anchor",
        "compile-cache-receipt",
        "concept-record",
        "conflict-record",
        "context-pack",
        "context-pack-composition",
        "conversation-capture",
        "diagnostic",
        "dispatch-receipt",
        "dispatch-record",
        "estate-intel-fixture",
        "eval-score-receipt",
        "event-retention-policy",
        "event-retention-report",
        "event-tombstone-index",
        "explain-graph-sidecar",
        "explain-receipt",
        "federation-join-inventory",
        "federation-read-lens",
        "graph-acceptance-receipt",
        "graph-health-snapshot",
        "graph-identity-explanation",
        "graph-incremental-state",
        "graph-quarantine-receipt",
        "graph-quarantine-record",
        "graph-relationship",
        "graph-relationship-quarantine",
        "graph-resolved-node",
        "graphify-edge",
        "graphify-envelope",
        "graphify-metadata",
        "graphify-node",
        "handoff-packet",
        "hybrid-retrieval-plan",
        "hybrid-retrieval-rrf",
        "impact-graph",
        "kci-compile-receipt",
        "kci-compile-request",
        "kdiff-as-of-snapshot",
        "kdiff-record",
        "kf2-entity",
        "kf2-fabric-inventory",
        "kf2-namespace",
        "kf2-relationship",
        "knowledge-answer",
        "knowledge-ci-harness",
        "knowledge-inbox-receipt",
        "knowledge-multifield-answer",
        "lifecycle-cert-report",
        "mcp-tool-registry",
        "obsidian-lens-registry",
        "obsidian-workspace-binding",
        "openai-import-fixture-receipt",
        "openai-import-path-receipt",
        "ops-event",
        "ops-event-stream",
        "ops-health-snapshot",
        "ops-report",
        "opt-experiment-receipt",
        "orchestration-route",
        "parser-output",
        "project-roadmap",
        "provenance-reference",
        "provider-adapter-registry",
        "provider-quarantine-envelope",
        "query-diagnostic",
        "query-multi-plan",
        "raw-capture",
        "reality-gap-inventory",
        "reality-gap-ui-catalog",
        "receipt-revocation-index",
        "review-entry",
        "runtime-context-compiler",
        "runtime-hybrid-retrieval",
        "scale-harness-plan",
        "scheduler-dry-run",
        "schema-compat-report",
        "scoring-broker-result",
        "security-adv-matrix",
        "security-continuous-receipt",
        "semantic-records",
        "source-record",
        "source-registry",
        "sync-plan-dry-run",
        "sync-production-plan",
        "sync-queue-dry-run",
        "sync-receipts-dry-run",
        "task-directive",
        "twin-fixture-scenario",
        "twin-production-projection",
        "twin-projection-fixture",
        "ux-mode-catalog",
        "validation-finding",
        "web-ask-atlas-contract",
        "web-surface-catalog",
        "workspace-registry-dry-run",
        "xproj-conflict-report",
        "xproj-duplicate-candidate",
        "xproj-edge-quarantine",
        "xproj-global-edge",
        "xproj-global-entity",
        "xproj-index-document",
        "xproj-join-key",
        "xproj-quarantine-candidate",
    ]


def test_schemas_load_and_are_valid() -> None:
    for kind in available_schemas():
        schema = load_schema(kind)
        assert schema["$schema"].startswith("https://json-schema.org/")


def test_unknown_schema_kind_rejected() -> None:
    with pytest.raises(KeyError):
        load_schema("nope")


def test_valid_records_pass() -> None:
    prov = ProvenanceReference(source_id="src-1", resource="sources/x.md")
    source = SourceRecord(source_id="src-1", path="a.md", media_type="text/markdown", size_bytes=1)
    validate_record(source, "source-record")
    concept = ConceptRecord(concept_id="c-1", type="Project", title="t", sources=[prov])
    validate_record(concept, "concept-record")
    claim = Claim(
        claim_id="clm-1", subject="c-1", field="status", value="active", provenance=[prov]
    )
    validate_record(claim, "claim")
    validate_record(prov, "provenance-reference")
    validate_record(
        ConflictRecord(
            conflict_id="conf-1",
            subject="c-1",
            field="v",
            claims=[{"source_id": "s-1", "claim": "a"}, {"source_id": "s-2", "claim": "b"}],
        ),
        "conflict-record",
    )
    finding = ValidationFinding(
        finding_id="f-1", rule_id="r", severity="info", gate="content", message="m"
    )
    validate_record(finding, "validation-finding")
    validate_record(ProjectRecord(project_id="p-1", name="Project"), "semantic-records")


def test_invalid_record_fails_schema() -> None:
    with pytest.raises(SchemaValidationError):
        validate_record({"source_id": "x"}, "source-record")


def test_schema_rejects_unknown_semantic_vocabularies() -> None:
    with pytest.raises(SchemaValidationError):
        validate_record(
            {
                "claim_id": "clm-1",
                "subject": "c-1",
                "claim_type": "future-claim-type",
                "field": "status",
                "value": "active",
                "provenance": [{"source_id": "src-1", "resource": "sources/x.md"}],
                "verification": "unreviewed",
            },
            "claim",
        )
    with pytest.raises(SchemaValidationError):
        validate_record(
            {
                "schema_version": 1,
                "review_id": "review-1",
                "project_id": "p-1",
                "category": "future-review-category",
                "subject_id": "clm-1",
                "reason": "test",
                "source_ids": [],
                "status": "pending",
            },
            "review-entry",
        )
    with pytest.raises(SchemaValidationError):
        validate_record(
            {
                "conflict_id": "conf-1",
                "subject": "c-1",
                "field": "status",
                "claims": [
                    {"source_id": "s-1", "claim": "a"},
                    {"source_id": "s-2", "claim": "b"},
                ],
                "conflict_type": "future-conflict-type",
            },
            "conflict-record",
        )


def test_semantic_schema_rejects_invalid_nested_records() -> None:
    with pytest.raises(SchemaValidationError):
        validate_record(
            {
                "schema_version": 1,
                "project_id": "p-1",
                "name": "Project",
                "generated": True,
                "sources": [
                    {
                        "schema_version": 1,
                        "source_id": "../escape",
                        "path": "README.md",
                        "sha256": None,
                        "lifecycle": "verified",
                        "first_seen": None,
                        "last_seen": None,
                        "previous_sha256": None,
                        "renamed_from": None,
                    }
                ],
                "authority": [],
                "coverage": [],
                "concepts": [],
                "claims": [],
                "agent_events": [],
                "validations": [],
                "decisions": [],
                "relationships": [],
            },
            "semantic-records",
        )


def test_cross_file_ref_resolves() -> None:
    """concept-record.sources $ref to provenance-reference must resolve."""
    with pytest.raises(SchemaValidationError):
        validate_record(
            {
                "concept_id": "c-1",
                "type": "Project",
                "title": "t",
                "sources": [{"resource": "x.md"}],  # missing source_id
            },
            "concept-record",
        )
