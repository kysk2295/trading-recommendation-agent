import { textElement } from "../dom";
import type { OptionsWorkbench } from "../options_workbench_schema";
import type { ChainLegSelection } from "./options_chain_table";
import {
  breakEvenPoints,
  type StrategyLeg,
  scenarioSeries,
  strategyLegFromFixture,
} from "./options_workbench_presenters";

type ScenarioEntry = Readonly<{ contractId: string; leg: StrategyLeg }>;
type ScenarioBaseline =
  | Readonly<{ kind: "ready"; entries: readonly ScenarioEntry[]; spots: readonly number[] }>
  | Readonly<{ kind: "missing" | "blocked" }>;

export type ScenarioPresentations = Readonly<{
  chain: HTMLElement;
  agent: HTMLElement;
  append: (selection: ChainLegSelection) => void;
}>;

export function renderWorkbenchHeader(): HTMLElement {
  const header = document.createElement("header");
  header.className = "options-workbench-header";
  const copy = document.createElement("div");
  copy.append(
    textElement("p", "OPTIONS RESEARCH", "meta-label"),
    textElement("h2", "Options Workbench"),
  );
  header.append(
    copy,
    textElement(
      "p",
      "RESEARCH ONLY · read-only provider evidence · no execution controls",
      "options-workbench-notice",
    ),
  );
  return header;
}

export function renderProviderStates(workbench: OptionsWorkbench): HTMLElement {
  const list = document.createElement("p");
  const labels = new Set<string>();
  for (const row of workbench.chain.rows) {
    if (row.call !== null) labels.add(`${row.call.provider} · ${row.call.state}`);
    if (row.put !== null) labels.add(`${row.put.provider} · ${row.put.state}`);
  }
  list.textContent = labels.size === 0 ? "Provider state unavailable" : [...labels].join(" · ");
  return list;
}

export function createScenarioPresentations(workbench: OptionsWorkbench): ScenarioPresentations {
  const chain = scenarioPanel();
  const agent = scenarioPanel();
  const baseline = scenarioBaseline(workbench);
  let selections: readonly ChainLegSelection[] = [];
  const render = (): void => {
    const content = scenarioContent(baseline, selections);
    chain.replaceChildren(...content);
    agent.replaceChildren(...scenarioContent(baseline, selections));
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

function scenarioContent(
  baseline: ScenarioBaseline,
  selections: readonly ChainLegSelection[],
): readonly Node[] {
  if (baseline.kind !== "ready") {
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
  const entries = [...baseline.entries, ...selectedEntries(selections)];
  const legs = entries.map((entry) => entry.leg);
  const selectedLegs = document.createElement("ol");
  selectedLegs.dataset["selectedLegs"] = "true";
  for (const entry of entries) {
    const item = textElement(
      "li",
      `${entry.leg.action} ${entry.leg.side} ${entry.leg.strike.toFixed(2)} @ ${entry.leg.premium.toFixed(2)}`,
    );
    item.dataset["selectedLeg"] = entry.contractId;
    selectedLegs.append(item);
  }
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
  const breakEvens = breakEvenPoints(legs);
  const breakEven = textElement(
    "p",
    `Break-even · ${breakEvens.length === 0 ? "none" : breakEvens.map((point) => point.toFixed(2)).join(", ")}`,
  );
  breakEven.dataset["breakEven"] = "true";
  return [
    textElement("h3", "Deterministic expiration scenario"),
    selectedLegs,
    breakEven,
    series,
    textElement("p", "RESEARCH ONLY · immutable snapshot input"),
  ];
}

function scenarioBaseline(workbench: OptionsWorkbench): ScenarioBaseline {
  if (workbench.scenario === null) return { kind: "missing" };
  const entries: ScenarioEntry[] = [];
  for (const fixture of workbench.scenario.legs) {
    const conversion = strategyLegFromFixture(fixture);
    if (conversion.kind === "blocked") return { kind: "blocked" };
    entries.push({ contractId: fixture.contract_id, leg: conversion.leg });
  }
  const spots = workbench.scenario.scenario_spots.map(Number);
  if (spots.some((spot) => !Number.isFinite(spot))) return { kind: "blocked" };
  return Object.freeze({
    kind: "ready" as const,
    entries: Object.freeze(entries),
    spots: Object.freeze(spots),
  });
}

function selectedEntries(selections: readonly ChainLegSelection[]): readonly ScenarioEntry[] {
  return selections.map((selection) => ({
    contractId: selection.contractId,
    leg: {
      action: "long" as const,
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
