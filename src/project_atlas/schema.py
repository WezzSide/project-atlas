"""JSON schema loading and validation for domain records (B-007).

Schemas ship as package data under ``project_atlas/schemas`` so validation
works from an installed package without a repository checkout (ADR-001).
Each domain model can be validated against its canonical schema, keeping
the Pydantic models and the published JSON contract in lockstep.
"""

from __future__ import annotations

import json
from functools import cache, lru_cache
from importlib import resources
from typing import Any

import jsonschema
from pydantic import BaseModel
from referencing import Registry, Resource

_SCHEMA_PACKAGE = "project_atlas.schemas"

#: Maps record kind to its schema file name.
SCHEMA_FILES: dict[str, str] = {
    "source-record": "source-record.schema.json",
    "source-registry": "source-registry.schema.json",
    "concept-record": "concept-record.schema.json",
    "claim": "claim.schema.json",
    "provenance-reference": "provenance-reference.schema.json",
    "conflict-record": "conflict-record.schema.json",
    "authority-record": "authority-record.schema.json",
    "claim-lifecycle": "claim-lifecycle.schema.json",
    "review-entry": "review-entry.schema.json",
    "validation-finding": "validation-finding.schema.json",
    "semantic-records": "semantic-records.schema.json",
    "claim-alias": "claim-alias.schema.json",
    "parser-output": "parser-output.schema.json",
    "diagnostic": "diagnostic.schema.json",
    "knowledge-answer": "knowledge-answer.schema.json",
    "knowledge-multifield-answer": "knowledge-multifield-answer.schema.json",
    "query-diagnostic": "query-diagnostic.schema.json",
    # AS-QUERY-MULTI-001 — tip-safe multi-subject / multi-project PLAN (≠ answer / authority)
    "query-multi-plan": "query-multi-plan.schema.json",
    # AS-EXPLAIN-001 — explainability / provenance receipts (operational metadata ≠ authority)
    "explain-receipt": "explain-receipt.schema.json",
    # AS-EXPLAIN-001 Band B — graph explain sidecars (derived enrichment ≠ query winner)
    "explain-graph-sidecar": "explain-graph-sidecar.schema.json",
    # AS-GRAPH-001 — Graphify acceptance (derived-only; not domain authority)
    "graphify-envelope": "graphify-envelope.schema.json",
    "graphify-node": "graphify-node.schema.json",
    "graphify-edge": "graphify-edge.schema.json",
    "graphify-metadata": "graphify-metadata.schema.json",
    "graph-acceptance-receipt": "graph-acceptance-receipt.schema.json",
    # AS-GRAPH-002 — Deterministic entity resolution (derived-only; not authority)
    "graph-resolved-node": "graph-resolved-node.schema.json",
    "graph-identity-explanation": "graph-identity-explanation.schema.json",
    # AS-OBS-001 - operational health snapshot (non-authoritative ops plane)
    "ops-health-snapshot": "ops-health-snapshot.schema.json",
    # AS-OBS-002 - operational event stream (OPS-EVT-*; non-authoritative ops plane)
    "ops-event": "ops-event.schema.json",
    "ops-event-stream": "ops-event-stream.schema.json",
    # AS-OBS-003 - ops-report projection (consume OBS-001 snapshot; optional OBS-002 events)
    "ops-report": "ops-report.schema.json",
    # AS-GRAPH-003 - Canonical derived relationships (not authority; not CP relationships/)
    "graph-relationship": "graph-relationship.schema.json",
    "graph-relationship-quarantine": "graph-relationship-quarantine.schema.json",
    # AS-GRAPH-004 - Durable quarantine / health / incremental (derived; never authority)
    "graph-quarantine-record": "graph-quarantine-record.schema.json",
    "graph-quarantine-receipt": "graph-quarantine-receipt.schema.json",
    "graph-health-snapshot": "graph-health-snapshot.schema.json",
    "graph-incremental-state": "graph-incremental-state.schema.json",
    # AS-XPROJ-001 - Global entity identity registry (derived; not automatic authority)
    "xproj-global-entity": "xproj-global-entity.schema.json",
    "xproj-join-key": "xproj-join-key.schema.json",
    "xproj-quarantine-candidate": "xproj-quarantine-candidate.schema.json",
    # AS-XPROJ-002 - Cross-project edges between registered globals (derived; no name-merge)
    "xproj-global-edge": "xproj-global-edge.schema.json",
    "xproj-edge-quarantine": "xproj-edge-quarantine.schema.json",
    # AS-XPROJ-004 - Conflict intelligence + global derived indexes (derived; not RET-001)
    "xproj-conflict-report": "xproj-conflict-report.schema.json",
    "xproj-index-document": "xproj-index-document.schema.json",
    # AS-XPROJ-003 - Duplicate / successor review candidates (derived; no autocollapse)
    "xproj-duplicate-candidate": "xproj-duplicate-candidate.schema.json",
    # AS-BACKUP-001 — verified snapshot / restore (operational durability ≠ authority)
    "backup-manifest": "backup-manifest.schema.json",
    "backup-meta": "backup-meta.schema.json",
    "backup-receipt": "backup-receipt.schema.json",
    # AS-INCR-COMPILE-001 tip-safe compile-cache receipt (not authority/GRAPH/XPROJ)
    "compile-cache-receipt": "compile-cache-receipt.schema.json",
    # AS-INT-009 — raw package / receipt retention (operational; not authority)
    "event-retention-policy": "event-retention-policy.schema.json",
    "event-retention-report": "event-retention-report.schema.json",
    # AS-INT-010 — removed-package / deletion tombstone projection (operational)
    "event-tombstone-index": "event-tombstone-index.schema.json",
    # AS-INT-011 — receipt revocation / invalidation (operational; never authority)
    "receipt-revocation-index": "receipt-revocation-index.schema.json",
    # AS-INT-012 — schema compatibility / migration report (operational)
    "schema-compat-report": "schema-compat-report.schema.json",
    # AS-CORE2-010 — fixture-safe lifecycle certification (≠ estate PILOT PASS)
    "lifecycle-cert-report": "lifecycle-cert-report.schema.json",
    # AS-ADV-RELEASE-001 — recovery/determinism/clean-clone/perf fixture cert (≠ RELEASE CERTIFIED)
    "adv-release-cert-report": "adv-release-cert-report.schema.json",
    # AS-SYNC-001-SCAFFOLD — dry-run workspace registry (≠ production SYNC-001 / PILOT)
    "workspace-registry-dry-run": "workspace-registry-dry-run.schema.json",
    # AS-SYNC-002-SCAFFOLD — dry-run sync plan (≠ production SYNC-002 / PILOT)
    "sync-plan-dry-run": "sync-plan-dry-run.schema.json",
    # AS-SYNC-003-SCAFFOLD - dry-run queue/retry/resume/receipt stubs
    "sync-queue-dry-run": "sync-queue-dry-run.schema.json",
    # AS-SYNC-004-SCAFFOLD - dry-run estate-receipt / trigger stubs
    "sync-receipts-dry-run": "sync-receipts-dry-run.schema.json",
    # AS-J-005 — derived impact graph projection (Graph≠authority; consume GRAPH-003)
    "impact-graph": "impact-graph.schema.json",
    # AS-2.0-COMPAT-001 — Atlas 1.0 compatibility anchor (2.0 packages must consume)
    "compatibility-anchor": "compatibility-anchor.schema.json",
    # AS-KF2-NS-001 / ENTITY-001 / REL-001 — Knowledge Fabric Wave 1 (derived ≠ authority)
    "kf2-namespace": "kf2-namespace.schema.json",
    "kf2-entity": "kf2-entity.schema.json",
    "kf2-relationship": "kf2-relationship.schema.json",
    # AS-2.0-FED-001 — operator-declared federation join inventory (consume-only)
    "federation-join-inventory": "federation-join-inventory.schema.json",
    # AS-2.0-PROV-001 — optional provider adapters + quarantine (≠ authority)
    "provider-adapter-registry": "provider-adapter-registry.schema.json",
    "provider-quarantine-envelope": "provider-quarantine-envelope.schema.json",
    # AS-2.0-AGENTOS-001 — governed session envelope (≠ Core authority)
    "agentos-session-envelope": "agentos-session-envelope.schema.json",
    # AS-2.0-RET-HYBRID-001 — hybrid retrieval plan (lexical + semantic slot disabled)
    "hybrid-retrieval-plan": "hybrid-retrieval-plan.schema.json",
    # AS-2.0-RET-HYBRID-001 P2 — Lexical/BM25/RRF fusion (semantic disabled; ≠ authority)
    "hybrid-retrieval-rrf": "hybrid-retrieval-rrf.schema.json",
    # AS-2.0-KCI-001 — consume-only compile request/receipt (≠ authority)
    "kci-compile-request": "kci-compile-request.schema.json",
    "kci-compile-receipt": "kci-compile-receipt.schema.json",
    # AS-2.0-CTX-001 — fixture-safe context packs (provenance pointers; ≠ estate facts)
    "context-pack": "context-pack.schema.json",
    # AS-2.2-RUNTIME-001 — hybrid retrieve + context compiler (derived; ≠ LLM authority)
    "runtime-hybrid-retrieval": "runtime-hybrid-retrieval.schema.json",
    "runtime-context-compiler": "runtime-context-compiler.schema.json",
    # AS-2.2-ASK2-001 — Ask Atlas 2 answer lens (project-scoped hybrid + p2 compiler;
    # derived; UNKNOWN stays UNKNOWN; legacy matches subordinate; ≠ authority)
    "ask-atlas-2-answer": "ask-atlas-2-answer-lens.schema.json",
    # AS-2.0-TEMPORAL-001 — bitemporal claim validity windows (deepens AS-CORE-005)
    "claim-validity-window": "claim-validity-window.schema.json",
    "claim-validity-catalog": "claim-validity-catalog.schema.json",
    "bitemporal-as-of-result": "bitemporal-as-of-result.schema.json",
    # AS-2.2-KDIFF-001 — Knowledge Diff / Time Machine P0 (read-only as-of + T1->T2 diff;
    # derived ≠ authority; consumes AS-2.0-TEMPORAL-001 + AS-CORE state; never writes)
    "kdiff-as-of-snapshot": "kdiff-as-of-snapshot.schema.json",
    "kdiff-record": "kdiff-record.schema.json",
    # AS-2.0-REALITY-GAP-001 — fixture-only reality-gap inventory (≠ PILOT / estate)
    "reality-gap-inventory": "reality-gap-inventory.schema.json",
    # AS-2.0-TWIN-FIXTURE-001 — disposable twin projections (≠ TWIN production / PILOT)
    "twin-projection-fixture": "twin-projection-fixture.schema.json",
    # AS-2.0-OAI-IMPORT-001 — OpenAI importer fixture receipt (no live API)
    "openai-import-fixture-receipt": "openai-import-fixture-receipt.schema.json",
    # AS-2.0-OBS-UX-001 — Obsidian non-canonical lens registry (≠ plugin / ≠ canonical)
    "obsidian-lens-registry": "obsidian-lens-registry.schema.json",
    # AS-2.0-MCP-001 — deny-by-default MCP tool registry (≠ live server)
    "mcp-tool-registry": "mcp-tool-registry.schema.json",
    # AS-2.0-UX-002 — Advanced Command Center mode catalog (≠ UI rewrite)
    "ux-mode-catalog": "ux-mode-catalog.schema.json",
    # AS-2.0-CTX-002 — context pack composition deepen (≠ estate facts)
    "context-pack-composition": "context-pack-composition.schema.json",
    # AS-2.0-AGENTOS-002 — Agent OS phase transition deepen (≠ Core authority)
    "agentos-phase-transition": "agentos-phase-transition.schema.json",
    # AS-2.0-OBS-UX-002 — Obsidian workspace binding (≠ plugin)
    "obsidian-workspace-binding": "obsidian-workspace-binding.schema.json",
    # AS-2.0-AUTONOMY-001 — Autonomy L0-L5 catalog (live=false)
    "autonomy-level-catalog": "autonomy-level-catalog.schema.json",
    # AS-2.0-WEB-ASK-001 — Ask Atlas read-only contract
    "web-ask-atlas-contract": "web-ask-atlas-contract.schema.json",
    # AS-2.0-WEB-SURFACE-001 — Twin UI/Canvas/Timeline catalog
    "web-surface-catalog": "web-surface-catalog.schema.json",
    # AS-KF2-002 — fabric inventory export (≠ authority)
    "kf2-fabric-inventory": "kf2-fabric-inventory.schema.json",
    # AS-2.0-FED-002 — federation read lens (≠ cross-vault promote)
    "federation-read-lens": "federation-read-lens.schema.json",
    # AS-2.0-INBOX-001 — Knowledge Inbox quarantine receipt
    "knowledge-inbox-receipt": "knowledge-inbox-receipt.schema.json",
    # AS-CODER-ALPHA-CONVERSATIONAL-CAPTURE-001 — quarantined conversation envelope
    "conversation-capture": "conversation-capture.schema.json",
    # AS-OBSIDIAN-CAPTURE-001 - raw capture evidence (quarantined; never authority)
    "raw-capture": "raw-capture.schema.json",
    # AS-2.0-SCHED-001 — autonomy scheduler dry-run
    "scheduler-dry-run": "scheduler-dry-run.schema.json",
    # AS-2.0-SEC-001 — continuous security receipt (metadata only)
    "security-continuous-receipt": "security-continuous-receipt.schema.json",
    # AS-2.0-API-001 — API 2.0 surface registry (write_enabled=false)
    "api-surface-registry": "api-surface-registry.schema.json",
    # AS-2.0-CHATGPT-CAPTURE-001 — fixture ChatGPT capture (no live API)
    "chatgpt-capture-receipt": "chatgpt-capture-receipt.schema.json",
    # AS-2.0-ESTATE-INTEL-001 — fixture estate intel (≠ PILOT PASS)
    "estate-intel-fixture": "estate-intel-fixture.schema.json",
    # AS-2.0-TWIN-FIXTURE-002 — twin fixture scenarios (≠ production)
    "twin-fixture-scenario": "twin-fixture-scenario.schema.json",
    # AS-2.0-AGENT-EVAL-001 — agent eval/shadow receipt
    "agent-eval-shadow-receipt": "agent-eval-shadow-receipt.schema.json",
    # AS-2.2-EVAL-001 — eval substrate score receipt (hidden holdouts)
    "eval-score-receipt": "eval-score-receipt.schema.json",
    # AS-2.2-EVAL-BROKER-001 — bounded out-of-process scoring broker result
    "scoring-broker-result": "scoring-broker-result.schema.json",
    # AS-OPT-GATE-001 — governed experiment / promotion receipt (≠ OPT wake)
    "opt-experiment-receipt": "opt-experiment-receipt.schema.json",
    # AS-ORCH-001A — agent result envelope (evidence ≠ authority; classify ≠ execute)
    "agent-result-envelope": "agent-result-envelope.schema.json",
    "agent-inbox-message": "agent-inbox-message.schema.json",
    "mailbox-successor-binding-v1": "mailbox-successor-binding-v1.schema.json",
    # AS-ORCH-001B — typed task directive + discriminated routing output
    "task-directive": "task-directive.schema.json",
    "orchestration-route": "orchestration-route.schema.json",
    # AS-ORCH-001C-R1 — transport-neutral handoff packet (≠ dispatch / authority)
    "handoff-packet": "handoff-packet.schema.json",
    # AS-ORCH-001D — single-hop dispatch identity / receipt (≠ authority / ≠ 001E loop)
    "dispatch-record": "dispatch-record.schema.json",
    "dispatch-receipt": "dispatch-receipt.schema.json",
    # AS-ORCH-AUTONOMY-001 — autonomous governor public report (≠ merge / 001E)
    "autonomy-governor-report": "autonomy-governor-report.schema.json",
    "autonomy-work-node": "autonomy-work-node.schema.json",
    "autonomy-lease": "autonomy-lease.schema.json",
    "autonomy-trusted-anchor": "autonomy-trusted-anchor.schema.json",
    "autonomy-loop-state": "autonomy-loop-state.schema.json",
    # AS-2.0-OAI-IMPORT-002 — OAI import path (fixtures if no export)
    "openai-import-path-receipt": "openai-import-path-receipt.schema.json",
    # AS-2.0-REALITY-GAP-UI-001 — Reality Gap UI catalog
    "reality-gap-ui-catalog": "reality-gap-ui-catalog.schema.json",
    # AS-2.0-KCI-HARNESS-001 — Knowledge CI harness
    "knowledge-ci-harness": "knowledge-ci-harness.schema.json",
    # AS-2.0-COLLAB-001 — collaboration stubs (live=false)
    "collaboration-stub-registry": "collaboration-stub-registry.schema.json",
    # AS-2.0-SCALE-001 — scale harness plan (live_load=false)
    "scale-harness-plan": "scale-harness-plan.schema.json",
    # AS-2.0-SEC-ADV-001 — advanced security control matrix
    "security-adv-matrix": "security-adv-matrix.schema.json",
    # AS-2.0-SYNC-001 — production sync plan under final-cert fixture waiver
    "sync-production-plan": "sync-production-plan.schema.json",
    # AS-2.0-TWIN-001 — production twin projection under final-cert fixture waiver
    "twin-production-projection": "twin-production-projection.schema.json",
    # AS-PROJECT-ROADMAP-001 — derived Living Project Roadmap V1 (≠ canonical)
    "project-roadmap": "project-roadmap.schema.json",
}


class SchemaValidationError(ValueError):
    """Raised when a record fails validation against its JSON schema."""


def available_schemas() -> list[str]:
    """Return the record kinds that have a shipped JSON schema."""
    return sorted(SCHEMA_FILES)


def _read_schema(file_name: str) -> dict[str, Any]:
    resource = resources.files(_SCHEMA_PACKAGE).joinpath(file_name)
    return json.loads(resource.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


@lru_cache(maxsize=1)
def _registry() -> Registry[Any]:
    """Registry resolving cross-file ``$ref`` targets by schema ``$id``."""
    pairs = [
        (schema["$id"], Resource.from_contents(schema))
        for schema in (_read_schema(name) for name in SCHEMA_FILES.values())
    ]
    return Registry().with_resources(pairs)


@cache
def load_schema(kind: str) -> dict[str, Any]:
    """Load a shipped schema by record kind (e.g. ``source-record``)."""
    try:
        file_name = SCHEMA_FILES[kind]
    except KeyError:
        raise KeyError(f"unknown schema kind: {kind!r}; available: {available_schemas()}") from None
    schema = _read_schema(file_name)
    jsonschema.Draft202012Validator.check_schema(schema)
    return schema


def validate_record(record: BaseModel | dict[str, Any], kind: str) -> None:
    """Validate a domain record against its shipped JSON schema.

    ``record`` may be a Pydantic model (dumped in JSON mode) or a plain
    mapping. Raises :class:`SchemaValidationError` on the first violation.
    """
    schema = load_schema(kind)
    instance = record.model_dump(mode="json") if isinstance(record, BaseModel) else record
    validator = jsonschema.Draft202012Validator(schema, registry=_registry())
    error = next(validator.iter_errors(instance), None)
    if error is not None:
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        raise SchemaValidationError(f"{kind} record violates schema at {location}: {error.message}")
