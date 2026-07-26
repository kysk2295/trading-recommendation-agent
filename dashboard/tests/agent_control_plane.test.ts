import { describe, expect, test } from "bun:test";
import { DashboardRealtimeHub, type RealtimePeer } from "../src/realtime";
import { autonomousTaskEventSchema, researchFamilyIdSchema } from "../src/schema";
import { MemorySnapshotStore } from "../src/store";

const task = {
  schema_version: 1,
  public_task_id: "a".repeat(32),
  event_id: "b".repeat(64),
  agent_family_id: "systematic_quant",
  channel: "autonomous_research",
  trigger_type: "new_data",
  policy_version: "autonomous-policy-v1",
  code_version: "c".repeat(40),
  sequence: 1,
  kind: "progress",
  state: "running",
  occurred_at: "2026-07-26T08:00:00Z",
  reason: null,
  evidence_refs: [],
  result_sha256: null,
  summary: null,
  consumed_tokens: 0,
  consumed_cost_microusd: 0,
  redaction_status: "passed",
  reviewer_state: "pending",
  lifecycle_state: "unchanged",
} as const;

class FakePeer implements RealtimePeer {
  readonly messages: string[] = [];

  send(message: string): void {
    this.messages.push(message);
  }

  close(): void {}
}

describe("autonomous research control plane", () => {
  test("registers exactly six primary families", () => {
    const accepted = [
      "opportunity_manager",
      "day_trading",
      "swing_trading",
      "systematic_quant",
      "derivatives_research",
      "market_context",
    ];

    expect(accepted.every((family) => researchFamilyIdSchema.safeParse(family).success)).toBe(true);
    expect(researchFamilyIdSchema.safeParse("delivery").success).toBe(false);
    expect(researchFamilyIdSchema.safeParse("allocation_manager").success).toBe(false);
  });

  test("persists and streams a redacted autonomous event exactly once", async () => {
    const store = new MemorySnapshotStore();
    const hub = new DashboardRealtimeHub(store);
    const viewer = new FakePeer();
    const publisher = new FakePeer();
    await hub.connectViewer(viewer);
    hub.connectPublisher(publisher);
    const event = autonomousTaskEventSchema.parse({ type: "agent_task_event", task });

    await hub.handlePublisherMessage(publisher, JSON.stringify(event));
    await hub.handlePublisherMessage(publisher, JSON.stringify(event));

    expect(await store.listAgentTaskEvents()).toEqual([event.task]);
    expect(viewer.messages.map((message) => JSON.parse(message))).toEqual([event]);
  });

  test("rejects recursive local path and session canaries", () => {
    expect(
      autonomousTaskEventSchema.safeParse({
        type: "agent_task_event",
        task: { ...task, local_path: "/Users/private/worktree" },
      }).success,
    ).toBe(false);
    expect(
      autonomousTaskEventSchema.safeParse({
        type: "agent_task_event",
        task: { ...task, interactive_session_id: "session-canary" },
      }).success,
    ).toBe(false);
  });
});
