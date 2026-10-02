# Atlas Vault Agent Documentation Skill

This subproject defines a reusable `SKILL.md` and companion tooling that requires every participating agent to document meaningful project work in the Project Atlas vault during the same work cycle in which the work occurs.

## Outcome

```text
Agent work
   |
   v
Immediate raw event capture
   |
   +--> Atlas source evidence (immutable)
   |
   v
mda-cli normalization
   |
   v
Structured Agent Work Event
   |
   +--> project log
   +--> current status
   +--> work package
   +--> decision / validation / risk / issue
   |
   v
Validation + documentation receipt
```

A task is not complete until meaningful work events are captured, evidence and validation are recorded, affected Atlas records are updated or queued, and the agent reports an `ATLAS-DOC-RECEIPT`.

## Capture precedes normalization

Raw events are durable evidence and must be written immediately. mda-cli may require a provider or experience a transient failure, so:

- capture is deterministic and local;
- normalization happens after capture;
- provider failure never causes documentation loss;
- unsynchronized events remain visible and block clean completion.

## Contents

| Path | Purpose |
|---|---|
| `SKILL.md` | Universal agent behavior contract |
| `MDA-STANDARD.md` | Governing mda-cli transformation specification |
| `references/` | Schemas, taxonomy, routing, and integration rules |
| `scripts/capture_event.py` | Dependency-free atomic raw-event capture |
| `scripts/check_documentation.py` | Capture and spool validation |
| `scripts/normalize_event.py` | Verified mda-cli normalization orchestration |
| `internal/` | Normalization subsystem (process, provenance, verification) |
| `templates/` | Raw, normalized, log, and receipt templates |
| `adapters/` | Common agent configuration fragments |
| `config/atlas-agent.example.yaml` | Project/vault configuration |
| `ACCEPTANCE_TESTS.md` | Behavioral acceptance contract |
| `IMPLEMENTATION_ROADMAP.md` | Delivery phases |
| `START_HERE_AGENT_PROMPT.md` | Coding-agent kickoff |

## mda-cli compatibility

The skill follows native mda-cli conventions:

- root `SKILL.md`;
- root `MDA-STANDARD.md`;
- optional Python helpers under `scripts/`;
- installation under a resolved skill directory;
- four-backtick Markdown output for safe stripping and writing.

## Quick start

PowerShell:

```powershell
pwsh -File scripts/install-skill.ps1
```

Bash / zsh:

```bash
bash scripts/install-skill.sh
```

Capture:

```bash
python scripts/capture_event.py   --vault /path/to/atlas-vault   --project-id PRJ-EXAMPLE   --project-slug example-project   --event-kind implementation   --summary "Implemented deterministic source hashing"   --agent codex   --work-package WP-002   --changed-file src/project_atlas/discovery/hash.py   --command "python -m pytest tests/unit/test_hashing.py"   --result "12 passed"
```

Normalize using an installed skill:

```bash
mda --skill atlas-vault-documentation <raw-event.md>
```

Or use the repository-local directory:

```bash
mda --skill-dir ./subprojects/atlas-vault-agent-skill <raw-event.md>
```

Validate:

```bash
python scripts/check_documentation.py --vault /path/to/atlas-vault
```

### Resume captured Vault events

If a managed `document` command captured raw Vault events but normalization or
routing stopped before session accounting closed, resume only the event IDs
already registered to that session:

```bash
atlas-agent reprocess-events \
  --vault-root /path/to/atlas-vault \
  --session-id AS-... \
  --mda-command mda \
  --json
```

The command discovers raw events from the selected session state, validates
their Vault/session/skill bindings, and composes the existing normalizer and
router. It does not accept arbitrary event paths, capture new events, process
spool work, issue receipts, or complete sessions. Pending spool work must use
`atlas-agent sync-spool`. Existing normalized outputs are revalidated and
routed idempotently; session counters are reconciled only after every
registered event succeeds.
