import { renderCompactChart } from "./compact_chart";
import { buttonElement, textElement, timeElement } from "./dom";
import type { EvidenceTraceDrawer } from "./evidence_trace";
import { resolveEvidenceTrace } from "./evidence_trace";
import type { DashboardSnapshotV2 } from "./schema_v2";
import { elementCell, tableCell, tableHead } from "./ui_table";
import type { WorkspaceDefinition, WorkspaceKey } from "./workspace_registry";

export type SourceStateName = DashboardSnapshotV2["workspaces"]["overview"]["state"] | "loading";
type SourceTone = "neutral" | "success" | "warning" | "error";

export type StatePresentation = {
  readonly label: string;
  readonly tone: SourceTone;
  readonly guidance: string;
};

type WorkspaceProjection = DashboardSnapshotV2["workspaces"][WorkspaceKey];
export type WorkspaceItem = WorkspaceProjection["items"][number];

export function renderWorkspace(
  definition: WorkspaceDefinition,
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
): DocumentFragment {
  const workspace = snapshot.workspaces[definition.key];
  const fragment = document.createDocumentFragment();
  fragment.append(renderSourcePanel(workspace, snapshot, drawer));
  if (workspace.items.length > 0) {
    if (definition.id === "markets" || definition.id === "derivatives") {
      const chart = renderCompactChart(workspace.items);
      if (chart !== null) fragment.append(chart);
    }
    fragment.append(renderItemTable(workspace.items, snapshot, drawer));
  }
  if (definition.key === "data_sources") {
    fragment.append(
      renderCapabilities(snapshot.workspaces.data_sources.capabilities, snapshot, drawer),
    );
  }
  return fragment;
}

export function sourceStatePresentation(state: SourceStateName): StatePresentation {
  switch (state) {
    case "loading":
      return { label: "불러오는 중", tone: "neutral", guidance: "권위 있는 읽기 결과 대기" };
    case "empty":
      return { label: "비어 있음", tone: "neutral", guidance: "읽기 성공 · 0 records" };
    case "error":
      return { label: "읽기 오류", tone: "error", guidance: "새 이벤트에서 다시 평가" };
    case "blocked":
      return { label: "차단됨", tone: "error", guidance: "명시된 gate가 사용을 차단" };
    case "unavailable":
      return { label: "사용 불가", tone: "neutral", guidance: "권위 또는 receipt 없음" };
    case "corrupt":
      return { label: "무결성 실패", tone: "error", guidance: "검증 실패 · fail closed" };
    case "stale":
      return { label: "기한 경과", tone: "warning", guidance: "신선도 기준 초과" };
    case "populated":
      return { label: "검증됨", tone: "success", guidance: "읽기·schema·신선도 통과" };
    default:
      return assertNever(state);
  }
}

function renderSourcePanel(
  workspace: WorkspaceProjection,
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
): HTMLElement {
  const presentation = sourceStatePresentation(workspace.state);
  const panel = document.createElement("section");
  panel.className = `source-state-panel state-${presentation.tone}`;
  panel.dataset["sourceState"] = workspace.state;
  if (workspace.state === "loading") panel.setAttribute("aria-busy", "true");
  const heading = document.createElement("div");
  heading.className = "state-panel-heading";
  heading.append(
    textElement("span", presentation.label, `state-badge state-${presentation.tone}`),
    traceButton(workspace.summary, workspace.trace_id, snapshot, drawer),
  );
  panel.append(
    heading,
    textElement("h2", workspace.summary),
    textElement("p", presentation.guidance, "state-guidance"),
  );
  if (workspace.blocker_code !== null) {
    panel.append(renderBlocker(workspace.blocker_code));
  }
  const metadata = document.createElement("dl");
  metadata.className = "source-metadata";
  metadata.append(
    metadataGroup("관측", timeElement(workspace.observed_at)),
    metadataGroup("신선도", textElement("span", freshnessText(workspace.freshness.age_seconds))),
    metadataGroup("표시", textElement("span", countText(workspace))),
  );
  panel.append(metadata);
  return panel;
}

function renderItemTable(
  items: readonly WorkspaceItem[],
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
): HTMLElement {
  const viewport = document.createElement("div");
  viewport.className = "table-viewport";
  viewport.tabIndex = 0;
  viewport.setAttribute("role", "region");
  viewport.setAttribute("aria-label", "권위 있는 workspace 항목 표");
  const table = document.createElement("table");
  table.append(
    tableHead(["항목", "값", "상태", "관측", "Evidence Trace"]),
    tableBody(items, snapshot, drawer),
  );
  const caption = textElement("caption", "권위 있는 workspace 항목과 Evidence Trace");
  table.prepend(caption);
  viewport.append(table);
  return viewport;
}

function tableBody(
  items: readonly WorkspaceItem[],
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
): HTMLTableSectionElement {
  const body = document.createElement("tbody");
  body.append(
    ...items.map((item) => {
      const presentation = sourceStatePresentation(item.state);
      const row = document.createElement("tr");
      row.append(
        tableCell(item.label),
        tableCell(item.value ?? "값 없음", "value-cell"),
        tableCell(presentation.label, `state-${presentation.tone}`),
        elementCell(timeElement(item.observed_at)),
        elementCell(traceButton(item.label, item.trace_id, snapshot, drawer)),
      );
      return row;
    }),
  );
  return body;
}

function renderCapabilities(
  capabilities: DashboardSnapshotV2["workspaces"]["data_sources"]["capabilities"],
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
): HTMLElement {
  return renderBoundedRows(
    "공급자 capability",
    capabilities.map((capability) => ({
      label: capability.label,
      detail: `${capability.provider} · ${capability.entitlement} · ${capability.state}`,
      traceId: capability.trace_id,
    })),
    snapshot,
    drawer,
  );
}

function renderBoundedRows(
  title: string,
  rows: readonly { readonly label: string; readonly detail: string; readonly traceId: string }[],
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
): HTMLElement {
  const section = document.createElement("section");
  section.className = "bounded-list";
  section.append(textElement("h2", title));
  if (rows.length === 0) {
    section.append(textElement("p", "권위 있는 항목이 없습니다.", "empty-state"));
    return section;
  }
  for (const row of rows) {
    const article = document.createElement("article");
    article.append(
      textElement("strong", row.label),
      textElement("p", row.detail),
      traceButton(row.label, row.traceId, snapshot, drawer),
    );
    section.append(article);
  }
  return section;
}

function traceButton(
  label: string,
  traceId: string,
  snapshot: DashboardSnapshotV2,
  drawer: EvidenceTraceDrawer,
): HTMLButtonElement {
  const button = buttonElement("Trace", "trace-button");
  button.setAttribute("aria-label", `${label} Evidence Trace 열기`);
  button.dataset["traceId"] = traceId;
  button.addEventListener("click", () => {
    drawer.open(
      label,
      resolveEvidenceTrace(traceId, snapshot.traces.nodes, snapshot.traces.edges),
      button,
    );
  });
  return button;
}

function renderBlocker(code: string): HTMLElement {
  const blocker = document.createElement("div");
  blocker.className = "blocker-notice";
  blocker.append(textElement("strong", "사용 차단"), textElement("code", code));
  return blocker;
}

function metadataGroup(label: string, value: HTMLElement): HTMLDivElement {
  const group = document.createElement("div");
  const description = document.createElement("dd");
  description.append(value);
  group.append(textElement("dt", label), description);
  return group;
}

function freshnessText(ageSeconds: number | null): string {
  return ageSeconds === null ? "나이 계산 불가" : `${ageSeconds.toLocaleString("ko-KR")}초`;
}

function countText(value: {
  readonly projected_count: number;
  readonly total_count: number;
  readonly truncated: boolean;
}): string {
  const suffix = value.truncated ? " · 일부 표시" : "";
  return `${value.projected_count}/${value.total_count}${suffix}`;
}

function assertNever(value: never): never {
  throw new RenderStateError(`unknown source state: ${String(value)}`);
}

class RenderStateError extends Error {
  override readonly name = "RenderStateError";
}
