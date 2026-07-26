import type { AutonomousTaskReceipt, DirectedJobEvent, Interaction } from "../schema";
import type { DashboardSnapshotV2 } from "../schema_v2";

type SourceState = DashboardSnapshotV2["workspaces"]["research"]["state"];
type PublicAgent = DashboardSnapshotV2["workspaces"]["command_center"]["agents"][number];

export type CausalStage =
  | "source"
  | "hypothesis"
  | "dataset"
  | "code"
  | "trial"
  | "reviewer"
  | "lifecycle";

type CausalNode = {
  readonly node_id: string;
  readonly kind: string;
  readonly safe_ref: string | null;
  readonly state: string;
};

type CausalEdge = {
  readonly from_node_id: string;
  readonly to_node_id: string;
};

export type CausalEvidencePath = {
  readonly status: "resolved" | "unavailable" | "corrupt";
  readonly startsAtSource: boolean;
  readonly nodes: readonly CausalNode[];
  readonly edges: readonly CausalEdge[];
};

export type CausalTracePresentation = {
  readonly state: SourceState;
  readonly missingStage: CausalStage | null;
  readonly datasetSha: string | null;
};

export type FamilyRosterEntry = {
  readonly familyId: (typeof FAMILY_IDS)[number];
  readonly agent: PublicAgent | null;
  readonly published: boolean;
};

export type OriginReceipt = {
  readonly origin: "conversation" | "directed_job" | "autonomous_research";
  readonly state: "populated" | "unavailable";
  readonly count: number;
};

export type ReceiptOriginInputs = {
  readonly interactions: readonly Interaction[];
  readonly directedJobs: readonly DirectedJobEvent[];
  readonly autonomousTasks: readonly AutonomousTaskReceipt[];
};

export type AutonomousReceiptPresentation = {
  readonly state: "blocked";
  readonly reason: "reviewer" | "lifecycle";
};

export const FAMILY_IDS = [
  "opportunity_manager",
  "day_trading",
  "swing_trading",
  "systematic_quant",
  "derivatives_research",
  "market_context",
] as const;

export function causalTracePresentation(
  workspace: "research" | "strategies",
  sourceState: SourceState,
  trace: CausalEvidencePath,
): CausalTracePresentation {
  if (sourceState === "unavailable" || sourceState === "corrupt" || sourceState === "error") {
    return { state: sourceState, missingStage: null, datasetSha: null };
  }
  if (trace.status === "corrupt") {
    return { state: "corrupt", missingStage: "source", datasetSha: null };
  }
  if (trace.status === "unavailable") {
    return { state: "unavailable", missingStage: "source", datasetSha: null };
  }
  const source = trace.nodes.find(
    (node) => node.kind === "source_receipt" && node.state === "accepted",
  );
  if (!trace.startsAtSource || source === undefined) return missing("source");
  const hypothesis = descendant(trace, source.node_id, "hypothesis");
  if (hypothesis === undefined) return missing("hypothesis");
  const dataset = descendant(trace, hypothesis.node_id, "dataset");
  if (dataset === undefined || dataset.safe_ref === null) return missing("dataset");
  const code = descendant(trace, hypothesis.node_id, "code_revision");
  if (code === undefined) return missing("code");
  const trial = sharedDescendant(trace, dataset.node_id, code.node_id, "trial");
  if (trial === undefined) return missing("trial");
  const reviewer = descendant(trace, trial.node_id, "reviewer_decision");
  if (reviewer === undefined || reviewer.state !== "accepted") return missing("reviewer");
  if (workspace === "research") {
    return { state: sourceState, missingStage: null, datasetSha: dataset.safe_ref };
  }
  const lifecycle = descendant(trace, reviewer.node_id, "lifecycle_decision");
  if (lifecycle === undefined || lifecycle.state !== "accepted") return missing("lifecycle");
  return { state: sourceState, missingStage: null, datasetSha: dataset.safe_ref };
}

export function promotionGate(trace: CausalEvidencePath): {
  readonly state: "resolved" | "blocked";
  readonly reason: CausalStage | null;
} {
  const presentation = causalTracePresentation("strategies", "populated", trace);
  return presentation.missingStage === null
    ? { state: "resolved", reason: null }
    : { state: "blocked", reason: presentation.missingStage };
}

export function familyRoster(agents: readonly PublicAgent[]): readonly FamilyRosterEntry[] {
  return FAMILY_IDS.map((familyId) => {
    const agent = agents.find((candidate) => candidate.agent_id === familyId) ?? null;
    return { familyId, agent, published: agent !== null };
  });
}

export function originReceipts(inputs: ReceiptOriginInputs): readonly OriginReceipt[] {
  const conversationCount = inputs.interactions.filter(
    (interaction) => interaction.mode === "conversation",
  ).length;
  return [
    originReceipt("conversation", conversationCount),
    originReceipt("directed_job", inputs.directedJobs.length),
    originReceipt("autonomous_research", inputs.autonomousTasks.length),
  ];
}

export function autonomousReceiptPresentation(
  receipt: AutonomousTaskReceipt,
): AutonomousReceiptPresentation {
  return receipt.reviewer_state === "accepted"
    ? { state: "blocked", reason: "lifecycle" }
    : { state: "blocked", reason: "reviewer" };
}

export function receiptBlockers(
  task: AutonomousTaskReceipt,
  tasks: readonly AutonomousTaskReceipt[],
): readonly ("cleanup" | "reviewer" | "lifecycle")[] {
  const cleanup = tasks.some(
    (candidate) =>
      candidate.public_task_id === task.public_task_id &&
      candidate.kind === "cleanup" &&
      candidate.state === "completed",
  );
  return [
    cleanup ? null : "cleanup",
    task.reviewer_state === "accepted" ? null : "reviewer",
    task.lifecycle_state === "unchanged" ? "lifecycle" : null,
  ].filter((value): value is "cleanup" | "reviewer" | "lifecycle" => value !== null);
}

function missing(stage: CausalStage): CausalTracePresentation {
  return { state: "blocked", missingStage: stage, datasetSha: null };
}

function originReceipt(origin: OriginReceipt["origin"], count: number): OriginReceipt {
  return { origin, state: count > 0 ? "populated" : "unavailable", count };
}

function descendant(
  trace: CausalEvidencePath,
  fromNodeId: string,
  kind: string,
): CausalNode | undefined {
  return trace.nodes.find(
    (node) => node.kind === kind && hasPath(trace.edges, fromNodeId, node.node_id),
  );
}

function sharedDescendant(
  trace: CausalEvidencePath,
  firstNodeId: string,
  secondNodeId: string,
  kind: string,
): CausalNode | undefined {
  return trace.nodes.find(
    (node) =>
      node.kind === kind &&
      hasPath(trace.edges, firstNodeId, node.node_id) &&
      hasPath(trace.edges, secondNodeId, node.node_id),
  );
}

function hasPath(edges: readonly CausalEdge[], start: string, target: string): boolean {
  const visited = new Set([start]);
  const queue = [start];
  for (const nodeId of queue) {
    if (nodeId === target) return true;
    for (const edge of edges) {
      if (edge.from_node_id === nodeId && !visited.has(edge.to_node_id)) {
        visited.add(edge.to_node_id);
        queue.push(edge.to_node_id);
      }
    }
  }
  return false;
}
