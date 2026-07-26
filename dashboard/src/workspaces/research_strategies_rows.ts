import { buttonElement, textElement } from "../dom";
import type { EvidenceTraceDrawer } from "../evidence_trace";
import { resolveEvidenceTrace } from "../evidence_trace";
import type { DashboardSnapshotV2 } from "../schema_v2";
import {
  autonomousReceiptPresentation,
  originReceipts,
  type ReceiptOriginInputs,
} from "./research_strategies_evidence";

export function renderAutonomousLedger(
  workspace: DashboardSnapshotV2["workspaces"]["research"],
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
  receipts: ReceiptOriginInputs,
): HTMLElement {
  const section = document.createElement("section");
  section.className = "provider-capability-section";
  section.append(textElement("h2", "Origin, autonomous trigger, job, and receipt ledger"));
  for (const origin of originReceipts(receipts)) {
    section.append(
      authorityRow(
        origin.origin,
        origin.count === 0
          ? "Unavailable · no typed receipt in the current operator feed"
          : `${origin.count} typed receipt(s)`,
        workspace.trace_id,
        snapshot,
        drawer,
      ),
    );
  }
  for (const task of receipts.autonomousTasks) {
    const gate = autonomousReceiptPresentation(task);
    section.append(
      authorityRow(
        `${task.trigger_type} · ${task.kind} · ${task.state}`,
        `Autonomous receipt blocked · ${gate.reason} terminal missing; cleanup/budget authority is not inferred`,
        workspace.trace_id,
        snapshot,
        drawer,
      ),
    );
  }
  if (receipts.autonomousTasks.length === 0) {
    section.append(
      authorityRow(
        "Isolated cleanup / budget / Reviewer receipts",
        "Blocked · canonical public snapshot and current operator feed provide no matching authority receipt",
        workspace.trace_id,
        snapshot,
        drawer,
      ),
    );
  }
  return section;
}

export function authorityRow(
  label: string,
  detail: string,
  traceId: string,
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
): HTMLElement {
  const row = document.createElement("article");
  row.append(
    textElement("strong", label),
    textElement("p", detail),
    traceButton(label, traceId, snapshot, drawer),
  );
  return row;
}

export function traceButton(
  label: string,
  traceId: string,
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
): HTMLButtonElement {
  const button = buttonElement("Trace", "trace-button");
  button.setAttribute("aria-label", `${label} Evidence Trace 열기`);
  button.addEventListener("click", () =>
    drawer.open(
      label,
      resolveEvidenceTrace(traceId, snapshot.traces.nodes, snapshot.traces.edges),
      button,
    ),
  );
  return button;
}
