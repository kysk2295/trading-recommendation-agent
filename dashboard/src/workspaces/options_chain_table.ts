import { textElement } from "../dom";
import type { OptionsWorkbench } from "../options_workbench_schema";
import {
  type ChainLegSelection,
  type ChainTableContext,
  renderChainBody,
  renderChainHead,
} from "./options_chain_cells";
import type { WorkbenchTraceContext } from "./options_workbench_trace";

type Chain = OptionsWorkbench["chain"];
type ChainRow = Chain["rows"][number];
type StrikeWindow = 3 | 5 | 10 | "all";
type ChainProjection = Readonly<{
  rows: readonly ChainRow[];
  emptyMessage: string;
  expiration: string;
}>;

export type { ChainLegSelection } from "./options_chain_cells";

export function renderOptionsChainTable(
  chain: Chain,
  trace: WorkbenchTraceContext,
  onSelect: (selection: ChainLegSelection) => void,
): HTMLElement {
  const root = document.createElement("section");
  root.className = "options-chain-explorer";
  const expiration = expirationControl(chain);
  const strikeWindow = strikeWindowControl();
  const controls = document.createElement("div");
  controls.className = "options-chain-controls";
  controls.append(expiration.label, strikeWindow.label);
  const viewport = document.createElement("div");
  viewport.className = "options-chain-viewport";
  viewport.tabIndex = 0;
  viewport.setAttribute("role", "region");
  viewport.setAttribute("aria-label", "Option chain calls left, strike center, puts right");
  const context: ChainTableContext = { ...trace, onSelect };
  const render = (): void => {
    const selectedExpiration = expiration.select.value;
    const hasProjection = selectedExpiration === chain.selected_expiration;
    const rows = hasProjection ? boundedRows(chain.rows, strikeWindow.select.value) : [];
    const message = hasProjection
      ? chain.summary
      : `No projected rows for selected expiration ${selectedExpiration}.`;
    viewport.replaceChildren(
      chainTable({ rows, emptyMessage: message, expiration: selectedExpiration }, context),
    );
    viewport.scrollLeft = 0;
  };
  expiration.select.addEventListener("change", render);
  strikeWindow.select.addEventListener("change", render);
  render();
  root.append(controls, viewport);
  return root;
}

function chainTable(projection: ChainProjection, context: ChainTableContext): HTMLTableElement {
  const table = document.createElement("table");
  const caption = textElement(
    "caption",
    `${projection.expiration || "Unavailable expiration"} · Calls left · strike center · Puts right`,
  );
  table.append(
    caption,
    renderChainHead(),
    renderChainBody(projection.rows, projection.emptyMessage, context),
  );
  return table;
}

function expirationControl(
  chain: Chain,
): Readonly<{ label: HTMLLabelElement; select: HTMLSelectElement }> {
  const select = document.createElement("select");
  select.id = "option-chain-expiration";
  select.disabled = chain.expirations.length === 0;
  if (chain.expirations.length === 0) {
    select.append(optionElement("", "Unavailable"));
  } else {
    for (const expiration of chain.expirations) {
      select.append(optionElement(expiration, expiration));
    }
    select.value = chain.selected_expiration ?? chain.expirations[0] ?? "";
  }
  return { label: labeledControl("Expiration", select), select };
}

function strikeWindowControl(): Readonly<{
  label: HTMLLabelElement;
  select: HTMLSelectElement;
}> {
  const select = document.createElement("select");
  select.id = "option-chain-strike-window";
  select.append(
    optionElement("3", "3 strikes"),
    optionElement("5", "5 strikes"),
    optionElement("10", "10 strikes"),
    optionElement("all", "All projected strikes"),
  );
  select.value = "5";
  return { label: labeledControl("Strike window", select), select };
}

function labeledControl(labelText: string, select: HTMLSelectElement): HTMLLabelElement {
  const label = document.createElement("label");
  label.append(textElement("span", labelText), select);
  return label;
}

function optionElement(value: string, label: string): HTMLOptionElement {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label;
  return option;
}

function boundedRows(rows: readonly ChainRow[], rawWindow: string): readonly ChainRow[] {
  const window = parseStrikeWindow(rawWindow);
  if (window === "all" || rows.length <= window) return rows;
  const start = Math.floor((rows.length - window) / 2);
  return rows.slice(start, start + window);
}

function parseStrikeWindow(value: string): StrikeWindow {
  switch (value) {
    case "3":
      return 3;
    case "5":
      return 5;
    case "10":
      return 10;
    case "all":
      return value;
    default:
      throw new OptionChainControlError(value);
  }
}

class OptionChainControlError extends Error {
  override readonly name = "OptionChainControlError";

  constructor(readonly value: string) {
    super(`unexpected strike window: ${value}`);
  }
}
