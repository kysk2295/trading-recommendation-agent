import { buttonElement, textElement } from "../dom";
import { resolveEvidenceTrace } from "../evidence_trace";
import { parseOperationalDecimal } from "../options_workbench_decimal";
import type { OptionsWorkbench } from "../options_workbench_schema";
import type { ChainLegSelection } from "./options_chain_table";
import { renderPayoffResearch, type ScenarioEntry } from "./options_workbench_payoff";
import {
  breakEvenPoints,
  scenarioSeries,
  strategyLegFromFixture,
} from "./options_workbench_presenters";
import { type WorkbenchTraceContext, workbenchTraceButton } from "./options_workbench_trace";

type ScenarioBaseline =
  | Readonly<{ kind: "ready"; entries: readonly ScenarioEntry[]; spots: readonly number[] }>
  | Readonly<{ kind: "missing" | "blocked" }>;

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
    chain.replaceChildren(...chainScenarioContent(baseline, selections));
    agent.replaceChildren(
      ...scenarioContent(workbench, baseline, selections, selectedSpot, setSpot, reset),
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

function chainScenarioContent(
  baseline: ScenarioBaseline,
  selections: readonly ChainLegSelection[],
): readonly Node[] {
  if (baseline.kind !== "ready") return unavailableScenario(baseline);
  const entries = [...baseline.entries, ...selectedEntries(selections)];
  const legs = entries.map((entry) => entry.leg);
  const series = document.createElement("ol");
  series.dataset["scenarioSeries"] = "true";
  for (const point of scenarioSeries(legs, baseline.spots)) {
    const item = document.createElement("li");
    item.append(
      textElement("span", point.spot.toFixed(2)),
      textElement("strong", point.payoff.toFixed(2)),
    );
    series.append(item);
  }
  return [
    textElement("h3", "Deterministic expiration scenario"),
    selectedLegList(entries),
    breakEvenText(legs),
    series,
    textElement("p", "RESEARCH ONLY · immutable snapshot input"),
  ];
}

function scenarioContent(
  workbench: OptionsWorkbench,
  baseline: ScenarioBaseline,
  selections: readonly ChainLegSelection[],
  selectedSpot: number,
  onSpot: (spot: number) => void,
  onReset: () => void,
): readonly Node[] {
  if (baseline.kind !== "ready") {
    return unavailableScenario(baseline);
  }
  const entries = [...baseline.entries, ...selectedEntries(selections)];
  const reset = buttonElement("Reset local legs", "options-local-reset");
  reset.addEventListener("click", onReset);
  return [
    textElement("h3", "Deterministic expiration scenario"),
    textElement("p", "Underlying and baseline are read-only; selected chain legs remain local."),
    selectedLegList(entries),
    reset,
    ...renderPayoffResearch(workbench, entries, baseline.spots, selectedSpot, onSpot),
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
  baseline: Exclude<ScenarioBaseline, { kind: "ready" }>,
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
  details.append(receipt, workbenchTraceButton("Agent Room", workbench.agent.trace_id, context));
  return details;
}

function receiptRow(label: string, value: string): HTMLElement {
  const row = document.createElement("div");
  row.append(textElement("dt", label), textElement("dd", value));
  return row;
}

function scenarioBaseline(workbench: OptionsWorkbench): ScenarioBaseline {
  if (workbench.scenario === null) return { kind: "missing" };
  const entries: ScenarioEntry[] = [];
  for (const fixture of workbench.scenario.legs) {
    const conversion = strategyLegFromFixture(fixture);
    if (conversion.kind === "blocked") return { kind: "blocked" };
    entries.push({ contractId: fixture.contract_id, leg: conversion.leg });
  }
  const spots: number[] = [];
  for (const spot of workbench.scenario.scenario_spots) {
    const parsed = parseOperationalDecimal(spot);
    if (parsed.kind === "blocked") return { kind: "blocked" };
    spots.push(parsed.decimal.value);
  }
  if (
    scenarioSeries(
      entries.map((entry) => entry.leg),
      spots,
    ).some((point) => !Number.isFinite(point.payoff))
  )
    return { kind: "blocked" };
  return { kind: "ready", entries: Object.freeze(entries), spots: Object.freeze(spots) };
}

function selectedEntries(selections: readonly ChainLegSelection[]): readonly ScenarioEntry[] {
  return selections.map((selection) => ({
    contractId: selection.contractId,
    leg: {
      action: "long",
      side: selection.side,
      strike: selection.strike,
      premium: selection.premium,
      quantity: 1,
      multiplier: 100,
    },
  }));
}

function scenarioPanel(): HTMLElement {
  const article = document.createElement("article");
  article.className = "options-workbench-scenario";
  return article;
}
