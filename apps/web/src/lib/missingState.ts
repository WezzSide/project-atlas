/**
 * STYLE-002 (SM-TRUTH) — missing ≠ zero / clean.
 *
 * Shared primitives so a lens never renders an absent, failed or unread source
 * as `0`, `false`, "none" or another reassuring empty state. Pure functions:
 * they only classify what the caller already holds and never invent
 * freshness, health or counts the backend did not provide.
 */

export const UNKNOWN_TEXT = "unknown";

/** A real finite number renders as itself; anything else is `unknown`, never 0. */
export function countOrUnknown(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? String(value)
    : UNKNOWN_TEXT;
}

/** Length of a real array; a missing / non-array value is `unknown`, never 0. */
export function lengthOrUnknown(value: unknown): string {
  return Array.isArray(value) ? String(value.length) : UNKNOWN_TEXT;
}

/** A real boolean renders as itself; a missing value is `unknown`, never false. */
export function boolOrUnknown(value: unknown): string {
  return typeof value === "boolean" ? String(value) : UNKNOWN_TEXT;
}

/**
 * Why a list has nothing to show. Only `empty` is a genuine empty result.
 * - loading: the read is still in flight
 * - failed: the read errored (HTTP / network / parse)
 * - unavailable: no payload and no error (not requested, demo-isolated, unread)
 * - missing: payload loaded but the field is absent / not a list
 * - empty: payload loaded and the source reported a real empty list
 * - present: at least one row
 */
export type ListState =
  | "loading"
  | "failed"
  | "unavailable"
  | "missing"
  | "empty"
  | "present";

export function listState(input: {
  loading?: boolean;
  error?: string | null;
  /** True only when the parent payload was actually loaded. */
  loaded: boolean;
  list: unknown;
}): ListState {
  if (input.loading) {
    return "loading";
  }
  if (!input.loaded) {
    return input.error ? "failed" : "unavailable";
  }
  if (!Array.isArray(input.list)) {
    return "missing";
  }
  return input.list.length === 0 ? "empty" : "present";
}

/**
 * Operator copy for every non-genuine-empty state. Returns null for
 * `empty` / `present`, where the caller renders its own truthful content.
 */
export function missingListText(state: ListState, subject: string): string | null {
  switch (state) {
    case "loading":
      return `Loading ${subject}…`;
    case "failed":
      return `UNAVAILABLE — ${subject} could not be read (source failed); not an empty result`;
    case "unavailable":
      return `UNKNOWN — ${subject} not loaded (no source read); not an empty result`;
    case "missing":
      return `UNKNOWN — ${subject} not provided by the source; not an empty result`;
    default:
      return null;
  }
}
