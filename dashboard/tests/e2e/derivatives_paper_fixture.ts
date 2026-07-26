import { dashboardSnapshotV2Schema } from "../../src/schema_v2";
import { snapshotV2 } from "../snapshot_v2_fixture";

const observedAt = snapshotV2.generated_at;
const quoteTraceId = "trace.derivatives.options.current";

const derivativeItems = [
  derivative(
    "derivative.quote.authority.0",
    "Current quote authority",
    "entitlement:active_realtime",
    quoteTraceId,
  ),
  derivative(
    "derivative.quote.authority.1",
    "Current quote authority",
    "redistribution:allowed",
    quoteTraceId,
  ),
  derivative(
    "derivative.quote.authority.2",
    "Current quote authority",
    "capability:healthy_current",
    quoteTraceId,
  ),
  derivative(
    "derivative.quote.authority.3",
    "Current quote authority",
    "quote:fresh",
    quoteTraceId,
  ),
  derivative("derivative.quote.0", "AAPL 200 Call", "5.00 / 5.20", quoteTraceId),
  derivative("derivative.iv.a", "AAPL 200 IV", "0.31", snapshotV2.workspaces.derivatives.trace_id),
  derivative(
    "derivative.skew.a",
    "AAPL put-call skew",
    "0.04",
    snapshotV2.workspaces.derivatives.trace_id,
  ),
  derivative(
    "derivative.term.a",
    "AAPL term 2026-08-21",
    "0.29",
    snapshotV2.workspaces.derivatives.trace_id,
  ),
  derivative(
    "derivative.future.a",
    "ES",
    "2026-09-18 · roll 2026-09-11",
    snapshotV2.workspaces.derivatives.trace_id,
  ),
  derivative(
    "derivative.cftc.positioning",
    "CFTC positioning",
    "leveraged funds · weekly",
    snapshotV2.workspaces.derivatives.trace_id,
  ),
] as const;

const paperItems = [
  paper("paper.daily_pnl", "Finalized daily PnL", "104.75", "populated"),
  paper("paper.equity", "Finalized conservative equity", "100125.25", "populated"),
  paper("paper.positions", "Finalized positions", "0 records", "empty"),
  paper("paper.orders", "Finalized open orders", "0 records", "empty"),
  paper("paper.lifecycle.entry", "Finalized entry intents", "1 records", "populated"),
  paper(
    "paper.lifecycle.protective_oco",
    "Finalized protective OCO plans",
    "1 records",
    "populated",
  ),
  paper("paper.lifecycle.reconcile", "Final reconciliation", "finalized", "populated"),
  paper("paper.lifecycle.cutoff", "Entry cutoff", "finalized", "populated"),
  paper("paper.lifecycle.eod_flat", "EOD flat", "finalized", "populated"),
] as const;

export const derivativesPaperHappyFixture = dashboardSnapshotV2Schema.parse({
  ...snapshotV2,
  workspaces: {
    ...snapshotV2.workspaces,
    derivatives: {
      ...snapshotV2.workspaces.derivatives,
      state: "populated",
      summary: "Current licensed options plus bounded derivatives research context",
      total_count: derivativeItems.length,
      projected_count: derivativeItems.length,
      items: derivativeItems,
    },
    paper: {
      ...snapshotV2.workspaces.paper,
      state: "populated",
      summary: "Finalized Paper ledger and complete lifecycle",
      total_count: paperItems.length,
      projected_count: paperItems.length,
      items: paperItems,
    },
  },
  traces: {
    nodes: [
      ...snapshotV2.traces.nodes,
      {
        node_id: quoteTraceId,
        kind: "source_receipt",
        label: "Current OPRA quote authority",
        observed_at: observedAt,
        safe_ref: "c".repeat(64),
        state: "accepted",
        source_namespace: "derivatives.options.current",
      },
    ],
    edges: snapshotV2.traces.edges,
  },
  projection: {
    ...snapshotV2.projection,
    total_count: derivativeItems.length + paperItems.length,
    projected_count: derivativeItems.length + paperItems.length,
  },
});

export const derivativesPaperAdverseFixture = dashboardSnapshotV2Schema.parse({
  ...snapshotV2,
  generated_at: "2026-07-26T03:01:00Z",
  snapshot_id: "019c0014-f0f5-7000-8000-000000000101",
  workspaces: {
    ...snapshotV2.workspaces,
    derivatives: {
      ...snapshotV2.workspaces.derivatives,
      state: "unavailable",
      observed_at: null,
      freshness: {
        ...snapshotV2.workspaces.derivatives.freshness,
        age_seconds: null,
        as_of: "2026-07-26T03:01:00Z",
      },
      blocker_code: "options_entitlement_missing",
      summary: "Research-only · current quote authority unavailable",
    },
    paper: {
      ...snapshotV2.workspaces.paper,
      state: "blocked",
      observed_at: "2026-07-26T03:01:00Z",
      freshness: {
        ...snapshotV2.workspaces.paper.freshness,
        as_of: "2026-07-26T03:01:00Z",
      },
      blocker_code: "paper_verification_incomplete",
      summary: "Paper verification incomplete",
    },
  },
  traces: {
    nodes: [
      ...snapshotV2.traces.nodes,
      blocker("trace-derivatives-blocker", "options entitlement missing"),
      blocker("trace-paper-blocker", "Paper verification incomplete"),
    ],
    edges: [
      ...snapshotV2.traces.edges.filter(
        (edge) =>
          edge.from_node_id !== snapshotV2.workspaces.derivatives.trace_id &&
          edge.from_node_id !== snapshotV2.workspaces.paper.trace_id,
      ),
      {
        from_node_id: snapshotV2.workspaces.derivatives.trace_id,
        to_node_id: "trace-derivatives-blocker",
        kind: "blocked_by",
      },
      {
        from_node_id: snapshotV2.workspaces.paper.trace_id,
        to_node_id: "trace-paper-blocker",
        kind: "blocked_by",
      },
    ],
  },
});

function derivative(itemId: string, label: string, value: string, traceId: string) {
  return {
    item_id: itemId,
    kind: "derivative" as const,
    label,
    state: "populated" as const,
    value,
    observed_at: observedAt,
    trace_id: traceId,
  };
}

function paper(itemId: string, label: string, value: string, state: "empty" | "populated") {
  return {
    item_id: itemId,
    kind: "paper" as const,
    label,
    state,
    value,
    observed_at: observedAt,
    trace_id: snapshotV2.workspaces.paper.trace_id,
  };
}

function blocker(nodeId: string, label: string) {
  return {
    node_id: nodeId,
    kind: "blocker_terminal" as const,
    label,
    observed_at: "2026-07-26T03:01:00Z",
    safe_ref: "d".repeat(64),
    state: "blocked" as const,
    source_namespace: "dashboard.fixture.blocker",
  };
}
