import { useSearchParams } from "react-router-dom";
import { ProdShell } from "../../components/ProdShell";
import {
  useLiveSourceHealth,
  type SourceHealthRow,
} from "../../hooks/useLiveSourceHealth";
import { useReadStatus } from "../../hooks/useReadStatus";

/**
 * AS-CODER-ALPHA-SOURCE-HEALTH-WEB-001 — read-only source-health web lens.
 * Explicit ?project= required. No implicit portfolio-all.
 * health_state is opaque. UNKNOWN/UNREADABLE stay honest.
 * UI != CANONICAL TRUTH. SOURCE HEALTH != AUTHORITY. No secret echo.
 */

function opaqueHealthState(value: unknown): string {
  return typeof value === "string" && value.trim() ? value.trim() : "UNKNOWN";
}

function countEntries(value: Record<string, number> | undefined): [string, number][] {
  return Object.entries(value ?? {}).sort(([left], [right]) => left.localeCompare(right));
}

function RowList({
  rows,
  emptyLabel,
}: {
  rows: SourceHealthRow[];
  emptyLabel: string;
}) {
  if (rows.length === 0) {
    return <p className="banner warn">{emptyLabel}</p>;
  }
  return (
    <ul className="theme-hub">
      {rows.slice(0, 30).map((row, index) => (
        <li key={`${row.source ?? "unknown"}-${row.reason_code ?? index}`}>
          <strong>
            {row.reason_code ?? "UNCLASSIFIED"} · {row.status ?? "UNKNOWN"}
          </strong>
          <span>{row.source ?? "UNKNOWN"}</span>
          <span>
            {row.human_explanation ?? "UNKNOWN — no safe explanation"}
            {row.pipeline_stage ? ` · stage ${row.pipeline_stage}` : ""}
          </span>
          <span>
            {row.suggested_next_action ?? "Inspect atlas source-health"}
            {row.evidence ? ` · evidence ${row.evidence}` : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}

export default function SourceHealthPage() {
  const [params, setParams] = useSearchParams();
  const { status, loading: statusLoading } = useReadStatus();
  const projects = status?.projects ?? [];
  const projectParam = params.get("project");
  const projectId =
    projectParam && projectParam.trim() ? projectParam.trim() : null;
  const { report, error, loading, dataSource } = useLiveSourceHealth(projectId);
  const isDemo = dataSource === "demo_stub";
  const reportProject =
    typeof report?.project_id === "string" ? report.project_id.trim() : "";
  const projectMismatch = Boolean(
    report && projectId && reportProject && reportProject !== projectId,
  );
  const healthState = projectMismatch
    ? "UNKNOWN"
    : opaqueHealthState(report?.health_state);
  const isUnknown = healthState === "UNKNOWN";
  const isUnreadable = healthState === "UNREADABLE";
  const honestDegraded = isUnknown || isUnreadable;
  const actionable = report?.actionable ?? [];
  const noise = report?.noise ?? [];

  function onSelectProject(next: string) {
    const nextParams = new URLSearchParams(params);
    if (next) {
      nextParams.set("project", next);
    } else {
      nextParams.delete("project");
    }
    setParams(nextParams, { replace: true });
  }

  return (
    <ProdShell>
      <main id="main" className="shell shell-data" tabIndex={-1}>
        <header className="hero">
          <p className="eyebrow">
            Production · Source Health · AS-CODER-ALPHA-SOURCE-HEALTH-WEB-001
          </p>
          <h1>Source health</h1>
          <p className="lede">
            Read-only LIVE_API projection of <code>atlas source-health</code> for{" "}
            <code>{projectId ?? "UNKNOWN"}</code>. Explicit <code>?project=</code>{" "}
            is required. This page is not vault truth and is not authority.
            Secret content is never shown.
          </p>
          <p className="flags" style={{ marginTop: "0.75rem" }}>
            <span className="chip">ui_canonical=false</span>
            <span className="chip">source_health≠authority</span>
            <span className="chip">unknown≠healthy</span>
            <span className="chip">data_source={dataSource ?? "unknown"}</span>
            <span className="chip">health_state={healthState}</span>
          </p>
        </header>

        <section className="panel" aria-label="Project selector">
          <h2>Project</h2>
          {statusLoading ? <p className="banner">Loading projects…</p> : null}
          <label className="lede" htmlFor="source-health-project">
            Focus project
          </label>
          <select
            id="source-health-project"
            value={projectId ?? ""}
            onChange={(event) => onSelectProject(event.target.value)}
            style={{ display: "block", marginTop: "0.5rem", maxWidth: "24rem" }}
          >
            {!projectId ? (
              <option value="">unknown — select a project</option>
            ) : null}
            {projects.map((project) => (
              <option
                key={project.project_id ?? project.path}
                value={project.project_id ?? ""}
              >
                {project.project_id ?? "unnamed"}
              </option>
            ))}
            {projectId &&
            !projects.some((project) => project.project_id === projectId) ? (
              <option value={projectId}>{projectId}</option>
            ) : null}
          </select>
          {!projectId ? (
            <p className="banner warn">
              UNKNOWN — explicit <code>?project=</code> required. No implicit
              portfolio-all.
            </p>
          ) : null}
        </section>

        {error ? (
          <p className="banner warn">Source health unavailable: {error}</p>
        ) : null}
        {loading ? <p className="banner">Loading…</p> : null}
        {isDemo ? (
          <p className="banner warn">
            DEMO STUB isolated — start <code>atlas live api-serve</code> to see
            live source-health (not vault truth)
          </p>
        ) : null}
        {projectMismatch ? (
          <p className="banner warn">
            UNKNOWN — source-health project does not match selected project
          </p>
        ) : null}

        <section className="panel" aria-label="Health state">
          <h2>Health state</h2>
          {!projectId ? (
            <p className="banner warn">
              UNKNOWN — no project selected; source-health was not requested
            </p>
          ) : !loading && !report && !error && !isDemo ? (
            <p className="banner warn">UNKNOWN — no source-health payload</p>
          ) : honestDegraded ? (
            <p className="banner warn">
              {healthState} — not healthy. {isUnreadable
                ? "An inspected artifact could not be read."
                : "Missing or unscoped evidence stays unknown."}{" "}
              The browser does not invent CLEAR.
            </p>
          ) : (
            <p>
              Derived label <strong>{healthState}</strong> (opaque string from
              LIVE_API — not an authority enum, not a score).
            </p>
          )}
          <p className="flags">
            <span className="chip">
              diagnostic={report?.diagnostic ?? "UNKNOWN"}
            </span>
            <span className="chip">
              authority={report?.authority ?? "derived"}
            </span>
            <span className="chip">
              lens_is_authority=
              {String(report?.honesty?.lens_is_authority ?? false)}
            </span>
          </p>
          <p className="disclaimer">
            SOURCE HEALTH != AUTHORITY · UI != CANONICAL TRUTH · UNKNOWN !=
            healthy · UNREADABLE != healthy
            {isDemo ? " · demo isolated" : ""}
          </p>
        </section>

        <section className="panel" aria-label="Counts">
          <h2>Counts</h2>
          {!report ? (
            <p className="banner warn">UNKNOWN — counts unavailable</p>
          ) : (
            <>
              <p className="flags">
                <span className="chip">
                  source_count={String(report.source_count ?? "UNKNOWN")}
                </span>
                <span className="chip">
                  actionable_count={String(report.actionable_count ?? "UNKNOWN")}
                </span>
                <span className="chip">
                  noise_count={String(report.noise_count ?? "UNKNOWN")}
                </span>
                <span className="chip">
                  unscoped_omitted=
                  {String(report.unscoped_omitted_count ?? "UNKNOWN")}
                </span>
              </p>
              {countEntries(report.reason_counts).length > 0 ? (
                <ul>
                  {countEntries(report.reason_counts).map(([code, count]) => (
                    <li key={code}>
                      {code}: {count}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="lede">No reason_code tallies on this lens.</p>
              )}
            </>
          )}
        </section>

        <section className="panel" aria-label="Actionable sources">
          <h2>Actionable</h2>
          {!report ? (
            <p className="banner warn">UNKNOWN — no actionable rows</p>
          ) : (
            <RowList
              rows={actionable}
              emptyLabel="unknown — no actionable source rows"
            />
          )}
          <p className="disclaimer">
            reason_code + safe explanation only · no secret content · no write
            controls
          </p>
        </section>

        <section className="panel" aria-label="Informational noise">
          <h2>Informational noise</h2>
          {!report ? (
            <p className="banner warn">UNKNOWN — no noise rows</p>
          ) : (
            <RowList
              rows={noise}
              emptyLabel="unknown — no informational noise rows"
            />
          )}
        </section>

        <section className="panel" aria-label="Source Health boundaries">
          <h2>Boundaries</h2>
          <p className="banner warn">
            UI != CANONICAL TRUTH — browser state is never vault truth.
          </p>
          <p className="banner warn">
            SOURCE HEALTH != AUTHORITY — derived explainability, not a winner.
          </p>
          <p className="banner warn">
            No secret echo — matched secret content is never rendered.
          </p>
          <p className="disclaimer">
            Read-only LIVE_API consumer · no Layer B writes · no score theatre ·
            no implicit portfolio-all
          </p>
        </section>
      </main>
    </ProdShell>
  );
}
