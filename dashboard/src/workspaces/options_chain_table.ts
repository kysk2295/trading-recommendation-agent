import { buttonElement, textElement } from "../dom";
import type { EvidenceTraceDrawer } from "../evidence_trace";
import { resolveEvidenceTrace } from "../evidence_trace";
import type { OptionsWorkbench } from "../options_workbench_schema";
import type { DashboardSnapshotV2 } from "../schema_v2";
import { selectableResearchLeg } from "./options_workbench_presenters";

type Chain = OptionsWorkbench["chain"];
type ChainCell = NonNullable<Chain["rows"][number]["call"]>;
type TraceDrawer = Pick<EvidenceTraceDrawer, "open">;

export type ChainLegSelection = Readonly<{
  contractId: string;
  side: "call" | "put";
  strike: number;
  premium: number;
  traceId: string;
}>;

export function renderOptionsChainTable(
  chain: Chain,
  snapshot: DashboardSnapshotV2,
  drawer: TraceDrawer,
  onSelect: (selection: ChainLegSelection) => void,
): HTMLElement {
  const region = document.createElement("div");
  region.className = "options-chain-viewport";
  region.tabIndex = 0;
  region.setAttribute("role", "region");
  region.setAttribute("aria-label", "Option chain calls left, strike center, puts right");
  const table = document.createElement("table");
  table.append(
    textElement("caption", "Calls left · strike center · Puts right"),
    chainHead(),
    chainBody(chain, snapshot, drawer, onSelect),
  );
  region.append(table);
  return region;
}

function chainHead(): HTMLTableSectionElement {
  const head = document.createElement("thead");
  const groups = document.createElement("tr");
  groups.append(groupHeading("Calls", 2), strikeHeading(), groupHeading("Puts", 2));
  const columns = document.createElement("tr");
  for (const label of ["Quote", "Leg", "Quote", "Leg"]) {
    const heading = textElement("th", label);
    heading.setAttribute("scope", "col");
    columns.append(heading);
  }
  head.append(groups, columns);
  return head;
}

function groupHeading(label: string, span: number): HTMLTableCellElement {
  const heading = document.createElement("th");
  heading.textContent = label;
  heading.setAttribute("scope", "colgroup");
  heading.colSpan = span;
  return heading;
}

function strikeHeading(): HTMLTableCellElement {
  const heading = document.createElement("th");
  heading.textContent = "Strike";
  heading.setAttribute("scope", "col");
  heading.rowSpan = 2;
  return heading;
}

function chainBody(
  chain: Chain,
  snapshot: DashboardSnapshotV2,
  drawer: TraceDrawer,
  onSelect: (selection: ChainLegSelection) => void,
): HTMLTableSectionElement {
  const body = document.createElement("tbody");
  if (chain.rows.length === 0) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.textContent = chain.summary;
    cell.colSpan = 5;
    row.append(cell);
    body.append(row);
    return body;
  }
  for (const item of chain.rows) {
    const row = document.createElement("tr");
    row.append(
      quoteCell(item.call, snapshot, drawer),
      legCell(item.call, item.strike, onSelect),
      rowHeading(item.strike),
      quoteCell(item.put, snapshot, drawer),
      legCell(item.put, item.strike, onSelect),
    );
    body.append(row);
  }
  return body;
}

function quoteCell(
  cell: ChainCell | null,
  snapshot: DashboardSnapshotV2,
  drawer: TraceDrawer,
): HTMLTableCellElement {
  const tableCell = document.createElement("td");
  if (cell === null) {
    tableCell.textContent = "Unavailable";
    return tableCell;
  }
  tableCell.append(
    textElement("span", `${cell.provider} · ${cell.state}`, "state-badge state-neutral"),
    textElement("span", ` ${cell.bid ?? "—"} / ${cell.ask ?? "—"}`),
    traceButton(cell.contract_id, cell.trace_id, snapshot, drawer),
  );
  return tableCell;
}

function legCell(
  cell: ChainCell | null,
  strike: string,
  onSelect: (selection: ChainLegSelection) => void,
): HTMLTableCellElement {
  const tableCell = document.createElement("td");
  if (cell === null) return tableCell;
  const selected = selectableResearchLeg(cell);
  if (selected.kind === "blocked") {
    tableCell.textContent = cell.state;
    return tableCell;
  }
  const button = buttonElement("Add leg", "quiet-button");
  button.setAttribute("aria-label", `Select ${strike} ${cell.side} leg`);
  button.addEventListener("click", () =>
    onSelect({
      contractId: selected.contractId,
      side: selected.side,
      strike: Number(strike),
      premium: selected.premium,
      traceId: cell.trace_id,
    }),
  );
  tableCell.append(button);
  return tableCell;
}

function rowHeading(strike: string): HTMLTableCellElement {
  const heading = document.createElement("th");
  heading.textContent = strike;
  heading.setAttribute("scope", "row");
  return heading;
}

function traceButton(
  label: string,
  traceId: string,
  snapshot: DashboardSnapshotV2,
  drawer: TraceDrawer,
): HTMLButtonElement {
  const button = buttonElement("Trace", "trace-button");
  button.dataset["traceId"] = traceId;
  button.setAttribute("aria-label", `${label} Evidence Trace 열기`);
  button.addEventListener("click", () => {
    drawer.open(
      label,
      resolveEvidenceTrace(traceId, snapshot.traces.nodes, snapshot.traces.edges),
      button,
    );
  });
  return button;
}
