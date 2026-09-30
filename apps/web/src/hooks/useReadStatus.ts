import { useEffect, useState } from "react";
import {
  LiveApiAuthError,
  liveApiDemoOnly,
  liveApiFetch,
} from "../api/liveApi";
import { ANNOUNCEMENTS, announce } from "../lib/announce";
import type { DataSource, ReadStatus } from "../types";

/** AS-2.1-WEB-LIVE-001 deepen: LIVE_API first; demo stub isolated + labelled. */
const STUB_URL = "/sample-read-status.json";

function stampDemo(status: ReadStatus): ReadStatus {
  return {
    ...status,
    read_plane: "stub",
    data_source: "demo_stub",
    demo_isolated: true,
    ui_canonical: false,
    graph_authority: false,
    unknown_equals_healthy: false,
  };
}

function stampLive(status: ReadStatus): ReadStatus {
  return {
    ...status,
    data_source: "live_api",
    demo_isolated: false,
    ui_canonical: false,
    graph_authority: false,
    unknown_equals_healthy: false,
  };
}

interface LoadResult {
  status: ReadStatus;
  source: DataSource;
  /**
   * Set only when LIVE_API was preferred but could not be reached/parsed, so
   * the demo stub shown below is a *fallback*, not a chosen demo. Keeps the
   * "no silent stub fallback labelled LIVE" invariant honest for the UI.
   */
  liveError: string | null;
}

async function loadLiveOrStub(): Promise<LoadResult> {
  let liveError: string | null = null;
  if (!liveApiDemoOnly()) {
    try {
      const live = await liveApiFetch("/v1/snapshot");
      if (live.ok) {
        const snap = (await live.json()) as {
          health?: { read_status?: ReadStatus };
        };
        const status = snap.health?.read_status;
        if (status && typeof status === "object") {
          return { status: stampLive(status), source: "live_api", liveError: null };
        }
        liveError = "LIVE_API snapshot missing read_status";
      } else {
        liveError = `LIVE_API HTTP ${live.status}`;
      }
    } catch (err: unknown) {
      if (err instanceof LiveApiAuthError) {
        // Missing per-launch Bearer is not a silent stub fallback — fail closed.
        throw err;
      }
      // Unreachable / CORS-blocked LIVE_API: fall back to isolated demo stub,
      // but remember *why* so the UI can label it as a fallback (not chosen).
      liveError = err instanceof Error ? err.message : "LIVE_API unreachable";
    }
  }
  const response = await fetch(STUB_URL);
  if (!response.ok) {
    throw new Error(`demo stub HTTP ${response.status}`);
  }
  const stub = (await response.json()) as ReadStatus;
  return { status: stampDemo(stub), source: "demo_stub", liveError };
}

export function useReadStatus(): {
  status: ReadStatus | null;
  error: string | null;
  loading: boolean;
  dataSource: DataSource | null;
  livePreferred: boolean;
  /** Reason LIVE_API was unreachable when a demo *fallback* is shown, else null. */
  liveError: string | null;
} {
  const [status, setStatus] = useState<ReadStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [dataSource, setDataSource] = useState<DataSource | null>(null);
  const [liveError, setLiveError] = useState<string | null>(null);
  const livePreferred = !liveApiDemoOnly();

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loadLiveOrStub()
      .then(({ status: payload, source, liveError: reason }) => {
        if (!cancelled) {
          setStatus(payload);
          setDataSource(source);
          setLiveError(reason);
          setError(null);
          // AX-003 / SC 4.1.3. A LIVE -> DEMO fallback is announced assertively
          // because it changes what the data *means*: without it a screen-reader
          // user can read fixture data believing it is live. Routine loads stay
          // polite so they do not interrupt the task in hand.
          if (source === "demo_stub" && livePreferred && reason !== null) {
            announce(ANNOUNCEMENTS.fellBackToDemo, "assertive");
          } else if (source === "demo_stub") {
            announce(ANNOUNCEMENTS.loadedDemo, "polite");
          } else {
            announce(ANNOUNCEMENTS.loadedLive, "polite");
          }
          if (payload.health?.rollup === "unknown") {
            announce(ANNOUNCEMENTS.rollupUnknown, "polite");
          }
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const reason = err instanceof Error ? err.message : "status load failed";
          setError(reason);
          setStatus(null);
          setDataSource(null);
          setLiveError(null);
          announce(ANNOUNCEMENTS.readFailed(reason), "assertive");
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
  }, [livePreferred]);

  return { status, error, loading, dataSource, livePreferred, liveError };
}
