import { describe, expect, test } from "bun:test";
import { createApp } from "../src/app";
import { dashboardSnapshotV1Schema } from "../src/schema";
import { MemorySnapshotStore } from "../src/store";
import { snapshotV1 } from "./snapshot_v1_fixture";
import { snapshotV2 } from "./snapshot_v2_fixture";

const INGEST_TOKEN = "ingest-token-with-adequate-length";
const OPERATOR_TOKEN = "operator-token-with-adequate-length";

describe("rolling snapshot storage", () => {
  test("retains a strict v1 rollback payload after v2 ingest", async () => {
    // Given: an empty rolling store.
    const store = new MemorySnapshotStore();
    const app = createApp(store, INGEST_TOKEN, OPERATOR_TOKEN);

    // When: the publisher ingests canonical v2.
    await ingest(app, snapshotV2);

    // Then: the rollback location remains readable by the original v1 schema.
    expect(dashboardSnapshotV1Schema.safeParse(await store.latestV1()).success).toBe(true);
  });

  test("does not let stale v1 overwrite newer canonical v2", async () => {
    // Given: the canonical store already contains a newer v2 projection.
    const store = new MemorySnapshotStore();
    const app = createApp(store, INGEST_TOKEN, OPERATOR_TOKEN);
    await ingest(app, snapshotV2);

    // When: a delayed v1 publisher submits an older snapshot.
    const delayed = await ingest(app, snapshotV1);
    const viewed = await app.request("/api/snapshot");

    // Then: v1 is rejected at the boundary and v2 remains current.
    expect(delayed.status).toBe(400);
    expect(await viewed.json()).toEqual(snapshotV2);
  });

  test("rejects a v1 collision at the same timestamp as v2", async () => {
    // Given: a v2 snapshot stored at a specific projection time.
    const store = new MemorySnapshotStore();
    const app = createApp(store, INGEST_TOKEN, OPERATOR_TOKEN);
    await ingest(app, snapshotV2);
    const duplicateTimeV1 = { ...snapshotV1, generated_at: snapshotV2.generated_at };

    // When: a v1 publisher submits the same timestamp.
    const delayed = await ingest(app, duplicateTimeV1);

    // Then: the v2 epoch wins deterministically.
    expect(delayed.status).toBe(400);
  });

  test("rejects an equal instant expressed with a different offset", async () => {
    // Given: v2 is stored at 03:00Z.
    const store = new MemorySnapshotStore();
    const app = createApp(store, INGEST_TOKEN, OPERATOR_TOKEN);
    await ingest(app, snapshotV2);
    const sameInstantV1 = { ...snapshotV1, generated_at: "2026-07-26T12:00:00+09:00" };

    // When: v1 expresses that same instant using Seoul offset.
    const result = await ingest(app, sameInstantV1);

    // Then: lower-version v1 cannot win an equal-instant collision.
    expect(result.status).toBe(400);
  });

  test("rejects an older instant whose offset string sorts later", async () => {
    // Given: v2 is stored at 03:00Z.
    const store = new MemorySnapshotStore();
    const app = createApp(store, INGEST_TOKEN, OPERATOR_TOKEN);
    await ingest(app, snapshotV2);
    const olderV1 = { ...snapshotV1, generated_at: "2026-07-26T11:59:59+09:00" };

    // When: the chronologically older v1 string sorts lexically after the Z string.
    const result = await ingest(app, olderV1);

    // Then: instant ordering still rejects it.
    expect(result.status).toBe(400);
  });
});

async function ingest(app: ReturnType<typeof createApp>, payload: unknown): Promise<Response> {
  return app.request("/api/ingest", {
    method: "POST",
    headers: {
      authorization: `Bearer ${INGEST_TOKEN}`,
      "content-type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}
