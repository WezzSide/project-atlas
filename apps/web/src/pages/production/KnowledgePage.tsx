import { Link, useSearchParams } from "react-router-dom";
import { ProdShell } from "../../components/ProdShell";
import { useLiveBrief } from "../../hooks/useLiveBrief";
import { useLiveKnowledge } from "../../hooks/useLiveKnowledge";
import { useReadStatus } from "../../hooks/useReadStatus";

/**
 * AS-CODER-ALPHA-WEB-001 / TRUTH-UX-001 — project Knowledge UX.
 * Read-only Core brief + lenses + truth panel. UI ≠ canonical.
 */

function textOrUnknown(value: unknown): string {
  if (typeof value === "string" && value.trim()) {
    return value;
  }
  return "UNKNOWN";
}

function Section({
  title,
  body,
  status,
}: {
  title: string;
  body: string;
  status?: string;
}) {
  const unknown = body.trim().toUpperCase() === "UNKNOWN" || body.includes("UNKNOWN");
  return (
    <section className="panel" aria-label={title}>
      <h2>
        {title}{" "}
        {status ? <span className="chip">{status}</span> : null}
        {unknown ? <span className="chip">UNKNOWN≠healthy</span> : null}
      </h2>
      <p className={unknown ? "banner warn" : "lede"} style={{ maxWidth: "48rem" }}>
        {body}
      </p>
    </section>
  );
}

export default function KnowledgePage() {
  const [params, setParams] = useSearchParams();
  const projectParam = params.get("project");
  const { status, loading: statusLoading } = useReadStatus();
  const projects = status?.projects ?? [];
  const projectId =
    projectParam ??
    (projects.find((p) => p.project_id === "project-atlas")?.project_id ??
      projects[0]?.project_id ??
      null);

  const { brief, error: briefError, loading: briefLoading, dataSource } =
    useLiveBrief(projectId);
  const { knowledge, error: knowledgeError, loading: knowledgeLoading } =
    useLiveKnowledge(projectId);
  const isDemo = dataSource === "demo_stub";
  const truth = brief?.truth;
  const nextWork = Array.isArray(brief?.suggested_next_work)
    ? brief.suggested_next_work
    : [];

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
          <p className="eyebrow">Production · Knowledge · Coder Alpha</p>
          <h1>{projectId ?? "Knowledge"}</h1>
          <p className="lede">
            Open Atlas and understand the project in under one minute — purpose,
            state, changes, decisions, unknowns, next work, and session memory.
            Same Core brief as CLI / Obsidian. UI ≠ canonical.
          </p>
          <p className="flags" style={{ marginTop: "0.75rem" }}>
            <span className="chip">ui_canonical=false</span>
            <span className="chip">graph_authority=false</span>
            <span className="chip">unknown≠healthy</span>
            <span className="chip">confidence_theatre=false</span>
            <span className="chip">data_source={dataSource ?? "unknown"}</span>
          </p>
        </header>

        <section className="panel" aria-label="Project selector">
          <h2>Project</h2>
          {statusLoading ? <p className="banner">Loading projects…</p> : null}
          <label className="lede" htmlFor="knowledge-project">
            Focus project
          </label>
          <select
            id="knowledge-project"
            value={projectId ?? ""}
            onChange={(event) => onSelectProject(event.target.value)}
            style={{ display: "block", marginTop: "0.5rem", maxWidth: "24rem" }}
          >
            {!projectId ? <option value="">unknown — select a project</option> : null}
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

        {briefError ? <p className="banner warn">Brief unavailable: {briefError}</p> : null}
        {knowledgeError ? (
          <p className="banner warn">Knowledge inventory: {knowledgeError}</p>
        ) : null}
        {briefLoading || knowledgeLoading ? <p className="banner">Loading…</p> : null}
        {isDemo ? (
          <p className="banner warn">
            DEMO STUB isolated — no live brief; not vault truth
          </p>
        ) : null}

        {brief ? (
          <>
            <Section title="Purpose" body={textOrUnknown(brief.purpose)} status="derived" />
            <Section
              title="Current state"
              body={textOrUnknown(brief.current_state)}
              status="derived"
            />
            <Section
              title="What changed"
              body={textOrUnknown(brief.recent_meaningful_changes)}
              status="derived"
            />
            <Section
              title="Decisions"
              body={textOrUnknown(brief.important_decisions)}
              status="derived"
            />
            <Section
              title="Unknown / conflicts"
              body={textOrUnknown(brief.unknown_or_conflicting)}
              status="honesty"
            />

            <section className="panel" aria-label="Next work">
              <h2>Next work</h2>
              {nextWork.length === 0 ? (
                <p className="banner warn">UNKNOWN — no suggested next work</p>
              ) : (
                <ul className="theme-hub">
                  {nextWork.map((item) => (
                    <li key={item}>
                      <strong>{item}</strong>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="panel" aria-label="Recent session memory">
              <h2>Recent session memory</h2>
              {(brief.session_captures ?? []).length === 0 ? (
                <p className="banner warn">unknown — no session captures</p>
              ) : (
                <ul className="theme-hub">
                  {(brief.session_captures ?? []).map((capture) => (
                    <li key={String(capture.capture_id)}>
                      <strong>
                        [{String(capture.kind ?? "capture")}]{" "}
                        {String(capture.summary ?? "UNKNOWN")}
                      </strong>
                      <span>
                        ops receipt · authority=false · {String(capture.source ?? "")}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="panel" aria-label="Knowledge Inbox conversation captures">
              <h2>Knowledge Inbox — conversation captures</h2>
              <p className="lede">
                Quarantined conversation evidence. Conversation ≠ authority.
                Capture ≠ Truth Core. Review ≠ automatic promotion.
              </p>
              {(brief.conversation_captures ?? []).length === 0 ? (
                <p className="banner warn">unknown — no conversation captures</p>
              ) : (
                <ul className="theme-hub">
                  {(brief.conversation_captures ?? []).map((capture) => (
                    <li key={String(capture.capture_id)}>
                      <strong>
                        [{String(capture.review_state ?? "captured")}]{" "}
                        {String(capture.summary ?? "UNKNOWN")}
                      </strong>
                      <span>
                        {String(capture.label ?? "Conversation capture — non-authoritative")}{" "}
                        · provider={String(capture.source_provider ?? "unknown")} ·
                        classification={String(capture.classification ?? "NON_CANONICAL")} ·
                        items={String(capture.item_count ?? 0)} · authority=false
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="panel" aria-label="Truth inspection">
              <h2>Truth — why does Atlas believe this?</h2>
              <p className="lede">
                Concrete provenance and status labels. No confidence scores.
                Evidence ≠ interpretation · model ≠ authority.
              </p>
              <p className="flags">
                <span className="chip">
                  pending={truth?.pending_review_count ?? 0}
                </span>
                <span className="chip">conflicts={truth?.conflict_count ?? 0}</span>
                <span className="chip">
                  human_decisions={truth?.human_decision_count ?? 0}
                </span>
                <span className="chip">
                  evidence={(truth?.evidence ?? []).length}
                </span>
              </p>

              <h3>Evidence</h3>
              {(truth?.evidence ?? []).length === 0 ? (
                <p className="banner warn">UNKNOWN — no evidence links</p>
              ) : (
                <ul className="theme-hub">
                  {(truth?.evidence ?? []).map((row) => (
                    <li key={String(row.path)}>
                      <strong>{String(row.path)}</strong>
                      <span>
                        kind={String(row.kind)} · authority=
                        {String(Boolean(row.authority))}
                      </span>
                    </li>
                  ))}
                </ul>
              )}

              <h3>Pending human review</h3>
              {(truth?.pending_reviews ?? []).length === 0 ? (
                <p className="banner">No pending reviews recorded</p>
              ) : (
                <ul className="theme-hub">
                  {(truth?.pending_reviews ?? []).map((row) => (
                    <li key={String(row.review_id)}>
                      <strong>{String(row.review_id)}</strong>
                      <span>
                        {String(row.reason ?? "UNKNOWN")} · status=
                        {String(row.status ?? "pending")} · verified=false
                      </span>
                    </li>
                  ))}
                </ul>
              )}

              <h3>Conflicts</h3>
              {(truth?.conflicts ?? []).length === 0 ? (
                <p className="banner">No unresolved conflicts recorded</p>
              ) : (
                <ul className="theme-hub">
                  {(truth?.conflicts ?? []).map((row) => (
                    <li key={String(row.conflict_id)}>
                      <strong>
                        {String(row.subject)} / {String(row.field)}
                      </strong>
                      <span>
                        {(row.claims ?? [])
                          .map((claim) => claim.claim ?? "")
                          .filter(Boolean)
                          .join(" vs ") || "UNKNOWN competing claims"}
                      </span>
                    </li>
                  ))}
                </ul>
              )}

              <h3>Human accept / reject</h3>
              {(truth?.human_decisions ?? []).length === 0 ? (
                <p className="banner warn">
                  unknown — no human dispositions yet (use{" "}
                  <code>atlas review decide</code>)
                </p>
              ) : (
                <ul className="theme-hub">
                  {(truth?.human_decisions ?? []).map((row) => (
                    <li key={`${row.review_id}-${row.decision}`}>
                      <strong>
                        {String(row.decision)} · {String(row.review_id)}
                      </strong>
                      <span>{String(row.reason ?? "")}</span>
                    </li>
                  ))}
                </ul>
              )}

              <p className="flags" style={{ marginTop: "1rem" }}>
                <Link
                  className="chip"
                  to={
                    projectId
                      ? `/context?project=${encodeURIComponent(projectId)}`
                      : "/context"
                  }
                >
                  agent context (≠ authority)
                </Link>
                <Link
                  className="chip"
                  to={
                    projectId
                      ? `/ask?project=${encodeURIComponent(projectId)}`
                      : "/ask"
                  }
                >
                  ask (≠ authority)
                </Link>
                <Link
                  className="chip"
                  to={
                    projectId
                      ? `/time-machine?project=${encodeURIComponent(projectId)}`
                      : "/time-machine"
                  }
                >
                  history / Time Machine
                </Link>
                <Link className="chip" to="/graph">
                  graph (≠ authority)
                </Link>
                <Link className="chip" to="/ops">
                  ops receipts
                </Link>
              </p>
            </section>
          </>
        ) : null}

        <section className="panel" aria-label="Knowledge answers">
          <h2>Answer inventory</h2>
          {!knowledgeLoading && knowledge.length === 0 ? (
            <p className="banner warn">unknown — no knowledge rows for project</p>
          ) : (
            <ul className="theme-hub">
              {knowledge.map((row, index) => (
                <li key={String(row.answer_id ?? row.subject ?? index)}>
                  <strong>
                    {String(row.field ?? row.title ?? row.answer_id ?? "row")}
                  </strong>
                  <span>{String(row.summary ?? row.value_text ?? "")}</span>
                </li>
              ))}
            </ul>
          )}
          <p className="disclaimer">
            UI ≠ canonical · Graph ≠ authority · knowledge_compiler never imported
            {isDemo ? " · demo isolated from LIVE_API" : " · LIVE_API read-only"}
            {brief?.brief_path ? ` · ${brief.brief_path}` : ""}
          </p>
        </section>
      </main>
    </ProdShell>
  );
}
