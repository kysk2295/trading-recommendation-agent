import { describe, expect, test } from "bun:test";
import { cjkFixtureGeneratedAt } from "../scripts/markets_cjk_fixture";
import { createApp } from "../src/app";
import { dashboardSnapshotV2Schema } from "../src/schema_v2";
import { MemorySnapshotStore } from "../src/store";
import { marketsDataSourcesFixture } from "./e2e/markets_data_sources_fixture";

const INGEST_TOKEN = "ingest-token-with-adequate-length";
const OPERATOR_TOKEN = "operator-token-with-adequate-length";
const VIEWPORT_WIDTHS = [375, 768, 1280] as const;

describe("markets CJK fixture publishing", () => {
  test("accepts sequential viewport fixtures without triggering stale snapshot policy", async () => {
    // Given: one CJK QA timestamp base and the real stale-snapshot ingest boundary.
    const baseEpochMs = Date.now();
    const app = createApp(new MemorySnapshotStore(), INGEST_TOKEN, OPERATOR_TOKEN);
    const fixture = dashboardSnapshotV2Schema.parse(marketsDataSourcesFixture());

    // When: the three viewport fixtures publish in their QA execution order.
    const statuses: number[] = [];
    for (const [ordinal] of VIEWPORT_WIDTHS.entries()) {
      statuses.push(await ingest(app, fixture, baseEpochMs, ordinal));
    }
    const snapshot = await app.request("/api/snapshot");

    // Then: each publish is accepted and the final snapshot has the final monotonic timestamp.
    expect(statuses).toEqual([202, 202, 202]);
    expect((await snapshot.json()).generated_at).toBe(cjkFixtureGeneratedAt(baseEpochMs, 2));
  });
});

async function ingest(
  app: ReturnType<typeof createApp>,
  fixture: ReturnType<typeof dashboardSnapshotV2Schema.parse>,
  baseEpochMs: number,
  ordinal: number,
): Promise<number> {
  const response = await app.request("/api/ingest", {
    method: "POST",
    headers: {
      authorization: `Bearer ${INGEST_TOKEN}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      ...fixture,
      generated_at: cjkFixtureGeneratedAt(baseEpochMs, ordinal),
    }),
  });
  return response.status;
}
