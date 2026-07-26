import { buttonElement, textElement, timeElement } from "../dom";
import type { EvidenceTraceDrawer } from "../evidence_trace";
import { resolveEvidenceTrace } from "../evidence_trace";
import { sourceStatePresentation } from "../render";
import type { DashboardSnapshotV2 } from "../schema_v2";
import type { WorkspaceRenderer } from "./types";

const PROVIDERS = [
  "fred",
  "alfred",
  "treasury",
  "cftc",
  "opendart",
  "kis",
  "ls",
  "alpaca",
] as const;
type Provider = (typeof PROVIDERS)[number];
type Capability = DashboardSnapshotV2["workspaces"]["data_sources"]["capabilities"][number];

export const renderDataSources: WorkspaceRenderer = (snapshot, drawer) => {
  const workspace = snapshot.workspaces.data_sources;
  const fragment = document.createDocumentFragment();
  fragment.append(renderSummary(workspace, snapshot, drawer));
  fragment.append(
    renderProviderTable(workspace.capabilities, workspace.trace_id, snapshot, drawer),
  );
  return fragment;
};

export function providerCoverageText(): string {
  return "미게시 · normalized v2 snapshot에 provider coverage field가 없습니다";
}

export function providerQuoteNotice(entitlement: Capability["entitlement"]): string {
  return `현재 quote 미표시 · ${entitlement} entitlement이며 redistribution permit이 snapshot에 없습니다`;
}

function renderSummary(
  workspace: DashboardSnapshotV2["workspaces"]["data_sources"],
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
): HTMLElement {
  const presentation = sourceStatePresentation(workspace.state);
  const section = document.createElement("section");
  section.className = `source-state-panel data-sources-summary state-${presentation.tone}`;
  section.dataset["sourceState"] = workspace.state;
  const heading = document.createElement("div");
  heading.className = "state-panel-heading";
  heading.append(
    textElement("h2", workspace.summary),
    traceButton(presentation.label, workspace.trace_id, snapshot, drawer),
  );
  section.append(
    heading,
    textElement("p", `${presentation.label} · ${presentation.guidance}`, "state-guidance"),
    textElement(
      "p",
      `${workspace.projected_count}/${workspace.total_count} provider evidence projected`,
      "source-count",
    ),
  );
  if (workspace.blocker_code !== null)
    section.append(textElement("p", `Blocker · ${workspace.blocker_code}`, "blocker-notice"));
  return section;
}

function renderProviderTable(
  capabilities: readonly Capability[],
  rootTraceId: string,
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
): HTMLElement {
  const section = document.createElement("section");
  section.className = "provider-capability-section";
  section.append(textElement("h2", "Eight authoritative provider capabilities"));
  const viewport = document.createElement("div");
  viewport.className = "table-viewport provider-table-viewport";
  viewport.tabIndex = 0;
  viewport.setAttribute("role", "region");
  viewport.setAttribute("aria-label", "8개 provider capability 표");
  const table = document.createElement("table");
  table.append(renderHead(), renderBody(capabilities, rootTraceId, snapshot, drawer));
  viewport.append(table);
  section.append(viewport);
  return section;
}

function renderHead(): HTMLTableSectionElement {
  const head = document.createElement("thead");
  const row = document.createElement("tr");
  for (const value of [
    "Provider",
    "Entitlement",
    "Freshness",
    "Coverage",
    "Receipt / blocker",
    "Evidence Trace",
  ])
    row.append(textElement("th", value));
  head.append(row);
  return head;
}

function renderBody(
  capabilities: readonly Capability[],
  rootTraceId: string,
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
): HTMLTableSectionElement {
  const body = document.createElement("tbody");
  for (const provider of PROVIDERS) {
    const capability = capabilities.find((candidate) => candidate.provider === provider);
    body.append(renderProviderRow(provider, capability, rootTraceId, snapshot, drawer));
  }
  return body;
}

function renderProviderRow(
  provider: Provider,
  capability: Capability | undefined,
  rootTraceId: string,
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
): HTMLTableRowElement {
  const row = document.createElement("tr");
  if (capability === undefined) {
    row.append(
      textElement("th", provider.toUpperCase()),
      textElement("td", "unavailable"),
      textElement("td", "관측 없음"),
      textElement("td", providerCoverageText()),
      textElement("td", "Blocker · canonical capability 미게시"),
      traceCell(traceButton(`${provider.toUpperCase()} missing`, rootTraceId, snapshot, drawer)),
    );
    return row;
  }
  const trace = resolveEvidenceTrace(
    capability.trace_id,
    snapshot.traces.nodes,
    snapshot.traces.edges,
  );
  const presentation = sourceStatePresentation(capability.state);
  row.append(
    textElement("th", capability.label),
    textElement("td", capability.entitlement),
    freshnessCell(capability, presentation.label, presentation.tone),
    textElement("td", providerCoverageText()),
    textElement(
      "td",
      receiptText(
        trace.status,
        trace.terminal?.label ?? null,
        capability.state,
        capability.entitlement,
      ),
    ),
    traceCell(traceButton(capability.label, capability.trace_id, snapshot, drawer)),
  );
  row.dataset["sourceState"] = capability.state;
  row.setAttribute("aria-label", `${capability.label} ${presentation.label}`);
  return row;
}

function freshnessCell(
  capability: Capability,
  stateLabel: string,
  tone: "neutral" | "success" | "warning" | "error",
): HTMLTableCellElement {
  const cell = document.createElement("td");
  cell.append(textElement("span", stateLabel, `state-badge state-${tone}`));
  cell.append(
    capability.observed_at === null
      ? textElement("span", "관측 없음")
      : timeElement(capability.observed_at),
  );
  cell.append(
    textElement("small", providerQuoteNotice(capability.entitlement), "provider-quote-notice"),
  );
  return cell;
}

function receiptText(
  status: "resolved" | "unavailable" | "corrupt",
  terminal: string | null,
  state: Capability["state"],
  entitlement: Capability["entitlement"],
): string {
  const receipt = terminal === null ? status : `${status} · ${terminal}`;
  if (state === "unavailable" || entitlement === "unavailable") {
    return `${receipt} · Blocker: entitlement unavailable`;
  }
  return `${status} · receipt verified · Blocker: capability에 미게시`;
}

function traceCell(button: HTMLButtonElement): HTMLTableCellElement {
  const cell = document.createElement("td");
  cell.append(button);
  return cell;
}

function traceButton(
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
