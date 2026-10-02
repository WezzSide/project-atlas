import { LabShell } from "../../components/LabShell";
import { useReadStatus } from "../../hooks/useReadStatus";

/** Theme B — Signal Rack (design-lab prototype). */
export default function SignalRackPage() {
  const { status, error, loading } = useReadStatus();
  const rollup = status?.health.rollup ?? "unknown";
  const lampClass =
    rollup === "healthy" ? "ok" : rollup === "unknown" ? "" : "warn";

  return (
    <LabShell theme="signal-rack" className="theme-signal">
      <main className="shell">
        <header className="hero">
          <p className="eyebrow">Design lab · Theme B</p>
          <h1>Signal Rack</h1>
          <p className="lede">
            Dark charcoal rails and teal signal lamps. Unknown lamp stays amber
            void — never green. Derived ops only; not authority.
          </p>
        </header>

        {error ? <p className="banner warn">Read status unavailable: {error}</p> : null}
        {loading ? <p className="banner">Loading read status…</p> : null}

        {status ? (
          <div className="rack" role="group" aria-label="Signal rack">
            <div className="lamp">
              <div>
                <span className={`lamp-dot ${lampClass}`} aria-hidden />
                Health
              </div>
              <p className="mono" style={{ margin: "0.5rem 0 0" }}>
                {rollup}
              </p>
              <p className="disclaimer" style={{ marginBottom: 0 }}>
                {status.health.source}
              </p>
            </div>
            <div className="lamp">
              <div>
                <span className="lamp-dot neutral" aria-hidden />
                Projects
              </div>
              <p className="mono" style={{ margin: "0.5rem 0 0" }}>
                {status.projects.length} listed (read-only)
              </p>
              <p className="disclaimer" style={{ marginBottom: 0 }}>
                Sample inventory — not Layer B.
              </p>
            </div>
            <div className="lamp">
              <div>
                <span className="lamp-dot" aria-hidden />
                Read plane
              </div>
              <p className="mono" style={{ margin: "0.5rem 0 0" }}>
                {status.read_plane}
              </p>
              <p className="flags" style={{ marginBottom: 0 }}>
                ui_canonical={String(status.ui_canonical)} · graph_authority=
                {String(status.graph_authority)}
              </p>
            </div>
          </div>
        ) : null}
      </main>
    </LabShell>
  );
}
