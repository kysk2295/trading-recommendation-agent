import { parseOperationalDecimal } from "../options_workbench_decimal";
import type { OptionsWorkbench } from "../options_workbench_schema";
import type { ChainLegSelection } from "./options_chain_table";
import type { ScenarioEntry } from "./options_workbench_payoff";
import { scenarioSeries, strategyLegFromFixture } from "./options_workbench_presenters";

export type ScenarioBaseline =
  | Readonly<{ kind: "ready"; entries: readonly ScenarioEntry[]; spots: readonly number[] }>
  | Readonly<{ kind: "missing" | "blocked" }>;

export type ResolvedScenario =
  | Readonly<{
      kind: "ready";
      entries: readonly ScenarioEntry[];
      spots: readonly number[];
      local: boolean;
    }>
  | Readonly<{ kind: "missing" | "blocked" }>;

export function scenarioBaseline(workbench: OptionsWorkbench): ScenarioBaseline {
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

export function resolveScenario(
  baseline: ScenarioBaseline,
  selections: readonly ChainLegSelection[],
): ResolvedScenario {
  if (baseline.kind === "blocked") return baseline;
  const selected = selectedEntries(selections);
  if (baseline.kind === "ready") {
    return {
      kind: "ready",
      entries: [...baseline.entries, ...selected],
      spots: baseline.spots,
      local: false,
    };
  }
  if (selected.length === 0) return baseline;
  return { kind: "ready", entries: selected, spots: localScenarioSpots(selected), local: true };
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

function localScenarioSpots(entries: readonly ScenarioEntry[]): readonly number[] {
  const strikes = entries.map((entry) => entry.leg.strike);
  const low = Math.max(0, Math.min(...strikes) * 0.8);
  const high = Math.max(...strikes) * 1.2;
  const interval = (high - low) / 4;
  return Object.freeze(Array.from({ length: 5 }, (_, index) => low + interval * index));
}
