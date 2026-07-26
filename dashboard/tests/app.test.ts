import { describe, expect, test } from "bun:test";
import { createApp } from "../src/app";
import { PairingTickets } from "../src/operator_auth";
import { dashboardSnapshotV1Schema } from "../src/schema";
import { MemorySnapshotStore } from "../src/store";
import { snapshotV2 } from "./snapshot_v2_fixture";

const INGEST_TOKEN = "ingest-token-with-adequate-length";
const OPERATOR_TOKEN = "operator-token-with-adequate-length";

const snapshot = {
  schema_version: 1,
  generated_at: "2026-07-25T03:00:00Z",
  source: "local-runtime",
  markets: [
    {
      market_id: "kr",
      label: "한국",
      local_time: "2026-07-25T12:00:00+09:00",
      state: "open",
    },
    {
      market_id: "us",
      label: "미국",
      local_time: "2026-07-24T23:00:00-04:00",
      state: "after",
    },
  ],
  forward: {
    session_date: "2026-07-24",
    eligible: false,
    ranking_cycles: 345,
    watch_cycles: 345,
    failed_watch_cycles: 70,
    read_retries: 1684,
    read_retry_failures: 0,
    candidate_input_cycles: 345,
    candidate_inputs: 3011,
    recommendations: 3,
    blockers: ["watch_cycle_failures:70"],
    incidents: ["watch_cycle_failures:70", "kis_read_retries:1684"],
  },
  agents: [
    {
      agent_id: "us-intraday",
      label: "미국 장중",
      state: "armed",
      scheduled_label: "ai.trading-agent.us-forward-open-handoff-20260727",
    },
  ],
  recommendations: [
    {
      symbol: "LITZ",
      strategy: "opening_range_breakout",
      created_at: "2026-07-24T10:04:06-04:00",
      entry: 15.04752,
      stop: 14.45,
      target_1r: 15.64504,
      target_2r: 16.24256,
      state: "target_2r",
      rationale: "완료 분봉 기준 돌파",
    },
  ],
  signals: [],
  research: {
    status: "blocked",
    session_date: "2026-07-24",
    summary: "실제 causal 연구 기반 게이트 차단",
  },
  account: {
    status: "incomplete",
    session_date: "2026-07-24",
    observed_at: "2026-07-24T20:01:15Z",
    currency: "USD",
    equity: "100000",
    daily_pnl: "0",
    realized_pnl: "0",
    unrealized_pnl: "0",
    planned_open_risk: "0",
    open_positions: 0,
    open_orders: 0,
  },
} as const;

describe("dashboard API", () => {
  test("reports public health without disclosing configuration", async () => {
    const app = createApp(new MemorySnapshotStore(), INGEST_TOKEN, OPERATOR_TOKEN);

    const response = await app.request("/api/health");

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ ok: true });
    expect(response.headers.get("x-content-type-options")).toBe("nosniff");
  });

  test("serves the nine-workspace shell without an access-key field", async () => {
    // Given: the public observatory application with a protected command boundary.
    const app = createApp(new MemorySnapshotStore(), INGEST_TOKEN, OPERATOR_TOKEN);

    // When: a browser opens the dashboard shell.
    const response = await app.request("/");
    const html = await response.text();

    // Then: nine routes and the trace drawer are present without a token input.
    expect(response.status).toBe(200);
    expect(html).toContain('id="workspace-nav"');
    expect(html).toContain('href="#command-center"');
    expect(html).toContain('href="#system"');
    expect(html).toContain('id="evidence-trace-dialog"');
    expect(html).not.toContain('type="password"');
  });

  test("protects ingestion while keeping snapshot reads public", async () => {
    const app = createApp(new MemorySnapshotStore(), INGEST_TOKEN, OPERATOR_TOKEN);

    const ingestDenied = await app.request("/api/ingest", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(snapshot),
    });
    const viewed = await app.request("/api/snapshot");

    expect(ingestDenied.status).toBe(401);
    expect(viewed.status).toBe(404);
  });

  test("stores a strict redacted snapshot and returns it to the viewer", async () => {
    const store = new MemorySnapshotStore();
    const app = createApp(store, INGEST_TOKEN, OPERATOR_TOKEN);

    const ingested = await app.request("/api/ingest", {
      method: "POST",
      headers: {
        authorization: `Bearer ${INGEST_TOKEN}`,
        "content-type": "application/json",
      },
      body: JSON.stringify(snapshotV2),
    });
    const viewed = await app.request("/api/snapshot");

    expect(ingested.status).toBe(202);
    expect(viewed.status).toBe(200);
    expect(await viewed.json()).toEqual(snapshotV2);
    expect(dashboardSnapshotV1Schema.safeParse(await store.latestV1()).success).toBe(true);
  });

  test("rejects v1 without creating canonical or rollback state", async () => {
    // Given: an empty strict-v2 ingest store.
    const store = new MemorySnapshotStore();
    const app = createApp(store, INGEST_TOKEN, OPERATOR_TOKEN);

    // When: a valid legacy v1 payload is submitted.
    const rejected = await app.request("/api/ingest", {
      method: "POST",
      headers: {
        authorization: `Bearer ${INGEST_TOKEN}`,
        "content-type": "application/json",
      },
      body: JSON.stringify(snapshot),
    });

    // Then: the request fails without creating either singleton.
    expect(rejected.status).toBe(400);
    expect(await store.latest()).toBeNull();
    expect(await store.latestV1()).toBeNull();
  });

  test("rejects fields outside the public schema", async () => {
    const app = createApp(new MemorySnapshotStore(), INGEST_TOKEN, OPERATOR_TOKEN);
    const unsafe = { ...snapshot, account_id: "must-not-cross-boundary" };

    const response = await app.request("/api/ingest", {
      method: "POST",
      headers: {
        authorization: `Bearer ${INGEST_TOKEN}`,
        "content-type": "application/json",
      },
      body: JSON.stringify(unsafe),
    });

    expect(response.status).toBe(400);
  });

  test("returns not found until the first publisher snapshot arrives", async () => {
    const app = createApp(new MemorySnapshotStore(), INGEST_TOKEN, OPERATOR_TOKEN);

    const response = await app.request("/api/snapshot");

    expect(response.status).toBe(404);
  });

  test("keeps commands private while public telemetry stays keyless", async () => {
    // Given: an unpaired public viewer.
    const app = createApp(new MemorySnapshotStore(), INGEST_TOKEN, OPERATOR_TOKEN);

    // When: the viewer reads telemetry and attempts to submit a command.
    const telemetry = await app.request("/api/snapshot");
    const command = await app.request("/api/agents/day_trading/interactions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ mode: "analysis", command: "현재 세션 차단 원인을 분석해줘" }),
    });

    // Then: telemetry remains public while command submission is unauthorized.
    expect(telemetry.status).toBe(404);
    expect(command.status).toBe(401);
  });

  test("pairs a trusted device and creates an immutable agent interaction receipt", async () => {
    // Given: a device that proves the operator secret once.
    const app = createApp(new MemorySnapshotStore(), INGEST_TOKEN, OPERATOR_TOKEN);
    const paired = await app.request("/api/operator/session", {
      method: "POST",
      headers: { authorization: `Bearer ${OPERATOR_TOKEN}` },
    });
    const cookie = paired.headers.get("set-cookie");

    // When: the paired device sends a goal to one dashboard agent.
    const response = await app.request("/api/agents/day_trading/interactions", {
      method: "POST",
      headers: {
        cookie: cookie ?? "",
        "content-type": "application/json",
      },
      body: JSON.stringify({ mode: "analysis", command: "현재 세션 차단 원인을 분석해줘" }),
    });
    const payload = await response.json();

    // Then: the command is queued under the exact selected agent.
    expect(paired.status).toBe(204);
    expect(response.status).toBe(202);
    expect(payload).toMatchObject({
      interaction: {
        agent_id: "day_trading",
        mode: "analysis",
        command: "현재 세션 차단 원인을 분석해줘",
        state: "queued",
      },
    });
  });

  test("pairs a trusted device through a single-use publisher ticket", async () => {
    const tickets = new PairingTickets();
    const app = createApp(new MemorySnapshotStore(), INGEST_TOKEN, OPERATOR_TOKEN, tickets);
    const ticket = tickets.issue();

    const paired = await app.request(`/operator/pair/${ticket}`);
    const replayed = await app.request(`/operator/pair/${ticket}`);

    expect(paired.status).toBe(302);
    expect(paired.headers.get("location")).toBe("/#command-center");
    expect(paired.headers.get("set-cookie")).toContain("HttpOnly");
    expect(replayed.status).toBe(404);
  });

  test("rejects private canaries before interaction storage", async () => {
    // Given: a paired operator and a command containing a prohibited local identifier
    const store = new MemorySnapshotStore();
    const app = createApp(store, INGEST_TOKEN, OPERATOR_TOKEN);
    const paired = await app.request("/api/operator/session", {
      method: "POST",
      headers: { authorization: `Bearer ${OPERATOR_TOKEN}` },
    });

    // When: the canary crosses the command boundary
    const response = await app.request("/api/agents/market_context/interactions", {
      method: "POST",
      headers: {
        cookie: paired.headers.get("set-cookie") ?? "",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        mode: "conversation",
        command: "inspect /Users/private/worktree/session.json",
      }),
    });

    // Then: no message, store row, relay event, or DOM-bound value is created
    expect(response.status).toBe(400);
    expect(await store.listInteractions()).toEqual([]);
  });
});
