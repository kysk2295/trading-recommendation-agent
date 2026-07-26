import { describe, expect, test } from "bun:test";
import type { AutonomousTaskReceipt, DirectedJobEvent } from "../src/schema";
import {
  autonomousReceiptPresentation,
  type CausalEvidencePath,
  causalTracePresentation,
  familyRoster,
  originReceipts,
  promotionGate,
  receiptBlockers,
} from "../src/workspaces/research_strategies_evidence";

const sha = "a".repeat(64);

describe("Research and Strategies causal evidence", () => {
  test("Given a complete source-to-lifecycle chain, when it is projected, then its dataset SHA remains exact", () => {
    // Given: a canonical causal chain whose nodes are connected in source order.
    const trace = completeTrace();

    // When: the Research projection derives its evidence presentation.
    const presentation = causalTracePresentation("research", "populated", trace);

    // Then: every causal stage resolves and the authoritative SHA is unmodified.
    expect(presentation.state).toBe("populated");
    expect(presentation.missingStage).toBeNull();
    expect(presentation.datasetSha).toBe(sha);
  });

  test("Given each required causal stage is absent, when it is projected, then it blocks at that exact stage", () => {
    // Given: individually incomplete traces from the same source-to-lifecycle chain.
    const stages = [
      "source",
      "hypothesis",
      "dataset",
      "code",
      "trial",
      "reviewer",
      "lifecycle",
    ] as const;

    // When: each trace is evaluated for a strategy candidate.
    const presentations = stages.map((stage) =>
      causalTracePresentation("strategies", "populated", withoutStage(stage)),
    );

    // Then: the first missing authority blocks rather than allowing a partial candidate onward.
    expect(presentations.map((presentation) => presentation.state)).toEqual(
      stages.map(() => "blocked"),
    );
    expect(presentations.map((presentation) => presentation.missingStage)).toEqual([...stages]);
  });

  test("Given a reviewer decision without lifecycle authority, when promotion is evaluated, then it stays blocked", () => {
    // Given: a completed trial with no lifecycle decision.
    const trace = withoutStage("lifecycle");

    // When: the strategy promotion gate is derived.
    const gate = promotionGate(trace);

    // Then: terminal review/lifecycle cannot be inferred from a candidate trace.
    expect(gate).toEqual({ state: "blocked", reason: "lifecycle" });
  });

  test("Given reviewer and lifecycle terminals, when promotion is evaluated, then it is resolved but read-only", () => {
    const gate = promotionGate(completeTrace());

    expect(gate).toEqual({ state: "resolved", reason: null });
  });

  test("Given canonical agents, when the six-family roster is derived, then allocation manager stays absent and locked", () => {
    // Given: the snapshot publishes only one known family.
    const roster = familyRoster([
      {
        agent_id: "systematic_quant",
        label: "Systematic Quant",
        role: "Research family",
        capabilities: ["conversation", "directed_tool", "autonomous_research"],
        runtime_state: "armed",
        trace_id: "trace-agent",
      },
    ]);

    // When: the complete roster and allocation gate are projected.
    // Then: there are exactly six families, no control-plane role is promoted to a family, and lock remains.
    expect(roster.map((entry) => entry.familyId)).toEqual([
      "opportunity_manager",
      "day_trading",
      "swing_trading",
      "systematic_quant",
      "derivatives_research",
      "market_context",
    ]);
    expect(new Set(roster.map((entry) => entry.familyId))).toHaveLength(6);
    expect(roster.find((entry) => entry.familyId === "systematic_quant")?.published).toBe(true);
    expect(roster.filter((entry) => !entry.published)).toHaveLength(5);
  });

  test("Given public family capabilities without private task receipts, when origins are shown, then conversation, directed, and autonomous remain distinct", () => {
    // Given: no private event receipt is embedded in the public v2 snapshot.
    const origins = originReceipts({ interactions: [], directedJobs: [], autonomousTasks: [] });

    // When: origin disclosure is derived.
    // Then: every channel remains separately unavailable rather than being merged into an activity claim.
    expect(origins.map((origin) => origin.origin)).toEqual([
      "conversation",
      "directed_job",
      "autonomous_research",
    ]);
    expect(origins.every((origin) => origin.state === "unavailable")).toBe(true);
  });

  test("Given typed directed and autonomous receipts, when origins are shown, then their channels remain separately populated and lifecycle stays blocked", () => {
    const task: AutonomousTaskReceipt = {
      schema_version: 1,
      public_task_id: "d".repeat(32),
      event_id: "e".repeat(64),
      agent_family_id: "systematic_quant",
      channel: "autonomous_research",
      trigger_type: "experiment_result",
      policy_version: "autonomous-policy-v1",
      code_version: "f".repeat(40),
      sequence: 2,
      kind: "result",
      state: "completed",
      occurred_at: "2026-07-26T08:00:00Z",
      reason: null,
      evidence_refs: [sha],
      result_sha256: sha,
      summary: "redacted receipt",
      consumed_tokens: 12,
      consumed_cost_microusd: 34,
      redaction_status: "passed",
      reviewer_state: "pending",
      lifecycle_state: "unchanged",
    };
    const directed: DirectedJobEvent = {
      type: "directed_job_event",
      interaction_id: "019c0014-f0f5-7000-8000-000000000100",
      agent_family_id: "systematic_quant",
      job_kind: "experiment",
      kind: "result",
      state: "completed",
      sequence: 1,
      step: "review",
      evidence_sha256: sha,
      result_sha256: sha,
      summary: "redacted receipt",
    };

    const origins = originReceipts({
      interactions: [],
      directedJobs: [directed],
      autonomousTasks: [task],
    });

    expect(origins.map((origin) => [origin.origin, origin.state, origin.count])).toEqual([
      ["conversation", "unavailable", 0],
      ["directed_job", "populated", 1],
      ["autonomous_research", "populated", 1],
    ]);
    expect(autonomousReceiptPresentation(task)).toEqual({ state: "blocked", reason: "reviewer" });
    expect(receiptBlockers(task, [task])).toEqual(["cleanup", "reviewer", "lifecycle"]);
  });
});

function completeTrace(): CausalEvidencePath {
  return {
    status: "resolved",
    startsAtSource: true,
    nodes: [
      node("source", "source_receipt"),
      node("hypothesis", "hypothesis"),
      node("dataset", "dataset", sha),
      node("code", "code_revision"),
      node("trial", "trial"),
      node("reviewer", "reviewer_decision"),
      node("lifecycle", "lifecycle_decision"),
    ],
    edges: [
      edge("source", "hypothesis"),
      edge("hypothesis", "dataset"),
      edge("hypothesis", "code"),
      edge("dataset", "trial"),
      edge("code", "trial"),
      edge("trial", "reviewer"),
      edge("reviewer", "lifecycle"),
    ],
  };
}

function withoutStage(
  stage: "source" | "hypothesis" | "dataset" | "code" | "trial" | "reviewer" | "lifecycle",
): CausalEvidencePath {
  const trace = completeTrace();
  const kindByStage = {
    source: "source_receipt",
    hypothesis: "hypothesis",
    dataset: "dataset",
    code: "code_revision",
    trial: "trial",
    reviewer: "reviewer_decision",
    lifecycle: "lifecycle_decision",
  } as const;
  const nodeId = trace.nodes.find((node) => node.kind === kindByStage[stage])?.node_id;
  return {
    ...trace,
    startsAtSource: stage === "source" ? false : trace.startsAtSource,
    nodes: trace.nodes.filter((node) => node.node_id !== nodeId),
    edges: trace.edges.filter((edge) => edge.from_node_id !== nodeId && edge.to_node_id !== nodeId),
  };
}

function node(
  nodeId: string,
  kind: CausalEvidencePath["nodes"][number]["kind"],
  safeRef: string | null = null,
) {
  return { node_id: nodeId, kind, safe_ref: safeRef, state: "accepted" as const };
}

function edge(fromNodeId: string, toNodeId: string) {
  return { from_node_id: fromNodeId, to_node_id: toNodeId };
}
