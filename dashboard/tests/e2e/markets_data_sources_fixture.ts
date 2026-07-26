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
  const providerItems = PROVIDERS.map(([provider, entitlement, state]) => ({
    item_id: `source.${provider}`,
    kind: "metric" as const,
    label: providerLabel(provider),
    state,
    value: entitlement,
    observed_at: state === "unavailable" ? null : observedAt,
    trace_id: providerTraceId(provider),
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
          marketSession("kr", "scheduled", "populated", observedAt),
          marketSession("us", "closed", "stale", observedAt),
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
        items: providerItems,
        capabilities: PROVIDERS.map(([provider, entitlement, state]) => ({
          capability_id: `${provider}.authoritative`,
          provider,
          label: providerLabel(provider),
          state,
          entitlement,
          observed_at: state === "unavailable" ? null : observedAt,
          trace_id: providerTraceId(provider),
        })),
      },
    },
    traces: {
      nodes: [
        ...snapshotV2.traces.nodes,
        terminal("trace-markets-terminal", observedAt),
        terminal("trace-data-terminal", observedAt),
        calendarSource("kr", observedAt),
        calendarSource("us", observedAt),
        ...PROVIDERS.flatMap(([provider, , state]) => providerNodes(provider, state, observedAt)),
      ],
      edges: [
        ...snapshotV2.traces.edges,
        {
          from_node_id: "trace-markets",
          to_node_id: "trace-markets-terminal",
          kind: "reviewed_by",
        },
        { from_node_id: "trace-data", to_node_id: "trace-data-terminal", kind: "reviewed_by" },
        ...PROVIDERS.flatMap(([provider, , state]) => providerEdges(provider, state)),
      ],
    },
    projection: { ...snapshotV2.projection, total_count: 11, projected_count: 11 },
  };
}

function marketSession(
  market: "kr" | "us",
  value: "scheduled" | "closed",
  state: "populated" | "stale",
  observedAt: string,
) {
  return {
    item_id: `market.${market}.session`,
    kind: "metric" as const,
    label: `${market.toUpperCase()} session`,
    state,
    value,
    observed_at: observedAt,
    trace_id: `trace.markets.calendar.${market}`,
  };
}

function calendarSource(market: "kr" | "us", observedAt: string) {
  return {
    node_id: `trace.markets.calendar.${market}`,
    kind: "source_receipt" as const,
    label: "Authoritative market calendar",
    observed_at: observedAt,
    safe_ref: null,
    state: "accepted" as const,
    source_namespace: "market_calendar.markets",
  };
}

function providerNodes(provider: string, state: string, observedAt: string) {
  const unavailable = state === "unavailable";
  const source = {
    node_id: providerTraceId(provider),
    kind: "source_receipt" as const,
    label: `${provider} authoritative receipt`,
    observed_at: observedAt,
    safe_ref: null,
    state: unavailable ? ("unavailable" as const) : ("accepted" as const),
    source_namespace: `provider.${provider}`,
  };
  if (!unavailable) return [source];
  return [
    source,
    {
      node_id: `${providerTraceId(provider)}.blocker`,
      kind: "blocker_terminal" as const,
      label: `${provider} entitlement unavailable`,
      observed_at: observedAt,
      safe_ref: null,
      state: "blocked" as const,
      source_namespace: `provider.${provider}`,
    },
  ];
}

function providerEdges(provider: string, state: string) {
  return state === "unavailable"
    ? [
        {
          from_node_id: providerTraceId(provider),
          to_node_id: `${providerTraceId(provider)}.blocker`,
          kind: "blocked_by" as const,
        },
      ]
    : [];
}

function providerTraceId(provider: string): string {
  return `trace.data_sources.${provider}`;
}

function providerLabel(provider: string): string {
  return provider === "fred"
    ? "FRED-권위있는장기식별자ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    : provider.toUpperCase();
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
