import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { parseArgs } from "node:util";
import AxeBuilder from "@axe-core/playwright";
import { websocket } from "hono/bun";
import { type Browser, type BrowserContext, chromium, type Page } from "playwright";
import { createApp } from "../src/app";
import { dashboardSnapshotV2Schema } from "../src/schema_v2";
import { MemorySnapshotStore } from "../src/store";
import { dayAgentFixture } from "../tests/day_agent_fixture";

class DayAgentQaError extends Error {}

type Finding = Readonly<{
  width: number;
  screenshot: string;
  lowerScreenshot: string;
  axeViolations: number;
  pageOverflow: boolean;
  keyboardTraceReturn: boolean;
  mutationRequestCount: number;
}>;

const { values } = parseArgs({
  options: {
    help: { type: "boolean", default: false },
    output: { type: "string" },
    widths: { type: "string", default: "375,768,1280" },
  },
  strict: true,
});
if (values.help) {
  console.log("Usage: bun run qa:day-agent -- --output <report.json> [--widths 375,768,1280]");
  process.exit(0);
}
if (values.output === undefined || values.output.length === 0)
  throw new DayAgentQaError("--output is required");
const widths = values.widths.split(",").map((value) => Number(value));
if (widths.length !== 3 || widths.some((width) => ![375, 768, 1280].includes(width))) {
  throw new DayAgentQaError("--widths must be 375,768,1280");
}
const output = values.output;
const screenshots = join(dirname(output), "screenshots-day-agent");
await mkdir(screenshots, { recursive: true });

const app = createApp(
  new MemorySnapshotStore(),
  "day-agent-qa-ingest-token-000000",
  "day-agent-qa-operator-token-000000",
);
const server = Bun.serve({ hostname: "127.0.0.1", port: 0, fetch: app.fetch, websocket });
const baseUrl = `http://127.0.0.1:${server.port}`;
let browser: Browser | null = null;
let context: BrowserContext | null = null;
let page: Page | null = null;
const consoleErrors: string[] = [];
const cleanup = {
  pageClosed: false,
  contextClosed: false,
  browserClosed: false,
  serverStopped: false,
};
let findings: readonly Finding[] = [];

try {
  await publish("happy");
  browser = await chromium.launch({ channel: "chrome", headless: true });
  context = await browser.newContext({ reducedMotion: "reduce" });
  page = await context.newPage();
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  const completed: Finding[] = [];
  for (const width of widths) completed.push(await verifyHappy(page, width));
  findings = completed;
  await verifyIsolation(
    page,
    "kr-corrupt",
    "US · Alpaca Paper",
    "KR · Shadow · provider read-only",
  );
  await verifyIsolation(
    page,
    "us-corrupt",
    "KR · Shadow · provider read-only",
    "US · Alpaca Paper",
  );
  if (consoleErrors.length > 0)
    throw new DayAgentQaError(`console errors: ${consoleErrors.join(" | ")}`);
} finally {
  await page?.close();
  cleanup.pageClosed = page?.isClosed() ?? true;
  await context?.close();
  cleanup.contextClosed = true;
  await browser?.close();
  cleanup.browserClosed = true;
  await server.stop(true);
  cleanup.serverStopped = true;
}

if (
  !cleanup.pageClosed ||
  !cleanup.contextClosed ||
  !cleanup.browserClosed ||
  !cleanup.serverStopped
) {
  throw new DayAgentQaError("cleanup incomplete");
}
await writeFile(
  output,
  `${JSON.stringify({ observable: "DAY_AGENT_DASHBOARD_QA_OK", browser: "chrome", widths, findings, isolation: ["kr-corrupt", "us-corrupt"], consoleErrors, cleanup }, null, 2)}\n`,
  { mode: 0o600 },
);
console.log(
  `DAY_AGENT_DASHBOARD_QA_OK widths=${widths.join(",")} axe=0 overflow=0 mutations=0 cleanup=closed`,
);

async function publish(lane: "happy" | "kr-corrupt" | "us-corrupt"): Promise<void> {
  const fixture = dashboardSnapshotV2Schema.parse(dayAgentFixture(lane));
  const response = await fetch(`${baseUrl}/api/ingest`, {
    method: "POST",
    headers: {
      authorization: "Bearer day-agent-qa-ingest-token-000000",
      "content-type": "application/json",
    },
    body: JSON.stringify(fixture),
  });
  if (response.status !== 202)
    throw new DayAgentQaError(`fixture ingest failed:${response.status}`);
}

async function verifyHappy(target: Page, width: number): Promise<Finding> {
  await target.setViewportSize({ width, height: 900 });
  const methods: string[] = [];
  const observe = (request: { method(): string }) => methods.push(request.method());
  target.on("request", observe);
  try {
    await target.goto(`${baseUrl}/#markets`, { waitUntil: "networkidle" });
    await target.getByRole("heading", { name: "Day Agent · 독립 관측 슬라이스" }).waitFor();
    for (const label of ["US · Alpaca Paper", "US · Shadow", "KR · Shadow · provider read-only"]) {
      if ((await target.getByText(label, { exact: true }).count()) < 1)
        throw new DayAgentQaError(`missing lane:${label}`);
    }
    const visible = await target.locator(".day-agent-lanes").innerText();
    if (
      !visible.includes("entry") ||
      !visible.includes("stop") ||
      !visible.includes("targets") ||
      !visible.includes("outcome")
    ) {
      throw new DayAgentQaError(`day agent detail missing:${width}`);
    }
    const forbidden = await target
      .getByRole("button", { name: /buy|sell|order|cancel|submit|promotion/i })
      .count();
    if (forbidden !== 0 || /combined|confidence|profitability/i.test(visible))
      throw new DayAgentQaError(`unsafe control or claim:${width}`);
    const trace = target.locator(".day-agent-lanes .trace-button").first();
    await trace.focus();
    await trace.press("Enter");
    await target.getByRole("dialog").waitFor({ state: "visible" });
    await target.keyboard.press("Escape");
    const keyboardTraceReturn = await trace.evaluate(
      (element) => document.activeElement === element,
    );
    if (!keyboardTraceReturn) throw new DayAgentQaError(`trace focus return:${width}`);
    const axe = await new AxeBuilder({ page: target }).include("#workspace-view").analyze();
    if (axe.violations.length > 0)
      throw new DayAgentQaError(`axe:${width}:${axe.violations.map((item) => item.id).join(",")}`);
    const pageOverflow = await target.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    if (pageOverflow) throw new DayAgentQaError(`overflow:${width}`);
    const screenshot = join(screenshots, `day-agent-${width}.png`);
    await target.screenshot({ path: screenshot, fullPage: true });
    const workspaceMain = target.locator("#workspace-main");
    await workspaceMain.evaluate((element) => {
      element.scrollTop = element.scrollHeight;
    });
    const lowerScreenshot = join(screenshots, `day-agent-${width}-lower.png`);
    await target.screenshot({ path: lowerScreenshot });
    const mutationRequestCount = methods.filter(
      (method) => !["GET", "OPTIONS"].includes(method),
    ).length;
    if (mutationRequestCount !== 0) throw new DayAgentQaError(`browser mutation:${width}`);
    return {
      width,
      screenshot,
      lowerScreenshot,
      axeViolations: axe.violations.length,
      pageOverflow,
      keyboardTraceReturn,
      mutationRequestCount,
    };
  } finally {
    target.off("request", observe);
  }
}

async function verifyIsolation(
  target: Page,
  lane: "kr-corrupt" | "us-corrupt",
  validLabel: string,
  blockedLabel: string,
): Promise<void> {
  await publish(lane);
  await target.goto(`${baseUrl}/#markets`, { waitUntil: "networkidle" });
  const valid = target.getByText(validLabel, { exact: true }).first();
  const blocked = target.getByText(blockedLabel, { exact: true }).first();
  if (!(await valid.isVisible()) || !(await blocked.isVisible()))
    throw new DayAgentQaError(`isolation labels:${lane}`);
  const blockedRow = blocked.locator("xpath=ancestor::div[contains(@class, 'day-agent-row')][1]");
  if ((await blockedRow.getByText("무결성 실패", { exact: true }).count()) !== 1)
    throw new DayAgentQaError(`isolation state:${lane}`);
}
