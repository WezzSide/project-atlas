/**
 * Truth-state announcement store (AX-003) — WCAG 2.2 SC 4.1.3 Status Messages.
 *
 * WHY A STORE RATHER THAN A RENDERED BANNER
 * -----------------------------------------
 * A live region must already exist in the DOM *before* its content is inserted;
 * adding aria-live at the same moment as the message does not reliably announce.
 * So `TruthAnnouncer` is mounted unconditionally by the shell and subscribes
 * here, while any component can call `announce()` on a state transition.
 *
 * SEVERITY IS A TRUTH-BOUNDARY DECISION, NOT A STYLE CHOICE
 * ---------------------------------------------------------
 * "assertive" is reserved for transitions that change what the data *means* —
 * above all a LIVE -> DEMO fallback, where a silent change would let a
 * screen-reader user read fixture data believing it is live. Routine progress
 * is "polite".
 */

export type AnnounceSeverity = "polite" | "assertive";

export interface Announcement {
  readonly message: string;
  readonly severity: AnnounceSeverity;
  /** Monotonic id so repeat announcements of identical text still fire. */
  readonly seq: number;
}

type Listener = (a: Announcement) => void;

const listeners = new Set<Listener>();
let seq = 0;

/** Announce a state transition to assistive technology. */
export function announce(message: string, severity: AnnounceSeverity = "polite"): void {
  const trimmed = message.trim();
  if (trimmed === "") {
    return;
  }
  seq += 1;
  const payload: Announcement = { message: trimmed, severity, seq };
  for (const listener of listeners) {
    listener(payload);
  }
}

/** Subscribe to announcements. Returns an unsubscribe function. */
export function subscribeAnnouncements(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * Standard announcement texts.
 *
 * Wording is deliberately explicit: research found no established accessible
 * pattern for communicating provenance or uncertainty, so there is no
 * convention a screen-reader user could be expected to already know. The text
 * therefore states the epistemic situation in full rather than gesturing at it.
 */
export const ANNOUNCEMENTS = {
  loadedLive: "Loaded. Reading LIVE vault.",
  loadedDemo: "Loaded. Showing DEMO FIXTURE data, not a live vault.",
  fellBackToDemo:
    "Now showing DEMO FIXTURE data — live vault unreachable. This is not live vault data.",
  readFailed: (reason: string) => `Read failed. ${reason}`,
  rollupUnknown: "Health rollup is UNKNOWN. Unknown is not healthy.",
  contested: (count: number) => `Sources disagree. ${count} competing sources.`,
  ownerRequired: "Owner decision required. This surface cannot act on it.",
} as const;
