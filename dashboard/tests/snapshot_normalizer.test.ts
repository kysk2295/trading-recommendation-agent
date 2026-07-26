import { describe, expect, test } from "bun:test";
import { dashboardSnapshotSchema, dashboardSnapshotV1Schema } from "../src/schema";
import { downProjectV1, parseAndNormalizeSnapshot } from "../src/snapshot_normalizer";
import { snapshotV1 } from "./snapshot_v1_fixture";
import { snapshotV2 } from "./snapshot_v2_fixture";

describe("snapshot compatibility projections", () => {
  test("preserves the original v1 payload exactly as the rollback artifact", () => {
    const normalized = parseAndNormalizeSnapshot(snapshotV1, dashboardSnapshotV1Schema);

    expect(normalized.ok).toBe(true);
    if (!normalized.ok) return;
    expect(normalized.value.inputVersion).toBe(1);
    expect(JSON.stringify(normalized.value.rollbackV1)).toBe(JSON.stringify(snapshotV1));
    expect(normalized.value.canonical.projection.source_schema_version).toBe(1);
  });

  test("down-projects canonical v2 with exact deterministic v1 semantics", () => {
    expect(downProjectV1(dashboardSnapshotSchema.parse(snapshotV2))).toEqual({
      schema_version: 1,
      generated_at: "2026-07-26T03:00:00Z",
      source: "local-runtime",
      markets: [
        {
          market_id: "kr",
          label: "한국",
          local_time: "2026-07-26T03:00:00Z",
          state: "closed",
        },
        {
          market_id: "us",
          label: "미국",
          local_time: "2026-07-26T03:00:00Z",
          state: "closed",
        },
      ],
      forward: {
        session_date: "2026-07-26",
        eligible: false,
        ranking_cycles: 0,
        watch_cycles: 0,
        failed_watch_cycles: 0,
        read_retries: 0,
        read_retry_failures: 0,
        candidate_input_cycles: 0,
        candidate_inputs: 0,
        recommendations: 0,
        blockers: [],
        incidents: [],
      },
      agents: [],
      recommendations: [],
      signals: [],
      research: {
        status: "ready",
        session_date: "2026-07-26",
        summary: "권위 있는 읽기 완료, 항목 없음",
      },
      account: {
        status: "unavailable",
        session_date: "2026-07-26",
        observed_at: "2026-07-26T03:00:00Z",
        currency: "USD",
        equity: null,
        daily_pnl: null,
        realized_pnl: null,
        unrealized_pnl: null,
        planned_open_risk: null,
        open_positions: 0,
        open_orders: 0,
      },
    });
  });

  test("keeps canonical v2 through the browser HTTP response parser", () => {
    const parsed = dashboardSnapshotSchema.parse(snapshotV2);

    expect(JSON.stringify(parsed)).toBe(JSON.stringify(snapshotV2));
    expect(parsed.schema_version).toBe(2);
    expect(parsed.snapshot_id).toBe(snapshotV2.snapshot_id);
  });
});
