import { textElement, timeElement } from "../dom";
import { type EvidenceTraceDrawer, resolveEvidenceTrace } from "../evidence_trace";
import type { OptionsWorkbench } from "../options_workbench_schema";
import type { DashboardSnapshotV2 } from "../schema_v2";
import { workbenchTraceButton } from "./options_workbench_trace";

type TraceDrawer = Pick<EvidenceTraceDrawer, "open">;
type Workspace = DashboardSnapshotV2["workspaces"]["paper"];

const CAPACITY = [
  ["Budgets", ["budget"]],
  ["Storage", ["storage", "disk"]],
  ["Backup", ["backup"]],
  ["Soak", ["soak"]],
] as const;

export function renderOptionsWorkbenchOperations(
  workbench: OptionsWorkbench,
  snapshot: DashboardSnapshotV2,
  drawer: TraceDrawer,
): HTMLElement {
  const container = document.createElement("div");
  container.className = "options-operations-panel";
  container.append(textElement("h3", "Promotion candidates"));
  if (workbench.promotions.length === 0) {
    container.append(
      textElement("p", "Promotion candidates unavailable · no canonical projection"),
    );
  } else {
    for (const promotion of workbench.promotions) {
      container.append(promotionCandidate(promotion, snapshot, drawer));
    }
  }
  container.append(
    workspaceSummary("Paper", snapshot.workspaces.paper),
    workspaceSummary("System", snapshot.workspaces.system),
    capacitySummary(snapshot.workspaces.system),
  );
  return container;
}

function promotionCandidate(
  promotion: OptionsWorkbench["promotions"][number],
  snapshot: DashboardSnapshotV2,
  drawer: TraceDrawer,
): HTMLElement {
  const article = document.createElement("article");
  article.className = "options-promotion-candidate";
  article.dataset["promotionCandidate"] = promotion.promotion_id;
  const header = document.createElement("header");
  header.append(
    textElement("h4", promotion.promotion_id),
    workbenchTraceButton(promotion.promotion_id, promotion.trace_id, { snapshot, drawer }),
  );
  const blockerText =
    promotion.blockers.length === 0 ? "no blockers" : promotion.blockers.join(", ");
  const status = textElement("p", `${promotion.state} · ${blockerText}`);
  const gates = document.createElement("dl");
  const trace = resolveEvidenceTrace(
    promotion.trace_id,
    snapshot.traces.nodes,
    snapshot.traces.edges,
  );
  const reviewer = trace.nodes.find((node) => node.kind === "reviewer_decision");
  const manual = promotion.blockers.find((blocker) => blocker.includes("manual_approval"));
  appendGate(
    gates,
    "Evidence",
    `Available · ${promotion.passed_gate_count}/${promotion.total_gate_count} gates passed`,
  );
  appendGate(
    gates,
    "Reviewer",
    reviewer === undefined
      ? "Not projected · reviewer decision"
      : `${reviewer.state} · ${reviewer.label}`,
  );
  appendGate(
    gates,
    "Manual Approval",
    manual === undefined ? "Not projected · manual approval" : `Blocked · ${manual}`,
  );
  appendGate(gates, "Next-session Authority", "Unavailable · not projected");
  article.append(header, status, gates);
  return article;
}

function appendGate(list: HTMLElement, label: string, value: string): void {
  const row = document.createElement("div");
  row.dataset["promotionGate"] = label;
  row.append(textElement("dt", label), textElement("dd", value));
  list.append(row);
}

function workspaceSummary(label: "Paper" | "System", workspace: Workspace): HTMLElement {
  const section = document.createElement("section");
  section.className = "options-operations-summary";
  section.dataset["operationsSummary"] = label.toLowerCase();
  section.append(
    textElement("h4", `${label} operations`),
    workspaceSummaryLine(workspace),
    textElement(
      "p",
      `Freshness · ${workspace.freshness.age_seconds === null ? "Unavailable" : `${workspace.freshness.age_seconds}s`}`,
    ),
    timeElement(workspace.freshness.as_of),
  );
  if (label === "Paper") {
    const reconciliation = workspace.items.find((item) =>
      `${item.item_id} ${item.label}`.toLowerCase().includes("reconcil"),
    );
    section.append(
      textElement(
        "p",
        reconciliation === undefined
          ? "Reconciliation · Unavailable"
          : `Reconciliation · ${reconciliation.state} · ${reconciliation.value ?? reconciliation.label}`,
      ),
    );
  }
  return section;
}

function workspaceSummaryLine(workspace: Workspace): HTMLParagraphElement {
  const paragraph = document.createElement("p");
  paragraph.append(
    document.createTextNode(
      `${workspace.state} · ${workspace.projected_count}/${workspace.total_count} · `,
    ),
  );
  const emptyMarker = "항목 없음";
  const hasEmptyMarker =
    workspace.state === "empty" &&
    workspace.total_count === 0 &&
    workspace.projected_count === 0 &&
    (workspace.summary === emptyMarker || workspace.summary.endsWith(`, ${emptyMarker}`));
  if (!hasEmptyMarker) {
    paragraph.append(document.createTextNode(workspace.summary));
    return paragraph;
  }
  paragraph.append(document.createTextNode(workspace.summary.slice(0, -emptyMarker.length)));
  paragraph.append(textElement("span", emptyMarker, "options-operations-summary-tail"));
  return paragraph;
}

function capacitySummary(system: Workspace): HTMLElement {
  const section = document.createElement("section");
  section.className = "options-operations-capacity";
  section.append(textElement("h4", "Operational capacity evidence"));
  const list = document.createElement("dl");
  for (const [label, terms] of CAPACITY) {
    const item = system.items.find((candidate) =>
      terms.some((term) => `${candidate.item_id} ${candidate.label}`.toLowerCase().includes(term)),
    );
    const row = document.createElement("div");
    row.dataset["operationsCapacity"] = label;
    row.append(
      textElement("dt", label),
      textElement(
        "dd",
        item === undefined
          ? "Unavailable · no matching system item"
          : `${item.state} · ${item.value ?? item.label}`,
      ),
    );
    list.append(row);
  }
  section.append(list);
  return section;
}
