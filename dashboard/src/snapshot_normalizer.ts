import { createHash, randomUUID } from "node:crypto";
import type { z } from "zod";
import type { DashboardSnapshotV1 } from "./schema";
import {
  type DashboardSnapshotInputV2,
  type DashboardSnapshotV2,
  dashboardSnapshotV2Schema,
} from "./schema_v2";

const forbiddenKey =
  /(?:^|_)(?:api_?key|auth|bearer|cookie|credential|password|secret|token|account_?id|account_?fingerprint|session_?id)(?:$|_)/i;
const V1_TERMINALS = [
  { suffix: "review", kind: "reviewer_decision", edge: "reviewed_by" },
  { suffix: "lifecycle", kind: "lifecycle_decision", edge: "decided_by" },
  { suffix: "paper", kind: "paper_receipt", edge: "reconciled_by" },
  { suffix: "process", kind: "process_receipt", edge: "executed_as" },
  { suffix: "deploy", kind: "deployment_receipt", edge: "deployed_as" },
] as const;

export type NormalizedSnapshot = {
  readonly canonical: DashboardSnapshotV2;
  readonly rollbackV1: DashboardSnapshotV1;
  readonly inputVersion: 1 | 2;
};

export type SnapshotParseResult =
  | { readonly ok: true; readonly value: NormalizedSnapshot }
  | { readonly ok: false; readonly reason: "forbidden_field" | "invalid_snapshot" };

export function parseAndNormalizeSnapshot(
  payload: unknown,
  v1Schema: z.ZodType<DashboardSnapshotV1>,
): SnapshotParseResult {
  if (hasForbiddenKey(payload) || payloadBytes(payload) > 256 * 1024) {
    return { ok: false, reason: "forbidden_field" };
  }
  const v1 = v1Schema.safeParse(payload);
  if (v1.success) {
    const canonical = normalizeV1(v1.data);
    return {
      ok: true,
      value: { canonical, rollbackV1: v1.data, inputVersion: 1 },
    };
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

function normalizeV1(snapshot: DashboardSnapshotV1): DashboardSnapshotV2 {
  const traceId = `v1-${createHash("sha256").update(snapshot.generated_at).digest("hex").slice(0, 24)}`;
  const base = sourceState(snapshot.generated_at, traceId);
  const input: DashboardSnapshotInputV2 = {
    schema_version: 2,
    snapshot_id: randomUUID(),
    generated_at: snapshot.generated_at,
    source: "local-redacted-projector",
    workspaces: {
      command_center: {
        ...base,
        agents: [],
      },
      overview: base,
      markets: base,
      data_sources: { ...base, capabilities: [] },
      research: { ...base, summary: snapshot.research.summary },
      strategies: base,
      derivatives: base,
      paper: base,
      system: base,
    },
    traces: {
      nodes: [
        {
          node_id: traceId,
          kind: "source_receipt",
          label: "v1 compatibility snapshot",
          observed_at: snapshot.generated_at,
          safe_ref: null,
          state: "accepted" as const,
          source_namespace: "dashboard.v1",
        },
        ...V1_TERMINALS.map((terminal) => ({
          node_id: `${traceId}-${terminal.suffix}`,
          kind: terminal.kind,
          label: `v1 compatibility ${terminal.suffix}`,
          observed_at: snapshot.generated_at,
          safe_ref: null,
          state: "accepted" as const,
          source_namespace: "dashboard.v1",
        })),
      ],
      edges: V1_TERMINALS.map((terminal) => ({
        from_node_id: traceId,
        to_node_id: `${traceId}-${terminal.suffix}`,
        kind: terminal.edge,
      })),
    },
    projection: {
      redaction_policy_version: "dashboard-redaction-v2",
      reader_versions: ["v1-compatibility"],
      source_schema_version: 1,
      total_count: 0,
      projected_count: 0,
      truncated: false,
    },
  };
  return dashboardSnapshotV2Schema.parse(input);
}

function sourceState(generatedAt: string, traceId: string) {
  return {
    state: "empty" as const,
    observed_at: generatedAt,
    freshness: { policy_id: "v1-compatibility", age_seconds: 0, as_of: generatedAt },
    blocker_code: null,
    summary: "v1 호환 입력에서 정규화됨",
    total_count: 0,
    projected_count: 0,
    truncated: false,
    trace_id: traceId,
    items: [],
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
