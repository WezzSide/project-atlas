import { describeTruthState, type TruthState } from "../lib/truthState";

interface TruthChipProps {
  state: TruthState;
  /** Optional context appended after the label, e.g. "2 competing sources". */
  detail?: string;
  /** Compact variant for table cells and narrow viewports. */
  compact?: boolean;
}

/**
 * Truth-state chip (AX-002).
 *
 * A chip is a *statement*, never a control — it renders as a span with no
 * interactive affordance. That matters for owner-gated states: an
 * OWNER REQUIRED chip must not look actionable, per
 * READ_ONLY_UI != EXECUTION_AUTHORITY.
 *
 * The glyph is aria-hidden because it is decorative; the visible label carries
 * the meaning for every reader. Colour is therefore never the sole carrier.
 */
export function TruthChip({ state, detail, compact = false }: TruthChipProps) {
  const { glyph, label, meaning } = describeTruthState(state);
  return (
    <span
      className={`truth-chip${compact ? " truth-chip-compact" : ""}`}
      data-truth-state={state}
      title={meaning}
    >
      <span className="truth-chip-glyph" aria-hidden="true">
        {glyph}
      </span>
      <span className="truth-chip-label">{label}</span>
      {detail ? <span className="truth-chip-detail">{detail}</span> : null}
    </span>
  );
}
