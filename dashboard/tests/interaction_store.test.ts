import { describe, expect, test } from "bun:test";
import { MemorySnapshotStore } from "../src/store";

describe("agent interaction store", () => {
  test("lists newest interactions per agent and preserves queued work for publisher reconnect", async () => {
    // Given: immutable receipts for two agents.
    const store = new MemorySnapshotStore();
    const older = {
      id: "019c0014-f0f5-7000-8000-000000000001",
      agent_id: "research",
      command: "첫 번째 명령",
      state: "completed",
      response: "완료",
      created_at: "2026-07-26T04:00:00Z",
      updated_at: "2026-07-26T04:01:00Z",
    } as const;
    const queued = {
      id: "019c0014-f0f5-7000-8000-000000000002",
      agent_id: "us-intraday",
      command: "두 번째 명령",
      state: "queued",
      response: null,
      created_at: "2026-07-26T04:02:00Z",
      updated_at: "2026-07-26T04:02:00Z",
    } as const;
    await store.createInteraction(older);
    await store.createInteraction(queued);
    await store.updateInteraction(queued.id, "running", null);

    // When: operator history and publisher backlog are read on connection.
    const history = await store.listInteractions();
    const pending = await store.pendingInteractions();

    // Then: history is newest-first and only executable work is redelivered.
    expect(history.map((interaction) => interaction.id)).toEqual([queued.id, older.id]);
    expect(pending).toMatchObject([{ id: queued.id, state: "running" }]);
  });
});
