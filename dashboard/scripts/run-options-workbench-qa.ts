import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { websocket } from "hono/bun";
import { type Browser, type BrowserContext, chromium, type Page } from "playwright";
import { createApp } from "../src/app";
import type { DashboardSnapshotV2 } from "../src/schema_v2";
import { MemorySnapshotStore } from "../src/store";
import {
  derivativesPaperAdverseFixture,
  derivativesPaperHappyFixture,
} from "../tests/e2e/derivatives_paper_fixture";
import {
  HELP_TEXT,
  parseQaOptions,
  verifyBlocked,
  verifyHappyWidth,
  type WidthFinding,
} from "./options_workbench_qa_support";

type QaReport = Readonly<{
  observable: "OPTIONS_WORKBENCH_BROWSER_QA_OK";
  browser: "chrome";
  widths: readonly number[];
  findings: readonly WidthFinding[];
  blocked: Awaited<ReturnType<typeof verifyBlocked>>;
  consoleErrors: readonly string[];
  cleanup: Readonly<{
    pageClosed: boolean;
    contextClosed: boolean;
    browserClosed: boolean;
    serverStopped: boolean;
    listenerClosed: boolean;
  }>;
}>;

class OptionsWorkbenchQaRuntimeError extends Error {
  override readonly name = "OptionsWorkbenchQaRuntimeError";
}

const options = parseQaOptions(process.argv.slice(2));
if (options.kind === "help") {
  console.log(HELP_TEXT);
  process.exit(0);
}

const ingestToken = "qa-ingest-options-workbench-token";
const operatorToken = "qa-operator-options-workbench-token";
const screenshotDirectory = join(dirname(options.output), "screenshots");
await mkdir(screenshotDirectory, { recursive: true });
const app = createApp(new MemorySnapshotStore(), ingestToken, operatorToken);
const server = Bun.serve({ hostname: "127.0.0.1", port: 0, fetch: app.fetch, websocket });
const baseUrl = `http://127.0.0.1:${server.port}`;
let browser: Browser | null = null;
let context: BrowserContext | null = null;
let page: Page | null = null;
let findings: readonly WidthFinding[] = [];
let blocked: Awaited<ReturnType<typeof verifyBlocked>> | null = null;
const consoleErrors: string[] = [];
const cleanup = {
  pageClosed: false,
  contextClosed: false,
  browserClosed: false,
  serverStopped: false,
  listenerClosed: false,
};

try {
  await publishFixture(baseUrl, ingestToken, derivativesPaperHappyFixture);
  browser = await chromium.launch({ channel: "chrome", headless: true });
  context = await browser.newContext({ reducedMotion: "reduce" });
  page = await context.newPage();
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));
  const completed: WidthFinding[] = [];
  for (const width of options.widths) {
    completed.push(await verifyHappyWidth(page, baseUrl, width, screenshotDirectory));
  }
  findings = completed;
  await publishFixture(baseUrl, ingestToken, derivativesPaperAdverseFixture);
  blocked = await verifyBlocked(page, baseUrl, screenshotDirectory);
  if (consoleErrors.length > 0) {
    throw new OptionsWorkbenchQaRuntimeError(`console errors: ${consoleErrors.join(" | ")}`);
  }
} finally {
  await page?.close();
  cleanup.pageClosed = page?.isClosed() ?? true;
  await context?.close();
  cleanup.contextClosed = true;
  await browser?.close();
  cleanup.browserClosed = true;
  await server.stop(true);
  cleanup.serverStopped = true;
  cleanup.listenerClosed = await listenerIsClosed(baseUrl);
}

if (blocked === null) throw new OptionsWorkbenchQaRuntimeError("blocked finding missing");
if (!cleanup.listenerClosed)
  throw new OptionsWorkbenchQaRuntimeError("ephemeral listener remains open");
const report: QaReport = {
  observable: "OPTIONS_WORKBENCH_BROWSER_QA_OK",
  browser: "chrome",
  widths: options.widths,
  findings,
  blocked,
  consoleErrors,
  cleanup,
};
await writeFile(options.output, `${JSON.stringify(report, null, 2)}\n`, { mode: 0o600 });
console.log(
  `OPTIONS_WORKBENCH_BROWSER_QA_OK widths=${options.widths.join(",")} views=5 axe=0 incomplete=0 overflow=0 console_errors=0 cleanup=closed`,
);

async function publishFixture(
  baseUrl: string,
  token: string,
  fixture: DashboardSnapshotV2,
): Promise<void> {
  const response = await fetch(`${baseUrl}/api/ingest`, {
    method: "POST",
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
    body: JSON.stringify(fixture),
  });
  if (response.status !== 202) {
    throw new OptionsWorkbenchQaRuntimeError(
      `fixture ingest failed with status ${response.status}`,
    );
  }
}

async function listenerIsClosed(baseUrl: string): Promise<boolean> {
  try {
    await fetch(`${baseUrl}/api/health`, { signal: AbortSignal.timeout(500) });
    return false;
  } catch {
    return true;
  }
}
