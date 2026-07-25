import { requiredElement, textElement } from "./dom";
import { count, marketTime, price, priceText, shortTime, stateLabel, statusClass } from "./format";
import type { DashboardSnapshot } from "./schema";

export type EvidenceFilter = "all" | "active";
const knownSignalKeys = new Set<string>();

export function renderSnapshot(snapshot: DashboardSnapshot, filter: EvidenceFilter): void {
  renderMarkets(snapshot);
  renderForward(snapshot);
  renderAgents(snapshot);
  renderRecommendations(snapshot);
  renderSignals(snapshot, filter);
  renderResearch(snapshot);
}

function renderMarkets(snapshot: DashboardSnapshot): void {
  const stack = requiredElement("market-stack", HTMLDivElement);
  stack.replaceChildren(
    ...snapshot.markets.map((market) => {
      const row = document.createElement("article");
      row.className = "market-row";
      const identity = document.createElement("div");
      identity.append(
        textElement("strong", `${market.label} 시장`),
        textElement("span", stateLabel(market.state), `state-text ${statusClass(market.state)}`),
      );
      const time = document.createElement("time");
      time.dateTime = market.local_time;
      time.textContent = marketTime(market.local_time);
      row.append(identity, time);
      return row;
    }),
  );
}

function renderForward(snapshot: DashboardSnapshot): void {
  const forward = snapshot.forward;
  const eligibleLabel = forward.eligible ? "READY" : "BLOCKED";
  const quality = requiredElement("quality-value", HTMLElement);
  quality.textContent = eligibleLabel;
  quality.className = forward.eligible ? "state-ready" : "state-failed";
  requiredElement("session-date", HTMLElement).textContent = forward.session_date ?? "—";
  const status = requiredElement("forward-status", HTMLElement);
  status.textContent = forward.eligible ? "품질 통과" : "품질 차단";
  status.className = `status-word ${forward.eligible ? "state-ready" : "state-failed"}`;
  const metrics = [
    ["Watch", forward.watch_cycles],
    ["Ranking", forward.ranking_cycles],
    ["Failed", forward.failed_watch_cycles],
    ["Retries", forward.read_retries],
    ["Recommendations", forward.recommendations],
  ] as const;
  const strip = requiredElement("metric-strip", HTMLDListElement);
  strip.replaceChildren(
    ...metrics.map(([label, value]) => {
      const group = document.createElement("div");
      group.append(textElement("dt", label), textElement("dd", count(value)));
      return group;
    }),
  );
  const blockers = requiredElement("blocker-list", HTMLDivElement);
  blockers.replaceChildren(...forward.blockers.map((blocker) => textElement("p", blocker)));
}

function renderAgents(snapshot: DashboardSnapshot): void {
  const body = requiredElement("agent-rows", HTMLTableSectionElement);
  if (snapshot.agents.length === 0) {
    body.replaceChildren(emptyRow("등록된 에이전트가 없습니다.", 3));
    return;
  }
  body.replaceChildren(
    ...snapshot.agents.map((agent) => {
      const row = document.createElement("tr");
      row.append(
        cell(agent.label),
        cell(stateLabel(agent.state), `state-text ${statusClass(agent.state)}`),
        cell(agent.scheduled_label, "job-label"),
      );
      return row;
    }),
  );
}

function renderRecommendations(snapshot: DashboardSnapshot): void {
  const body = requiredElement("recommendation-rows", HTMLTableSectionElement);
  requiredElement("recommendation-count", HTMLElement).textContent =
    `${count(snapshot.recommendations.length)}건`;
  if (snapshot.recommendations.length === 0) {
    body.replaceChildren(emptyRow("이 세션에 게시된 추천이 없습니다.", 5));
    return;
  }
  body.replaceChildren(
    ...snapshot.recommendations.map((recommendation) => {
      const identity = document.createElement("div");
      identity.className = "symbol-cell";
      identity.append(
        textElement("strong", recommendation.symbol),
        textElement("span", recommendation.strategy),
      );
      const row = document.createElement("tr");
      const identityCell = document.createElement("td");
      identityCell.append(identity);
      row.append(
        identityCell,
        cell(price(recommendation.entry), "price-cell"),
        cell(price(recommendation.stop), "price-cell"),
        cell(
          `${price(recommendation.target_1r)} / ${price(recommendation.target_2r)}`,
          "price-cell",
        ),
        cell(stateLabel(recommendation.state), `state-text ${statusClass(recommendation.state)}`),
      );
      return row;
    }),
  );
}

function renderSignals(snapshot: DashboardSnapshot, filter: EvidenceFilter): void {
  const now = Date.now();
  const signals = snapshot.signals.filter(
    (signal) => filter === "all" || new Date(signal.valid_until).getTime() > now,
  );
  const stream = requiredElement("evidence-stream", HTMLDivElement);
  if (signals.length === 0) {
    stream.replaceChildren(textElement("div", "조건에 맞는 신호가 없습니다.", "empty-state"));
    return;
  }
  stream.replaceChildren(
    ...signals.map((signal) => {
      const signalKey = `${signal.symbol}:${signal.strategy}:${signal.observed_at}`;
      const article = document.createElement("article");
      article.className = "evidence-item";
      if (!knownSignalKeys.has(signalKey)) {
        article.classList.add("is-new");
      }
      const topline = document.createElement("div");
      topline.className = "evidence-topline";
      const time = document.createElement("time");
      time.dateTime = signal.observed_at;
      time.textContent = shortTime(signal.observed_at);
      topline.append(textElement("span", `${signal.symbol} · ${signal.side.toUpperCase()}`), time);
      const levels = document.createElement("dl");
      levels.className = "price-grid";
      levels.append(
        priceGroup("진입", priceText(signal.entry_price)),
        priceGroup("손절", priceText(signal.stop_price)),
        priceGroup("목표", priceText(signal.targets.at(-1) ?? "—")),
      );
      article.append(
        topline,
        textElement("h3", `${signal.symbol} ${signal.side.toUpperCase()}: ${signal.strategy}`),
        levels,
        textElement("p", signal.rationale),
        textElement(
          "small",
          signal.evidence_namespaces.length === 0
            ? "evidence · 없음"
            : `evidence · ${signal.evidence_namespaces.join(" · ")}`,
        ),
      );
      return article;
    }),
  );
  for (const signal of snapshot.signals) {
    knownSignalKeys.add(`${signal.symbol}:${signal.strategy}:${signal.observed_at}`);
  }
}

function renderResearch(snapshot: DashboardSnapshot): void {
  const research = snapshot.research;
  requiredElement("research-summary", HTMLElement).textContent = research.summary;
  const status = requiredElement("research-status", HTMLElement);
  status.textContent = stateLabel(research.status);
  status.className = statusClass(research.status);
  requiredElement("research-date", HTMLElement).textContent = research.session_date ?? "—";
}

function cell(value: string, className?: string): HTMLTableCellElement {
  const element = document.createElement("td");
  element.textContent = value;
  if (className !== undefined) {
    element.className = className;
  }
  return element;
}

function emptyRow(value: string, columns: number): HTMLTableRowElement {
  const row = document.createElement("tr");
  const item = cell(value, "empty-state");
  item.colSpan = columns;
  row.append(item);
  return row;
}

function priceGroup(label: string, value: string): HTMLDivElement {
  const group = document.createElement("div");
  group.append(textElement("dt", label), textElement("dd", value));
  return group;
}
