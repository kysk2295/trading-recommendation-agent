import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import { resolve } from "node:path";
import { type Browser, chromium, type Page } from "playwright";
import { dashboardSnapshotV2Schema } from "../src/schema_v2";
import { snapshotV2 } from "./snapshot_v2_fixture";

let browser: Browser;
let page: Page;

beforeAll(async () => {
  const result = await Bun.build({
    entrypoints: [resolve(import.meta.dir, "../src/workspaces/research_strategies_workspace.ts")],
    format: "esm",
    target: "browser",
  });
  expect(result.success).toBeTrue();
  const output = result.outputs[0];
  if (output === undefined) throw new ResearchBoardTestError("browser bundle missing");
  browser = await chromium.launch({ channel: "chrome", headless: true });
  page = await browser.newPage();
  await page.setContent("<!doctype html><html><body></body></html>");
  await page.addScriptTag({
    type: "module",
    content: `${await output.text()}\nwindow.mountResearch = (snapshot) => {
      const drawer = { open: () => undefined };
      document.body.replaceChildren(
        renderResearchStrategiesWorkspace("research", snapshot, drawer, {
          interactions: [], directedJobs: [], autonomousTasks: []
        })
      );
    };`,
  });
});

afterAll(async () => {
  await browser.close();
});

describe("6-Agent Research Board", () => {
  test("renders one input-decision-result-next-wake row for every research family", async () => {
    const snapshot = dashboardSnapshotV2Schema.parse(snapshotV2);
    snapshot.workspaces.research.agent_cycles[0] = {
      agent_family_id: "opportunity_manager",
      cycle_state: "completed",
      result_status: "completed",
      input_source: "opportunity.candidates",
      decision_kind: "investigate_candidate",
      result_summary: "AAPL candidate artifact recorded",
      artifact_count: 1,
      observed_at: "2026-07-26T03:00:00Z",
      next_wake_kind: "new_evidence",
      next_wake_at: null,
      order_authority: false,
    };

    await page.evaluate(`window.mountResearch(${JSON.stringify(snapshot)})`);

    const board = page.getByRole("region", { name: "6-Agent Research Board" });
    expect(await board.getByRole("row").count()).toBe(7);
    expect(await board.getByRole("columnheader").allTextContents()).toEqual([
      "Agent",
      "Input",
      "Decision",
      "Result",
      "Next wake",
      "Evidence Trace",
    ]);
    expect(await board.getByText("기회 관리자 · OPPORTUNITY MANAGER").count()).toBe(1);
    expect(await board.getByText("파생상품 연구 · DERIVATIVES RESEARCH").count()).toBe(1);
    expect(await board.getByText("opportunity.candidates").count()).toBe(1);
    expect(await board.getByText("investigate_candidate").count()).toBe(1);
    expect(await board.getByText(/artifacts:1 · order:false/).count()).toBe(1);
    expect(await board.getByText("new_evidence").count()).toBe(1);
  });
});

class ResearchBoardTestError extends Error {}
