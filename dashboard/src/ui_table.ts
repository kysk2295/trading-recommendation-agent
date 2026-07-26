import { textElement } from "./dom";

export function tableHead(labels: readonly string[]): HTMLTableSectionElement {
  const head = document.createElement("thead");
  const row = document.createElement("tr");
  for (const label of labels) {
    const cell = textElement("th", label);
    cell.setAttribute("scope", "col");
    row.append(cell);
  }
  head.append(row);
  return head;
}

export function tableCell(value: string, className?: string): HTMLTableCellElement {
  const cell = document.createElement("td");
  cell.textContent = value;
  if (className !== undefined) cell.className = className;
  return cell;
}

export function elementCell(value: HTMLElement): HTMLTableCellElement {
  const cell = document.createElement("td");
  cell.append(value);
  return cell;
}
