import { buttonElement, textElement } from "../dom";
import type { EvidenceTraceDrawer } from "../evidence_trace";
import type { OptionsWorkbench } from "../options_workbench_schema";
import type { DashboardSnapshotV2 } from "../schema_v2";
import { renderOptionsChainTable } from "./options_chain_table";
import { renderOptionsWorkbenchExperiment } from "./options_workbench_experiment";
import { renderOptionsWorkbenchMarket } from "./options_workbench_market";
import { renderOptionsWorkbenchOperations } from "./options_workbench_operations";
import { renderWorkbenchHeader } from "./options_workbench_panels";
import { createScenarioPresentations } from "./options_workbench_strategy";
import { workbenchTraceButton } from "./options_workbench_trace";

export type WorkbenchView =
  | "market_pulse"
  | "option_chain"
  | "strategy_agent"
  | "experiment_lab"
  | "promotion_operations";

export const WORKBENCH_VIEWS: readonly WorkbenchView[] = Object.freeze([
  "market_pulse",
  "option_chain",
  "strategy_agent",
  "experiment_lab",
  "promotion_operations",
]);

type TraceDrawer = Pick<EvidenceTraceDrawer, "open">;
type PanelContext = Readonly<{
  workbench: OptionsWorkbench;
  snapshot: DashboardSnapshotV2;
  drawer: TraceDrawer;
  scenario: HTMLElement;
}>;

const VIEW_LABELS: Readonly<Record<WorkbenchView, string>> = {
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
  tabs.setAttribute("aria-describedby", "options_workbench_tabs_hint");
  const tabsHint = textElement("p", "5 views · swipe / scroll ↔", "options-workbench-tabs-hint");
  tabsHint.id = "options_workbench_tabs_hint";
  const panels = document.createElement("div");
  panels.className = "options-workbench-panels";
  const scenarios = createScenarioPresentations(workbench, { snapshot, drawer });
  const panelByView = buildPanels({ workbench, snapshot, drawer, scenario: scenarios.agent });
  const tabByView = new Map<WorkbenchView, HTMLButtonElement>();
  for (const view of WORKBENCH_VIEWS) {
    const tab = workbenchTab(view);
    tabByView.set(view, tab);
    tabs.append(tab);
    const panel = panelByView.get(view);
    if (panel !== undefined) panels.append(panel);
  }
  const activate = (view: WorkbenchView, focus: boolean): void => {
    for (const candidate of WORKBENCH_VIEWS) {
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
    renderOptionsChainTable(workbench.chain, { snapshot, drawer }, (selection) => {
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
  section.append(tabsHint, tabs, panels);
  return section;
}

function buildPanels(context: PanelContext): ReadonlyMap<WorkbenchView, HTMLElement> {
  const { workbench, snapshot, drawer, scenario } = context;
  const panels = new Map<WorkbenchView, HTMLElement>();
  const market = panel("market_pulse");
  market.append(renderOptionsWorkbenchMarket(workbench, snapshot, drawer));
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
  agent.append(scenario);
  const experiment = panel("experiment_lab");
  experiment.append(renderOptionsWorkbenchExperiment(workbench, snapshot, drawer));
  const promotions = panel("promotion_operations");
  promotions.append(renderOptionsWorkbenchOperations(workbench, snapshot, drawer));
  panels.set("market_pulse", market);
  panels.set("option_chain", chain);
  panels.set("strategy_agent", agent);
  panels.set("experiment_lab", experiment);
  panels.set("promotion_operations", promotions);
  return panels;
}

function panel(view: WorkbenchView): HTMLElement {
  const element = document.createElement("section");
  element.id = view;
  element.className = "options-workbench-panel";
  element.setAttribute("role", "tabpanel");
  element.setAttribute("aria-labelledby", `${view}_tab`);
  element.hidden = true;
  return element;
}

function workbenchTab(view: WorkbenchView): HTMLButtonElement {
  const tab = buttonElement(VIEW_LABELS[view], "");
  tab.id = `${view}_tab`;
  tab.setAttribute("role", "tab");
  tab.setAttribute("aria-controls", view);
  tab.setAttribute("aria-selected", "false");
  tab.tabIndex = -1;
  return tab;
}

function bindTabs(
  tabs: ReadonlyMap<WorkbenchView, HTMLButtonElement>,
  activate: (view: WorkbenchView, focus: boolean) => void,
): void {
  for (const [index, view] of WORKBENCH_VIEWS.entries()) {
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

function targetView(key: string, current: number): WorkbenchView | null {
  if (key === "Enter" || key === " ") return WORKBENCH_VIEWS[current] ?? null;
  if (key === "Home") return WORKBENCH_VIEWS[0] ?? null;
  if (key === "End") return WORKBENCH_VIEWS.at(-1) ?? null;
  if (key === "ArrowRight") return WORKBENCH_VIEWS[(current + 1) % WORKBENCH_VIEWS.length] ?? null;
  if (key === "ArrowLeft")
    return WORKBENCH_VIEWS[(current - 1 + WORKBENCH_VIEWS.length) % WORKBENCH_VIEWS.length] ?? null;
  return null;
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
  heading.append(copy, workbenchTraceButton(title, traceId, { snapshot, drawer }));
  return heading;
}
