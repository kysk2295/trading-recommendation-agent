import { buttonElement, textElement } from "../dom";
import type { EvidenceTraceDrawer } from "../evidence_trace";
import { resolveEvidenceTrace } from "../evidence_trace";
import type { OptionsWorkbench } from "../options_workbench_schema";
import type { DashboardSnapshotV2 } from "../schema_v2";
import { renderOptionsChainTable } from "./options_chain_table";
import {
  createScenarioPresentations,
  renderProviderStates,
  renderWorkbenchHeader,
} from "./options_workbench_panels";
import { workbenchStatePresentation } from "./options_workbench_presenters";

export const OPTIONS_WORKBENCH_VIEWS = [
  "market_pulse",
  "option_chain",
  "strategy_agent",
  "experiment_lab",
  "promotion_operations",
] as const;

type ViewId = (typeof OPTIONS_WORKBENCH_VIEWS)[number];
type TraceDrawer = Pick<EvidenceTraceDrawer, "open">;
type WorkbenchSection = OptionsWorkbench["market"];

const VIEW_LABELS: Readonly<Record<ViewId, string>> = {
  market_pulse: "Market Pulse",
  option_chain: "Option Chain",
  strategy_agent: "Strategy & Agent",
  experiment_lab: "Experiment Lab",
  promotion_operations: "Promotion & Operations",
};

export function renderOptionsWorkbench(
  snapshot: DashboardSnapshotV2,
  drawer: TraceDrawer,
): HTMLElement {
  const workbench = snapshot.workspaces.derivatives.workbench;
  const section = document.createElement("section");
  section.className = "options-workbench";
  section.append(renderWorkbenchHeader());
  const tabs = document.createElement("div");
  tabs.className = "options-workbench-tabs";
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", "Options research views");
  const panels = document.createElement("div");
  panels.className = "options-workbench-panels";
  const scenarios = createScenarioPresentations(workbench);
  const panelByView = buildPanels(workbench, snapshot, drawer, scenarios.agent);
  const tabByView = new Map<ViewId, HTMLButtonElement>();
  for (const view of OPTIONS_WORKBENCH_VIEWS) {
    const tab = workbenchTab(view);
    tabByView.set(view, tab);
    tabs.append(tab);
    const panel = panelByView.get(view);
    if (panel !== undefined) panels.append(panel);
  }
  const activate = (view: ViewId, focus: boolean): void => {
    for (const candidate of OPTIONS_WORKBENCH_VIEWS) {
      const selected = candidate === view;
      const tab = tabByView.get(candidate);
      const panel = panelByView.get(candidate);
      if (tab !== undefined) {
        tab.tabIndex = selected ? 0 : -1;
        tab.setAttribute("aria-selected", String(selected));
      }
      if (panel !== undefined) panel.hidden = !selected;
    }
    if (focus) tabByView.get(view)?.focus();
  };
  bindTabs(tabByView, activate);
  const chain = panelByView.get("option_chain");
  chain?.append(
    renderOptionsChainTable(workbench.chain, snapshot, drawer, (selection) => {
      scenarios.append(selection);
    }),
    textElement(
      "p",
      "Horizontal scroll is local to the option chain.",
      "options-workbench-scroll-note",
    ),
    scenarios.chain,
  );
  activate("market_pulse", false);
  section.append(tabs, panels);
  return section;
}

function buildPanels(
  workbench: OptionsWorkbench,
  snapshot: DashboardSnapshotV2,
  drawer: TraceDrawer,
  scenario: HTMLElement,
): ReadonlyMap<ViewId, HTMLElement> {
  const panels = new Map<ViewId, HTMLElement>();
  const market = panel("market_pulse");
  market.append(
    sectionPresentation(workbench.market, snapshot, drawer),
    renderProviderStates(workbench),
  );
  const chain = panel("option_chain");
  chain.append(
    panelHeading(
      "Option Chain",
      workbench.chain.summary,
      workbench.chain.trace_id,
      snapshot,
      drawer,
    ),
  );
  const agent = panel("strategy_agent");
  agent.append(sectionPresentation(workbench.agent, snapshot, drawer), scenario);
  const experiment = panel("experiment_lab");
  experiment.append(sectionPresentation(workbench.experiment, snapshot, drawer));
  const promotions = panel("promotion_operations");
  promotions.append(...promotionRows(workbench, snapshot, drawer));
  panels.set("market_pulse", market);
  panels.set("option_chain", chain);
  panels.set("strategy_agent", agent);
  panels.set("experiment_lab", experiment);
  panels.set("promotion_operations", promotions);
  return panels;
}

function panel(view: ViewId): HTMLElement {
  const element = document.createElement("section");
  element.id = view;
  element.className = "options-workbench-panel";
  element.setAttribute("role", "tabpanel");
  element.setAttribute("aria-labelledby", `${view}_tab`);
  element.hidden = true;
  return element;
}

function workbenchTab(view: ViewId): HTMLButtonElement {
  const tab = buttonElement(VIEW_LABELS[view], "");
  tab.id = `${view}_tab`;
  tab.setAttribute("role", "tab");
  tab.setAttribute("aria-controls", view);
  tab.setAttribute("aria-selected", "false");
  tab.tabIndex = -1;
  return tab;
}

function bindTabs(
  tabs: ReadonlyMap<ViewId, HTMLButtonElement>,
  activate: (view: ViewId, focus: boolean) => void,
): void {
  for (const [index, view] of OPTIONS_WORKBENCH_VIEWS.entries()) {
    const tab = tabs.get(view);
    if (tab === undefined) continue;
    tab.addEventListener("click", () => activate(view, true));
    tab.addEventListener("keydown", (event) => {
      const target = targetView(event.key, index);
      if (target === null) return;
      event.preventDefault();
      activate(target, true);
    });
  }
}

function targetView(key: string, current: number): ViewId | null {
  if (key === "Enter" || key === " ") return OPTIONS_WORKBENCH_VIEWS[current] ?? null;
  if (key === "Home") return OPTIONS_WORKBENCH_VIEWS[0];
  if (key === "End") return OPTIONS_WORKBENCH_VIEWS.at(-1) ?? null;
  if (key === "ArrowRight")
    return OPTIONS_WORKBENCH_VIEWS[(current + 1) % OPTIONS_WORKBENCH_VIEWS.length] ?? null;
  if (key === "ArrowLeft")
    return (
      OPTIONS_WORKBENCH_VIEWS[
        (current - 1 + OPTIONS_WORKBENCH_VIEWS.length) % OPTIONS_WORKBENCH_VIEWS.length
      ] ?? null
    );
  return null;
}

function sectionPresentation(
  section: WorkbenchSection,
  snapshot: DashboardSnapshotV2,
  drawer: TraceDrawer,
): HTMLElement {
  const presentation = workbenchStatePresentation(section.state);
  const article = document.createElement("article");
  article.className = "options-workbench-receipt";
  article.append(
    panelHeading(presentation.label, section.summary, section.trace_id, snapshot, drawer),
    textElement("p", section.blocker_code ?? "No blocker recorded"),
  );
  return article;
}

function panelHeading(
  title: string,
  summary: string,
  traceId: string,
  snapshot: DashboardSnapshotV2,
  drawer: TraceDrawer,
): HTMLElement {
  const heading = document.createElement("header");
  heading.className = "options-workbench-panel-heading";
  const copy = document.createElement("div");
  copy.append(textElement("h3", title), textElement("p", summary));
  heading.append(copy, traceButton(title, traceId, snapshot, drawer));
  return heading;
}

function promotionRows(
  workbench: OptionsWorkbench,
  snapshot: DashboardSnapshotV2,
  drawer: TraceDrawer,
): readonly HTMLElement[] {
  if (workbench.promotions.length === 0)
    return [textElement("p", "Promotion evidence unavailable")];
  return workbench.promotions.map((promotion) => {
    const row = document.createElement("article");
    row.className = "options-workbench-promotion";
    row.append(
      textElement("h3", promotion.promotion_id),
      textElement(
        "p",
        `${promotion.state} · ${promotion.passed_gate_count}/${promotion.total_gate_count} · ${promotion.blockers.join(", ")}`,
      ),
      traceButton(promotion.promotion_id, promotion.trace_id, snapshot, drawer),
    );
    return row;
  });
}

function traceButton(
  label: string,
  traceId: string,
  snapshot: DashboardSnapshotV2,
  drawer: TraceDrawer,
): HTMLButtonElement {
  const button = buttonElement("Trace", "trace-button");
  button.dataset["traceId"] = traceId;
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
