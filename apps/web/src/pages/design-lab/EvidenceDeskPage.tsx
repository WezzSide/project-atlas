import { useState } from "react";
import { LabShell } from "../../components/LabShell";
import { TruthChip } from "../../components/TruthChip";
import { ClaimText, type ClaimSource } from "../../components/ClaimText";
import { type TruthState } from "../../lib/truthState";

/**
 * Design-lab prototype E — "Evidence Desk" (selected synthesis direction).
 *
 * Standing Watch's shell + Dossier's reading surface + Interrogation Room's
 * truth model. See docs/design/atlas-experience-system/04-DIRECTION-COMPARISON.md
 *
 * This is a *prototype*: it renders fixture rows to exercise the truth-state
 * language across every state, including the four the audit found missing from
 * the web layer entirely (OWNER_REQUIRED, RUNNING, READY, BLOCKED). It reads no
 * live vault and makes no acceptance claim. UI != canonical.
 */

interface QueueRow {
  readonly id: string;
  readonly title: string;
  readonly state: TruthState;
  readonly detail: string;
}

/** Fixture rows — explicitly not live vault data. */
const NEEDS_YOU: readonly QueueRow[] = [
  {
    id: "q1",
    title: "Deployment target for atlas-web",
    state: "contested",
    detail: "2 competing sources",
  },
  {
    id: "q2",
    title: "Promote AS-OPT-GATE-001 evaluation",
    state: "owner_required",
    detail: "wake gate CLOSED",
  },
  {
    id: "q3",
    title: "Retention policy for agent captures",
    state: "unknown",
    detail: "no traceable source",
  },
  {
    id: "q4",
    title: "Source scan for deps/vendor",
    state: "blocked",
    detail: "dependency unmet",
  },
];

const ACTIVITY: readonly QueueRow[] = [
  { id: "a1", title: "build-portfolio", state: "running", detail: "started 4m ago" },
  { id: "a2", title: "discover --source .", state: "ready", detail: "eligible, not merged" },
  { id: "a3", title: "validate --vault .", state: "failed", detail: "3 link errors" },
  { id: "a4", title: "index freshness", state: "stale", detail: "valid until 2026-08-30" },
];

/** A claim as the Dossier reading surface renders it: text with inline citation. */
interface Claim {
  readonly text: string;
  readonly state: TruthState;
  readonly sources: readonly ClaimSource[];
  readonly validUntil?: string;
}

const CLAIMS: readonly Claim[] = [
  {
    text: "The Core pipeline is discover → ingest → build-indexes → build-portfolio → validate.",
    state: "ok",
    sources: [{ label: "CLAUDE.md" }],
  },
  {
    text: "Atlas 2.2 capabilities are unlocked per-capability.",
    state: "ok",
    sources: [{ label: "docs/atlas-2.2/PACKAGE-MATURITY.json" }],
  },
  {
    // No source: ClaimText forces UNKNOWN regardless of any declared state.
    text: "The external security revalidation status is",
    state: "ok",
    sources: [],
  },
  {
    text: "The supported minimum Python version is",
    state: "contested",
    sources: [{ label: "pyproject.toml" }, { label: "docs/plan.md" }],
  },
  {
    text: "The lexical index was last rebuilt on 2026-08-14.",
    state: "stale",
    sources: [{ label: "generated/indexes/manifest.json" }],
    validUntil: "2026-08-30",
  },
];

export default function EvidenceDeskPage() {
  const [area, setArea] = useState<"home" | "project" | "activity">("home");

  return (
    <LabShell theme="signal-rack">
      <main className="shell" id="main">
        <header className="hero">
          <p className="eyebrow">Design lab · prototype E · selected direction</p>
          <h1>Evidence Desk</h1>
          <p className="lede">
            Standing Watch shell · Dossier reading surface · Interrogation Room truth model.
            Prototype only — fixture rows, no live vault read.
          </p>
          <p className="banner warn">
            <TruthChip state="fixture" /> prototype fixture data · UI ≠ canonical · not acceptance
          </p>
        </header>

        {/* Standing Watch: persistent area rail, five job-areas not fifteen lenses. */}
        <nav className="panel" aria-label="Evidence Desk areas">
          <div className="lens-switcher" role="group" aria-label="Area">
            {(
              [
                ["home", "Home — needs you"],
                ["project", "Project — knows"],
                ["activity", "Activity"],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => setArea(key)}
                aria-pressed={area === key}
              >
                {label}
              </button>
            ))}
          </div>
        </nav>

        {area === "home" ? (
          <section className="panel" aria-label="Needs you">
            <h2>Needs you</h2>
            <p className="disclaimer">
              Job J-8. Owner-gated rows are labelled, never actionable —
              READ_ONLY_UI ≠ EXECUTION_AUTHORITY.
            </p>
            <ul className="rows">
              {NEEDS_YOU.map((row) => (
                <li key={row.id}>
                  <TruthChip state={row.state} detail={row.detail} />{" "}
                  <span>{row.title}</span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {area === "project" ? (
          <section className="panel" aria-label="What Atlas knows">
            <h2>What Atlas knows</h2>
            <p className="disclaimer">
              Job J-1, Dossier surface. A claim with no source renders as UNKNOWN in the
              sentence where the fact belongs — it is never quietly omitted.
            </p>
            <div className="claims">
              {CLAIMS.map((claim) => (
                <ClaimText
                  key={claim.text}
                  text={claim.text}
                  state={claim.state}
                  sources={claim.sources}
                  validUntil={claim.validUntil}
                />
              ))}
            </div>
            <h3>Contested claim, shown without a winner</h3>
            <div className="truth-pair">
              <p>
                <TruthChip state="contested" compact /> Minimum Python is <strong>3.11</strong>
                {" — "}
                <cite>pyproject.toml</cite>
              </p>
              <p>
                <TruthChip state="contested" compact /> Minimum Python is <strong>3.12</strong>
                {" — "}
                <cite>docs/plan.md</cite>
              </p>
              <p className="disclaimer">
                Neither is styled as the winner. Resolution is <code>atlas review decide</code> —
                a human act.
              </p>
            </div>
          </section>
        ) : null}

        {area === "activity" ? (
          <section className="panel" aria-label="Activity">
            <h2>Activity</h2>
            <p className="disclaimer">
              Job J-7. Observation only — this surface never starts, stops or approves work.
            </p>
            <ul className="rows">
              {ACTIVITY.map((row) => (
                <li key={row.id}>
                  <TruthChip state={row.state} detail={row.detail} />{" "}
                  <span>{row.title}</span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        <section className="panel" aria-label="Truth-state vocabulary">
          <h2>Truth-state vocabulary</h2>
          <p className="disclaimer">
            All thirteen states. Each carries a glyph and a label, so the system stays
            readable with colour removed.
          </p>
          <div className="chip-grid">
            {(
              [
                "ok", "live", "demo", "fixture", "unknown", "unresolved", "contested",
                "stale", "blocked", "owner_required", "ready", "running", "failed",
              ] as const
            ).map((s) => (
              <TruthChip key={s} state={s} />
            ))}
          </div>
        </section>
      </main>
    </LabShell>
  );
}
