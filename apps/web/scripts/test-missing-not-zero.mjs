#!/usr/bin/env node
/**
 * STYLE-002 (SM-TRUTH) gates — missing ≠ zero / clean.
 * 1. Runtime tests of the shared helper (type-stripped import, no network).
 * 2. Source gates per audited site: the false-reassurance pattern is gone and
 *    the truthful UNKNOWN / unavailable branch is present.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  boolOrUnknown,
  countOrUnknown,
  lengthOrUnknown,
  listState,
  missingListText,
} from "../src/lib/missingState.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (rel) => readFileSync(join(root, rel), "utf8");
const flat = (text) => text.replace(/\s+/g, " ");

// --- 1. helper runtime -----------------------------------------------------
assert.equal(countOrUnknown(0), "0", "a real zero stays 0");
assert.equal(countOrUnknown(7), "7");
for (const missing of [undefined, null, "3", NaN, Infinity, {}]) {
  assert.equal(countOrUnknown(missing), "unknown", `count ${String(missing)}`);
}
assert.equal(lengthOrUnknown([]), "0", "a real empty list stays 0");
assert.equal(lengthOrUnknown([1, 2]), "2");
assert.equal(lengthOrUnknown(undefined), "unknown");
assert.equal(lengthOrUnknown(null), "unknown");
assert.equal(boolOrUnknown(false), "false", "a real false stays false");
assert.equal(boolOrUnknown(true), "true");
assert.equal(boolOrUnknown(undefined), "unknown");

assert.equal(listState({ loading: true, loaded: false, list: undefined }), "loading");
assert.equal(listState({ error: "HTTP 500", loaded: false, list: undefined }), "failed");
assert.equal(listState({ error: null, loaded: false, list: undefined }), "unavailable");
assert.equal(listState({ loaded: true, list: undefined }), "missing");
assert.equal(listState({ loaded: true, list: [] }), "empty");
assert.equal(listState({ loaded: true, list: ["x"] }), "present");
// A stale list must not outrank a not-loaded parent.
assert.equal(listState({ loaded: false, list: [] }), "unavailable");

assert.equal(missingListText("empty", "blockers"), null);
assert.equal(missingListText("present", "blockers"), null);
assert.match(missingListText("failed", "blockers"), /^UNAVAILABLE — blockers/);
assert.match(missingListText("unavailable", "blockers"), /^UNKNOWN — blockers/);
assert.match(missingListText("missing", "blockers"), /^UNKNOWN — blockers/);
assert.match(missingListText("loading", "blockers"), /^Loading blockers/);
for (const state of ["failed", "unavailable", "missing"]) {
  assert.match(missingListText(state, "x"), /not an empty result/);
}

// --- 2. source gates per audited site -------------------------------------
const knowledge = read("src/pages/production/KnowledgePage.tsx");
assert.doesNotMatch(knowledge, /_count \?\? 0/, "Knowledge: truth counts must not default to 0");
assert.doesNotMatch(knowledge, /truth\?\.evidence \?\? \[\]\)\.length\}/, "Knowledge: evidence chip");
for (const needle of [
  "countOrUnknown(truth?.pending_review_count)",
  "countOrUnknown(truth?.conflict_count)",
  "countOrUnknown(truth?.human_decision_count)",
  "lengthOrUnknown(truth?.evidence)",
  "{pendingMissing ? (",
  "{conflictsMissing ? (",
  "loaded: Boolean(truth)",
]) {
  assert.ok(knowledge.includes(needle), `Knowledge missing: ${needle}`);
}
assert.ok(
  flat(knowledge).includes("truth panel not provided by this brief"),
  "Knowledge: missing truth panel banner",
);

const roadmap = read("src/pages/production/RoadmapPage.tsx");
for (const needle of [
  "loaded: Boolean(roadmap)",
  "{pathMissing ? (",
  "{blockersMissing ? (",
  "{unknownsMissing ? (",
]) {
  assert.ok(roadmap.includes(needle), `Roadmap missing: ${needle}`);
}
// The reassuring copy may only follow the missing-state guard.
for (const [guard, copy] of [
  ["{blockersMissing ? (", "No derived blockers on this lens."],
  ["{unknownsMissing ? (", "No UNKNOWN signals on this derived lens."],
  ["{pathMissing ? (", "no remaining-work path"],
]) {
  assert.ok(
    roadmap.indexOf(guard) !== -1 && roadmap.indexOf(guard) < roadmap.indexOf(copy),
    `Roadmap: "${copy}" must be guarded by ${guard}`,
  );
}

const ops = read("src/pages/production/OpsHealthPage.tsx");
const opsFlat = flat(ops);
assert.ok(opsFlat.includes("receiptError || !inventory ? ("), "Ops: failed branch");
assert.ok(
  opsFlat.includes("receipt inventory could not be read; whether ops receipts exist is unknown"),
  "Ops: fetch failure must not claim an empty disk",
);
assert.ok(opsFlat.includes('inventory.ops_root === "unknown"'), "Ops: ops_root unknown branch");
assert.ok(
  opsFlat.indexOf("receiptError || !inventory ? (") <
    opsFlat.indexOf("no ops receipts on disk"),
  "Ops: 'no ops receipts on disk' only after failed/demo/unknown branches",
);
assert.equal(
  opsFlat.split("no ops receipts on disk").length - 1,
  1,
  "Ops: empty-disk claim appears exactly once (LIVE success branch)",
);
assert.doesNotMatch(ops, /completion_claimed \?\? false/, "Ops: forced constant must be labelled");
assert.ok(opsFlat.includes("completion_claimed=false (UI policy"), "Ops: UI policy label");
assert.ok(read("src/hooks/useOpsReceipts.ts").includes("ops_root?: string"));

const panel = read("src/components/ReadStatusPanel.tsx");
assert.doesNotMatch(
  panel,
  /data_source \?\?[^;]*"live_api"/,
  "ReadStatusPanel: missing data_source must not default to live_api",
);
for (const needle of [
  "DATA SOURCE UNKNOWN",
  "data source unknown",
  'source === "live_api"',
  "FIXTURE — deterministic sample",
  '{source ?? "unknown"}',
  "aria-label={`Vault read status (${sourceLabel})`}",
]) {
  assert.ok(panel.includes(needle), `ReadStatusPanel missing: ${needle}`);
}

const discovery = read("src/pages/production/DiscoveryPage.tsx");
assert.doesNotMatch(discovery, /counts\?\.\w+ \?\? 0/, "Discovery: counts must not default to 0");
for (const key of ["projects", "knowledge", "required_review", "connected"]) {
  assert.ok(
    discovery.includes(`countOrUnknown(view.counts?.${key})`),
    `Discovery missing countOrUnknown for ${key}`,
  );
}

const lensHook = read("src/hooks/useLiveMissionWorkspace.ts");
assert.ok(lensHook.includes("export const PILOT_UI_POLICY_NOTE"), "hook: UI policy note");
assert.ok(lensHook.includes("UI policy"), "hook: UI policy wording");
for (const [rel, field] of [
  ["src/pages/production/MissionControlPage.tsx", "mission_board_available"],
  ["src/pages/production/WorkspacePage.tsx", "workspace_board_available"],
]) {
  const page = read(rel);
  assert.doesNotMatch(page, /_available \?\? false/, `${rel}: board availability default`);
  assert.doesNotMatch(page, /pilot_estate_rows\.length : 0/, `${rel}: PILOT rows default`);
  assert.doesNotMatch(page, /authentic_pilot \?\? false/, `${rel}: authentic_pilot default`);
  assert.ok(page.includes(`boolOrUnknown(view.${field})`), `${rel}: boolOrUnknown`);
  assert.ok(page.includes("lengthOrUnknown(view.pilot_estate_rows)"), `${rel}: lengthOrUnknown`);
  assert.equal(
    page.split("({PILOT_UI_POLICY_NOTE})").length - 1,
    2,
    `${rel}: both forced constants labelled as UI policy`,
  );
}

console.log("STYLE-002 missing-not-zero gates PASS");
