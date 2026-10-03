# Atlas Web (`apps/web`)

**Vite + React (TypeScript)** shell with hash client router, design-lab
themes (AS-WEB-002), and production lenses (AS-WEB-003 / WEB-ACCEPT-002).

Architecture: ADR-008 (stack) · ADR-009 (tokens) · ADR-010 (Command Center).

## Invariants

Governed by the ATLAS Global Style Mission (`docs/global/ATLAS-GLOBAL-STYLE-MISSION.md`):
truthful UI and semantic separation of connectivity, freshness, health, execution,
verification and authorization.

- **UI ≠ canonical** — this app never writes Layer B / claims / authority.
- **Graph ≠ authority** — derived displays only.
- **Unknown ≠ healthy** — missing OBS snapshot renders as `unknown`.
- Governor sign-off is recorded; UI ≠ canonical remains normative after acceptance.
- Vault reads go through `project_atlas.web_api` (Python); the shell may use
  `public/sample-read-status.json` until an HTTP bridge lands.

## Run

```bash
cd apps/web
npm install
npm run dev
```

### Production shell routes

| Route | Surface |
|---|---|
| `#/` | Hub |
| `#/projects` | Projects lens |
| `#/knowledge` | Knowledge lens |
| `#/graph` | Graph lens (derived ≠ authority) |
| `#/ops` | Ops Health + receipt evidence (read-only unknown stub) |
| `#/command-center` | Command Center modes |
| `#/mission-control` | Mission Control — LIVE-first; `?mode=live\|demo\|fixture` |
| `#/workspace` | Workspace — LIVE-first; `?mode=live\|demo\|fixture` |
| `#/design-lab/*` | Design-lab themes A–D |

### Design-lab themes

| Route | Theme |
|---|---|
| `#/design-lab/ledger-desk` | A · Ledger Desk |
| `#/design-lab/signal-rack` | B · Signal Rack |
| `#/design-lab/cartograph-quiet` | C · Cartograph Quiet |
| `#/design-lab/terminal-honest` | D · Terminal Honest |

## Smoke / acceptance gates (local)

No browser install required for file-presence + invariant smoke:

```bash
cd apps/web
npm run smoke
# equivalent:
node apps/web/scripts/smoke.mjs
```

STYLE-002 (missing ≠ zero / clean) gates — runtime tests of
`src/lib/missingState.ts` plus source gates for each audited lens:

```bash
cd apps/web
npm run test:missing-not-zero
```

Use `countOrUnknown` / `lengthOrUnknown` / `boolOrUnknown` / `listState` from
`src/lib/missingState.ts` instead of `?? 0`, `?? false` or `?? []` whenever the
value comes from a source that may be absent, failed or unread.

## Browser E2E acceptance (`AS-WEB-BROWSER-E2E-001`)

`e2e/mission-control.acceptance.spec.ts` is the first repository-native browser
acceptance spec. It is the automated, reproducible reproduction of the operator
journey that was previously captured only as a manual screen recording
(observational evidence). It drives real Chromium via Playwright:

- Hub `#/` renders the production shell
- Projects `#/projects` renders the read-only inventory (`demo-alpha`)
- Mission Control `#/mission-control` shows the LIVE failure **honestly**
  (`choose DEMO or FIXTURE mode (no silent invent)`), not silently replaced
- clicking **DEMO** changes state: URL → `?mode=demo`, DEMO stub banner + blurb
  appear, and the Mission board populates (still `0` PILOT estate rows)
- design-lab `#/design-lab/terminal-honest` renders the themed JSON block

```bash
cd apps/web
npm run test:e2e            # builds + vite preview on :4173 (override: PLAYWRIGHT_WEB_PORT)
```

Playwright's Chromium is provisioned by the environment install (`npx playwright
install chromium`); run it manually once if you set up deps by hand.

Status (updated only from a green automated run, never from the recording):

- `MANUAL_BROWSER_EVIDENCE = OBSERVED`
- `BROWSER_E2E = PASS` — 5/5 specs green, reproduced across repeated
  Playwright-managed standalone runs. UI ≠ canonical remains normative.

Unit gates (from repo root, after `pip install -e ".[dev]"`):

```bash
python -m pytest tests/unit/test_as_web_001_web_api.py \
  tests/unit/test_as_web_accept_001_checklist.py \
  tests/unit/test_as_web_accept_002_closeout.py \
  tests/unit/test_as_web_ops_health_001.py -q
```

These commands document and exercise acceptance evidence under
`docs/AS-WEB-ACCEPT-GOVERNOR-SIGNOFF.md`.

CI: invoke the same `node apps/web/scripts/smoke.mjs` step in the quality
job when the workflow matrix is healthy. Empty-step CI failures are tracked
under `CI_INFRA_EXCEPTION` and do **not** alone overturn WEB ACCEPTED.

**WEB APPLICATION ACCEPTED = YES** (governor APPROVED on pinned tip; see
`docs/AS-WEB-ACCEPT-GOVERNOR-SIGNOFF.md`). UI ≠ canonical remains in force.
RELEASE CERTIFIED remains **NO**.

## Design tokens

Shared CSS variables live in `src/tokens.css` and are remapped per
`[data-theme="…"]`. See [ADR-009](../../docs/adr/ADR-009-web-design-tokens.md).

## Status page

Pages show vault health / read status from the sample stub (shape matches
`web_api.read_status`). Do not invent PILOT data here.
