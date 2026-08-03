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
  if (output === undefined) throw new ResearchPanelTestError("browser bundle missing");
  browser = await chromium.launch({ channel: "chrome", headless: true });
  page = await browser.newPage();
  await page.setContent("<!doctype html><html><body></body></html>");
  await page.addScriptTag({
    type: "module",
    content: `${await output.text()}\nwindow.mountWorkbench = (snapshot) => {
      window.snapshotBefore = JSON.stringify(snapshot);
      window.lastMountedSnapshot = snapshot;
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

describe("complete options research panels", () => {
  test("synchronizes and resets local strategy research with payoff and Greeks", async () => {
    // Given: one immutable baseline leg and one locally selected chain leg.
    await mount(derivativesPaperHappyFixture);
    await page.locator("#option_chain_tab").click();
    await page.getByRole("button", { name: "Select 195 call leg" }).click();

    // When: Strategy & Agent highlights a bounded scenario spot, then resets local legs.
    await page.locator("#strategy_agent_tab").click();
    const strategy = page.locator("#strategy_agent");
    expect(
      await strategy.getByRole("figure", { name: "Strategy payoff visualization" }).count(),
    ).toBe(1);
    expect(await strategy.getByRole("table", { name: "Payoff samples" }).count()).toBe(1);
    expect(await metric(strategy, "sampled-max-gain")).toBe("1880.00 USD");
    expect(await metric(strategy, "sampled-max-loss")).toBe("-620.00 USD");
    expect(await metric(strategy, "net-delta")).toBe("100.0000");
    expect(await metric(strategy, "net-gamma")).toBe("4.0000");
    expect(await metric(strategy, "net-theta")).toBe("-2.0000");
    expect(await metric(strategy, "net-vega")).toBe("20.0000");
    expect(await metric(strategy, "underlying")).toBe("AAPL");
    expect(await metric(strategy, "edit-availability")).toBe("Local research legs only");
    await strategy.getByLabel("Scenario spot").selectOption("210");
    const highlighted = await strategy.locator('[data-highlighted-spot="210"]').count();
    await strategy.getByRole("button", { name: "Reset local legs" }).click();

    // Then: the baseline remains immutable, both panels resynchronize, and snapshot input is unchanged.
    expect(highlighted).toBe(1);
    expect(await strategy.getByText(/Indicative quote inputs/).count()).toBe(1);
    expect(await metric(strategy, "net-delta")).toBe("50.0000");
    expect(await selectedLegIds(strategy)).toEqual(["aapl-20260821-c-200"]);
    await page.locator("#option_chain_tab").click();
    expect(await selectedLegIds(page.locator("#option_chain"))).toEqual(["aapl-20260821-c-200"]);
    expect(
      await page.evaluate("window.snapshotBefore === JSON.stringify(window.lastMountedSnapshot)"),
    ).toBeTrue();
  });

  test("keeps incomplete Greeks unavailable and exposes a safe Agent Room receipt", async () => {
    // Given: a matching chain contract lacks one required Greek.
    await mount(missingGreekFixture());

    // When: Strategy & Agent opens its expandable Agent Room.
    await page.locator("#strategy_agent_tab").click();
    const strategy = page.locator("#strategy_agent");
    const room = strategy.getByRole("group", { name: /Agent Room/ });

    // Then: no partial net Greek is shown and tool data stays bounded to current evidence.
    expect(await metric(strategy, "net-delta")).toContain("Unavailable");
    expect(await room.count()).toBe(1);
    expect(await room.getByText(/Safe parameters.*Not projected/).count()).toBe(1);
    expect(await room.getByText(/Progress.*populated/).count()).toBe(1);
    expect(await room.getByText(/reviewer_decision/).count()).toBe(1);
    expect(await room.getByRole("button", { name: /Agent Room Evidence Trace/ }).count()).toBe(1);
  });

  test("renders the immutable experiment trace and explicit unprojected research gates", async () => {
    // Given: the experiment section references the current source-to-reviewer trace.
    await mount(derivativesPaperHappyFixture);

    // When: Experiment Lab is opened.
    await page.locator("#experiment_lab_tab").click();
    const experiment = page.locator("#experiment_lab");

    // Then: real nodes render in order and unsupported evaluation metrics are not invented.
    expect(await experiment.locator("[data-experiment-trace-node]").count()).toBe(2);
    expect(
      await experiment.locator("[data-experiment-trace-node]").first().textContent(),
    ).toContain("source_receipt");
    expect(await experiment.locator("[data-experiment-trace-node]").last().textContent()).toContain(
      "reviewer_decision",
    );
    expect(await experiment.locator("[data-experiment-gate]").count()).toBe(8);
    expect(
      await experiment
        .locator("[data-experiment-gate] dd")
        .evaluateAll((values) =>
          values.every((value) => value.textContent?.includes("Not projected")),
        ),
    ).toBeTrue();
    expect(
      await experiment.getByRole("button", { name: /Experiment Lab Evidence Trace/ }).count(),
    ).toBe(1);
    expect(await experiment.textContent()).not.toContain("profitable");
  });

  test("renders read-only promotion gates with Paper and System operational truth", async () => {
    // Given: one held candidate, finalized Paper rows, and no projected system budget rows.
    await mount(derivativesPaperHappyFixture);

    // When: Promotion & Operations is opened.
    await page.locator("#promotion_operations_tab").click();
    const operations = page.locator("#promotion_operations");

    // Then: gates stay separate, reconciliation is read-only, and missing operations remain unavailable.
    expect(await operations.locator("[data-promotion-candidate]").count()).toBe(1);
    expect(await operations.locator("[data-promotion-gate]").count()).toBe(4);
    expect(await operations.getByText(/held.*manual_approval_pending/).count()).toBe(1);
    expect(await operations.locator('[data-operations-summary="paper"]').textContent()).toContain(
      "populated · 9/9",
    );
    expect(await operations.locator('[data-operations-summary="system"]').textContent()).toContain(
      "empty · 0/0",
    );
    const systemSummaryTail = operations.locator(
      '[data-operations-summary="system"] .options-operations-summary-tail',
    );
    expect(await systemSummaryTail.count()).toBe(1);
    expect(await systemSummaryTail.textContent()).toBe("항목 없음");
    expect(await operations.locator("[data-operations-capacity]").count()).toBe(4);
    expect(
      await operations
        .locator("[data-operations-capacity] dd")
        .evaluateAll((values) =>
          values.every((value) => value.textContent?.includes("Unavailable")),
        ),
    ).toBeTrue();
    expect(
      await operations.getByRole("button", { name: /execute|order|broker|provider/i }).count(),
    ).toBe(0);
  });

  test("keeps a long non-empty-marker operation tail ordinarily wrappable", async () => {
    // Given: an empty 0/0 workspace whose canonical summary has a different long trailing clause.
    const summary =
      "권위 있는 읽기 완료, 길게 이어지는 다른 운영 상태 설명은 일반 줄바꿈을 유지합니다";
    await mount(systemSummaryFixture(summary));

    // When: the System operations summary is rendered.
    await page.locator("#promotion_operations_tab").click();
    const system = page.locator('[data-operations-summary="system"]');

    // Then: the data remains exact and no arbitrary comma-delimited tail receives nowrap semantics.
    expect(await system.textContent()).toContain(summary);
    expect(await system.locator(".options-operations-summary-tail").count()).toBe(0);
  });

  test("fails closed when experiments and promotions have no canonical projection", async () => {
    // Given: the adverse workbench has no scenario, promotions, or usable chain.
    await mount(derivativesPaperAdverseFixture);

    // When: the remaining research panels are inspected.
    await page.locator("#experiment_lab_tab").click();
    const experimentText = await page.locator("#experiment_lab").textContent();
    await page.locator("#promotion_operations_tab").click();
    const operationsText = await page.locator("#promotion_operations").textContent();

    // Then: absent projections are explicit and no mutation control appears.
    expect(experimentText).toContain("Not projected");
    expect(operationsText).toContain("Promotion candidates unavailable");
    expect(await page.getByRole("button", { name: /execute|order|broker|provider/i }).count()).toBe(
      0,
    );
  });
});

async function metric(scope: ReturnType<Page["locator"]>, key: string): Promise<string> {
  return (await scope.locator(`[data-strategy-metric="${key}"] dd`).textContent()) ?? "";
}

async function selectedLegIds(scope: ReturnType<Page["locator"]>): Promise<readonly string[]> {
  return scope
    .locator("[data-selected-leg]")
    .evaluateAll((legs) => legs.map((leg) => leg.getAttribute("data-selected-leg") ?? ""));
}

function missingGreekFixture(): unknown {
  const workbench = derivativesPaperHappyFixture.workspaces.derivatives.workbench;
  const rows = workbench.chain.rows.map((row) =>
    row.call?.contract_id === "aapl-20260821-c-200"
      ? { ...row, call: { ...row.call, gamma: null } }
      : row,
  );
  if (!rows.some((row) => row.call?.contract_id === "aapl-20260821-c-200")) {
    throw new ResearchPanelTestError("call fixture required");
  }
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
            rows,
          },
        },
      },
    },
  };
}

function systemSummaryFixture(summary: string): unknown {
  return {
    ...derivativesPaperHappyFixture,
    workspaces: {
      ...derivativesPaperHappyFixture.workspaces,
      system: { ...derivativesPaperHappyFixture.workspaces.system, summary },
    },
  };
}

class ResearchPanelTestError extends Error {
  override readonly name = "ResearchPanelTestError";
}
