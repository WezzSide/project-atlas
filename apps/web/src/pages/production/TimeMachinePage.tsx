import { useSearchParams } from "react-router-dom";
import { ProdShell } from "../../components/ProdShell";
import { useReadStatus } from "../../hooks/useReadStatus";
import {
  TIME_MACHINE_PROJECT,
  TIME_MACHINE_T1,
  TIME_MACHINE_T2,
  useLiveTimeMachine,
  type KdiffCell,
} from "../../hooks/useLiveTimeMachine";

/**
 * AS-2.2-KDIFF-001 web lens — conflict + Time Machine (as-of / T1→T2 diff)
 * for the selected project. Golden-demo defaults remain harbor-api / T1→T2.
 * LIVE_API preferred; demo fallback stays empty and isolated. Read-only;
 * kdiff ≠ authority.
 */
export default function TimeMachinePage() {
  const [params, setParams] = useSearchParams();
  const { status, loading: statusLoading } = useReadStatus();
  const projects = status?.projects ?? [];
  const projectParam = params.get("project");
  const usingControlledDemoDefault = projectParam === null;
  const projectId = projectParam ?? TIME_MACHINE_PROJECT;
  const t1 = params.get("from") ?? TIME_MACHINE_T1;
  const t2 = params.get("to") ?? TIME_MACHINE_T2;
  const {
    conflicts,
    asOfT1Cells,
    asOfT2Cells,
    diff,
    error,
    loading,
    dataSource,
  } = useLiveTimeMachine(projectId, t1, t2);
  const isDemo = dataSource === "demo_stub";
  const liveReady = !loading && !error && dataSource === "live_api";

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
          <p className="eyebrow">Production · Conflict &amp; Time Machine</p>
          <h1>Conflict &amp; Time Machine</h1>
          <p className="lede">
            {usingControlledDemoDefault ? (
              <>
                No <code>?project=</code> query — using{" "}
                <code>harbor-api</code> as the controlled golden-demo fixture,
                not as a selected authentic project. AUTHENTIC_HISTORY_CLAIM=NO.
                Unresolved conflicts plus as-of snapshots at {t1} and {t2} and
                the T1→T2 diff. LIVE_API preferred; demo fallback stays empty
                and isolated — nothing is invented in the browser.
              </>
            ) : (
              <>
                Read-only view of <code>{projectId ?? "UNKNOWN"}</code> LIVE
                state: unresolved conflicts plus Time Machine as-of snapshots at{" "}
                {t1} and {t2} and the T1→T2 diff. LIVE_API preferred; demo
                fallback stays empty and isolated — nothing is invented in the
                browser.
              </>
            )}
          </p>
          <p className="flags" style={{ marginTop: "0.75rem" }}>
            <span className="chip">ui_canonical=false</span>
            <span className="chip">graph_authority=false</span>
            <span className="chip">kdiff≠authority</span>
            <span className="chip">data_source={dataSource ?? "unknown"}</span>
            {usingControlledDemoDefault ? (
              <>
                <span className="chip">fixture_class=controlled_demo_fixture</span>
                <span className="chip">authentic_history_claim=no</span>
              </>
            ) : (
              <span className="chip">project_query=explicit</span>
            )}
          </p>
        </header>

        <section className="panel" aria-label="Project selector">
          <h2>Project</h2>
          {statusLoading ? <p className="banner">Loading projects…</p> : null}
          <label className="lede" htmlFor="time-machine-project">
            Focus project
          </label>
          <select
            id="time-machine-project"
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
        </section>

        {error ? <p className="banner warn">Time Machine unavailable: {error}</p> : null}
        {loading ? <p className="banner">Loading…</p> : null}
        {usingControlledDemoDefault ? (
          <p className="banner warn">
            Controlled demo fixture default — <code>harbor-api</code> is not
            claimed as the user&apos;s selected authentic project. Authentic
            binds (including Dark Factory) require <code>?project=</code>.
            Dark Factory is authentic estate identity, not kdiff history.
          </p>
        ) : null}
        {isDemo ? (
          <p className="banner warn">
            DEMO STUB isolated — start <code>atlas live api-serve</code> to see
            live conflict + Time Machine state (not vault truth)
          </p>
        ) : null}

        <section className="panel" aria-label="Unresolved conflict">
          <h2>Unresolved conflict</h2>
          {liveReady && conflicts.length === 0 ? (
            <p className="banner warn">unknown — no conflict rows</p>
          ) : !liveReady && conflicts.length === 0 ? (
            <p className="lede">
              {error
                ? "Unavailable — not an empty conflict catalog."
                : isDemo
                  ? "Demo stub isolated — not an empty conflict catalog."
                  : "Waiting for live conflict rows."}
            </p>
          ) : (
            <ul className="theme-hub">
              {conflicts.map((conflict, index) => (
                <li key={conflict.conflict_id || `${conflict.subject}-${index}`}>
                  <strong>
                    {conflict.subject} · {conflict.field}
                  </strong>
                  <span>type: {conflict.conflict_type}</span>
                  <div className="flags" style={{ marginTop: "0.5rem" }}>
                    {conflict.claims.map((claim, claimIndex) => (
                      <span
                        key={`${claim.source_id ?? "unsourced"}-${claimIndex}`}
                        className="chip"
                      >
                        {claimIndex === 0 ? "VALUE A" : claimIndex === 1 ? "VALUE B" : `VALUE ${claimIndex + 1}`}
                        : {claim.claim || "—"} (source:{" "}
                        {claim.source_id ?? "—"})
                      </span>
                    ))}
                  </div>
                  <span style={{ marginTop: "0.5rem" }}>
                    UNRESOLVED — Atlas does not pick a winner
                  </span>
                </li>
              ))}
            </ul>
          )}
          <p className="disclaimer">
            Conflict projection ≠ authority · ≠ resolution
            {isDemo ? " · demo isolated from LIVE_API" : " · LIVE_API read-only"}
          </p>
        </section>

        <section className="panel" aria-label="Time Machine">
          <h2>Time Machine</h2>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "1rem",
            }}
          >
            <div>
              <h3>At T1 ({t1})</h3>
              <AsOfCells cells={asOfT1Cells} liveReady={liveReady} error={error} isDemo={isDemo} />
            </div>
            <div>
              <h3>At T2 ({t2})</h3>
              <AsOfCells cells={asOfT2Cells} liveReady={liveReady} error={error} isDemo={isDemo} />
            </div>
          </div>

          <h3 style={{ marginTop: "1rem" }}>What changed T1 → T2</h3>
          {liveReady &&
          diff.value_changed.length === 0 &&
          diff.added.length === 0 ? (
            <p className="banner warn">unknown — no recorded changes</p>
          ) : !liveReady &&
            diff.value_changed.length === 0 &&
            diff.added.length === 0 ? (
            <p className="lede">
              {error
                ? "Unavailable — not an empty Time Machine diff."
                : isDemo
                  ? "Demo stub isolated — not an empty Time Machine diff."
                  : "Waiting for live Time Machine diff."}
            </p>
          ) : (
            <ul className="theme-hub">
              {diff.value_changed.map((change, index) => (
                <li key={`changed-${change.subject}-${change.field}-${index}`}>
                  <strong>
                    {change.subject}.{change.field}
                  </strong>
                  <span>
                    {change.from_value_sketch || "—"} →{" "}
                    {change.to_value_sketch || "—"}
                  </span>
                </li>
              ))}
              {diff.added.map((added, index) => (
                <li key={`added-${added.subject}-${added.field}-${index}`}>
                  <strong>
                    {added.subject}.{added.field}
                  </strong>
                  <span>+ {added.value_sketch ?? "—"} (added)</span>
                </li>
              ))}
            </ul>
          )}
          <p className="disclaimer">
            Time Machine read (as-of / T1→T2) · kdiff ≠ authority
            {isDemo ? " · demo isolated from LIVE_API" : " · LIVE_API read-only"}
          </p>
        </section>
      </main>
    </ProdShell>
  );
}

function AsOfCells({
  cells,
  liveReady,
  error,
  isDemo,
}: {
  cells: KdiffCell[];
  liveReady: boolean;
  error: string | null;
  isDemo: boolean;
}) {
  if (liveReady && cells.length === 0) {
    return <p className="banner warn">unknown — no cells</p>;
  }
  if (!liveReady && cells.length === 0) {
    return (
      <p className="lede">
        {error
          ? "Unavailable — not an empty as-of catalog."
          : isDemo
            ? "Demo stub isolated — not an empty as-of catalog."
            : "Waiting for live as-of cells."}
      </p>
    );
  }
  return (
    <ul className="theme-hub">
      {cells.map((cell, index) => {
        const absent =
          cell.disposition === "not_found" || cell.disposition === "absent";
        return (
          <li key={`${cell.subject}-${cell.field}-${index}`}>
            <strong>
              {cell.subject} · {cell.field}
            </strong>
            <span style={{ display: "block" }}>
              disposition: {cell.disposition}
            </span>
            <span style={{ display: "block" }}>
              {absent ? "— (absent)" : cell.value_sketch ?? "—"}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
