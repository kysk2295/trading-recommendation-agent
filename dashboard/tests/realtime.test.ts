import { describe, expect, test } from "bun:test";
import { DashboardRealtimeHub, type RealtimePeer } from "../src/realtime";
import { MemorySnapshotStore } from "../src/store";

const snapshot = {
  schema_version: 1,
  generated_at: "2026-07-26T03:00:00Z",
  source: "local-runtime",
  markets: [
    {
      market_id: "kr",
      label: "한국",
      local_time: "2026-07-26T12:00:00+09:00",
      state: "closed",
    },
    {
      market_id: "us",
      label: "미국",
      local_time: "2026-07-25T23:00:00-04:00",
      state: "closed",
    },
  ],
  forward: {
    session_date: null,
    eligible: false,
    ranking_cycles: 0,
    watch_cycles: 0,
    failed_watch_cycles: 0,
    read_retries: 0,
    read_retry_failures: 0,
    candidate_input_cycles: 0,
    candidate_inputs: 0,
    recommendations: 0,
    blockers: ["session_unavailable"],
    incidents: [],
  },
  agents: [],
  recommendations: [],
  signals: [],
  research: {
    status: "unavailable",
    session_date: null,
    summary: "실제 연구 산출물 없음",
  },
  account: {
    status: "unavailable",
    session_date: null,
    observed_at: null,
    currency: "USD",
    equity: null,
    daily_pnl: null,
    realized_pnl: null,
    unrealized_pnl: null,
    planned_open_risk: null,
    open_positions: 0,
    open_orders: 0,
  },
} as const;

class FakePeer implements RealtimePeer {
  readonly messages: string[] = [];
  closed: { readonly code: number; readonly reason: string } | null = null;

  send(message: string): void {
    this.messages.push(message);
  }

  close(code: number, reason: string): void {
    this.closed = { code, reason };
  }
}

describe("event-driven dashboard relay", () => {
  test("pushes snapshots to connected viewers without a browser poll", async () => {
    const hub = new DashboardRealtimeHub(new MemorySnapshotStore());
    const viewer = new FakePeer();
    const publisher = new FakePeer();

    await hub.connectViewer(viewer);
    hub.connectPublisher(publisher);
    await hub.handlePublisherMessage(publisher, JSON.stringify({ type: "snapshot", snapshot }));

    expect(viewer.messages.map((message) => JSON.parse(message))).toEqual([
      { type: "snapshot", snapshot },
    ]);
  });

  test("rejects invalid publisher messages without mutating the snapshot store", async () => {
    const store = new MemorySnapshotStore();
    const hub = new DashboardRealtimeHub(store);
    const publisher = new FakePeer();
    hub.connectPublisher(publisher);

    await hub.handlePublisherMessage(publisher, JSON.stringify({ type: "snapshot", secret: "no" }));

    expect(publisher.closed).toEqual({ code: 1003, reason: "invalid_message" });
    expect(await store.latest()).toBeNull();
  });
});
