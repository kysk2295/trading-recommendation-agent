import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import { resolve } from "node:path";
import { type Browser, chromium, type Page } from "playwright";
import {
  derivativesPaperAdverseFixture,
  derivativesPaperHappyFixture,
} from "./e2e/derivatives_paper_fixture";

let browser: Browser;
let page: Page;

beforeAll(async () => {
  const result = await Bun.build({
    entrypoints: [resolve(import.meta.dir, "../src/workspaces/options_workbench.ts")],
    format: "esm",
    target: "browser",
  });
  expect(result.success).toBeTrue();
  const output = result.outputs[0];
  if (output === undefined) throw new DepthRenderTestError("browser bundle missing");
  browser = await chromium.launch({ channel: "chrome", headless: true });
  page = await browser.newPage();
  await page.setContent("<!doctype html><html><body></body></html>");
  await page.addScriptTag({
    type: "module",
    content: `${await output.text()}\nwindow.mountWorkbench = (snapshot) => {
      const drawer = { open: () => undefined };
      document.body.replaceChildren(renderOptionsWorkbench(snapshot, drawer));
    };`,
  });
});

afterAll(async () => {
  await browser.close();
});

async function mount(snapshot: unknown): Promise<void> {
  await page.evaluate(`window.mountWorkbench(${JSON.stringify(snapshot)})`);
}

describe("deep options workbench rendering", () => {
  test("filters a bounded strike window and fails closed for an unprojected expiration", async () => {
    // Given: five projected rows for only the selected expiration.
    await mount(expandedChainFixture());
    await page.locator("#option_chain_tab").click();
    const expiration = page.getByLabel("Expiration");
    const strikeWindow = page.getByLabel("Strike window");

    // When: the bounded window is narrowed, then an unprojected expiration is selected.
    await strikeWindow.selectOption("3");
    const boundedRows = await page.locator("#option_chain tbody tr").count();
    await expiration.selectOption("2026-09-18");

    // Then: native controls expose the contract without reusing selected-expiration rows.
    expect(await expiration.locator("option").allTextContents()).toEqual([
      "2026-08-21",
      "2026-09-18",
    ]);
    expect(await strikeWindow.locator("option").allTextContents()).toEqual([
      "3 strikes",
      "5 strikes",
      "10 strikes",
      "All projected strikes",
    ]);
    expect(boundedRows).toBe(3);
    expect(
      await page.getByText("No projected rows for selected expiration 2026-09-18.").count(),
    ).toBe(1);
    expect(await page.getByRole("button", { name: /Select .* leg/ }).count()).toBe(0);
  });

  test("renders calls-left strike-center puts-right quote analytics", async () => {
    // Given: a populated current-contract option projection.
    await mount(derivativesPaperHappyFixture);

    // When: the Option Chain view is opened.
    await page.locator("#option_chain_tab").click();
    const table = page.getByRole("table", { name: /Calls left.*Puts right/ });

    // Then: both sides expose every bounded quote and Greek field.
    for (const heading of [
      "Provider / state",
      "Bid / ask",
      "Mid / spread",
      "Last",
      "Volume / OI",
      "IV",
      "Delta / gamma",
      "Theta / vega",
      "Observed",
      "Evidence",
      "Research leg",
    ]) {
      expect(await table.getByRole("columnheader", { name: heading, exact: true }).count()).toBe(2);
    }
    expect(await table.getByText("1.10 / 0.20").first().isVisible()).toBeTrue();
    expect(await table.getByText("0 / 0").first().isVisible()).toBeTrue();
  });

  test("renders evidence-bound Market Pulse metrics and explicit unsupported values", async () => {
    // Given: a current workbench contract with scenario and option evidence.
    await mount(derivativesPaperHappyFixture);

    // When: the default Market Pulse is inspected.
    const market = page.locator("#market_pulse");

    // Then: derivable values render, unsupported values remain unavailable, and source routes link.
    expect(await metricValue(market, "spot")).toBe("200 USD");
    expect(await metricValue(market, "selected-expiration")).toBe("2026-08-21");
    expect(await metricValue(market, "projected-expirations")).toBe("2");
    expect(await metricValue(market, "projected-strikes")).toBe("3 / 3");
    expect(await metricValue(market, "aggregate-volume")).toBe("0");
    expect(await metricValue(market, "aggregate-open-interest")).toBe("0");
    expect(await metricValue(market, "iv-summary")).toContain("31.00%");
    expect(await metricValue(market, "skew-summary")).toContain("0.00 pp");
    expect(await metricValue(market, "term-summary")).toContain("Unavailable");
    expect(await metricValue(market, "completed-bar")).toContain("Unavailable");
    expect(await metricValue(market, "futures-basis")).toContain("Unavailable");
    expect(await market.getByRole("link", { name: "Open Markets" }).getAttribute("href")).toBe(
      "#markets",
    );
    expect(await market.getByRole("link", { name: "Open Data Sources" }).getAttribute("href")).toBe(
      "#data-sources",
    );
  });

  test("keeps Market Pulse unavailable when current contract evidence is absent", async () => {
    // Given: the adverse contract has no scenario and no projected option rows.
    await mount(derivativesPaperAdverseFixture);

    // When: Market Pulse derives its bounded metrics.
    const market = page.locator("#market_pulse");

    // Then: missing values are labeled unavailable instead of estimated.
    expect(await metricValue(market, "spot")).toContain("Unavailable");
    expect(await metricValue(market, "iv-summary")).toContain("Unavailable");
    expect(await metricValue(market, "aggregate-volume")).toContain("Unavailable");
  });
});

async function metricValue(scope: ReturnType<Page["locator"]>, metric: string): Promise<string> {
  return (await scope.locator(`[data-market-metric="${metric}"] dd`).textContent()) ?? "";
}

function expandedChainFixture(): unknown {
  const workbench = derivativesPaperHappyFixture.workspaces.derivatives.workbench;
  const [first, second, third] = workbench.chain.rows;
  if (first === undefined || second === undefined || third === undefined) {
    throw new DepthRenderTestError("three fixture rows required");
  }
  const rows = [{ ...first, strike: "190" }, first, second, third, { ...third, strike: "210" }];
  return {
    ...derivativesPaperHappyFixture,
    workspaces: {
      ...derivativesPaperHappyFixture.workspaces,
      derivatives: {
        ...derivativesPaperHappyFixture.workspaces.derivatives,
        workbench: {
          ...workbench,
          chain: {
            ...workbench.chain,
            total_count: rows.length,
            projected_count: rows.length,
            rows,
          },
        },
      },
    },
  };
}

class DepthRenderTestError extends Error {
  override readonly name = "DepthRenderTestError";
}
