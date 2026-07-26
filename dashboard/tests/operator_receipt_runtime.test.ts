import { describe, expect, test } from "bun:test";
import {
  AgentWorkspace,
  type OperatorCallbacks,
  type OperatorFactory,
} from "../src/agent_workspace";
import type { Interaction } from "../src/schema";

const sha = "a".repeat(64);

describe("shared operator receipt runtime", () => {
  test("Research-first startup creates one operator runtime and publishes typed receipts", async () => {
    let callbacks: OperatorCallbacks | undefined;
    let starts = 0;
    const factory: OperatorFactory = (next) => {
      callbacks = next;
      return {
        start: async () => {
          starts += 1;
        },
        submit: async () => interaction(),
      };
    };
    const workspace = new AgentWorkspace(factory);
    const snapshots: number[] = [];
    workspace.subscribe((snapshot) => snapshots.push(snapshot.directedJobs.length));

    workspace.ensureStarted();
    workspace.ensureStarted();
    await Promise.resolve();
    if (callbacks === undefined) throw new Error("operator callbacks missing");
    callbacks.onDirectedJob({
      type: "directed_job_event",
      interaction_id: interaction().id,
      agent_family_id: "systematic_quant",
      job_kind: "experiment",
      kind: "result",
      state: "completed",
      sequence: 1,
      step: "review",
      evidence_sha256: sha,
      result_sha256: sha,
      summary: "redacted result",
    });
    callbacks.onAutonomousJob({
      schema_version: 1,
      public_task_id: "b".repeat(32),
      event_id: "c".repeat(64),
      agent_family_id: "systematic_quant",
      channel: "autonomous_research",
      trigger_type: "experiment_result",
      policy_version: "autonomous-policy-v1",
      code_version: "d".repeat(40),
      sequence: 2,
      kind: "result",
      state: "completed",
      occurred_at: "2026-07-26T08:00:00Z",
      reason: null,
      evidence_refs: [sha],
      result_sha256: sha,
      summary: "redacted autonomous result",
      consumed_tokens: 12,
      consumed_cost_microusd: 34,
      redaction_status: "passed",
      reviewer_state: "pending",
      lifecycle_state: "unchanged",
    });

    const snapshot = workspace.receiptSnapshot();
    expect(starts).toBe(1);
    expect(snapshots).toEqual([1, 1]);
    expect(snapshot.directedJobs).toHaveLength(1);
    expect(snapshot.autonomousTasks).toHaveLength(1);
    expect(Object.keys(snapshot).sort()).toEqual([
      "autonomousTasks",
      "directedJobs",
      "interactions",
    ]);
  });
});

function interaction(): Interaction {
  return {
    id: "019c0014-f0f5-7000-8000-000000000100",
    agent_id: "systematic_quant",
    mode: "experiment",
    command: "run a redacted experiment",
    state: "completed",
    response: null,
    created_at: "2026-07-26T08:00:00Z",
    updated_at: "2026-07-26T08:00:00Z",
  };
}
