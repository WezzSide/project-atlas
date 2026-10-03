/**
 * AS-WEB-003 + AS-WEB-ACCEPT-001 smoke — production shell, design-lab, ADRs.
 * Exit 0 on success; non-zero on missing artifacts / invariant breaks.
 * Does NOT claim WEB APPLICATION ACCEPTED.
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = join(root, "..", "..");

const required = [
  "package.json",
  "README.md",
  "index.html",
  "vite.config.ts",
  "src/main.tsx",
  "src/App.tsx",
  "src/tokens.css",
  "src/styles.css",
  "src/pages/HomePage.tsx",
  "src/pages/production/ProjectsPage.tsx",
  "src/pages/production/KnowledgePage.tsx",
  "src/pages/production/GraphPage.tsx",
  "src/pages/production/OpsHealthPage.tsx",
  "src/pages/production/CommandCenterPage.tsx",
  "src/pages/production/MissionControlPage.tsx",
  "src/pages/production/WorkspacePage.tsx",
  "src/pages/production/SourceHealthPage.tsx",
  "src/hooks/useLiveSourceHealth.ts",
  "scripts/test-source-health-web.mjs",
  "src/lib/missingState.ts",
  "scripts/test-missing-not-zero.mjs",
  "src/pages/design-lab/LedgerDeskPage.tsx",
  "src/pages/design-lab/SignalRackPage.tsx",
  "src/pages/design-lab/CartographQuietPage.tsx",
  "src/pages/design-lab/TerminalHonestPage.tsx",
  "src/components/LabNav.tsx",
  "src/components/LabShell.tsx",
  "src/components/ProdNav.tsx",
  "src/components/ProdShell.tsx",
  "src/components/ReadStatusPanel.tsx",
  "src/components/LensModeSwitcher.tsx",
  "src/hooks/useReadStatus.ts",
  "src/hooks/useLiveMissionWorkspace.ts",
  "public/sample-read-status.json",
  "public/sample-mission-control.json",
  "public/sample-workspace.json",
  "public/sample-mission-control.fixture.json",
  "public/sample-workspace.fixture.json",
];

const missing = required.filter((rel) => !existsSync(join(root, rel)));
if (missing.length) {
  console.error("AS-WEB-ACCEPT-001 smoke FAIL — missing:", missing.join(", "));
  process.exit(1);
}

const stub = JSON.parse(
  readFileSync(join(root, "public/sample-read-status.json"), "utf8"),
);
if (stub.ui_canonical !== false || stub.graph_authority !== false) {
  console.error("AS-WEB-ACCEPT-001 smoke FAIL — stub must keep UI/graph non-authority");
  process.exit(1);
}
if (stub.unknown_equals_healthy !== false) {
  console.error("AS-WEB-ACCEPT-001 smoke FAIL — unknown_equals_healthy must be false");
  process.exit(1);
}
if (stub.health?.rollup === "healthy" && stub.health?.available === false) {
  console.error("AS-WEB-ACCEPT-001 smoke FAIL — unavailable health must not be healthy");
  process.exit(1);
}

const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
if (!pkg.dependencies?.["react-router-dom"]) {
  console.error("AS-WEB-ACCEPT-001 smoke FAIL — react-router-dom required for client routes");
  process.exit(1);
}
// PREP-GUARD: Windows Vite/Rollup builds need the platform-specific native
// package to actually be resolvable, not merely declared. npm's optional-
// dependency resolution has a known bug (npm/cli#4828 and similar) that
// can silently skip installing an optionalDependencies entry even on a
// matching platform. Checked here directly against the installed tree (on
// win32 only -- irrelevant elsewhere), not against which package.json
// section declares it: the package stays under optionalDependencies
// (never `dependencies`) because its own "os": ["win32"] field is only
// honored by npm's *optional*-dependency platform filter -- moving it to
// `dependencies` makes an unsupported-platform install a hard `npm ci`/
// `npm install` failure (EBADPLATFORM) on Linux/macOS instead of a no-op.
if (process.platform === "win32" && !pkg.optionalDependencies?.["@rollup/rollup-win32-x64-msvc"]) {
  console.error(
    "AS-WEB-ACCEPT-001 smoke FAIL — declare @rollup/rollup-win32-x64-msvc " +
      "under optionalDependencies for Windows builds",
  );
  process.exit(1);
}
if (process.platform === "win32") {
  const rollupWin32Entry = join(
    root,
    "node_modules",
    "@rollup",
    "rollup-win32-x64-msvc",
    "package.json",
  );
  if (!existsSync(rollupWin32Entry)) {
    console.error(
      "AS-WEB-ACCEPT-001 smoke FAIL — @rollup/rollup-win32-x64-msvc did not install " +
        "(known npm optional-deps bug on Windows, npm/cli#4828: delete node_modules + " +
        "package-lock.json and re-run npm install)",
    );
    process.exit(1);
  }
}

const tokens = readFileSync(join(root, "src/tokens.css"), "utf8");
for (const theme of [
  "ledger-desk",
  "signal-rack",
  "cartograph-quiet",
  "terminal-honest",
]) {
  if (!tokens.includes(`[data-theme="${theme}"]`)) {
    console.error(`AS-WEB-ACCEPT-001 smoke FAIL — missing token theme ${theme}`);
    process.exit(1);
  }
}

const app = readFileSync(join(root, "src/App.tsx"), "utf8");
for (const route of [
  "/projects",
  "/knowledge",
  "/graph",
  "/ops",
  "/command-center",
  "/mission-control",
  "/workspace",
  "/source-health",
  "/design-lab/ledger-desk",
  "/design-lab/signal-rack",
  "/design-lab/cartograph-quiet",
  "/design-lab/terminal-honest",
]) {
  if (!app.includes(route)) {
    console.error(`AS-WEB-ACCEPT-001 smoke FAIL — App missing route ${route}`);
    process.exit(1);
  }
}

const commandCenter = readFileSync(
  join(root, "src/pages/production/CommandCenterPage.tsx"),
  "utf8",
);
for (const mode of ["overview", "projects", "ops", "impact"]) {
  if (!commandCenter.includes(`"${mode}"`) && !commandCenter.includes(`'${mode}'`)) {
    console.error(`AS-WEB-ACCEPT-001 smoke FAIL — Command Center missing mode ${mode}`);
    process.exit(1);
  }
}

const main = readFileSync(join(root, "src/main.tsx"), "utf8");
if (!main.includes("HashRouter") && !main.includes("BrowserRouter")) {
  console.error("AS-WEB-ACCEPT-001 smoke FAIL — client router missing in main.tsx");
  process.exit(1);
}

// ADR-008 / ADR-009 / ADR-010 presence (acceptance prep — not certification)
const adrs = [
  ["ADR-008-atlas-web-application.md", "UI"],
  ["ADR-009-web-design-tokens.md", "token"],
  ["ADR-010-atlas-web-ux.md", "Command Center"],
];
for (const [name, needle] of adrs) {
  const path = join(repoRoot, "docs", "adr", name);
  if (!existsSync(path)) {
    console.error(`AS-WEB-ACCEPT-001 smoke FAIL — missing ${name}`);
    process.exit(1);
  }
  const body = readFileSync(path, "utf8");
  if (!body.toLowerCase().includes(needle.toLowerCase())) {
    console.error(`AS-WEB-ACCEPT-001 smoke FAIL — ${name} missing expected content`);
    process.exit(1);
  }
}

const checklist = join(repoRoot, "docs", "AS-WEB-ACCEPT-001-checklist.md");
if (!existsSync(checklist)) {
  console.error("AS-WEB-ACCEPT-001 smoke FAIL — checklist doc missing");
  process.exit(1);
}
const checklistBody = readFileSync(checklist, "utf8");
if (
  !checklistBody.includes("WEB APPLICATION ACCEPTED") ||
  !checklistBody.includes("**YES**") ||
  !checklistBody.includes("closed — APPROVED")
) {
  console.error("AS-WEB-ACCEPT-001 smoke FAIL — checklist must state ACCEPTED=YES with item 10 closed");
  process.exit(1);
}

// web_api read-only boundary — module present, no writer imports in __init__
const webApiInit = join(repoRoot, "src", "project_atlas", "web_api", "__init__.py");
if (!existsSync(webApiInit)) {
  console.error("AS-WEB-ACCEPT-001 smoke FAIL — web_api __init__.py missing");
  process.exit(1);
}
const webApiText = readFileSync(webApiInit, "utf8");
const webApiImports = webApiText
  .split("\n")
  .filter((line) => /^\s*(from|import)\s/.test(line))
  .filter((line) => !line.includes("__future__"));
for (const forbidden of ["knowledge_compiler", "ingestion", "_promote", "write_plan"]) {
  if (webApiImports.some((line) => line.includes(forbidden))) {
    console.error(`AS-WEB-ACCEPT-001 smoke FAIL — web_api imports writer: ${forbidden}`);
    process.exit(1);
  }
}
if (!webApiImports.every((line) => line.includes("project_atlas.web_api"))) {
  console.error("AS-WEB-ACCEPT-001 smoke FAIL — web_api must only import web_api submodules");
  process.exit(1);
}

// UI invariant banners on production pages
const pageChecks = [
  ["src/pages/HomePage.tsx", ["ui_canonical", "graph_authority", "unknown"]],
  ["src/pages/production/ProjectsPage.tsx", ["UI", "canonical"]],
  ["src/pages/production/KnowledgePage.tsx", ["ui_canonical", "graph_authority", "unknown"]],
  ["src/pages/production/GraphPage.tsx", ["graph_authority", "ui_canonical", "unknown"]],
  ["src/pages/production/OpsHealthPage.tsx", ["ui_canonical", "graph_authority", "unknown", "Receipt evidence", "useOpsReceipts", "No PILOT estate rows", "WEB APPLICATION ACCEPTED = YES"]],
  ["src/pages/production/CommandCenterPage.tsx", ["ui_canonical", "graph_authority"]],
  ["src/pages/production/MissionControlPage.tsx", ["ui_canonical", "graph_authority", "unknown", "UI ≠ canonical", "Graph ≠ authority", "LensModeSwitcher", "LIVE", "DEMO", "FIXTURE"]],
  ["src/pages/production/WorkspacePage.tsx", ["ui_canonical", "graph_authority", "unknown", "UI ≠ canonical", "Graph ≠ authority", "LensModeSwitcher", "LIVE", "DEMO", "FIXTURE"]],
  ["src/pages/production/SourceHealthPage.tsx", ["ui_canonical", "SOURCE HEALTH != AUTHORITY", "UNKNOWN", "UNREADABLE", "?project=", "no implicit portfolio-all", "No secret echo"]],
  ["src/hooks/useLiveSourceHealth.ts", ["liveApiFetch", "/v1/source-health?project=", "health_state", "No implicit portfolio-all"]],
  ["src/pages/HomePage.tsx", ["/source-health", "Source health"]],
  ["src/components/LensModeSwitcher.tsx", ["LIVE", "DEMO", "FIXTURE", "mode-switcher"]],
  ["src/components/ReadStatusPanel.tsx", ["ui_canonical", "graph_authority", "unknown_equals_healthy"]],
  ["src/components/ProdShell.tsx", ["skip-link", "Skip to main"]],
  ["src/components/ProdNav.tsx", ["/mission-control", "Mission Control", "/workspace", "Workspace", "/source-health", "Source Health"]],
];
for (const [rel, needles] of pageChecks) {
  const body = readFileSync(join(root, rel), "utf8").toLowerCase();
  for (const needle of needles) {
    if (!body.includes(needle.toLowerCase())) {
      console.error(`AS-WEB-ACCEPT-001 smoke FAIL — ${rel} missing invariant: ${needle}`);
      process.exit(1);
    }
  }
}

const missionStub = JSON.parse(
  readFileSync(join(root, "public/sample-mission-control.json"), "utf8"),
);
if (
  missionStub.ui_canonical !== false ||
  missionStub.graph_authority !== false ||
  missionStub.unknown_equals_healthy !== false
) {
  console.error("AS-WEB-ACCEPT-001 smoke FAIL — mission stub must keep UI/graph/unknown non-authority");
  process.exit(1);
}
if (!Array.isArray(missionStub.pilot_estate_rows) || missionStub.pilot_estate_rows.length !== 0) {
  console.error("AS-WEB-ACCEPT-001 smoke FAIL — mission stub must not invent PILOT estate rows");
  process.exit(1);
}

const workspaceStub = JSON.parse(
  readFileSync(join(root, "public/sample-workspace.json"), "utf8"),
);
if (
  workspaceStub.ui_canonical !== false ||
  workspaceStub.graph_authority !== false ||
  workspaceStub.unknown_equals_healthy !== false
) {
  console.error("AS-WEB-ACCEPT-001 smoke FAIL — workspace stub must keep UI/graph/unknown non-authority");
  process.exit(1);
}
if (!Array.isArray(workspaceStub.pilot_estate_rows) || workspaceStub.pilot_estate_rows.length !== 0) {
  console.error("AS-WEB-ACCEPT-001 smoke FAIL — workspace stub must not invent PILOT estate rows");
  process.exit(1);
}

for (const fixtureRel of [
  "public/sample-mission-control.fixture.json",
  "public/sample-workspace.fixture.json",
]) {
  const fixture = JSON.parse(readFileSync(join(root, fixtureRel), "utf8"));
  if (fixture.data_source !== "fixture") {
    console.error(`AS-2.1-WEB-MISSION-WORKSPACE-UX smoke FAIL — ${fixtureRel} data_source must be fixture`);
    process.exit(1);
  }
  if (
    fixture.ui_canonical !== false ||
    fixture.graph_authority !== false ||
    fixture.unknown_equals_healthy !== false ||
    fixture.authentic_pilot !== false
  ) {
    console.error(`AS-2.1-WEB-MISSION-WORKSPACE-UX smoke FAIL — ${fixtureRel} must stay non-authority`);
    process.exit(1);
  }
  if (!Array.isArray(fixture.pilot_estate_rows) || fixture.pilot_estate_rows.length !== 0) {
    console.error(`AS-2.1-WEB-MISSION-WORKSPACE-UX smoke FAIL — ${fixtureRel} must not invent PILOT`);
    process.exit(1);
  }
}

const lensHook = readFileSync(
  join(root, "src/hooks/useLiveMissionWorkspace.ts"),
  "utf8",
);
for (const needle of ["LensModeId", "fixture", "demo", "live", "no silent invent"]) {
  if (!lensHook.includes(needle)) {
    console.error(`AS-2.1-WEB-MISSION-WORKSPACE-UX smoke FAIL — hook missing ${needle}`);
    process.exit(1);
  }
}


// E2E-003 / SEC-009: Web ↔ LIVE_API Bearer helper (no hardcoded tokens)
const liveApiPath = join(root, "src/api/liveApi.ts");
if (!existsSync(liveApiPath)) {
  console.error("E2E-003 smoke FAIL — missing src/api/liveApi.ts");
  process.exit(1);
}
const liveApi = readFileSync(liveApiPath, "utf8");
for (const needle of [
  "VITE_ATLAS_API_TOKEN",
  "Authorization",
  "Bearer",
  "liveApiFetch",
  "LiveApiAuthError",
  "Do not disable auth",
]) {
  if (!liveApi.includes(needle)) {
    console.error(`E2E-003 smoke FAIL — liveApi.ts missing ${needle}`);
    process.exit(1);
  }
}
if (/Bearer\s+[A-Za-z0-9_\-]{16,}/.test(liveApi)) {
  console.error("E2E-003 smoke FAIL — liveApi.ts must not hardcode Bearer secrets");
  process.exit(1);
}
for (const hookRel of [
  "src/hooks/useLiveGraph.ts",
  "src/hooks/useLiveKnowledge.ts",
  "src/hooks/useLiveMissionWorkspace.ts",
  "src/hooks/useOpsReceipts.ts",
  "src/hooks/useReadStatus.ts",
  "src/hooks/useLiveSourceHealth.ts",
]) {
  const body = readFileSync(join(root, hookRel), "utf8");
  if (!body.includes("liveApiFetch") && !body.includes('../api/liveApi')) {
    console.error(`E2E-003 smoke FAIL — ${hookRel} must use liveApi helper`);
    process.exit(1);
  }
  if (/Authorization:\s*`?Bearer\s+[A-Za-z0-9_\-]{16,}/.test(body)) {
    console.error(`E2E-003 smoke FAIL — ${hookRel} must not hardcode Bearer secrets`);
    process.exit(1);
  }
}
const startPs1 = readFileSync(
  join(repoRoot, "scripts", "windows", "atlas-start.ps1"),
  "utf8",
);
for (const needle of [
  "ATLAS_API_READ_TOKEN",
  "VITE_ATLAS_API_TOKEN",
  "Wait-AtlasApiReadToken",
]) {
  if (!startPs1.includes(needle)) {
    console.error(`E2E-003 smoke FAIL — atlas-start.ps1 missing ${needle}`);
    process.exit(1);
  }
}
if (startPs1.includes("VITE_ATLAS_API_PRIVILEGED") || startPs1.includes("PRIVILEGED_TOKEN=$")) {
  console.error("E2E-003 smoke FAIL — privileged token must not be placed in VITE_");
  process.exit(1);
}

// STYLE-001 (SM-SEPARATION / SM-TRUTH): selection state (.mode-*) must never
// be painted with the health "ok" green — LIVE may be selected while failed.
const stylesCss = readFileSync(join(root, "src/styles.css"), "utf8").replace(
  /\/\*[\s\S]*?\*\//g,
  "",
);
for (const [, selector, body] of stylesCss.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
  if (body.includes("var(--atlas-ok)") && /\.mode\b|\.mode-/.test(selector)) {
    console.error(
      `STYLE-001 smoke FAIL — selection selector uses --atlas-ok green: ${selector.trim()}`,
    );
    process.exit(1);
  }
}
const signalRack = readFileSync(
  join(root, "src/pages/design-lab/SignalRackPage.tsx"),
  "utf8",
);
if (/lamp-dot ok"[^>]*\/>\s*Projects/.test(signalRack)) {
  console.error("STYLE-001 smoke FAIL — 'listed' Projects lamp must not be lamp-dot ok");
  process.exit(1);
}

console.log(
  "AS-WEB-ACCEPT-004 + AS-2.1-WEB-MISSION-WORKSPACE-UX smoke PASS — LIVE/DEMO/FIXTURE modes visible (ACCEPTED=YES; no PILOT invent)",
);
