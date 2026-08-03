import { join } from "node:path";
import type { Page } from "playwright";
import type { DashboardSnapshotV2 } from "../src/schema_v2";
import { WORKBENCH_VIEWS } from "../src/workspaces/options_workbench";
import { analyzeAtScrollPositions, requireEqual } from "./browser_qa_support";
import { reachableBlockerTerminal } from "./options_workbench_store_qa_support";

type QaKind = "actual" | "blocked";
type StoreQaCaptureKind = QaKind | `blocked-${string}`;

export type StoreQaFinding = Readonly<{
  label: string;
  width: number;
  viewsVisited: number;
  selectionControls: number;
  traceFocusReturned: boolean;
  pageOverflow: boolean;
  axeViolations: number;
  axeIncomplete: number;
  reducedMotion: boolean;
  screenshotPaths: readonly string[];
}>;

export async function verifyActualSnapshot(
  page: Page,
  baseUrl: string,
  snapshot: DashboardSnapshotV2,
  width: number,
  screenshotDirectory: string,
): Promise<StoreQaFinding> {
  await preparePage(page, baseUrl, width);
  const screenshotPaths = await visitViews(page, width, "actual", screenshotDirectory);
  await page.locator("#market_pulse_tab").click();
  await assertVisibleText(
    page.locator("#market_pulse"),
    snapshot.workspaces.derivatives.workbench.chain.selected_expiration ?? "Unavailable",
  );
  await assertMarketSourceTruth(page, snapshot);
  await page.locator("#option_chain_tab").click();
  await assertProjectedChain(page, snapshot);
  const selectionControls = await page.getByRole("button", { name: /Select .* leg/ }).count();
  requireEqual(selectionControls, 0, `${width} actual selectable leg controls`);
  const traceFocusReturned = await traceOpensAndReturnsFocus(
    page,
    page.locator("#option_chain .trace-button").first(),
    undefined,
  );
  await assertResearchOnlyTerms(page);
  return finalFinding(
    page,
    "actual",
    width,
    screenshotPaths,
    selectionControls,
    traceFocusReturned,
  );
}

export async function verifyBlockedSnapshot(
  page: Page,
  baseUrl: string,
  label: string,
  snapshot: DashboardSnapshotV2,
  width: number,
  screenshotDirectory: string,
): Promise<StoreQaFinding> {
  await preparePage(page, baseUrl, width);
  const screenshotPaths = await visitViews(page, width, `blocked-${label}`, screenshotDirectory);
  await page.locator("#option_chain_tab").click();
  const chain = snapshot.workspaces.derivatives.workbench.chain;
  if (chain.blocker_code === null) {
    throw new OptionsWorkbenchStoreQaBrowserError(`${label} chain has no blocker code`);
  }
  await assertVisibleText(page.locator("#option_chain"), chain.summary);
  requireEqual(
    await page.locator("#option_chain tbody th[scope='row']").count(),
    0,
    "blocked rows",
  );
  const selectionControls = await page.getByRole("button", { name: /Select .* leg/ }).count();
  requireEqual(selectionControls, 0, `${label}/${width} selectable leg controls`);
  const terminal = reachableBlockerTerminal(snapshot, chain.trace_id);
  const traceFocusReturned = await traceOpensAndReturnsFocus(
    page,
    page.getByRole("button", { name: "Option Chain Evidence Trace 열기" }),
    terminal.label,
  );
  await assertResearchOnlyTerms(page);
  return finalFinding(page, label, width, screenshotPaths, selectionControls, traceFocusReturned);
}

async function preparePage(page: Page, baseUrl: string, width: number): Promise<void> {
  await page.setViewportSize({ width, height: 900 });
  await page.goto(`${baseUrl}/#derivatives`, { waitUntil: "networkidle" });
  await page.locator("#market_pulse_tab").waitFor({ state: "visible" });
  requireEqual(
    await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches),
    true,
    `${width} reduced motion`,
  );
}

async function visitViews(
  page: Page,
  width: number,
  kind: StoreQaCaptureKind,
  screenshotDirectory: string,
): Promise<readonly string[]> {
  const screenshots: string[] = [];
  for (const view of WORKBENCH_VIEWS) {
    const tab = page.locator(`#${view}_tab`);
    await tab.click();
    requireEqual(await tab.getAttribute("aria-selected"), "true", `${kind}/${view} selected`);
    requireEqual(
      await page.locator(`#${view}:not([hidden])`).count(),
      1,
      `${kind}/${view} visible`,
    );
    const filename = `${kind}-${width}-${view}.png`;
    const screenshot = join(screenshotDirectory, filename);
    await page.screenshot({ path: screenshot, fullPage: true });
    screenshots.push(storeQaScreenshotArtifactReference(kind, width, view));
  }
  return screenshots;
}

export function storeQaScreenshotArtifactReference(
  kind: StoreQaCaptureKind,
  width: number,
  view: (typeof WORKBENCH_VIEWS)[number],
): string {
  return `options-workbench-store-screenshots/${kind}-${width}-${view}.png`;
}

async function assertProjectedChain(page: Page, snapshot: DashboardSnapshotV2): Promise<void> {
  const workbench = snapshot.workspaces.derivatives.workbench;
  const chainPanel = page.locator("#option_chain");
  await assertSelectedExpiration(page, workbench.chain.selected_expiration);
  for (const row of workbench.chain.rows) {
    await assertVisibleText(chainPanel, row.strike);
    for (const cell of [row.call, row.put]) {
      if (cell === null) continue;
      await assertVisibleText(chainPanel, `${cell.provider} · ${cell.state}`);
      await assertVisibleText(
        chainPanel,
        `${cell.bid ?? "Unavailable"} / ${cell.ask ?? "Unavailable"}`,
      );
      await assertVisibleText(chainPanel, cell.observed_at?.replace("T", " ") ?? "Unavailable");
      requireEqual(
        await page.getByRole("button", { name: `${cell.contract_id} Evidence Trace 열기` }).count(),
        1,
        `${cell.contract_id} trace control`,
      );
    }
  }
  const visibleState = workbench.chain.rows
    .flatMap((row) => [row.call, row.put])
    .some((cell) => cell?.state === "indicative" || cell?.state === "delayed");
  requireEqual(visibleState, true, "indicative or delayed projected state");
}

async function assertSelectedExpiration(page: Page, expected: string | null): Promise<void> {
  requireEqual(
    await page.locator("#option-chain-expiration").inputValue(),
    expected ?? "",
    "selected expiration",
  );
}

async function assertMarketSourceTruth(page: Page, snapshot: DashboardSnapshotV2): Promise<void> {
  const cells = snapshot.workspaces.derivatives.workbench.chain.rows.flatMap((row) => [
    row.call,
    row.put,
  ]);
  const sourceTruth = [
    ...new Set(
      cells.flatMap((cell) => (cell === null ? [] : [`${cell.provider} · ${cell.state}`])),
    ),
  ].join(" · ");
  await assertVisibleText(
    page.locator("#market_pulse"),
    sourceTruth || "Unavailable · no quote rows",
  );
}

async function assertVisibleText(scope: ReturnType<Page["locator"]>, text: string): Promise<void> {
  const target = scope.getByText(text, { exact: true }).first();
  requireEqual(await target.isVisible(), true, `visible exact text: ${text}`);
}

async function traceOpensAndReturnsFocus(
  page: Page,
  invoker: ReturnType<Page["locator"]>,
  terminalLabel: string | undefined,
): Promise<boolean> {
  requireEqual(await invoker.count(), 1, "trace invoker count");
  await invoker.click();
  const dialog = page.locator("#evidence-trace-dialog");
  await dialog.waitFor({ state: "visible" });
  if (terminalLabel !== undefined) {
    await assertVisibleText(dialog, "blocker terminal");
    await assertVisibleText(dialog, terminalLabel);
  }
  await dialog.press("Escape");
  await dialog.waitFor({ state: "hidden" });
  const focusReturned = await invoker.evaluate((element) => document.activeElement === element);
  requireEqual(focusReturned, true, "trace focus return");
  return focusReturned;
}

async function assertResearchOnlyTerms(page: Page): Promise<void> {
  const text = await page.locator(".options-workbench").innerText();
  requireEqual(
    /\b(?:nan|current|realtime|profit)\b/i.test(text),
    false,
    "unsafe Workbench wording",
  );
}

async function finalFinding(
  page: Page,
  label: string,
  width: number,
  screenshotPaths: readonly string[],
  selectionControls: number,
  traceFocusReturned: boolean,
): Promise<StoreQaFinding> {
  const axe = await analyzeAtScrollPositions(page);
  requireEqual(
    axe.violations,
    0,
    `${label}/${width} axe violations ${axe.violationKeys.join(",")}`,
  );
  requireEqual(
    axe.incomplete,
    0,
    `${label}/${width} axe incomplete ${axe.incompleteKeys.join(",")}`,
  );
  const pageOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  );
  requireEqual(pageOverflow, false, `${label}/${width} page overflow`);
  return {
    label,
    width,
    viewsVisited: WORKBENCH_VIEWS.length,
    selectionControls,
    traceFocusReturned,
    pageOverflow,
    axeViolations: axe.violations,
    axeIncomplete: axe.incomplete,
    reducedMotion: true,
    screenshotPaths,
  };
}

class OptionsWorkbenchStoreQaBrowserError extends Error {
  override readonly name = "OptionsWorkbenchStoreQaBrowserError";
}
