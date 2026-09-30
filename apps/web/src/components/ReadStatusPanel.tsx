import { TruthChip } from "./TruthChip";
import { readPlaneState, truthStateFor } from "../lib/truthState";
import type { ReadStatus } from "../types";

interface ReadStatusPanelProps {
  status: ReadStatus;
  compact?: boolean;
}

/** Shared read-status fields — LIVE labelled; demo stub isolated; never canonical. */
export function ReadStatusPanel({ status, compact = false }: ReadStatusPanelProps) {
  const source = status.data_source ?? (status.read_plane === "stub" ? "demo_stub" : "live_api");
  const isDemo = source === "demo_stub" || status.demo_isolated === true;
  return (
    <section
      className="panel"
      aria-label={isDemo ? "Vault read status (demo stub)" : "Vault read status (live API)"}
    >
      <h2>Vault read status</h2>
      {/*
        AX-002: the read plane is stated with the shared chip vocabulary rather
        than an ad-hoc string, so DEMO can never be styled like LIVE and the
        label survives loss of colour.
      */}
      <p className={isDemo ? "banner warn" : "banner"}>
        <TruthChip state={isDemo ? "demo" : "live"} />{" "}
        {isDemo
          ? "isolated sample data · not live vault · not acceptance"
          : "read-only vault projection · UI ≠ canonical"}
      </p>
      <p className="disclaimer">
        UI ≠ canonical · Graph ≠ authority · Unknown ≠ healthy
        {isDemo ? " · demo isolated from LIVE_API" : ""}
      </p>
      <dl className="grid">
        <div>
          <dt>Data source</dt>
          <dd>
            <TruthChip state={readPlaneState(source)} detail={source} compact />
          </dd>
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
          {/*
            truthStateFor() maps absent/unrecognised evidence to UNKNOWN, so an
            unreadable rollup can never render as healthy (unknown != healthy).
          */}
          <dd className={status.health.rollup === "unknown" ? "rollup-unknown" : undefined}>
            <TruthChip state={truthStateFor(status.health.rollup)} compact />
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
