import { buttonElement, textElement, timeElement } from "../dom";
import type { EvidenceTraceDrawer } from "../evidence_trace";
import { resolveEvidenceTrace } from "../evidence_trace";
import { sourceStatePresentation } from "../render";
import type { DashboardSnapshotV2 } from "../schema_v2";
import type { WorkspaceRenderer } from "./types";

type MarketItem = DashboardSnapshotV2["workspaces"]["markets"]["items"][number];

export const renderMarkets: WorkspaceRenderer = (snapshot, drawer) => {
  const workspace = snapshot.workspaces.markets;
  const fragment = document.createDocumentFragment();
  fragment.append(renderSummary(workspace, snapshot, drawer));
  fragment.append(renderSessions(workspace.items, snapshot, drawer));
  fragment.append(renderContext(workspace.items, snapshot, drawer));
  return fragment;
};

export function marketFactValue(item: {
  readonly item_id: string;
  readonly value: string | null;
}): string {
  if (item.item_id.endsWith(".session")) return item.value ?? "사용 불가 · session receipt 없음";
  return "사용 불가 · currentness, entitlement, redistribution proof가 snapshot에 없습니다";
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
  section.append(
    renderHeading(presentation.label, workspace.summary, workspace.trace_id, snapshot, drawer),
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
  const presentation = sourceStatePresentation(item.state);
  const article = document.createElement("article");
  article.className = "market-session-card";
  article.append(
    textElement("p", presentation.label, `state-badge state-${presentation.tone}`),
    textElement("h3", item.label),
    textElement("strong", marketFactValue(item), "market-session-value"),
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
  section.append(textElement("h2", "Market context and quote guard"));
  const otherItems = items.filter((item) => !item.item_id.endsWith(".session"));
  section.append(
    textElement(
      "p",
      "현재 quote는 entitlement, currentness, redistribution permit이 함께 있는 canonical snapshot에서만 표시합니다. 이 v2 projection은 calendar/session evidence만 게시합니다.",
      "state-guidance",
    ),
  );
  for (const item of otherItems) {
    const row = document.createElement("div");
    row.className = "market-withheld-row";
    row.append(
      textElement("strong", item.label),
      textElement("span", marketFactValue(item)),
      traceButton(item.label, item.trace_id, snapshot, drawer),
    );
    section.append(row);
  }
  return section;
}

function renderHeading(
  state: string,
  summary: string,
  traceId: string,
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
): HTMLElement {
  const heading = document.createElement("div");
  heading.className = "state-panel-heading";
  heading.append(textElement("h2", summary), traceButton(state, traceId, snapshot, drawer));
  return heading;
}

function renderMetadata(
  observedAt: string | null,
  ageSeconds: number | null,
  blockerCode: string | null,
): HTMLElement {
  const metadata = document.createElement("dl");
  metadata.className = "market-metadata";
  metadata.append(
    metadataValue(
      "관측",
      observedAt === null ? textElement("span", "없음") : timeElement(observedAt),
    ),
    metadataValue(
      "신선도",
      textElement(
        "span",
        ageSeconds === null ? "계산 불가" : `${ageSeconds.toLocaleString("ko-KR")}초`,
      ),
    ),
    metadataValue("Blocker", textElement("code", blockerCode ?? "없음")),
  );
  return metadata;
}

function metadataValue(label: string, value: HTMLElement): HTMLElement {
  const group = document.createElement("div");
  group.append(textElement("dt", label), value);
  return group;
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
