import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { parseArgs } from "node:util";
import ky from "ky";
import { chromium, type BrowserContext, type Page } from "playwright";
import { WORKSPACES } from "../src/workspace_registry";
import {
  type PublishedState,
  workstationStateFixture,
} from "../tests/e2e/workstation_shell_fixture";
import {
  type BrowserFinding,
  captureMatrixCase,
  type MatrixState,
} from "./browser_qa_capture";
import {
  analyzeAtScrollPositions,
  parseWidths,
  requiredEnvironment,
  requiredOption,
  requireEqual,
  requirePositive,
} from "./browser_qa_support";

type ShowcaseFinding = {
  readonly width: number;
  readonly axeViolations: number;
  readonly axeIncomplete: number;
  readonly pageOverflow: boolean;
  readonly screenshot: string;
};

type BrowserQaReport = {
  readonly observable: "DASHBOARD_BROWSER_QA_OK";
  readonly browser: "chrome";
  readonly expectedMatrixCases: 216;
  readonly widths: readonly number[];
  readonly workspaces: readonly string[];
  readonly states: readonly MatrixState[];
  readonly findings: readonly BrowserFinding[];
  readonly showcase: readonly ShowcaseFinding[];
  readonly keyboard: {
    readonly arrowRoute: string;
    readonly homeRoute: string;
    readonly escapeReturnedFocus: boolean;
  };
  readonly reducedMotion: { readonly movingElements: number };
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
const MATRIX_STATES = ["loading", ...PUBLISHED_STATES] as const;
const EXPECTED_MATRIX_CASES = 216;
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
const screenshotDirectory = join(dirname(output), "screenshots-exhaustive");
await mkdir(screenshotDirectory, { recursive: true });
const browser = await chromium.launch({ channel: "chrome", headless: true });
const context = await browser.newContext({ reducedMotion: "reduce" });
await context.route("**/__qa__/materialize.css", async (route) => {
  await route.fulfill({
    contentType: "text/css",
    body: [
      "html,body{block-size:auto!important;overflow:visible!important}",
      ".workstation-shell{block-size:auto!important;min-block-size:100dvb;overflow:visible!important;grid-template-rows:64px auto 28px!important}",
      ".workspace-scroll-body{overflow:visible!important}",
      ".market-summary,.data-sources-summary,.market-session-section,.market-context-section,.provider-capability-section{content-visibility:visible!important}",
    ].join(""),
  });
});
const page = await context.newPage();
const findings: BrowserFinding[] = [];
let generatedOffset = 0;

try {
  for (const width of widths) {
    await captureLoadingMatrix(context, width, findings);
    await page.setViewportSize({ width, height: 900 });
    for (const state of PUBLISHED_STATES) {
      generatedOffset += 1;
      await publishFixture(state, generatedOffset);
      for (const workspace of WORKSPACES) {
        await page.goto(`${baseUrl}/${workspace.hash}`, { waitUntil: "networkidle" });
        findings.push(
          await captureMatrixCase(page, width, workspace.id, state, screenshotDirectory),
        );
      }
    }
  }
  verifyMatrix(findings);
  const showcase = await captureShowcase(page);
  await page.setViewportSize({ width: 768, height: 900 });
  await page.goto(`${baseUrl}/#command-center`, { waitUntil: "networkidle" });
  const keyboard = await verifyKeyboardAndTrace(page);
  const movingElements = await countMovingElements(page);
  requireEqual(showcase.some((finding) => finding.axeViolations > 0), false, "showcase axe");
  requireEqual(showcase.some((finding) => finding.axeIncomplete > 0), false, "showcase incomplete");
  requireEqual(showcase.some((finding) => finding.pageOverflow), false, "showcase overflow");
  await page.goto(`${baseUrl}/showcase`, { waitUntil: "networkidle" });
  const showcaseRows = await page.locator("#stress-rows > tr").count();
  requireEqual(showcaseRows, 200, "showcase row count");
  const report: BrowserQaReport = {
    observable: "DASHBOARD_BROWSER_QA_OK",
    browser: "chrome",
    expectedMatrixCases: 216,
    widths,
    workspaces: WORKSPACES.map((workspace) => workspace.id),
    states: MATRIX_STATES,
    findings,
    showcase,
    keyboard,
    reducedMotion: { movingElements },
    showcaseRows,
  };
  await writeFile(output, `${JSON.stringify(report, null, 2)}\n`, { mode: 0o600 });
  console.log(
    `DASHBOARD_BROWSER_QA_OK cases=${findings.length} widths=${widths.join(",")} workspaces=9 states=8 axe=0 incomplete=0 overflow=0 trace_assertions=216`,
  );
} finally {
  await context.close();
  await browser.close();
}

async function captureLoadingMatrix(
  browserContext: BrowserContext,
  width: number,
  target: BrowserFinding[],
): Promise<void> {
  const loadingPage = await browserContext.newPage();
  await loadingPage.setViewportSize({ width, height: 900 });
  await loadingPage.route("**/api/snapshot", async () => {
    await new Promise<void>(() => undefined);
  });
  await loadingPage.routeWebSocket("**/api/realtime/view", () => {});
  for (const workspace of WORKSPACES) {
    await loadingPage.goto(`${baseUrl}/${workspace.hash}`, { waitUntil: "domcontentloaded" });
    await loadingPage.locator("#workspace-heading").waitFor({ state: "visible" });
    target.push(
      await captureMatrixCase(loadingPage, width, workspace.id, "loading", screenshotDirectory),
    );
  }
  await loadingPage.close();
}

async function publishFixture(state: PublishedState, offset: number): Promise<void> {
  const generatedAt = new Date(Date.now() + offset).toISOString();
  await ky.post(`${baseUrl}/api/ingest`, {
    headers: { authorization: `Bearer ${ingestToken}` },
    json: workstationStateFixture(state, generatedAt),
    retry: 0,
  });
}

function verifyMatrix(matrix: readonly BrowserFinding[]): void {
  requireEqual(matrix.length, EXPECTED_MATRIX_CASES, "matrix case count");
  const keys = new Set(matrix.map((item) => `${item.width}/${item.route}/${item.state}`));
  requireEqual(keys.size, EXPECTED_MATRIX_CASES, "distinct matrix case count");
  requireEqual(matrix.some((item) => item.axeViolations > 0), false, "axe violations");
  requireEqual(matrix.some((item) => item.axeIncomplete > 0), false, "axe incomplete");
  requireEqual(matrix.some((item) => item.pageOverflow), false, "page overflow");
  requireEqual(matrix.some((item) => item.leakHits > 0), false, "DOM/storage leaks");
}

async function captureShowcase(pageToCapture: Page): Promise<readonly ShowcaseFinding[]> {
  const results: ShowcaseFinding[] = [];
  for (const width of widths) {
    await pageToCapture.setViewportSize({ width, height: 900 });
    await pageToCapture.goto(`${baseUrl}/showcase`, { waitUntil: "networkidle" });
    const axe = await analyzeAtScrollPositions(pageToCapture);
    const pageOverflow = await pageToCapture.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    const screenshot = join(screenshotDirectory, `${width}-showcase.png`);
    await pageToCapture.screenshot({ path: screenshot });
    results.push({
      width,
      axeViolations: axe.violations,
      axeIncomplete: axe.incomplete,
      pageOverflow,
      screenshot,
    });
  }
  return results;
}

async function verifyKeyboardAndTrace(pageToVerify: Page): Promise<BrowserQaReport["keyboard"]> {
  const commandLink = pageToVerify.locator('[data-workspace-link="command-center"]');
  await commandLink.focus();
  await commandLink.press("ArrowRight");
  const arrowRoute = new URL(pageToVerify.url()).hash;
  requireEqual(arrowRoute, "#overview", "ArrowRight route");
  await pageToVerify.locator('[data-workspace-link="overview"]').press("Home");
  const homeRoute = new URL(pageToVerify.url()).hash;
  requireEqual(homeRoute, "#command-center", "Home route");
  const invoker = pageToVerify.locator(".trace-button:not([disabled])").first();
  requirePositive(await invoker.count(), "trace button count");
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
