import { ProdShell } from "../../components/ProdShell";
import { useOpsReceipts } from "../../hooks/useOpsReceipts";
import { useReadStatus } from "../../hooks/useReadStatus";
import { countOrUnknown } from "../../lib/missingState";

/**
 * Ops Health micro-lens — AS-WEB-OPS-HEALTH-001.
 * LIVE_API health + ops receipt inventory; never fabricates completion/PILOT.
 */
export default function OpsHealthPage() {
  const { status, error, loading, dataSource } = useReadStatus();
  const {
    inventory,
    error: receiptError,
    loading: receiptLoading,
    dataSource: receiptSource,
  } = useOpsReceipts();
  const health = status?.health;
  const available = health?.available === true;
  const rollup = available ? health?.rollup ?? "unknown" : "unknown";
  const isDemo = dataSource === "demo_stub" || status?.demo_isolated === true;
  const receiptsDemo =
    receiptSource === "demo_stub" || inventory?.demo_isolated === true;
  const receiptAvailable = inventory?.available === true;
  const receiptRows = inventory?.receipts ?? [];

  return (
    <ProdShell>
      <main id="main" className="shell" tabIndex={-1}>
        <header className="hero">
          <p className="eyebrow">
            Production · Ops Health · AS-WEB-OPS-HEALTH-001
          </p>
          <h1>Ops health</h1>
          <p className="lede">
            Read-only operational health and receipt inventory. Absent evidence
            stays unknown; the browser never fabricates completion, PILOT, or
            release certification.
          </p>
          <p className="flags" style={{ marginTop: "0.75rem" }}>
            <span className="chip">ui_canonical=false</span>
            <span className="chip">graph_authority=false</span>
            <span className="chip">unknown≠healthy</span>
            <span className="chip">data_source={dataSource ?? "unknown"}</span>
          </p>
        </header>

        {error ? <p className="banner warn">Health read failed: {error}</p> : null}
        {loading ? <p className="banner">Loading…</p> : null}
        {isDemo ? (
          <p className="banner warn">DEMO STUB — isolated sample data · not live vault</p>
        ) : dataSource === "live_api" ? (
          <p className="banner">LIVE_API — read-only vault projection</p>
        ) : null}

        <section className="panel" aria-label="Health rollup">
          <h2>Rollup</h2>
          <p className="flags">
            <span className="chip">available={String(available)}</span>
            <span className="chip">rollup={rollup}</span>
            <span className="chip">read_plane={status?.read_plane ?? "unknown"}</span>
          </p>
          {!available ? (
            <p className="banner warn">unknown — OBS / health unavailable</p>
          ) : (
            <p>
              Operational rollup <strong>{rollup}</strong> (ops plane only — not
              project authority).
            </p>
          )}
          <p className="disclaimer">
            Unknown ≠ healthy · UI ≠ canonical · operational rollup ≠ project
            authority
            {isDemo ? " · demo isolated" : ""}
          </p>
        </section>

        <section className="panel" aria-label="Receipt evidence">
          <h2>Receipt evidence</h2>
          {receiptError ? (
            <p className="banner warn">Receipt adapter error: {receiptError}</p>
          ) : null}
          {receiptLoading ? <p className="banner">Loading receipts…</p> : null}
          {receiptsDemo ? (
            <p className="banner warn">
              DEMO STUB — receipt inventory unavailable offline; not inferred from UI
            </p>
          ) : null}
          {/* STYLE-002: only a successful LIVE read may claim an empty disk. */}
          {!receiptLoading && !receiptAvailable ? (
            receiptError || !inventory ? (
              <p className="banner warn">
                unavailable — receipt inventory could not be read; whether ops
                receipts exist is unknown (not an empty result)
              </p>
            ) : receiptsDemo ? (
              <p className="banner warn">
                unknown — receipt inventory not read (demo stub, no LIVE_API);
                not an empty result
              </p>
            ) : inventory.ops_root === "unknown" ? (
              <p className="banner warn">
                unknown — generated/ops could not be inspected by LIVE_API
                (ops_root=unknown); not an empty result
              </p>
            ) : (
              <p className="banner warn">
                unknown — LIVE_API found no ops receipts on disk under
                generated/ops (honest empty)
              </p>
            )
          ) : null}
          {receiptAvailable ? (
            <ul className="theme-hub">
              {receiptRows.slice(0, 20).map((row) => (
                <li key={row.relative_path ?? row.name}>
                  <strong>{row.kind ?? "ops"}</strong>
                  <span>
                    {row.name ?? "receipt"}
                    {row.package_id ? ` · ${row.package_id}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
          <p className="flags">
            <span className="chip">
              receipt_source={inventory?.receipt_source ?? "unavailable"}
            </span>
            <span className="chip">
              receipt_rows={receiptsDemo ? "unknown" : countOrUnknown(inventory?.receipt_rows)}
            </span>
            <span className="chip">
              completion_claimed=false (UI policy — browser never claims completion)
            </span>
            <span className="chip">read_only=true</span>
          </p>
          <p className="disclaimer">
            Inventory only · never completion claim · never PILOT PASS · never release cert
          </p>
        </section>

        <section className="panel" aria-label="Ops Health boundaries">
          <h2>Boundaries</h2>
          <p className="banner warn">UI ≠ canonical — browser state is never vault truth.</p>
          <p className="banner warn">Graph ≠ authority — derived edges never pick winners.</p>
          <p className="banner warn">No PILOT estate rows are invented.</p>
          <p className="disclaimer">
            Read-only lens · no vault mutation APIs · WEB APPLICATION ACCEPTED = YES · UI ≠ canonical
          </p>
        </section>
      </main>
    </ProdShell>
  );
}
