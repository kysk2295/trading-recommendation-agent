import type { EvidenceTraceDrawer } from "../evidence_trace";
import type { OperatorReceiptSnapshot } from "../operator_receipts";
import type { DashboardSnapshotV2 } from "../schema_v2";

export type WorkspaceRenderer = (
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
  context: WorkspaceRenderContext,
) => DocumentFragment;

export type WorkspaceRenderContext = Readonly<{
  receipts: OperatorReceiptSnapshot;
}>;
