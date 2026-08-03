import { join } from "node:path";
import { parseArgs } from "node:util";
import AxeBuilder from "@axe-core/playwright";
import type { Page } from "playwright";
import { WORKBENCH_VIEWS } from "../src/workspaces/options_workbench";

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
  screenshot: string;
}>;

export type BlockedFinding = Readonly<{
  selectionControls: number;
  summaryVisible: boolean;
  pageOverflow: boolean;
  axeViolations: number;
  axeIncomplete: number;
  screenshot: string;
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
  const traceFocusReturned = await verifyTrace(page);
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
  const axe = await new AxeBuilder({ page }).analyze();
  requireEqual(axe.violations.length, 0, `${width} axe violations`);
  requireEqual(axe.incomplete.length, 0, `${width} axe incomplete`);
  const pageOverflow = await hasPageOverflow(page);
  requireEqual(pageOverflow, false, `${width} page overflow`);
  const reducedMotion = await page.evaluate(
    () => matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  requireEqual(reducedMotion, true, `${width} reduced motion`);
  const screenshot = join(screenshotDirectory, `options-workbench-${width}.png`);
  await page.screenshot({ path: screenshot, fullPage: true });
  return {
    width,
    viewsDriven: WORKBENCH_VIEWS.length,
    selectedLegs: chainLegs,
    strategySynchronized,
    breakEven: chainBreakEven,
    traceFocusReturned,
    localScrollOwned: localScroll.owned,
    localOverflow: localScroll.overflow,
    pageOverflow,
    axeViolations: axe.violations.length,
    axeIncomplete: axe.incomplete.length,
    reducedMotion,
    screenshot,
  };
}

export async function verifyBlocked(
  page: Page,
  baseUrl: string,
  screenshotDirectory: string,
): Promise<BlockedFinding> {
  await page.setViewportSize({ width: 375, height: 900 });
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
  const axe = await new AxeBuilder({ page }).analyze();
  requireEqual(axe.violations.length, 0, "blocked axe violations");
  requireEqual(axe.incomplete.length, 0, "blocked axe incomplete");
  const pageOverflow = await hasPageOverflow(page);
  requireEqual(pageOverflow, false, "blocked page overflow");
  const screenshot = join(screenshotDirectory, "options-workbench-blocked-375.png");
  await page.screenshot({ path: screenshot, fullPage: true });
  return {
    selectionControls,
    summaryVisible,
    pageOverflow,
    axeViolations: axe.violations.length,
    axeIncomplete: axe.incomplete.length,
    screenshot,
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

async function verifyTrace(page: Page): Promise<boolean> {
  const invoker = page.locator("#option_chain .trace-button").first();
  await invoker.click();
  const dialog = page.locator("#evidence-trace-dialog");
  await dialog.waitFor({ state: "visible" });
  await dialog.press("Escape");
  await dialog.waitFor({ state: "hidden" });
  const returned = await invoker.evaluate((element) => document.activeElement === element);
  requireEqual(returned, true, "trace focus return");
  return returned;
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
