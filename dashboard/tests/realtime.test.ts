import { describe, expect, test } from "bun:test";
import { PairingTickets } from "../src/operator_auth";
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

    expect(viewer.messages.map((message) => JSON.parse(message))).toMatchObject([
      {
        type: "snapshot",
        snapshot: {
          schema_version: 2,
          projection: { source_schema_version: 1 },
        },
      },
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

  test("delivers agent commands to the publisher and responses only to operator viewers", async () => {
    // Given: a public viewer, an authenticated operator viewer, and the Mac mini publisher.
    const store = new MemorySnapshotStore();
    const hub = new DashboardRealtimeHub(store);
    const publicViewer = new FakePeer();
    const operatorViewer = new FakePeer();
    const publisher = new FakePeer();
    const interaction = {
      id: "019c0014-f0f5-7000-8000-000000000001",
      agent_id: "research",
      command: "현재 데이터 결손을 요약해줘",
      state: "queued",
      response: null,
      created_at: "2026-07-26T04:00:00Z",
      updated_at: "2026-07-26T04:00:00Z",
    } as const;
    await hub.connectViewer(publicViewer);
    await hub.connectOperator(operatorViewer);
    hub.connectPublisher(publisher);

    // When: the command is queued and its terminal response returns.
    await hub.queueInteraction(interaction);
    await hub.handlePublisherMessage(
      publisher,
      JSON.stringify({
        type: "interaction_result",
        interaction_id: interaction.id,
        state: "completed",
        response: "실제 causal 세션의 watch cycle 품질이 미달입니다.",
      }),
    );

    // Then: the publisher receives work and only the operator receives command content.
    expect(publisher.messages.map((message) => JSON.parse(message))).toContainEqual({
      type: "interaction",
      interaction,
    });
    expect(publicViewer.messages).toEqual([]);
    const operatorMessages = operatorViewer.messages.map((message) => JSON.parse(message));
    expect(operatorMessages.at(-1)).toMatchObject({
      type: "interaction",
      interaction: {
        id: interaction.id,
        agent_id: interaction.agent_id,
        command: interaction.command,
        created_at: interaction.created_at,
        state: "completed",
        response: "실제 causal 세션의 watch cycle 품질이 미달입니다.",
      },
    });
    expect(operatorMessages.at(-1).interaction.updated_at).not.toBe(interaction.updated_at);
  });

  test("returns a one-time browser pairing path only to the trusted publisher", async () => {
    const tickets = new PairingTickets();
    const hub = new DashboardRealtimeHub(new MemorySnapshotStore(), tickets);
    const publisher = new FakePeer();
    hub.connectPublisher(publisher);

    await hub.handlePublisherMessage(publisher, JSON.stringify({ type: "pairing_request" }));

    const message = JSON.parse(publisher.messages.at(-1) ?? "{}");
    expect(message.type).toBe("pairing_ticket");
    expect(message.path).toStartWith("/operator/pair/");
    expect(tickets.consume(message.path.replace("/operator/pair/", ""))).toBe(true);
  });

  test("fails in-flight work on publisher loss and never redelivers a paid model call", async () => {
    const store = new MemorySnapshotStore();
    const hub = new DashboardRealtimeHub(store);
    const operator = new FakePeer();
    const publisher = new FakePeer();
    const replacement = new FakePeer();
    const interaction = {
      id: "019c0014-f0f5-7000-8000-000000000002",
      agent_id: "research",
      command: "한 번만 실행해줘",
      state: "queued",
      response: null,
      created_at: "2026-07-26T04:10:00Z",
      updated_at: "2026-07-26T04:10:00Z",
    } as const;
    await hub.connectOperator(operator);
    hub.connectPublisher(publisher);
    await hub.queueInteraction(interaction);
    await hub.handlePublisherMessage(
      publisher,
      JSON.stringify({
        type: "interaction_result",
        interaction_id: interaction.id,
        state: "running",
        response: null,
      }),
    );

    await hub.disconnectPublisher(publisher);
    hub.connectPublisher(replacement);
    await Promise.resolve();

    expect(replacement.messages).toEqual([]);
    expect((await store.listInteractions()).at(0)).toMatchObject({
      id: interaction.id,
      state: "failed",
      response: "publisher 연결이 끊겨 자동 재시도하지 않았습니다.",
    });
    expect(operator.messages.map((message) => JSON.parse(message)).at(-1)).toMatchObject({
      type: "interaction",
      interaction: {
        id: interaction.id,
        state: "failed",
      },
    });
  });
});
