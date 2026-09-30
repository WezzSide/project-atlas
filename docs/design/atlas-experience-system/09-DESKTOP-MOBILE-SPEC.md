# 09 — Desktop and Mobile Specification

**Session:** `AS-20260903T132018Z-claude-design-continuation-20260903-project-atlas-60ba98bb`
**Status:** desktop `RECOMMENDED-AGAINST` (with conditions) · mobile `SPECIFIED` (responsive web)

> **Truth boundary.** The audit found **no** Electron, Tauri, pywebview, React Native,
> Capacitor or Xcode project anywhere in the repository; `apps/` contains only `web/`.
> Atlas has no desktop application and no native mobile application. This document
> specifies a responsive model and records a recommendation. Neither may be read as
> describing shipped software.

---

## Part A — Desktop

### A.1 Recommendation: do not build a desktop app now

The directive asks for an implementation-ready desktop model "only where justified by the
directive." Examined honestly, it is not yet justified, and saying so is more useful than
producing a specification that invites premature work.

**What a desktop shell would add over the browser:** OS-native file access for local
vaults, a tray presence for long-running agent work, deep links (`atlas://`), offline
launch, and native notifications for `OWNER_REQUIRED` transitions.

**What it costs:** a second distribution channel with code signing on three platforms, an
auto-update path, a new security review surface — against a project whose stated posture is
`EXTERNAL_SECURITY_REVALIDATION_REQUIRED = YES`. A desktop shell that bundles a local
server materially widens the attack surface, and would need its own security
certification.

**Decisive point:** Atlas already serves the desktop-shaped need. `atlas live api-serve`
binds `127.0.0.1`, the vault is already local, and the browser already reads it. A desktop
app would mostly re-wrap a local-first tool that is already local-first. The genuinely
missing capability is *notification of owner-gated work* — and that is better served by
the TUI (`08`) or a small notifier than by an Electron shell.

### A.2 Conditions under which to revisit

Reconsider when **at least two** hold:

1. Non-technical operators are a real user group — people who will not run a terminal
   command to start a server.
2. Owner-gate latency becomes a measured problem (owners not noticing `OWNER_REQUIRED`).
3. Multi-vault switching becomes routine enough that OS-level file handling pays for itself.
4. External security revalidation has completed, so a new attack surface is assessable.

### A.3 If it is built, the shape

- **Tauri, not Electron** — smaller footprint and a narrower attack surface, which matters
  given the security posture above.
- **A shell around the existing web app**, not a re-implementation. It supervises
  `atlas live api-serve`, provides the token (never hardcoded — the existing SEC-009
  per-launch Bearer flow is preserved exactly), and handles OS integration.
- **Tray states use the `05` vocabulary**: `◆ LIVE`, `⚿ n owner-gated`, `? UNKNOWN`.
- **Native notifications only for `OWNER_REQUIRED` and `FAILED`.** Nothing else earns an
  interrupt.
- **The notification is not an action.** It opens the surface that displays the gate. Even
  in a native shell, `READ_ONLY_UI != EXECUTION_AUTHORITY` holds — a notification that
  approved something from the OS tray would breach it.

### A.4 Desktop-class browser behaviour (which *is* in scope now)

Independently of any app shell, the web app should use large viewports properly — the
audit found `--atlas-max: 42rem` applied to 400-line operator tables (A-3):

| Viewport | Behaviour |
|---|---|
| ≥ 1600px | Rail + content + pinned evidence drawer, three columns |
| 1200–1599px | Rail + content; evidence drawer overlays on demand |
| 900–1199px | Rail collapses to icons + labels on hover/focus; single content column |

Measure policy, resolving A-3: **prose keeps a `--atlas-max-prose: 42rem` measure; tables
and comparisons get `--atlas-max-data: 96rem`.** One token becomes two, which is the whole
fix — a record should read like a record and a matrix should not be squeezed into a column.

## Part B — Mobile

### B.1 Position: responsive web, no native app

Atlas is local-first and reads a vault on the operator's machine. A native mobile app
would need a remote service, which contradicts the product's core stance. Mobile is
therefore a **responsive presentation of the same web surface**, and no native mobile
application is planned or claimed.

Realistic mobile jobs are the reading ones — J-8 ("what needs me"), J-2 ("what is
contested"), J-7 ("what is running"). The pipeline jobs (J-11) are desktop/CLI work and
should not be attempted on a phone.

### B.2 Breakpoints

The audit found exactly **one** media query for the entire console (A-7). Specified:

| Token | Range | Behaviour |
|---|---|---|
| `--bp-compact` | ≤ 480px | Single column; area rail → bottom tab bar (5 areas); tables → card list |
| `--bp-narrow` | 481–720px | Single column; rail → horizontal scroller with a visible overflow affordance |
| `--bp-medium` | 721–1199px | Rail visible; tables scroll inside their own container |
| `--bp-wide` | ≥ 1200px | Full Evidence Desk layout |

### B.3 Table → card transformation

Operator tables need a declared column-priority order rather than an arbitrary crop. For a
claims table:

1. Truth state (**never dropped**)
2. Claim subject
3. Source
4. Valid-time
5. Ingested-at

At `≤ 480px` each row becomes a card carrying priorities 1–3, with 4–5 behind a disclosure.
**Priority 1 is never dropped at any width** — a claim rendered without its truth state
would breach `05` Rule 1 exactly where space pressure is highest.

### B.4 Mobile rules that are non-negotiable

- **Truth chips keep their text label at every width.** They must never degrade to a bare
  colour dot. This is enforced by an automated test at 360/480/768/1440px, already passing
  (`e2e/evidence-desk.a11y.spec.ts`).
- **The read-plane indicator stays visible** in the compact header. Plane is the one thing
  a small screen may not drop, since `DEMO != LIVE` is the invariant most likely to mislead
  on a glance-sized surface.
- **No horizontal page scroll at any breakpoint.** Wide content scrolls inside its own
  container. Verified at four viewports.
- **Touch targets ≥ 44×44px** for interactive controls (WCAG 2.2 SC 2.5.8 Target Size
  (Minimum) is 24×24; 44 is the comfortable standard and the one to design to).
- **Zoom/reflow to 320px at 400%** without loss of content (SC 1.4.10).

### B.5 Validation status

| Check | Status | Evidence |
|---|---|---|
| No horizontal overflow at 360/480/768/1440 | **PASS** | `e2e/evidence-desk.a11y.spec.ts` |
| Truth chips keep labels at all four widths | **PASS** | same |
| Card transformation | `SPECIFIED` | not implemented — `AX-005` |
| Bottom tab bar | `SPECIFIED` | not implemented — `AX-005` |
| Touch target audit | **NOT DONE** | recorded as an open gap in `11-HANDOFF.md` |
| 400% zoom reflow | **NOT DONE** | recorded as an open gap in `11-HANDOFF.md` |

The last two are stated as not done rather than assumed. They are real remaining work.
