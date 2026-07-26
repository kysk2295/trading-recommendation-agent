import { buttonElement, textElement } from "../dom";
import type { EvidenceTraceDrawer } from "../evidence_trace";
import { resolveEvidenceTrace } from "../evidence_trace";
import type { DashboardSnapshotV2 } from "../schema_v2";
import {
  originReceipts,
  type ReceiptOriginInputs,
  receiptBlockers,
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
    section.append(autonomousReceiptRow(task, receipts.autonomousTasks));
  }
  for (const event of receipts.directedJobs) section.append(directedReceiptRow(event));
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

function directedReceiptRow(event: ReceiptOriginInputs["directedJobs"][number]): HTMLElement {
  const detail = [
    `${event.agent_family_id} · ${event.job_kind}/${event.kind} · ${event.state}`,
    `evidence:${event.evidence_sha256 ?? "unavailable"}`,
    `result:${event.result_sha256 ?? "unavailable"}`,
    "cleanup:unavailable · reviewer:unavailable · lifecycle:unavailable",
  ].join(" · ");
  return receiptRow("Directed receipt", detail, event.summary);
}

function autonomousReceiptRow(
  task: ReceiptOriginInputs["autonomousTasks"][number],
  tasks: ReceiptOriginInputs["autonomousTasks"],
): HTMLElement {
  const missing = receiptBlockers(task, tasks);
  const detail = [
    `${task.agent_family_id} · ${task.trigger_type}/${task.kind} · ${task.state}`,
    `budget:${task.consumed_tokens} tokens · cost:${task.consumed_cost_microusd}μUSD`,
    `evidence:${task.evidence_refs.join(",") || "unavailable"}`,
    `result:${task.result_sha256 ?? "unavailable"}`,
    `reviewer:${task.reviewer_state} · lifecycle:${task.lifecycle_state}`,
    missing.length === 0 ? "terminals resolved" : `Blocked · ${missing.join(", ")} missing`,
  ].join(" · ");
  return receiptRow("Autonomous receipt", detail, task.summary);
}

function receiptRow(label: string, detail: string, summary: string | null): HTMLElement {
  const row = document.createElement("article");
  row.append(
    textElement("strong", label),
    textElement("p", detail),
    ...(summary === null ? [] : [textElement("p", summary)]),
    unavailableTraceButton(),
  );
  return row;
}

function unavailableTraceButton(): HTMLButtonElement {
  const button = buttonElement("Trace unavailable", "trace-button");
  button.disabled = true;
  button.title = "Receipt has no canonical Evidence Trace authority";
  return button;
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
