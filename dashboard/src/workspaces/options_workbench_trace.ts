import { buttonElement } from "../dom";
import type { EvidenceTraceDrawer } from "../evidence_trace";
import { resolveEvidenceTrace } from "../evidence_trace";
import type { DashboardSnapshotV2 } from "../schema_v2";

export type WorkbenchTraceContext = Readonly<{
  snapshot: DashboardSnapshotV2;
  drawer: Pick<EvidenceTraceDrawer, "open">;
}>;

export function workbenchTraceButton(
  label: string,
  traceId: string,
  context: WorkbenchTraceContext,
): HTMLButtonElement {
  const button = buttonElement("Trace", "trace-button");
  button.dataset["traceId"] = traceId;
  button.setAttribute("aria-label", `${label} Evidence Trace 열기`);
  button.addEventListener("click", () => {
    context.drawer.open(
      label,
      resolveEvidenceTrace(traceId, context.snapshot.traces.nodes, context.snapshot.traces.edges),
      button,
    );
  });
  return button;
}
