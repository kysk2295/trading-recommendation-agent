import { textElement } from "../dom";
import type { EvidenceTraceDrawer } from "../evidence_trace";
import type { OptionsWorkbench } from "../options_workbench_schema";
import type { DashboardSnapshotV2 } from "../schema_v2";
import { workbenchTraceButton } from "./options_workbench_trace";

type ChainCell = NonNullable<OptionsWorkbench["chain"]["rows"][number]["call"]>;
type TraceDrawer = Pick<EvidenceTraceDrawer, "open">;
type MarketMetric = Readonly<{ key: string; label: string; value: string }>;

export function renderOptionsWorkbenchMarket(
  workbench: OptionsWorkbench,
  snapshot: DashboardSnapshotV2,
  drawer: TraceDrawer,
): HTMLElement {
  const article = document.createElement("article");
  article.className = "options-market-pulse";
  article.append(
    marketHeading(workbench, snapshot, drawer),
    metricGrid(marketMetrics(workbench), "options-market-metrics"),
    truthSummary(workbench),
    marketLinks(),
  );
  return article;
}

function marketHeading(
  workbench: OptionsWorkbench,
  snapshot: DashboardSnapshotV2,
  drawer: TraceDrawer,
): HTMLElement {
  const heading = document.createElement("header");
  heading.className = "options-workbench-panel-heading";
  const copy = document.createElement("div");
  copy.append(textElement("h3", "Market Pulse"), textElement("p", workbench.market.summary));
  heading.append(
    copy,
    workbenchTraceButton("Market Pulse", workbench.market.trace_id, { snapshot, drawer }),
  );
  return heading;
}

function marketMetrics(workbench: OptionsWorkbench): readonly MarketMetric[] {
  const cells = chainCells(workbench);
  return [
    metric("spot", "Spot", spotValue(workbench)),
    metric(
      "selected-expiration",
      "Selected expiration",
      workbench.chain.selected_expiration ?? unavailable("no selected expiration"),
    ),
    metric(
      "projected-expirations",
      "Bounded expirations",
      String(workbench.chain.expirations.length),
    ),
    metric(
      "projected-strikes",
      "Projected strikes",
      `${workbench.chain.projected_count} / ${workbench.chain.total_count}`,
    ),
    metric("aggregate-volume", "Aggregate volume", aggregateInteger(cells, "volume")),
    metric(
      "aggregate-open-interest",
      "Aggregate open interest",
      aggregateInteger(cells, "open_interest"),
    ),
    metric("iv-summary", "IV summary", ivSummary(cells)),
    metric("skew-summary", "Put-call IV skew", skewSummary(cells)),
    metric("term-summary", "Term structure", unavailable("single-expiration rows only")),
    metric("completed-bar", "Latest completed bar", unavailable("not in workbench contract")),
    metric("futures-basis", "Futures basis", unavailable("not in workbench contract")),
  ];
}

function truthSummary(workbench: OptionsWorkbench): HTMLElement {
  const cells = chainCells(workbench);
  const sources = new Set(cells.map((cell) => `${cell.provider} · ${cell.state}`));
  const observed = cells
    .map((cell) => cell.observed_at)
    .filter((value) => value !== null)
    .sort()
    .at(-1);
  const metrics = [
    metric(
      "source-truth",
      "Source truth",
      sources.size > 0 ? [...sources].join(" · ") : unavailable("no quote rows"),
    ),
    metric("entitlement", "Entitlement", unavailable("explicit field absent")),
    metric(
      "freshness",
      "Freshness",
      observed === undefined
        ? unavailable("no observation")
        : `${workbench.market.state} · ${observed}`,
    ),
    metric("redistribution", "Redistribution", unavailable("explicit field absent")),
  ];
  const section = document.createElement("section");
  section.className = "options-market-truth";
  section.append(
    textElement("h4", "Authority and source truth"),
    metricGrid(metrics, "options-market-truth-grid"),
  );
  return section;
}

function metricGrid(metrics: readonly MarketMetric[], className: string): HTMLDListElement {
  const list = document.createElement("dl");
  list.className = className;
  for (const item of metrics) {
    const row = document.createElement("div");
    row.dataset["marketMetric"] = item.key;
    row.append(textElement("dt", item.label), textElement("dd", item.value));
    list.append(row);
  }
  return list;
}

function marketLinks(): HTMLElement {
  const navigation = document.createElement("nav");
  navigation.className = "options-market-links";
  navigation.setAttribute("aria-label", "Related market evidence workspaces");
  navigation.append(
    workspaceLink("#markets", "Open Markets"),
    workspaceLink("#data-sources", "Open Data Sources"),
  );
  return navigation;
}

function workspaceLink(href: string, label: string): HTMLAnchorElement {
  const link = document.createElement("a");
  link.href = href;
  link.textContent = label;
  return link;
}

function chainCells(workbench: OptionsWorkbench): readonly ChainCell[] {
  const cells: ChainCell[] = [];
  for (const row of workbench.chain.rows) {
    if (row.call !== null) cells.push(row.call);
    if (row.put !== null) cells.push(row.put);
  }
  return cells;
}

function spotValue(workbench: OptionsWorkbench): string {
  return workbench.scenario === null
    ? unavailable("scenario receipt absent")
    : `${workbench.scenario.spot} ${workbench.scenario.currency}`;
}

function aggregateInteger(cells: readonly ChainCell[], field: "volume" | "open_interest"): string {
  if (cells.length === 0 || cells.some((cell) => cell[field] === null)) {
    return unavailable("complete quote field absent");
  }
  return String(cells.reduce((total, cell) => total + (cell[field] ?? 0), 0));
}

function ivSummary(cells: readonly ChainCell[]): string {
  const average = decimalAverage(cells.map((cell) => cell.implied_volatility));
  return average === null ? unavailable("IV absent") : `${(average * 100).toFixed(2)}% mean`;
}

function skewSummary(cells: readonly ChainCell[]): string {
  const calls = decimalAverage(
    cells.filter((cell) => cell.side === "call").map((cell) => cell.implied_volatility),
  );
  const puts = decimalAverage(
    cells.filter((cell) => cell.side === "put").map((cell) => cell.implied_volatility),
  );
  return calls === null || puts === null
    ? unavailable("two-sided IV absent")
    : `${((puts - calls) * 100).toFixed(2)} pp put minus call`;
}

function decimalAverage(values: readonly (string | null)[]): number | null {
  const numeric: number[] = [];
  for (const value of values) {
    if (value === null) continue;
    const parsed = Number(value);
    if (Number.isFinite(parsed)) numeric.push(parsed);
  }
  if (numeric.length === 0) return null;
  return numeric.reduce((total, value) => total + value, 0) / numeric.length;
}

function metric(key: string, label: string, value: string): MarketMetric {
  return { key, label, value };
}

function unavailable(reason: string): string {
  return `Unavailable · ${reason}`;
}
