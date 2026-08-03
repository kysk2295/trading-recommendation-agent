import type {
  OptionChainCellInput,
  OptionsWorkbenchInput,
} from "../../src/options_workbench_schema";
import { dashboardSnapshotV2Schema } from "../../src/schema_v2";
import { snapshotV2 } from "../snapshot_v2_fixture";

// Fixture-only exception: complete paper and options scenarios remain readable above 250 lines.
const observedAt = snapshotV2.generated_at;
const quoteTraceId = "trace.derivatives.options.current";
const optionsTerminalTraceId = "trace.derivatives.options.reviewer";
const promotionTraceId = "trace.derivatives.options.promotion";
const promotionReviewerTraceId = "trace.derivatives.options.promotion.reviewer";
const promotionBlockerTraceId = "trace.derivatives.options.promotion.blocker";

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

const primaryAgents = [
  "opportunity_manager",
  "day_trading",
  "swing_trading",
  "systematic_quant",
  "derivatives_research",
  "market_context",
] as const;

export function populatedOptionsWorkbenchFixture(
  firstCallOverride: Partial<Pick<OptionChainCellInput, "state" | "selectable">> = {},
): OptionsWorkbenchInput {
  const market = section(
    "Research demonstration only · indicative Alpaca context; KIS and LS unavailable",
  );
  const rows = ["195", "200", "205"].map((strike, index) => ({
    strike,
    call: optionCell(
      `aapl-20260821-c-${strike}`,
      "call",
      index === 0 ? firstCallOverride : undefined,
    ),
    put: optionCell(`aapl-20260821-p-${strike}`, "put"),
  }));
  return {
    schema_version: 1,
    selected_view: "market_pulse",
    market,
    chain: {
      ...section("Research demonstration only · bounded indicative option chain"),
      underlying: "AAPL",
      selected_expiration: "2026-08-21",
      expirations: ["2026-08-21", "2026-09-18"],
      total_count: rows.length,
      projected_count: rows.length,
      truncated: false,
      rows,
    },
    scenario: {
      state: "research_only",
      currency: "USD",
      spot: "200",
      legs: [
        {
          contract_id: "aapl-20260821-c-200",
          action: "long",
          side: "call",
          strike: "200",
          premium: "5",
          quantity: 1,
          multiplier: 100,
          trace_id: quoteTraceId,
        },
      ],
      scenario_spots: ["190", "200", "210"],
      trace_id: quoteTraceId,
    },
    agent: section("Derivatives agent receipt available for research review"),
    experiment: section("Experiment evidence available for research review"),
    promotions: [
      {
        promotion_id: "promotion-options-research-001",
        state: "held",
        passed_gate_count: 6,
        total_gate_count: 7,
        blockers: ["manual_approval_required"],
        trace_id: promotionTraceId,
      },
    ],
  };
}

export const derivativesPaperHappyFixture = dashboardSnapshotV2Schema.parse({
  ...snapshotV2,
  workspaces: {
    ...snapshotV2.workspaces,
    command_center: {
      ...snapshotV2.workspaces.command_center,
      agents: primaryAgents.map((agentId) => ({
        agent_id: agentId,
        label: agentId
          .split("_")
          .map((word) => `${word[0]?.toUpperCase() ?? ""}${word.slice(1)}`)
          .join(" "),
        role: `${agentId} autonomous research`,
        capabilities: ["conversation", "directed_tool", "autonomous_research"],
        runtime_state: "idle",
        trace_id: snapshotV2.workspaces.command_center.trace_id,
      })),
    },
    derivatives: {
      ...snapshotV2.workspaces.derivatives,
      state: "populated",
      summary: "DEMONSTRATION · RESEARCH ONLY · bounded derivatives research context",
      total_count: derivativeItems.length,
      projected_count: derivativeItems.length,
      items: derivativeItems,
      workbench: populatedOptionsWorkbenchFixture(),
    },
    paper: {
      ...snapshotV2.workspaces.paper,
      state: "populated",
      summary: "DEMONSTRATION · RESEARCH ONLY · finalized Paper lifecycle",
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
      {
        node_id: optionsTerminalTraceId,
        kind: "reviewer_decision",
        label: "Options research review complete",
        observed_at: observedAt,
        safe_ref: "c".repeat(64),
        state: "accepted",
        source_namespace: "derivatives.options.current",
      },
      {
        node_id: promotionTraceId,
        kind: "source_receipt",
        label: "Options promotion evidence",
        observed_at: observedAt,
        safe_ref: "c".repeat(64),
        state: "accepted",
        source_namespace: "derivatives.options.promotion",
      },
      {
        node_id: promotionReviewerTraceId,
        kind: "reviewer_decision",
        label: "Independent Reviewer decision accepted",
        observed_at: observedAt,
        safe_ref: "c".repeat(64),
        state: "accepted",
        source_namespace: "derivatives.options.promotion",
      },
      blocker(promotionBlockerTraceId, "manual approval pending"),
    ],
    edges: [
      ...snapshotV2.traces.edges,
      { from_node_id: quoteTraceId, to_node_id: optionsTerminalTraceId, kind: "reviewed_by" },
      { from_node_id: promotionTraceId, to_node_id: promotionReviewerTraceId, kind: "reviewed_by" },
      {
        from_node_id: promotionReviewerTraceId,
        to_node_id: promotionBlockerTraceId,
        kind: "blocked_by",
      },
    ],
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
      workbench: unavailableOptionsWorkbenchFixture(),
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

function section(summary: string) {
  return {
    state: "populated" as const,
    observed_at: observedAt,
    blocker_code: null,
    summary,
    trace_id: quoteTraceId,
  };
}

function optionCell(
  contractId: string,
  side: "call" | "put",
  override: Partial<Pick<OptionChainCellInput, "state" | "selectable">> = {},
) {
  return {
    contract_id: contractId,
    side,
    provider: "alpaca" as const,
    state: "indicative" as const,
    bid: "1.00",
    ask: "1.20",
    last: "1.10",
    implied_volatility: "0.31",
    delta: side === "call" ? "0.50" : "-0.50",
    gamma: "0.02",
    theta: "-0.01",
    vega: "0.10",
    volume: 0,
    open_interest: 0,
    observed_at: observedAt,
    trace_id: quoteTraceId,
    selectable: true,
    ...override,
  };
}

function unavailableOptionsWorkbenchFixture(): OptionsWorkbenchInput {
  const populated = populatedOptionsWorkbenchFixture({ state: "unavailable", selectable: false });
  return {
    ...populated,
    market: { ...populated.market, trace_id: snapshotV2.workspaces.derivatives.trace_id },
    agent: { ...populated.agent, trace_id: snapshotV2.workspaces.derivatives.trace_id },
    experiment: { ...populated.experiment, trace_id: snapshotV2.workspaces.derivatives.trace_id },
    scenario: null,
    promotions: [],
    chain: {
      ...populated.chain,
      trace_id: snapshotV2.workspaces.derivatives.trace_id,
      state: "unavailable",
      observed_at: null,
      blocker_code: "canonical_option_chain_missing",
      summary: "Canonical option chain unavailable",
      total_count: 0,
      projected_count: 0,
      truncated: false,
      rows: [],
    },
  };
}
