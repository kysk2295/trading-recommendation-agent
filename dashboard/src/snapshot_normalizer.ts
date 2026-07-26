import type { DashboardSnapshotV1 } from "./schema";
import { type DashboardSnapshotV2, dashboardSnapshotV2Schema } from "./schema_v2";

const forbiddenKey =
  /(?:^|_)(?:api_?key|auth|bearer|cookie|credential|password|secret|token|account_?id|account_?fingerprint|session_?id)(?:$|_)/i;
export type NormalizedSnapshot = {
  readonly canonical: DashboardSnapshotV2;
  readonly rollbackV1: DashboardSnapshotV1;
  readonly inputVersion: 2;
};

export type SnapshotParseResult =
  | { readonly ok: true; readonly value: NormalizedSnapshot }
  | { readonly ok: false; readonly reason: "forbidden_field" | "invalid_snapshot" };

export function parseAndNormalizeSnapshot(payload: unknown): SnapshotParseResult {
  if (hasForbiddenKey(payload) || payloadBytes(payload) > 256 * 1024) {
    return { ok: false, reason: "forbidden_field" };
  }
  const v2 = dashboardSnapshotV2Schema.safeParse(payload);
  if (!v2.success) {
    return { ok: false, reason: "invalid_snapshot" };
  }
  return {
    ok: true,
    value: { canonical: v2.data, rollbackV1: downProjectV1(v2.data), inputVersion: 2 },
  };
}

function payloadBytes(payload: unknown): number {
  const serialized = JSON.stringify(payload);
  return serialized === undefined ? 0 : new TextEncoder().encode(serialized).byteLength;
}

export function downProjectV1(snapshot: DashboardSnapshotV2): DashboardSnapshotV1 {
  const generatedAt = snapshot.generated_at;
  return {
    schema_version: 1,
    generated_at: generatedAt,
    source: "local-runtime",
    markets: [
      { market_id: "kr", label: "한국", local_time: generatedAt, state: "closed" },
      { market_id: "us", label: "미국", local_time: generatedAt, state: "closed" },
    ],
    forward: {
      session_date: generatedAt.slice(0, 10),
      eligible: false,
      ranking_cycles: 0,
      watch_cycles: 0,
      failed_watch_cycles: 0,
      read_retries: 0,
      read_retry_failures: 0,
      candidate_input_cycles: 0,
      candidate_inputs: 0,
      recommendations: 0,
      blockers:
        snapshot.workspaces.overview.blocker_code === null
          ? []
          : [snapshot.workspaces.overview.blocker_code],
      incidents: [],
    },
    agents: [],
    recommendations: [],
    signals: [],
    research: {
      status: rollbackResearchState(snapshot.workspaces.research.state),
      session_date: generatedAt.slice(0, 10),
      summary: snapshot.workspaces.research.summary,
    },
    account: {
      status: "unavailable",
      session_date: generatedAt.slice(0, 10),
      observed_at: snapshot.workspaces.paper.observed_at,
      currency: "USD",
      equity: null,
      daily_pnl: null,
      realized_pnl: null,
      unrealized_pnl: null,
      planned_open_risk: null,
      open_positions: 0,
      open_orders: 0,
    },
  };
}

function hasForbiddenKey(value: unknown): boolean {
  if (Array.isArray(value)) {
    return value.some(hasForbiddenKey);
  }
  if (value === null || typeof value !== "object") {
    return false;
  }
  return Object.entries(value).some(
    ([key, nested]) => forbiddenKey.test(key) || hasForbiddenKey(nested),
  );
}

function rollbackResearchState(
  state: DashboardSnapshotV2["workspaces"]["research"]["state"],
): "ready" | "blocked" | "pending" | "unavailable" {
  switch (state) {
    case "populated":
    case "empty":
      return "ready";
    case "blocked":
    case "corrupt":
    case "error":
      return "blocked";
    case "loading":
      return "pending";
    case "stale":
    case "unavailable":
      return "unavailable";
  }
}
