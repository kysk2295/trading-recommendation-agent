import { describe, expect, test } from "bun:test";
import { parseStoredInteractionPayloads } from "../src/stored_interaction_compat";

describe("stored interaction compatibility", () => {
  test("keeps current receipts and rejects legacy agent identities without crashing", () => {
    // Given: one current receipt beside pre-v2 persisted rows.
    const current = {
      id: "019c0014-f0f5-7000-8000-000000000001",
      agent_id: "market_context",
      mode: "conversation",
      command: "현재 시장 맥락을 설명해줘",
      state: "completed",
      response: "완료",
      created_at: "2026-07-26T04:00:00Z",
      updated_at: "2026-07-26T04:01:00Z",
    } as const;
    const legacyAgent = { ...current, agent_id: "us-intraday" };
    const legacyMode = {
      ...current,
      id: "019c0014-f0f5-7000-8000-000000000002",
      mode: "old-analysis-mode",
    };

    // When: persisted rows cross the current schema boundary.
    const parsed = parseStoredInteractionPayloads([legacyAgent, current, legacyMode]);

    // Then: only the current strict receipt remains visible or executable.
    expect(parsed).toEqual([current]);
  });
});
