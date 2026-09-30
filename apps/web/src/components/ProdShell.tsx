import type { MouseEvent, ReactNode } from "react";
import { ProdNav } from "./ProdNav";
import { TruthAnnouncer } from "./TruthAnnouncer";

type Props = {
  children: ReactNode;
  className?: string;
};

// Independent verification (D-ATLAS web a11y P2.1 re-check) found that
// `href="#main"` alone does not work: the app runs under `HashRouter`
// (main.tsx), which treats `#main` as a route change to a nonexistent
// path -- the wildcard route then redirects to "/", and the skip link
// silently navigates the keyboard user to the home page instead of
// moving focus into the current page's content. Confirmed on all 14
// production routes via real keyboard-activation tracing.
//
// Fixed by intercepting the click and moving focus directly, so the
// router never sees the hash change at all. Keyboard activation (Enter
// on a focused `<a>`) fires the same `click` event in every browser, so
// this handles keyboard and pointer activation identically -- no
// separate keydown handler is needed. `href="#main"` is kept as a
// no-JS/no-React fallback and for semantic correctness; `preventDefault`
// only stops the DEFAULT navigation, not the focus-transfer intent.
function focusMainContent(event: MouseEvent<HTMLAnchorElement>): void {
  event.preventDefault();
  document.getElementById("main")?.focus();
}

/** Production chrome wrapper — Ledger Desk lean tokens; not vault authority. */
export function ProdShell({ children, className = "" }: Props) {
  return (
    <div className={`prod-shell theme-ledger ${className}`} data-theme="ledger-desk">
      <a className="skip-link" href="#main" onClick={focusMainContent}>
        Skip to main content
      </a>
      {/*
        Mounted unconditionally and before any announcement: a live region added
        at the same moment as its content does not reliably announce (AX-003).
      */}
      <TruthAnnouncer />
      <ProdNav />
      {children}
    </div>
  );
}
