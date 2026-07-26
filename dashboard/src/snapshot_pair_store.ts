import type { DashboardSnapshotV1 } from "./schema";
import type { DashboardSnapshotV2 } from "./schema_v2";
import type { NormalizedSnapshot } from "./snapshot_normalizer";

export type SnapshotSaveResult = "saved" | "stale";

export interface SnapshotPairTransaction {
  lock(): Promise<void>;
  readCanonical(): Promise<DashboardSnapshotV2 | null>;
  writeCanonical(snapshot: DashboardSnapshotV2): Promise<void>;
  writeRollback(snapshot: DashboardSnapshotV1): Promise<void>;
}

export async function saveSnapshotPair(
  transaction: SnapshotPairTransaction,
  snapshot: NormalizedSnapshot,
): Promise<SnapshotSaveResult> {
  await transaction.lock();
  const current = await transaction.readCanonical();
  if (current !== null && isStale(current, snapshot)) {
    return "stale";
  }
  await transaction.writeCanonical(snapshot.canonical);
  await transaction.writeRollback(snapshot.rollbackV1);
  return "saved";
}

function isStale(current: DashboardSnapshotV2, incoming: NormalizedSnapshot): boolean {
  const order = Date.parse(incoming.canonical.generated_at) - Date.parse(current.generated_at);
  if (order < 0) return true;
  if (order > 0) return false;
  if (incoming.inputVersion < current.projection.source_schema_version) return true;
  return incoming.canonical.snapshot_id <= current.snapshot_id;
}
