import { describe, expect, test } from "bun:test";
import { latestInteraction } from "../src/agent_workspace";

const queued = {
  id: "019c0014-f0f5-7000-8000-000000000001",
  agent_id: "market_context",
  mode: "conversation",
  command: "명령",
  state: "queued",
  response: null,
  created_at: "2026-07-26T04:00:00.000Z",
  updated_at: "2026-07-26T04:00:00.000Z",
} as const;

const completed = {
  ...queued,
  state: "completed",
  response: "완료 응답",
  updated_at: "2026-07-26T04:00:00.005Z",
} as const;

describe("agent interaction ordering", () => {
  test("does not let a late HTTP receipt overwrite a faster websocket completion", () => {
    expect(latestInteraction(completed, queued)).toEqual(completed);
    expect(latestInteraction(queued, completed)).toEqual(completed);
  });
});
