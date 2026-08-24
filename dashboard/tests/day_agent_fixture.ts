import { snapshotV2 } from "./snapshot_v2_fixture";

type Lane = "happy" | "kr-corrupt" | "us-corrupt";

export function dayAgentFixture(lane: Lane): unknown {
  const generatedAt = new Date().toISOString();
  const usState = lane === "us-corrupt" ? ("corrupt" as const) : ("populated" as const);
  const krState = lane === "kr-corrupt" ? ("corrupt" as const) : ("populated" as const);
  const markets = [
    item(
      "day_agent.us.paper",
      "US · Alpaca Paper",
      usState,
      "NVDA · entry 200.05 · stop 199.50 · targets 200.60/201.15 · rationale theme and flow evidence · outcome targeted · immutable paper history",
      generatedAt,
    ),
    item(
      "day_agent.us.shadow",
      "US · Shadow",
      usState,
      "close learning · supported 2 · refuted 1 · inconclusive 0",
      generatedAt,
    ),
    item(
      "day_agent.us.capsules",
      "US · Shadow capsule states",
      usState,
      "active 1 · queued 1 · suspended 0",
      generatedAt,
    ),
    item(
      "day_agent.us.recommendation.1",
      "US · Shadow · NVDA",
      usState,
      "entry 200.05 · stop 199.50 · targets 200.60/201.15 · rationale theme and flow evidence · outcome pending",
      generatedAt,
    ),
    item(
      "day_agent.kr.shadow",
      "KR · Shadow · provider read-only",
      krState,
      krState === "corrupt"
        ? "KR evidence invalid"
        : "close learning · supported 1 · refuted 0 · inconclusive 1",
      generatedAt,
    ),
    item(
      "day_agent.kr.capsules",
      "KR · Shadow capsule states",
      krState,
      "active 1 · queued 0 · suspended 1",
      generatedAt,
    ),
    item(
      "day_agent.kr.recommendation.1",
      "KR · Shadow · provider read-only",
      krState,
      "005930 · entry 10150 · stop 10020 · targets 10300/10450 · rationale entry · outcome active",
      generatedAt,
    ),
  ];
  const research = [
    item(
      "day_agent.us.learning",
      "US · Shadow close learning",
      usState,
      "close learning · supported 2 · refuted 1 · inconclusive 0",
      generatedAt,
    ),
    item(
      "day_agent.us.policy",
      "US · Shadow next-session policy",
      usState,
      "next session · maintain_evidence · report 0123456789ab",
      generatedAt,
    ),
    item(
      "day_agent.kr.learning",
      "KR · Shadow close learning",
      krState,
      krState === "corrupt"
        ? "close learning unavailable"
        : "close learning · supported 1 · refuted 0 · inconclusive 1",
      generatedAt,
    ),
    item(
      "day_agent.kr.policy",
      "KR · Shadow next-session policy",
      krState,
      krState === "corrupt"
        ? "next-session policy unavailable"
        : "next session · keep_shadow · report abcdef012345",
      generatedAt,
    ),
  ];
  const items = [...markets, ...research];
  return {
    ...snapshotV2,
    snapshot_id: crypto.randomUUID(),
    generated_at: generatedAt,
    workspaces: {
      ...snapshotV2.workspaces,
      markets: withItems(snapshotV2.workspaces.markets, markets, generatedAt),
      research: withItems(snapshotV2.workspaces.research, research, generatedAt),
    },
    traces: {
      nodes: [
        ...snapshotV2.traces.nodes,
        ...items.flatMap((value) => traceNodes(value, generatedAt)),
      ],
      edges: [...snapshotV2.traces.edges, ...items.map(traceEdge)],
    },
    projection: {
      ...snapshotV2.projection,
      total_count: snapshotV2.projection.total_count + items.length,
      projected_count: snapshotV2.projection.projected_count + items.length,
    },
  };
}

function withItems<
  T extends {
    readonly items: readonly unknown[];
    readonly total_count: number;
    readonly projected_count: number;
    readonly truncated: boolean;
    readonly observed_at: string | null;
    readonly freshness: { readonly as_of: string };
  },
>(workspace: T, additions: readonly unknown[], generatedAt: string) {
  return {
    ...workspace,
    observed_at: generatedAt,
    freshness: { ...workspace.freshness, as_of: generatedAt, age_seconds: 0 },
    total_count: workspace.total_count + additions.length,
    projected_count: workspace.projected_count + additions.length,
    truncated: false,
    items: [...additions, ...workspace.items],
  };
}

function item(
  itemId: string,
  label: string,
  state: "populated" | "corrupt",
  value: string,
  observedAt: string,
) {
  return {
    item_id: itemId,
    kind: "research" as const,
    label,
    state,
    value,
    observed_at: observedAt,
    trace_id: `trace.${itemId}`,
  };
}

function traceNodes(value: ReturnType<typeof item>, observedAt: string) {
  const blocked = value.state === "corrupt";
  const safeRef = "a".repeat(64);
  return [
    {
      node_id: value.trace_id,
      kind: "source_receipt" as const,
      label: value.label,
      observed_at: observedAt,
      safe_ref: safeRef,
      state: blocked ? ("unavailable" as const) : ("accepted" as const),
      source_namespace: "dashboard.day_agent",
    },
    {
      node_id: `${value.trace_id}.terminal`,
      kind: blocked ? ("blocker_terminal" as const) : ("reviewer_decision" as const),
      label: `${value.label} projection`,
      observed_at: observedAt,
      safe_ref: safeRef,
      state: blocked ? ("blocked" as const) : ("accepted" as const),
      source_namespace: "dashboard.day_agent",
    },
  ];
}

function traceEdge(value: ReturnType<typeof item>) {
  return {
    from_node_id: value.trace_id,
    to_node_id: `${value.trace_id}.terminal`,
    kind: value.state === "corrupt" ? ("blocked_by" as const) : ("reviewed_by" as const),
  };
}
