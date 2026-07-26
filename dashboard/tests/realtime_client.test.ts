import { describe, expect, test } from "bun:test";
import { reconnectDelayMs } from "../src/realtime_client";
import { viewerMessageSchema } from "../src/schema";
import { snapshotV2 } from "./snapshot_v2_fixture";

describe("dashboard realtime client", () => {
  test("uses bounded exponential reconnect instead of periodic polling", () => {
    expect([0, 1, 2, 3, 8].map(reconnectDelayMs)).toEqual([1_000, 2_000, 4_000, 8_000, 60_000]);
  });

  test("contains no periodic snapshot network refresh", async () => {
    const source = await Bun.file(new URL("../src/client.ts", import.meta.url)).text();

    expect(source).not.toContain("POLL_INTERVAL");
    expect(source).not.toContain("setInterval(() => void refreshSnapshot()");
  });

  test("keeps canonical v2 through the browser websocket parser", () => {
    const parsed = viewerMessageSchema.parse({ type: "snapshot", snapshot: snapshotV2 });

    expect(parsed.snapshot.schema_version).toBe(2);
    expect(parsed.snapshot.snapshot_id).toBe(snapshotV2.snapshot_id);
  });
});
