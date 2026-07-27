import { textElement, timeElement } from "../dom";
import type { EvidenceTraceDrawer } from "../evidence_trace";
import { resolveEvidenceTrace } from "../evidence_trace";
import { sourceStatePresentation } from "../render";
import type { DashboardSnapshotV2 } from "../schema_v2";
import { elementCell, tableCell, tableHead } from "../ui_table";
import {
  causalTracePresentation,
  familyRoster,
  promotionGate,
  type ReceiptOriginInputs,
} from "./research_strategies_evidence";
import { authorityRow, renderAutonomousLedger, traceButton } from "./research_strategies_rows";

type WorkspaceKey = "research" | "strategies";
type Workspace = DashboardSnapshotV2["workspaces"][WorkspaceKey];

export const EMPTY_RECEIPT_ORIGINS: ReceiptOriginInputs = {
  interactions: [],
  directedJobs: [],
  autonomousTasks: [],
};

export function renderResearchStrategiesWorkspace(
  workspaceKey: WorkspaceKey,
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
  receipts: ReceiptOriginInputs,
): DocumentFragment {
  const workspace = snapshot.workspaces[workspaceKey];
  const fragment = document.createDocumentFragment();
  fragment.append(renderSummary(workspace, snapshot, drawer));
  fragment.append(renderCausalLedger(workspaceKey, workspace, snapshot, drawer));
  if (workspaceKey === "strategies")
    fragment.append(renderStrategyGovernance(workspace, snapshot, drawer));
  fragment.append(renderAutonomousLedger(workspace, snapshot, drawer, receipts));
  return fragment;
}

function renderSummary(
  workspace: Workspace,
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
): HTMLElement {
  const presentation = sourceStatePresentation(workspace.state);
  const section = document.createElement("section");
  section.className = `source-state-panel state-${presentation.tone}`;
  section.dataset["sourceState"] = workspace.state;
  const heading = document.createElement("div");
  heading.className = "state-panel-heading";
  heading.append(
    textElement("span", presentation.label, `state-badge state-${presentation.tone}`),
    traceButton("workspace", workspace.trace_id, snapshot, drawer),
  );
  section.append(
    heading,
    textElement("h2", workspace.summary),
    textElement("p", presentation.guidance, "state-guidance"),
    textElement(
      "p",
      `${workspace.projected_count}/${workspace.total_count} canonical records projected`,
      "source-count",
    ),
  );
  if (workspace.blocker_code !== null)
    section.append(textElement("p", `Blocker · ${workspace.blocker_code}`, "blocker-notice"));
  return section;
}

function renderCausalLedger(
  workspaceKey: WorkspaceKey,
  workspace: Workspace,
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
): HTMLElement {
  const section = document.createElement("section");
  section.className = "provider-capability-section";
  section.append(textElement("h2", "가설·실험 증거 원장"));
  const viewport = document.createElement("div");
  viewport.className = "table-viewport";
  viewport.tabIndex = 0;
  viewport.setAttribute("role", "region");
  viewport.setAttribute("aria-label", "causal evidence queue");
  const table = document.createElement("table");
  const body = document.createElement("tbody");
  for (const item of workspace.items.filter((candidate) => candidate.kind !== "system")) {
    const trace = resolveEvidenceTrace(item.trace_id, snapshot.traces.nodes, snapshot.traces.edges);
    const evidence = causalTracePresentation(workspaceKey, item.state, trace);
    const row = document.createElement("tr");
    row.dataset["sourceState"] = evidence.state;
    row.append(
      tableCell(item.label),
      tableCell(
        evidence.datasetSha ?? `Blocker · ${evidence.missingStage ?? "dataset"} authority missing`,
      ),
      tableCell(
        evidence.missingStage === null
          ? "review gate resolved"
          : `Blocker · ${evidence.missingStage} missing`,
      ),
      tableCell(item.value ?? "Unavailable · code version not published"),
      elementCell(timeElement(item.observed_at)),
      elementCell(traceButton(item.label, item.trace_id, snapshot, drawer)),
    );
    body.append(row);
  }
  if (workspace.items.length === 0) {
    const row = document.createElement("tr");
    const cell = tableCell("0 records · canonical causal queue is empty");
    cell.colSpan = 6;
    row.append(cell);
    body.append(row);
  }
  table.append(
    tableHead([
      "Queue entry",
      "Dataset SHA",
      "Review gate",
      "Code version",
      "Observed",
      "Evidence Trace",
    ]),
    body,
  );
  viewport.append(table);
  section.append(viewport);
  return section;
}

function renderStrategyGovernance(
  workspace: Workspace,
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
): HTMLElement {
  const section = document.createElement("section");
  section.className = "bounded-list";
  section.append(textElement("h2", "전략 승격과 배분 gate"));
  const candidates = workspace.items.filter((candidate) => candidate.kind === "strategy");
  const gates = candidates.map((item) => ({
    item,
    gate: promotionGate(
      resolveEvidenceTrace(item.trace_id, snapshot.traces.nodes, snapshot.traces.edges),
    ),
  }));
  for (const { item, gate } of gates) {
    section.append(
      authorityRow(
        `${item.label} · family/lane/version/trial`,
        gate.state === "resolved"
          ? "Lifecycle terminal authority resolved · read-only; no mutation control"
          : `Lifecycle promotion blocked · ${gate.reason ?? "lifecycle"} authority missing`,
        item.trace_id,
        snapshot,
        drawer,
      ),
    );
  }
  const allocation = workspace.items.find(
    (item) => item.item_id === "strategies.allocation_authority",
  );
  const terminalAuthority = gates.some(({ gate }) => gate.state === "resolved")
    ? "Resolved · persisted Reviewer and lifecycle terminal authority is present; read-only"
    : "Blocked · terminal Reviewer and lifecycle receipts are both required; no candidate is promoted here";
  section.append(
    authorityRow(
      "Walk-forward and overfit diagnostics",
      "Unavailable · canonical v2 trace does not type walk-forward or overfit diagnostics",
      workspace.trace_id,
      snapshot,
      drawer,
    ),
    authorityRow(
      "Independent Reviewer / lifecycle",
      terminalAuthority,
      workspace.trace_id,
      snapshot,
      drawer,
    ),
    authorityRow(
      "Allocation Manager",
      allocation?.value ?? "Unavailable · allocation authority receipt is not projected",
      allocation?.trace_id ?? workspace.trace_id,
      snapshot,
      drawer,
    ),
  );
  const roster = familyRoster(snapshot.workspaces.command_center.agents);
  for (const family of roster) {
    section.append(
      authorityRow(
        family.familyId,
        family.published
          ? `${family.agent?.role ?? "published family"} · ${family.agent?.runtime_state ?? "unavailable"}`
          : "Unavailable · family receipt not published",
        family.agent?.trace_id ?? workspace.trace_id,
        snapshot,
        drawer,
      ),
    );
  }
  section.append(
    authorityRow(
      "Loop Engineer",
      "Control-plane role only · not a research family and not an allocation authority",
      workspace.trace_id,
      snapshot,
      drawer,
    ),
  );
  return section;
}
