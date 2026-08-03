import { buttonElement, textElement } from "../dom";
import { formatOperationalRatio, parseOperationalDecimal } from "../options_workbench_decimal";
import type { OptionsWorkbench } from "../options_workbench_schema";
import { selectableResearchLeg } from "./options_workbench_presenters";
import { type WorkbenchTraceContext, workbenchTraceButton } from "./options_workbench_trace";

type Chain = OptionsWorkbench["chain"];
type ChainRow = Chain["rows"][number];
type ChainCell = NonNullable<ChainRow["call"]>;

export type ChainLegSelection = Readonly<{
  contractId: string;
  side: "call" | "put";
  strike: number;
  premium: number;
  traceId: string;
}>;

export type ChainTableContext = WorkbenchTraceContext &
  Readonly<{ onSelect: (selection: ChainLegSelection) => void }>;

const CELL_HEADINGS = [
  "Provider / state",
  "Bid / ask",
  "Mid / spread",
  "Last",
  "Volume / OI",
  "IV",
  "Delta / gamma",
  "Theta / vega",
  "Observed",
  "Evidence",
  "Research leg",
] as const;

export function renderChainHead(): HTMLTableSectionElement {
  const head = document.createElement("thead");
  const groups = document.createElement("tr");
  groups.append(groupHeading("Calls"), strikeHeading(), groupHeading("Puts"));
  const columns = document.createElement("tr");
  for (const label of [...CELL_HEADINGS, ...CELL_HEADINGS]) {
    const heading = textElement("th", label);
    heading.setAttribute("scope", "col");
    columns.append(heading);
  }
  head.append(groups, columns);
  return head;
}

export function renderChainBody(
  rows: readonly ChainRow[],
  emptyMessage: string,
  context: ChainTableContext,
): HTMLTableSectionElement {
  const body = document.createElement("tbody");
  if (rows.length === 0) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.textContent = emptyMessage;
    cell.colSpan = CELL_HEADINGS.length * 2 + 1;
    row.append(cell);
    body.append(row);
    return body;
  }
  for (const item of rows) {
    const row = document.createElement("tr");
    row.append(
      ...quoteCells(item.call, item.strike, context),
      rowHeading(item.strike),
      ...quoteCells(item.put, item.strike, context),
    );
    body.append(row);
  }
  return body;
}

function quoteCells(
  cell: ChainCell | null,
  strike: string,
  context: ChainTableContext,
): readonly HTMLTableCellElement[] {
  if (cell === null) {
    const unavailable = document.createElement("td");
    unavailable.textContent = "Unavailable";
    unavailable.colSpan = CELL_HEADINGS.length;
    return [unavailable];
  }
  return [
    valueCell(`${cell.provider} · ${cell.state}`, "state-badge state-neutral"),
    valueCell(pair(cell.bid, cell.ask)),
    valueCell(operationalMidSpread(cell.bid, cell.ask)),
    valueCell(cell.last),
    valueCell(pair(integerText(cell.volume), integerText(cell.open_interest))),
    valueCell(cell.implied_volatility),
    valueCell(pair(cell.delta, cell.gamma)),
    valueCell(pair(cell.theta, cell.vega)),
    observedCell(cell.observed_at),
    controlCell(workbenchTraceButton(cell.contract_id, cell.trace_id, context)),
    legCell(cell, strike, context.onSelect),
  ];
}

function groupHeading(label: string): HTMLTableCellElement {
  const heading = document.createElement("th");
  heading.textContent = label;
  heading.setAttribute("scope", "colgroup");
  heading.colSpan = CELL_HEADINGS.length;
  return heading;
}

function strikeHeading(): HTMLTableCellElement {
  const heading = document.createElement("th");
  heading.textContent = "Strike";
  heading.setAttribute("scope", "col");
  heading.rowSpan = 2;
  return heading;
}

function rowHeading(strike: string): HTMLTableCellElement {
  const heading = document.createElement("th");
  heading.textContent = strike;
  heading.setAttribute("scope", "row");
  return heading;
}

function valueCell(value: string | null, className = ""): HTMLTableCellElement {
  const cell = document.createElement("td");
  cell.textContent = value ?? "Unavailable";
  cell.className = className;
  return cell;
}

function controlCell(control: HTMLElement): HTMLTableCellElement {
  const cell = document.createElement("td");
  cell.append(control);
  return cell;
}

function observedCell(value: string | null): HTMLTableCellElement {
  if (value === null) return valueCell(null);
  const cell = document.createElement("td");
  const time = document.createElement("time");
  time.textContent = value.replace("T", " ");
  time.dateTime = value;
  cell.append(time);
  return cell;
}

function legCell(
  cell: ChainCell,
  strike: string,
  onSelect: ChainTableContext["onSelect"],
): HTMLTableCellElement {
  const tableCell = document.createElement("td");
  const selected = selectableResearchLeg(cell);
  const parsedStrike = parseOperationalDecimal(strike);
  if (selected.kind === "blocked" || parsedStrike.kind === "blocked") {
    tableCell.textContent = cell.state;
    return tableCell;
  }
  const button = buttonElement("Add leg", "quiet-button");
  button.setAttribute("aria-label", `Select ${strike} ${cell.side} leg`);
  button.addEventListener("click", () =>
    onSelect({
      contractId: selected.contractId,
      side: selected.side,
      strike: parsedStrike.decimal.value,
      premium: selected.premium,
      traceId: cell.trace_id,
    }),
  );
  tableCell.append(button);
  return tableCell;
}

function pair(left: string | null, right: string | null): string {
  return `${left ?? "Unavailable"} / ${right ?? "Unavailable"}`;
}

function integerText(value: number | null): string | null {
  return value === null ? null : String(value);
}

export function operationalMidSpread(bid: string | null, ask: string | null): string {
  if (bid === null || ask === null) return "Unavailable / Unavailable";
  const parsedBid = parseOperationalDecimal(bid);
  const parsedAsk = parseOperationalDecimal(ask);
  if (parsedBid.kind === "blocked" || parsedAsk.kind === "blocked")
    return "Unavailable / Unavailable";
  const bidScaled = parsedBid.decimal.scaled;
  const askScaled = parsedAsk.decimal.scaled;
  return `${formatOperationalRatio(bidScaled + askScaled, 2n, 2)} / ${formatOperationalRatio(askScaled - bidScaled, 1n, 2)}`;
}
