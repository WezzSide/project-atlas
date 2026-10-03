import { useEffect, useState } from "react";
import { liveApiDemoOnly, liveApiFetch } from "../api/liveApi";
import type { DataSource } from "../types";

/** LIVE_API ops receipt inventory; demo fallback is honest unknown (no invent). */

export interface OpsReceiptRow {
  kind?: string;
  name?: string;
  relative_path?: string;
  bytes?: number;
  package_id?: string;
  parse?: string;
}

export interface OpsReceiptInventory {
  receipt_source: string;
  /** Backend-reported ops root status (already in /v1/ops/receipts). */
  ops_root?: string;
  receipt_rows: number;
  receipts: OpsReceiptRow[];
  available: boolean;
  rollup: string;
  completion_claimed: boolean;
  data_source?: DataSource;
  demo_isolated?: boolean;
}

const EMPTY_UNKNOWN: OpsReceiptInventory = {
  receipt_source: "unavailable",
  receipt_rows: 0,
  receipts: [],
  available: false,
  rollup: "unknown",
  completion_claimed: false,
  data_source: "demo_stub",
  demo_isolated: true,
};

export function useOpsReceipts(): {
  inventory: OpsReceiptInventory | null;
  error: string | null;
  loading: boolean;
  dataSource: DataSource | null;
} {
  const [inventory, setInventory] = useState<OpsReceiptInventory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [dataSource, setDataSource] = useState<DataSource | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    async function load(): Promise<void> {
      if (!liveApiDemoOnly()) {
        try {
          const resp = await liveApiFetch("/v1/ops/receipts?limit=50");
          if (resp.ok) {
            const body = (await resp.json()) as OpsReceiptInventory;
            if (!cancelled) {
              setInventory({
                ...body,
                data_source: "live_api",
                demo_isolated: false,
                completion_claimed: false,
              });
              setDataSource("live_api");
              setError(null);
            }
            return;
          }
          if (!cancelled) {
            setError(`ops receipts HTTP ${resp.status}`);
            setInventory(null);
            setDataSource(null);
          }
          return;
        } catch (err: unknown) {
          if (!cancelled) {
            setError(
              err instanceof Error ? err.message : "ops receipts load failed",
            );
            setInventory(null);
            setDataSource(null);
          }
          return;
        }
      }
      if (!cancelled) {
        setInventory(EMPTY_UNKNOWN);
        setDataSource("demo_stub");
        setError(null);
      }
    }

    load()
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "ops receipts load failed");
          setInventory(null);
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
  }, []);

  return { inventory, error, loading, dataSource };
}
