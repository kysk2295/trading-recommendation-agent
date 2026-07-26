import { describe, expect, test } from "bun:test";
import type { WorkspaceItem } from "../src/render";
import {
  autonomousControlRows,
  launchdRows,
  milestoneRows,
  systemFamilyRoster,
} from "../src/workspaces/system";
import { snapshotV2 } from "./snapshot_v2_fixture";

describe("System workspace evidence", () => {
  test("keeps exact M0-M10 in machine order", () => {
    const items = Array.from({ length: 11 }, (_, index) => ({
      ...item(`system.m${index}`, `M${index}`),
    }));

    expect(milestoneRows(items).map((item) => item.label)).toEqual([
      "M0",
      "M1",
      "M2",
      "M3",
      "M4",
      "M5",
      "M6",
      "M7",
      "M8",
      "M9",
      "M10",
    ]);
  });

  test("separates launchd operational aliases from the exact six product families", () => {
    const items = [item("system.operation.launchd-delivery", "delivery")];

    expect(launchdRows(items)).toHaveLength(1);
    expect(
      systemFamilyRoster(snapshotV2.workspaces.command_center.agents).map((row) => row.id),
    ).toEqual([
      "opportunity_manager",
      "day_trading",
      "swing_trading",
      "systematic_quant",
      "derivatives_research",
      "market_context",
    ]);
    expect(
      systemFamilyRoster(snapshotV2.workspaces.command_center.agents).some(
        (row) => row.id === "delivery",
      ),
    ).toBe(false);
    expect(
      systemFamilyRoster(snapshotV2.workspaces.command_center.agents).some(
        (row) => row.id === "allocation_manager",
      ),
    ).toBe(false);
  });

  test("keeps authorized and blocked autonomous control receipts typed", () => {
    const items = [
      {
        ...item("system.autonomous.scheduler", "Autonomous scheduler"),
        item_id: "system.autonomous.scheduler",
        label: "Autonomous scheduler",
        value: "passed",
      },
      {
        ...item("system.autonomous.budget", "Budget gate"),
        item_id: "system.autonomous.budget",
        label: "Budget gate",
        state: "blocked" as const,
        value: "family_token_budget_exhausted",
      },
    ];

    expect(autonomousControlRows(items).map((row) => [row.label, row.state, row.value])).toEqual([
      ["Autonomous scheduler", "populated", "passed"],
      ["Budget gate", "blocked", "family_token_budget_exhausted"],
    ]);
  });
});

function item(itemId: string, label: string): WorkspaceItem {
  return {
    item_id: itemId,
    kind: "system",
    label,
    state: "populated",
    value: "passed",
    observed_at: "2026-07-26T08:00:00Z",
    trace_id: "trace-system",
  };
}
