import type { DashboardSnapshotV1 } from "./schema";
import type { DashboardSnapshotV2 } from "./schema_v2";
import { downProjectV1 } from "./snapshot_normalizer";

export function projectSnapshotForV1Render(snapshot: DashboardSnapshotV2): DashboardSnapshotV1 {
  return downProjectV1(snapshot);
}
