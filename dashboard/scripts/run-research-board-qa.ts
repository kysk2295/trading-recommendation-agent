import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { parseArgs } from "node:util";
import AxeBuilder from "@axe-core/playwright";
import ky from "ky";
import { chromium } from "playwright";
import { dashboardSnapshotV2Schema } from "../src/schema_v2";
import { snapshotV2 } from "../tests/snapshot_v2_fixture";

class ResearchBoardQaError extends Error {}

const { values } = parseArgs({
  options: {
    "base-url": { type: "string", default: "http://127.0.0.1:3000" },
    output: { type: "string" },
  },
  strict: true,
});
const output = required(values.output, "--output");
const baseUrl = new URL(values["base-url"]).toString().replace(/\/$/, "");
const ingestToken = required(process.env["DASHBOARD_INGEST_TOKEN"], "DASHBOARD_INGEST_TOKEN");
const screenshotDirectory = join(dirname(output), "screenshots");
await mkdir(screenshotDirectory, { recursive: true });

const browser = await chromium.launch({ channel: "chrome", headless: true });
const context = await browser.newContext();
const page = await context.newPage();
const consoleErrors: string[] = [];
page.on("console", (message) => {
  if (message.type() === "error") consoleErrors.push(message.text());
});
const findings: Array<{
  width: number;
  screenshot: string;
  pageOverflow: boolean;
  rowCount: number;
  axeViolations: number;
  lowerScreenshot: string;
  traceScreenshot: string;
}> = [];

try {
  await publishFixture();
  for (const width of [375, 768, 1280] as const) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`${baseUrl}/#research`, { waitUntil: "networkidle" });
    const board = page.getByRole("region", { name: "6-Agent Research Board" });
    await board.waitFor({ state: "visible" });
    const rowCount = await board.getByRole("row").count();
    if (rowCount !== 7) throw new ResearchBoardQaError(`research_board_row_count:${rowCount}`);
    const pageOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    if (pageOverflow) throw new ResearchBoardQaError(`research_board_page_overflow:${width}`);
    await board.getByRole("button", { name: "기회 관리자 Evidence Trace 열기" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.waitFor({ state: "visible" });
    const traceScreenshot = join(screenshotDirectory, `research-board-trace-${width}.png`);
    await page.screenshot({ path: traceScreenshot, fullPage: true });
    await page.keyboard.press("Escape");
    await dialog.waitFor({ state: "hidden" });
    await board.evaluate((element) => {
      element.scrollLeft = 0;
    });
    const accessibility = await new AxeBuilder({ page }).include("#workspace-view").analyze();
    if (accessibility.violations.length > 0) {
      throw new ResearchBoardQaError(
        `research_board_axe:${width}:${accessibility.violations.map((item) => item.id).join(",")}`,
      );
    }
    const screenshot = join(screenshotDirectory, `research-board-${width}.png`);
    await page.screenshot({ path: screenshot, fullPage: true });
    await board.getByText("시장 맥락 · MARKET CONTEXT").scrollIntoViewIfNeeded();
    const lowerScreenshot = join(screenshotDirectory, `research-board-lower-${width}.png`);
    await page.screenshot({ path: lowerScreenshot, fullPage: true });
    findings.push({
      width,
      screenshot,
      pageOverflow,
      rowCount,
      axeViolations: 0,
      lowerScreenshot,
      traceScreenshot,
    });
  }
  if (consoleErrors.length > 0) {
    throw new ResearchBoardQaError(`research_board_console:${consoleErrors.join("|")}`);
  }
  await writeFile(
    output,
    `${JSON.stringify({ observable: "RESEARCH_BOARD_QA_OK", findings, consoleErrors }, null, 2)}\n`,
    { mode: 0o600 },
  );
  console.log("RESEARCH_BOARD_QA_OK widths=375,768,1280 axe=0 console=0");
} finally {
  await context.close();
  await browser.close();
}

async function publishFixture(): Promise<void> {
  const fixture = dashboardSnapshotV2Schema.parse(structuredClone(snapshotV2));
  fixture.snapshot_id = crypto.randomUUID();
  fixture.generated_at = new Date().toISOString();
  const states = [
    ["opportunity.candidates", "investigate_candidate", "AAPL candidate artifact recorded", 1],
    ["day.recommendation", "review_open_state", "Recommendation reached target_1r", 2],
    ["swing.signal", "review_open_state", "Swing shadow remains active", 1],
    [
      "systematic.generated_review",
      "request_heavy_experiment",
      "Reviewer hold feedback recorded",
      4,
    ],
    ["derivatives.snapshot", "publish_context", "Indicative option surface published", 1],
    ["market_context.snapshot", "publish_context", "Breadth and liquidity context published", 1],
  ] as const;
  fixture.workspaces.research.agent_cycles = fixture.workspaces.research.agent_cycles.map(
    (cycle, index) => {
      const state = states[index];
      if (state === undefined) throw new ResearchBoardQaError("research_board_fixture_missing");
      return {
        ...cycle,
        cycle_state: "completed" as const,
        result_status: "completed" as const,
        input_source: state[0],
        decision_kind: state[1],
        result_summary: state[2],
        artifact_count: state[3],
        observed_at: fixture.generated_at,
        next_wake_kind: "new_evidence" as const,
      };
    },
  );
  await ky.post(`${baseUrl}/api/ingest`, {
    headers: { authorization: `Bearer ${ingestToken}` },
    json: fixture,
    retry: 0,
  });
}

function required(value: string | undefined, name: string): string {
  if (value === undefined || value.length === 0)
    throw new ResearchBoardQaError(`${name} is required`);
  return value;
}
