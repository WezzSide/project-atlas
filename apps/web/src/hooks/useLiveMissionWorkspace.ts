import { useEffect, useState } from "react";
import type { LensModeId } from "../components/LensModeSwitcher";
import {
  LiveApiAuthError,
  liveApiDemoOnly,
  liveApiFetch,
} from "../api/liveApi";
import type { DataSource } from "../types";

/**
 * AS-2.1-WEB-MISSION-WORKSPACE-UX — LIVE-first lens loader.
 * Explicit LIVE / DEMO / FIXTURE modes; never invents PILOT estate rows.
 * Exclusion: apps/web only — does not edit API server or shared schema roots.
 * SEC-009: LIVE path uses liveApiFetch (Bearer READ token).
 */

export interface LensView {
  rollup?: string;
  project_count?: number;
  knowledge_count?: number;
  mission_board_available?: boolean;
  workspace_board_available?: boolean;
  pilot_estate_rows?: unknown[];
  authentic_pilot?: boolean;
  data_source?: DataSource;
  demo_isolated?: boolean;
  fixture_isolated?: boolean;
  note?: string;
  [key: string]: unknown;
}

/**
 * STYLE-002: `pilot_estate_rows: []` and `authentic_pilot: false` below are
 * constants forced by this browser shell in every mode (LIVE / DEMO / FIXTURE),
 * not values read from the vault. Lenses must label them as UI policy so a
 * forced `0` / `false` is never mistaken for a measured backend result.
 */
export const PILOT_UI_POLICY_NOTE =
  "UI policy — forced by the browser shell, not read from the vault";

function stampDemo(stub: LensView): LensView {
  return {
    ...stub,
    data_source: "demo_stub",
    demo_isolated: true,
    fixture_isolated: false,
    authentic_pilot: false,
    pilot_estate_rows: [],
    ui_canonical: false,
    graph_authority: false,
    unknown_equals_healthy: false,
  };
}

function stampFixture(stub: LensView): LensView {
  return {
    ...stub,
    data_source: "fixture",
    fixture_isolated: true,
    demo_isolated: false,
    authentic_pilot: false,
    pilot_estate_rows: [],
    ui_canonical: false,
    graph_authority: false,
    unknown_equals_healthy: false,
  };
}

async function fetchJson(url: string): Promise<LensView> {
  const resp = await fetch(url);
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status} for ${url}`);
  }
  return (await resp.json()) as LensView;
}

async function loadLens(
  path: string,
  demoUrl: string,
  fixtureUrl: string,
  mode: LensModeId,
): Promise<{
  view: LensView;
  source: DataSource;
}> {
  const effective: LensModeId = liveApiDemoOnly() && mode === "live" ? "demo" : mode;

  if (effective === "fixture") {
    const stub = await fetchJson(fixtureUrl);
    return { view: stampFixture(stub), source: "fixture" };
  }

  if (effective === "demo") {
    const stub = await fetchJson(demoUrl);
    return { view: stampDemo(stub), source: "demo_stub" };
  }

  // LIVE-first: no silent demo fallback — unavailable stays unknown.
  try {
    const resp = await liveApiFetch(path);
    if (!resp.ok) {
      throw new Error(`LIVE_API HTTP ${resp.status}`);
    }
    const body = (await resp.json()) as LensView;
    return {
      view: {
        ...body,
        data_source: "live_api",
        demo_isolated: false,
        fixture_isolated: false,
        authentic_pilot: false,
        // Never surface invented PILOT estate rows from the browser shell.
        // Browser-forced constant — rendered with PILOT_UI_POLICY_NOTE.
        pilot_estate_rows: [],
      },
      source: "live_api",
    };
  } catch (err: unknown) {
    if (err instanceof LiveApiAuthError) {
      throw err;
    }
    const detail = err instanceof Error ? err.message : "LIVE_API unavailable";
    throw new Error(
      `${detail} — choose DEMO or FIXTURE mode (no silent invent)`,
    );
  }
}

function useLens(
  path: string,
  demoUrl: string,
  fixtureUrl: string,
  mode: LensModeId,
): {
  view: LensView | null;
  error: string | null;
  loading: boolean;
  dataSource: DataSource | null;
} {
  const [view, setView] = useState<LensView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [dataSource, setDataSource] = useState<DataSource | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loadLens(path, demoUrl, fixtureUrl, mode)
      .then(({ view: payload, source }) => {
        if (!cancelled) {
          setView(payload);
          setDataSource(source);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "lens load failed");
          setView(null);
          setDataSource(null);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [path, demoUrl, fixtureUrl, mode]);

  return { view, error, loading, dataSource };
}

export function useLiveMission(mode: LensModeId = "live") {
  return useLens(
    "/v1/mission",
    "/sample-mission-control.json",
    "/sample-mission-control.fixture.json",
    mode,
  );
}

export function useLiveWorkspace(mode: LensModeId = "live") {
  return useLens(
    "/v1/workspace",
    "/sample-workspace.json",
    "/sample-workspace.fixture.json",
    mode,
  );
}
