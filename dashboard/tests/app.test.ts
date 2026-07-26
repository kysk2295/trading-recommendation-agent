import { describe, expect, test } from "bun:test";
import { createApp } from "../src/app";
import { MemorySnapshotStore } from "../src/store";

const INGEST_TOKEN = "ingest-token-with-adequate-length";

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
    const app = createApp(new MemorySnapshotStore(), INGEST_TOKEN);

    const response = await app.request("/api/health");

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ ok: true });
    expect(response.headers.get("x-content-type-options")).toBe("nosniff");
  });

  test("serves a read-only observatory with account PnL and no operator controls", async () => {
    // Given: the public read-only observatory application.
    const app = createApp(new MemorySnapshotStore(), INGEST_TOKEN);

    // When: a browser opens the dashboard shell.
    const response = await app.request("/");
    const html = await response.text();

    // Then: account telemetry is visible and remote-control surfaces are absent.
    expect(response.status).toBe(200);
    expect(html).toContain('id="account-equity"');
    expect(html).toContain('id="account-daily-pnl"');
    expect(html).not.toContain('id="operator-pair-form"');
    expect(html).not.toContain('id="interaction-form"');
  });

  test("protects ingestion while keeping snapshot reads public", async () => {
    const app = createApp(new MemorySnapshotStore(), INGEST_TOKEN);

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
    const app = createApp(store, INGEST_TOKEN);

    const ingested = await app.request("/api/ingest", {
      method: "POST",
      headers: {
        authorization: `Bearer ${INGEST_TOKEN}`,
        "content-type": "application/json",
      },
      body: JSON.stringify(snapshot),
    });
    const viewed = await app.request("/api/snapshot");

    expect(ingested.status).toBe(202);
    expect(viewed.status).toBe(200);
    expect(await viewed.json()).toEqual(snapshot);
  });

  test("rejects fields outside the public schema", async () => {
    const app = createApp(new MemorySnapshotStore(), INGEST_TOKEN);
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
    const app = createApp(new MemorySnapshotStore(), INGEST_TOKEN);

    const response = await app.request("/api/snapshot");

    expect(response.status).toBe(404);
  });

  test("does not expose operator, interaction, or command routes", async () => {
    // Given: the browser surface is intentionally read-only.
    const app = createApp(new MemorySnapshotStore(), INGEST_TOKEN);

    // When: a visitor probes every former command surface.
    const responses = await Promise.all([
      app.request("/api/operator/session"),
      app.request("/api/agents/us-intraday/interactions"),
      app.request("/api/operator/agents/us-intraday/view"),
      app.request("/api/worker/interactions/3f454e79-608d-4c83-88d6-10f3db79dc35/result"),
    ]);

    // Then: none of those routes exist.
    expect(responses.map((response) => response.status)).toEqual([404, 404, 404, 404]);
  });
});
