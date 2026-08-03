import { textElement } from "../dom";
import { formatOperationalRatio, parseOperationalDecimal } from "../options_workbench_decimal";
import type { OptionsWorkbench } from "../options_workbench_schema";
import { breakEvenPoints, type StrategyLeg, scenarioSeries } from "./options_workbench_presenters";

export type ScenarioEntry = Readonly<{ contractId: string; leg: StrategyLeg }>;

export function renderPayoffResearch(
  workbench: OptionsWorkbench,
  entries: readonly ScenarioEntry[],
  spots: readonly number[],
  selectedSpot: number,
  onSpot: (spot: number) => void,
): readonly Node[] {
  const legs = entries.map((entry) => entry.leg);
  const points = scenarioSeries(legs, spots);
  if (points.some((point) => !Number.isFinite(point.payoff))) {
    return [
      textElement(
        "p",
        "Scenario unavailable · operational arithmetic exceeded the safe presentation boundary",
        "options-research-warning",
      ),
    ];
  }
  const figure = document.createElement("figure");
  figure.className = "options-payoff";
  figure.setAttribute("aria-label", "Strategy payoff visualization");
  figure.append(payoffChart(points), payoffTable(points, selectedSpot));
  const breakEvens = breakEvenPoints(legs);
  const breakEven = textElement(
    "p",
    `Break-even · ${breakEvens.length === 0 ? "none" : breakEvens.map((point) => point.toFixed(2)).join(", ")}`,
  );
  breakEven.dataset["breakEven"] = "true";
  const control = document.createElement("label");
  control.className = "options-payoff-control";
  control.append(document.createTextNode("Scenario spot"));
  const select = document.createElement("select");
  select.setAttribute("aria-label", "Scenario spot");
  for (const spot of spots) {
    const option = document.createElement("option");
    option.value = String(spot);
    option.textContent = spot.toFixed(2);
    option.selected = spot === selectedSpot;
    select.append(option);
  }
  select.addEventListener("change", () => {
    const parsed = parseOperationalDecimal(select.value);
    if (parsed.kind === "ready") onSpot(parsed.decimal.value);
  });
  control.append(select);
  return [
    control,
    breakEven,
    figure,
    strategyMetrics(workbench, entries, points),
    ...warnings(workbench, entries),
  ];
}

function payoffChart(points: readonly Readonly<{ spot: number; payoff: number }>[]): SVGSVGElement {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 600 180");
  svg.setAttribute("role", "img");
  svg.append(svgText("title", "Sampled expiration payoff"));
  svg.append(svgText("desc", "Payoff line across the bounded scenario spots."));
  const axis = document.createElementNS(svg.namespaceURI, "line");
  axis.setAttribute("x1", "20");
  axis.setAttribute("x2", "580");
  axis.setAttribute("y1", "90");
  axis.setAttribute("y2", "90");
  axis.setAttribute("class", "options-payoff-axis");
  const line = document.createElementNS(svg.namespaceURI, "polyline");
  line.setAttribute("points", chartPoints(points));
  line.setAttribute("class", "options-payoff-line");
  svg.append(axis, line);
  return svg;
}

function chartPoints(points: readonly Readonly<{ payoff: number }>[]): string {
  if (points.length === 0) return "";
  const payoffs = points.map((point) => point.payoff);
  const low = Math.min(...payoffs);
  const span = Math.max(Math.max(...payoffs) - low, 1);
  return points
    .map((point, index) => {
      const x = 20 + (560 * index) / Math.max(points.length - 1, 1);
      const y = 160 - (140 * (point.payoff - low)) / span;
      return `${x},${y}`;
    })
    .join(" ");
}

function payoffTable(
  points: readonly Readonly<{ spot: number; payoff: number }>[],
  selectedSpot: number,
): HTMLTableElement {
  const table = document.createElement("table");
  table.setAttribute("aria-label", "Payoff samples");
  table.append(textElement("caption", "Payoff samples · bounded scenario spots"));
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const label of ["Spot", "Payoff"]) headRow.append(textElement("th", label));
  head.append(headRow);
  const body = document.createElement("tbody");
  body.dataset["scenarioSeries"] = "true";
  for (const point of points) {
    const row = document.createElement("tr");
    if (point.spot === selectedSpot) row.dataset["highlightedSpot"] = String(point.spot);
    row.append(
      textElement("td", point.spot.toFixed(2)),
      textElement("td", point.payoff.toFixed(2)),
    );
    body.append(row);
  }
  table.append(head, body);
  return table;
}

function strategyMetrics(
  workbench: OptionsWorkbench,
  entries: readonly ScenarioEntry[],
  points: readonly Readonly<{ payoff: number }>[],
): HTMLElement {
  const list = document.createElement("dl");
  list.className = "options-strategy-metrics";
  const payoffs = points.map((point) => point.payoff);
  metric(list, "Sampled max gain", "sampled-max-gain", money(Math.max(...payoffs), workbench));
  metric(list, "Sampled max loss", "sampled-max-loss", money(Math.min(...payoffs), workbench));
  const greeks = netGreeks(workbench, entries);
  for (const key of ["delta", "gamma", "theta", "vega"] as const) {
    metric(list, `Net ${key}`, `net-${key}`, greeks === null ? unavailableGreeks() : greeks[key]);
  }
  metric(list, "Underlying", "underlying", workbench.chain.underlying ?? "Unavailable");
  metric(list, "Edit availability", "edit-availability", "Local research legs only");
  return list;
}

function netGreeks(
  workbench: OptionsWorkbench,
  entries: readonly ScenarioEntry[],
): Readonly<Record<"delta" | "gamma" | "theta" | "vega", string>> | null {
  const cells = workbench.chain.rows.flatMap((row) => [row.call, row.put]).filter(isChainCell);
  const total = { delta: 0n, gamma: 0n, theta: 0n, vega: 0n };
  for (const entry of entries) {
    const cell = cells.find((candidate) => candidate.contract_id === entry.contractId);
    if (
      cell === undefined ||
      [cell.delta, cell.gamma, cell.theta, cell.vega].some((value) => value === null)
    )
      return null;
    const sign = entry.leg.action === "long" ? 1 : -1;
    for (const key of ["delta", "gamma", "theta", "vega"] as const) {
      const parsed = parseOperationalDecimal(cell[key] ?? "");
      if (parsed.kind === "blocked") return null;
      const factor = BigInt(sign * entry.leg.quantity * entry.leg.multiplier);
      total[key] += factor * parsed.decimal.scaled;
    }
  }
  return {
    delta: formatOperationalRatio(total.delta, 1n, 4),
    gamma: formatOperationalRatio(total.gamma, 1n, 4),
    theta: formatOperationalRatio(total.theta, 1n, 4),
    vega: formatOperationalRatio(total.vega, 1n, 4),
  };
}

function isChainCell<T>(cell: T | null): cell is T {
  return cell !== null;
}

function warnings(
  workbench: OptionsWorkbench,
  entries: readonly ScenarioEntry[],
): readonly HTMLElement[] {
  const ids = new Set(entries.map((entry) => entry.contractId));
  const states = workbench.chain.rows
    .flatMap((row) => [row.call, row.put])
    .filter(isChainCell)
    .filter((cell) => ids.has(cell.contract_id))
    .map((cell) => cell.state);
  const result = [
    textElement("p", "RESEARCH ONLY · immutable snapshot input", "options-research-warning"),
  ];
  if (states.some((state) => state !== "current"))
    result.push(
      textElement("p", "Indicative quote inputs · not executable", "options-research-warning"),
    );
  if (workbench.chain.state !== "populated")
    result.push(
      textElement(
        "p",
        `Data quality unavailable · ${workbench.chain.blocker_code ?? workbench.chain.state}`,
        "options-research-warning",
      ),
    );
  return result;
}

function metric(list: HTMLElement, label: string, key: string, value: string): void {
  const row = document.createElement("div");
  row.dataset["strategyMetric"] = key;
  row.append(textElement("dt", label), textElement("dd", value));
  list.append(row);
}

function money(value: number, workbench: OptionsWorkbench): string {
  return `${value.toFixed(2)} ${workbench.scenario?.currency ?? "USD"}`;
}

function unavailableGreeks(): string {
  return "Unavailable · complete matching Greeks required";
}

function svgText(tag: "title" | "desc", value: string): SVGElement {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  element.textContent = value;
  return element;
}
