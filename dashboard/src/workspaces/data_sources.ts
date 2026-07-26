import { buttonElement, textElement, timeElement } from "../dom";
import type { EvidenceTraceDrawer } from "../evidence_trace";
import { resolveEvidenceTrace } from "../evidence_trace";
import { sourceStatePresentation } from "../render";
import type { DashboardSnapshotV2 } from "../schema_v2";
import {
  type Capability,
  PROVIDERS,
  type Provider,
  type ProviderEvidencePresentation,
  providerEvidencePresentation,
  providerQuoteNotice,
} from "./data_source_evidence";
import type { WorkspaceRenderer } from "./types";

export type { ProviderEvidencePath } from "./data_source_evidence";
export {
  providerCoverageText,
  providerEvidencePresentation,
  providerQuoteNotice,
} from "./data_source_evidence";

export const renderDataSources: WorkspaceRenderer = (snapshot, drawer) => {
  const workspace = snapshot.workspaces.data_sources;
  const fragment = document.createDocumentFragment();
  fragment.append(
    renderSummary(workspace, snapshot, drawer),
    renderProviderTable(workspace.capabilities, snapshot, drawer),
  );
  return fragment;
};

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
  table.append(renderHead(), renderBody(capabilities, snapshot, drawer));
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
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
): HTMLTableSectionElement {
  const body = document.createElement("tbody");
  const traceIds = capabilities.map((capability) => capability.trace_id);
  for (const provider of PROVIDERS) {
    const capability = capabilities.find((candidate) => candidate.provider === provider);
    const trace =
      capability === undefined
        ? resolveEvidenceTrace(
            `trace.data_sources.${provider}.missing`,
            snapshot.traces.nodes,
            snapshot.traces.edges,
          )
        : resolveEvidenceTrace(capability.trace_id, snapshot.traces.nodes, snapshot.traces.edges);
    const display = providerEvidencePresentation(provider, capability, trace, traceIds);
    body.append(renderProviderRow(provider, capability, display, snapshot, drawer));
  }
  return body;
}

function renderProviderRow(
  provider: Provider,
  capability: Capability | undefined,
  display: ProviderEvidencePresentation,
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
): HTMLTableRowElement {
  const presentation = sourceStatePresentation(display.state);
  const row = document.createElement("tr");
  row.dataset["sourceState"] = display.state;
  row.append(
    textElement("th", capability?.label ?? provider.toUpperCase()),
    textElement("td", capability?.entitlement ?? "unavailable"),
    freshnessCell(capability, presentation.label, presentation.tone),
    textElement("td", display.coverage),
    textElement("td", display.receipt),
    traceCell(
      traceButton(
        capability?.label ?? `${provider.toUpperCase()} capability missing`,
        display.traceId ?? `trace.data_sources.${provider}.missing`,
        snapshot,
        drawer,
      ),
    ),
  );
  return row;
}

function freshnessCell(
  capability: Capability | undefined,
  stateLabel: string,
  tone: "neutral" | "success" | "warning" | "error",
): HTMLTableCellElement {
  const cell = document.createElement("td");
  cell.append(textElement("span", stateLabel, `state-badge state-${tone}`));
  cell.append(
    capability?.observed_at === undefined || capability.observed_at === null
      ? textElement("span", "관측 없음 · freshness age 미게시")
      : timeElement(capability.observed_at),
  );
  if (capability !== undefined)
    cell.append(
      textElement("small", providerQuoteNotice(capability.entitlement), "provider-quote-notice"),
    );
  return cell;
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
