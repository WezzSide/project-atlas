import { TruthChip } from "./TruthChip";
import { truthStateFor, type TruthState } from "../lib/truthState";

export interface ClaimSource {
  /** Human-readable citation, e.g. "pyproject.toml" or "docs/plan.md". */
  readonly label: string;
  /** Optional deep link into the evidence drawer (AX-011). */
  readonly href?: string;
}

interface ClaimTextProps {
  /**
   * The claim as prose. For an UNKNOWN claim this should read as the *lead-in*
   * to the missing fact — "The supported minimum Python version is" — so the
   * UNKNOWN chip lands in the sentence where the fact belongs.
   */
  text: string;
  /** Declared state. Absent/unrecognised values normalise to UNKNOWN. */
  state?: TruthState | string | null;
  /** Sources backing the claim. Empty or absent forces UNKNOWN. */
  sources?: readonly ClaimSource[];
  /** For STALE claims, the end of the declared validity window. */
  validUntil?: string | null;
}

/**
 * A claim rendered as prose with its epistemic status inline (AX-012).
 *
 * THE LOAD-BEARING RULE
 * ---------------------
 * A claim with no source renders UNKNOWN *in the sentence where the fact
 * belongs*. It is never silently omitted, and it never renders as a bare
 * assertion. This is `no claim without a traceable source` expressed as a
 * component contract: the only way to show a sourceless value as fact would be
 * to not use this component.
 *
 * CONTESTED never collapses either — every competing source is rendered and
 * none is styled as the winner. Resolution is `atlas review decide`, a human
 * act, so this component points at it rather than performing it.
 *
 * UI != canonical. This renders a read projection.
 */
export function ClaimText({ text, state, sources, validUntil }: ClaimTextProps) {
  const cited = sources ?? [];
  // Absence of a source outranks a declared state: a claim asserted as "ok"
  // with nothing behind it is UNKNOWN, not ok.
  const resolved: TruthState = cited.length === 0 ? "unknown" : truthStateFor(state);
  const isContested = resolved === "contested";

  return (
    <p className="claim" data-truth-state={resolved}>
      <span className="claim-text">{text}</span>{" "}
      <TruthChip
        state={resolved}
        detail={
          resolved === "stale" && validUntil
            ? `valid until ${validUntil}`
            : isContested
              ? `${cited.length} competing sources`
              : undefined
        }
        compact
      />
      {cited.length > 0 ? (
        <span className="claim-sources">
          {cited.map((source, index) => (
            <span key={`${source.label}-${index}`}>
              {index > 0 ? " · " : " "}
              {source.href ? (
                <a href={source.href}>
                  <cite>{source.label}</cite>
                </a>
              ) : (
                <cite>{source.label}</cite>
              )}
            </span>
          ))}
        </span>
      ) : null}
      {isContested ? (
        <span className="claim-resolve">
          {" "}
          No winner is shown. Resolve with <code>atlas review decide</code>.
        </span>
      ) : null}
    </p>
  );
}
