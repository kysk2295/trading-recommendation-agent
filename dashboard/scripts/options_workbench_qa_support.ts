import { parseArgs } from "node:util";
import type { Page } from "playwright";
import { WORKBENCH_VIEWS } from "../src/workspaces/options_workbench";
import { analyzeAtScrollPositions, resetScrollableContent } from "./browser_qa_support";
import {
  captureBlockedView,
  captureTraceAndReturnFocus,
  captureVisibleViews,
  type VisualCapture,
} from "./options_workbench_qa_visual";

export type QaOptions =
  | Readonly<{ kind: "help" }>
  | Readonly<{ kind: "run"; output: string; widths: readonly number[] }>;

export type WidthFinding = Readonly<{
  width: number;
  viewsDriven: number;
  selectedLegs: readonly string[];
  strategySynchronized: boolean;
  breakEven: string;
  traceFocusReturned: boolean;
  localScrollOwned: boolean;
  localOverflow: boolean;
  pageOverflow: boolean;
  axeViolations: number;
  axeIncomplete: number;
  reducedMotion: boolean;
  operationSummary: OperationSummaryFinding;
  captures: readonly VisualCapture[];
}>;

export type OperationSummaryFinding = Readonly<{
  text: string;
  whiteSpace: string;
  visible: boolean;
  positiveDimensions: boolean;
  insideSystemSummary: boolean;
  launcherVisible: boolean;
  nonOverlappingLauncher: boolean;
  tailBox: Box;
  systemBox: Box;
  launcherBox: Box;
}>;

type Box = Readonly<{
  top: number;
  right: number;
  bottom: number;
  left: number;
  width: number;
  height: number;
}>;

export type BlockedFinding = Readonly<{
  width: number;
  selectionControls: number;
  summaryVisible: boolean;
  pageOverflow: boolean;
  axeViolations: number;
  axeIncomplete: number;
  topScrollReset: boolean;
  capture: VisualCapture;
}>;

export const HELP_TEXT = `Usage: bun run qa:options-workbench -- --output <report.json> [--widths 375,768,1280]

Options:
  --help                 Show this help and exit.
  --output <path>        Required JSON report destination.
  --widths <csv>         One to six unique integer widths from 320 to 2560.`;

export function parseQaOptions(args: readonly string[]): QaOptions {
  const { values } = parseArgs({
    args,
    options: {
      help: { type: "boolean", default: false },
      output: { type: "string" },
      widths: { type: "string", default: "375,768,1280" },
    },
    strict: true,
  });
  if (values.help) return { kind: "help" };
  if (values.output === undefined || values.output.length === 0) {
    throw new OptionsWorkbenchQaError("--output is required");
  }
  const widths = values.widths.split(",").map((value) => Number(value));
  if (
    widths.length < 1 ||
    widths.length > 6 ||
    widths.some((width) => !Number.isInteger(width) || width < 320 || width > 2560) ||
    new Set(widths).size !== widths.length
  ) {
    throw new OptionsWorkbenchQaError(
      "--widths must contain one to six unique integers from 320 to 2560",
    );
  }
  return { kind: "run", output: values.output, widths };
}

export async function verifyHappyWidth(
  page: Page,
  baseUrl: string,
  width: number,
  screenshotDirectory: string,
): Promise<WidthFinding> {
  await page.setViewportSize({ width, height: 900 });
  await page.goto(`${baseUrl}/#derivatives`, { waitUntil: "networkidle" });
  await page.locator("#market_pulse_tab").waitFor({ state: "visible" });
  await verifyTabs(page);
  await page.locator("#option_chain_tab").click();
  const baseline = page.locator('#option_chain [data-selected-leg="aapl-20260821-c-200"]');
  requireEqual(await baseline.count(), 1, `${width} canonical baseline leg`);
  await page.getByRole("button", { name: "Select 195 call leg" }).click();
  const chainLegs = await selectedLegIds(page, "#option_chain");
  requireEqual(chainLegs.join(","), "aapl-20260821-c-200,aapl-20260821-c-195", `${width} legs`);
  const chainBreakEven = await requiredText(page, "#option_chain [data-break-even]");
  requireEqual(chainBreakEven.includes("200.60"), true, `${width} break-even recomputed`);
  const trace = await captureTraceAndReturnFocus(page, width, screenshotDirectory);
  const viewport = page.locator(".options-chain-viewport");
  const localScroll = await viewport.evaluate((element) => ({
    owned: getComputedStyle(element).overflowX === "auto",
    overflow: element.scrollWidth > element.clientWidth,
  }));
  requireEqual(localScroll.owned, true, `${width} local table scroll owner`);
  if (width === 375) requireEqual(localScroll.overflow, true, "375 local table overflow");
  await page.locator("#strategy_agent_tab").click();
  const agentLegs = await selectedLegIds(page, "#strategy_agent");
  const agentBreakEven = await requiredText(page, "#strategy_agent [data-break-even]");
  const strategySynchronized =
    agentLegs.join(",") === chainLegs.join(",") && agentBreakEven === chainBreakEven;
  requireEqual(strategySynchronized, true, `${width} strategy synchronization`);
  const operationSummary = await verifyOperationSummary(page, width);
  const captures = await captureVisibleViews(page, width, screenshotDirectory);
  const axe = await analyzeAtScrollPositions(page);
  requireEqual(axe.violations, 0, `${width} axe violations ${axe.violationKeys.join(",")}`);
  requireEqual(axe.incomplete, 0, `${width} axe incomplete ${axe.incompleteKeys.join(",")}`);
  const pageOverflow = await hasPageOverflow(page);
  requireEqual(pageOverflow, false, `${width} page overflow`);
  const reducedMotion = await page.evaluate(
    () => matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  requireEqual(reducedMotion, true, `${width} reduced motion`);
  return {
    width,
    viewsDriven: WORKBENCH_VIEWS.length,
    selectedLegs: chainLegs,
    strategySynchronized,
    breakEven: chainBreakEven,
    traceFocusReturned: trace.focusReturned,
    localScrollOwned: localScroll.owned,
    localOverflow: localScroll.overflow,
    pageOverflow,
    axeViolations: axe.violations,
    axeIncomplete: axe.incomplete,
    reducedMotion,
    operationSummary,
    captures: [...captures, trace.capture],
  };
}

async function verifyOperationSummary(page: Page, width: number): Promise<OperationSummaryFinding> {
  await page.locator("#promotion_operations_tab").click();
  const tail = page.locator(
    '#promotion_operations [data-operations-summary="system"] .options-operations-summary-tail',
  );
  await tail.waitFor({ state: "visible" });
  const finding = await page.evaluate(
    ({ tailSelector, systemSelector, launcherSelector }) => {
      const tailElement = document.querySelector(tailSelector);
      const systemElement = document.querySelector(systemSelector);
      const launcherElement = document.querySelector(launcherSelector);
      if (
        !(tailElement instanceof HTMLElement) ||
        !(systemElement instanceof HTMLElement) ||
        !(launcherElement instanceof HTMLElement)
      ) {
        throw new Error("operation summary geometry target missing");
      }
      const box = (element: HTMLElement): Box => {
        const rect = element.getBoundingClientRect();
        return {
          top: rect.top,
          right: rect.right,
          bottom: rect.bottom,
          left: rect.left,
          width: rect.width,
          height: rect.height,
        };
      };
      const tailBox = box(tailElement);
      const systemBox = box(systemElement);
      const launcherBox = box(launcherElement);
      const launcherStyle = getComputedStyle(launcherElement);
      const launcherVisible =
        launcherStyle.display !== "none" && launcherBox.width > 0 && launcherBox.height > 0;
      return {
        text: tailElement.textContent ?? "",
        whiteSpace: getComputedStyle(tailElement).whiteSpace,
        visible: tailElement.getClientRects().length > 0,
        positiveDimensions: tailBox.width > 0 && tailBox.height > 0,
        insideSystemSummary:
          tailBox.top >= systemBox.top &&
          tailBox.right <= systemBox.right &&
          tailBox.bottom <= systemBox.bottom &&
          tailBox.left >= systemBox.left,
        launcherVisible,
        nonOverlappingLauncher: !launcherVisible || tailBox.bottom <= launcherBox.top,
        tailBox,
        systemBox,
        launcherBox,
      };
    },
    {
      tailSelector:
        '#promotion_operations [data-operations-summary="system"] .options-operations-summary-tail',
      systemSelector: '[data-operations-summary="system"]',
      launcherSelector: ".mobile-launcher",
    },
  );
  requireEqual(finding.text, "항목 없음", `${width} operation summary tail text`);
  requireEqual(finding.whiteSpace, "nowrap", `${width} operation summary tail whitespace`);
  requireEqual(finding.visible, true, `${width} operation summary tail visible`);
  requireEqual(finding.positiveDimensions, true, `${width} operation summary tail dimensions`);
  requireEqual(finding.insideSystemSummary, true, `${width} operation summary inside System card`);
  requireEqual(
    finding.nonOverlappingLauncher,
    true,
    `${width} operation summary launcher overlap ${JSON.stringify({ tail: finding.tailBox, launcher: finding.launcherBox })}`,
  );
  return finding;
}

export async function verifyBlocked(
  page: Page,
  baseUrl: string,
  width: number,
  screenshotDirectory: string,
): Promise<BlockedFinding> {
  await page.setViewportSize({ width, height: 900 });
  await page.goto(`${baseUrl}/#derivatives`, { waitUntil: "networkidle" });
  await page.locator("#option_chain_tab").click();
  const selectionControls = await page.getByRole("button", { name: /Select .* leg/ }).count();
  requireEqual(selectionControls, 0, "blocked selection controls");
  const summaryVisible = await page
    .locator("#option_chain")
    .getByText("Canonical option chain unavailable")
    .first()
    .isVisible();
  requireEqual(summaryVisible, true, "blocked summary");
  const chainViewport = page.locator(".options-chain-viewport");
  await chainViewport.evaluate((element) => {
    const strike = element.querySelector('thead th[rowspan="2"]');
    if (!(strike instanceof HTMLElement)) throw new Error("semantic strike header missing");
    element.scrollLeft = Math.max(
      strike.offsetLeft - element.clientWidth / 2 + strike.offsetWidth / 2,
      0,
    );
  });
  const axe = await analyzeAtScrollPositions(page);
  requireEqual(axe.violations, 0, `blocked axe violations ${axe.violationKeys.join(",")}`);
  requireEqual(axe.incomplete, 0, `blocked axe incomplete ${axe.incompleteKeys.join(",")}`);
  const pageOverflow = await hasPageOverflow(page);
  requireEqual(pageOverflow, false, "blocked page overflow");
  await resetScrollableContent(page);
  await chainViewport.evaluate((element) => {
    element.scrollLeft = 0;
  });
  const topScrollReset = await page.evaluate(() => {
    const workspace = document.querySelector(".workspace-scroll-body");
    return (
      workspace instanceof HTMLElement &&
      workspace.scrollTop === 0 &&
      document.documentElement.scrollTop === 0 &&
      document.body.scrollTop === 0
    );
  });
  requireEqual(topScrollReset, true, `blocked top scroll reset ${width}`);
  const capture = await captureBlockedView(page, width, screenshotDirectory);
  return {
    width,
    selectionControls,
    summaryVisible,
    pageOverflow,
    axeViolations: axe.violations,
    axeIncomplete: axe.incomplete,
    topScrollReset,
    capture,
  };
}

function asyncActiveId(page: Page): Promise<string> {
  return page.evaluate(() => document.activeElement?.id ?? "");
}

async function verifyTabs(page: Page): Promise<void> {
  const tabs = page.getByRole("tab");
  requireEqual(await tabs.count(), WORKBENCH_VIEWS.length, "tab count");
  const first = page.locator("#market_pulse_tab");
  await first.focus();
  await first.press("ArrowLeft");
  requireEqual(await asyncActiveId(page), "promotion_operations_tab", "ArrowLeft wrap");
  await page.locator("#promotion_operations_tab").press("ArrowRight");
  requireEqual(await asyncActiveId(page), "market_pulse_tab", "ArrowRight wrap");
  await first.press("End");
  requireEqual(await asyncActiveId(page), "promotion_operations_tab", "End focus");
  await page.locator("#promotion_operations_tab").press("Home");
  requireEqual(await asyncActiveId(page), "market_pulse_tab", "Home focus");
  await page.locator("#option_chain_tab").focus();
  await page.locator("#option_chain_tab").press("Enter");
  await page.locator("#strategy_agent_tab").focus();
  await page.locator("#strategy_agent_tab").press("Space");
  for (const view of WORKBENCH_VIEWS) {
    const tab = page.locator(`#${view}_tab`);
    await tab.click();
    requireEqual(await tab.getAttribute("aria-selected"), "true", `${view} selected`);
    requireEqual(await asyncActiveId(page), `${view}_tab`, `${view} focus`);
    requireEqual(await page.locator('[role="tabpanel"]:not([hidden])').count(), 1, `${view} panel`);
  }
}

async function selectedLegIds(page: Page, scope: string): Promise<readonly string[]> {
  return page
    .locator(`${scope} [data-selected-leg]`)
    .evaluateAll((elements) =>
      elements.flatMap((element) => element.getAttribute("data-selected-leg") ?? []),
    );
}

async function requiredText(page: Page, selector: string): Promise<string> {
  const value = await page.locator(selector).textContent();
  if (value === null) throw new OptionsWorkbenchQaError(`missing text: ${selector}`);
  return value;
}

function hasPageOverflow(page: Page): Promise<boolean> {
  return page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  );
}

function requireEqual<T>(actual: T, expected: T, label: string): void {
  if (actual !== expected) {
    throw new OptionsWorkbenchQaError(
      `${label}: expected ${String(expected)}, received ${String(actual)}`,
    );
  }
}

export class OptionsWorkbenchQaError extends Error {
  override readonly name = "OptionsWorkbenchQaError";
}
