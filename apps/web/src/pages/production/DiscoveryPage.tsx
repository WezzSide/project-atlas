import { ProdShell } from "../../components/ProdShell";
import {
  type DiscoveryCategoryKey,
  useEstateDiscovery,
} from "../../hooks/useEstateDiscovery";
import { countOrUnknown } from "../../lib/missingState";

const CARE_ORDER: DiscoveryCategoryKey[] = [
  "DISCOVERED_PROJECTS",
  "AMBIGUOUS_MATCHES",
  "NEW_KNOWLEDGE",
  "UNMATCHED_KNOWLEDGE",
  "CONNECTED",
  "IGNORED",
];

const LABELS: Record<DiscoveryCategoryKey, string> = {
  DISCOVERED_PROJECTS: "Discovered projects",
  NEW_KNOWLEDGE: "New knowledge",
  AMBIGUOUS_MATCHES: "Ambiguous matches",
  UNMATCHED_KNOWLEDGE: "Unmatched knowledge",
  IGNORED: "Ignored (policy / safety)",
  CONNECTED: "Connected",
};

/** Production discovery lens — categorized estate findings; never a raw FS browser. */
export default function DiscoveryPage() {
  const { view, error, loading, dataSource } = useEstateDiscovery();
  const isDemo = dataSource === "demo_stub" || view?.demo_isolated === true;
  const categories = view?.categories;
  const scanIncomplete = view?.present && view.scan?.scan_complete === false;

  return (
    <ProdShell>
      <main className="shell" id="main" tabIndex={-1}>
        <header className="hero">
          <p className="eyebrow">Production · Discovery</p>
          <h1>Knowledge estate</h1>
          <p className="lede">
            {view?.primary_question ??
              "What did Atlas find that I should care about?"}{" "}
            Discovery is not ingest, trust, or authority.
          </p>
        </header>

        {error ? <p className="banner warn">Discovery unavailable: {error}</p> : null}
        {loading ? <p className="banner">Loading…</p> : null}
        {isDemo ? (
          <p className="banner warn">DEMO STUB — isolated · not live vault</p>
        ) : dataSource === "live_api" ? (
          <p className="banner">LIVE_API — read-only discovery projection</p>
        ) : null}

        {view && !view.present ? (
          <p className="banner warn">
            {view.note ??
              "No estate discovery report yet. Run atlas discover --root <path> --vault <vault>."}
          </p>
        ) : null}

        {view?.present && view.volume_root_authorized ? (
          <p className="banner warn">
            Owner-authorized Windows volume root — not an ordinary
            bounded-directory scan ({view.volume_root_kind ?? "NON_SYSTEM_WINDOWS_VOLUME"}
            ).
          </p>
        ) : null}

        {scanIncomplete ? (
          <p className="banner warn">
            SCAN INCOMPLETE
            {view?.scan?.depth_limit_reached
              ? ` — Depth limit reached (max_depth=${String(view.scan.max_depth ?? "?")}). Some files/directories were not inspected.`
              : view?.scan?.truncation_reason
                ? `: ${view.scan.truncation_reason}`
                : ""}{" "}
            Results are not a complete estate inventory.
          </p>
        ) : null}

        {view?.present ? (
          <section className="panel" aria-label="Discovery summary">
            <h2>Summary</h2>
            <p>
              Root: {view.authorized_root ?? "unknown"} · mode{" "}
              {view.authorized_root_mode ?? "BOUNDED_DIRECTORY"} · volume{" "}
              {view.volume_root_authorized
                ? `authorized (${view.volume_root_kind ?? "NON_SYSTEM_WINDOWS_VOLUME"})`
                : "not authorized"}{" "}
              · projects {countOrUnknown(view.counts?.projects)} · knowledge{" "}
              {countOrUnknown(view.counts?.knowledge)} · review{" "}
              {countOrUnknown(view.counts?.required_review)} · connected{" "}
              {countOrUnknown(view.counts?.connected)}
              {view.scan?.project_limit_reached
                ? ` · project output ${String(view.scan.project_candidates_emitted ?? "?")}/${String(view.scan.project_candidates_seen ?? "?")} enriched ${String(view.scan.project_candidates_enriched ?? "?")} (${view.scan.candidate_selection_policy ?? "bounded"})`
                : ""}
            </p>
            <p className="disclaimer">
              DISCOVER ≠ INGEST ≠ TRUST ≠ AUTHORITY · UI ≠ canonical · LIKELY ≠
              CONNECTED
            </p>
          </section>
        ) : null}

        {CARE_ORDER.map((key) => {
          const rows = categories?.[key] ?? [];
          if (!view?.present && rows.length === 0) return null;
          if (key === "IGNORED" && rows.length === 0) return null;
          const shown =
            key === "IGNORED" ? rows.slice(0, 12) : rows.slice(0, 40);
          return (
            <section className="panel" aria-label={LABELS[key]} key={key}>
              <h2>
                {LABELS[key]} ({rows.length})
              </h2>
              {shown.length === 0 ? (
                <p className="banner">none</p>
              ) : (
                <ul className="theme-hub">
                  {shown.map((row) => (
                    <li
                      key={
                        row.candidate_id ??
                        row.path ??
                        `${key}-${row.display_name ?? "row"}`
                      }
                    >
                      <strong>
                        {row.display_name ?? row.candidate_id ?? "unnamed"}
                      </strong>
                      <span>
                        [{row.match_state ?? "—"}] {row.path ?? "path unknown"}
                        {row.required_review ? " · review required" : ""}
                      </span>
                      {row.why_matched?.[0] ? (
                        <span>why: {row.why_matched[0]}</span>
                      ) : null}
                      {row.why_connected?.[0] ? (
                        <span>connected: {row.why_connected[0]}</span>
                      ) : null}
                      {row.conflicting_evidence?.[0]?.detail ? (
                        <span>
                          conflict: {row.conflicting_evidence[0].kind}:{" "}
                          {row.conflicting_evidence[0].detail}
                        </span>
                      ) : null}
                      {row.required_action ? (
                        <span>action: {row.required_action}</span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
              {key === "IGNORED" && rows.length > shown.length ? (
                <p className="disclaimer">
                  Showing {shown.length} of {rows.length} ignored paths
                </p>
              ) : null}
            </section>
          );
        })}
      </main>
    </ProdShell>
  );
}
