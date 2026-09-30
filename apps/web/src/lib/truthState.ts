/**
 * Cross-surface truth-state vocabulary (AX-002).
 *
 * One vocabulary for web / CLI / TUI. See
 * docs/design/atlas-experience-system/05-TRUTH-STATE-LANGUAGE.md
 *
 * Invariants this module makes structural rather than advisory:
 *   - unknown != healthy — absent evidence resolves to "unknown", never "ok".
 *   - Every state carries a glyph AND a label, so colour is never the sole carrier.
 *   - owner_required is a label on a fact, never an actionable control.
 *
 * UI != canonical. These are presentation states over read-only projections.
 */

export type TruthState =
  | "ok"
  | "live"
  | "demo"
  | "fixture"
  | "unknown"
  | "unresolved"
  | "contested"
  | "stale"
  | "blocked"
  | "owner_required"
  | "ready"
  | "running"
  | "failed";

export interface TruthStateDescriptor {
  /** Decorative mark; always paired with `label`, never used alone. */
  readonly glyph: string;
  /** Short uppercase label carrying the meaning for sighted and AT users alike. */
  readonly label: string;
  /** Long-form meaning, used for `title` and CLI help. */
  readonly meaning: string;
  /** True when only the owner may decide — must never gain an interactive control. */
  readonly ownerGated: boolean;
}

export const TRUTH_STATES: Readonly<Record<TruthState, TruthStateDescriptor>> = {
  ok: {
    glyph: "●",
    label: "OK",
    meaning: "Evidence present, single authority, within declared validity.",
    ownerGated: false,
  },
  live: {
    glyph: "◆",
    label: "LIVE",
    meaning: "Read from LIVE_API against a real vault.",
    ownerGated: false,
  },
  demo: {
    glyph: "◇",
    label: "DEMO FIXTURE",
    meaning: "Isolated sample data. Not a live vault, not acceptance evidence.",
    ownerGated: false,
  },
  fixture: {
    glyph: "◇",
    label: "FIXTURE",
    meaning: "Deterministic test data. Fixture is not authentic pilot data.",
    ownerGated: false,
  },
  unknown: {
    glyph: "?",
    label: "UNKNOWN",
    meaning: "No traceable source. Not an error, and not healthy.",
    ownerGated: false,
  },
  unresolved: {
    glyph: "~",
    label: "UNRESOLVED",
    meaning: "Question is open; resolution is possible.",
    ownerGated: false,
  },
  contested: {
    glyph: "⇄",
    label: "CONTESTED",
    meaning: "Sources disagree. No display winner is chosen.",
    ownerGated: false,
  },
  stale: {
    glyph: "⧗",
    label: "STALE",
    meaning: "Outside its declared valid-time window.",
    ownerGated: false,
  },
  blocked: {
    glyph: "⊘",
    label: "BLOCKED",
    meaning: "Cannot proceed; a dependency is unmet.",
    ownerGated: false,
  },
  owner_required: {
    glyph: "⚿",
    label: "OWNER REQUIRED",
    meaning: "Only the owner may decide. Never actionable from a read-only surface.",
    ownerGated: true,
  },
  ready: {
    glyph: "▷",
    label: "READY",
    meaning: "Eligible to proceed. Promote-eligible is not merged or authoritative.",
    ownerGated: false,
  },
  running: {
    glyph: "◌",
    label: "RUNNING",
    meaning: "Work is in progress.",
    ownerGated: false,
  },
  failed: {
    glyph: "✕",
    label: "FAILED",
    meaning: "Attempted and failed.",
    ownerGated: false,
  },
} as const;

const KNOWN = new Set<string>(Object.keys(TRUTH_STATES));

/**
 * Normalise an arbitrary value into a TruthState.
 *
 * Absent evidence — null, undefined, empty/whitespace strings — resolves to
 * "unknown". This is the enforcement point for `unknown != healthy`: rendering
 * missing evidence as healthy requires deliberately bypassing this helper.
 * Casing and hyphen/space separators are tolerated because the existing codebase
 * carries three casings (see audit finding A-4).
 */
export function truthStateFor(value: unknown): TruthState {
  if (typeof value !== "string") {
    return "unknown";
  }
  const normalised = value.trim().toLowerCase().replace(/[\s-]+/g, "_");
  if (normalised === "") {
    return "unknown";
  }
  return KNOWN.has(normalised) ? (normalised as TruthState) : "unknown";
}

/** Descriptor lookup that never throws — unrecognised input degrades to UNKNOWN. */
export function describeTruthState(state: TruthState): TruthStateDescriptor {
  return TRUTH_STATES[state] ?? TRUTH_STATES.unknown;
}

/** True when a state may only be decided by the owner. */
export function isOwnerGated(state: TruthState): boolean {
  return describeTruthState(state).ownerGated;
}

/**
 * Read-plane state from a data-source string.
 * Anything not positively identified as the live API is treated as a demo
 * fixture, so an unrecognised plane can never read as LIVE.
 */
export function readPlaneState(dataSource: string | null | undefined): TruthState {
  return dataSource === "live_api" ? "live" : "demo";
}

/** Screen-reader/CLI text for a state, e.g. "UNKNOWN — No traceable source…". */
export function truthStateText(state: TruthState): string {
  const d = describeTruthState(state);
  return `${d.label} — ${d.meaning}`;
}
