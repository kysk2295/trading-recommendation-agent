import { buttonElement, textElement, timeElement } from "../dom";
import type { EvidenceTraceDrawer } from "../evidence_trace";
import { resolveEvidenceTrace } from "../evidence_trace";
import { sourceStatePresentation } from "../render";
import type { DashboardSnapshotV2 } from "../schema_v2";
import type { WorkspaceRenderer } from "./types";

type MarketItem = DashboardSnapshotV2["workspaces"]["markets"]["items"][number];
type SourceState = MarketItem["state"];
const CALENDAR_SESSIONS = [
  { itemId: "market.kr.session", label: "KR session", values: ["scheduled", "closed"] },
  { itemId: "market.us.session", label: "US session", values: ["open", "closed"] },
] as const;

export type MarketEvidencePath = {
  readonly status: "resolved" | "unavailable" | "corrupt";
  readonly startsAtSource: boolean;
  readonly nodes: readonly {
    readonly node_id: string;
    readonly kind: string;
    readonly state: string;
    readonly source_namespace: string;
  }[];
};

export type MarketEvidencePresentation = { readonly state: SourceState; readonly value: string };
export type KrRealtimeCyclePresentation = {
  readonly records: number;
  readonly successfulSources: number;
  readonly totalSources: number;
  readonly cycleId: string;
};

export const renderMarkets: WorkspaceRenderer = (snapshot, drawer) => {
  const workspace = snapshot.workspaces.markets;
  const fragment = document.createDocumentFragment();
  fragment.append(renderSummary(workspace, snapshot, drawer));
  const dayAgent = renderDayAgentMarkets(workspace.items, snapshot, drawer);
  if (dayAgent !== null) fragment.append(dayAgent);
  const realtime = renderRealtimeCycle(workspace.items, snapshot, drawer);
  if (realtime !== null) fragment.append(realtime);
  fragment.append(renderSessions(workspace.items, snapshot, drawer));
  fragment.append(renderContext(workspace.items, snapshot, drawer));
  return fragment;
};

export function krRealtimeCyclePresentation(
  value: string | null,
): KrRealtimeCyclePresentation | null {
  if (value === null) return null;
  const match = /^records=(\d+);coverage=(\d+)\/(\d+);cycle=([a-zA-Z0-9._:-]{1,120})$/.exec(value);
  if (match === null) return null;
  const [, rawRecords, rawSuccessful, rawTotal, cycleId] = match;
  const records = Number(rawRecords);
  const successfulSources = Number(rawSuccessful);
  const totalSources = Number(rawTotal);
  if (
    cycleId === undefined ||
    !Number.isSafeInteger(records) ||
    !Number.isSafeInteger(successfulSources) ||
    !Number.isSafeInteger(totalSources) ||
    totalSources < 1 ||
    totalSources > 12 ||
    successfulSources < 0 ||
    successfulSources > totalSources
  ) {
    return null;
  }
  return { records, successfulSources, totalSources, cycleId };
}

function renderDayAgentMarkets(
  items: readonly MarketItem[],
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
): HTMLElement | null {
  const lanes = items.filter((item) => item.item_id.startsWith("day_agent."));
  if (lanes.length === 0) return null;
  const section = document.createElement("section");
  section.className = "day-agent-lanes";
  section.append(
    textElement("h2", "Day Agent · 독립 관측 슬라이스"),
    textElement(
      "p",
      "Paper와 Shadow는 서로 합산하지 않으며, 표시된 결과는 미래 수익성을 뜻하지 않습니다.",
      "state-guidance",
    ),
  );
  const laneGroups = [
    (item: MarketItem) => item.item_id === "day_agent.us.paper",
    (item: MarketItem) =>
      item.item_id.startsWith("day_agent.us.") && item.item_id !== "day_agent.us.paper",
    (item: MarketItem) => item.item_id.startsWith("day_agent.kr."),
  ] as const;
  for (const belongsToLane of laneGroups) {
    const laneItems = lanes.filter(belongsToLane);
    if (laneItems.length === 0) continue;
    const lane = document.createElement("article");
    lane.className = "day-agent-lane";
    for (const item of laneItems) {
      const presentation = sourceStatePresentation(item.state);
      const row = document.createElement("div");
      row.className = "day-agent-row";
      row.dataset["sourceState"] = item.state;
      row.append(
        textElement("p", presentation.label, `state-badge state-${presentation.tone}`),
        textElement("h3", item.label),
        textElement("p", item.value ?? "Unavailable", "day-agent-value"),
        traceButton(item.label, item.trace_id, snapshot, drawer),
      );
      lane.append(row);
    }
    section.append(lane);
  }
  return section;
}

export function marketEvidencePresentation(
  item: Pick<MarketItem, "item_id" | "label" | "state" | "value" | "observed_at" | "trace_id">,
  trace: MarketEvidencePath,
): MarketEvidencePresentation {
  const contract = CALENDAR_SESSIONS.find((candidate) => candidate.itemId === item.item_id);
  const hasValidValue = contract?.values.some((value) => value === item.value) ?? false;
  const source = trace.nodes.find((node) => node.node_id === item.trace_id);
  const authoritative =
    contract?.label === item.label &&
    hasValidValue &&
    item.observed_at !== null &&
    (item.state === "populated" || item.state === "stale") &&
    trace.status === "resolved" &&
    trace.startsAtSource &&
    source?.kind === "source_receipt" &&
    source.state === "accepted" &&
    source.source_namespace === "market_calendar.markets";
  return authoritative
    ? { state: item.state, value: item.value ?? "사용 불가 · session value 없음" }
    : { state: "unavailable", value: "사용 불가 · authoritative calendar/session evidence 없음" };
}

function renderSummary(
  workspace: DashboardSnapshotV2["workspaces"]["markets"],
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
): HTMLElement {
  const presentation = sourceStatePresentation(workspace.state);
  const section = document.createElement("section");
  section.className = `source-state-panel market-summary state-${presentation.tone}`;
  section.dataset["sourceState"] = workspace.state;
  const heading = document.createElement("div");
  heading.className = "state-panel-heading";
  heading.append(
    textElement("h2", workspace.summary),
    traceButton(presentation.label, workspace.trace_id, snapshot, drawer),
  );
  section.append(
    heading,
    textElement("p", presentation.guidance, "state-guidance"),
    renderMetadata(workspace.observed_at, workspace.freshness.age_seconds, workspace.blocker_code),
  );
  return section;
}

function renderSessions(
  items: readonly MarketItem[],
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
): HTMLElement {
  const section = document.createElement("section");
  section.className = "market-session-section";
  section.append(textElement("h2", "정규장 상태"));
  const sessions = items.filter((item) => item.item_id.endsWith(".session"));
  if (sessions.length === 0) {
    section.append(
      textElement("p", "권위 있는 KR/US session receipt가 게시되지 않았습니다.", "empty-state"),
    );
    return section;
  }
  const grid = document.createElement("div");
  grid.className = "market-session-grid";
  for (const item of sessions) grid.append(renderSession(item, snapshot, drawer));
  section.append(grid);
  return section;
}

function renderRealtimeCycle(
  items: readonly MarketItem[],
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
): HTMLElement | null {
  const item = items.find((candidate) => candidate.item_id === "market.kr.realtime_cycle");
  if (item === undefined) return null;
  const presentation = sourceStatePresentation(item.state);
  const cycle = krRealtimeCyclePresentation(item.value);
  const section = document.createElement("section");
  section.className = "market-live-cycle";
  const heading = document.createElement("header");
  heading.append(
    textElement("div", "KR REGULAR SESSION", "meta-label"),
    textElement("span", presentation.label, `state-badge state-${presentation.tone}`),
    traceButton(item.label, item.trace_id, snapshot, drawer),
  );
  section.append(heading);
  if (cycle === null) {
    section.append(
      textElement("h2", "실시간 수집 cycle 권위 없음"),
      textElement("p", "완료된 source receipt가 게시되기 전에는 감지 수치를 만들지 않습니다."),
    );
    return section;
  }
  const metrics = document.createElement("div");
  metrics.className = "market-live-metrics";
  metrics.append(
    metric("감지 records", cycle.records.toLocaleString("ko-KR")),
    metric("source coverage", `${cycle.successfulSources}/${cycle.totalSources}`),
    metric(
      "관측",
      item.observed_at === null
        ? "없음"
        : new Intl.DateTimeFormat("ko-KR", {
            month: "numeric",
            day: "numeric",
            hour: "2-digit",
            minute: "2-digit",
          }).format(new Date(item.observed_at)),
    ),
  );
  const meter = document.createElement("div");
  meter.className = "market-live-meter";
  meter.setAttribute(
    "aria-label",
    `${cycle.totalSources}개 source 중 ${cycle.successfulSources}개 수집 성공`,
  );
  meter.setAttribute("role", "img");
  for (let index = 0; index < cycle.totalSources; index += 1) {
    const segment = document.createElement("span");
    if (index < cycle.successfulSources) segment.className = "is-covered";
    meter.append(segment);
  }
  section.append(
    textElement("h2", item.label),
    metrics,
    meter,
    textElement("code", cycle.cycleId, "market-cycle-id"),
  );
  return section;
}

function metric(label: string, value: string): HTMLElement {
  const group = document.createElement("div");
  group.append(textElement("span", label), textElement("strong", value));
  return group;
}

function renderSession(
  item: MarketItem,
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
): HTMLElement {
  const display = marketEvidencePresentation(
    item,
    resolveEvidenceTrace(item.trace_id, snapshot.traces.nodes, snapshot.traces.edges),
  );
  const presentation = sourceStatePresentation(display.state);
  const article = document.createElement("article");
  article.className = "market-session-card";
  article.append(
    textElement("p", presentation.label, `state-badge state-${presentation.tone}`),
    textElement("h3", item.label),
    textElement("strong", display.value, "market-session-value"),
    item.observed_at === null
      ? textElement("p", "관측 시각 없음", "market-observed")
      : timeElement(item.observed_at),
    traceButton(item.label, item.trace_id, snapshot, drawer),
  );
  return article;
}

function renderContext(
  items: readonly MarketItem[],
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
): HTMLElement {
  const section = document.createElement("section");
  section.className = "market-context-section";
  const guidance = document.createElement("p");
  guidance.className = "state-guidance";
  guidance.append(
    "현재 quote는 entitlement, currentness, redistribution permit이 함께 있는 canonical snapshot에서만 표시합니다. ",
    textElement("span", "이\u00a0v2", "market-projection-label"),
    " projection은 calendar/session evidence만 게시합니다.",
  );
  section.append(textElement("h2", "시장 데이터 권위"), guidance);
  for (const item of items.filter(
    (value) =>
      !value.item_id.endsWith(".session") &&
      value.item_id !== "market.kr.realtime_cycle" &&
      !value.item_id.startsWith("day_agent."),
  )) {
    const row = document.createElement("div");
    row.className = "market-withheld-row";
    row.append(
      textElement("strong", item.label),
      textElement("span", "사용 불가 · current quote authority 없음"),
      traceButton(item.label, item.trace_id, snapshot, drawer),
    );
    section.append(row);
  }
  return section;
}

function renderMetadata(
  observedAt: string | null,
  ageSeconds: number | null,
  blockerCode: string | null,
): HTMLElement {
  const metadata = document.createElement("dl");
  metadata.className = "market-metadata";
  for (const [label, value] of [
    ["관측", observedAt === null ? textElement("span", "없음") : timeElement(observedAt)],
    [
      "신선도",
      textElement(
        "span",
        ageSeconds === null ? "계산 불가" : `${ageSeconds.toLocaleString("ko-KR")}초`,
      ),
    ],
    ["Blocker", textElement("code", blockerCode ?? "없음")],
  ] as const) {
    const group = document.createElement("div");
    const description = document.createElement("dd");
    description.append(value);
    group.append(textElement("dt", label), description);
    metadata.append(group);
  }
  return metadata;
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
