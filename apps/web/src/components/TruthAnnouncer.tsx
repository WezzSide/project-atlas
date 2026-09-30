import { useEffect, useState } from "react";
import { subscribeAnnouncements } from "../lib/announce";

/**
 * Live regions for truth-state transitions (AX-003, WCAG 2.2 SC 4.1.3).
 *
 * Both regions are mounted unconditionally and permanently — that is the whole
 * point. A live region added at the same moment as its content does not
 * reliably announce, so the shell keeps these present and empty until there is
 * something to say.
 *
 * Announcing must not move focus, so this renders no focusable node and is
 * visually hidden rather than display:none (which would remove it from the
 * accessibility tree entirely).
 */
export function TruthAnnouncer() {
  const [polite, setPolite] = useState("");
  const [assertive, setAssertive] = useState("");

  useEffect(() => {
    return subscribeAnnouncements((a) => {
      if (a.severity === "assertive") {
        setAssertive(a.message);
      } else {
        setPolite(a.message);
      }
    });
  }, []);

  return (
    <>
      <div
        className="sr-only"
        role="status"
        aria-live="polite"
        aria-atomic="true"
        data-testid="truth-announcer-polite"
      >
        {polite}
      </div>
      <div
        className="sr-only"
        role="alert"
        aria-live="assertive"
        aria-atomic="true"
        data-testid="truth-announcer-assertive"
      >
        {assertive}
      </div>
    </>
  );
}
