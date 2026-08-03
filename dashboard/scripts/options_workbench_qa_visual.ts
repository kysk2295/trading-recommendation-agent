import { mkdir, unlink } from "node:fs/promises";
import { join } from "node:path";
import type { Page } from "playwright";
import { WORKBENCH_VIEWS, type WorkbenchView } from "../src/workspaces/options_workbench";

export type VisualState = WorkbenchView | "evidence_trace_open" | "blocked_option_chain";

export type VisualCapture = Readonly<{
  width: number;
  state: VisualState;
  screenshot: string;
}>;

export async function prepareScreenshotDirectory(
  directory: string,
  widths: readonly number[],
): Promise<void> {
  await mkdir(directory, { recursive: true });
  const names = widths.flatMap((width) => [
    ...WORKBENCH_VIEWS.map((view) => screenshotName(width, view)),
    screenshotName(width, "evidence_trace_open"),
    screenshotName(width, "blocked_option_chain"),
    `options-workbench-${width}.png`,
  ]);
  names.push("options-workbench-blocked-375.png");
  for (const name of names) {
    try {
      await unlink(join(directory, name));
    } catch (error: unknown) {
      if (error instanceof Error && Reflect.get(error, "code") === "ENOENT") continue;
      throw error;
    }
  }
}

export async function captureVisibleViews(
  page: Page,
  width: number,
  directory: string,
): Promise<readonly VisualCapture[]> {
  const captures: VisualCapture[] = [];
  for (const view of WORKBENCH_VIEWS) {
    await page.locator(`#${view}_tab`).click();
    const visible = await page.locator(`#${view}`).isVisible();
    if (!visible) throw new OptionsWorkbenchVisualError(`${width}/${view} panel not visible`);
    captures.push(await capture(page, width, view, directory));
  }
  return captures;
}

export async function captureTraceAndReturnFocus(
  page: Page,
  width: number,
  directory: string,
): Promise<Readonly<{ capture: VisualCapture; focusReturned: boolean }>> {
  await page.locator("#option_chain_tab").click();
  const invoker = page.locator("#option_chain .trace-button").first();
  await invoker.click();
  const dialog = page.locator("#evidence-trace-dialog");
  await dialog.waitFor({ state: "visible" });
  const traceCapture = await capture(page, width, "evidence_trace_open", directory);
  await dialog.press("Escape");
  await dialog.waitFor({ state: "hidden" });
  const focusReturned = await invoker.evaluate((element) => document.activeElement === element);
  if (!focusReturned) throw new OptionsWorkbenchVisualError(`${width} trace focus not returned`);
  return { capture: traceCapture, focusReturned };
}

export function captureBlockedView(
  page: Page,
  width: number,
  directory: string,
): Promise<VisualCapture> {
  return capture(page, width, "blocked_option_chain", directory);
}

function asyncScreenshot(page: Page, path: string): Promise<Buffer> {
  return page.screenshot({ path, fullPage: true });
}

async function capture(
  page: Page,
  width: number,
  state: VisualState,
  directory: string,
): Promise<VisualCapture> {
  const screenshot = join(directory, screenshotName(width, state));
  await asyncScreenshot(page, screenshot);
  return { width, state, screenshot };
}

function screenshotName(width: number, state: VisualState): string {
  return `options-workbench-${width}-${state}.png`;
}

class OptionsWorkbenchVisualError extends Error {
  override readonly name = "OptionsWorkbenchVisualError";
}
