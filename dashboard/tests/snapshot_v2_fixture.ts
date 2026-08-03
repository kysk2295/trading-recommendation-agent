// Fixture-only exception: canonical multi-state snapshot data remains readable above 250 lines.
const generatedAt = "2026-07-26T03:00:00Z";
const traceIds = {
  command_center: "trace-command",
  overview: "trace-overview",
  markets: "trace-markets",
  data_sources: "trace-data",
  research: "trace-research",
  strategies: "trace-strategies",
  derivatives: "trace-derivatives",
  paper: "trace-paper",
  system: "trace-system",
} as const;

function sourceState(traceId: string) {
  return {
    state: "empty" as const,
    observed_at: generatedAt,
    freshness: {
      policy_id: "snapshot-current",
      age_seconds: 0,
      as_of: generatedAt,
    },
    blocker_code: null,
    summary: "권위 있는 읽기 완료, 항목 없음",
    total_count: 0,
    projected_count: 0,
    truncated: false,
    trace_id: traceId,
    items: [],
  };
}

function unavailableOptionsWorkbench(traceId: string) {
  return {
    schema_version: 1 as const,
    selected_view: "market_pulse" as const,
    market: unavailableSection("canonical_option_market_missing", traceId),
    chain: {
      ...unavailableSection("canonical_option_chain_missing", traceId),
      underlying: null,
      selected_expiration: null,
      expirations: [],
      total_count: 0,
      projected_count: 0,
      truncated: false,
      rows: [],
    },
    scenario: null,
    agent: unavailableSection("derivatives_agent_receipt_missing", traceId),
    experiment: unavailableSection("options_experiment_missing", traceId),
    promotions: [],
  };
}

function unavailableSection(blockerCode: string, traceId: string) {
  return {
    state: "unavailable" as const,
    observed_at: null,
    blocker_code: blockerCode,
    summary: "Canonical options research evidence unavailable",
    trace_id: traceId,
  };
}

const baseNodes = [
  ...Object.entries(traceIds).map(([workspace, nodeId]) => ({
    node_id: nodeId,
    kind: "source_receipt" as const,
    label: `${workspace} source`,
    observed_at: generatedAt,
    safe_ref: "a".repeat(64),
    state: "accepted" as const,
    source_namespace: `dashboard.fixture.${workspace}`,
  })),
  terminalNode("trace-command-terminal", "process_receipt"),
  terminalNode("trace-research-terminal", "reviewer_decision"),
  terminalNode("trace-strategies-terminal", "reviewer_decision"),
  terminalNode("trace-paper-terminal", "paper_receipt"),
  terminalNode("trace-system-terminal", "process_receipt"),
];

const baseEdges = [
  edge(traceIds.command_center, "trace-command-terminal", "executed_as"),
  edge(traceIds.research, "trace-research-terminal", "reviewed_by"),
  edge(traceIds.strategies, "trace-strategies-terminal", "reviewed_by"),
  edge(traceIds.paper, "trace-paper-terminal", "reconciled_by"),
  edge(traceIds.system, "trace-system-terminal", "executed_as"),
];

export const snapshotV2 = {
  schema_version: 2,
  snapshot_id: "019c0014-f0f5-7000-8000-000000000100",
  generated_at: generatedAt,
  source: "local-redacted-projector",
  workspaces: {
    command_center: { ...sourceState(traceIds.command_center), agents: [] },
    overview: sourceState(traceIds.overview),
    markets: sourceState(traceIds.markets),
    data_sources: { ...sourceState(traceIds.data_sources), capabilities: [] },
    research: sourceState(traceIds.research),
    strategies: sourceState(traceIds.strategies),
    derivatives: {
      ...sourceState(traceIds.derivatives),
      workbench: unavailableOptionsWorkbench(traceIds.derivatives),
    },
    paper: sourceState(traceIds.paper),
    system: sourceState(traceIds.system),
  },
  traces: { nodes: baseNodes, edges: baseEdges },
  projection: {
    redaction_policy_version: "dashboard-redaction-v2",
    reader_versions: ["fixture-v1"],
    source_schema_version: 2,
    total_count: 0,
    projected_count: 0,
    truncated: false,
  },
} as const;

const workspaceKinds = {
  command_center: "system",
  overview: "metric",
  markets: "metric",
  data_sources: "metric",
  research: "research",
  strategies: "strategy",
  derivatives: "derivative",
  paper: "paper",
  system: "system",
} as const;

const agentIds = [
  "opportunity_manager",
  "day_trading",
  "swing_trading",
  "systematic_quant",
  "derivatives_research",
  "market_context",
] as const;
const providers = [
  "fred",
  "alfred",
  "treasury",
  "cftc",
  "opendart",
  "kis",
  "ls",
  "alpaca",
] as const;
const paddingNodes = Array.from({ length: 170 }, (_, index) => ({
  node_id: paddingId(index),
  kind: "observation" as const,
  label: "N".repeat(100),
  observed_at: generatedAt,
  safe_ref: "b".repeat(64),
  state: "accepted" as const,
  source_namespace: `fixture.${"s".repeat(92)}`,
}));
const paddingEdges = [
  edge(traceIds.system, paddingId(0), "observed_by"),
  ...paddingNodes
    .slice(0, -1)
    .map((node, index) => edge(node.node_id, paddingId(index + 1), "derived_from")),
  edge(paddingId(paddingNodes.length - 1), "trace-system-terminal", "executed_as"),
];
const nearMaximumEdges = [
  ...baseEdges.filter((candidate) => candidate.from_node_id !== traceIds.system),
  ...paddingEdges,
];

export const nearMaximumSnapshotV2 = {
  ...snapshotV2,
  workspaces: {
    command_center: {
      ...populatedState("command_center", traceIds.command_center),
      agents: Array.from({ length: 12 }, (_, index) => ({
        agent_id: agentIds[index % agentIds.length],
        label: "A".repeat(40),
        role: "R".repeat(80),
        capabilities: ["conversation", "directed_tool", "autonomous_research"] as const,
        runtime_state: "idle" as const,
        trace_id: traceIds.command_center,
      })),
    },
    overview: populatedState("overview", traceIds.overview),
    markets: populatedState("markets", traceIds.markets),
    data_sources: {
      ...populatedState("data_sources", traceIds.data_sources),
      capabilities: Array.from({ length: 30 }, (_, index) => ({
        capability_id: `capability-${index}`,
        provider: providers[index % providers.length],
        label: "C".repeat(80),
        state: "populated" as const,
        entitlement: "research_only" as const,
        observed_at: generatedAt,
        trace_id: traceIds.data_sources,
      })),
    },
    research: populatedState("research", traceIds.research),
    strategies: populatedState("strategies", traceIds.strategies),
    derivatives: {
      ...populatedState("derivatives", traceIds.derivatives),
      workbench: unavailableOptionsWorkbench(traceIds.derivatives),
    },
    paper: populatedState("paper", traceIds.paper),
    system: populatedState("system", traceIds.system),
  },
  traces: {
    nodes: [...baseNodes, ...paddingNodes],
    edges: nearMaximumEdges,
  },
  projection: {
    ...snapshotV2.projection,
    reader_versions: Array.from(
      { length: 40 },
      (_, index) => `r${index.toString().padStart(3, "0")}-${"v".repeat(95)}`,
    ),
    total_count: 216,
    projected_count: 216,
  },
} as const;

function terminalNode(nodeId: string, kind: string) {
  return {
    node_id: nodeId,
    kind,
    label: "fixture accepted",
    observed_at: generatedAt,
    safe_ref: null,
    state: "accepted" as const,
    source_namespace: "dashboard.fixture",
  };
}

function populatedState(workspace: keyof typeof workspaceKinds, traceId: string) {
  return {
    ...sourceState(traceId),
    state: "populated" as const,
    summary: "S".repeat(160),
    total_count: 24,
    projected_count: 24,
    items: Array.from({ length: 24 }, (_, index) => ({
      item_id: `${workspace}-item-${index}`,
      kind: workspaceKinds[workspace],
      label: "L".repeat(80),
      state: "populated" as const,
      value: "V".repeat(160),
      observed_at: generatedAt,
      trace_id: traceId,
    })),
  };
}

function edge(fromNodeId: string, toNodeId: string, kind: string) {
  return {
    from_node_id: fromNodeId,
    to_node_id: toNodeId,
    kind,
  };
}

function paddingId(index: number): string {
  return `p${index.toString().padStart(3, "0")}-${"n".repeat(95)}`;
}
