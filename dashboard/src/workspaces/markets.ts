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

export const renderMarkets: WorkspaceRenderer = (snapshot, drawer) => {
  const workspace = snapshot.workspaces.markets;
  const fragment = document.createDocumentFragment();
  fragment.append(renderSummary(workspace, snapshot, drawer));
  fragment.append(renderSessions(workspace.items, snapshot, drawer));
  fragment.append(renderContext(workspace.items, snapshot, drawer));
  return fragment;
};

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
  section.append(textElement("h2", "Authoritative calendar sessions"));
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
  section.append(textElement("h2", "Market context and quote guard"), guidance);
  for (const item of items.filter((value) => !value.item_id.endsWith(".session"))) {
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
