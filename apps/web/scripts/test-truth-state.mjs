#!/usr/bin/env node
/**
 * AX-002 / AX-003 verification.
 *
 * Checks the truth-state system against its own spec rather than against a
 * snapshot, so it keeps holding as themes change:
 *
 *   1. Every declared TruthState has a glyph and a label (colour is never the
 *      sole carrier of meaning).
 *   2. Every state colour token meets WCAG 2.2 AA (4.5:1) against the real
 *      panel and paper backgrounds of the theme it belongs to.
 *   3. Absent evidence normalises to "unknown", never to a healthy state.
 *   4. The live regions exist unconditionally in the shell and carry the right
 *      role/aria-live pairing.
 *
 * Run: node scripts/test-truth-state.mjs
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");
const read = (p) => readFileSync(resolve(root, p), "utf8");

let failures = 0;
let checks = 0;

function check(name, condition, detail = "") {
  checks += 1;
  if (condition) {
    console.log(`  PASS  ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ""}`);
  }
}

/* ---------- contrast maths (WCAG 2.x relative luminance) ---------- */

function channel(c) {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

function luminance(hex) {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function contrast(a, b) {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/* ---------- 1. vocabulary completeness ---------- */

console.log("\nTruth-state vocabulary");

const truthSrc = read("src/lib/truthState.ts");
const declared = [
  ...truthSrc.matchAll(/^\s{2}(\w+):\s*\{\n\s*glyph:\s*"([^"]+)",\n\s*label:\s*"([^"]+)"/gm),
].map((m) => ({ state: m[1], glyph: m[2], label: m[3] }));

const EXPECTED = [
  "ok", "live", "demo", "fixture", "unknown", "unresolved", "contested",
  "stale", "blocked", "owner_required", "ready", "running", "failed",
];

check(
  `all ${EXPECTED.length} states declared`,
  declared.length === EXPECTED.length,
  `found ${declared.length}: ${declared.map((d) => d.state).join(", ")}`,
);

for (const state of EXPECTED) {
  const found = declared.find((d) => d.state === state);
  check(
    `${state} has glyph + label`,
    Boolean(found && found.glyph.length > 0 && found.label.length > 0),
    found ? "" : "state missing",
  );
}

// The states the audit measured at 0 occurrences must now exist.
for (const state of ["owner_required", "running", "ready", "blocked"]) {
  check(
    `${state} exists (audit A-4 gap closed)`,
    declared.some((d) => d.state === state),
  );
}

/* ---------- 2. contrast ---------- */

console.log("\nContrast (WCAG 2.2 AA, 4.5:1 normal text)");

const tokensSrc = read("src/tokens.css");

/**
 * Extract literal --truth-* colour declarations from the CSS block whose
 * selector matches `selectorRe`.
 *
 * Blocks are matched by selector and then filtered to those that actually
 * declare truth colours, rather than by file position: tokens.css has several
 * :root blocks and more may be appended, so a positional lookup would silently
 * find the wrong one and under-report the check count.
 */
function truthTokensFor(selectorRe) {
  const out = {};
  for (const block of tokensSrc.matchAll(/([^{}]+)\{([^}]*)\}/g)) {
    // The capture runs back to the previous "}", so it can include a preceding
    // comment. The selector is the last non-empty line of it.
    const selector = block[1]
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line !== "")
      .pop();
    if (!selector || !selectorRe.test(selector)) continue;
    for (const m of block[2].matchAll(/--(truth-[\w-]+):\s*(#[0-9a-fA-F]{6})/g)) {
      out[m[1]] = m[2];
    }
  }
  return out;
}

const lightPairs = truthTokensFor(/^:root$/);
const darkPairs = truthTokensFor(/data-theme="(signal-rack|terminal-honest)"/);

const LIGHT_BG = { panel: "#fffdf8", paper: "#f5f0e8" };
const DARK_BG = { panel: "#1c1917", paper: "#0c0a09" };

check("light truth tokens found", Object.keys(lightPairs).length >= 13,
  `found ${Object.keys(lightPairs).length}`);
check("dark truth tokens found", Object.keys(darkPairs).length >= 13,
  `found ${Object.keys(darkPairs).length}`);

let worst = { ratio: Infinity, name: "" };

for (const [set, pairs, bgs] of [
  ["light", lightPairs, LIGHT_BG],
  ["dark", darkPairs, DARK_BG],
]) {
  for (const [token, hex] of Object.entries(pairs)) {
    for (const [bgName, bg] of Object.entries(bgs)) {
      const r = contrast(hex, bg);
      if (r < worst.ratio) worst = { ratio: r, name: `${set}/${token}/${bgName}` };
      check(
        `${set} ${token} on ${bgName} (${r.toFixed(2)}:1)`,
        r >= 4.5,
        `${hex} vs ${bg} is below AA 4.5:1`,
      );
    }
  }
}

console.log(`  ---> lowest ratio: ${worst.ratio.toFixed(2)}:1 (${worst.name})`);

/* ---------- 3. unknown != healthy ---------- */

console.log("\nInvariant: unknown != healthy");

check(
  "truthStateFor() returns unknown for non-strings",
  /if \(typeof value !== "string"\)\s*\{\s*return "unknown";/.test(truthSrc),
);
check(
  "truthStateFor() returns unknown for empty strings",
  /if \(normalised === ""\)\s*\{\s*return "unknown";/.test(truthSrc),
);
check(
  "truthStateFor() returns unknown for unrecognised values",
  /KNOWN\.has\(normalised\) \? \(normalised as TruthState\) : "unknown"/.test(truthSrc),
);
check(
  "readPlaneState() only returns live for an explicit live_api source",
  /dataSource === "live_api" \? "live" : "demo"/.test(truthSrc),
);
check(
  "owner_required is the only owner-gated state",
  (truthSrc.match(/ownerGated: true/g) || []).length === 1,
);

/* ---------- 4. live regions ---------- */

console.log("\nWCAG 2.2 SC 4.1.3 status messages");

const announcerSrc = read("src/components/TruthAnnouncer.tsx");
const shellSrc = read("src/components/ProdShell.tsx");

check('polite region has role="status"', /role="status"/.test(announcerSrc));
check('polite region has aria-live="polite"', /aria-live="polite"/.test(announcerSrc));
check('assertive region has role="alert"', /role="alert"/.test(announcerSrc));
check('assertive region has aria-live="assertive"', /aria-live="assertive"/.test(announcerSrc));
check("both regions are aria-atomic", (announcerSrc.match(/aria-atomic="true"/g) || []).length === 2);
check("announcer is mounted by the shell", /<TruthAnnouncer \/>/.test(shellSrc));
check(
  "announcer is mounted unconditionally (no conditional render)",
  !/\{[^}]*&&\s*<TruthAnnouncer/.test(shellSrc) && !/\?\s*<TruthAnnouncer/.test(shellSrc),
);

const stylesSrc = read("src/styles.css");
check(
  ".sr-only does not use display:none (would silence live regions)",
  /\.sr-only\s*\{[^}]*\}/.test(stylesSrc) &&
    !/\.sr-only\s*\{[^}]*display:\s*none/.test(stylesSrc),
);

const hookSrc = read("src/hooks/useReadStatus.ts");
check(
  "LIVE -> DEMO fallback is announced assertively",
  /announce\(ANNOUNCEMENTS\.fellBackToDemo, "assertive"\)/.test(hookSrc),
);
check(
  "read failure is announced assertively",
  /announce\(ANNOUNCEMENTS\.readFailed\(reason\), "assertive"\)/.test(hookSrc),
);
check(
  "routine load is announced politely",
  /announce\(ANNOUNCEMENTS\.loadedLive, "polite"\)/.test(hookSrc),
);

/* ---------- 5. chip is a statement, not a control ---------- */

console.log("\nOwner-gate boundary");

const chipSrc = read("src/components/TruthChip.tsx");
check(
  "TruthChip renders a span, not a button/anchor",
  /<span\s/.test(chipSrc) && !/<button/.test(chipSrc) && !/<a\s/.test(chipSrc),
);
check("TruthChip has no onClick handler", !/onClick/.test(chipSrc));
check("TruthChip glyph is aria-hidden (label carries meaning)", /aria-hidden="true"/.test(chipSrc));

/* ---------- summary ---------- */

console.log(`\n${checks - failures}/${checks} checks passed`);
if (failures > 0) {
  console.error(`FAILED: ${failures} check(s)`);
  process.exit(1);
}
console.log("AX-002 / AX-003 verification PASS");
