# Executable Backlog

## Epic A — Repository foundation

- [x] A-001 Create `pyproject.toml`
- [x] A-002 Create `src/project_atlas`
- [x] A-003 Configure pytest
- [x] A-004 Add ruff and mypy configuration
- [x] A-005 Implement structured logging
- [x] A-006 Add CI workflow
- [x] A-007 Add `atlas --help`

## Epic B — Domain model

- [x] B-001 Implement `SourceRecord`
- [x] B-002 Implement `ConceptRecord`
- [x] B-003 Implement `Claim`
- [x] B-004 Implement `ProvenanceReference`
- [x] B-005 Implement `ConflictRecord`
- [x] B-006 Implement `ValidationFinding`
- [x] B-007 Validate against JSON schemas

## Epic C — Discovery

- [x] C-001 Recursive source scanner
- [x] C-002 Include/exclude configuration
- [x] C-003 MIME and extension detection
- [x] C-004 Streaming SHA-256 hashing
- [x] C-005 Exact duplicate grouping
- [x] C-006 Manifest persistence
- [x] C-007 Unsupported file reporting
- [x] C-008 Path traversal tests

## Epic D — Parsing

- [x] D-001 Markdown parser
- [x] D-002 YAML frontmatter parser
- [x] D-003 Plain-text parser
- [x] D-004 Heading extraction
- [x] D-005 Link extraction
- [x] D-006 Parser registry
- [x] D-007 Malformed input tests

## Epic E — Classification

- [x] E-001 Explicit override rules
- [x] E-002 Path rules
- [x] E-003 Filename rules
- [x] E-004 Frontmatter rules
- [x] E-005 Heading rules
- [x] E-006 Classification method audit field
- [x] E-007 Unknown classification path

## Epic F — Generation

- [x] F-001 Stable ID strategy
- [x] F-002 Frontmatter renderer
- [x] F-003 Project note renderer
- [x] F-004 Source reference renderer
- [x] F-005 Conflict renderer
- [x] F-006 Deterministic ordering
- [x] F-007 Atomic file writes

## Epic G — Human-safe updates

- [x] G-001 Protected marker parser
- [x] G-002 Generated-region replacement
- [x] G-003 Human-region preservation
- [x] G-004 Fail-closed malformed marker handling
- [x] G-005 Golden-file tests

## Epic H — Validation

- [x] H-001 YAML validator
- [x] H-002 Schema validator
- [x] H-003 Link validator
- [x] H-004 Provenance validator
- [x] H-005 Lifecycle validator
- [x] H-006 Freshness validator
- [x] H-007 Orphan validator
- [x] H-008 Secret scanner
- [x] H-009 Coverage validator
- [x] H-010 Severity exit codes

## Epic I — Portfolio intelligence

_Remaining items (I-002 through I-005, I-007, I-008) are implemented
under **AS-MVP-001** on `feat/as-mvp-001-portfolio-pilots`
(implementation complete, acceptance-tested, awaiting independent
verification and owner merge; see
`docs/adr/ADR-005-mvp-portfolio-intelligence-pilot-onboarding.md` and
`docs/evidence/AS-MVP-001-receipt.yaml`). Left unchecked until
independent verification passes and the branch is merged to `main`._

- [x] I-001 Project index generator
- [x] I-002 Portfolio overview — implemented, acceptance-tested (AS-MVP-001); pending independent verification/merge
- [x] I-003 Maturity matrix — implemented, acceptance-tested (AS-MVP-001); pending independent verification/merge
- [x] I-004 Documentation gap report — implemented, acceptance-tested (AS-MVP-001); pending independent verification/merge
- [x] I-005 Stale knowledge report — implemented, acceptance-tested (AS-MVP-001); pending independent verification/merge
- [x] I-006 Conflict review queue
- [x] I-007 Dependency report — implemented, acceptance-tested (AS-MVP-001); pending independent verification/merge
- [x] I-008 Capability report — implemented, acceptance-tested (AS-MVP-001); pending independent verification/merge

## Epic J — Incremental operation

- [x] J-001 State cache
- [x] J-002 Added source detection
- [x] J-003 Changed source detection
- [x] J-004 Removed source handling
- [x] J-005 Impact graph
- [x] J-006 Selective regeneration

## Epic K — Pilot onboarding

_Items are implemented under **AS-MVP-001** on
`fix/as-mvp-001-r1-relation-edge-tests` (originally
`feat/as-mvp-001-portfolio-pilots`; see
`docs/adr/ADR-005-mvp-portfolio-intelligence-pilot-onboarding.md` and
`docs/evidence/AS-MVP-001-receipt.yaml`). All of K-001 through K-007 are
now implemented and acceptance-tested (AS-MVP-001-R1 release-closure
remediation: golden manifest/output fixtures for K-004/K-005, an
itemized contradiction checklist for K-006, and a dedicated
credential-shaped canary fixture for K-007). Left unchecked until final
independent verification passes and the branch is merged to `main`._

- [x] K-001 Nebula fixture corpus — implemented, acceptance-tested (AS-MVP-001); pending independent verification/merge
- [x] K-002 Black Agency OS fixture corpus — implemented, acceptance-tested (AS-MVP-001); pending independent verification/merge
- [x] K-003 Dark Factory fixture corpus — implemented, acceptance-tested (AS-MVP-001); pending independent verification/merge
- [x] K-004 Expected manifests — implemented, acceptance-tested (AS-MVP-001-R1); pending independent verification/merge
- [x] K-005 Expected generated vault — implemented, acceptance-tested (AS-MVP-001-R1); pending independent verification/merge
- [x] K-006 Contradiction fixtures — implemented, acceptance-tested (AS-MVP-001-R1); pending independent verification/merge
- [x] K-007 Secret fixtures — implemented, acceptance-tested (AS-MVP-001-R1); pending independent verification/merge

## Cross-cutting follow-up — Atlas Core vertical slice

## Epic L — Repository governance

- [x] L-001 AS-GH-001 GitHub repository governance baseline — tip artifacts present (`GOVERNANCE.md`, `VERSIONING.md`, `RELEASING.md`, `SUPPORT.md`, `CODE_OF_CONDUCT.md`, issue templates, README nav, additive governance tests, `docs/adr/ADR-006-github-repository-governance-baseline.md`, `docs/work-packages/AS-GH-001.md`, `docs/evidence/AS-GH-001-receipt.yaml`); docs reconciliation on `feat/as-l-001-governance-close` — **GOVERNOR REQUIRED**; live settings activation deferred (AS-GH-002 owner-gated).

- [x] CORE-MODEL-001 Integrate `ConceptRecord`, `Claim`, and
  `ProvenanceReference` into formal project projections and richer validated
  project frontmatter — **CLOSED — SATISFIED BY** MODEL-001A + 001B + 001C on tip
  per `PROJECT-ATLAS-1.0-RECONCILED-STATE.md`. Residual richer frontmatter polish
  is optional deferred, not a reopen.
- [x] AS-INGEST-MANIFEST-001 Multi-batch discovery snapshot and ingest-report
  merge by `source_id` — implementation complete on
  `feat/as-ingest-manifest-001` (closes AS-MVP-001 accepted overwrite debt;
  governor review required; not self-certified).
- [x] CORE-OPS-001 Add explicit read-before-write/hash-before-replace
  accounting and evidence for filesystem-write suppression on unchanged
  replay. Keep `content drift` and `canonical content changes` distinct from
  physical filesystem writes. (AS-CORE-OPS-001 on
  `feat/as-core-ops-001-promote-accounting` — governor review required; not
  self-certified.)
- [x] AS-BACKUP-001 Verified Atlas Snapshot (fixture restore; cold D1-D4+D6; ops durability ≠ authority)
- [x] AS-DEMO-2.2-RECOVERY-ID-001 Fresh product bootstrap establishes canonical
  `.atlas/vault.json` so stranger/demo pipelines are recovery-capable without
  weakening snapshot/restore identity trust (D-PROJECT-ATLAS-CLOUD-DEMO-RECOVERY-019)
- [x] CORE-SEC-001 Implement content-based secret detection and redaction for
  pilot ingestion. Filename-only sensitive-file detection must not be treated
  as sufficient for real-project ingestion.

## AS-INT-001 — Governed agent-event ingestion

- [x] INT-001 Define versioned shared event/provenance/receipt contracts
- [x] INT-002 Discover and classify Control Plane event packages
- [x] INT-003 Revalidate packages at Core ingestion boundary
- [x] INT-004 Generate project activity and session projections
- [x] INT-005 Quarantine invalid, pending and conflicting packages
- [x] INT-006 Prove deterministic replay and strict public CLI workflow
- [x] INT-007 Independent integration certification
- [x] INT-008 Bind event skill identity to a trusted Vault policy
- [x] INT-009 Define raw-package and receipt retention policy
- [x] INT-010 Preserve removed-package/deletion state in projections
  (AS-INT-010 tip-safe tombstone projection on
  `feat/as-int-010-tombstones`; IMPLEMENTATION COMPLETE — GOVERNOR REQUIRED)
- [x] INT-011 Define receipt revocation and invalidation semantics
  (AS-INT-011 tip-safe operational index under
  `generated/ops/receipt-revocations.json`; never Layer B / never authority)
- [x] INT-012 Define schema migration and compatibility tooling
  (AS-INT-012 tip-safe compat/migrate-dry-run report under
  `generated/ops/schema-compat-report.json`; never Layer B / never authority)
- [ ] INT-013 Run the bounded multi-project integration pilot

## AS-CORE-002 — Semantic domain model and source lifecycle hardening

- [x] CORE2-001 Add strict versioned Project, source lifecycle, authority,
  coverage, validation, decision, work-package and agent-event reference models
- [x] CORE2-002 Add semantic record schema and schema validation coverage
- [x] CORE2-003 Compile deterministic rich project metadata and coverage
- [x] CORE2-004 Preserve human regions and fail closed on malformed markers
- [x] CORE2-005 Add content-based secret findings with metadata-only output
- [x] CORE2-006 Persist source lifecycle state and deletion tombstones
- [x] CORE2-007 Complete ConceptRecord/Claim projection composition and migrations
  — **CLOSED — SATISFIED BY** MODEL-001A/B/C composition on tip per
  `PROJECT-ATLAS-1.0-RECONCILED-STATE.md`. Stale “001B pending merge” prose
  **SUPERSEDED**.
- [x] CORE2-008 Add duplicate-source conflict projections and authority review queue
  (AS-CORE2-008 tip-safe residual on `feat/as-core2-008-review-queue`; IMPLEMENTATION COMPLETE — GOVERNOR REQUIRED)
- [x] CORE2-009 Add interrupted-write recovery and complete write accounting
  (AS-CORE2-009 tip-safe crash orphan recovery on
  `feat/as-core2-009-promote-recovery`; IMPLEMENTATION COMPLETE — GOVERNOR REQUIRED;
  contract refresh under D-PROJECT-ATLAS-WEB-AND-1.0-AUTONOMOUS-COMPLETION-001)
- [x] CORE2-010 Run controlled pilot lifecycle certification
  (AS-CORE2-010 fixture-safe matrix; estate_pilot_passed=false)

## AS-ID-001 — Durable Source Lineage Identity

- [x] ID-001 Add UUIDv4 project genesis with injected test providers
- [x] ID-002 Add Core-local project identity synchronization
- [x] ID-003 Add source registry v2 and durable lineage derivation
- [x] ID-004 Add canonical path and raw-byte fingerprint contracts
- [x] ID-005 Add atomic v1-to-v2 migration receipts
- [x] ID-006 Add duplicate-identity and ambiguity fail-closed checks
- [x] ID-007 Add replay, rollback, concurrency, and schema validation tests
- [x] ID-008 Governor review and independent certification

## AS-CORE-003 — Claim Identity v2 remediation

_Status: V2-003 was preserved and rejected by independent review. V2-004 implemented and validated the remediation but was superseded before review because its immutable tag annotation recorded an invalid tree string. V2-005 passed a fresh isolated review with three non-blocking findings on 2026-08-05, but its PR head failed ubuntu CI on platform-dependent `media_type` detection and K-004 fixture newline translation. V2-006 fixes both determinism defects additively, passed an isolated review addendum, and is green on Windows and Linux gates. Remote CI verification and Project Owner merge authorization remain pending._

- [x] CORE3-001 Finalize Claim Identity v2 formula using raw stable semantic locator
- [x] CORE3-002 Use `event:{event_id}` locator for agent-event-derived claims
- [x] CORE3-003 Restore ingestion project identity locks
- [x] CORE3-004 Fix OCC compare-and-swap `None` precondition handling
- [x] CORE3-005 Rewrite v1-to-v2 migration as self-contained, schema-validated, atomic, idempotent module
- [x] CORE3-006 Record ambiguous v1-to-v2 mappings instead of guessing
- [x] CORE3-007 Add `claim-alias` schema and register it
- [x] CORE3-008 Regenerate dependency-report.json golden fixture after identity-formula change
- [x] CORE3-009 Add concurrency, migration, historical-completeness and alias-schema tests
- [x] CORE3-010 Mark all `tests/integration/*.py` modules with `pytest.mark.integration`
- [x] CORE3-011 Add canonical JSON-array identity serialization to close F-001
- [x] CORE3-012 Enforce mutually-exclusive resolved/ambiguous alias collections to close F-002
- [x] CORE3-013 Extract shared `project_atlas.claim_identity` rules module for compiler/migration parity
- [x] CORE3-014 Normalize CRLF to LF before hashing text sources (cross-platform identity stability)
- [x] CORE3-015 Author ADR-007 for Claim Identity v2 canonicalization
- [x] CORE3-016 Regenerate K-004/K-005 golden fixtures for canonical identity and source-lineage changes
- [x] CORE3-017 Preserve the V2-003 independent-review failure and exact findings
- [x] CORE3-018 Reconstruct real v1 identities from ingested source/lineage/project evidence
- [x] CORE3-019 Scan all supported text evidence and share architecture fallback semantics
- [x] CORE3-020 Isolate migration state per safe project component
- [x] CORE3-021 Commit alias state and its receipt as one validated atomic bundle
- [x] CORE3-022 Reject incomplete, cross-project, or resolved/ambiguous replay state
- [x] CORE3-023 Add tested cross-file promotion rollback and artifact cleanup
- [x] CORE3-024 Preserve the malformed V2-004 annotation and supersede it without moving the tag
- [x] CORE3-025 Complete a fresh independent V2-005 review and certification
- [x] CORE3-027 Remediate ubuntu CI platform determinism (media types, K-004 fixture) and cut V2-006
- [ ] CORE3-026 Merge to `main`

## AS-EXT-001A — Structured evidence parsers, locator refinement, and compilation-status reporting

_Status: implementation complete through Level 0 self-host evidence. Directive D-PROJECT-ATLAS-KIMI-AS-EXT-001A-001; base `6d874751d3ed9cb05433a8d50ab372a997418d84`; addresses measured P0 failures (batch abort on first of 29 flat-YAML locator failures, 2 heading-locator collisions, 15 verified claims, 0 explicit anchors). Package contract: `docs/work-packages/AS-EXT-001A.md`. Self-host receipt: `docs/evidence/AS-EXT-001A-level0-selfhost-receipt.yaml`. Adversarial remediation complete (blocking locator-collision isolation fix + five concerns, commit 33bc65a); candidate re-frozen V2 with reconciled re-run — receipt `docs/evidence/AS-EXT-001A-level0-selfhost-receipt-v2.yaml` (supersedes V1, preserved)._

- [x] EXT1A-001 Create AS-EXT-001A package specification and confirm implementation baseline gates
- [x] EXT1A-002 Materialize frozen fixtures (real F-01…F-08 with provenance + authored synthetic cases, directive §9)
- [x] EXT1A-003 Implement compilation outcome state machine (§7.8: COMPLETE_CANDIDATE / PARTIAL_CANDIDATE / FAILED / PROMOTION_FAILED / COMPLETE)
- [x] EXT1A-004 Implement frozen Pydantic v2 parser-output contract (§7.2; no final claim identity in parser output)
- [x] EXT1A-005 Implement specific-first classification precedence (§7.1; keywords must not override structural classification)
- [x] EXT1A-006 Implement safe YAML parsing and `yamlpath:` locators (§7.4; NFC normalization, duplicate-key diagnostics)
- [x] EXT1A-007 Implement evidence receipt profiles (§7.5; concept mapping + profile adapters + unknown-field preservation)
- [x] EXT1A-008 Implement registered VERIFY structured profile (§7.6; zero collision, zero abort)
- [x] EXT1A-009 Remediate heading-locator collisions (§7.7; both official collision fixtures)
- [x] EXT1A-010 Implement structured diagnostic model (§7.9; no silent drop)
- [x] EXT1A-011 Implement locator-refinement alias handling via existing v2 mechanism (§7.10; one-to-one only)
- [x] EXT1A-012 Enforce security bounds (§8; safe loading, duplicate keys, bounded nesting/aliases/nodes/size)
- [x] EXT1A-013 Record self-host evidence: RAW corpus Level 0 + product metrics (§12/§13)
- [x] EXT1A-020 All 31 real receipts structurally parse with a support/profile status (§10 MUST)
- [x] EXT1A-021 One bad source does not prevent extraction from independent good sources (§10 MUST)
- [x] EXT1A-022 PARTIAL candidate never alters canonical state; COMPLETE alone may promote; promotion failure rolls back (§10 MUST)
- [x] EXT1A-023 Deterministic repeat produces byte-identical candidate output (§10 MUST)
- [x] EXT1A-024 Claim Identity v2 tests remain green; compiler and migration remain consistent (§10 MUST)
- [x] EXT1A-025 Aliases promoted only for provable one-to-one mappings; one-to-many remain ambiguous (§10 MUST)
- [x] EXT1A-026 Source/parser provenance on every emitted claim; claim counts from canonical JSON cross-checked (§10 MUST)

## AS-CORE-008 — Subject Multi-Field Knowledge Query

_Status: implementation complete on `feat/as-core-008-subject-multifield-query`; awaiting governor review / merge. Governing contract: query-scope-lock `AS-CORE-008-PACKAGE-CONTRACT.md`. Base `d209b359ddd30e75e4709932fd55cb9b71016927`. Additive read-only composition over AS-CORE-007; persistence NONE; authority/temporal CONSUME-ONLY._

- [x] CORE8-001 Domain envelope `KnowledgeMultiFieldAnswer` + JSON schema
- [x] CORE8-002 Library `query_knowledge_fields` (single snapshot; point-builder reuse)
- [x] CORE8-003 CLI adapter (repeatable `--field` / `--fields`; point path preserved)
- [x] CORE8-004 Focused FR/INV suite (ordering, duplicates, snapshot fail-closed, parity, no-mutation)
- [x] CORE8-005 Package guide `docs/AS-CORE-008-subject-multifield-query.md`
- [ ] CORE8-006 Governor review and merge to `main`

## AS-QUERY-DIAG-001 — Structured Query Outcome Diagnostics

_Status: implementation complete on `feat/as-query-diag-001`; awaiting governor review. MERGE NO. Directive `D-PROJECT-ATLAS-FORWARD-PIPELINE-ACTIVATION-001`. Frozen contract: orphans `gen4-next-wave-parallel-001/AS-QUERY-DIAG-001-CONTRACT.md`. Base `9f656ab` / tree `20882c55`. Additive diagnostics only; success-path 007/008 JSON default-stable; `knowledge_compiler` / Graph / MODEL FORBIDDEN._

- [x] QDIAG-001 Domain `QueryDiagnostic` + outcome classes + JSON schema
- [x] QDIAG-002 Library classifiers / serializers (`classify_query_outcome`, `query_diagnostic_from_*`)
- [x] QDIAG-003 CLI structured stdout on `KnowledgeQueryError` (exit 1); argparse exit 2 unchanged
- [x] QDIAG-004 Focused T01–T12 suite `tests/unit/test_as_query_diag_001.py`
- [x] QDIAG-005 Package guide + 007/008 cross-links
- [ ] QDIAG-006 Governor review and merge to `main`

## AS-WEB-001 — Atlas Web Application foundation

_Status: **MERGED** via PR #53 (`bcd453f`). Directive `D-PROJECT-ATLAS-WEB-AND-1.0-AUTONOMOUS-COMPLETION-001`. Firewall: `apps/web/**` + `src/project_atlas/web_api/**` + ADR-008. UI ≠ canonical; Graph ≠ authority; Unknown ≠ healthy. NO vault truth writes; NO REL-001; NO PILOT invent. **WEB APPLICATION ACCEPTED = NO** (foundation only)._

- [x] WEB001-001 ADR-008 Vite+React architecture + read-first API boundary
- [x] WEB001-002 Scaffold `apps/web` runnable shell + smoke script
- [x] WEB001-003 `project_atlas.web_api` read-only adapters (projects / OBS health consume)
- [x] WEB001-004 Orphan design-lab note (4 prototype themes)
- [x] WEB001-005 Focused pytest `tests/unit/test_as_web_001_*.py`
- [x] WEB001-006 Governor review and merge to `main`

## AS-WEB-002 — Design-lab prototypes + shared tokens

_Status: implementation complete on `feat/as-web-002-design-lab`; awaiting governor review / merge. Base tip at open: `bcd453f` / TREE `0afe3218`. Firewall: **`apps/web/**` + ADR-009 + soft WORKLOG/backlog only** — zero `src/project_atlas` mutation. UI ≠ canonical; Graph ≠ authority; Unknown ≠ healthy. **WEB APPLICATION ACCEPTED = NO** (design-lab only)._

- [x] WEB002-001 Four HashRouter design-lab prototype routes (Ledger Desk / Signal Rack / Cartograph Quiet / Terminal Honest)
- [x] WEB002-002 Shared `tokens.css` `--atlas-*` tokens + `[data-theme]` remaps
- [x] WEB002-003 ADR-009 web design tokens
- [x] WEB002-004 Smoke script covers design-lab routes
- [x] WEB002-005 Soft WORKLOG + backlog checklist
- [x] WEB002-006 Governor review and merge to `main`

## AS-WEB-003 — Production shell + Command Center + ADR-010

_Status: implementation complete on `feat/as-web-003-production-shell`; awaiting governor review / merge. Tip base `6c74b91`. Firewall: `apps/web/**` + ADR-010 + soft WORKLOG/backlog. UI ≠ canonical; Graph ≠ authority; Unknown ≠ healthy. **WEB APPLICATION ACCEPTED = NO**._

- [x] WEB003-001 ADR-010 / ADR-ATLAS-WEB-UX-001 (production vs design-lab; Command Center modes)
- [x] WEB003-002 Production routes: Home / Projects / Ops / Command Center
- [x] WEB003-003 Command Center mode switcher (overview · projects · ops · impact)
- [x] WEB003-004 Preserve design-lab routes; smoke extended
- [x] WEB003-005 Soft WORKLOG + backlog checklist
- [ ] WEB003-006 Governor review and merge to `main`
- [ ] WEB003-007 WEB APPLICATION ACCEPTED criteria package (later — not this PR)

## AS-OPT-GATE-001 — Governed experiment and promotion boundary

_Status: **MERGED** via PR `#321` (`c0ebd46` on `main`). Directives `D-PROJECT-ATLAS-OPT-GATE-027` + `D-PROJECT-ATLAS-OPT-GATE-REMEDIATE-030` + D-031 residual closed under `D-PROJECT-ATLAS-CLOUD-AUTONOMOUS-E2E-032`. Reuses AS-2.2-EVAL-001 + AS-2.2-EVAL-BROKER-001. Does **not** wake Atlas-OPT, AutoLab, RL, or Prime. `ATLAS_OPT_WAKE_GATE = CLOSED` (runtime). `EVALUATOR_STABLE = YES`; wake recommendation `OPEN_ELIGIBLE` (governance only; AutoLab not activated)._

- [x] OPTGATE-001 Typed hard-gate contract (PASS/FAIL only; gates precede score)
- [x] OPTGATE-002 Sealed experiment envelope + mid-run immutability verify
- [x] OPTGATE-003 Privacy-safe reconstructable experiment receipt
- [x] OPTGATE-004 Promotion engine (`PROMOTE_ELIGIBLE` / `REJECT` / `INVALID_EXPERIMENT`)
- [x] OPTGATE-005 Anti-gaming A–G + fail-closed + security IV tests
- [x] OPTGATE-007 Honesty-catalog object seal + receipt threshold binding (IV remediate-030)
- [x] OPTGATE-008 Sealed anchors required to certify `PROMOTE_ELIGIBLE` (closes threshold-downgrade redigest)
- [x] OPTGATE-006 Independent IV PASS + merge to `main`
- [x] OPTGATE-009 Post-merge evaluator reassessment (D-032): `EVALUATOR_STABLE = YES`; wake recommendation `OPEN_ELIGIBLE` (governance only — runtime `ATLAS_OPT_WAKE_GATE` remains `CLOSED`; AutoLab not activated)

## Coder Alpha (D-PROJECT-ATLAS-CODER-ALPHA-035 / D-037)

_North star: persistent brain for AI-native projects. Durable anchor:
`docs/product/CODER-ALPHA-NORTH-STAR.md`. Rebase table:
`docs/CODER-ALPHA-035-REBASE.md`. Historical roadmap/backlog items below
Epic K remain evidence; they do not override Coder Alpha owner priority._

- [x] AS-CODER-ALPHA-CONNECT-001 `atlas connect .` one-command bind+compile
- [x] AS-CODER-ALPHA-OVERVIEW-001 Project Overview lens
- [x] AS-CODER-ALPHA-STATE-001 Current State lens
- [x] AS-CODER-ALPHA-CHANGED-001 What Changed defaults
- [x] AS-CODER-ALPHA-DECISIONS-001 Decision memory
- [x] AS-CODER-ALPHA-UNKNOWN-001 Unknown/conflict bundle
- [x] AS-CODER-ALPHA-BRIEF-001 Unified project brief (`atlas brief`)
- [x] AS-CODER-ALPHA-CAPTURE-001 Session capture defaults
- [x] AS-CODER-ALPHA-HANDOFF-001 `atlas handoff` create/resume
- [x] AS-CODER-ALPHA-CONTEXT-001 Agent context export
- [x] AS-CODER-ALPHA-OBSIDIAN-001 Living Obsidian projection
- [x] AS-CODER-ALPHA-HUMAN-LOOP-001 Human decisions → Truth Core
- [x] AS-CODER-ALPHA-WEB-001 Web Knowledge UX on Core
- [x] AS-CODER-ALPHA-TRUTH-UX-001 Evidence/conflict/UNKNOWN inspection
- [x] AS-CODER-ALPHA-ARCH-001 Architecture summary from plan/AGENTS (≠ purpose echo)
- [x] AS-CODER-ALPHA-CHANGED-002 Second-connect What Changed / stale-context measure
- [x] AS-CODER-ALPHA-CHANGED-002b Positive-delta add/mod/remove + self-churn exclusion
- [x] AS-CODER-ALPHA-ATTENTION-001 Attention hygiene classifier (`atlas attention`)
- [x] AS-CODER-ALPHA-ARCH-002 Structured architecture lens (multi-slot, honest UNKNOWN)
- [x] AS-CODER-ALPHA-CROSS-SURFACE-001 Disk/Web/Obsidian/Agent brief consistency tests
- [x] AS-CODER-ALPHA-HUMAN-LOOP-V2 Decide → rematerialize → no pending resurrection
- [x] AS-CODER-ALPHA-SOURCE-HEALTH-001 Source failure explainability (`atlas source-health`)
- [x] AS-CODER-ALPHA-DECISIONS-002 Decision status labels (active/superseded/…)
- [x] AS-CODER-ALPHA-DECISIONS-003 ACTIVE_GOVERNING authority gate
- [x] AS-CODER-ALPHA-ATTENTION-002 care_about triage + source-failure collapse
- [x] AS-CODER-ALPHA-CHANGED-003 semantic know_about narrative
- [x] AS-CODER-ALPHA-ARCH-FIDELITY-001 exact module/path identifiers
- [ ] AS-CODER-ALPHA-044-HIGH D-041 Windows/adversarial HIGH truth/isolation remediations
- [x] AS-CODER-ALPHA-D049-AUTHORIZED-VOLUME-ROOT-001 CLOSED (merge c282f2c; D-088 authentic PASS; post-merge seal PASS)
- [x] AS-CODER-ALPHA-CAPTURE-002 Conversational capture CLOSED (D-042; merge 9441b0c; D-096 post-hoc owner ratification GRANTED; PRE_MERGE_AUTHORIZATION_PROVENANCE UNVERIFIED; do not rewrite as pre-merge authorization; do not reopen #344)
- [x] AS-2.1-MCP-BRIEF-001 zero-arg `atlas.brief.read` MCP tool (vault-scoped Coder Alpha briefs; MCP!=authority; no request args)
- [ ] AS-CODER-ALPHA-REPORT-READ-CONVERGENCE-001 consume-only union of #593-#603 on branch `cursor/aug26-report-read-convergence-f3ff` (`next/changed/overview/decisions/unknown/state/architecture/roadmap/portfolio/bitemporal/index-status`; MCP/API/CLI registered here; EMPTY/UNKNOWN stay not-healthy; CONVERGED_ON_BRANCH != SATISFIED_ON_MAIN; MERGE_AUTHORIZATION NOT_GRANTED)
- [ ] AS-CODER-ALPHA-INCREMENTAL-CONNECT-001 no-change reconnect must not double discover+ingest
- [x] AS-CODER-ALPHA-NEXT-001 What Next daily lens (`atlas next`; compose attention/roadmap/unknown/source-health; NEXT!=command; independent of AS-2.0-NEXT-001)
- [ ] AS-CODER-ALPHA-CONTEXT-FRESHNESS-ADV-001 frozen-at-write estate vs later live estate (draft; does not retarget #378; does not duplicate #419; MERGE_AUTHORIZATION NOT_GRANTED)
- [x] AS-PROJECT-ROADMAP-001 Living Project Roadmap V1 (derived; ROADMAP!=canonical; CLI/API/Web/connect/handoff; D-098 Web context remediation on #354; ROADMAP_STATE=LOCAL_RECERTIFICATION_PENDING; CLOUD_IV=PASS; ROADMAP_LOCAL_AUTHENTIC_IV=PENDING_RECHECK; MERGE_ELIGIBLE=NO; MERGE_AUTHORIZATION NOT_GRANTED)

## AS-ORCH-001A — Agent Result Contract + Deterministic Transition Classification

_Status: **IMPLEMENTED — READY FOR INDEPENDENT VERIFICATION** (not merge-eligible; not owner-approved; not production-ready). Classifies the next eligible transition from a structured `AgentResultEnvelope`. Does **not** dispatch, route automatically, create Cursor hooks, merge, or grant owner authority. `execution_authorized = false` always._

Honesty (mandatory):

- `STRUCTURED RESULT CONTRACT = IMPLEMENTED`
- `DETERMINISTIC CLASSIFICATION = IMPLEMENTED`
- `AUTOMATIC ROUTING = NOT YET IMPLEMENTED`
- `CURSOR HOOK = NOT YET IMPLEMENTED`
- `AGENT DISPATCH = NOT YET IMPLEMENTED`
- `AUTONOMOUS LOOP = NOT YET IMPLEMENTED`
- `AUTOMATIC MERGE = NOT IMPLEMENTED`
- `OWNER AUTHORITY = STILL REQUIRED`

- [x] ORCH001A-001 `AgentResultEnvelope` model + shipped JSON schema
- [x] ORCH001A-002 `OrchestrationDecision` (`execution_authorized=false`, `merge_authorized=false`)
- [x] ORCH001A-003 Deterministic transition classifier + explicit precedence
- [x] ORCH001A-004 Owner gate (`MERGE_ELIGIBLE` → `OWNER_REQUIRED`, never `MERGE`)
- [x] ORCH001A-005 Read-only CLI `atlas orchestrator validate-result`
- [x] ORCH001A-006 Focused unit tests + schema/model parity
- [ ] ORCH001A-007 Independent integration verification
- [x] ORCH001B Policy Router — see AS-ORCH-001B (routing policy implemented; runtime automatic routing NOT implemented)
- [x] ORCH001C Cursor Integration — see AS-ORCH-001C (bridge + optional stop-hook adapter + explicit completion transport; authentic Cursor stop delivery ENVIRONMENT_DEPENDENT; dispatch NOT implemented)
- [x] ORCH001D Agent Dispatcher — see AS-ORCH-001D (fresh current-main single-hop; not #396)
- [x] ORCH001E Governed Autonomous Loop — see AS-ORCH-001E

## AS-ORCH-001B — Deterministic Policy Router + Typed TaskDirective

_Status: **IMPLEMENTED — READY FOR INDEPENDENT VERIFICATION** (not merge-eligible; not owner-approved; not production-ready). Routes a 001A `OrchestrationDecision` to a typed `TaskDirective` or an explicit owner-gate / terminal result. Does **not** dispatch, create Cursor hooks, execute tasks, merge, or grant owner authority. `execution_authorized = false` always._

Honesty (mandatory):

- `STRUCTURED_RESULT_CONTRACT = IMPLEMENTED`
- `DETERMINISTIC_CLASSIFICATION = IMPLEMENTED`
- `DETERMINISTIC_POLICY_ROUTING = IMPLEMENTED`
- `TYPED_TASK_DIRECTIVE = IMPLEMENTED`
- `ROUTING POLICY IMPLEMENTED`
- `RUNTIME AUTOMATIC ROUTING NOT IMPLEMENTED`
- `CURSOR_HOOK = NOT_IMPLEMENTED`
- `AGENT_DISPATCH = NOT_IMPLEMENTED`
- `AUTONOMOUS_LOOP = NOT_IMPLEMENTED`
- `AUTOMATIC_MERGE = NOT_IMPLEMENTED`
- `OWNER AUTHORITY = STILL REQUIRED`

Remediation role: existing taxonomy is `local` | `integration` | `autonomous`. There is no `implementation` role. `REMEDIATION_REQUIRED` targets `local` (least-authoritative existing role). Recertification remains `integration`.

- [x] ORCH001B-001 Typed `TaskDirective` + fail-closed `DirectivePermissions`
- [x] ORCH001B-002 Discriminated `OrchestrationRoute` (`task` | `owner_gate` | `terminal`)
- [x] ORCH001B-003 Deterministic policy table over 001A `NextTransition` values
- [x] ORCH001B-004 Source-result digest binding + decision/envelope consistency
- [x] ORCH001B-005 Read-only CLI `atlas orchestrator route-result`
- [x] ORCH001B-006 Shipped JSON schemas + model/schema parity tests
- [x] ORCH001B-007 Focused unit + composition + privilege-invariant tests
- [ ] ORCH001B-008 Independent integration verification
- [x] ORCH001C Cursor Integration — see AS-ORCH-001C (bridge + optional stop-hook adapter + explicit completion transport; authentic Cursor stop delivery ENVIRONMENT_DEPENDENT; dispatch NOT implemented)
- [x] ORCH001D Agent Dispatcher — see AS-ORCH-001D (fresh current-main single-hop; not #396)
- [x] ORCH001E Governed Autonomous Loop — see AS-ORCH-001E

## AS-ORCH-001C — Cursor Integration Bridge + Governed Stop Hook

_Status: **REMEDIATED — READY FOR RE-CERTIFICATION** (AS-ORCH-001C-R1; not merge-eligible; not owner-approved; not production-ready). Surfaces a governed Atlas route via an optional Cursor stop-hook adapter **or** a deterministic explicit completion transport. Does **not** spawn agents, execute `TaskDirective`, merge, or grant authority. The stop hook is **not** the required primary runtime trigger._

Honesty (mandatory):

- `STRUCTURED_RESULT_CONTRACT = IMPLEMENTED`
- `DETERMINISTIC_CLASSIFICATION = IMPLEMENTED`
- `DETERMINISTIC_POLICY_ROUTING = IMPLEMENTED`
- `TYPED_TASK_DIRECTIVE = IMPLEMENTED`
- `CURSOR_BRIDGE_CORE = IMPLEMENTED`
- `CURSOR_STOP_HOOK_ADAPTER = IMPLEMENTED`
- `EXPLICIT_COMPLETION_TRANSPORT = IMPLEMENTED`
- `AUTHENTIC_CURSOR_STOP_EVENT_DELIVERY = NOT_RELIABLE_IN_CURRENT_WINDOWS_CLI_RUNTIME`
- `AUTHENTIC_CURSOR_STOP_EVENT_DELIVERY = ENVIRONMENT_DEPENDENT`
- `HOOK_RUNTIME_REQUIRED_FOR_CORE_FLOW = NO`
- `CROSS_AGENT_DISPATCH = NOT_IMPLEMENTED`
- `AGENT_DISPATCH = NOT_IMPLEMENTED`
- `AUTONOMOUS_LOOP = NOT_IMPLEMENTED`
- `AUTOMATIC_MERGE = NOT_IMPLEMENTED`
- `OWNER AUTHORITY = STILL REQUIRED`

- [x] ORCH001C-001 Typed `CursorStopEvent` / `CursorBridgeState` / `CursorBridgeResponse`
- [x] ORCH001C-002 Single-slot runtime state under `.atlas/orchestration/cursor/` (gitignored)
- [x] ORCH001C-003 `atlas orchestrator cursor-stage-result` (recompute 001A+001B; fail closed on pending overwrite)
- [x] ORCH001C-004 Thin `.cursor/hooks.json` + `.cursor/hooks/atlas_stop.py` (no policy in the hook)
- [x] ORCH001C-005 One trusted `followup_message` for task/owner_gate; `{}` for terminal/aborted/error
- [x] ORCH001C-006 Loop guard + `atlas orchestrator cursor-ack` (ack != authority)
- [x] ORCH001C-007 Tamper/injection tests + hook stdin/stdout contract
- [x] ORCH001C-008 Read-only `atlas orchestrator cursor-status`
- [x] ORCH001C-R1-001 Typed `HandoffPacket` shared by hook adapter and explicit completion
- [x] ORCH001C-R1-002 `complete_staged_handoff` / `atlas orchestrator cursor-complete` (no Cursor event required)
- [x] ORCH001C-R1-003 Transport-equivalence + tamper + idempotence proofs
- [ ] ORCH001C-009 Independent integration verification (re-certification required after R1 HEAD/TREE move)
- [ ] ORCH001C-010 Local Windows explicit-completion acceptance (stop-event observation is non-blocking)
- [x] ORCH001D Agent Dispatcher — see AS-ORCH-001D (fresh current-main single-hop; not #396)
- [x] ORCH001E Governed Autonomous Loop — see AS-ORCH-001E

## AS-ORCH-001D — Governed Single-Hop Agent Dispatcher (current-main reconstruction)

_Status: **IMPLEMENTED — READY FOR OWNER MERGE GATE** (not merged; merge authorization not granted). Starts exactly one target agent for a governed `HANDOFF_READY` dispatchable task, then stops. Does **not** auto-dispatch the next hop. Does **not** resurrect PR #396. Does **not** start AS-ORCH-001E._

Honesty (mandatory):

- `SINGLE_HOP_AGENT_DISPATCHER = IMPLEMENTED`
- `GENERAL_AGENT_DISPATCH_RUNTIME = IMPLEMENTED`
- `WINDOWS_CMD_WRAPPER_SUPPORTED = YES`
- `CURSOR_CLI_PROCESS_TRANSPORT = IMPLEMENTED`
- `MULTI_HOP_AUTODISPATCH = NOT_IMPLEMENTED`
- `AUTONOMOUS_LOOP = NOT_IMPLEMENTED`
- `DISPATCH_RECEIPT_IS_AUTHORITY = NO`
- `PR396_RESURRECTED = NO`
- `AUTOMATIC_MERGE = NOT_IMPLEMENTED`
- `OWNER AUTHORITY = STILL REQUIRED`
- `MERGE_AUTHORIZATION = NOT_GRANTED`

- [x] ORCH001D-001 Typed `DispatchRecord` / `DispatchReceipt` + shipped schemas
- [x] ORCH001D-002 Deterministic dispatch identity bound to trusted routing fields
- [x] ORCH001D-003 Single-active-dispatch slot under `.atlas/orchestration/dispatcher/`
- [x] ORCH001D-004 Eligibility revalidation (HANDOFF_READY + dispatchable + fail-closed privileges)
- [x] ORCH001D-005 Owner/terminal non-executing outcomes (`PROCESS_STARTED = NO`)
- [x] ORCH001D-006 Cursor CLI argv transport with Windows `.cmd` wrapper + stdin prompt
- [x] ORCH001D-007 `dispatch-submit-result` / `dispatch-recover` (no respawn)
- [x] ORCH001D-008 `atlas orchestrator dispatch-once` / `dispatch-status`
- [x] ORCH001D-009 Mutating remediation fail closed (`CAPABILITY_REQUIRED`)
- [x] ORCH001D-010 Focused unit + schema tests
- [ ] ORCH001D-011 Independent verification
- [ ] ORCH001D-012 Authentic Local Windows Cursor agent dispatch acceptance
- [x] ORCH001E Governed Autonomous Loop — see AS-ORCH-001E

## AS-ORCH-001D-RESULT-BINDING-001 — process result capture / D-AS-ORCH-001D-RESULT-BINDING-014

_Status: **IMPLEMENTED — READY FOR OWNER MERGE GATE** (not merged; merge authorization not granted). Extends the existing 001D parent so a terminal ask-mode process can return one framed `AgentResultEnvelope` that the parent validates and binds. Does **not** create a second dispatcher, grant ask-mode write, merge, or mutate PR #402/#396._

Honesty (mandatory):

- `PROCESS_DISPATCH_PATH_COUNT = 1`
- `DISPATCH_PATH = AS_ORCH_001D`
- `SECOND_PROCESS_LAUNCH_PATH = NO`
- `STDOUT_IS_AUTHORITY = NO`
- `STDERR_IS_AUTHORITY = NO`
- `PROCESS_EXIT_ZERO_IS_AUTHORITY = NO`
- `RESULT_ADAPTER_CAN_AUTHORIZE_MERGE = NO`
- `ASK_MODE_GENERAL_MUTATION = NO`
- `AS_ORCH_001A_R1 = BLOCKED` until a later owner merge of this package
- `PR402_CERTIFICATION = NOT_GRANTED`
- `MERGE_AUTHORIZATION = NOT_GRANTED`

- [x] ORCH001DRB-001 Uniquely delimited terminal result frame
- [x] ORCH001DRB-002 Parent capture + 001A validation of untrusted payload
- [x] ORCH001DRB-003 Strict dispatch/lease/package/role/pin identity binding
- [x] ORCH001DRB-004 Replay fail-closed (duplicate/stale/wrong/after-finalization)
- [x] ORCH001DRB-005 Exit code is not semantic PASS
- [x] ORCH001DRB-006 Internal governed submit/finalize (child write not required)
- [ ] ORCH001DRB-007 Independent verification (bootstrap: candidate tests + exact-head CI + Windows process matrix; adapter PASS is not self-trust)

## AS-ORCH-001E — Governed Autonomous Loop

_Status: **IMPLEMENTED — READY FOR OWNER MERGE GATE** (not merged; merge authorization not granted). Persistent loop above the landed 001D dispatcher. Does **not** bypass owner gates, authorize merge, grant waivers, expand objectives, or mutate #396._

Honesty (mandatory):

- `PERSISTENT_AUTONOMOUS_LOOP = IMPLEMENTED`
- `AUTONOMOUS_LOOP_001E = IMPLEMENTED`
- `SUCCESSOR_EXECUTION_UNDER_NEW_MODEL = ACTIVE`
- `LOOP_CAN_BYPASS_OWNER_GATE = NO`
- `LOOP_CAN_AUTHORIZE_MERGE = NO`
- `LOOP_CAN_GRANT_WAIVER = NO`
- `LOOP_CAN_EXPAND_OBJECTIVE = NO`
- `AUTOMATIC_MERGE = NOT_IMPLEMENTED`
- `OWNER AUTHORITY = STILL REQUIRED`
- `MERGE_AUTHORIZATION = NOT_GRANTED`

- [x] ORCH001E-001 Persisted loop state with fail-closed digest
- [x] ORCH001E-002 Tick: select READY → lease → 001D dispatch-once → stop
- [x] ORCH001E-003 Completion → validate → governor transition → DAG refresh
- [x] ORCH001E-004 Owner-gate and hard-blocker stop propagation
- [x] ORCH001E-005 Crash/restart recovery without duplicate dispatch
- [x] ORCH001E-006 Duplicate lease/result/dispatch prevention
- [x] ORCH001E-007 Adversarial matrix (authority, replay, corruption, cross-project)
- [ ] ORCH001E-008 Independent verification
- [ ] ORCH001E-009 Owner merge gate (not this package)

## AS-ORCH-MAILBOX-001 — Durable Agent Inbox (isolated candidate)

_Status: **LOCAL CANDIDATE — FOCUSED TESTS PASS; PROVIDER/IV VERTICAL INCOMPLETE**. Adds project-scoped durable ingress, 001A/001B composition, and typed read-only successor admission through the existing governor. Not merged, not owner-approved, and not a production queue. Inbox receipt creates no authority._

- [x] Versioned `AgentInboxMessage` contract and payload digest
- [x] Atomic project-isolated ingress, exact/idempotency replay handling, incident correlation, quarantine, and restart recovery
- [x] Result validation/classification through existing 001A and route through existing 001B
- [x] Narrow pre-start launcher-failure recovery classification to existing `AUTONOMOUS_RECONCILE` policy
- [x] Unit/schema and bridge tests (focused mailbox/orchestration regression selection passes in isolated `.[dev]` venv)
- [x] Bind eligible non-authoritative directive to deterministic governor-owned read-only successor WorkNode
- [ ] Concrete 001E `DispatchPort` provider wiring and durable verifier-result/candidate binding
- [ ] Independent verification and owner merge gate
- [ ] Atlas documentation normalization/receipt

## AS-ORCH-AUTONOMY-001 — Autonomous governor / operating-model transition

_Status: **IMPLEMENTED ON MAIN**. Formalizes a single logical autonomous governor, work DAG, leases, overlap gate, continuation, bounded remediation, IV routing, adversarial trigger, evidence hashing, and owner gates A–F. Process dispatch is owned by AS-ORCH-001D (this tree). Does **not** start AS-ORCH-001E, mutate #396, or merge._

Honesty (mandatory):

- `AUTONOMOUS_GOVERNOR = IMPLEMENTED`
- `WORK_DAG = IMPLEMENTED`
- `AGENT_LEASE_MODEL = IMPLEMENTED`
- `SURFACE_OVERLAP_GATE = IMPLEMENTED`
- `AUTONOMOUS_CONTINUATION_POLICY = IMPLEMENTED`
- `AUTOMATIC_REMEDIATION = IMPLEMENTED`
- `IV_ROUTING = IMPLEMENTED`
- `ADVERSARIAL_REVIEW_TRIGGER = IMPLEMENTED`
- `EVIDENCE_CONTRACT = IMPLEMENTED`
- `OWNER_GATES_A_F = IMPLEMENTED`
- `AGENT_DISPATCH = IMPLEMENTED_BY_AS_ORCH_001D`
- `MULTI_HOP_AUTODISPATCH = NOT_IMPLEMENTED`
- `AUTONOMOUS_LOOP_001E = IMPLEMENTED`
- `SUCCESSOR_EXECUTION_UNDER_NEW_MODEL = ACTIVE`
- `AUTOMATIC_MERGE = NOT_IMPLEMENTED`
- `OWNER AUTHORITY = STILL REQUIRED`
- `MERGE_AUTHORIZATION = NOT_GRANTED`

- [x] ORCHAUT-001 Authoritative governor state + WHAT_CAN_RUN / WAIT / PARALLEL / OWNER
- [x] ORCHAUT-002 Work DAG with explicit recorded transitions
- [x] ORCHAUT-003 Surface overlap gate (unsafe parallel = NO)
- [x] ORCHAUT-004 Agent lease model; no autonomous scope expansion
- [x] ORCHAUT-005 Continuation policy; stop at owner gate / hard blocker
- [x] ORCHAUT-006 Bounded remediation (max 3) then BLOCKED
- [x] ORCHAUT-007 IV routing: implementer != verifier
- [x] ORCHAUT-008 Adversarial review trigger for control-plane / authorization
- [x] ORCHAUT-009 Deterministic hashed evidence bundles
- [x] ORCHAUT-010 Owner gates A–F fail closed
- [x] ORCHAUT-011 CLI `governor-status` / `governor-discover` / `governor-pilot`
- [x] ORCHAUT-012 Controlled non-destructive in-process pilot
- [ ] ORCHAUT-013 Owner merge gate (not this package)

## AS-ORCH-AUTONOMY-001-PIN-RETARGET — trusted-anchor retarget / D-AUTONOMY-PIN-RETARGET-003

_Status: **IMPLEMENTED — READY FOR OWNER MERGE GATE** (not merged; merge authorization not granted). Replaces compile-time `EXPECTED_BASE_MAIN` as runtime authority with a provenance-bound trusted-anchor record. Initial retarget is the verified #398 merge (`62f8d59f...` / tree `aed48e48...`). Does **not** start R2/R6/R7/001E, mutate #396, or merge._

Honesty (mandatory):

- `STATIC_BOOTSTRAP_PIN_AS_RUNTIME_AUTHORITY = NO`
- `TRUSTED_ANCHOR_ADVANCEMENT = IMPLEMENTED`
- `GOVERNOR_CAN_INVENT_OWNER_AUTHORITY = NO`
- `GOVERNOR_CAN_ADVANCE_ANCHOR_FROM_OBSERVED_MAIN_ONLY = NO`
- `DESCENDANT_ONLY_IS_SUFFICIENT_AUTHORITY = NO`
- `UNVERIFIED_MAIN_MOVEMENT_FAILS_CLOSED = YES`
- `SUCCESSOR_EXECUTION_UNDER_NEW_MODEL = NOT_YET_ACTIVE`
- `AUTOMATIC_MERGE = NOT_IMPLEMENTED`
- `OWNER AUTHORITY = STILL REQUIRED`
- `MERGE_AUTHORIZATION = NOT_GRANTED`

- [x] ORCHAUT-014 Distinct BOOTSTRAP / TRUSTED_RUNTIME / OBSERVED pins
- [x] ORCHAUT-015 Shipped evidence-based #398 trusted-anchor record
- [x] ORCHAUT-016 Authorized advancement only when all §10 checks pass
- [x] ORCHAUT-017 Unauthorized / descendant-only / stale / concurrent / TOCTOU fail closed
- [x] ORCHAUT-018 Atomic compare-and-advance with append-only history
- [x] ORCHAUT-019 Negative matrix cases 1–15 + positive A/B/C
- [ ] ORCHAUT-020 Owner merge gate (not this package)

## AS-MDA-CONTROL-PLANE-COMPAT-001-R1 — mda-cli 0.2.9 control-plane compatibility

_Status: **RECONSTRUCTED — RECERTIFICATION IN PROGRESS** (owner-authorized because the previously certified Git object was lost before publication). Prior HEAD `4cb80a0aa0e28fbddee8c8a71f1875519f19fc92` remains historical evidence only. Prior certification is not transferable. This package does not touch PR #396 / AS-ORCH-001D._

- [x] MDA-R1-001 Explicit trusted 0.2.9 contract (`*.restructured.md`, `--out-dir`)
- [x] MDA-R1-002 Fail-closed missing / empty / stale / ambiguous / unknown-contract / confinement
- [x] MDA-R1-003 Focused reconstruction tests (18 cases) + mock models production contract
- [x] MDA-R1-004 Session-start-relevant stale `*.normalized.md` production refs = 0
- [ ] MDA-R1-005 Authentic PATH mda 0.2.9 + billed OpenRouter + `normalize_event` (host-blocked here)
- [ ] MDA-R1-006 Independent verification against published R1 HEAD/TREE
- [ ] MDA-R1-007 Exact-head GitHub CI + owner merge gate (merge not authorized)

## AS-ORCH-DURABLE-LEASE-PROJECTION-001 — durable read projection of governor leases

_Status: **IMPLEMENTING**. Projects primary-governor lease grant/release into a durable file for process-restart and subordinate read-only visibility. The projection is **not** authority. Grant/ack source remains the primary governor. Does **not** replace `AutonomousGovernor._leases`. Does **not** consume the Cursor bridge slot. Does **not** merge._

Honesty (mandatory):

- `PRIMARY_GOVERNOR_REMAINS_AUTHORITY = YES`
- `DURABLE_PROJECTION_IS_AUTHORITY = NO`
- `LEASE_GRANT_SOURCE = PRIMARY_GOVERNOR`
- `LEASE_ACK_SOURCE = PRIMARY_GOVERNOR`
- `CROSS_PROCESS_LEASE_VISIBILITY = PARTIAL_UNTIL_THIS_LANDS`
- `GLOBAL_AUTONOMY_BLOCKER = NO`

- [x] ORCHLEASE-001 Optional governor projection store (default off; existing tests unchanged)
- [x] ORCHLEASE-002 Atomic JSON projection + identity lock
- [x] ORCHLEASE-002a Exclusive nofollow tmp write (ORCH-LEASE-SYMLINK-ESCAPE-001)
- [x] ORCHLEASE-003 Reject stale / duplicate / foreign worker / foreign package / replay
- [x] ORCHLEASE-004 Ack + release visibility after process restart
- [x] ORCHLEASE-005 Focused + concurrent + control-plane tests
- [ ] ORCHLEASE-006 Exact-head CI + independent IV + adversarial control-plane review
- [ ] ORCHLEASE-007 Owner merge gate (not this package)

## Atlas 3.0 program (D-191 / D-192 / D-193)

_Status: **FOUNDATION CONVERGENCE — ISOLATED IMPLEMENTATION-READY**. Canonical
docs live under `docs/atlas-3/`. Runtime lives under `src/project_atlas/atlas3/`.
Does **not** rewrite certified 2.x demo surfaces. `FULL_LIVE_DEMO_READY = NO`.
`MERGE_AUTHORIZATION = NOT_GRANTED`. Chronicle remains ROADMAP_HORIZON.
Historical roadmaps are classified as inputs and are not erased._

- [x] AT3-DOC Program documents (north star, architecture, roadmap, epics, DAG, migration, UX, competitive, acceptance)
- [x] AT3-DOC-LLM D-192 LLM memory program documents
- [x] AT3-001 Foundation layer ownership (`FOUNDATION.md`)
- [x] AT3-002 Isolated project twin schema + constructors
- [x] AT3-010 Isolated repository/component inventory (declared; UNKNOWN if absent)
- [x] AT3-013 Isolated PR/commit/test/build nodes (ledger projection; no invented git)
- [x] AT3-011 Isolated file/symbol graph (declared; no host walk)
- [x] AT3-012 Isolated service/environment nodes (declared fixture; not authentic estate)
- [x] AT3-003 Isolated engineering event model (canonical envelope)
- [x] AT3-004 Isolated semantic capability registry
- [x] AT3-005 Isolated 2.x→3.x compatibility prover
- [x] AT3-006 Foundation threat catalog (reviewed; not certified)
- [x] AT3-014 Isolated universal event ledger
- [x] AT3-015 Isolated Atlas Pulse (eight questions)
- [x] AT3-030 Isolated Atlas Start (budget + freshness)
- [x] AT3-050 Isolated AGENT_PROOF
- [x] AT3-035/036/039/040/041/042/044/047/048/049 Isolated ChatGPT-first memory vertical
- [x] AT3-036 Isolated ChatGPT export ingest (parse_chat_export compose; live history claim fails closed; does not replace chatgpt_bridge)
- [x] AT3-039 Isolated conversation normalization (canonical envelope; mixed corrupt fail-closed; no new CLI)
- [x] AT3-040 Isolated conversation extractor (landed ITEM_TYPES; forged owner stays proposed; no Truth Core)
- [x] AT3-041 Isolated cross-LLM dedup (provenance retained; no state/intent/history collapse)
- [x] AT3-042 Isolated cross-LLM conflict detection (no winner; no layer collapse)
- [x] AT3-044 Isolated memory freshness (STALE != CURRENT; UNKNOWN stays UNKNOWN)
- [x] AT3-047 Isolated privacy/secret gate (fail-closed scan; raw transcript minimized)
- [x] AT3-048 Isolated unified memory search (extracted items only; not a transcript dump)
- [x] AT3-049 Isolated memory reconciliation (compose 041/042/044; never auto-promote)
- [x] AT3-CHRONICLE Horizon design notes only (no runtime)
- [x] AT3-037 Isolated Claude fixture/export ingest (native history sync remains NOT IMPLEMENTED)
- [ ] AT3-037 Claude native history sync (NOT IMPLEMENTED; EXPORT_ONLY honesty)
- [x] AT3-038 Isolated Gemini fixture/export ingest (native history sync remains NOT IMPLEMENTED)
- [ ] AT3-038 Gemini native history sync (NOT IMPLEMENTED; EXPORT_ONLY honesty)
- [x] AT3-043 Isolated conversation decision + intent extraction
- [x] AT3-045 Isolated provider session lineage
- [x] AT3-061 Isolated intent vs current-state honesty wrapper
- [x] AT3-060 Isolated causal graph (declared CAUSED_BY; graph != authority)
- [x] AT3-062 Isolated DECIDED_BY provenance (owner_origin required)
- [x] AT3-020 Isolated claim/decision/requirement nodes (declared; graph != authority; no Truth Core write)
- [x] AT3-021 Isolated derived relationship expansion (GRAPH_REUSE aliases; no AS-GRAPH-003 write)
- [x] AT3-022 Isolated conflict/UNKNOWN projection (UNKNOWN stays UNKNOWN; no winner; no healthy filter)
- [x] AT3-023 Isolated graph != authority prover (winners/trust fail closed; no AS-GRAPH-003 write)
- [x] AT3-051 Isolated independent-verification binding (exact HEAD/TREE; IV != MERGE)
- [x] AT3-052 Isolated ADV binding (exact HEAD/TREE; ADV != MERGE / != security cert)
- [x] AT3-070 Isolated surface contract (CLI/API/Web/TUI/MCP/A2A; surface != authority)
- [x] AT3-071 Isolated transport != authority prover (HTTP/CLI/MCP/A2A success != authority)
- [x] AT3-072 Isolated provider-register / capabilities CLI design (no CLI proliferation)
- [x] AT3-080 Isolated impact explorer data (declared; graph != authority; no trust scores)
- [x] AT3-100 Isolated twin health (derived signals; health != authority; estate != authorization)
- [x] AT3-090 Isolated Atlas Home composer (Pulse+Start+twin health; UI != truth)
- [x] AT3-091 Isolated Timeline (declared valid-time; wall-clock != valid-time)
- [x] AT3-094 Isolated Decision Explorer (declared owner_origin; model paraphrase != owner)
- [x] AT3-092 Isolated Truth Graph UX (declared claims/relationships; graph != authority)
- [x] AT3-096 Isolated Mission Command Center (declared DAG/leases; no self-merge)
- [x] AT3-095 Isolated Impact Explorer UX (composes AT3-080; no new CLI)
- [x] AT3-110 Isolated multi-project twin (declared siblings; federation != authority)
- [x] AT3-111 Isolated org identity (declared only; does not mint)
- [x] AT3-081 Isolated stale/conflict intelligence (Pulse + memory compose; no winner; stale != current)
- [x] AT3-082 Isolated next-action honesty (Pulse + next-lens compose; NEXT != command; no write)
- [x] AT3-093 Isolated Time Machine UX reuse (kdiff only; no second clock; wall-clock != valid-time)
- [x] AT3-112 Isolated federation reuse honesty (FED-001/002 compose; federation != authority; no promote)
- [x] AT3-053 Isolated autonomy gate reuse (orch DAG/lease compose; no self-dispatch; lease != merge)
- [x] AT3-101 Isolated ledger observability (validated read; ledger != truth; no healthy filter)
- [x] AT3-102 Isolated provider sync status (honest capabilities; AT3-046 EXTERNAL_BLOCKED)
- [x] AT3-046 Isolated incremental export-cursor (local apply only; live provider incremental EXTERNAL_BLOCKED)
- [x] AT3-054 Isolated consume-only memory context compiler (no 2.x rewrite; stale != current; UNKNOWN stays UNKNOWN)
- [x] AT3-055 Isolated ranked-context local serve (chatgpt/claude/gemini/cursor pack; live serve EXTERNAL_BLOCKED)
- [x] AT3-056 Isolated fixture provider handoff (ChatGPT→Claude fixture path; live multi-account EXTERNAL_BLOCKED)
- [x] AT3-057 Isolated Cursor fixture / local-session ingest (AGENTS.md != ingestion; Cursor Cloud history NOT IMPLEMENTED)
- [ ] AT3-057 Cursor Cloud history sync (NOT IMPLEMENTED; LOCAL_SESSION honesty)
- [x] AT3-058 Isolated Codex fixture / structured-submission ingest (CODEX.md != ingestion; native history NOT IMPLEMENTED)
- [ ] AT3-058 Codex native history sync (NOT IMPLEMENTED; STRUCTURED_SUBMISSION honesty)
- [ ] AT3-046 Incremental live provider sync (EXTERNAL_BLOCKED; credentials / history API)
- [ ] Chronicle / Ambient Knowledge runtime (ROADMAP_HORIZON)
- [ ] AT3-003/014 certified-surface implementation after `FULL_LIVE_DEMO_READY = YES`
