import type { z } from "zod";

type TraceNodeInput = {
  readonly node_id: string;
  readonly kind: string;
  readonly observed_at: string;
};

type TraceEdgeInput = {
  readonly from_node_id: string;
  readonly to_node_id: string;
};

type WorkspaceInput = {
  readonly state: string;
  readonly observed_at: string | null;
  readonly freshness: { readonly as_of: string };
  readonly trace_id: string;
  readonly items: readonly {
    readonly state: string;
    readonly observed_at: string | null;
    readonly trace_id: string;
  }[];
};

type SnapshotGraphInput = {
  readonly generated_at: string;
  readonly workspaces: {
    readonly command_center: WorkspaceInput & {
      readonly agents: readonly { readonly trace_id: string }[];
    };
    readonly overview: WorkspaceInput;
    readonly markets: WorkspaceInput;
    readonly data_sources: WorkspaceInput & {
      readonly capabilities: readonly {
        readonly state: string;
        readonly observed_at: string | null;
        readonly trace_id: string;
      }[];
    };
    readonly research: WorkspaceInput;
    readonly strategies: WorkspaceInput;
    readonly derivatives: WorkspaceInput;
    readonly paper: WorkspaceInput;
    readonly system: WorkspaceInput;
  };
  readonly traces: {
    readonly nodes: readonly TraceNodeInput[];
    readonly edges: readonly TraceEdgeInput[];
  };
};

type WorkspaceName = keyof SnapshotGraphInput["workspaces"];

const DECISION_TERMINALS = new Set([
  "reviewer_decision",
  "lifecycle_decision",
  "paper_receipt",
  "process_receipt",
  "deployment_receipt",
  "blocker_terminal",
]);
const TERMINALS_BY_WORKSPACE: Readonly<Record<WorkspaceName, ReadonlySet<string>>> = {
  command_center: new Set(["process_receipt", "blocker_terminal"]),
  overview: new Set([
    "source_receipt",
    "reviewer_decision",
    "lifecycle_decision",
    "paper_receipt",
    "process_receipt",
    "blocker_terminal",
  ]),
  markets: new Set(["source_receipt", "reviewer_decision", "blocker_terminal"]),
  data_sources: new Set(["source_receipt", "reviewer_decision", "blocker_terminal"]),
  research: new Set(["reviewer_decision", "blocker_terminal"]),
  strategies: new Set(["reviewer_decision", "lifecycle_decision", "blocker_terminal"]),
  derivatives: new Set(["source_receipt", "reviewer_decision", "blocker_terminal"]),
  paper: new Set(["paper_receipt", "blocker_terminal"]),
  system: new Set([
    "reviewer_decision",
    "process_receipt",
    "deployment_receipt",
    "blocker_terminal",
  ]),
};

export function validateSnapshotGraph(
  snapshot: SnapshotGraphInput,
  context: z.RefinementCtx,
): void {
  const nodes = new Map(snapshot.traces.nodes.map((node) => [node.node_id, node]));
  if (nodes.size !== snapshot.traces.nodes.length) issue(context, "duplicate_trace_node");
  const generatedAt = Date.parse(snapshot.generated_at);
  if (generatedAt > Date.now() + 300_000) issue(context, "generated_at_too_far_future");
  validateObservationTimes(
    snapshot,
    Math.min(generatedAt + 300_000, Date.now() + 300_000),
    context,
  );
  const adjacency = buildDirectedAdjacency(nodes, snapshot.traces.edges, context);
  for (const group of referenceGroups(snapshot)) {
    for (const reference of new Set(group.references)) {
      if (!nodes.has(reference)) {
        issue(context, "dangling_trace_reference");
        continue;
      }
      const kinds = new Set(
        [...reachableNodes(reference, adjacency)].map((nodeId) => nodes.get(nodeId)?.kind),
      );
      if (!kinds.has("source_receipt")) issue(context, "trace_source_missing");
      if (![...group.terminals].some((kind) => kinds.has(kind))) {
        issue(
          context,
          [...DECISION_TERMINALS].some((kind) => kinds.has(kind))
            ? "trace_terminal_wrong_domain"
            : "trace_terminal_missing",
        );
      }
    }
  }
  if (hasDirectedCycle(nodes, snapshot.traces.edges)) issue(context, "cyclic_trace_graph");
  if (new TextEncoder().encode(JSON.stringify(snapshot)).byteLength > 256 * 1024) {
    issue(context, "snapshot_too_large");
  }
}

function referenceGroups(snapshot: SnapshotGraphInput) {
  const workspaces = snapshot.workspaces;
  return (Object.keys(TERMINALS_BY_WORKSPACE) as WorkspaceName[]).flatMap((name) => {
    const workspace = workspaces[name];
    const groups = [
      {
        terminals: terminalsForState(workspace.state, TERMINALS_BY_WORKSPACE[name]),
        references: [workspace.trace_id],
      },
      ...workspace.items.map((item) => ({
        terminals: terminalsForState(item.state, TERMINALS_BY_WORKSPACE[name]),
        references: [item.trace_id],
      })),
    ];
    const nested =
      name === "command_center"
        ? [
            {
              terminals: TERMINALS_BY_WORKSPACE.command_center,
              references: workspaces.command_center.agents.map((agent) => agent.trace_id),
            },
          ]
        : name === "data_sources"
          ? workspaces.data_sources.capabilities.map((capability) => ({
              terminals: terminalsForState(capability.state, TERMINALS_BY_WORKSPACE.data_sources),
              references: [capability.trace_id],
            }))
          : [];
    return [...groups, ...nested];
  });
}

function terminalsForState(
  state: string,
  domainTerminals: ReadonlySet<string>,
): ReadonlySet<string> {
  return ["error", "blocked", "unavailable", "corrupt"].includes(state)
    ? new Set(["blocker_terminal"])
    : domainTerminals;
}

function validateObservationTimes(
  snapshot: SnapshotGraphInput,
  ceiling: number,
  context: z.RefinementCtx,
): void {
  const workspaces = Object.values(snapshot.workspaces);
  const timestamps = [
    ...snapshot.traces.nodes.map((node) => node.observed_at),
    ...workspaces.flatMap((workspace) => [
      workspace.observed_at,
      workspace.freshness.as_of,
      ...workspace.items.map((item) => item.observed_at),
    ]),
    ...snapshot.workspaces.data_sources.capabilities.map((capability) => capability.observed_at),
  ];
  if (timestamps.some((timestamp) => timestamp !== null && Date.parse(timestamp) > ceiling)) {
    issue(context, "observation_too_far_future");
  }
}

function buildDirectedAdjacency(
  nodes: ReadonlyMap<string, TraceNodeInput>,
  edges: readonly TraceEdgeInput[],
  context: z.RefinementCtx,
): ReadonlyMap<string, ReadonlySet<string>> {
  const adjacency = new Map([...nodes.keys()].map((nodeId) => [nodeId, new Set<string>()]));
  for (const edge of edges) {
    const from = adjacency.get(edge.from_node_id);
    if (from === undefined || !nodes.has(edge.to_node_id)) {
      issue(context, "dangling_trace_edge");
      continue;
    }
    if (from.has(edge.to_node_id)) {
      issue(context, "duplicate_trace_edge");
      continue;
    }
    from.add(edge.to_node_id);
  }
  return adjacency;
}

function reachableNodes(
  start: string,
  adjacency: ReadonlyMap<string, ReadonlySet<string>>,
): ReadonlySet<string> {
  const reached = new Set([start]);
  const queue = [start];
  for (const nodeId of queue) {
    for (const next of adjacency.get(nodeId) ?? []) {
      if (!reached.has(next)) {
        reached.add(next);
        queue.push(next);
      }
    }
  }
  return reached;
}

function hasDirectedCycle(
  nodes: ReadonlyMap<string, TraceNodeInput>,
  edges: readonly TraceEdgeInput[],
): boolean {
  const indegree = new Map([...nodes.keys()].map((nodeId) => [nodeId, 0]));
  for (const edge of edges) {
    if (nodes.has(edge.from_node_id) && nodes.has(edge.to_node_id)) {
      indegree.set(edge.to_node_id, (indegree.get(edge.to_node_id) ?? 0) + 1);
    }
  }
  const queue = [...indegree].filter((entry) => entry[1] === 0).map((entry) => entry[0]);
  let visited = 0;
  for (const nodeId of queue) {
    visited += 1;
    for (const edge of edges) {
      if (edge.from_node_id !== nodeId) continue;
      const next = (indegree.get(edge.to_node_id) ?? 1) - 1;
      indegree.set(edge.to_node_id, next);
      if (next === 0) queue.push(edge.to_node_id);
    }
  }
  return visited !== nodes.size;
}

function issue(context: z.RefinementCtx, message: string): void {
  context.addIssue({ code: "custom", message });
}
