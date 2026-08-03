import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import { resolve } from "node:path";
import { type Browser, chromium, type Page } from "playwright";
import {
  derivativesPaperAdverseFixture,
  derivativesPaperHappyFixture,
} from "./e2e/derivatives_paper_fixture";

const views = [
  "market_pulse",
  "option_chain",
  "strategy_agent",
  "experiment_lab",
  "promotion_operations",
] as const;

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
  if (output === undefined) throw new Error("options workbench browser bundle missing");
  const source = await output.text();
  browser = await chromium.launch({ channel: "chrome", headless: true });
  page = await browser.newPage();
  await page.setContent("<!doctype html><html><body></body></html>");
  await page.addScriptTag({
    type: "module",
    content: `${source}\nwindow.mountWorkbench = (snapshot) => {
      window.openedTrace = null;
      window.lastMountedSnapshot = snapshot;
      window.snapshotBefore = JSON.stringify(snapshot);
      const drawer = { open: (label, trace, invoker) => {
        window.openedTrace = { label, status: trace.status, id: invoker.dataset.traceId };
        document.body.dataset.openedTraceStatus = trace.status;
      }};
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

async function activeElementId(): Promise<string> {
  return page.evaluate("document.activeElement?.id ?? ''");
}

describe("options workbench rendering", () => {
  test("renders semantic calls, strike, puts structure and exact five defaulted views", async () => {
    // Given
    const reloadFixture = {
      ...derivativesPaperHappyFixture,
      workspaces: {
        ...derivativesPaperHappyFixture.workspaces,
        derivatives: {
          ...derivativesPaperHappyFixture.workspaces.derivatives,
          workbench: {
            ...derivativesPaperHappyFixture.workspaces.derivatives.workbench,
            selected_view: "promotion_operations",
          },
        },
      },
    };
    await mount(reloadFixture);

    // When
    const defaultPanelVisible = await page.locator("#market_pulse").isVisible();

    // Then
    expect(await page.getByRole("tab").evaluateAll((tabs) => tabs.map((tab) => tab.id))).toEqual(
      views.map((view) => `${view}_tab`),
    );
    expect(
      await page
        .locator("[role=tabpanel]")
        .evaluateAll((panels) => panels.map((panel) => panel.id)),
    ).toEqual([...views]);
    expect(defaultPanelVisible).toBeTrue();
    expect(await page.locator("[role=tabpanel][hidden]").count()).toBe(4);
    expect(await page.getByText("alpaca · indicative").first().isVisible()).toBeTrue();
    await page.locator("#option_chain_tab").click();
    const table = page.getByRole("table", { name: /Calls left.*Puts right/ });
    expect(await table.getByRole("columnheader", { name: "Calls" }).getAttribute("scope")).toBe(
      "colgroup",
    );
    expect(await table.getByRole("columnheader", { name: "Strike" }).getAttribute("scope")).toBe(
      "col",
    );
    expect(await table.getByRole("columnheader", { name: "Puts" }).getAttribute("scope")).toBe(
      "colgroup",
    );
  });

  test("fails closed without leg controls when the chain is unavailable", async () => {
    // Given
    await mount(derivativesPaperAdverseFixture);

    // When
    await page.locator("#option_chain_tab").click();

    // Then
    expect(await page.locator("#option_chain").textContent()).toContain(
      "Canonical option chain unavailable",
    );
    expect(await page.getByRole("button", { name: /Select .* leg/ }).count()).toBe(0);
  });

  test("keeps native roving selection, focus and one visible panel", async () => {
    // Given
    await mount(derivativesPaperHappyFixture);
    const first = page.locator("#market_pulse_tab");
    await first.focus();

    // When
    await first.press("ArrowLeft");

    // Then
    expect(await activeElementId()).toBe("promotion_operations_tab");
    expect(await page.locator("#promotion_operations_tab").getAttribute("aria-selected")).toBe(
      "true",
    );
    expect(await page.locator("[role=tabpanel]:not([hidden])").count()).toBe(1);
    await page.locator("#promotion_operations_tab").press("Home");
    expect(await activeElementId()).toBe("market_pulse_tab");
    await first.press("End");
    expect(await activeElementId()).toBe("promotion_operations_tab");
    await page.locator("#promotion_operations_tab").press("ArrowRight");
    expect(await activeElementId()).toBe("market_pulse_tab");
    await page.locator("#option_chain_tab").focus();
    await page.locator("#option_chain_tab").press("Enter");
    expect(await page.locator("#option_chain_tab").getAttribute("aria-selected")).toBe("true");
    await page.locator("#strategy_agent_tab").focus();
    await page.locator("#strategy_agent_tab").press("Space");
    expect(await page.locator("#strategy_agent_tab").getAttribute("aria-selected")).toBe("true");
    expect(await activeElementId()).toBe("strategy_agent_tab");
  });

  test("updates the deterministic scenario without mutating the snapshot and opens trace", async () => {
    // Given
    await mount(derivativesPaperHappyFixture);
    await page.locator("#option_chain_tab").click();
    const before = await page.locator("#option_chain [data-scenario-series]").textContent();
    const initialBreakEven = await page.locator("#option_chain [data-break-even]").textContent();
    expect(
      await page.locator('#option_chain [data-selected-leg="aapl-20260821-c-200"]').isVisible(),
    ).toBeTrue();

    // When
    await page.getByRole("button", { name: "Select 195 call leg" }).click();
    const after = await page.locator("#option_chain [data-scenario-series]").textContent();
    const chainBreakEven = await page.locator("#option_chain [data-break-even]").textContent();
    const chainLegs = await page
      .locator("#option_chain [data-selected-leg]")
      .evaluateAll((legs) => legs.map((leg) => leg.getAttribute("data-selected-leg")));
    await page.locator("#option_chain [data-trace-id]").first().click();
    await page.locator("#strategy_agent_tab").click();
    const agentSeries = await page.locator("#strategy_agent [data-scenario-series]").textContent();
    const agentBreakEven = await page.locator("#strategy_agent [data-break-even]").textContent();
    const agentLegs = await page
      .locator("#strategy_agent [data-selected-leg]")
      .evaluateAll((legs) => legs.map((leg) => leg.getAttribute("data-selected-leg")));

    // Then
    expect(after).not.toBe(before);
    expect(initialBreakEven).toContain("205.00");
    expect(chainBreakEven).toContain("200.60");
    expect(chainBreakEven).not.toBe(initialBreakEven);
    expect(chainLegs).toEqual(["aapl-20260821-c-200", "aapl-20260821-c-195"]);
    expect(agentLegs).toEqual(chainLegs);
    expect(agentSeries).toBe(after);
    expect(agentBreakEven).toBe(chainBreakEven);
    expect(
      await page.evaluate("window.snapshotBefore === JSON.stringify(window.lastMountedSnapshot)"),
    ).toBeTrue();
    expect(await page.locator("body").getAttribute("data-opened-trace-status")).toBe("resolved");
  });
});
