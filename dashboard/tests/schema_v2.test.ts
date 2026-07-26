import { describe, expect, test } from "bun:test";
import { createApp } from "../src/app";
import { dashboardSnapshotV2Schema } from "../src/schema";
import { MemorySnapshotStore } from "../src/store";
import { snapshotV1 } from "./snapshot_v1_fixture";
import { nearMaximumSnapshotV2, snapshotV2 } from "./snapshot_v2_fixture";
import { oversizedSnapshotV2 } from "./snapshot_v2_limit_fixture";

const INGEST_TOKEN = "ingest-token-with-adequate-length";
const OPERATOR_TOKEN = "operator-token-with-adequate-length";

describe("snapshot v2 compatibility boundary", () => {
  test("accepts v1 and returns canonical v2 when the compatibility rollout is active", async () => {
    // Given: a compatibility server and a current v1 publisher payload.
    const app = createApp(new MemorySnapshotStore(), INGEST_TOKEN, OPERATOR_TOKEN);

    // When: the v1 payload is ingested and the public snapshot is read.
    const accepted = await app.request("/api/ingest", {
      method: "POST",
      headers: {
        authorization: `Bearer ${INGEST_TOKEN}`,
        "content-type": "application/json",
      },
      body: JSON.stringify(snapshotV1),
    });
    const viewed = await app.request("/api/snapshot");

    // Then: compatibility input is accepted but the viewer contract is canonical v2.
    expect(accepted.status).toBe(202);
    expect(viewed.status).toBe(200);
    expect(await viewed.json()).toMatchObject({
      schema_version: 2,
      projection: { source_schema_version: 1 },
    });
  });

  test("parses a strict bounded v2 fixture", () => {
    // Given: a complete v2 envelope with all nine workspace projections.
    // When: the public schema parses the envelope.
    const parsed = dashboardSnapshotV2Schema.safeParse(snapshotV2);

    // Then: v2 is accepted without dropping its version discriminator.
    expect(parsed.success).toBe(true);
    if (parsed.success) {
      expect(parsed.data.schema_version).toBe(2);
    }
    expect(new TextEncoder().encode(JSON.stringify(snapshotV2)).byteLength).toBeLessThan(
      256 * 1024,
    );
  });

  test("round-trips a near-maximum populated fixture below 256 KiB", () => {
    const parsed = dashboardSnapshotV2Schema.parse(nearMaximumSnapshotV2);
    const bytes = new TextEncoder().encode(JSON.stringify(parsed)).byteLength;

    expect(JSON.stringify(parsed)).toBe(JSON.stringify(nearMaximumSnapshotV2));
    expect(bytes).toBeGreaterThanOrEqual(Math.ceil(256 * 1024 * 0.9));
    expect(bytes).toBeLessThan(256 * 1024);
    expect(parsed.projection.projected_count).toBe(216);
  });

  test("rejects one item beyond the workspace collection cap", () => {
    const overCap = {
      ...nearMaximumSnapshotV2,
      workspaces: {
        ...nearMaximumSnapshotV2.workspaces,
        system: {
          ...nearMaximumSnapshotV2.workspaces.system,
          items: [
            ...nearMaximumSnapshotV2.workspaces.system.items,
            nearMaximumSnapshotV2.workspaces.system.items[0],
          ],
          total_count: 25,
          projected_count: 25,
        },
      },
    };

    expect(dashboardSnapshotV2Schema.safeParse(overCap).success).toBe(false);
  });

  test("rejects a deterministic valid-shape fixture above 256 KiB", () => {
    expect(
      new TextEncoder().encode(JSON.stringify(oversizedSnapshotV2)).byteLength,
    ).toBeGreaterThan(256 * 1024);
    expect(dashboardSnapshotV2Schema.safeParse(oversizedSnapshotV2).success).toBe(false);
  });

  test("rejects text beyond the item label cap", () => {
    const first = nearMaximumSnapshotV2.workspaces.system.items[0];
    const overCap = {
      ...nearMaximumSnapshotV2,
      workspaces: {
        ...nearMaximumSnapshotV2.workspaces,
        system: {
          ...nearMaximumSnapshotV2.workspaces.system,
          items: [
            { ...first, label: "x".repeat(81) },
            ...nearMaximumSnapshotV2.workspaces.system.items.slice(1),
          ],
        },
      },
    };

    expect(dashboardSnapshotV2Schema.safeParse(overCap).success).toBe(false);
  });

  test("rejects inconsistent counts", () => {
    const badCounts = {
      ...snapshotV2,
      projection: { ...snapshotV2.projection, total_count: 1, truncated: false },
    };

    expect(dashboardSnapshotV2Schema.safeParse(badCounts).success).toBe(false);
  });

  test("rejects dangling workspace trace references", () => {
    const dangling = {
      ...snapshotV2,
      workspaces: {
        ...snapshotV2.workspaces,
        system: { ...snapshotV2.workspaces.system, trace_id: "missing-node" },
      },
    };

    expect(dashboardSnapshotV2Schema.safeParse(dangling).success).toBe(false);
  });

  test("rejects invalid trace edges", () => {
    const invalidEdge = {
      ...snapshotV2,
      traces: {
        ...snapshotV2.traces,
        edges: [
          ...snapshotV2.traces.edges,
          {
            from_node_id: "trace-source",
            to_node_id: "missing-node",
            kind: "derived_from",
          },
        ],
      },
    };

    expect(dashboardSnapshotV2Schema.safeParse(invalidEdge).success).toBe(false);
  });

  test("rejects cyclic trace graphs", () => {
    const cyclic = {
      ...snapshotV2,
      traces: {
        ...snapshotV2.traces,
        edges: [
          ...snapshotV2.traces.edges,
          {
            from_node_id: "trace-research-terminal",
            to_node_id: "trace-research",
            kind: "reviewed_by",
          },
        ],
      },
    };

    expect(dashboardSnapshotV2Schema.safeParse(cyclic).success).toBe(false);
  });

  test("rejects duplicate trace node identifiers", () => {
    const duplicate = {
      ...snapshotV2,
      traces: {
        ...snapshotV2.traces,
        nodes: [...snapshotV2.traces.nodes, snapshotV2.traces.nodes[0]],
      },
    };

    expect(dashboardSnapshotV2Schema.safeParse(duplicate).success).toBe(false);
  });

  test("rejects far-future generated timestamps", () => {
    const future = { ...snapshotV2, generated_at: "2099-01-01T00:00:00Z" };

    expect(dashboardSnapshotV2Schema.safeParse(future).success).toBe(false);
  });

  test("rejects publisher-serialized loading state", () => {
    const loading = {
      ...snapshotV2,
      workspaces: {
        ...snapshotV2.workspaces,
        system: { ...snapshotV2.workspaces.system, state: "loading" },
      },
    };

    expect(dashboardSnapshotV2Schema.safeParse(loading).success).toBe(false);
  });

  test("rejects graphs without a source receipt", () => {
    const noSource = {
      ...snapshotV2,
      traces: {
        ...snapshotV2.traces,
        nodes: snapshotV2.traces.nodes.map((node) => ({ ...node, kind: "observation" })),
      },
    };

    expect(dashboardSnapshotV2Schema.safeParse(noSource).success).toBe(false);
  });

  test("rejects graphs without an allowed terminal", () => {
    const noTerminal = {
      ...snapshotV2,
      traces: {
        nodes: snapshotV2.traces.nodes.filter((node) => node.kind === "source_receipt"),
        edges: [],
      },
    };

    expect(dashboardSnapshotV2Schema.safeParse(noTerminal).success).toBe(false);
  });

  test("rejects secret-shaped nested keys", async () => {
    // Given: an accepted v2 snapshot and a later payload with a nested secret-shaped key.
    const store = new MemorySnapshotStore();
    const app = createApp(store, INGEST_TOKEN, OPERATOR_TOKEN);
    await ingest(app, snapshotV2);
    const unsafe = {
      ...snapshotV2,
      workspaces: {
        ...snapshotV2.workspaces,
        system: { ...snapshotV2.workspaces.system, api_token: "forbidden" },
      },
    };

    // When: the unsafe payload crosses the ingest boundary.
    const rejected = await ingest(app, unsafe);
    const viewed = await app.request("/api/snapshot");

    // Then: it fails closed and does not overwrite the previous snapshot.
    expect(rejected.status).toBe(400);
    expect(await viewed.json()).toEqual(snapshotV2);
  });

  test("rejects unknown versions and oversized payloads without overwrite", async () => {
    // Given: a previously accepted canonical snapshot.
    const store = new MemorySnapshotStore();
    const app = createApp(store, INGEST_TOKEN, OPERATOR_TOKEN);
    await ingest(app, snapshotV2);

    // When: unknown-version and oversized payloads are submitted.
    const unknown = await ingest(app, { ...snapshotV2, schema_version: 3 });
    const oversized = await ingest(app, {
      ...snapshotV2,
      padding: "x".repeat(256 * 1024),
    });

    // Then: both fail and the canonical snapshot is unchanged.
    expect(unknown.status).toBe(400);
    expect(oversized.status).toBe(413);
    expect(await (await app.request("/api/snapshot")).json()).toEqual(snapshotV2);
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
