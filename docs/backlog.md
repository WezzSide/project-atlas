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

_Remaining items (I-002 through I-005, I-007, I-008) were implemented
under **AS-MVP-001** on `feat/as-mvp-001-portfolio-pilots` and are
**merged**: that candidate (`da04bd31`) is an ancestor of `main`, and
later mainline work has evolved the implementation (AS-MVP-001-R1 and
subsequent packages). There is no outstanding merge of that candidate,
and the branch name above is historical rather than a pending
integration target. The four portfolio capabilities were re-verified
against `main` `f1b52565` on 2026-08-27 through the real CLI pipeline
(`init` -> `discover` -> `ingest` -> `build-indexes` ->
`build-portfolio` -> `validate`). See
`docs/adr/ADR-005-mvp-portfolio-intelligence-pilot-onboarding.md` and
`docs/evidence/AS-MVP-001-receipt.yaml` -- that receipt is historical
evidence pinned to the pre-merge candidate and does not certify current
`main` (see its status-reconciliation block)._

- [x] I-001 Project index generator
- [x] I-002 Portfolio overview — implemented, acceptance-tested (AS-MVP-001); merged to `main`
- [x] I-003 Maturity matrix — implemented, acceptance-tested (AS-MVP-001); merged to `main`
- [x] I-004 Documentation gap report — implemented, acceptance-tested (AS-MVP-001); merged to `main`
- [x] I-005 Stale knowledge report — implemented, acceptance-tested (AS-MVP-001); merged to `main`
- [x] I-006 Conflict review queue
- [x] I-007 Dependency report — implemented, acceptance-tested (AS-MVP-001); merged to `main`
- [x] I-008 Capability report — implemented, acceptance-tested (AS-MVP-001); merged to `main`

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
credential-shaped canary fixture for K-007). This work is merged to
`main`; the branches named above are historical rather than pending
integration targets._

- [x] K-001 Nebula fixture corpus — implemented, acceptance-tested (AS-MVP-001); merged to `main`
- [x] K-002 Black Agency OS fixture corpus — implemented, acceptance-tested (AS-MVP-001); merged to `main`
- [x] K-003 Dark Factory fixture corpus — implemented, acceptance-tested (AS-MVP-001); merged to `main`
- [x] K-004 Expected manifests — implemented, acceptance-tested (AS-MVP-001-R1); merged to `main`
- [x] K-005 Expected generated vault — implemented, acceptance-tested (AS-MVP-001-R1); merged to `main`
- [x] K-006 Contradiction fixtures — implemented, acceptance-tested (AS-MVP-001-R1); merged to `main`
- [x] K-007 Secret fixtures — implemented, acceptance-tested (AS-MVP-001-R1); merged to `main`

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
  (EXTERNAL_BLOCKED — authentic INT-013 requires owner-provided authentic
  project roots; committed DEMO_FIXTURE projects (e.g.
  `tests/fixtures/demo/estate/`) do not satisfy this gate; see
  `docs/product/CODER-ALPHA-NORTH-STAR.md` and
  `docs/AS-PILOT-FIXTURE-ONLY-WAIVER.md`. A fixture-backed bounded
  technical-acceptance pilot has run and passed
  (`tests/integration/test_int_013_bounded_multi_project_pilot.py`, now run
  unskipped on every CI invocation) but does not itself clear this
  blocker. Note that unskipping it also means it no longer counts as
  `extract_corroborating_facts()` corroborating evidence — that heuristic
  only credits skip/xfail-marked files — so INT-013's
  `evidence_completeness` is `INTENT_ONLY`, which is strictly more
  conservative and leaves the item blocked either way.)

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

### DOGFOOD-001 — Source-safe genesis marker write (hardening follow-up)

_Found by the first authentic Atlas dogfood run against real
`B0LK13/project-atlas` content: ID-001 genesis (intended) rewrote the
entire `.atlas-project.yaml` marker via `yaml.safe_dump` instead of
appending the one new field, clobbering unrelated human-authored
formatting with no CLI-visible disclosure. Not a defect in the genesis
design itself (random UUIDv4 allocated once, persisted in source
config -- confirmed intended); the defect was the write mechanism and
its silence. See `WORKLOG.md` 2026-08-31 entry and
`tests/unit/test_dogfood_001_source_marker_identity_write.py`._

- [ ] DOGFOOD-001 Minimal-diff marker append + CLI disclosure of
      source-tree identity writes -- implemented, tested, red/green
      proven on `repair/dogfood-001-source-safe-genesis`, but **blocked
      on an owner decision**: the fix edits `ingestion.py`, which
      `tests/unit/test_atlas3_demo_isolation_001.py` freezes per
      `docs/atlas-3/ARCHITECTURE.md` §9 while `FULL_LIVE_DEMO_READY = NO`.
      Not mergeable without an explicit, narrowly-scoped freeze exception
      (or deferral until that flag flips on its own).

## AS-CORE-003 — Claim Identity v2 remediation

_Status: **MERGED** via the PR #5 platform merge commit `6d874751d3ed9cb05433a8d50ab372a997418d84` (candidate V2-006). Phantom-work correction 2026-08-28: this section still read "Remote CI verification and Project Owner merge authorization remain pending" and CORE3-026 was unchecked, despite the merge commit being a genuine ancestor of current `main` (confirmed via `git merge-base --is-ancestor`) and the code being live -- `claim_identity.py` exists and is imported by both `knowledge_compiler.py` and `evidence_compiler.py`, not stub text. A separate post-merge closure record (docs commit `f7837377`, "AS-CORE-003 post-merge closure record") correctly reconciled this checkbox once already, but that commit is not itself an ancestor of current `main` (confirmed via the same check) -- its `docs/evidence/AS-CORE-003-post-merge-receipt.yaml` does not exist on this branch -- so this correction is re-applied directly here rather than assumed from that lost commit. V2-003 was preserved and rejected by independent review. V2-004 was superseded before review because its immutable tag annotation recorded an invalid tree string. V2-005 passed a fresh isolated review with three non-blocking findings but failed ubuntu CI on platform-dependent `media_type` detection and K-004 fixture newline translation. V2-006 fixed both determinism defects additively, passed an isolated review addendum, and was green on Windows and Linux gates. Non-blocking V2-005 findings are routed to the parser roadmap as follow-ups._

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
- [x] CORE3-026 Merge to `main` (commit `6d874751d3ed9cb05433a8d50ab372a997418d84`; phantom-work correction 2026-08-28)

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

_Status: **MERGED** via PR #14 (`59670bf33feede82dd85daa3da994f410a8d838e`, 2026-08-08). Phantom-work correction 2026-08-28: this section still read "awaiting governor review / merge" and CORE8-006 was unchecked despite the PR having merged three weeks prior -- corrected directly against `main`, confirmed both via `git merge-base --is-ancestor` (the merge commit is a genuine ancestor of current `main`) and directly against real code (`query_knowledge_fields` imported and called from `cli.py`, not stub text). Governing contract: query-scope-lock `AS-CORE-008-PACKAGE-CONTRACT.md`. Base `d209b359ddd30e75e4709932fd55cb9b71016927`. Additive read-only composition over AS-CORE-007; persistence NONE; authority/temporal CONSUME-ONLY._

- [x] CORE8-001 Domain envelope `KnowledgeMultiFieldAnswer` + JSON schema
- [x] CORE8-002 Library `query_knowledge_fields` (single snapshot; point-builder reuse)
- [x] CORE8-003 CLI adapter (repeatable `--field` / `--fields`; point path preserved)
- [x] CORE8-004 Focused FR/INV suite (ordering, duplicates, snapshot fail-closed, parity, no-mutation)
- [x] CORE8-005 Package guide `docs/AS-CORE-008-subject-multifield-query.md`
- [x] CORE8-006 Governor review and merge to `main` (PR #14, merged 2026-08-08; phantom-work correction 2026-08-28)

## AS-QUERY-DIAG-001 — Structured Query Outcome Diagnostics

_Status: **MERGED** via PR #21 (`e3b5b6b33a0b7c320dc9f902a9025da3476234e1`, 2026-08-09). Phantom-work correction 2026-08-28: this section still read "awaiting governor review, MERGE NO" and QDIAG-006 was unchecked despite the PR having merged three weeks prior -- corrected directly against `main`, confirmed both via `git merge-base --is-ancestor` and directly against real code (`classify_query_outcome` defined and used in `knowledge_query.py`, not stub text). Directive `D-PROJECT-ATLAS-FORWARD-PIPELINE-ACTIVATION-001`. Frozen contract: orphans `gen4-next-wave-parallel-001/AS-QUERY-DIAG-001-CONTRACT.md`. Base `9f656ab` / tree `20882c55`. Additive diagnostics only; success-path 007/008 JSON default-stable; `knowledge_compiler` / Graph / MODEL FORBIDDEN._

- [x] QDIAG-001 Domain `QueryDiagnostic` + outcome classes + JSON schema
- [x] QDIAG-002 Library classifiers / serializers (`classify_query_outcome`, `query_diagnostic_from_*`)
- [x] QDIAG-003 CLI structured stdout on `KnowledgeQueryError` (exit 1); argparse exit 2 unchanged
- [x] QDIAG-004 Focused T01–T12 suite `tests/unit/test_as_query_diag_001.py`
- [x] QDIAG-005 Package guide + 007/008 cross-links
- [x] QDIAG-006 Governor review and merge to `main` (PR #21, merged 2026-08-09; phantom-work correction 2026-08-28)

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
- [ ] AS-CODER-ALPHA-FRESH-AGENT-CHALLENGE-V2 machine-scored fixture harness (current-main; do not retarget #375; HARNESS!=AUTHENTIC_PILOT; MERGE_AUTHORIZATION NOT_GRANTED)
- [x] AS-CODER-ALPHA-WORKFLOW-METRICS-001 honest North Star telemetry from ops receipts (current-main; do not retarget #377; TELEMETRY!=TRUTH CORE; independently verified 2026-08-28, `PASS_WITH_NONBLOCKING_FINDINGS` -- see WORKLOG "EOD convergence wave"; MERGE_AUTHORIZATION NOT_GRANTED)
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
- [x] AS-CODER-ALPHA-044-HIGH D-041 Windows/adversarial HIGH truth/isolation remediations (independently verified 2026-08-28, isolation/truth-boundary claims `PASS`; the LIVE_API IPv6-loopback-bind P1 fix in PR #628 **merged to `main`** 2026-08-28T15:50:48Z as `33fb4fad`, confirmed a real ancestor of current `main` via `git merge-base --is-ancestor`; checkbox updated 2026-08-30 during D-PHASE2A post-merge DAG reconstruction -- see WORKLOG "EOD convergence wave")
- [x] AS-ATLAS3-FREEZE-GUARD-001 certified-surface freeze guard hosted-CI blind-spot remediation (PR #630, 5 rounds, independently re-verified PASS across all 5, **merged to `main`** 2026-08-28T15:51:46Z as `264248ba`, confirmed a real ancestor of current `main`; checkbox updated 2026-08-30 during D-PHASE2A post-merge DAG reconstruction; see WORKLOG "PR #630 -- Atlas-3 freeze guard hosted-CI blind spot"). The guard's own correctness is now live on `main`. This does **not** by itself grant the separate Atlas-3 freeze exception `api_server.py` may still need for any future certified-surface change -- that remains a distinct, separately-gated owner decision, unaffected by this checkbox.
- [x] AS-CODER-ALPHA-D049-AUTHORIZED-VOLUME-ROOT-001 CLOSED (merge c282f2c; D-088 authentic PASS; post-merge seal PASS)
- [x] AS-CODER-ALPHA-CAPTURE-002 Conversational capture CLOSED (D-042; merge 9441b0c; D-096 post-hoc owner ratification GRANTED; PRE_MERGE_AUTHORIZATION_PROVENANCE UNVERIFIED; do not rewrite as pre-merge authorization; do not reopen #344)
- [x] AS-2.1-MCP-BRIEF-001 zero-arg `atlas.brief.read` MCP tool (vault-scoped Coder Alpha briefs; MCP!=authority; no request args)
- [x] AS-CODER-ALPHA-REPORT-READ-CONVERGENCE-001 consume-only union of #593-#603, originally on branch `cursor/aug26-report-read-convergence-f3ff` (`next/changed/overview/decisions/unknown/state/architecture/roadmap/portfolio/bitemporal/index-status`). Phantom-work correction 2026-08-28: `CONVERGED_ON_BRANCH != SATISFIED_ON_MAIN` no longer holds -- all 11 `/v1/*-status` API routes are registered in `api_server.py` and all 11 `atlas.*.read` MCP tools are registered in `mcp_registry.py` on current `main`, verified directly (not assumed) against real route/tool handler code, not stub text. EMPTY/UNKNOWN still stay not-healthy on main. This checkbox tracked landing on main, not any further merge authorization.
- [x] AS-CODER-ALPHA-INCREMENTAL-CONNECT-001 no-change reconnect must not double discover+ingest. Phantom-work correction 2026-08-28: implemented and wired on current `main` -- `connect.py` names this package directly in its own docstring and contains `_finish_no_change_reconnect()`; `evaluate_incremental_reconnect()` is defined in `incremental_connect.py` and imported/called from `connect.py`'s real `connect` flow, verified directly against the code (not stub text). Origin PR #374 is merged.
- [x] AS-CODER-ALPHA-NEXT-001 What Next daily lens (`atlas next`; compose attention/roadmap/unknown/source-health; NEXT!=command; independent of AS-2.0-NEXT-001)
- [x] AS-CODER-ALPHA-CONTEXT-FRESHNESS-ADV-001 frozen-at-write estate vs later live estate (does not retarget #378; does not duplicate #419; independently verified 2026-08-28, `PASS_WITH_NONBLOCKING_FINDINGS` -- see WORKLOG "EOD convergence wave"; MERGE_AUTHORIZATION NOT_GRANTED)
- [x] AS-PROJECT-ROADMAP-001 Living Project Roadmap V1 (derived; ROADMAP!=canonical; CLI/API/Web/connect/handoff; D-098 Web context remediation on #354; ROADMAP_STATE=LOCAL_RECERTIFICATION_PENDING; CLOUD_IV=PASS; ROADMAP_LOCAL_AUTHENTIC_IV=BASIC_RECHECK_PASS_2026-08-28 (34/34 current test suite reconfirmed passing against current main -- a basic recheck, not a fresh full adversarial IV matching the 2026-08-28 EOD convergence wave's other packages; see WORKLOG); MERGE_ELIGIBLE=NO; MERGE_AUTHORIZATION NOT_GRANTED)

## AS-ORCH-001A — Agent Result Contract + Deterministic Transition Classification

_Status: **IMPLEMENTED — READY FOR OWNER MERGE GATE** (independently verified 2026-08-28, see WORKLOG "ORCH001A-007"; `MERGE_AUTHORIZATION = GRANTED` (2026-08-28, see docs/evidence/D-202-OWNER-AUTHORIZED-ORCHESTRATION-ACTIVATION.md); not yet merged; not production-ready). Classifies the next eligible transition from a structured `AgentResultEnvelope`. Does **not** dispatch, route automatically, create Cursor hooks, merge, or grant owner authority. `execution_authorized = false` always._

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
- [x] ORCH001A-007 Independent integration verification (2026-08-28: 118 existing tests re-run PASS + 7 black-box CLI probes, 1 baseline + 6 adversarial, against `main` `718f2beb`, see WORKLOG "ORCH001A-007"; PASS)
- [x] ORCH001A-008 Owner-authorized activation: `MERGE_AUTHORIZATION = GRANTED` (2026-08-28, see docs/evidence/D-202-OWNER-AUTHORIZED-ORCHESTRATION-ACTIVATION.md; consumes existing owner authorization, does not re-merge code, does not grant automatic merge to the loop)
- [x] ORCH001B Policy Router — see AS-ORCH-001B (routing policy implemented; runtime automatic routing NOT implemented)
- [x] ORCH001C Cursor Integration — see AS-ORCH-001C (bridge + optional stop-hook adapter + explicit completion transport; authentic Cursor stop delivery ENVIRONMENT_DEPENDENT; dispatch NOT implemented)
- [x] ORCH001D Agent Dispatcher — see AS-ORCH-001D (fresh current-main single-hop; not #396)
- [x] ORCH001E Governed Autonomous Loop — see AS-ORCH-001E

## AS-ORCH-001B — Deterministic Policy Router + Typed TaskDirective

_Status: **IMPLEMENTED — READY FOR OWNER MERGE GATE** (independently verified 2026-08-28, see WORKLOG "ORCH001B-008"; `MERGE_AUTHORIZATION = GRANTED` (2026-08-28, see docs/evidence/D-202-OWNER-AUTHORIZED-ORCHESTRATION-ACTIVATION.md); not yet merged; not production-ready). Routes a 001A `OrchestrationDecision` to a typed `TaskDirective` or an explicit owner-gate / terminal result. Does **not** dispatch, create Cursor hooks, execute tasks, merge, or grant owner authority. `execution_authorized = false` always._

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
- [x] ORCH001B-008 Independent integration verification (2026-08-28: dedicated pass — dispatchable=true permission audit + decision/envelope consistency probe against `main` `718f2beb`, see WORKLOG "ORCH001B-008"; PASS)
- [x] ORCH001B-009 Owner-authorized activation: `MERGE_AUTHORIZATION = GRANTED` (2026-08-28, see docs/evidence/D-202-OWNER-AUTHORIZED-ORCHESTRATION-ACTIVATION.md)
- [x] ORCH001C Cursor Integration — see AS-ORCH-001C (bridge + optional stop-hook adapter + explicit completion transport; authentic Cursor stop delivery ENVIRONMENT_DEPENDENT; dispatch NOT implemented)
- [x] ORCH001D Agent Dispatcher — see AS-ORCH-001D (fresh current-main single-hop; not #396)
- [x] ORCH001E Governed Autonomous Loop — see AS-ORCH-001E

## AS-ORCH-001C — Cursor Integration Bridge + Governed Stop Hook

_Status: **REMEDIATED — INDEPENDENTLY VERIFIED — READY FOR OWNER MERGE GATE** (AS-ORCH-001C-R1; re-certified 2026-08-28, see WORKLOG "ORCH001C-009"; ORCH001C-010 Local Windows explicit-completion acceptance = `PASS`, exercised 2026-08-28 end-to-end via the real CLI in a disposable directory (stage/complete/ack/idempotence/tamper-fail-closed all confirmed; zero dispatch, zero external calls, structurally guaranteed by `cursor_bridge.py`'s import graph) -- see WORKLOG "ORCH001C-010"; `MERGE_AUTHORIZATION = GRANTED` (2026-08-28, see docs/evidence/D-202-OWNER-AUTHORIZED-ORCHESTRATION-ACTIVATION.md); not yet merged; not production-ready). Surfaces a governed Atlas route via an optional Cursor stop-hook adapter **or** a deterministic explicit completion transport. Does **not** spawn agents, execute `TaskDirective`, merge, or grant authority. The stop hook is **not** the required primary runtime trigger._

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
- [x] ORCH001C-009 Independent integration verification (re-certification required after R1 HEAD/TREE move) (2026-08-28: 44 existing tests re-run PASS + 10 black-box CLI probes -- 2 baseline + 8 adversarial, covering `cursor-status`/`cursor-stage-result`/`cursor-ack`/`cursor-complete` (the R1-added explicit-completion transport, initially missed -- added after review), including on-disk state-file tamper injection against both `cursor-status` and `cursor-complete` (`STAGED_STATE_TAMPERED`), transport-equivalence and idempotence evidence for `cursor-complete` -- against `main` `5ff62221`, see WORKLOG "ORCH001C-009"; PASS; still not merge-eligible)
- [x] ORCH001C-010 Local Windows explicit-completion acceptance (stop-event observation is non-blocking)
- [x] ORCH001C-011 Owner-authorized activation: `MERGE_AUTHORIZATION = GRANTED` (2026-08-28, see docs/evidence/D-202-OWNER-AUTHORIZED-ORCHESTRATION-ACTIVATION.md)
- [x] ORCH001D Agent Dispatcher — see AS-ORCH-001D (fresh current-main single-hop; not #396)
- [x] ORCH001E Governed Autonomous Loop — see AS-ORCH-001E

## AS-ORCH-001D — Governed Single-Hop Agent Dispatcher (current-main reconstruction)

_Status: **IMPLEMENTED — READY FOR OWNER MERGE GATE** (ORCH001D-011 independently verified 2026-08-28 across two rounds -- round 1 found and remediated a real P2 (subprocess capture memory-bound) and, on independent re-verification, a real P1 (fix silently defeated the timeout when stdin was present); round 2 independently re-verified the fix clean with 7 further adversarial variants; see WORKLOG "ORCH001D-011"; ORCH001D-012 authentic-Cursor acceptance remains separately outstanding, `ATTEMPTED_BLOCKED_ON_ACCOUNT_USAGE_LIMIT` (2026-08-29, genuine attempt against the existing `docs/orch001c-010-cursor-acceptance-packet.md`, see D-206 including its own correction of an initially-missed packet and an over-count of authorized dispatches: first pair of dispatches hit `Workspace Trust Required`; owner then interactively trusted a dedicated `D:\atlas-cursor-acceptance` workspace (real `.workspace-trusted` marker independently confirmed, `trustedAt` matches); a separately-authorized third real dispatch against that now-trusted workspace passed the trust gate and reached Cursor's real cloud service, which returned a genuine account-level `ActionRequiredError: ... You're out of usage. Switch to Auto, or ask your admin to increase your limit` -- a real billing/usage-limit constraint on the owner's Cursor account, not an Atlas defect and not something this pass attempted to route around or retry against; no flag in `agent_transport.FORBIDDEN_CURSOR_FLAGS` (`-f`/`--force`/`--force-allow-http`) was used, and `--trust`/`--yolo` were never emitted -- not because they are forbidden-listed, but because they are simply outside the fixed flag allowlist the transport ever builds from) -- corrected 2026-08-28 from a false `EXTERNAL_BLOCKED` claim, see WORKLOG "Cursor CLI availability correction": a live Cursor CLI is genuinely present on this host; not merged; `MERGE_AUTHORIZATION = GRANTED` (2026-08-28, see docs/evidence/D-202-OWNER-AUTHORIZED-ORCHESTRATION-ACTIVATION.md)). Starts exactly one target agent for a governed `HANDOFF_READY` dispatchable task, then stops. Does **not** auto-dispatch the next hop. Does **not** resurrect PR #396. Does **not** start AS-ORCH-001E._

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
- `MERGE_AUTHORIZATION = GRANTED` (2026-08-28, see docs/evidence/D-202-OWNER-AUTHORIZED-ORCHESTRATION-ACTIVATION.md)

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
- [x] ORCH001D-011 Independent verification (2026-08-28: round 1 IV found a real P2 -- capture bounded only on the returned value, not during collection -- remediated; round-1 *independent* re-verification found a further real P1 -- the fix silently defeated `timeout_seconds` whenever stdin was populated (always true on the real dispatch path) -- remediated; round-2 independent re-verification PASS with 7 further adversarial variants beyond round 1's, confirming the memory-bound fix stayed intact and no thread leak. 107 orchestration tests pass, ruff/mypy clean. See WORKLOG "ORCH001D-011"; still not merge-eligible)
- [ ] ORCH001D-012 Authentic Local Windows Cursor agent dispatch acceptance (unaffected by activation below -- separately outstanding; genuinely attempted 2026-08-29 through a real, owner-trusted workspace; transport/trust layers both proved correct end to end; blocked on the owner's Cursor account usage limit, an account/spend decision, not an Atlas defect -- see D-206)
- [x] ORCH001D-013 Owner-authorized activation: `MERGE_AUTHORIZATION = GRANTED` (2026-08-28, see docs/evidence/D-202-OWNER-AUTHORIZED-ORCHESTRATION-ACTIVATION.md)
- [x] ORCH001E Governed Autonomous Loop — see AS-ORCH-001E

## AS-ORCH-001D-RESULT-BINDING-001 — process result capture / D-AS-ORCH-001D-RESULT-BINDING-014

_Status: **IMPLEMENTED — READY FOR OWNER MERGE GATE** (not merged; `MERGE_AUTHORIZATION = GRANTED`, 2026-08-28, see docs/evidence/D-202-OWNER-AUTHORIZED-ORCHESTRATION-ACTIVATION.md). Extends the existing 001D parent so a terminal ask-mode process can return one framed `AgentResultEnvelope` that the parent validates and binds. Does **not** create a second dispatcher, grant ask-mode write, merge, or mutate PR #402/#396._

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
- `MERGE_AUTHORIZATION = GRANTED` (2026-08-28, see docs/evidence/D-202-OWNER-AUTHORIZED-ORCHESTRATION-ACTIVATION.md)

- [x] ORCH001DRB-001 Uniquely delimited terminal result frame
- [x] ORCH001DRB-002 Parent capture + 001A validation of untrusted payload
- [x] ORCH001DRB-003 Strict dispatch/lease/package/role/pin identity binding
- [x] ORCH001DRB-004 Replay fail-closed (duplicate/stale/wrong/after-finalization)
- [x] ORCH001DRB-005 Exit code is not semantic PASS
- [x] ORCH001DRB-006 Internal governed submit/finalize (child write not required)
- [x] ORCH001DRB-007 Independent verification (bootstrap: candidate tests + exact-head CI + Windows process matrix; adapter PASS is not self-trust; independently verified 2026-08-28, `PASS`, 32/32 tests + 15 adversarial frame-injection probes including real Windows CreateProcess -- see WORKLOG "EOD convergence wave")
- [x] ORCH001DRB-008 Owner-authorized activation: `MERGE_AUTHORIZATION = GRANTED` (2026-08-28, see docs/evidence/D-202-OWNER-AUTHORIZED-ORCHESTRATION-ACTIVATION.md)

## AS-ORCH-001E — Governed Autonomous Loop

_Status: **IMPLEMENTED — READY FOR OWNER MERGE GATE** (ORCH001E-008 independently verified 2026-08-28, see WORKLOG "ORCH001E-008"; PASS with 3 non-blocking follow-ups recorded (2x P2 dead/misleading owner-gate guard + overstated AUTONOMY-001 honesty marker, 1x P3 crash-recovery liveness gap) -- no live authority leak found; not merged; `MERGE_AUTHORIZATION = GRANTED`, 2026-08-28, see docs/evidence/D-202-OWNER-AUTHORIZED-ORCHESTRATION-ACTIVATION.md). Persistent loop above the landed 001D dispatcher. Does **not** bypass owner gates, authorize merge, grant waivers, expand objectives, or mutate #396._

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
- `MERGE_AUTHORIZATION = GRANTED` (2026-08-28, see docs/evidence/D-202-OWNER-AUTHORIZED-ORCHESTRATION-ACTIVATION.md)

- [x] ORCH001E-001 Persisted loop state with fail-closed digest
- [x] ORCH001E-002 Tick: select READY → lease → 001D dispatch-once → stop
- [x] ORCH001E-003 Completion → validate → governor transition → DAG refresh
- [x] ORCH001E-004 Owner-gate and hard-blocker stop propagation
- [x] ORCH001E-005 Crash/restart recovery without duplicate dispatch
- [x] ORCH001E-006 Duplicate lease/result/dispatch prevention
- [x] ORCH001E-007 Adversarial matrix (authority, replay, corruption, cross-project)
- [x] ORCH001E-008 Independent verification (2026-08-28: 255 existing tests re-run PASS + 8 adversarial probes against `main` `0aa37abf`, ruff/mypy clean; PASS with 3 non-blocking follow-ups recorded, see WORKLOG "ORCH001E-008"; 2 of 3 follow-ups resolved as a side effect of ORCHAUT-010 (see below); the third, P3 crash-recovery orphaned-dispatch, tracked separately on PR #635, not yet certified)
- [x] ORCH001E-010 Owner-authorized activation: `MERGE_AUTHORIZATION = GRANTED` (2026-08-28, see docs/evidence/D-202-OWNER-AUTHORIZED-ORCHESTRATION-ACTIVATION.md)
- [x] ORCH001E-011 `governor-loop-tick` CLI command had no node discovery/rehydration step -- constructed a brand-new empty `AutonomousGovernor` on every invocation (gap found during independent IV of PR #637, independently reproduced; see docs/evidence/D-204-GOVERNOR-LOOP-TICK-NO-NODE-REHYDRATION.md). Not fixed by PR #635 or PR #637 (both correct within their own tested scope; recovery failed earlier, on the empty governor's node lookup, before either fix's code was reached). Fixed here in `rehydration.py` — reuses the existing `AS-ORCH-DURABLE-LEASE-PROJECTION-001` lease projection plus `discover()`/`ingest_discovery()` (no second, competing persisted-DAG model); LEASED-phase recovery reconstructs the exact granted lease from durable evidence, fails closed (`NODE_NOT_REHYDRATABLE`) for any package_id other than the one deterministic node factory the governor has, and fails closed (`EXECUTION_STATE_NOT_REHYDRATABLE`) for DISPATCHING/AWAITING_RESULT/VALIDATING rather than guess at in-flight execution state. See D-205 for the authentic real-subprocess recovery proof (`test_real_subprocess_recovers_leased_pilot_node_after_crash`: two genuinely separate OS processes, independently re-run 2026-08-29, PASS) and the full adversarial matrix. **`GOVERNOR_LOOP_TICK_CLI_CROSS_PROCESS_RECOVERY` = `FUNCTIONAL` (demonstrated by the real-subprocess test above). `GOVERNOR_LOOP_TICK_CLI_CROSS_PROCESS_ORIGINATION` stays `NOT_FUNCTIONAL`/not yet demonstrated** -- the origination code path is fixed (originate-then-mark-ready), but the real `discover()` implementation has never returned an eligible non-pilot candidate yet (every hardcoded candidate is `eligible=False`, see `test_originate_marks_newly_discovered_node_ready`'s own docstring), so cross-process origination of genuinely new work is unproven, not merely unfixed.
- [x] ORCH001E-012 `_complete_validated()` dangling-`active_dispatch_id` stuck-loop gap (found during ORCH001E-011 independent IV, recorded but deliberately not fixed there -- see docs/evidence/D-205-ORCH001E-011-GOVERNOR-LOOP-REHYDRATION.md's "found, out of scope" section -- this line item itself was never actually added to this file at the time, a gap closed here alongside the fix). `apply_observed_result()` persists `phase=VALIDATING` well before its own final `_save(...)` clears `active_dispatch_id`; if whatever interrupted the call landed in that window, `_complete_validated()` used to see `active_dispatch_id is not None` and silently `return self._result()` forever -- no error, no state change, no progress. **Scope-narrowing, independently re-confirmed**: `rehydrate_governor()` (`rehydration.py`) already fails closed (`EXECUTION_STATE_NOT_REHYDRATABLE`) for `{DISPATCHING, AWAITING_RESULT, VALIDATING}` *before* `run_governor_loop_tick()` ever constructs an `AutonomousLoop` -- a real cross-process crash during VALIDATING was already safely rejected, not silently stuck; this gap is reachable only same-process (a caller retrying `tick()`/`recover()` on the same still-live `AutonomousLoop`/`AutonomousGovernor` objects after an interruption -- e.g. a future `run_until_stop()` caller or a hand-written retry -- `run_until_stop()` itself is not currently called by any production code path, only tests). Fixed in `loop.py` using the same established idiom PR #637 already applied to the LEASED/ACTIVE ambiguity: `_complete_validated()` now reads the governor's *current* node state (still live in-process) to learn how far the interrupted call actually got, then either safely redrives `apply_observed_result()` from scratch (node still LEASED/ACTIVE -- nothing was mutated yet, re-deriving `passed` from the dispatch port's own durable record via a new `_reobserve_dispatch_outcome()`, the same move `recover()` already makes for DISPATCHING/AWAITING_RESULT, rather than trusting a stale in-memory value never persisted anywhere) or finishes only the LoopState bookkeeping directly via a new `_finalize_validated()` helper (node already reached a terminal verified state -- CERTIFIED/OWNER_HELD/MERGE_ELIGIBLE, or BLOCKED, or REMEDIATING -- redriving would attempt an illegal transition out of an already-transitioned node). An unexpected node state (VERIFYING, or a passed/failed mismatch between the re-observed dispatch outcome and the node's own terminal state) fails closed with a new `VALIDATION_STATE_AMBIGUOUS` code rather than guessing. 7 new adversarial tests in `tests/unit/test_orchestration_autonomy_loop.py` (`test_validating_dangling_*`) construct the exact crash-window fixture and cover: IN_PROCESS redrive-to-completion, already-CERTIFIED bookkeeping-only finish (the illegal-transition hazard, directly proven not to raise), EXTERNAL dispatch re-observation (proven via an actually-invoked `recover()` call, not assumed), REMEDIATING resume, BLOCKED hard-stop, the ambiguous-VERIFYING fail-closed case, and an adversarial CERTIFIED-node-vs-FAILED-dispatch consistency mismatch fail-closed case; all 7 independently confirmed to fail against the pre-fix code and pass post-fix. No real cross-process/multi-process test was added for this specific gap -- per the scope-narrowing fact above it is not reachable cross-process at all (already-passing `test_orchestration_autonomy_rehydration.py`'s existing in-flight-phase matrix, including VALIDATING, re-run and still 20/20 PASS, unaffected by this change), so a subprocess test would not have exercised anything this fix changes. Full `orchestration or autonomy` suite: 334 passed (327 pre-existing + 7 new), 0 regressions. `ruff check .` and `mypy src`: clean (2 pre-existing, unrelated `connect_perf.py` `os.getrusage` Windows-stub errors, not touched here, matching D-205's own note).
- [ ] ORCH001E-009 Owner merge gate (not this package) -- activation of the stack is not a grant of automatic merge authority to the loop itself, which remains permanently Owner-reserved

## D-PHASE2A / AS-ORIGIN-001 — Specification-backed autonomous work origination

- [x] D-PHASE2A-1 Specification-backed work origination proof of concept: `src/project_atlas/orchestration/origination/` (facts, identity, adapter, proposal, policy, risk, materialize, projection, pipeline) derives governed `WorkNode`s only from evidence that already exists in a project's own repository (a fenced `docs/ROADMAP.md` record + a skip/xfail-marked spec test) -- `AI_INVENTED_WORK` is structurally unreachable, not merely discouraged by prompting. Additive-only extension of `orchestration/autonomy/rehydration.py` (new optional `origination_projection_store` parameter, `None` by default, byte-identical behavior for every existing caller) closes the sealed baseline's `CROSS_PROCESS_ORIGINATION = UNPROVEN` gap. Demonstrated end-to-end with 3 genuinely separate OS processes against the real Gamma/TASK-017 estate (origination+lease → real cross-process recovery → real `pytest` verification of a pre-existing implementation → completion → successor rescan correctly returning `NO_ELIGIBLE_WORK`); see `docs/adr/ADR-033-phase2a-specification-backed-work-origination.md` and `docs/evidence/d-phase2a/` (EVIDENCE.md, POC-RUNBOOK.md, receipts, demo script). **Explicit claim boundary** (do not overstate): `AUTONOMOUS_IMPLEMENTATION_EXECUTION = EXTERNAL/OWNER BLOCKED`, not proven -- no existing Atlas mechanism can autonomously write an implementation today (`governor.execute_leased()`'s only real IN_PROCESS call site records an evidence bundle, never a file write; the only real code-writing path, `EXTERNAL_AGENT`/Cursor, remains blocked on the owner's account usage limit, ORCH001D-012, unrelated to this package). 3 full rounds of independent adversarial IV (separate fresh agents each round, no self-certification), all CONFIRMED (round 1 and round 2 "with minor notes," round 3 unqualified CONFIRMED after every finding from both prior rounds was fixed and re-verified); 14/14 directive-required negative/adversarial matrix; exact-head CI PASS across all 4 gate jobs (control-plane, ubuntu 3.12 full, ubuntu 3.13 compat, windows) on the final merged commit. Also fixed, during automated-review remediation on the PR itself: a `RiskClassification` max_length that could be exceeded by the enum's own cardinality, an unguarded second file read, a path-traversal read primitive (evidence refs like `"../outside.py"` could read outside the intended project root -- fixed with an explicit, segment-based `".."` rejection; **the character-class-regex-alone insufficiency for embedded traversal segments like `"a/../b"` is an explicit, tested, load-bearing invariant, not incidental**), an origination-identity collision across multiple simultaneously-eligible roadmap items, discarded `WorkNode` dependency edges, and silently-erased declared blockers -- see `docs/evidence/d-phase2a/EVIDENCE.md`'s `AUTOMATED_REVIEW_REMEDIATIONS` section for the full account. Merged to `main` as PR #643 (merge commit `2cee1489`), 2026-08-30.
- [x] D-PHASE2A-1a `orchestration.autonomy.governor.lease()` now consults `WorkNode.dependencies` directly (`_unsatisfied_dependencies()`), fail-closed (`DEPENDENCIES_NOT_SATISFIED`) for both an unknown dependency id and a known-but-not-yet-`CERTIFIED`/`OWNER_HELD`/`MERGE_ELIGIBLE`/`MERGED`/`CLOSED` one -- a real governed-DAG-layer chokepoint, not just proposal-level caller discipline. `AutonomousLoop._dispatch_leased()` maps the new code to `StopReason.HARD_BLOCKER`. Merged to `main` as PR #645, merge commit `66e52d49d76da952e8fb83536057c0d6c16938c6`, 2026-08-30. Independently re-verified across 2 rounds (round 2 `CONFIRMED WITH MINOR NOTES`); post-merge seal re-run against the integrated `main` tree confirms the dependency-enforcement test suite passes live (9/9), not merely on the pre-merge PR branch.
- [ ] D-PHASE2A-2 Wiring origination into the live governed DAG/lease/dispatch loop (`orchestration/autonomy/loop.py` and friends) -- explicitly deferred by D-PHASE2A-1's own scoping ("Phase 2A-1 only... a separate later PR"). Both of its prior blockers are now resolved: D-PHASE2A-1a (above) is merged, and PR #642 (`feat/phase2a-specification-backed-origination`, the independent overlapping implementation) is **CLOSED as superseded** (not merged, not transplanted) -- verified before closure that merging it as-is would have been semantically broken (a real module/package namespace collision: `orchestration/origination.py` vs. the merged `orchestration/origination/` package, confirmed via an isolated Python import test that Python always resolves to the package, permanently orphaning `origination.py` and breaking its own tests), plus a duplicate `ADR-033` document. The `#643`-descendant `orchestration/origination/` package (extended by D-PHASE2A-1a/-3) is the canonical Phase 2A origination lineage. Canonical carrier is PR #654 (`feat/d-phase2a-2-origination-governor-wiring`) -- wiring is implemented there, **not merged**. Night-cycle 2026-08-30 remediations on that same carrier (exact-object recert required after HEAD move): completed-dependency CERTIFIED witnesses, durable lease-sequence adoption, STOPPED/NO_ELIGIBLE_WORK resume when later origination adds READY work, stale `base_pin` skip, same-identity persist idempotency, corrupt-lease fail-closed. `MERGE_AUTHORIZATION = NOT_GRANTED`.
- [x] D-PHASE2A-3 Consolidated origination-scan entry point: `orchestration/origination/cli.py::run_origination_scan()` -- a single pure function sequencing `collect_live_inventory()` → `originate_new_only()` → `risk.classify()` → `materialize_work_node()` → `projection.persist_proposed()`/`persist_materialized()`, mirroring `orchestration/autonomy/cli.py::run_governor_loop_tick()`'s own pattern (JSON-serializable payload + exit code, fail-closed on trust/projection errors, `merge_authorized`/`execution_authorized` always `False`). Not yet wired into the top-level `atlas` argparse command (neither is `run_governor_loop_tick()` itself) -- that remains a separate, larger UX/product-surface decision. Merged to `main` as PR #647, merge commit `ab1b64ea8cd021eeaa6fedc3674803447906e3c8`, 2026-08-30. Independently re-verified across 2 rounds (round 1 found and fixed 2 real defects -- an unsafe `project_id` and a valid-but-`surface_id`-overflowing `project_id`, both escaping the function's documented "never raises" contract; round 2 `CONFIRMED WITH MINOR NOTES`); post-merge seal re-run against the integrated `main` tree confirms the CLI test suite passes live (7/7).

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
- `OWNER_GATES_A_F = IMPLEMENTED` (corrected back 2026-08-28 -- was PARTIALLY_IMPLEMENTED earlier the same day, found during ORCH001E-008 IV: gates C/D/E/F existed only as descriptive `OwnerGateKind` tags with zero `require_owner(...)` call sites. ORCHAUT-010 closed this: `governor.py::lease()` now enforces `require_owner(...)` for any owner_gate other than A, and `continuation.py::select_next` unconditionally excludes any owner-gated node from autonomous selection -- independently verified PASS in two rounds, merged as `7bcb8ea2`. See docs/evidence/D-200-ORCHAUT-010-GATE-CF-SELECT-LEASE-FIX.md and ORCHAUT-010 below.)
- `AGENT_DISPATCH = IMPLEMENTED_BY_AS_ORCH_001D`
- `MULTI_HOP_AUTODISPATCH = NOT_IMPLEMENTED`
- `AUTONOMOUS_LOOP_001E = IMPLEMENTED`
- `SUCCESSOR_EXECUTION_UNDER_NEW_MODEL = ACTIVE`
- `AUTOMATIC_MERGE = NOT_IMPLEMENTED`
- `OWNER AUTHORITY = STILL REQUIRED`
- `MERGE_AUTHORIZATION = GRANTED` (2026-08-28, see docs/evidence/D-202-OWNER-AUTHORIZED-ORCHESTRATION-ACTIVATION.md)

- [x] ORCHAUT-001 Authoritative governor state + WHAT_CAN_RUN / WAIT / PARALLEL / OWNER
- [x] ORCHAUT-002 Work DAG with explicit recorded transitions
- [x] ORCHAUT-003 Surface overlap gate (unsafe parallel = NO)
- [x] ORCHAUT-004 Agent lease model; no autonomous scope expansion
- [x] ORCHAUT-005 Continuation policy; stop at owner gate / hard blocker
- [x] ORCHAUT-006 Bounded remediation (max 3) then BLOCKED
- [x] ORCHAUT-007 IV routing: implementer != verifier
- [x] ORCHAUT-008 Adversarial review trigger for control-plane / authorization
- [x] ORCHAUT-009 Deterministic hashed evidence bundles
- [x] ORCHAUT-010 Owner gates A–F fail closed -- reopened 2026-08-28 (found during ORCH001E-008 IV); fixed 2026-08-28 in two rounds (round 1: `continuation.py::select_next`/`loop.py::_select_and_lease` dead-code owner-gate check; round 2, found by independent IV: `governor.py::lease()`/`execute_leased()` reachable directly via `run_controlled_pilot()`/`continue_autonomous()`, bypassing the loop entirely) -- both rounds independently verified PASS by a separate agent. Merged as PR #633 / `7bcb8ea2`. See docs/evidence/D-200-ORCHAUT-010-GATE-CF-SELECT-LEASE-FIX.md.
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
- [x] ORCHLEASE-006 Exact-head CI + independent IV + adversarial control-plane review (independently verified 2026-08-28, `PASS_WITH_NONBLOCKING_FINDINGS`, 127/127 broader autonomy suite, symlink-escape and tamper/replay defenses reconstructed adversarially, real concurrency race test -- see WORKLOG "EOD convergence wave")
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

- [x] AS-OBSIDIAN-CAPTURE-001 Conversational knowledge capture + Obsidian bridge (raw evidence preserved verbatim; projection != source; CAPTURE != AUTHORITY; renamed from the incoming `AS-OBS-001` title, which collides with the CLOSED observability health-snapshot package)
- [ ] AS-OBSIDIAN-CAPTURE-002 Localhost capture API + browser extension (BLOCKED: LIVE_API is contractually read-only; needs its own work package)
- [x] AS-OBSIDIAN-CAPTURE-001-F1 Ambiguous HUMAN region identity fails closed (a name repeated **in one scope**, or a region nested inside one of its own name, is ambiguous; extraction keys blocks by name so a re-render silently dropped a human block -- now refused, note left byte-identical. Resolved structurally over region spans, not by counting names: counting refused nested distinct-name documents on their *second* render, which would have decided F2 by side effect. Sibling names are compared per scope at every depth; a top-level-only check shipped briefly as a fail-open. `graph_projections.py` carried an independent copy of this defect until F4 consolidated it onto the canonical core, integrated as `15c9a6d6`)
- [x] AS-OBSIDIAN-CAPTURE-001-F2 HUMAN region nesting semantics -- resolved as **structural-scope-qualified identity**: a region is identified by its ancestry scope path plus its name, so `a/x` and `b/x` are independent and preserved independently. Name-keyed last-wins collapsed them into one slot and spliced the survivor into the other container -- silent human-content loss, measured at 111 lost payloads per 1,241 merges on a randomized harness before the fix and 0 after. Same-scope repeats, self-nesting and crossed markers fail closed with the note untouched; the merge plan is resolved and validated before any byte is written. Nested distinct-name documents no longer duplicate the inner block. `graph_projections.py` carried an independent implementation this did not reach until F4, integrated as `15c9a6d6`
- [x] AS-OBSIDIAN-CAPTURE-001-F3 Literal generated markers inside a HUMAN region (**SEALED 2026-09-08** as PR #717, merge commit 48a51875 onto main 15c9a6d6; evidence-corrected successor c6b0ecb8 independently verified PASS_WITH_NONBLOCKING_FINDINGS before merge). **Owner policy decided: marker spellings are RESERVED EVERYWHERE**, including inside HUMAN content; HUMAN payload is not opaque and raw HUMAN bytes are immutable (no auto-escaping, no zero-width rewriting). The historical uncertainty stands as recorded: the alternative was opaque HUMAN payload, which needs ownership rules and anti-forgery coverage, so simply dropping the count was unsafe. Empirically the safety behaviour already held before this work package -- every exact spelling inside a HUMAN region already failed closed with the note byte-identical, and near misses (bare token, extra spacing, `startx`, uppercase, partial token, ordinary comments) were already preserved as prose -- so no parser change was manufactured. The remaining defect was diagnostic: `malformed-generated-markers:<path>` did not tell an operator what collided or that nothing was written. The refusal now reports observable facts (`count`/`end-before-begin`, begin/end/expected counts, `reserved-marker-in-human-region` only when structurally determinable, `no-write`) while making no authorship claim, since the same shape can arise from Atlas-side corruption. **Open residuals, not closed by this work package** (register in WORKLOG): CRLF is normalised LF-only end to end at FOUR generated-span-preserving writers, `ingestion.py:99` included, pre-existing and byte-identical on base main, so "no normalisation" is not honoured for line endings at the product boundary; the diagnostic is not uniform across surfaces (`graph_projections` emits the bare message at five sites, and `ingestion.py:103`/`:107`/`:484` raise a plain `ValueError` with no diagnostic at all); the containment helper pairs self-nested same-name markers where the canonical parser refuses them; and #716's split-token near miss is pinned by no assertion. **Figures that must not be cited, no instrument committed**: "52 of 66,430", the F4 fuzz absolutes, "249 passed", "18.3s pairwise scan". Evidence: `docs/evidence/AS-OBSIDIAN-CAPTURE-001-F3-RESERVED-MARKER-CLOSURE.md`.
- [x] AS-OBSIDIAN-CAPTURE-001-F4 Consolidate the third protected-region implementation -- **INTEGRATED and SEALED 2026-09-07** as PR #707 (merge commit `15c9a6d6`). `graph_projections._merge_protected_regions` is no longer an independent name-keyed copy: it is a narrow adapter onto the canonical `protected_regions.py` core, translating `ProtectedRegionError` to `GraphProjectionError`, and retaining exactly one disclosed graph-specific contract -- a prior note with no HUMAN regions keeps its text outside the generated span, where the canonical core returns the fresh render. ("Exactly one" is precise about *intentional* contracts, not about all observable differences: a second, production-unreachable divergence is recorded in the WORKLOG residual register -- a no-HUMAN prior note carrying a generated span, where the fresh render has none, is refused by graph and accepted by canonical.) The claim is canonical reuse of HUMAN-region semantics only, not general equivalence. **Pre-fix behaviour, for the record** (quantified in `docs/evidence/F4-GRAPH-PROJECTION-HUMAN-REGION-DIVERGENCE.md`, reproducible via `docs/scripts/f4_protected_region_divergence_harness.py`): same-leaf cross-scope `a/x`/`b/x` was accepted with one payload destroyed and the survivor spliced into both scopes; same-scope duplicates were accepted with last-wins loss; and **self-nesting was accepted at the real refresh surface with outer-content loss** -- an earlier revision of this entry said self-nesting was "refused only incidentally via marker-count mismatch", which understated main's sharpest defect and is corrected here. On the seeded 4,000-trial harness graph lost payloads in 897 of 3,051 accepted merges, duplicated content in 194 and grafted spurious region structure in 108, where canonical F2 recorded zero on every counter; the superseded "~50.5%" figure came from a harness that was never preserved and must not be cited. **Postmerge seal** on `15c9a6d6`: all six corruption counters zero on seeds 20260907 and 99, with graph's accept/refuse partition identical to canonical (2178/1822 and 2175/1825) where it was 3051/949 before; every failure path surfaces `GraphProjectionError` with no raw `ValueError`/`IndexError`/`ProtectedRegionError` leakage.
- [x] AS-OBSIDIAN-CAPTURE-001-F5 HUMAN line endings are bytes, not formatting -- **INTEGRATED and SEALED 2026-09-08** as PR #724 (merge commit `91f40368`, second parent `2b472b91`). **What was verified, stated precisely**: four IV rounds ran against four *different* objects (`6788f3bc`, `7e632070`, `78b48d93`, `bf8799ee`), each superseded by the next, so no single object carries all four verdicts. The merged object equals the final branch head -- `git diff 2b472b91 91f40368` is empty -- and its `src` (`0f909c49`) and `tests` (`8c3b9dfc`) trees are hash-identical to round 4's object `bf8799ee`, so round 4's runtime and test certification transfers. The docs-only delta `bf8799ee`->`2b472b91` (WORKLOG and evidence prose, no code or test change) was **not** independently verified. All four generated-span-preserving writers read the prior note with `Path.read_text(encoding="utf-8")`, whose universal-newline translation rewrites `\r\n` and a lone `\r` to `\n` **before the merge sees the bytes**, so a refresh the operator did not request silently mutates their HUMAN region -- contradicting the F3 owner policy ("no normalisation"), `write_projection_outputs`'s own "preserves HUMAN protected regions byte-for-byte" docstring, and `canonical_content`'s stated boundary that line-ending normalisation exists only for identity and "never changes what is persisted". **Invisible to every existing guard**: marker presence, count and placement are all unchanged by translation, and the F1-F4 suites assert substring presence rather than bytes. Reproduced on `7a9eeb76` through all four real entry points (`capture`/`retry`, `materialize_obsidian_projection`, `write_projection_outputs`, `ingestion._generated_content`), CR 2->0 each. Pre-existing, not introduced by F1-F4; the write side is already correct (`write_bytes` everywhere, `ingestion.py:296` already passes `newline="\n"`). Fixed by one shared helper `protected_regions.read_note_text`, decoding bytes directly because `Path.read_text(newline=...)` needs 3.13 and this package supports 3.12. **Scope split by an owner gate**: `ingestion.py` is a certified surface frozen by `test_atlas3_demo_isolation_001`, needing an owner-approved sha256-pinned exception (ARCHITECTURE.md SS9.1) this lane cannot self-grant, so F5 fixes the three permitted sites and leaves `ingestion.py` byte-identical to main; its four reproductions stay in-suite as `xfail(strict=True)` so they flip to a visible failure the moment the gated fix lands. **CORE3-014 is untouched** -- identity hashing still normalises deliberately, pinned by test. 27 tests asserting bytes/digests plus 4 strict xfail tripwires (31 collected); five negative controls, one per live site plus the helper and the ownership probe (27 passed -> 19/5/6/4/3 failed), each failing a distinct set. **A regression this package introduced and fixed**: `_existing_capture_id` gated ownership on `startswith("---\n")`, so with the faithful read a note whose FIRST LINE ends CRLF -- a Windows editor, or `core.autocrlf=true` -- was judged unmanaged and refused with `OBSIDIAN_NOTE_CONFLICT` on every refresh. Fixed by normalising for the ownership probe only; verification proved no false-positive was traded in (18 hostile shapes still refused; 42/42 probe-decision equivalence with base). Not claimed: that already-normalised notes are recoverable, or that other normalisation is absent. **Seal on the merge commit**: F5 suite 27 passed / 4 xfailed; full suite 5,643 passed / 8 skipped / 4 xfailed; freeze guard 78; ruff and mypy clean (405 files); all five negative controls reproduce on main (19/5/6/4/3), each failing a distinct set -- so the protections are load-bearing on `main`, not only on the branch. Four IV rounds: round 1 found a regression this fix introduced (ownership gated on `startswith("---\n")`, so a CRLF first line made a note permanently unmanageable -- fixed by normalising for the ownership probe only, with 18 hostile shapes still refused and 42/42 probe-decision equivalence with base); rounds 2-4 found only evidence-consistency defects; round 4 returned P0/P1/P2 = none. Evidence: `docs/evidence/AS-OBSIDIAN-CAPTURE-001-F5-NEWLINE-FIDELITY.md`.
- [x] AS-OBSIDIAN-CAPTURE-001-F6 An unreadable note must not escape the module's error boundary (INTEGRATED and SEALED). Both projection writers read the prior note to splice a generated span into it; when that read failed -- not valid UTF-8, or unopenable -- the raw `UnicodeDecodeError`/`PermissionError` escaped the module's error boundary, so a caller catching the module's own error type did not catch these at all (the two `PermissionError` cases already carried the path via `OSError.filename`; the escape is what was wrong in all four). A **consistency** defect, not a design question: `obsidian_capture_note.write_note` already catches `(OSError, UnicodeError)` correctly; the two projection writers did not. Reproduced on `91f40368`: **four** leaks (both writers x both failure modes), not the three an informal probe found -- `obsidian_projection` leaks on the unreadable case too. #707's 60,000-trial fuzz reported 0 raw leaks and is not contradicted: its corpus was valid UTF-8 with malformed markers, so invalid bytes were never in the space. **Availability and diagnostics, NOT data loss** -- for `graph_projections` the read precedes `_promote`, so that failure path already wrote nothing, and two tests assert that by sha256 rather than assuming it. 13 tests asserting the exact domain type, the named note and `__cause__`; six negative controls (13 passed -> 5/2/4/1/2/1): A-D fail pairwise disjoint sets, and F is contained in E which is contained in C -- F < E < C, all strict, including one pinning that widening the guard over the merge must not relabel F3's `malformed-generated-markers`. **The first candidate `24fbf2f4` FAILED verification**: its `chmod(0o000)` tests are not portable (Windows `chmod` clears only the read-only bit, so the read succeeds -- Windows CI was red) and, more substantively, on Windows the condition surfaces at WRITE time where `_write_atomic`'s `os.replace` was unguarded, leaking a raw `PermissionError`. Tests now inject the failure so the boundary is exercised on every runner and uid; the write path is guarded too. **Not claimed**: that `obsidian_projection` writes nothing on failure -- it calls `_write_atomic` inside its project loop, so a multi-project vault can have an earlier note already rewritten (pre-existing, proven on base); that `ingestion.py`'s parallel plain-`ValueError` sites are addressed; or that the diagnostic is uniform across surfaces. Evidence: `docs/evidence/AS-OBSIDIAN-CAPTURE-001-F6-ERROR-BOUNDARY.md` -- **INTEGRATED and SEALED 2026-09-08** as PR #729 (merge commit `e264d599`, second parent `c5d85fe7`, base `7b0989a7`). **Merged unrebased at the verified object**: `git diff c5d85fe7 e264d599` is empty and the merge object's `src` (`8086e6f9`), `tests` (`e6157e27`) and `docs` (`aa0b3336`) trees are hash-identical to the certified object. **Nine IV rounds against nine objects** (`ce771fb7` was pushed but superseded before a round ran on it) -- the most-corrected package in this lane. An earlier revision of this entry claimed every finding after round 1 was in the claim record rather than the code; that is **false** and was raised by two independent reviewers. Six of the nine objects changed `src`/`tests` (`24fbf2f4`, `c61efe3a`, `cb9d882d`, `3710db65`, `087c5c01`, `1c65ee98`); only `c995040a`, `8ec6311d` and `c5d85fe7` were documentation-only. R3's `finally` masking and R5's discarded log payload were engineering defects -- they are this seal's controls E and F. What recurred was the bookkeeping defect *class*, not every finding. The rounds: R1 platform-incomplete fix (Windows fails at `os.replace`, not at the read), R2 a control corrected without re-running it, R3 a `finally` that could replace the error it cleaned up after, R4/R6 figures and the prose describing them updated in some copies and not all, R5 a `_LOG.warning` whose `extra` was passed at top level so both formatters discarded it -- the log existed and carried nothing while the receipt claimed the residual was operator-visible, R7 four prose distance figures, R8 a byte-identity premise that contradicted a measurement taken one step earlier plus a full-suite figure the base refresh had moved, R9 a ledger figure hardcoded in the body generator so regeneration could not catch it. **Post-merge seal on `e264d599`**: F6 suite 13 passed; nine-file group set 277 passed / 4 xfailed; full suite 5,682 passed / 8 skipped / 4 xfailed; freeze guard 78; ruff and mypy clean (405 files). **All six negative controls reproduce on main** (13 passed -> 5/2/4/1/2/1), each mutation applied under a sha256 assertion that it changed the file and both sources restored byte-identical afterwards; A-D fail **pairwise disjoint** sets and `F` is a strict subset of `E`, itself a strict subset of `C`, verified on failing-test-name sets rather than counts. Control D reproduces only when the merge call is moved inside the `try` **and** the except clause is widened -- each half alone is a no-op (13 passed), which is why an earlier reconstruction of D that returned 13 passed was discarded rather than reported. **Residuals recorded, not fixed**: `graph_projections` still emits the bare `malformed-generated-markers` at five sites and `ingestion.py:103`/`:107`/`:484` raise a plain `ValueError` with no diagnostic (owner-gated, frozen surface); `_write_atomic`'s `mkdir` sits outside the new guard; F5's sealed work-package entry still carries an abbreviated suite list whose expansions do not exist, untouchable here because it lies in the byte-identical prefix that proves no sealed record was rewritten; and the receipt's own headline figures and control tables remain typed rather than derived, which is where the round-8 blocker lived.
- [x] AS-OBSIDIAN-CAPTURE-001-F7 A BOM must not make a note unmanageable (INTEGRATED and SEALED). Windows Notepad writes UTF-8 **with** a byte-order mark by default, so an operator who opens an Atlas-managed note there and saves it gets a file Atlas no longer recognises as its own -- correct `capture_id`, correct frontmatter, just a `U+FEFF` prefix -- and every refresh is refused `OBSIDIAN_NOTE_CONFLICT` ("refusing to overwrite a note Atlas does not manage"). The note stops updating permanently and the error names the wrong cause. Reproduced on `8076d360` through the real `capture()`/`retry()` driver. **Pre-existing**: same consumer and consequence as the CRLF-frontmatter regression F5's verification caught, different trigger, so deliberately NOT folded into F5. **The trap is sharper than the defect**: refusing an unrecognised note is correct fail-closed behaviour, so the fix widens what Atlas RECOGNISES, never what it ACCEPTS -- one line, stripping the BOM from the ownership *probe copy* only (the same split `canonical_content` draws for identity), with ownership still requiring `atlas.managed is True` and a matching `capture_id`. Evidence is weighted accordingly: 18 hostile-shape refusal tests against 8 recognition tests, each hostile shape asserted refused AND left byte-identical. Four negative controls, each load-bearing and each failing a *distinct* set (26 passed -> 6/4/3/18); not disjoint -- |A n B| = 3 and C is a subset of D; dropping the `capture_id` match fails all 18 hostile shapes, which is what makes this safe to land. Not claimed: that the BOM is preserved -- it sits in Atlas-owned generated territory and is dropped on re-render, pinned by test, with HUMAN bytes surviving verbatim. **Residual, pre-existing and out of scope**: `yaml.safe_load` raises a bare `KeyError` (not a `yaml.YAMLError`) for a malformed explicit bool tag, so `_existing_capture_id` escapes uncaught out of the public `retry()` API -- fail-closed in outcome, an escaped exception in mechanism; identical at base and head. Evidence: `docs/evidence/AS-OBSIDIAN-CAPTURE-001-F7-BOM-OWNERSHIP.md` -- **INTEGRATED and SEALED 2026-09-08** as PR #731 (merge commit `7b0989a7`, second parent `3d5b1d97`, base `8076d360`). **Merged unrebased at the verified object**: `git diff 3d5b1d97 7b0989a7` is empty and the merge object's `src` (`2d3d6d88`), `tests` (`ebb90845`) and `docs` (`cd818083`) trees are hash-identical to the object round 5 certified, so that certification transfers by hash rather than by assertion. Five IV rounds against six objects; round 5 returned **PASS with no P0, no P1 and no P2** and concluded the object was mergeable. The `src` tree was identical across all six PR objects and `obsidian_capture_note.py` is blob `d6617798` in every one, so **the one-line fix never changed after round 1**; an earlier revision said "every round after the first changed only evidence prose", which overstates it, since the `tests` tree moved in three of the six. CI green on all four jobs at the exact head, Windows included. **Post-merge seal on `7b0989a7`**: F7 suite 26 passed; F5 suite 27 passed / 4 xfailed; the protected-region and Obsidian selection 259 passed / 4 xfailed; full suite 5,669 passed / 8 skipped / 4 xfailed; freeze guard 78; ruff and mypy clean (405 files); zero literal `U+FEFF` bytes anywhere in `src/project_atlas`, confirmed by byte scan rather than by grep, because the character is invisible in an editor and that invisibility is what silently no-opped two controls during development. All four negative controls reproduce **on main**, not only on the branch (26 passed -> 6/4/3/18), each mutation applied under an assertion that it actually changed the file and the source restored byte-identical to `7b0989a7` afterwards -- so the protections are load-bearing on the integrated result. **Round 5's two P3s are fixed in the seal, not carried**: the receipt heading "a pre-existing fail-open-shaped path" contradicted its own paragraph, which concludes the *outcome* is fail-closed, and now reads "a pre-existing escaped exception"; and PR #731's description heading "What four verification rounds caught" carried prose for rounds 1-2 only and is corrected there, that heading not being in the receipt.
- [x] AS-OBSIDIAN-CAPTURE-001-F8 #716's split-token near miss is pinned (INTEGRATED and SEALED). A **pin, not a fix**: the behaviour is already correct on `main` and `src/` is byte-identical to it. The F1-F4 residual register recorded that `<!-- atlas:generated:sta rt -->` "behaves correctly but is pinned by no assertion in the F3 test file". Reproduced through `merge_protected_regions` on `7b0989a7` and re-run on `e264d599` after F6 moved the base (`protected_regions.py` is hash-identical at both, `d3fe8615`, so the two runs are one measurement -- both were done rather than assumed): four split shapes are all preserved as ordinary HUMAN prose with bytes intact. **Why it matters**: the owner policy makes marker spellings reserved everywhere, so the matcher's exactness is load-bearing in a direction that fails quietly -- a matcher made *more* tolerant does not error, it starts REFUSING ordinary human prose, leaving a note that discusses Atlas syntax permanently unmanageable with the error naming the wrong cause. Same consumer and consequence as the CRLF regression F5's verification caught and the BOM defect F7 fixed, reached from a third direction. Four cases added to the F3 NEAR_MISS corpus (7 -> 11), plus four byte-level assertions that the HUMAN region survives verbatim rather than merely as a substring. Controls were measured on `e264d599`; the base has since moved to `9972d164` and the measurement is **checked** there rather than assumed (`src` `8086e6f9` and `tests` `e6157e27` identical at both, F3 corpus blob `e07cbf16` at `7b0989a7`, `e264d599` and the base alike). The mutation source is committed at `docs/scripts/f8_near_miss_controls.py`, so the six cells reproduce from a clean checkout. **Three negative controls, each run against BOTH the corpus on main and the pinned corpus**, because a control that only shows the new tests failing proves they are tests, not that they are needed: A (broadly whitespace-tolerant matching) 1 caught -> 9; B (tolerant only of a break inside the token) **0 caught, 37 passed clean** -> 6; C (tolerant only of whitespace around the colons) **0 caught, 37 passed clean** -> 2 (the right-hand figures count F8's four corpus entries and its four byte-level assertions; an earlier revision said 5/3/1, measured before those assertions were added in response to verification, and is re-derived here rather than carried). B and C carry the weight -- both are plausible relaxations, both pass entirely undetected against main's corpus, and the pre-existing `extra-inner-spacing` case catches only A. **Not claimed**: that all four shapes come from #716 -- **one** does (`<!-- atlas:generated:sta rt -->`, the only shape the residual register attributes to it); the other three are locally derived to discriminate the controls, and an earlier revision of the receipt and WORKLOG said all four were "the ones #716 raised", which review and verification independently flagged as unsupported provenance. Nor that the corpus is complete -- the near-miss space is not enumerated, no exhaustive sweep is committed, and no coverage fraction is asserted. Evidence: `docs/evidence/AS-OBSIDIAN-CAPTURE-001-F8-SPLIT-TOKEN-PIN.md` -- **INTEGRATED and SEALED 2026-09-08** as PR #740 (merge commit `8aaf7b63`, second parent `bb033a68`, base `9972d164`). **Merged unrebased at the verified object**: `git diff bb033a68 8aaf7b63` is empty and the merge trees `src 8086e6f9`, `tests 00bbde18`, `docs 01fe760d` are hash-identical to the certified object. **`src` is byte-identical to pre-merge `main`**, which is the proof that this was a pin and not a fix. Verified across three heads (`4bb8999a`, `9b2dba97`, `bb033a68`); the verdict strings are deliberately not quoted here -- see the citation boundary below. The central argument was confirmed by a **second mutation mechanism**: verification patched the public `validate_protected_markers` in `protected_regions.py` to count via regex, leaving the document untouched, where the committed tool canonicalises the input inside `merge_protected_regions`. The *mechanism* was independent; the three regex patterns were reused from the committed tool, so this is one derivation tested two ways rather than two from-scratch derivations. Provenance of the figures: the #740 rounds measured the pre-byte-test object (41 baseline, 5/3/1); the 9/6/2 corroboration comes from the seal round's instrument. **Citation boundary**: IV reports are session artifacts and are **not in this repository or on the PRs**, so no verdict is asserted here as fact -- every measurement cited is re-runnable and every git fact checkable, but a verdict is neither. The same limit applies to the F5, F6 and F7 seals. **Post-merge seal on `8aaf7b63`**: F3 suite 45 passed; full suite 5,690 passed / 8 skipped / 4 xfailed; freeze guard 78; ruff clean (both the configured scope and the doc script explicitly, since `docs/scripts` sits outside ruff's `include`); mypy clean (405 files). **The controls reproduce on main** via the committed `docs/scripts/f8_near_miss_controls.py`: base corpus 37 with A 1 / B **0** / C **0**, pinned corpus 45 with A 9 / B 6 / C 2, sources restored byte-identical. **Findings corrected before merge**: the receipt, WORKLOG and PR body claimed all four shapes were "the ones #716 raised" when the residual register attributes exactly one -- raised independently by review and by verification; the corpus assertion `body.strip() in merged` was weaker than the "human bytes intact" claim, so four byte-level tests now pin it, and **adding them moved the control figures from 5/3/1 to 9/6/2**, re-derived in all four copies rather than carried; and a stale WORKLOG numstat survived in the PR body, re-derived in four places and missed in the fifth. Residual: the reproduction tool is linted only by explicit invocation, outside CI's scope.
- [x] AS-OBSIDIAN-CAPTURE-001-F9 One marker diagnosis, whichever writer refuses (INTEGRATED and SEALED). A generated-marker collision is one operator condition, but it did not read as one. Reproduced on `e264d599` and re-checked on current main: the identical corrupt note yields `malformed-generated-markers:count,begin=2,end=1,expected=1,no-write:n.md` from the canonical core and a bare `malformed-generated-markers:n.md` from `graph_projections`, so what an operator was told about their own file depended on which writer reached it first -- an internal routing detail they cannot see. Recorded in the F1-F4 residual register as "the diagnostic is not uniform across surfaces". The five bare sites now emit the canonical diagnosis via a public `generated_marker_diagnosis` exported from `protected_regions`. **Diagnosis, not policy**: the same notes are refused, with the same fail-closed guarantee and the same bytes on disk, both pinned by test; the message PREFIX is unchanged, so all **100** pre-existing tests matching `malformed-generated-markers` across five suites keep passing. One site gets an honest distinct reason: when the FRESH RENDER offers no generated span the refusal is an Atlas-side condition, not a corrupt note, and reporting it as a marker malformation pointed the operator at the wrong artifact -- it now reads `rendered-has-no-generated-span`. **Five negative controls** (33 passed -> 12/4/4/1/2), each under a sha256 assertion that the mutation changed the file, sources restored byte-identical. **Controls C and E earned their place by first failing to fail**: reverting the `_generated_span` site left the suite at 27 passed, because that guard is UNREACHABLE through `_merge_protected_regions` -- `_validate_protected_markers` runs first on both `existing` and `rendered` and already refuses every triggering shape. It is defence in depth for direct callers, now pinned by four direct-call tests, which is the only way it can be pinned. **Owner-gated, not fixed**: `ingestion.py:103`/`:107`/`:484` raise a plain `ValueError` with a different spelling again (`malformed generated markers`, spaces not hyphens) and no diagnosis at all -- a third surface reporting a third thing for the same condition, and the one closest to the product boundary. It is a certified surface frozen by `test_atlas3_demo_isolation_001`; the only sanctioned path is an owner-approved sha256-pinned exception under `docs/atlas-3/ARCHITECTURE.md` §9.1, which this lane cannot self-grant. **Not claimed**: that refusal behaviour changed, that the three surfaces now agree (two do), or that `_generated_span`'s guard is reachable in production -- it demonstrably is not. Evidence: `docs/evidence/AS-OBSIDIAN-CAPTURE-001-F9-DIAGNOSTIC-UNIFORMITY.md` -- **INTEGRATED and SEALED 2026-09-09** as PR #748 (merge commit `dbf8d838`, second parent `f11d89ec`, base `7f3dff69`). **Merged unrebased at the verified object**: `git diff f11d89ec dbf8d838` is empty and the merge trees `src 08e61813`, `tests 8b4237b8`, `docs 8205a5b6` are hash-identical to the certified object. Four verification rounds across four heads. **Post-merge seal on `dbf8d838`**: diagnosis parity **7 of 7** corrupt shapes byte-identical between the canonical core and `graph_projections`, including two shapes absent from this package's own tests; F9 suite 33 passed; the five pre-existing suites 100 passed; freeze guard and lifecycle sweep 81; full suite 5,726 passed / 8 skipped / 4 xfailed; ruff and mypy clean (405 files). **All five negative controls reproduce on main** (33 passed -> 12/4/4/1/2), each mutation under a sha256 assertion that it changed the file, source restored byte-identical. **Reproducible from a clean checkout**: `docs/scripts/f9_diagnostic_parity.py` compares both surfaces over a named corpus plus a generated sweep -- 635 shapes, 616 refused, 635 byte-identical outcomes, 0 divergences, across 27 distinct outcomes of which 168 exercise a generated-marker diagnosis -- and reverting the count site to its bare message yields 72 divergences, while an inert sweep trips the corpus guard. An earlier revision cited 226, inflated roughly threefold by an empty sweep fragment that duplicated 60 shapes, with guards satisfied by construction rather than by coverage. It exists because review raised, correctly, that this seal cited measurements no clean checkout could audit. Verification ran larger corpora of its own reporting the same direction (19 of 27 shapes divergent on the base, 0 at head; refusal set unmoved across 20,314 cases), but **those harnesses are not in this repository**, so those figures are attributed rather than cited as evidence. **A gap in that instrument**: nothing executes it -- not CI, not any test -- and it cannot live under `tests/` without breaking the byte-identity invariant this seal rests on, so it is reproducible on demand rather than continuously enforced; wiring it up is a follow-up. **A residual it found, pre-existing and not F9's**: across a 20,314-case corpus, 41 shapes where the surfaces disagree on whether to refuse (30 canonical-refuses/graph-accepts, 11 the reverse); a separate 40,000-case fuzz gives 90 (61 and 29). An earlier revision attributed the 41 to the 40,000-case run, wrongly, and neither corpus is committed. The larger group is graph **accepting and writing** a rendered document canonical refuses as structurally unpaired -- fail-open-shaped relative to canonical. It concerns HUMAN marker pairing rather than generated-marker diagnosis and is identical on the base, so F9 neither introduced nor worsened it. **It warrants its own work package**, being a refusal-set divergence between two writers measured today by no committed instrument. **Two tests that could not fail were removed**: one compared an immutable `str` to itself while four artifacts cited it as pinning byte identity; its replacement in the same commit globbed `*.tmp`, a suffix this module never writes, and the control validating it renamed staging to `.tmp` to match the test's glob rather than the code's naming. The replacement compares the whole vault byte for byte and was falsified seven ways, including the structural regression its docstring claims to guard. **Owner-gated, unchanged**: `ingestion.py:103`/`:107`/`:484` still raise a plain `ValueError` with a third spelling and no diagnosis -- a certified surface needing an owner-approved sha256-pinned exception under `docs/atlas-3/ARCHITECTURE.md` §9.1. **Citation boundary**: IV reports are session artifacts and are not in this repository, so no verdict is asserted here as fact; the figures this entry rests on are reproducible by the committed script and by re-running the named suites, and every git fact is checkable; figures attributed to verification's own harnesses are marked as such and are not reproducible here.
- [x] AS-OBSIDIAN-CAPTURE-001-F10 Graph must not write what canonical refuses (INTEGRATED and SEALED). Two generated-span-preserving writers disagreed on **whether** a document was safe to write -- not on how to describe a refusal (F9), but on the refusal itself, which is a policy difference at a writer boundary. Reproduced on `dbf8d838` by sweeping **both sides** of the merge, which is what makes it visible: a corpus varying only the prior note cannot see a defect on the rendered side. With a prior note carrying a generated span and no HUMAN regions, and a render whose HUMAN markers are reversed, canonical refuses `malformed-protected-markers:unpaired` while graph **accepts and writes**. **A path asymmetry, not a validator one**, which is why counting arguments missed it: graph's no-HUMAN branch keeps F4's disclosed contract and splices by hand rather than delegating, so it never reaches the canonical structural parse, and its own check compares marker counts and names but never **order**. Both validators accept these shapes; canonical only refuses inside the merge. Fixed by running the already-public `reject_ambiguous_region_identity` on the rendered document -- four lines plus an import. **Differential sweep, both sides varied** (every row reads `<pairs> pairs / <divergences> divergences / <fail-open> fail-open`, because an earlier revision abbreviated the head rows to `2 / 0 fail-open`, where the leading figure reads as a pair count rather than a divergence count): base depth 2 = 625 pairs / 3 divergences / 1 fail-open; head depth 2 = 625 pairs / 2 divergences / 0 fail-open; head depth 3 = 15,625 pairs / 12 divergences / 0 fail-open. Every remaining divergence is the opposite direction -- canonical accepts, graph refuses -- which is **F4's disclosed contract**, pinned by a test so it is not removed while "fixing the asymmetry" wholesale. **Five negative controls in one**: removing the check fails 4 of 7 including the differential sweep and the end-to-end brick test; `unclosed-region` fails **either way**, since graph's count check already catches an unmatched BEGIN, so the control distinguishes what the fix actually closes. **Blast radius, corrected**: an earlier revision called this defence in depth needing a broken renderer. False. Nothing in the render path escapes marker text -- `_redact_text` strips secrets and truncates but never touches HTML comments -- so a relationship field carrying HUMAN marker text reaches the render verbatim, and with a prior note lacking HUMAN regions the pre-fix writer **wrote** it, after which every later refresh **including a clean one with zero relationships** was permanently refused. A durable denial-of-refresh; the fix turns a brick into a clean refusal, pinned end to end. **Not claimed**: that a full discover->ingest->graphify run with attacker-controlled sources can plant such a field value -- the render and write layers propagate it unsanitised and the pre-fix writer persists it, but the reachability ceiling is upstream and is not established here; nor that the two surfaces agree in all directions (they deliberately do not); nor that `ingestion.py` is covered (a third writer, owner-gated behind a frozen surface needing an owner-approved sha256-pinned exception under `docs/atlas-3/ARCHITECTURE.md` §9.1). Found by independent verification of F9, which measured it as a refusal-set divergence and recommended its own package. Evidence: `docs/evidence/AS-OBSIDIAN-CAPTURE-001-F10-REFUSAL-PARITY.md`. **INTEGRATED and SEALED 2026-09-09** as PR #753 (merge commit `06362807`, second parent `3b22f6d4`, base `a7adce4e`). **Merged unrebased at the verified object**: the merge tree `1b11d3a0` is hash-identical to the PR head tree, so the exact-head CI that ran on `3b22f6d4` tested byte-for-byte what landed, and `git diff 3b22f6d4 06362807` is empty; component trees `src 8ec29893`, `tests 6aa392e7`, `docs f81c9855`. **Post-merge seal measured on `06362807`**: F10 suite 7 passed; the differential sweep re-run on the merge object at three depths -- 625 / 15,625 / 390,625 pairs, **406,875 in total** -- reports **0 fail-open** at every depth, with the disclosed F4 direction still present and growing (2 / 12 / 42), so the corpus has not gone inert while reporting zero. **The sweep still bites**: with the fix removed from the merge object the same corpus at the same depths reports 1 / 3 / 54 fail-open and the F10 suite drops to 4 failed of 7 (`crossed-a-b`, `reversed-end-before-begin`, `a_poisoned_field_cannot_brick_the_projection`, `no_fail_open_divergence_across_a_differential_sweep`), the mutation applied under a sha256 assertion that it changed the file and the source restored byte-identical (`1d9c0f84` before and after). Freeze guard and lifecycle sweep 81 passed; full suite 5,733 passed, 8 skipped, 4 xfailed; `ruff check .` clean; `mypy src` clean (405 files). **What the seal does NOT claim**: that upstream reachability is established -- a live corruption path is demonstrated at the render and write layers, but whether a full discover->ingest->graphify run with attacker-controlled sources can plant such a field value is not shown; that the two surfaces agree in all directions (they deliberately do not, and F4's disclosed contract is asserted so it is not removed while "fixing the asymmetry" wholesale); or that `ingestion.py` is covered (a third writer, owner-gated behind a frozen surface needing an owner-approved sha256-pinned exception under `docs/atlas-3/ARCHITECTURE.md` §9.1). **The code held from the first reproduction; every blocking finding was in the claim record.** Three corrections were needed before sealing: the blast radius was understated (an earlier revision called it defence in depth needing a broken renderer, and reproduction proved a durable denial-of-refresh instead); that retraction then reached the WORKLOG, the backlog and the receipt's Blast radius section but NOT the test module's docstring or the receipt's own `What is not claimed` list, whose first bullet still asserted the retracted claim in the conclusion a reader reaches last -- corrected-in-some-copies-not-all, on the central claim of a package whose subject is claim honesty; and a count was wrong by one, four propagating relationship fields named where there are five, the fifth (`provenance.graphify_artifact_refs[].relative_path`) being the one that bypasses `_redact_text` entirely. A fourth, on the code side: the first version of the no-write test bound `before = NO_HUMAN_PRIOR` and asserted `before == NO_HUMAN_PRIOR` -- two names for one immutable `str`, the third tautological assertion in this lane and the second written after the lesson was recorded; both review bots and verification caught it independently, and it was replaced by the end-to-end reproduction, which fails without the fix.
- [x] AS-OBSIDIAN-CAPTURE-001-F11 The mkdir failure site in each writer (IMPLEMENTED; MERGED on main via #754, 2026-09-09T08:08:32Z; no formal-IV receipt located on the PR or the DAG event bus -- MERGED != IV_PASS. Absence of a receipt is not proof that no review occurred, and this merge grants no permission to bypass current gates). F6 closed the error boundary for the read of the prior note and for `os.replace`; creating the note's parent directory was left OUTSIDE the guard in **both** generated-span-preserving writers, so a blocked or unwritable parent escaped as a raw `OSError` past `ObsidianProjectionError` / `GraphProjectionError` -- the defect F6 exists to prevent, one step earlier in the same function. Reproduced on `a7adce4e` and again on current `main` by a plain file where a path component must be a directory: both writers leak `NotADirectoryError`. **Both sites were already recorded and an earlier revision claimed otherwise; that is retracted** -- F6's residual register names the `_promote` one explicitly ("an ancestor directory replaced by a file") 13 lines above the bullet this package quoted. The fix is new; the finding was not. Six lines of code replace one in each writer (`+11/-1` and `+10/-1` with comments), raising the module's own error type and naming the **directory**: `unwritable-note-directory:<Type>:<path>`. **The P0 this package paid for**: two tests asserted the literal string `"NotADirectoryError"`; Windows raises `FileExistsError` for the same fixture and CI went red, and on Linux alone the class depends on shape (ancestor file -> ENOTDIR 20, immediate parent -> EEXIST 17), so the assertion was platform- AND shape-coupled from the start. Fixed by **measuring**: a helper performs the same `mkdir`, catches the `OSError`, and the test asserts the guard names THAT class -- portable and strictly stronger, since a guard reporting a generic `OSError` now fails where the hard-coded string could not detect it. Under a plugin simulating the Windows class: new assertion 5 passed, old 2 failed / 3 passed. Windows CI green at `83a3d7a3` and again at `56c986f1`, 5,682 passed at each -- the SHAs are named because a backlog line outlives the PR. **A justification that was exactly backwards**: the docstring claimed a `pytest.raises` test "would pass on a writer that raised nothing at all" and used that to prefer a `try/except OSError`. `pytest.raises` fails with DID NOT RAISE and re-raises non-matching exceptions, so it rejects both failure modes; demonstrated with a mutant that swallows the `mkdir` failure and raises nothing -- old form 1 passed, new form 1 failed. That test was also redundant with the two above it and now runs a second, materially different blocked shape. **Controls** (each mutation under a sha256 assertion, sources restored byte-identical): baseline 5 passed; projection guard removed 3 failed; graph guard removed 3 failed; both guards swallow and raise nothing 4 failed / 1 passed; guard reports a generic `OSError` 3 failed; fixture made inert 3 failed (one fixture) or 4 failed (both); restored 5 passed. Read the 3/3 rows honestly -- each is carried by ONE guard-specific test, the other two being shared; judged on name sets neither contains the other, so both guards are independently load-bearing with one witness each, not three. **Reachability, corrected downward**: an earlier revision said the failure "surfaces today as an unhandled traceback"; not true at either production surface -- `cli.py:4264` catches `(ObsidianProjectionError, OSError, ValueError)` and `connect.py:786` catches `(OSError, ValueError, KeyError, TypeError)`, both already containing the raw error on base. Only `demo_readiness.py:162`, an internal harness, is unguarded. Weaker still on the graph side: `write_projection_outputs` has **no caller anywhere in `src/`** (seven test modules only). What this buys is precision and type-correctness at the boundary, not traceback-vs-diagnostic. **Not a policy change**, verified independently rather than argued: 67 legitimate scenarios at the previous head (attributed, not cited -- that harness is not in this repository) and 57 re-instrumented at this one with tree-level manifests compared by path and sha256, zero differing scenarios; plus 960 concurrent writes into a shared not-yet-existing tree with zero errors measured on base AND head. **What it does NOT close**: F6's register named THREE raw `_promote` escapes and this closes one; the other two remain raw (`graph_projections.py:620` read-only output directory, `:616` unreadable existing target) plus an unregistered `ENAMETOOLONG` at `:614`, all filed as **#757**. **The object matters more than the numbers**: the same three sites are at 603/605/609 on `ef628223`, 605/607/611 on current `main` (`e4dd17bc`), and 614/616/620 here. An earlier revision cited the first set and called it `main`; a first correction cited this set and called it the merged base -- both wrong about the OBJECT rather than the arithmetic, which is why the code expressions are the durable anchors. Issue #757 names `main` and therefore needs 605/607/611, not these; its body was corrected to those numbers on 2026-09-09, verified by read-back rather than by exit code. **Not claimed**: that every `OSError` in these modules is contained (the mkdir site only); that the F6 property holds "at all three sites" as an earlier docstring said; that `ingestion.py` is covered (a third writer, owner-gated behind a frozen surface needing an owner-approved sha256-pinned exception under `docs/atlas-3/ARCHITECTURE.md` §9.1); or that anything is verified on Windows beyond CI. Evidence: `docs/evidence/AS-OBSIDIAN-CAPTURE-001-F11-MKDIR-BOUNDARY.md`.
- [x] AS-OBSIDIAN-CAPTURE-001-F12 The committed instruments must actually run (IMPLEMENTED; MERGED on main via #756, 2026-09-09T10:22:57Z; no formal-IV receipt located on the PR or the DAG event bus -- MERGED != IV_PASS. Absence of a receipt is not proof that no review occurred, and this merge grants no permission to bypass current gates). Three evidence instruments under `docs/scripts/` were each committed because "an instrument described is not an instrument available", and measured on `a7adce4e` **none of the three is referenced by any test or CI file**: `pyproject.toml` sets `testpaths = ["tests"]` so pytest never collects them, and ruff's `include` covers only `src/**/*.py` and `tests/**/*.py`, so they are not linted either -- they can be renamed or broken by a refactor with nothing noticing, which is the same failure the seals were meant to close, one level up. Scope differs per instrument: `f9_diagnostic_parity.py` has a decidable contract and is **executed** with its result asserted; `f8_near_miss_controls.py` rewrites source files and restores them in a `finally`, so it is import- and interface-checked only; `seal_retracted_claim_sweep.py` over-reports by design and needs a human to classify hits. **Five revisions of one guard, four defeated by verification on the round after each was called complete**: a source substring (needle contained a space, haystack had them stripped -- it could never match, and the subprocess path that would really run the mutating instrument passed UNDETECTED); AST call sites (15 of 20 paths slipped past, including `_load(_MUTATING).main()` using the module's own constant, on the very line the guard test writes); a runtime fuse (which published its own bypass -- the real callable on `__wrapped__`, the standard unwrapping convention, plus a second unfused loader added by the same commit); the content hash (a same-named file with ONE BYTE edited loaded unfused, and a test of mine asserted that behaviour, so the suite was DEFENDING the gap while its control passed); and finally **filename OR content hash**, the union rather than a sixth replacement. **The lesson generalises**: replacing the identity a guard keys on trades one hole for another, because neither rule is a superset of the other -- other-name/identical-bytes escapes the name rule and is blocked by the hash; same-name/one-byte-edited escapes the hash rule and is blocked by the name. Each replacement felt like a strict improvement because it closed the case the reviewer had just named, and nobody re-measures the column that used to pass. Both halves are load-bearing (removing either fails a test), and the predicate has not collapsed to `True`: unrelated code under a different name is still not fused, and `f9`, the one instrument that genuinely executes, still runs to exit 0. The second layer is a static detector for primitives that bypass the loader (`subprocess`, `runpy`, `os`, `importlib`, `asyncio`, `exec`/`eval`/`compile`), resolved through import aliases, module-name spellings and any name EVER bound to something naming the script, deliberately order- and scope-blind. It **fails closed**: an earlier revision capped the name-resolution fixpoint at 16 and returned what it had, so a long enough reverse-ordered chain produced an empty -- i.e. "clean" -- verdict with no error. **Two residues, measured and not closed**: a copy changing BOTH name and content matches neither half; and the detector does not reach a path that both bypasses the loader and names the script through no resolvable constant. Nothing plausible produces either by accident, which is a different claim from saying they are absent. **A stale `__pycache__` hazard** found by verification and reproduced: a same-size, same-mtime edit leaves the pyc header matching, so the loaded module executes OLD code while the file says something else -- inert in CI, live in the local edit-test loop where this lane runs its negative controls, and where a control has already been misled by a stale snapshot once. The loader now drops cached bytecode first, pinned against a throwaway file with the hazard reproduced inside the same test. **Sixteen controls**, each under an anchor assertion with the file restored byte-identical, baseline 19 passed; **six of those rows passed at the revision before the one that added them**. **Claims corrected under verification**: "one pass could not see it" was wrong (a single pass resolves forward chains; only reverse-ordered ones need iteration); "14 of 14 detected" had no derivable denominator (the corpus is 22, all 22 detect); and the `CITED` comment claimed "only `main` appears at all" when four of ten attributes appear zero times -- now stated WITH its method, since the prior round could not reproduce the bare figure and the missing method was exactly why. **Not claimed**: that the instruments are *correct*, only that they still load and, for the one with a decidable contract, still report what the record says; and `f9_diagnostic_parity.py` always passes `rendered=FRESH`, so it varies only the prior note and **cannot observe a rendered-side divergence at all** -- exactly the class F10 needed a two-sided sweep to find. **A gap recorded rather than fixed**: `seal_retracted_claim_sweep.py` is cited by NO evidence record, backlog line or WORKLOG line anywhere -- committed as reproducible evidence with nothing referring to it, the same rot this package catches, one level further out.
- [ ] AS-OBSIDIAN-CAPTURE-001-F5-B Line-ending fidelity at the fourth writer (OWNER-GATED; not runnable by this lane). `ingestion.py:99` (`_generated_content`) preserves a generated span and reads the prior note with `Path.read_text(encoding="utf-8")`, so it carries the identical F5 defect -- reproduced on `7a9eeb76`, CR 2->0 -- but `src/project_atlas/ingestion.py` is a certified surface frozen by `test_atlas3_demo_isolation_001`, and the only sanctioned edit path is an owner-approved exception pinned to an exact sha256 under `docs/atlas-3/ARCHITECTURE.md` SS9.1. **The fix is one line** (swap the read for `read_note_text`, the helper F5 introduces -- **which is not on `main` until F5 merges**; an earlier revision of this entry said "already merged by F5", which tripped `PROMOTE_ELIGIBLE != MERGED/DEPLOYED/AUTHORITATIVE`) plus the exception entry; what is missing is the owner decision, not engineering. Until then the four reproductions run as `xfail(strict=True)` in `tests/unit/test_as_obsidian_capture_001_f5_newline_fidelity.py`, so the residual self-alerts rather than decaying into prose.
- [ ] AS-OBSIDIAN-CAPTURE-003 Conversation intelligence (summary/decision/action extraction; consumes AS-OBSIDIAN-CAPTURE-001 CaptureRecords; AI enrichment stays opt-in)
