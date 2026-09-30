import { FormEvent, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ProdShell } from "../../components/ProdShell";
import { TruthChip } from "../../components/TruthChip";
import { useLiveAsk } from "../../hooks/useLiveAsk";
import { ANNOUNCEMENTS, announce } from "../../lib/announce";

/**
 * AS-2.1-ASK-ATLAS-LIVE-001 / AS-2.0-WEB-ASK-001 web lens.
 * Read-only GET /v1/ask. ASK ≠ authority. UI ≠ canonical. UNKNOWN stays UNKNOWN.
 */
export default function AskPage() {
  const [params, setParams] = useSearchParams();
  const urlQuery = params.get("q") ?? "";
  const contextProject = (params.get("project") ?? "").trim();
  const [draft, setDraft] = useState(urlQuery);
  const { answer, error, loading, dataSource } = useLiveAsk(urlQuery || null);
  const isDemo = dataSource === "demo_stub";
  const projects = answer?.matches?.projects ?? [];
  const knowledge = answer?.matches?.knowledge ?? [];
  const healthHits = answer?.matches?.health_keywords ?? [];
  const noMatches =
    Boolean(urlQuery) &&
    !loading &&
    !error &&
    dataSource === "live_api" &&
    projects.length === 0 &&
    knowledge.length === 0 &&
    healthHits.length === 0;

  // AX-004: the primary input surface must announce its outcome. Ask has three
  // equally valid results — answered, UNKNOWN, and unavailable — and a
  // screen-reader user must be able to tell them apart without the visual
  // banner. UNKNOWN is announced as a result, not as an error.
  useEffect(() => {
    if (!urlQuery || loading) {
      return;
    }
    if (error) {
      announce(ANNOUNCEMENTS.readFailed(error), "assertive");
    } else if (noMatches) {
      announce("UNKNOWN — no matching live projections. Unknown is not an error.", "polite");
    } else {
      const total = projects.length + knowledge.length + healthHits.length;
      announce(`Answered. ${total} matching projections.`, "polite");
    }
  }, [urlQuery, loading, error, noMatches, projects.length, knowledge.length, healthHits.length]);

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    const next = draft.trim();
    const nextParams = new URLSearchParams(params);
    if (next) {
      nextParams.set("q", next);
    } else {
      nextParams.delete("q");
    }
    if (contextProject) {
      nextParams.set("project", contextProject);
    }
    setParams(nextParams, { replace: true });
  }

  return (
    <ProdShell>
      <main id="main" className="shell" tabIndex={-1}>
        <header className="hero">
          <p className="eyebrow">Production · Ask Atlas live</p>
          <h1>Ask Atlas</h1>
          <p className="lede">
            Read-only lexical ask over the live vault. Not a chat model. Not
            canonical truth. Not authority. UNKNOWN stays UNKNOWN.
          </p>
          <p className="flags" style={{ marginTop: "0.75rem" }}>
            <span className="chip">ui_canonical=false</span>
            <span className="chip">ask≠authority</span>
            <span className="chip">canonical_write=false</span>
            <span className="chip">data_source={dataSource ?? "unknown"}</span>
            {contextProject ? (
              <span className="chip">
                context_project={contextProject} (hint only — ask is vault-wide)
              </span>
            ) : null}
          </p>
        </header>

        <section className="panel" aria-label="Ask">
          <h2>Question</h2>
          <form onSubmit={onSubmit}>
            <label className="lede" htmlFor="ask-query">
              Query (max 256 characters)
            </label>
            {/*
              AX-004: the error is associated with the field via
              aria-describedby and flagged with aria-invalid, so an assistive
              technology user meets the failure at the input rather than having
              to hunt for a banner elsewhere on the page.
            */}
            <input
              id="ask-query"
              name="q"
              value={draft}
              maxLength={256}
              aria-describedby="ask-query-hint ask-query-error"
              aria-invalid={error ? true : undefined}
              onChange={(event) => setDraft(event.target.value)}
              style={{ display: "block", marginTop: "0.5rem", width: "100%", maxWidth: "40rem" }}
            />
            <p id="ask-query-hint" className="disclaimer">
              Read-only lexical search. UNKNOWN is a valid answer, not a failure.
            </p>
            {/*
              Present in the DOM whether or not there is an error, so the
              aria-describedby reference never dangles.
            */}
            <p id="ask-query-error" className={error ? "banner warn" : "sr-only"}>
              {error ? `Ask unavailable: ${error}` : ""}
            </p>
            <button type="submit" style={{ marginTop: "0.75rem" }}>
              Ask (read-only)
            </button>
          </form>
        </section>

        {loading ? <p className="banner">Loading…</p> : null}
        {isDemo ? (
          <p className="banner warn">
            <TruthChip state="demo" /> isolated — start{" "}
            <code>atlas live api-serve</code> to ask the live vault (not invented answers)
          </p>
        ) : null}
        {noMatches ? (
          <p className="banner warn">
            <TruthChip state="unknown" /> no matching live projections
          </p>
        ) : null}

        {urlQuery ? (
          <>
            <section className="panel" aria-label="Project matches">
              <h2>Projects</h2>
              {projects.length === 0 ? (
                <p className="banner warn">
                  <TruthChip state="unknown" /> no project matches
                </p>
              ) : (
                <ul className="theme-hub">
                  {projects.map((project, index) => (
                    <li key={`${project.project_id ?? "p"}-${index}`}>
                      <strong>
                        {project.project_id ?? "UNKNOWN"}
                        {contextProject &&
                        project.project_id === contextProject
                          ? " · context project"
                          : ""}
                      </strong>
                      <span>
                        {project.path ?? project.title ?? project.name ?? ""}
                      </span>
                      {project.project_id ? (
                        <span className="flags" style={{ marginTop: "0.5rem" }}>
                          <Link
                            className="chip"
                            to={`/knowledge?project=${encodeURIComponent(project.project_id)}`}
                          >
                            knowledge
                          </Link>
                          <Link
                            className="chip"
                            to={`/time-machine?project=${encodeURIComponent(project.project_id)}`}
                          >
                            time machine
                          </Link>
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="panel" aria-label="Knowledge matches">
              <h2>Knowledge</h2>
              {knowledge.length === 0 ? (
                <p className="banner warn">
                  <TruthChip state="unknown" /> no knowledge matches
                </p>
              ) : (
                <ul className="theme-hub">
                  {knowledge.map((row, index) => (
                    <li key={`${row.answer_id ?? row.subject ?? "k"}-${index}`}>
                      <strong>
                        {row.subject ?? row.answer_id ?? "UNKNOWN"}
                        {row.field ? ` · ${row.field}` : ""}
                      </strong>
                      <span>
                        {row.summary ?? row.value_text ?? row.title ?? ""}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="panel" aria-label="Health keyword matches">
              <h2>Health keywords</h2>
              {healthHits.length === 0 ? (
                <p className="banner warn">
                  <TruthChip state="unknown" /> no health keyword matches
                </p>
              ) : (
                <ul className="theme-hub">
                  {healthHits.map((token) => (
                    <li key={token}>
                      <strong>{token}</strong>
                      <span>lexical health token — not a health verdict</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        ) : (
          <p className="lede">
            Enter a query to search live projections. Idle is not UNKNOWN.
          </p>
        )}

        <p className="disclaimer">
          {answer?.truth_boundary ?? "ASK ATLAS LIVE != CANONICAL WRITE / UI!=TRUTH / != AUTHORITY"}
          {isDemo ? " · demo isolated from LIVE_API" : " · LIVE_API read-only"}
        </p>
      </main>
    </ProdShell>
  );
}
