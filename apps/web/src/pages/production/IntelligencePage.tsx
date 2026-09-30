import { useSearchParams } from "react-router-dom";
import { ProdShell } from "../../components/ProdShell";
import {
  type IntelligenceView,
  useLiveIntelligence,
} from "../../hooks/useLiveIntelligence";
import { useReadStatus } from "../../hooks/useReadStatus";

const VIEWS: Array<{ id: IntelligenceView; label: string }> = [
  { id: "overview", label: "Project Intelligence" },
  { id: "evidence", label: "Evidence Explorer" },
  { id: "contradictions", label: "Contradictions" },
  { id: "state", label: "Project State" },
  { id: "attention", label: "Attention" },
  { id: "decision", label: "Decision candidates" },
  { id: "portfolio", label: "Portfolio Intelligence" },
];

function asList(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? (value as Record<string, unknown>[]) : [];
}

function text(value: unknown, fallback = "UNKNOWN"): string {
  if (typeof value === "string" && value.trim()) {
    return value;
  }
  return fallback;
}

function JsonBlock({ value }: { value: unknown }) {
  if (value == null) {
    return <p className="empty">NO_DATA</p>;
  }
  return (
    <pre className="mono" style={{ whiteSpace: "pre-wrap", fontSize: "0.8rem" }}>
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export default function IntelligencePage() {
  const [params, setParams] = useSearchParams();
  const { status, loading: statusLoading } = useReadStatus();
  const projects = status?.projects ?? [];
  const projectParam = params.get("project");
  const projectId =
    projectParam && projectParam.trim() ? projectParam.trim() : null;
  const viewParam = params.get("view");
  const view: IntelligenceView = VIEWS.some((item) => item.id === viewParam)
    ? (viewParam as IntelligenceView)
    : "overview";
  const { bundle, error, loading, dataSource, truth, demoSelected } =
    useLiveIntelligence(projectId, view);
  const projectMismatch = Boolean(
    bundle.state &&
      projectId &&
      text(bundle.state.project_id, "") &&
      text(bundle.state.project_id, "") !== projectId,
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

  function onSelectView(next: IntelligenceView) {
    const nextParams = new URLSearchParams(params);
    if (next === "overview") {
      nextParams.delete("view");
    } else {
      nextParams.set("view", next);
    }
    setParams(nextParams, { replace: true });
  }

  const facts = asList(bundle.state?.known_facts);
  const unknowns = asList(bundle.state?.unknown_facts);
  const stale = asList(bundle.state?.stale_facts);
  const contested = asList(bundle.state?.contested_facts);
  const changes = asList(bundle.state?.recently_changed_facts);
  const gaps = asList(bundle.gapPriority?.prioritized_gaps);
  const assessments = asList(bundle.evidence?.assessments);
  const candidates = asList(bundle.conflicts?.candidates);
  const risks = asList(bundle.attention?.risks);
  const decision = bundle.decision?.decision as Record<string, unknown> | null;
  const dependencies = asList(
    bundle.dependencies?.dependencies ?? bundle.portfolio?.dependencies,
  );

  return (
    <ProdShell>
      <main id="main" className="shell shell-data" tabIndex={-1}>
        <header className="hero">
          <p className="eyebrow">Production · Atlas Intelligence</p>
          <h1>Intelligence</h1>
          <p className="lede">
            Read-only derived intelligence for{" "}
            <code>{projectId ?? "UNKNOWN"}</code>. Not canonical truth. Not
            authority. UNKNOWN stays UNKNOWN. Contradiction candidates are not
            proven falsehoods.
          </p>
          <p className="flags" style={{ marginTop: "0.75rem" }}>
            <span className="chip">ui_canonical=false</span>
            <span className="chip">derived≠authority</span>
            <span className="chip">canonical_write=false</span>
            <span className={`chip truth-${truth.toLowerCase()}`}>
              truth={truth}
            </span>
            <span className="chip">data_source={dataSource ?? "unknown"}</span>
          </p>
        </header>

        <section className="panel" aria-label="Project selector">
          <h2>Project binding</h2>
          <p className="lede">
            Intelligence is actual project binding. Ask remains vault-wide.
            Portfolio is explicit cross-project scope. No silent fixture default.
          </p>
          {statusLoading ? <p className="banner">Loading projects…</p> : null}
          <label className="lede" htmlFor="intel-project">
            Focus project
          </label>
          <select
            id="intel-project"
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
          </select>
        </section>

        <nav className="intel-subnav" aria-label="Intelligence views">
          {VIEWS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={view === item.id ? "chip truth-live" : "chip"}
              onClick={() => onSelectView(item.id)}
            >
              {item.label}
            </button>
          ))}
        </nav>

        {demoSelected ? (
          <p className="banner warn">
            DEMO selected explicitly via VITE_ATLAS_DEMO_ONLY. Not a live
            substitute.
          </p>
        ) : null}
        {error ? (
          <p className="banner warn">
            HTTP_FAILURE — live intelligence unavailable: {error}. Demo was not substituted.
          </p>
        ) : null}
        {loading ? <p className="banner">Loading derived intelligence…</p> : null}
        {projectMismatch ? (
          <p className="banner warn">
            UNKNOWN — intelligence project does not match selected project
          </p>
        ) : null}
        {!projectId ? (
          <p className="banner warn">
            UNKNOWN / SELECT_SCOPE — select a project. Intelligence does not
            default to harbor-api and does not fetch all projects.
          </p>
        ) : null}

        {view === "overview" || view === "state" ? (
          <section className="panel" aria-label="Derived current state">
            <h2>Derived current state</h2>
            <p className="lede">
              DERIVED ≠ authority. UNKNOWN ≠ false. STALE ≠ invalid. CONTESTED ≠
              resolved.
            </p>
            <FactList title="Known facts" items={facts} />
            <FactList title="Unknowns" items={unknowns} />
            <FactList title="Stale areas" items={stale} />
            <FactList title="Contested areas" items={contested} />
            <FactList title="Recent changes" items={changes} />
            <FactList title="Evidence gaps" items={gaps} />
          </section>
        ) : null}

        {view === "overview" || view === "evidence" ? (
          <section className="panel" aria-label="Evidence Explorer">
            <h2>Evidence Explorer</h2>
            <p className="lede">
              Discrete confidence classes only. No AI confidence percentage.
            </p>
            {assessments.length === 0 ? (
              <p className="empty">NO_DATA — no assessments for this scope.</p>
            ) : (
              assessments.map((item, index) => (
                <article key={text(item.claim_id, `assessment-${index}`)} className="intel-card">
                  <p>
                    <span className="chip">
                      {text(item.confidence_class, "UNKNOWN").toUpperCase()}
                    </span>
                    <span className="chip">{text(item.field)}</span>
                    <span className="chip">{text(item.claim_id)}</span>
                  </p>
                  <p className="lede">{text(item.authority_note)}</p>
                  <details>
                    <summary>Inspect explanation / provenance</summary>
                    <JsonBlock value={item} />
                  </details>
                </article>
              ))
            )}
          </section>
        ) : null}

        {view === "overview" || view === "contradictions" ? (
          <section className="panel" aria-label="Contradiction candidates">
            <h2>Contradiction candidates</h2>
            <p className="lede">
              CONTRADICTION CANDIDATE · CONTESTED · NEEDS REVIEW. Not proven
              falsehood.
            </p>
            {candidates.length === 0 ? (
              <p className="empty">
                VALID_EMPTY — no contradiction candidates. This is not proven
                consistency.
              </p>
            ) : (
              candidates.map((item, index) => (
                <article key={text(item.candidate_id, `cc-${index}`)} className="intel-card">
                  <p>
                    <span className="chip truth-contested">CONTESTED</span>
                    <span className="chip">NEEDS REVIEW</span>
                    <span className="chip">{text(item.candidate_class)}</span>
                  </p>
                  <p className="lede">
                    Why candidate exists: {text(item.reason ?? item.why)}
                  </p>
                  <details>
                    <summary>Inspect claim pair / sources / time / authority</summary>
                    <JsonBlock value={item} />
                  </details>
                </article>
              ))
            )}
            {bundle.explain ? (
              <details>
                <summary>Explain-why for datastore</summary>
                <JsonBlock value={bundle.explain} />
              </details>
            ) : null}
          </section>
        ) : null}

        {view === "overview" || view === "attention" ? (
          <section className="panel" aria-label="Attention signals">
            <h2>Attention</h2>
            <p className="lede">
              Attention is not a score or health grade. No 73/100. No risk 82%.
              Discrete explainable classes only.
            </p>
            {risks.length === 0 ? (
              <p className="empty">UNKNOWN — no attention signals. Not healthy.</p>
            ) : (
              risks.map((item, index) => (
                <article key={text(item.signal_id ?? item.risk_id, `risk-${index}`)} className="intel-card">
                  <p>
                    <span className="chip">{text(item.risk_class ?? item.kind, "UNKNOWN")}</span>
                    <span className="chip">risk≠fact</span>
                  </p>
                  <p className="lede">{text(item.reason)}</p>
                  <details>
                    <summary>Inspect evidence</summary>
                    <JsonBlock value={item} />
                  </details>
                </article>
              ))
            )}
          </section>
        ) : null}

        {view === "overview" ? (
          <section className="panel" aria-label="Dependencies">
            <h2>Dependencies</h2>
            <p className="lede">
              Explicit evidence only. DEPENDENCY_IS_INFERRED = NO.
            </p>
            {dependencies.length === 0 ? (
              <p className="empty">NO_DATA — no explicit dependency evidence.</p>
            ) : (
              dependencies.map((item, index) => (
                <article key={text(item.dependency_id, `dep-${index}`)} className="intel-card">
                  <p className="lede">{text(item.reason ?? item.statement)}</p>
                  <details>
                    <summary>Inspect provenance</summary>
                    <JsonBlock value={item} />
                  </details>
                </article>
              ))
            )}
          </section>
        ) : null}

        {view === "overview" || view === "decision" ? (
          <section className="panel" aria-label="Decision candidates">
            <h2>Decision candidates</h2>
            <p className="lede">
              Not a command. Not a selected decision. Not authority. No execute
              control.
            </p>
            {!decision ? (
              <p className="empty">UNKNOWN — no decision candidate composed.</p>
            ) : (
              <article className="intel-card">
                <p>
                  <span className="chip">selected=null</span>
                  <span className="chip">not-a-command</span>
                </p>
                <dl className="grid">
                  <dt>Question</dt>
                  <dd>{text(decision.question)}</dd>
                  <dt>Known evidence</dt>
                  <dd>{asList(decision.known_evidence).length}</dd>
                  <dt>Unknowns</dt>
                  <dd>{asList(decision.unknowns).length}</dd>
                  <dt>Conflicts</dt>
                  <dd>{asList(decision.conflicts).length}</dd>
                  <dt>Options</dt>
                  <dd>{asList(decision.options).length}</dd>
                  <dt>Constraints</dt>
                  <dd>{asList(decision.constraints).length}</dd>
                  <dt>Gaps</dt>
                  <dd>{asList(decision.evidence_gaps).length}</dd>
                  <dt>Reversibility</dt>
                  <dd>{text(decision.reversibility)}</dd>
                </dl>
                <details>
                  <summary>Inspect decision candidate</summary>
                  <JsonBlock value={decision} />
                </details>
              </article>
            )}
          </section>
        ) : null}

        {view === "overview" || view === "portfolio" ? (
          <section className="panel" aria-label="Portfolio Intelligence">
            <h2>Portfolio Intelligence</h2>
            <p className="lede">
              Explicit cross-project scope. No numeric priority score.
            </p>
            <JsonBlock value={bundle.portfolio} />
          </section>
        ) : null}
      </main>
    </ProdShell>
  );
}

function FactList({
  title,
  items,
}: {
  title: string;
  items: Record<string, unknown>[];
}) {
  return (
    <div style={{ marginTop: "1rem" }}>
      <h3>{title}</h3>
      {items.length === 0 ? (
        <p className="empty">NO_DATA</p>
      ) : (
        items.map((item, index) => (
          <article key={text(item.fact_id ?? item.gap_id, `${title}-${index}`)} className="intel-card">
            <p>
              <span className={`chip truth-${text(item.status, "unknown").toLowerCase()}`}>
                {text(item.status, "UNKNOWN").toUpperCase()}
              </span>
              <span className="chip">{text(item.field ?? item.kind)}</span>
            </p>
            <p className="lede">{text(item.value ?? item.reason ?? item.why)}</p>
            <details>
              <summary>Inspect explanation / provenance</summary>
              <JsonBlock value={item} />
            </details>
          </article>
        ))
      )}
    </div>
  );
}
