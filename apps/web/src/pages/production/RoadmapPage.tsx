import { useSearchParams } from "react-router-dom";
import { ProdShell } from "../../components/ProdShell";
import { useLiveRoadmap } from "../../hooks/useLiveRoadmap";
import { useReadStatus } from "../../hooks/useReadStatus";
import { listState, missingListText } from "../../lib/missingState";

/**
 * AS-PROJECT-ROADMAP-001 web lens — derived Living Project Roadmap V1.
 * UI ≠ canonical; ROADMAP ≠ authority; UNKNOWN ≠ healthy.
 * Project is explicit ?project= only. No silent fixture default.
 */
export default function RoadmapPage() {
  const [params, setParams] = useSearchParams();
  const { status, loading: statusLoading } = useReadStatus();
  const projects = status?.projects ?? [];
  const projectParam = params.get("project");
  const projectId =
    projectParam && projectParam.trim() ? projectParam.trim() : null;
  const { roadmap, error, loading, dataSource } = useLiveRoadmap(projectId);
  const isDemo = dataSource === "demo_stub";
  const here = roadmap?.you_are_here;
  const nextUnlock = roadmap?.next_unlock;
  const path = roadmap?.critical_path ?? [];
  const briefProject =
    typeof roadmap?.project_id === "string" ? roadmap.project_id.trim() : "";
  // STYLE-002: no roadmap payload (no project, demo-only, failed, loading) is
  // UNKNOWN / unavailable — never "no blockers" or "no unknowns".
  const roadmapSource = { loading, error, loaded: Boolean(roadmap) };
  const pathMissing = missingListText(
    listState({ ...roadmapSource, list: roadmap?.critical_path }),
    "critical path",
  );
  const blockersMissing = missingListText(
    listState({ ...roadmapSource, list: roadmap?.blockers }),
    "blockers",
  );
  const unknownsMissing = missingListText(
    listState({ ...roadmapSource, list: roadmap?.unknowns }),
    "unknown signals",
  );
  const projectMismatch = Boolean(
    roadmap && projectId && briefProject && briefProject !== projectId,
  );

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
      <main id="main" className="shell" tabIndex={-1}>
        <header className="hero">
          <p className="eyebrow">Production · Living Project Roadmap V1</p>
          <h1>Project roadmap</h1>
          <p className="lede">
            Derived projection of where <code>{projectId ?? "UNKNOWN"}</code> is,
            why it is there, and what unlocks next. Not canonical truth. Not
            authority. No invented completion percentages.
          </p>
          <p className="flags" style={{ marginTop: "0.75rem" }}>
            <span className="chip">ui_canonical=false</span>
            <span className="chip">roadmap≠authority</span>
            <span className="chip">unknown≠healthy</span>
            <span className="chip">data_source={dataSource ?? "unknown"}</span>
          </p>
        </header>

        <section className="panel" aria-label="Project selector">
          <h2>Project</h2>
          {statusLoading ? <p className="banner">Loading projects…</p> : null}
          <label className="lede" htmlFor="roadmap-project">
            Focus project
          </label>
          <select
            id="roadmap-project"
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

        {error ? <p className="banner warn">Roadmap unavailable: {error}</p> : null}
        {loading ? <p className="banner">Loading…</p> : null}
        {isDemo ? (
          <p className="banner warn">
            DEMO STUB isolated — start <code>atlas live api-serve</code> to see
            a live derived roadmap (not vault truth)
          </p>
        ) : null}
        {projectMismatch ? (
          <p className="banner warn">
            UNKNOWN — roadmap project does not match selected project
          </p>
        ) : null}

        <section className="panel" aria-label="You are here">
          <h2>You are here</h2>
          {!loading && !here ? (
            <p className="banner warn">UNKNOWN — no position evidence</p>
          ) : (
            <p>
              <strong>{here?.title ?? "UNKNOWN"}</strong> [{here?.status ?? "UNKNOWN"}
              {here?.lifecycle ? ` / ${here.lifecycle}` : ""}]
              <span> · {here?.why ?? here?.reason ?? "unknown"}</span>
            </p>
          )}
        </section>

        <section className="panel" aria-label="Next unlock">
          <h2>Next unlock</h2>
          {!nextUnlock ? (
            <p className="banner warn">UNKNOWN — no unlock evidence</p>
          ) : (
            <p>
              <strong>{nextUnlock.title}</strong> [{nextUnlock.status}]
              <span> · {nextUnlock.why ?? nextUnlock.unlock_condition ?? "UNKNOWN"}</span>
            </p>
          )}
        </section>

        <section className="panel" aria-label="Critical path">
          <h2>Critical path</h2>
          {pathMissing ? (
            <p className="banner warn">{pathMissing}</p>
          ) : path.length === 0 ? (
            <p className="banner warn">
              {roadmap?.honesty?.cyclic_dependencies
                ? "UNKNOWN — cyclic dependencies; no invented path"
                : "empty — no remaining-work path (not an invented completion)"}
            </p>
          ) : (
            <p>{path.join(" → ")}</p>
          )}
        </section>

        <section className="panel" aria-label="Blockers">
          <h2>Blockers</h2>
          {blockersMissing ? (
            <p className="banner warn">{blockersMissing}</p>
          ) : (roadmap?.blockers ?? []).length === 0 ? (
            <p>No derived blockers on this lens.</p>
          ) : (
            <ul>
              {(roadmap?.blockers ?? []).map((blocker, index) => (
                <li key={`${blocker.waiting_on ?? "none"}-${index}`}>
                  {blocker.reason ?? "UNKNOWN"}
                  {blocker.waiting_on ? ` · waiting on ${blocker.waiting_on}` : ""}
                  {blocker.unlock_condition
                    ? ` · unlock: ${blocker.unlock_condition}`
                    : ""}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="panel" aria-label="Work items">
          <h2>Items</h2>
          {(roadmap?.items ?? []).length === 0 ? (
            <p className="banner warn">UNKNOWN — no roadmap items</p>
          ) : (
            <ul className="theme-hub">
              {(roadmap?.items ?? []).map((item) => (
                <li key={item.id}>
                  <strong>
                    {item.critical_path ? "* " : ""}
                    {item.id}
                  </strong>
                  <span>
                    [{item.status}] {item.title}
                    {item.missing_acceptance_evidence
                      ? " · missing acceptance evidence"
                      : ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="panel" aria-label="Unknowns">
          <h2>Unknowns</h2>
          {unknownsMissing ? (
            <p className="banner warn">{unknownsMissing}</p>
          ) : (roadmap?.unknowns ?? []).length === 0 ? (
            <p>No UNKNOWN signals on this derived lens.</p>
          ) : (
            <ul>
              {(roadmap?.unknowns ?? []).map((unknown) => (
                <li key={unknown}>{unknown}</li>
              ))}
            </ul>
          )}
        </section>
      </main>
    </ProdShell>
  );
}
