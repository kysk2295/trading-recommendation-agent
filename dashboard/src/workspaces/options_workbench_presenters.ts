import type { OptionChainCellInput, OptionsWorkbench } from "../options_workbench_schema";

export type StrategyLeg = Readonly<{
  action: "long" | "short";
  side: "call" | "put";
  strike: number;
  premium: number;
  quantity: number;
  multiplier: number;
}>;

export type StrategyLegFixture = Readonly<{
  action: StrategyLeg["action"];
  side: StrategyLeg["side"];
  strike: string;
  premium: string;
  quantity: number;
  multiplier: number;
}>;

export type ScenarioPoint = Readonly<{ spot: number; payoff: number }>;
export type WorkbenchStatePresentation = Readonly<{
  tone: "neutral" | "warning" | "positive";
  label: string;
}>;

export type StrategyLegConversion =
  | Readonly<{ kind: "ready"; leg: StrategyLeg }>
  | Readonly<{ kind: "blocked"; reason: "non_finite_decimal" }>;

export type SelectableResearchLeg =
  | Readonly<{ kind: "selected"; contractId: string; side: "call" | "put"; premium: number }>
  | Readonly<{
      kind: "blocked";
      reason: "quote_not_selectable" | "quote_price_missing" | "non_finite_decimal";
    }>;

type DecimalConversion =
  | Readonly<{ kind: "ready"; value: number }>
  | Readonly<{ kind: "blocked"; reason: "non_finite_decimal" }>;

type WorkbenchState = OptionsWorkbench["market"]["state"];

export function payoffAtExpiration(legs: readonly StrategyLeg[], spot: number): number {
  return legs.reduce(
    (total, leg) =>
      total +
      direction(leg.action) *
        (intrinsic(leg.side, leg.strike, spot) - leg.premium) *
        leg.quantity *
        leg.multiplier,
    0,
  );
}

export function scenarioSeries(
  legs: readonly StrategyLeg[],
  spots: readonly number[],
): readonly ScenarioPoint[] {
  return spots.map((spot) => ({ spot, payoff: payoffAtExpiration(legs, spot) }));
}

export function breakEvenPoints(legs: readonly StrategyLeg[]): readonly number[] {
  const strikes = [...new Set(legs.map((leg) => leg.strike))].sort((left, right) => left - right);
  const breakEvens: number[] = [];
  let lower = 0;
  for (const upper of strikes) {
    appendBreakEven(legs, lower, upper, breakEvens);
    lower = upper;
  }
  appendBreakEven(legs, lower, null, breakEvens);
  return breakEvens;
}

export function strategyLegFromFixture(fixture: StrategyLegFixture): StrategyLegConversion {
  const strike = decimalNumber(fixture.strike);
  const premium = decimalNumber(fixture.premium);
  if (
    strike.kind === "blocked" ||
    premium.kind === "blocked" ||
    !Number.isFinite(fixture.quantity) ||
    !Number.isFinite(fixture.multiplier)
  ) {
    return { kind: "blocked", reason: "non_finite_decimal" };
  }
  return {
    kind: "ready",
    leg: {
      action: fixture.action,
      side: fixture.side,
      strike: strike.value,
      premium: premium.value,
      quantity: fixture.quantity,
      multiplier: fixture.multiplier,
    },
  };
}

export function selectableResearchLeg(cell: OptionChainCellInput): SelectableResearchLeg {
  if (!cell.selectable) return { kind: "blocked", reason: "quote_not_selectable" };
  const quote = cell.ask ?? cell.last ?? cell.bid;
  if (quote === null || quote === undefined)
    return { kind: "blocked", reason: "quote_price_missing" };
  const premium = decimalNumber(quote);
  switch (premium.kind) {
    case "ready":
      return {
        kind: "selected",
        contractId: cell.contract_id,
        side: cell.side,
        premium: premium.value,
      };
    case "blocked":
      return premium;
    default:
      return assertNever(premium);
  }
}

export function workbenchStatePresentation(state: WorkbenchState): WorkbenchStatePresentation {
  switch (state) {
    case "loading":
      return { tone: "neutral", label: "Loading research snapshot" };
    case "empty":
      return { tone: "neutral", label: "No research records" };
    case "error":
    case "blocked":
    case "unavailable":
    case "corrupt":
    case "stale":
      return { tone: "warning", label: "Research snapshot unavailable" };
    case "populated":
      return { tone: "positive", label: "Research snapshot available" };
    default:
      return assertNever(state);
  }
}

function appendBreakEven(
  legs: readonly StrategyLeg[],
  lower: number,
  upper: number | null,
  breakEvens: number[],
): void {
  if (upper !== null && upper === lower) return;
  const probe = upper === null ? lower + 1 : (lower + upper) / 2;
  const slope = payoffSlope(legs, probe);
  if (slope === 0) return;
  const root = probe - payoffAtExpiration(legs, probe) / slope;
  if (root < lower || (upper !== null && root > upper) || breakEvens.includes(root)) return;
  breakEvens.push(root);
}

function payoffSlope(legs: readonly StrategyLeg[], spot: number): number {
  return legs.reduce(
    (total, leg) =>
      total +
      direction(leg.action) *
        activeSlope(leg.side, leg.strike, spot) *
        leg.quantity *
        leg.multiplier,
    0,
  );
}

function activeSlope(side: StrategyLeg["side"], strike: number, spot: number): number {
  switch (side) {
    case "call":
      return spot > strike ? 1 : 0;
    case "put":
      return spot < strike ? -1 : 0;
    default:
      return assertNever(side);
  }
}

function intrinsic(side: StrategyLeg["side"], strike: number, spot: number): number {
  switch (side) {
    case "call":
      return Math.max(spot - strike, 0);
    case "put":
      return Math.max(strike - spot, 0);
    default:
      return assertNever(side);
  }
}

function direction(action: StrategyLeg["action"]): 1 | -1 {
  switch (action) {
    case "long":
      return 1;
    case "short":
      return -1;
    default:
      return assertNever(action);
  }
}

function decimalNumber(value: string): DecimalConversion {
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? { kind: "ready", value: numeric }
    : { kind: "blocked", reason: "non_finite_decimal" };
}

function assertNever(value: never): never {
  throw new OptionsWorkbenchPresenterError(`unexpected variant: ${String(value)}`);
}

class OptionsWorkbenchPresenterError extends Error {
  override readonly name = "OptionsWorkbenchPresenterError";
}
