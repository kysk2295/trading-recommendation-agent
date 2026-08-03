import { describe, expect, test } from "bun:test";
import type { OptionChainCellInput } from "../src/options_workbench_schema";
import {
  breakEvenPoints,
  payoffAtExpiration,
  type StrategyLeg,
  scenarioSeries,
  selectableResearchLeg,
  strategyLegFromFixture,
  workbenchStatePresentation,
} from "../src/workspaces/options_workbench_presenters";

describe("options workbench presenters", () => {
  test("calculates a long call expiry payoff without LLM arithmetic", () => {
    // Given: one long call with a known expiry spot.
    const legs: readonly StrategyLeg[] = [longCall()];

    // When: deterministic expiry payoff is projected.
    const result = payoffAtExpiration(legs, 110);

    // Then: the payoff is the intrinsic value less premium times multiplier.
    expect(result).toBe(500);
  });

  test("projects scenario series and break-even points", () => {
    // Given: one long call and ascending scenario spots.
    const legs: readonly StrategyLeg[] = [longCall()];

    // When: the pure scenario presenters are evaluated.
    const series = scenarioSeries(legs, [100, 105, 110]);
    const breakEvens = breakEvenPoints(legs);

    // Then: the chart values and crossing point are deterministic.
    expect(series).toEqual([
      { spot: 100, payoff: -500 },
      { spot: 105, payoff: 0 },
      { spot: 110, payoff: 500 },
    ]);
    expect(breakEvens).toEqual([105]);
  });

  test("refuses a blocked chain cell", () => {
    // Given: a blocked non-selectable option-chain cell.
    const cell = optionCell({ state: "blocked", selectable: false });

    // When: the cell is offered for research-leg selection.
    const result = selectableResearchLeg(cell);

    // Then: the presenter returns its fail-closed selection state.
    expect(result).toEqual({ kind: "blocked", reason: "quote_not_selectable" });
  });

  test("rejects a non-finite numeric fixture conversion", () => {
    // Given: a typed fixture whose decimal overflows JavaScript number precision.
    const fixture = { ...longCallFixture(), premium: "1e309" };

    // When: it crosses the numeric presenter boundary.
    const result = strategyLegFromFixture(fixture);

    // Then: the expected conversion failure is discriminated rather than thrown.
    expect(result).toEqual({ kind: "blocked", reason: "non_finite_decimal" });
  });

  test("presents canonical workbench state without quote authority", () => {
    // Given: the transient loading state.
    const state = "loading";

    // When: its presenter label is derived.
    const presentation = workbenchStatePresentation(state);

    // Then: it remains explicitly non-actionable.
    expect(presentation).toEqual({ tone: "neutral", label: "Loading research snapshot" });
  });
});

function longCall(): StrategyLeg {
  return { action: "long", side: "call", strike: 100, premium: 5, quantity: 1, multiplier: 100 };
}

function longCallFixture(): Parameters<typeof strategyLegFromFixture>[0] {
  return {
    action: "long",
    side: "call",
    strike: "100",
    premium: "5",
    quantity: 1,
    multiplier: 100,
  };
}

function optionCell(overrides: Partial<OptionChainCellInput>): OptionChainCellInput {
  return {
    contract_id: "AAPL-20260821-C-00100000",
    side: "call",
    provider: "alpaca",
    state: "current",
    bid: "4.90",
    ask: "5.00",
    last: "5.00",
    implied_volatility: "0.25",
    delta: "0.50",
    gamma: "0.01",
    theta: "-0.02",
    vega: "0.10",
    volume: 10,
    open_interest: 100,
    observed_at: "2026-08-03T14:30:00Z",
    trace_id: "trace-1",
    selectable: true,
    ...overrides,
  };
}
