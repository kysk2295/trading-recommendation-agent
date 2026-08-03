import { buttonElement, textElement } from "../dom";
import { resolveEvidenceTrace } from "../evidence_trace";
import type { OptionsWorkbench } from "../options_workbench_schema";
import type { ChainLegSelection } from "./options_chain_table";
import { renderPayoffResearch, type ScenarioEntry } from "./options_workbench_payoff";
import { breakEvenPoints, scenarioSeries } from "./options_workbench_presenters";
import {
  type ResolvedScenario,
  resolveScenario,
  scenarioBaseline,
} from "./options_workbench_scenario_data";
import { type WorkbenchTraceContext, workbenchTraceButton } from "./options_workbench_trace";

export type ScenarioPresentations = Readonly<{
  chain: HTMLElement;
  agent: HTMLElement;
  append: (selection: ChainLegSelection) => void;
}>;

export function createScenarioPresentations(
  workbench: OptionsWorkbench,
  traceContext: WorkbenchTraceContext,
): ScenarioPresentations {
  const chain = scenarioPanel();
  const agent = scenarioPanel();
  const baseline = scenarioBaseline(workbench);
  let selections: readonly ChainLegSelection[] = [];
  let selectedSpot = baseline.kind === "ready" ? Number(workbench.scenario?.spot) : 0;
  const render = (): void => {
    const resolved = resolveScenario(baseline, selections);
    if (resolved.kind === "ready" && !resolved.spots.includes(selectedSpot)) {
      selectedSpot = resolved.spots[Math.floor(resolved.spots.length / 2)] ?? 0;
    }
    chain.replaceChildren(...chainScenarioContent(resolved));
    agent.replaceChildren(
      ...scenarioContent(workbench, resolved, selectedSpot, setSpot, reset),
      agentRoom(workbench, traceContext),
    );
  };
  const setSpot = (spot: number): void => {
    selectedSpot = spot;
    render();
  };
  const reset = (): void => {
    selections = [];
    render();
  };
  render();
  return {
    chain,
    agent,
    append: (selection) => {
      selections = [...selections, selection];
      render();
    },
  };
}

function chainScenarioContent(scenario: ResolvedScenario): readonly Node[] {
  if (scenario.kind !== "ready") return unavailableScenario(scenario);
  const { entries, spots } = scenario;
  const legs = entries.map((entry) => entry.leg);
  const points = scenarioSeries(legs, spots);
  if (points.some((point) => !Number.isFinite(point.payoff))) {
    return [
      textElement("h3", "Deterministic expiration scenario"),
      selectedLegList(entries),
      textElement(
        "p",
        "Scenario unavailable · operational arithmetic exceeded the safe presentation boundary",
        "options-research-warning",
      ),
    ];
  }
  const series = document.createElement("ol");
  series.dataset["scenarioSeries"] = "true";
  for (const point of points) {
    const item = document.createElement("li");
    item.append(
      textElement("span", point.spot.toFixed(2)),
      textElement("strong", point.payoff.toFixed(2)),
    );
    series.append(item);
  }
  return [
    textElement("h3", "Deterministic expiration scenario"),
    textElement("p", scenario.local ? "LOCAL RESEARCH SCENARIO" : "CANONICAL RESEARCH SCENARIO"),
    selectedLegList(entries),
    breakEvenText(legs),
    series,
    textElement("p", "RESEARCH ONLY · immutable snapshot input"),
  ];
}

function scenarioContent(
  workbench: OptionsWorkbench,
  scenario: ResolvedScenario,
  selectedSpot: number,
  onSpot: (spot: number) => void,
  onReset: () => void,
): readonly Node[] {
  if (scenario.kind !== "ready") return unavailableScenario(scenario);
  const { entries, spots } = scenario;
  const reset = buttonElement("Reset local legs", "options-local-reset");
  reset.addEventListener("click", onReset);
  return [
    textElement("h3", "Deterministic expiration scenario"),
    textElement("p", scenario.local ? "LOCAL RESEARCH SCENARIO" : "CANONICAL RESEARCH SCENARIO"),
    textElement("p", "Underlying and baseline are read-only; selected chain legs remain local."),
    selectedLegList(entries),
    reset,
    ...renderPayoffResearch(workbench, entries, spots, selectedSpot, onSpot),
  ];
}

function selectedLegList(entries: readonly ScenarioEntry[]): HTMLOListElement {
  const list = document.createElement("ol");
  list.dataset["selectedLegs"] = "true";
  for (const entry of entries) {
    const item = textElement(
      "li",
      `${entry.leg.action} ${entry.leg.side} ${entry.leg.strike.toFixed(2)} @ ${entry.leg.premium.toFixed(2)}`,
    );
    item.dataset["selectedLeg"] = entry.contractId;
    list.append(item);
  }
  return list;
}

function breakEvenText(legs: readonly ScenarioEntry["leg"][]): HTMLElement {
  const points = breakEvenPoints(legs);
  const element = textElement(
    "p",
    `Break-even · ${points.length === 0 ? "none" : points.map((point) => point.toFixed(2)).join(", ")}`,
  );
  element.dataset["breakEven"] = "true";
  return element;
}

function unavailableScenario(
  baseline: Exclude<ResolvedScenario, { kind: "ready" }>,
): readonly Node[] {
  return [
    textElement("h3", "Scenario unavailable"),
    textElement(
      "p",
      baseline.kind === "missing"
        ? "No canonical scenario receipt"
        : "Canonical scenario decimals failed closed",
    ),
  ];
}

function agentRoom(workbench: OptionsWorkbench, context: WorkbenchTraceContext): HTMLElement {
  const details = document.createElement("details");
  details.className = "options-agent-room";
  details.setAttribute("aria-label", "Agent Room · Tool Receipt");
  details.open = true;
  details.append(textElement("summary", "Agent Room · Tool Receipt"));
  const trace = resolveEvidenceTrace(
    workbench.agent.trace_id,
    context.snapshot.traces.nodes,
    context.snapshot.traces.edges,
  );
  const terminal = trace.terminal;
  const receipt = document.createElement("dl");
  receipt.append(
    receiptRow("Safe parameters", "Not projected"),
    receiptRow("Availability", workbench.agent.state),
    receiptRow("Progress", workbench.agent.state),
    receiptRow(
      "Terminal",
      terminal === null ? `Unavailable · ${trace.status}` : `${terminal.kind} · ${terminal.state}`,
    ),
    receiptRow("Evidence path", terminal?.safe_ref ?? "Unavailable"),
  );
  const families = document.createElement("ol");
  families.className = "options-agent-families";
  for (const agent of context.snapshot.workspaces.command_center.agents) {
    const item = document.createElement("li");
    item.dataset["agentFamily"] = agent.agent_id;
    item.append(
      textElement("strong", agent.label),
      textElement("span", `${agent.runtime_state} · ${agent.capabilities.join(", ")}`),
    );
    families.append(item);
  }
  if (families.childElementCount === 0) {
    families.append(textElement("li", "Six-family runtime unavailable"));
  }
  details.append(
    receipt,
    textElement("h4", "Primary research families"),
    families,
    workbenchTraceButton("Agent Room", workbench.agent.trace_id, context),
  );
  return details;
}

function receiptRow(label: string, value: string): HTMLElement {
  const row = document.createElement("div");
  row.append(textElement("dt", label), textElement("dd", value));
  return row;
}

function scenarioPanel(): HTMLElement {
  const article = document.createElement("article");
  article.className = "options-workbench-scenario";
  return article;
}
