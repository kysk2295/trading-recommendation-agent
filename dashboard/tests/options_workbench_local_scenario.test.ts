import { afterAll, beforeAll, expect, test } from "bun:test";
import { resolve } from "node:path";
import { type Browser, chromium, type Page } from "playwright";
import { derivativesPaperHappyFixture } from "./e2e/derivatives_paper_fixture";

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
  if (output === undefined) throw new LocalScenarioTestError("browser bundle missing");
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

test("builds a local research scenario without a canonical scenario receipt", async () => {
  const fixture = derivativesPaperHappyFixture;
  const snapshot = {
    ...fixture,
    workspaces: {
      ...fixture.workspaces,
      derivatives: {
        ...fixture.workspaces.derivatives,
        workbench: {
          ...fixture.workspaces.derivatives.workbench,
          scenario: null,
        },
      },
    },
  };
  await page.evaluate(`window.mountWorkbench(${JSON.stringify(snapshot)})`);

  await page.locator("#option_chain_tab").click();
  await page.getByRole("button", { name: "Select 195 call leg" }).click();
  await page.locator("#strategy_agent_tab").click();
  const strategy = page.locator("#strategy_agent");

  expect(await selectedLegIds(strategy)).toEqual(["aapl-20260821-c-195"]);
  expect(
    await strategy.getByRole("figure", { name: "Strategy payoff visualization" }).count(),
  ).toBe(1);
  expect(await strategy.getByText("LOCAL RESEARCH SCENARIO", { exact: true }).count()).toBe(1);
});

async function selectedLegIds(scope: ReturnType<Page["locator"]>): Promise<readonly string[]> {
  return scope
    .locator("[data-selected-leg]")
    .evaluateAll((legs) => legs.map((leg) => leg.getAttribute("data-selected-leg") ?? ""));
}

class LocalScenarioTestError extends Error {
  override readonly name = "LocalScenarioTestError";
}
