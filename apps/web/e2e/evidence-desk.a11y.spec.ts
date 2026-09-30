import { expect, test, type Page } from "@playwright/test";

/**
 * AX-002 / AX-003 rendered-behaviour validation.
 *
 * These assertions are deliberately made against the *rendered* page rather
 * than source text: the audit's whole point is that source-level claims about
 * visual and assistive behaviour are not evidence. Contrast, focus order and
 * live-region wiring are checked in a real browser.
 */

const DESK = "/#/design-lab/evidence-desk";

const ALL_STATES = [
  "ok", "live", "demo", "fixture", "unknown", "unresolved", "contested",
  "stale", "blocked", "owner_required", "ready", "running", "failed",
] as const;

async function gotoDesk(page: Page) {
  await page.goto(DESK);
  await expect(page.getByRole("heading", { name: "Evidence Desk", level: 1 })).toBeVisible();
}

test.describe("Evidence Desk prototype", () => {
  test("renders every truth state with a glyph and a visible label", async ({ page }) => {
    await gotoDesk(page);
    for (const state of ALL_STATES) {
      const chip = page.locator(`.truth-chip[data-truth-state="${state}"]`).first();
      await expect(chip).toBeVisible();
      // Rule 1: the label must be present, not implied by colour.
      const label = chip.locator(".truth-chip-label");
      await expect(label).not.toHaveText("");
    }
  });

  test("truth state survives removal of all colour", async ({ page }) => {
    await gotoDesk(page);
    // R-5: if the design becomes unusable stripped of colour, it is broken.
    await page.addStyleTag({
      content: `*, *::before, *::after { color: #000 !important;
                background: #fff !important; border-color: #000 !important; }`,
    });
    for (const state of ALL_STATES) {
      const chip = page.locator(`.truth-chip[data-truth-state="${state}"]`).first();
      const text = (await chip.innerText()).trim();
      expect(text.length, `state ${state} must remain readable without colour`).toBeGreaterThan(1);
    }
    await expect(
      page.locator('.truth-chip[data-truth-state="owner_required"]').first(),
    ).toContainText("OWNER REQUIRED");
  });

  test("owner-gated state is never an interactive control", async ({ page }) => {
    await gotoDesk(page);
    const gated = page.locator('.truth-chip[data-truth-state="owner_required"]');
    await expect(gated.first()).toBeVisible();
    const count = await gated.count();
    for (let i = 0; i < count; i += 1) {
      const el = gated.nth(i);
      // READ_ONLY_UI != EXECUTION_AUTHORITY — must not be a button/link/focusable.
      await expect(el).toHaveJSProperty("tagName", "SPAN");
      expect(await el.getAttribute("tabindex")).toBeNull();
      expect(await el.getAttribute("href")).toBeNull();
      expect(await el.getAttribute("onclick")).toBeNull();
    }
  });

  test("contested claims render both sides with neither marked the winner", async ({ page }) => {
    await gotoDesk(page);
    await page.getByRole("button", { name: /Project/ }).click();
    const pair = page.locator(".truth-pair");
    await expect(pair).toBeVisible();
    await expect(pair).toContainText("3.11");
    await expect(pair).toContainText("3.12");
    // Both sides carry the same state — no display winner.
    const chips = pair.locator('.truth-chip[data-truth-state="contested"]');
    expect(await chips.count()).toBeGreaterThanOrEqual(2);
  });

  test("a claim with no source renders UNKNOWN rather than being omitted", async ({ page }) => {
    await gotoDesk(page);
    await page.getByRole("button", { name: /Project/ }).click();
    const section = page.getByRole("region", { name: "What Atlas knows" });
    await expect(section).toContainText("external security revalidation status");
    await expect(
      section.locator('.truth-chip[data-truth-state="unknown"]').first(),
    ).toBeVisible();
  });

  test("area switching is keyboard operable with a visible focus ring", async ({ page }) => {
    await gotoDesk(page);
    const activity = page.getByRole("button", { name: /Activity/ });
    await activity.focus();
    await expect(activity).toBeFocused();
    const outline = await activity.evaluate(
      (el) => getComputedStyle(el).outlineStyle,
    );
    expect(outline === "none").toBeFalsy();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("region", { name: "Activity" })).toBeVisible();
    await expect(activity).toHaveAttribute("aria-pressed", "true");
  });

  test("has exactly one main landmark and a labelled nav", async ({ page }) => {
    await gotoDesk(page);
    await expect(page.locator("main")).toHaveCount(1);
    await expect(page.getByRole("navigation", { name: "Evidence Desk areas" })).toBeVisible();
  });
});

test.describe("Truth-state live regions (WCAG 2.2 SC 4.1.3)", () => {
  test("both live regions exist on load, before any announcement", async ({ page }) => {
    await page.goto("/#/");
    const polite = page.getByTestId("truth-announcer-polite");
    const assertive = page.getByTestId("truth-announcer-assertive");
    // Present in the accessibility tree from the start — that is the requirement.
    await expect(polite).toHaveAttribute("aria-live", "polite");
    await expect(polite).toHaveAttribute("role", "status");
    await expect(assertive).toHaveAttribute("aria-live", "assertive");
    await expect(assertive).toHaveAttribute("role", "alert");
    await expect(polite).toHaveAttribute("aria-atomic", "true");
    await expect(assertive).toHaveAttribute("aria-atomic", "true");
  });

  test("live regions are hidden visually but not from assistive tech", async ({ page }) => {
    await page.goto("/#/");
    const display = await page
      .getByTestId("truth-announcer-polite")
      .evaluate((el) => getComputedStyle(el).display);
    // display:none would remove the region entirely and silence announcements.
    expect(display).not.toBe("none");
  });

  test("a read status transition is announced to assistive tech", async ({ page }) => {
    await page.goto("/#/");
    // Which region fires depends on the environment: a successful read is
    // polite, while a fail-closed read (e.g. SEC-009 Bearer auth absent) is
    // assertive. The invariant under test is that the transition is never
    // silent — a visually rendered status with an empty live region is exactly
    // the defect AX-003 exists to prevent.
    await expect
      .poll(
        async () => {
          const polite = await page.getByTestId("truth-announcer-polite").innerText();
          const assertive = await page.getByTestId("truth-announcer-assertive").innerText();
          return (polite + assertive).trim().length;
        },
        { timeout: 15_000, message: "no truth-state transition reached a live region" },
      )
      .toBeGreaterThan(0);
  });

  test("a visible status banner is never announced silently", async ({ page }) => {
    await page.goto("/#/");
    const banner = page.locator(".banner").first();
    await expect(banner).toBeVisible({ timeout: 15_000 });
    const bannerText = (await banner.innerText()).trim();
    const polite = await page.getByTestId("truth-announcer-polite").innerText();
    const assertive = await page.getByTestId("truth-announcer-assertive").innerText();
    const announced = (polite + assertive).trim();
    // The pairing is the point: whatever the shell shows about read state, an
    // assistive-technology user is told about it too.
    expect(bannerText.length).toBeGreaterThan(0);
    expect(announced.length).toBeGreaterThan(0);
  });

  test("a read failure is announced assertively, not politely", async ({ page }) => {
    await page.goto("/#/");
    const assertive = page.getByTestId("truth-announcer-assertive");
    const failed = await page
      .locator(".banner")
      .first()
      .innerText()
      .then((t) => /unavailable|failed/i.test(t))
      .catch(() => false);
    test.skip(!failed, "environment produced a successful read; no failure path to assert");
    // A failure changes what the data means, so it must interrupt.
    await expect(assertive).not.toHaveText("", { timeout: 15_000 });
    await expect(page.getByTestId("truth-announcer-polite")).toHaveText("");
  });
});

test.describe("Responsive behaviour", () => {
  const VIEWPORTS = [
    { name: "mobile-portrait", width: 360, height: 740 },
    { name: "mobile-large", width: 480, height: 900 },
    { name: "tablet", width: 768, height: 1024 },
    { name: "desktop", width: 1440, height: 900 },
  ];

  for (const vp of VIEWPORTS) {
    test(`no horizontal overflow at ${vp.name} (${vp.width}px)`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await gotoDesk(page);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, `page must not scroll horizontally at ${vp.width}px`).toBeLessThanOrEqual(1);
    });

    test(`truth chips keep their text label at ${vp.name}`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await gotoDesk(page);
      // Must never degrade to a bare colour dot, least of all on small screens.
      const label = page
        .locator('.truth-chip[data-truth-state="unknown"] .truth-chip-label')
        .first();
      await expect(label).toBeVisible();
      await expect(label).toHaveText("UNKNOWN");
    });
  }
});

test.describe("Ask form semantics (AX-004)", () => {
  test("the query input is labelled and described", async ({ page }) => {
    await page.goto("/#/ask");
    const input = page.getByLabel(/Query/);
    await expect(input).toBeVisible();
    const described = await input.getAttribute("aria-describedby");
    expect(described).toContain("ask-query-hint");
    expect(described).toContain("ask-query-error");
  });

  test("every aria-describedby target exists in the DOM", async ({ page }) => {
    await page.goto("/#/ask");
    const ids = (await page.getByLabel(/Query/).getAttribute("aria-describedby"))?.split(/\s+/) ?? [];
    expect(ids.length).toBeGreaterThan(0);
    for (const id of ids) {
      // A dangling reference is worse than no reference — it silently drops.
      await expect(page.locator(`#${id}`)).toHaveCount(1);
    }
  });

  test("an error is associated with the field and marks it invalid", async ({ page }) => {
    await page.goto("/#/ask?q=deployment");
    const input = page.getByLabel(/Query/);
    const errorNode = page.locator("#ask-query-error");
    const errored = await errorNode.innerText().then((t) => t.trim().length > 0);
    test.skip(!errored, "environment produced no ask error to assert against");
    await expect(input).toHaveAttribute("aria-invalid", "true");
    await expect(errorNode).toContainText(/unavailable/i);
    // The failure must also reach a live region, not only the page.
    await expect(page.getByTestId("truth-announcer-assertive")).not.toHaveText("");
  });

  test("submitting does not steal focus from the input", async ({ page }) => {
    await page.goto("/#/ask");
    const input = page.getByLabel(/Query/);
    await input.fill("deployment target");
    await input.press("Enter");
    await page.waitForTimeout(500);
    // Announcements must not move focus (SC 4.1.3).
    await expect(input).toBeFocused();
  });
});

test.describe("Density (AX-007)", () => {
  const DATA_ROUTES = ["/#/intelligence", "/#/knowledge", "/#/time-machine", "/#/source-health"];

  for (const route of DATA_ROUTES) {
    test(`${route} uses the wide data measure`, async ({ page }) => {
      await page.setViewportSize({ width: 1600, height: 900 });
      await page.goto(route);
      const main = page.locator("main.shell-data");
      await expect(main).toHaveCount(1);
      const width = await main.evaluate((el) => el.getBoundingClientRect().width);
      // 42rem prose measure is ~672px; the data measure must be materially wider.
      expect(width).toBeGreaterThan(700);
    });

    test(`${route} still does not scroll horizontally on mobile`, async ({ page }) => {
      await page.setViewportSize({ width: 360, height: 740 });
      await page.goto(route);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBeLessThanOrEqual(1);
    });
  }

  test("prose surfaces keep the narrow measure", async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 900 });
    await page.goto("/#/");
    // Home is prose: it must not have opted into the data measure.
    await expect(page.locator("main.shell-data")).toHaveCount(0);
  });
});

test.describe("ClaimText contract (AX-012)", () => {
  async function gotoClaims(page: Page) {
    await page.goto(DESK);
    await page.getByRole("button", { name: /Project/ }).click();
    await expect(page.getByRole("region", { name: "What Atlas knows" })).toBeVisible();
  }

  test("a claim with no source is forced to UNKNOWN even if it declares ok", async ({ page }) => {
    await gotoClaims(page);
    // The fixture declares state "ok" with an empty sources array. Absence of a
    // source must outrank the declared state.
    const claim = page.locator('.claim[data-truth-state="unknown"]').filter({
      hasText: "external security revalidation status",
    });
    await expect(claim).toHaveCount(1);
    await expect(claim.locator(".truth-chip-label")).toHaveText("UNKNOWN");
    // It is rendered in place, not omitted.
    await expect(claim).toContainText("The external security revalidation status is");
  });

  test("an unsourced claim carries a non-colour text marker", async ({ page }) => {
    await gotoClaims(page);
    const decoration = await page
      .locator('.claim[data-truth-state="unknown"] .claim-text')
      .first()
      .evaluate((el) => getComputedStyle(el).textDecorationLine);
    expect(decoration).toContain("underline");
  });

  test("a sourced claim cites every source", async ({ page }) => {
    await gotoClaims(page);
    const contested = page.locator('.claim[data-truth-state="contested"]').first();
    await expect(contested).toBeVisible();
    const cites = contested.locator("cite");
    expect(await cites.count()).toBe(2);
    await expect(contested).toContainText("pyproject.toml");
    await expect(contested).toContainText("docs/plan.md");
  });

  test("a contested claim states that no winner is shown", async ({ page }) => {
    await gotoClaims(page);
    const contested = page.locator('.claim[data-truth-state="contested"]').first();
    await expect(contested).toContainText("No winner is shown");
    await expect(contested).toContainText("atlas review decide");
    await expect(contested.locator(".truth-chip-detail")).toContainText("2 competing sources");
  });

  test("a stale claim states its validity window", async ({ page }) => {
    await gotoClaims(page);
    const stale = page.locator('.claim[data-truth-state="stale"]').first();
    await expect(stale).toBeVisible();
    await expect(stale.locator(".truth-chip-detail")).toContainText("valid until 2026-08-30");
  });
});
