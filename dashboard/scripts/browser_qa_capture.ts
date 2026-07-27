import { join } from "node:path";
import type { Page } from "playwright";
import type { PublishedState } from "../tests/e2e/workstation_shell_fixture";
import {
  analyzeAtScrollPositions,
  requireEqual,
  requirePositive,
  resetScrollableContent,
} from "./browser_qa_support";

export type MatrixState = PublishedState | "loading";

export type BrowserFinding = {
  readonly width: number;
  readonly route: string;
  readonly state: MatrixState;
  readonly control: "pending-snapshot-route-gate" | "published-snapshot";
  readonly routeActive: boolean;
  readonly stateRendered: boolean;
  readonly traceAssertion: "absent-during-loading" | "opened-and-returned-focus";
  readonly scrollPositions: number;
  readonly axeViolations: number;
  readonly axeIncomplete: number;
  readonly axeViolationKeys: readonly string[];
  readonly axeIncompleteKeys: readonly string[];
  readonly leakHits: number;
  readonly pageOverflow: boolean;
  readonly screenshot: string;
};

export async function captureMatrixCase(
  page: Page,
  width: number,
  route: string,
  state: MatrixState,
  screenshotDirectory: string,
): Promise<BrowserFinding> {
  const activeLink = page.locator(`[data-workspace-link="${route}"]`);
  const routeActive = (await activeLink.getAttribute("aria-current")) === "page";
  requireEqual(routeActive, true, `${width}/${route}/${state} active route`);
  const stateRendered = await verifyRenderedState(page, state);
  const traceAssertion = await verifyTrace(page, state);
  const screenshot = join(screenshotDirectory, `${width}-${route}-${state}.png`);
  await page.screenshot({ path: screenshot });
  const axe = await analyzeAtScrollPositions(page);
  requireEqual(
    axe.violations,
    0,
    `${width}/${route}/${state} axe violations ${axe.violationKeys.join(",")}`,
  );
  requireEqual(
    axe.incomplete,
    0,
    `${width}/${route}/${state} axe incomplete ${axe.incompleteKeys.join(",")}`,
  );
  await resetScrollableContent(page);
  const pageOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  );
  const leakHits = await browserLeakHits(page);
  return {
    width,
    route,
    state,
    control: state === "loading" ? "pending-snapshot-route-gate" : "published-snapshot",
    routeActive,
    stateRendered,
    traceAssertion,
    scrollPositions: axe.scrollPositions,
    axeViolations: axe.violations,
    axeIncomplete: axe.incomplete,
    axeViolationKeys: axe.violationKeys,
    axeIncompleteKeys: axe.incompleteKeys,
    leakHits,
    pageOverflow,
    screenshot,
  };
}

async function verifyRenderedState(page: Page, state: MatrixState): Promise<boolean> {
  if (state === "loading") {
    const busy = (await page.locator("#workspace-content").getAttribute("aria-busy")) === "true";
    requireEqual(busy, true, "loading aria-busy");
    return busy;
  }
  const statePanels = page.locator(`[data-source-state="${state}"]`);
  const count = await statePanels.count();
  requirePositive(count, `${state} rendered state panels`);
  return count > 0;
}

async function verifyTrace(
  page: Page,
  state: MatrixState,
): Promise<BrowserFinding["traceAssertion"]> {
  const traceButtons = page.locator(".trace-button:not([disabled])");
  if (state === "loading") {
    requireEqual(await traceButtons.count(), 0, "loading trace button count");
    return "absent-during-loading";
  }
  requirePositive(await traceButtons.count(), `${state} trace button count`);
  const invoker = traceButtons.first();
  await invoker.click();
  const dialog = page.locator("#evidence-trace-dialog");
  await dialog.waitFor({ state: "visible" });
  await dialog.press("Escape");
  await dialog.waitFor({ state: "hidden" });
  const focusReturned = await invoker.evaluate((element) => document.activeElement === element);
  requireEqual(focusReturned, true, `${state} trace focus return`);
  return "opened-and-returned-focus";
}

async function browserLeakHits(page: Page): Promise<number> {
  return page.evaluate(() => {
    const serialized = [
      document.documentElement.outerHTML,
      ...Object.values(localStorage),
      ...Object.values(sessionStorage),
    ].join("\n");
    return [
      /\bauthorization\b/i,
      /\bbearer\s+[a-z0-9._-]+/i,
      /\bapi[_-]?key\b/i,
      /\bsession[_-]?id\b/i,
      /\braw[_-]?payload\b/i,
      /\/Users\//,
      /[A-Za-z]:\\Users\\/,
      /\baccount[_ -]?(number|fingerprint)\b/i,
    ].filter((pattern) => pattern.test(serialized)).length;
  });
}
