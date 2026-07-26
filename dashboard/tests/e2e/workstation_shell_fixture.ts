import { nearMaximumSnapshotV2, snapshotV2 } from "../snapshot_v2_fixture";

export type PublishedState =
  | "empty"
  | "error"
  | "blocked"
  | "unavailable"
  | "corrupt"
  | "stale"
  | "populated";

export function workstationStateFixture(state: PublishedState, generatedAt: string): unknown {
  const populated = state === "populated";
  const base = populated ? nearMaximumSnapshotV2 : snapshotV2;
  const workspaces = {
    command_center: {
      ...sourceState(base.workspaces.command_center, state, generatedAt),
      agents: populated ? nearMaximumSnapshotV2.workspaces.command_center.agents : [],
    },
    overview: sourceState(base.workspaces.overview, state, generatedAt),
    markets: sourceState(base.workspaces.markets, state, generatedAt),
    data_sources: {
      ...sourceState(base.workspaces.data_sources, state, generatedAt),
      capabilities: populated ? nearMaximumSnapshotV2.workspaces.data_sources.capabilities : [],
    },
    research: sourceState(base.workspaces.research, state, generatedAt),
    strategies: sourceState(base.workspaces.strategies, state, generatedAt),
    derivatives: sourceState(base.workspaces.derivatives, state, generatedAt),
    paper: sourceState(base.workspaces.paper, state, generatedAt),
    system: sourceState(base.workspaces.system, state, generatedAt),
  };
  return {
    ...base,
    snapshot_id: crypto.randomUUID(),
    generated_at: generatedAt,
    workspaces,
    traces: traceGraphForState(state, generatedAt, base.traces),
    projection: {
      ...base.projection,
      total_count: populated ? 216 : 0,
      projected_count: populated ? 216 : 0,
    },
  };
}

type TraceGraph = {
  readonly nodes: readonly unknown[];
  readonly edges: readonly unknown[];
};

function traceGraphForState(
  state: PublishedState,
  generatedAt: string,
  base: TraceGraph,
): TraceGraph {
  if (!["error", "blocked", "unavailable", "corrupt"].includes(state)) return base;
  const sources = Object.entries(snapshotV2.workspaces).map(([workspace, source]) => ({
    node_id: source.trace_id,
    kind: "source_receipt",
    label: `${workspace} source`,
    observed_at: generatedAt,
    safe_ref: "a".repeat(64),
    state: "accepted",
    source_namespace: `dashboard.fixture.${workspace}`,
  }));
  const terminals = Object.entries(snapshotV2.workspaces).map(([workspace, source]) => ({
    node_id: `${source.trace_id}-blocker`,
    kind: "blocker_terminal",
    label: `${workspace} blocker`,
    observed_at: generatedAt,
    safe_ref: null,
    state: "blocked",
    source_namespace: `dashboard.fixture.${workspace}`,
  }));
  const edges = Object.values(snapshotV2.workspaces).map((source) => ({
    from_node_id: source.trace_id,
    to_node_id: `${source.trace_id}-blocker`,
    kind: "blocked_by",
  }));
  return { nodes: [...sources, ...terminals], edges };
}

type SourceStateInput = {
  readonly trace_id: string;
  readonly items: readonly unknown[];
  readonly truncated: boolean;
};

function sourceState(base: SourceStateInput, state: PublishedState, generatedAt: string) {
  const items = state === "populated" ? base.items : [];
  return {
    ...base,
    state,
    observed_at: state === "unavailable" ? null : generatedAt,
    freshness: {
      policy_id: "shell-state-qa",
      age_seconds: state === "unavailable" ? null : state === "stale" ? 86_400 : 0,
      as_of: generatedAt,
    },
    blocker_code: blockerCode(state),
    summary: summaryForState(state),
    total_count: items.length,
    projected_count: items.length,
    truncated: false,
    items,
  };
}

function blockerCode(state: PublishedState): string | null {
  switch (state) {
    case "error":
    case "blocked":
    case "unavailable":
    case "corrupt":
      return `${state}_qa_authority`;
    case "empty":
    case "stale":
    case "populated":
      return null;
  }
}

function summaryForState(state: PublishedState): string {
  switch (state) {
    case "empty":
      return "권위 있는 읽기 성공, 0 records";
    case "error":
      return "typed reader가 완료되지 않았습니다";
    case "blocked":
      return "안전 gate가 사용을 차단했습니다";
    case "unavailable":
      return "현재 권위 receipt가 없습니다";
    case "corrupt":
      return "schema 또는 hash 무결성 검증 실패";
    case "stale":
      return "마지막 관측이 신선도 기준을 초과했습니다";
    case "populated":
      return "권위 데이터가 검증되어 표시됩니다";
  }
}
