import type { EvidenceTraceDrawer } from "../evidence_trace";
import type { DashboardSnapshotV2 } from "../schema_v2";

export type WorkspaceRenderer = (
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
) => DocumentFragment;
