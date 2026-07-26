import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { parseArgs } from "node:util";
import ky from "ky";
import { chromium, type Page } from "playwright";
import { WORKSPACES } from "../src/workspace_registry";
import {
  type PublishedState,
  workstationStateFixture,
} from "../tests/e2e/workstation_shell_fixture";
import {
  analyzeAtScrollPositions,
  parseWidths,
  requiredEnvironment,
  requiredOption,
  requireEqual,
  requirePositive,
  resetScrollableContent,
} from "./browser_qa_support";

type BrowserFinding = {
  readonly width: number;
  readonly route: string;
  readonly state: PublishedState | "loading" | "showcase";
  readonly axeViolations: number;
  readonly axeIncomplete: number;
  readonly leakHits: number;
  readonly pageOverflow: boolean;
  readonly bottomScreenshot: string;
  readonly screenshot: string;
};

type BrowserQaReport = {
  readonly observable: "DASHBOARD_BROWSER_QA_OK";
  readonly browser: "chrome";
  readonly widths: readonly number[];
  readonly workspaces: readonly string[];
  readonly states: readonly string[];
  readonly findings: readonly BrowserFinding[];
  readonly keyboard: {
    readonly arrowRoute: string;
    readonly homeRoute: string;
    readonly escapeReturnedFocus: boolean;
  };
  readonly reducedMotion: {
    readonly movingElements: number;
  };
  readonly showcaseRows: number;
};

const PUBLISHED_STATES = [
  "empty",
  "error",
  "blocked",
  "unavailable",
  "corrupt",
  "stale",
  "populated",
] as const satisfies readonly PublishedState[];

const { values } = parseArgs({
  options: {
    "base-url": { type: "string", default: "http://127.0.0.1:3000" },
    output: { type: "string" },
    widths: { type: "string", default: "375,768,1280" },
  },
  strict: true,
});
const output = requiredOption(values.output, "--output");
const baseUrl = new URL(values["base-url"]).toString().replace(/\/$/, "");
const widths = parseWidths(values.widths);
const ingestToken = requiredEnvironment("DASHBOARD_INGEST_TOKEN");
const screenshotDirectory = join(dirname(output), "screenshots");
await mkdir(screenshotDirectory, { recursive: true });

const browser = await chromium.launch({ channel: "chrome", headless: true });
const context = await browser.newContext({ reducedMotion: "reduce" });
const page = await context.newPage();
const findings: BrowserFinding[] = [];
let generatedOffset = 0;

try {
  for (const width of widths) {
    await page.setViewportSize({ width, height: 900 });
    await captureLoading(page, width, findings);

    for (const state of PUBLISHED_STATES) {
      generatedOffset += 1;
      await publishFixture(state, generatedOffset);
      await page.goto(`${baseUrl}/#command-center`, { waitUntil: "networkidle" });
      await capture(page, width, "command-center", state, findings);
    }

    generatedOffset += 1;
    await publishFixture("populated", generatedOffset);
    for (const workspace of WORKSPACES) {
      await page.goto(`${baseUrl}/${workspace.hash}`, { waitUntil: "networkidle" });
      await capture(page, width, workspace.id, "populated", findings);
    }

    await page.goto(`${baseUrl}/showcase`, { waitUntil: "networkidle" });
    await capture(page, width, "showcase", "showcase", findings);
  }

  await page.setViewportSize({ width: 768, height: 900 });
  await page.goto(`${baseUrl}/#command-center`, { waitUntil: "networkidle" });
  const keyboard = await verifyKeyboardAndTrace(page);
  const movingElements = await countMovingElements(page);
  await page.goto(`${baseUrl}/showcase`, { waitUntil: "networkidle" });
  const showcaseRows = await page.locator("#stress-rows > tr").count();
  requireEqual(showcaseRows, 200, "showcase row count");

  requireEqual(findings.filter((finding) => finding.axeViolations > 0).length, 0, "axe violations");
  requireEqual(findings.filter((finding) => finding.axeIncomplete > 0).length, 0, "axe incomplete");
  requireEqual(
    findings.filter((finding) => finding.pageOverflow).length,
    0,
    "page overflow captures",
  );
  requireEqual(findings.filter((finding) => finding.leakHits > 0).length, 0, "DOM leak captures");
  requireEqual(movingElements, 0, "reduced-motion moving elements");

  const report: BrowserQaReport = {
    observable: "DASHBOARD_BROWSER_QA_OK",
    browser: "chrome",
    widths,
    workspaces: WORKSPACES.map((workspace) => workspace.id),
    states: ["loading", ...PUBLISHED_STATES],
    findings,
    keyboard,
    reducedMotion: { movingElements },
    showcaseRows,
  };
  await writeFile(output, `${JSON.stringify(report, null, 2)}\n`, { mode: 0o600 });
  console.log(
    `DASHBOARD_BROWSER_QA_OK widths=${widths.join(",")} workspaces=${WORKSPACES.length} states=8 axe=0 incomplete=0 overflow=0`,
  );
} finally {
  await context.close();
  await browser.close();
}

async function captureLoading(
  pageToCapture: Page,
  width: number,
  target: BrowserFinding[],
): Promise<void> {
  const loadingPage = await pageToCapture.context().newPage();
  await loadingPage.setViewportSize({ width, height: 900 });
  await loadingPage.route("**/api/snapshot", async () => {
    await new Promise<void>(() => undefined);
  });
  await loadingPage.goto(`${baseUrl}/#command-center`, { waitUntil: "domcontentloaded" });
  await loadingPage.locator("#workspace-heading").waitFor({ state: "visible" });
  await capture(loadingPage, width, "command-center", "loading", target);
  await loadingPage.close();
}

async function capture(
  pageToCapture: Page,
  width: number,
  route: string,
  state: BrowserFinding["state"],
  target: BrowserFinding[],
): Promise<void> {
  const axe = await analyzeAtScrollPositions(pageToCapture);
  const pageOverflow = await pageToCapture.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  );
  const leakHits = await pageToCapture.evaluate(() => {
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
  const screenshot = join(screenshotDirectory, `${width}-${route}-${state}.png`);
  const bottomScreenshot = join(screenshotDirectory, `${width}-${route}-${state}-bottom.png`);
  await pageToCapture.screenshot({ path: bottomScreenshot, fullPage: true });
  await resetScrollableContent(pageToCapture);
  await pageToCapture.screenshot({ path: screenshot, fullPage: true });
  target.push({
    width,
    route,
    state,
    axeViolations: axe.violations,
    axeIncomplete: axe.incomplete,
    leakHits,
    pageOverflow,
    bottomScreenshot,
    screenshot,
  });
}

async function publishFixture(state: PublishedState, offset: number): Promise<void> {
  const generatedAt = new Date(Date.now() + offset).toISOString();
  await ky.post(`${baseUrl}/api/ingest`, {
    headers: { authorization: `Bearer ${ingestToken}` },
    json: workstationStateFixture(state, generatedAt),
    retry: 0,
  });
}

async function verifyKeyboardAndTrace(pageToVerify: Page): Promise<BrowserQaReport["keyboard"]> {
  const commandLink = pageToVerify.locator('[data-workspace-link="command-center"]');
  await commandLink.focus();
  await commandLink.press("ArrowRight");
  const arrowRoute = new URL(pageToVerify.url()).hash;
  requireEqual(arrowRoute, "#overview", "ArrowRight route");
  const overviewLink = pageToVerify.locator('[data-workspace-link="overview"]');
  await overviewLink.press("Home");
  const homeRoute = new URL(pageToVerify.url()).hash;
  requireEqual(homeRoute, "#command-center", "Home route");
  const traceButtons = pageToVerify.locator("[data-trace-id]");
  const traceCount = await traceButtons.count();
  requirePositive(traceCount, "trace button count");
  const invoker = traceButtons.nth(0);
  await invoker.click();
  const dialog = pageToVerify.locator("#evidence-trace-dialog");
  await dialog.press("Escape");
  await dialog.waitFor({ state: "hidden" });
  const escapeReturnedFocus = await invoker.evaluate(
    (element) => document.activeElement === element,
  );
  requireEqual(escapeReturnedFocus, true, "Evidence Trace focus return");
  return { arrowRoute, homeRoute, escapeReturnedFocus };
}

async function countMovingElements(pageToInspect: Page): Promise<number> {
  return pageToInspect.evaluate(
    () =>
      [...document.querySelectorAll("*")].filter((element) => {
        const style = getComputedStyle(element);
        return (
          style.animationDuration.split(",").some((value) => value.trim() !== "0s") ||
          style.transitionDuration.split(",").some((value) => value.trim() !== "0s")
        );
      }).length,
  );
}
