import { NavLink } from "react-router-dom";

const LINKS = [
  { to: "/", label: "Home" },
  { to: "/design-lab/ledger-desk", label: "A · Ledger" },
  { to: "/design-lab/signal-rack", label: "B · Signal" },
  { to: "/design-lab/cartograph-quiet", label: "C · Cartograph" },
  { to: "/design-lab/terminal-honest", label: "D · Terminal" },
  // Prototype E — selected synthesis direction (AX-002). Additive: ADR-010
  // requires design-lab directions A–D to be preserved, never replaced.
  { to: "/design-lab/evidence-desk", label: "E · Evidence Desk" },
] as const;

/** Shared design-lab chrome — prototype nav only; not vault authority. */
export function LabNav() {
  return (
    <nav className="lab-nav" aria-label="Design lab">
      {LINKS.map((link) => (
        <NavLink key={link.to} to={link.to} end={link.to === "/"}>
          {link.label}
        </NavLink>
      ))}
      <span className="lab-badge">AS-WEB-002 · UI≠canonical · sample only</span>
    </nav>
  );
}
