import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { websocket } from "hono/bun";
import { type Browser, type BrowserContext, chromium, type Page } from "playwright";
import { createApp } from "../src/app";
import { MemorySnapshotStore } from "../src/store";
import {
  type StoreQaFinding,
  verifyActualSnapshot,
  verifyBlockedSnapshot,
} from "./options_workbench_store_qa_browser";
import {
  type LoadedSnapshot,
  loadSnapshot,
  OptionsWorkbenchStoreQaError,
  parseStoreQaOptions,
  STORE_QA_HELP_TEXT,
} from "./options_workbench_store_qa_support";

type ServerCleanup = Readonly<{
  label: string;
  pageClosed: boolean;
  serverStopped: boolean;
  listenerClosed: boolean;
}>;

type StoreQaReport = Readonly<{
  observable: "OPTIONS_WORKBENCH_STORE_BROWSER_QA_OK";
  browser: "chrome";
  widths: readonly number[];
  findings: readonly StoreQaFinding[];
  consoleErrors: readonly string[];
  cleanup: Readonly<{
    contextClosed: boolean;
    browserClosed: boolean;
    cases: readonly ServerCleanup[];
  }>;
}>;

const ingestToken = "qa-store-options-workbench-ingest-token";
const operatorToken = "qa-store-options-workbench-operator-token";

export function formatExpectedCliError(error: unknown): string | null {
  if (!(error instanceof OptionsWorkbenchStoreQaError)) return null;
  if (error.message.startsWith("--")) return `ERROR: ${error.message}`;
  if (error.message.startsWith("unknown option:")) return "ERROR: unknown option";
  if (error.message.includes("snapshot could not be read")) {
    return "ERROR: snapshot could not be read";
  }
  if (error.message.startsWith("snapshot ingest failed")) return "ERROR: snapshot ingest failed";
  if (error.message.endsWith("listener remains open")) return "ERROR: listener cleanup failed";
  return "ERROR: options workbench store QA failed";
}

if (import.meta.main) void main().then(undefined, reportCliFailure);

async function main(): Promise<void> {
  const options = parseStoreQaOptions(process.argv.slice(2));
  if (options.kind === "help") {
    console.log(STORE_QA_HELP_TEXT);
    return;
  }
  const actual = await loadSnapshot(options.actualPath, "actual");
  const blocked = await Promise.all(
    options.blocked.map(async (entry) => loadSnapshot(entry.path, entry.label)),
  );
  const screenshotDirectory = join(dirname(options.output), "options-workbench-store-screenshots");
  await mkdir(screenshotDirectory, { recursive: true });
  let browser: Browser | null = null;
  let context: BrowserContext | null = null;
  const findings: StoreQaFinding[] = [];
  const consoleErrors: string[] = [];
  const cases: ServerCleanup[] = [];
  let contextClosed = false;
  let browserClosed = false;
  try {
    browser = await chromium.launch({ channel: "chrome", headless: true });
    context = await browser.newContext({ reducedMotion: "reduce" });
    findings.push(
      ...(await runPublishedCase(
        context,
        actual,
        consoleErrors,
        async (page, baseUrl) => {
          const checks: StoreQaFinding[] = [];
          for (const width of options.widths) {
            checks.push(
              await verifyActualSnapshot(
                page,
                baseUrl,
                actual.snapshot,
                width,
                screenshotDirectory,
              ),
            );
          }
          return checks;
        },
        cases,
      )),
    );
    for (const fixture of blocked) {
      findings.push(
        ...(await runPublishedCase(
          context,
          fixture,
          consoleErrors,
          async (page, baseUrl) => {
            const checks: StoreQaFinding[] = [];
            for (const width of options.widths) {
              checks.push(
                await verifyBlockedSnapshot(
                  page,
                  baseUrl,
                  fixture.label,
                  fixture.snapshot,
                  width,
                  screenshotDirectory,
                ),
              );
            }
            return checks;
          },
          cases,
        )),
      );
    }
    if (consoleErrors.length > 0) {
      throw new OptionsWorkbenchStoreQaError(`console errors: ${consoleErrors.join(" | ")}`);
    }
  } finally {
    await context?.close();
    contextClosed = true;
    await browser?.close();
    browserClosed = true;
  }
  const report: StoreQaReport = {
    observable: "OPTIONS_WORKBENCH_STORE_BROWSER_QA_OK",
    browser: "chrome",
    widths: options.widths,
    findings,
    consoleErrors,
    cleanup: { contextClosed, browserClosed, cases },
  };
  await writeFile(options.output, `${JSON.stringify(report, null, 2)}\n`, { mode: 0o600 });
  console.log(
    `OPTIONS_WORKBENCH_STORE_BROWSER_QA_OK widths=${options.widths.join(",")} findings=${findings.length} screenshots=${findings.flatMap((finding) => finding.screenshotPaths).length} axe=0 incomplete=0 overflow=0 console_errors=0 cleanup=closed`,
  );
}

function reportCliFailure(error: unknown): void {
  const expected = formatExpectedCliError(error);
  console.error(expected ?? "ERROR: unexpected options workbench store QA failure");
  process.exitCode = expected === null ? 1 : 2;
}

async function runPublishedCase(
  context: BrowserContext,
  fixture: LoadedSnapshot,
  errors: string[],
  verify: (page: Page, baseUrl: string) => Promise<readonly StoreQaFinding[]>,
  cleanup: ServerCleanup[],
): Promise<readonly StoreQaFinding[]> {
  const app = createApp(new MemorySnapshotStore(), ingestToken, operatorToken);
  const server = Bun.serve({ hostname: "127.0.0.1", port: 0, fetch: app.fetch, websocket });
  const baseUrl = `http://127.0.0.1:${server.port}`;
  let page: Page | null = null;
  let serverStopped = false;
  let listenerClosed = false;
  let findings: readonly StoreQaFinding[] = [];
  try {
    await publishSnapshot(baseUrl, fixture.snapshot);
    page = await context.newPage();
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(`${fixture.label}: ${message.text()}`);
    });
    page.on("pageerror", (error) => errors.push(`${fixture.label}: ${error.message}`));
    findings = await verify(page, baseUrl);
  } finally {
    await page?.close();
    const pageClosed = page?.isClosed() ?? true;
    await server.stop(true);
    serverStopped = true;
    listenerClosed = await listenerIsClosed(baseUrl);
    cleanup.push({ label: fixture.label, pageClosed, serverStopped, listenerClosed });
  }
  if (!listenerClosed)
    throw new OptionsWorkbenchStoreQaError(`${fixture.label} listener remains open`);
  return findings;
}

async function publishSnapshot(
  baseUrl: string,
  snapshot: LoadedSnapshot["snapshot"],
): Promise<void> {
  const response = await fetch(`${baseUrl}/api/ingest`, {
    method: "POST",
    headers: { authorization: `Bearer ${ingestToken}`, "content-type": "application/json" },
    body: JSON.stringify(snapshot),
  });
  if (response.status !== 202) {
    throw new OptionsWorkbenchStoreQaError(`snapshot ingest failed with status ${response.status}`);
  }
}

async function listenerIsClosed(baseUrl: string): Promise<boolean> {
  try {
    await fetch(`${baseUrl}/api/health`, { signal: AbortSignal.timeout(500) });
    return false;
  } catch (error: unknown) {
    if (error instanceof Error) return true;
    throw error;
  }
}
