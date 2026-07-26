import { snapshotV2 } from "../snapshot_v2_fixture";

const PROVIDERS = [
  ["fred", "realtime", "populated"],
  ["alfred", "research_only", "populated"],
  ["treasury", "delayed", "stale"],
  ["cftc", "research_only", "populated"],
  ["opendart", "delayed", "populated"],
  ["kis", "unavailable", "unavailable"],
  ["ls", "realtime", "populated"],
  ["alpaca", "realtime", "populated"],
] as const;

export function marketsDataSourcesFixture(): unknown {
  const observedAt = snapshotV2.generated_at;
  const dataItems = PROVIDERS.map(([provider, entitlement, state]) => ({
    item_id: `source.${provider}`,
    kind: "metric" as const,
    label: provider.toUpperCase(),
    state,
    value: entitlement,
    observed_at: state === "unavailable" ? null : observedAt,
    trace_id: "trace-data",
  }));
  return {
    ...snapshotV2,
    workspaces: {
      ...snapshotV2.workspaces,
      markets: {
        ...snapshotV2.workspaces.markets,
        state: "populated" as const,
        summary: "KR/US authoritative session context",
        total_count: 3,
        projected_count: 3,
        items: [
          {
            item_id: "market.kr.session",
            kind: "metric" as const,
            label: "KR session",
            state: "populated" as const,
            value: "scheduled",
            observed_at: observedAt,
            trace_id: "trace-markets",
          },
          {
            item_id: "market.us.session",
            kind: "metric" as const,
            label: "US session",
            state: "stale" as const,
            value: "closed",
            observed_at: observedAt,
            trace_id: "trace-markets",
          },
          {
            item_id: "market.us.quote",
            kind: "metric" as const,
            label: "US current quote",
            state: "populated" as const,
            value: "999.99",
            observed_at: observedAt,
            trace_id: "trace-markets",
          },
        ],
      },
      data_sources: {
        ...snapshotV2.workspaces.data_sources,
        state: "populated" as const,
        summary: "Eight provider authority receipts",
        total_count: PROVIDERS.length,
        projected_count: PROVIDERS.length,
        items: dataItems,
        capabilities: PROVIDERS.map(([provider, entitlement, state]) => ({
          capability_id: `${provider}.authoritative`,
          provider,
          label: providerLabel(provider),
          state,
          entitlement,
          observed_at: state === "unavailable" ? null : observedAt,
          trace_id: "trace-data",
        })),
      },
    },
    traces: {
      nodes: [
        ...snapshotV2.traces.nodes,
        terminal("trace-markets-terminal", observedAt),
        terminal("trace-data-terminal", observedAt),
        blocker("trace-data-blocker", observedAt),
      ],
      edges: [
        ...snapshotV2.traces.edges,
        {
          from_node_id: "trace-markets",
          to_node_id: "trace-markets-terminal",
          kind: "reviewed_by",
        },
        { from_node_id: "trace-data", to_node_id: "trace-data-terminal", kind: "reviewed_by" },
        { from_node_id: "trace-data", to_node_id: "trace-data-blocker", kind: "blocked_by" },
      ],
    },
    projection: {
      ...snapshotV2.projection,
      total_count: 11,
      projected_count: 11,
    },
  };
}

function providerLabel(provider: string): string {
  return provider === "fred"
    ? "FRED-권위있는장기식별자ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    : provider.toUpperCase();
}

function blocker(nodeId: string, observedAt: string) {
  return {
    node_id: nodeId,
    kind: "blocker_terminal" as const,
    label: "provider entitlement unavailable",
    observed_at: observedAt,
    safe_ref: null,
    state: "blocked" as const,
    source_namespace: "dashboard.fixture.authority",
  };
}

function terminal(nodeId: string, observedAt: string) {
  return {
    node_id: nodeId,
    kind: "reviewer_decision" as const,
    label: "authoritative workspace review",
    observed_at: observedAt,
    safe_ref: null,
    state: "accepted" as const,
    source_namespace: "dashboard.fixture.authority",
  };
}
