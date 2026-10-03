import { useSearchParams } from "react-router-dom";
import {
  LensModeSwitcher,
  LENS_MODES,
  resolveLensMode,
  type LensModeId,
} from "../../components/LensModeSwitcher";
import { ProdShell } from "../../components/ProdShell";
import {
  PILOT_UI_POLICY_NOTE,
  useLiveMission,
} from "../../hooks/useLiveMissionWorkspace";
import { boolOrUnknown, lengthOrUnknown } from "../../lib/missingState";

/**
 * Mission Control lens — AS-WEB-MISSION-001 / AS-2.1-WEB-MISSION-WORKSPACE-UX.
 * LIVE-first with visible DEMO / FIXTURE modes; never invents PILOT estate rows.
 * Exclusion: apps/web UI only — API server / shared schema roots untouched.
 */
export default function MissionControlPage() {
  const [params, setParams] = useSearchParams();
  const mode: LensModeId = resolveLensMode(params.get("mode"));
  const { view, error, loading, dataSource } = useLiveMission(mode);
  const active = LENS_MODES.find((item) => item.id === mode) ?? LENS_MODES[0];
  const surfaces =
    view && typeof view.surfaces === "object" && view.surfaces !== null
      ? (view.surfaces as Record<string, unknown>)
      : {};

  return (
    <ProdShell>
      <main id="main" className="shell" tabIndex={-1}>
        <header className="hero">
          <p className="eyebrow">Production · Mission Control · AS-2.1-WEB-MISSION-WORKSPACE-UX</p>
          <h1>Mission Control</h1>
          <p className="lede">
            Operator mission lens. LIVE-first composition with visible DEMO and
            FIXTURE modes. Never invents PILOT estate rows or elevates UI to vault
            truth.
          </p>
          <p className="flags" style={{ marginTop: "0.75rem" }}>
            <span className="chip">ui_canonical=false</span>
            <span className="chip">graph_authority=false</span>
            <span className="chip">unknown≠healthy</span>
            <span className="chip">authentic_pilot=false</span>
            <span className="chip">lens_mode={mode}</span>
            <span className="chip">data_source={dataSource ?? "unknown"}</span>
          </p>
        </header>

        <LensModeSwitcher
          mode={mode}
          ariaLabel="Mission Control data modes"
          onChange={(next) => setParams({ mode: next })}
        />
        <p className="disclaimer">{active.blurb}</p>

        {error ? <p className="banner warn">Mission unavailable: {error}</p> : null}
        {loading ? <p className="banner">Loading…</p> : null}
        {mode === "demo" || dataSource === "demo_stub" ? (
          <p className="banner warn">DEMO STUB — isolated sample · not live vault · not PILOT</p>
        ) : null}
        {mode === "fixture" || dataSource === "fixture" ? (
          <p className="banner warn">FIXTURE — deterministic sample · flags only · not PILOT</p>
        ) : null}
        {mode === "live" && dataSource === "live_api" ? (
          <p className="banner">LIVE_API — composed read projection</p>
        ) : null}

        <section className="panel" aria-label="Mission Control banners">
          <h2>Invariants</h2>
          <p className="banner warn">UI ≠ canonical — browser state is never vault truth.</p>
          <p className="banner warn">Graph ≠ authority — derived edges never pick winners.</p>
          <p className="banner warn">Unknown ≠ healthy — absent evidence stays unknown.</p>
        </section>

        <section className="panel" aria-label="Mission Control board">
          <h2>Mission board</h2>
          {!loading && !view ? (
            <p className="banner warn">unknown — mission view unavailable</p>
          ) : null}
          {view?.empty_projects === true ? (
            <p className="banner warn">unknown — no project rows (honest empty)</p>
          ) : null}
          {view ? (
            <dl className="grid">
              <div>
                <dt>Rollup</dt>
                <dd>{String(view.rollup ?? "unknown")}</dd>
              </div>
              <div>
                <dt>Project count</dt>
                <dd>{String(view.project_count ?? "unknown")}</dd>
              </div>
              <div>
                <dt>Board available</dt>
                <dd>{boolOrUnknown(view.mission_board_available)}</dd>
              </div>
              <div>
                <dt>PILOT estate rows</dt>
                <dd>
                  {lengthOrUnknown(view.pilot_estate_rows)} ({PILOT_UI_POLICY_NOTE})
                </dd>
              </div>
              <div>
                <dt>Authentic pilot</dt>
                <dd>
                  {boolOrUnknown(view.authentic_pilot)} ({PILOT_UI_POLICY_NOTE})
                </dd>
              </div>
            </dl>
          ) : null}
          <p className="disclaimer">
            UI ≠ canonical · Graph ≠ authority · Unknown ≠ healthy · no PILOT invent
            · WEB APPLICATION ACCEPTED = YES
            {mode === "demo"
              ? " · DEMO isolated"
              : mode === "fixture"
                ? " · FIXTURE sample"
                : " · LIVE-first read-only"}
          </p>
        </section>

        <section className="panel" aria-label="Mission surface presence">
          <h2>Surface presence</h2>
          <p className="flags">
            {Object.keys(surfaces).length === 0 ? (
              <span className="chip">surfaces=unknown</span>
            ) : (
              Object.entries(surfaces).map(([key, value]) => (
                <span className="chip" key={key}>
                  {key}={String(value)}
                </span>
              ))
            )}
          </p>
          <p className="disclaimer">
            Presence flags only · missing surface ≠ healthy · not release certification
          </p>
        </section>
      </main>
    </ProdShell>
  );
}
