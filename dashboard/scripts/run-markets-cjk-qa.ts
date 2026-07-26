import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { parseArgs } from "node:util";
import ky from "ky";
import { chromium } from "playwright";
import { dashboardSnapshotV2Schema } from "../src/schema_v2";
import { marketsDataSourcesFixture } from "../tests/e2e/markets_data_sources_fixture";

type CjkFinding = {
  readonly width: number;
  readonly quoteLeadAndProjectionShareLine: boolean;
  readonly pageOverflow: boolean;
  readonly screenshot: string;
};

type CjkQaReport = {
  readonly observable: "DASHBOARD_MARKETS_CJK_QA_OK";
  readonly findings: readonly CjkFinding[];
};

const { values } = parseArgs({
  options: {
    "base-url": { type: "string", default: "http://127.0.0.1:3000" },
    output: { type: "string" },
  },
  strict: true,
});
const output = requiredOption(values.output, "--output");
const baseUrl = new URL(values["base-url"]).toString().replace(/\/$/, "");
const ingestToken = requiredEnvironment("DASHBOARD_INGEST_TOKEN");
const screenshotDirectory = join(dirname(output), "screenshots");
await mkdir(screenshotDirectory, { recursive: true });

const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage();
const findings: CjkFinding[] = [];

try {
  for (const width of [375, 768, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await publishMarketsFixture(width);
    await page.goto(`${baseUrl}/#markets`, { waitUntil: "networkidle" });
    const guidance = page.locator(".market-context-section .state-guidance");
    await guidance.waitFor({ state: "visible" });
    await guidance.scrollIntoViewIfNeeded();
    const quoteLeadAndProjectionShareLine = await guidance.evaluate((element) => {
      const label = element.querySelector(".market-projection-label");
      const text = label?.firstChild;
      if (text?.nodeType !== Node.TEXT_NODE) return false;
      const content = text.textContent ?? "";
      if (content !== "이\u00a0v2") return false;
      const leadRange = document.createRange();
      leadRange.setStart(text, 0);
      leadRange.setEnd(text, 1);
      const projectionRange = document.createRange();
      projectionRange.setStart(text, 2);
      projectionRange.setEnd(text, 4);
      return leadRange.getBoundingClientRect().top === projectionRange.getBoundingClientRect().top;
    });
    const pageOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    const screenshot = join(screenshotDirectory, `markets-${width}.png`);
    await page.screenshot({ path: screenshot, fullPage: true });
    findings.push({ width, quoteLeadAndProjectionShareLine, pageOverflow, screenshot });
  }
  const orphanWidths = findings
    .filter((finding) => !finding.quoteLeadAndProjectionShareLine)
    .map((finding) => finding.width);
  if (orphanWidths.length > 0) {
    throw new Error(`markets_cjk_orphan_detected widths=${orphanWidths.join(",")}`);
  }
  if (findings.some((finding) => finding.pageOverflow)) {
    throw new Error("markets_cjk_page_overflow_detected");
  }
  const report: CjkQaReport = { observable: "DASHBOARD_MARKETS_CJK_QA_OK", findings };
  await writeFile(output, `${JSON.stringify(report, null, 2)}\n`, { mode: 0o600 });
  console.log(
    `DASHBOARD_MARKETS_CJK_QA_OK widths=${findings.map((finding) => finding.width).join(",")}`,
  );
} finally {
  await page.close();
  await browser.close();
}

async function publishMarketsFixture(width: number): Promise<void> {
  const fixture = dashboardSnapshotV2Schema.parse(marketsDataSourcesFixture());
  const generatedAt = new Date(Date.now() + width).toISOString();
  await ky.post(`${baseUrl}/api/ingest`, {
    headers: { authorization: `Bearer ${ingestToken}` },
    json: { ...fixture, generated_at: generatedAt },
    retry: 0,
  });
}

function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (value === undefined || value.length === 0) throw new Error(`${name} is required`);
  return value;
}

function requiredOption(value: string | undefined, name: string): string {
  if (value === undefined || value.length === 0) throw new Error(`${name} is required`);
  return value;
}
