import type { ReadStatus } from "../types";

interface ReadStatusPanelProps {
  status: ReadStatus;
  compact?: boolean;
}

/** Shared read-status fields — LIVE labelled; demo stub isolated; never canonical. */
export function ReadStatusPanel({ status, compact = false }: ReadStatusPanelProps) {
  // STYLE-002: a missing data_source is UNKNOWN provenance — never defaulted to LIVE_API.
  const source = status.data_source ?? (status.read_plane === "stub" ? "demo_stub" : null);
  const isDemo = source === "demo_stub" || status.demo_isolated === true;
  const isFixture =
    !isDemo && (source === "fixture" || status.fixture_isolated === true);
  const isLive = !isDemo && !isFixture && source === "live_api";
  const sourceLabel = isDemo
    ? "demo stub"
    : isFixture
      ? "fixture"
      : isLive
        ? "live API"
        : "data source unknown";
  return (
    <section
      className="panel"
      aria-label={`Vault read status (${sourceLabel})`}
    >
      <h2>Vault read status</h2>
      <p className={isLive ? "banner" : "banner warn"}>
        {isDemo
          ? "DEMO STUB — isolated sample data · not live vault · not acceptance"
          : isFixture
            ? "FIXTURE — deterministic sample · not live vault · not acceptance"
            : isLive
              ? "LIVE_API — read-only vault projection · UI ≠ canonical"
              : "DATA SOURCE UNKNOWN — provenance not provided · not labelled LIVE · UI ≠ canonical"}
      </p>
      <p className="disclaimer">
        UI ≠ canonical · Graph ≠ authority · Unknown ≠ healthy
        {isDemo ? " · demo isolated from LIVE_API" : ""}
      </p>
      <dl className="grid">
        <div>
          <dt>Data source</dt>
          <dd>{source ?? "unknown"}</dd>
        </div>
        <div>
          <dt>Vault</dt>
          <dd>{status.vault_present ? status.vault_id ?? "present" : "absent"}</dd>
        </div>
        <div>
          <dt>Read plane</dt>
          <dd>{status.read_plane}</dd>
        </div>
        <div>
          <dt>Health rollup</dt>
          <dd className={status.health.rollup === "unknown" ? "rollup-unknown" : undefined}>
            {status.health.rollup}
          </dd>
        </div>
        <div>
          <dt>Health source</dt>
          <dd>{status.health.source}</dd>
        </div>
      </dl>
      {!compact ? (
        <>
          <p className="disclaimer">{status.health.disclaimer}</p>
          <p className="flags">
            ui_canonical={String(status.ui_canonical)} · graph_authority=
            {String(status.graph_authority)} · unknown_equals_healthy=
            {String(status.unknown_equals_healthy)} · demo_isolated=
            {String(status.demo_isolated ?? isDemo)}
          </p>
          <h3>Projects (read-only)</h3>
          {status.projects.length === 0 ? (
            <p className="empty">No projects listed (honest empty).</p>
          ) : (
            <ul>
              {status.projects.map((project) => (
                <li key={project.project_id}>
                  <code>{project.project_id}</code>
                  {project.has_project_note ? " · project.md" : " · no project.md"}
                </li>
              ))}
            </ul>
          )}
        </>
      ) : null}
    </section>
  );
}
