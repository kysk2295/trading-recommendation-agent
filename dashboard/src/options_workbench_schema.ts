import { z } from "zod";

const boundedIdSchema = z.string().regex(/^[a-zA-Z0-9_.:-]{1,100}$/);
const safeCodeSchema = z.string().regex(/^[a-z][a-z0-9_]{0,63}$/);
const decimalSchema = z
  .string()
  .max(32)
  .regex(/^-?[0-9]+(?:\.[0-9]{1,8})?$/);
const nonnegativeDecimalSchema = z
  .string()
  .max(32)
  .regex(/^[0-9]+(?:\.[0-9]{1,8})?$/);
const timestampSchema = z.iso.datetime({ offset: true });
const dateSchema = z.iso.date();

export const quoteStateSchema = z.enum([
  "indicative",
  "delayed",
  "current",
  "stale",
  "blocked",
  "unavailable",
]);

export const workbenchSectionStateSchema = z.enum([
  "loading",
  "empty",
  "error",
  "blocked",
  "unavailable",
  "corrupt",
  "stale",
  "populated",
]);

const sectionFields = {
  state: workbenchSectionStateSchema,
  observed_at: timestampSchema.nullable(),
  blocker_code: safeCodeSchema.nullable(),
  summary: z.string().min(1).max(160),
  trace_id: boundedIdSchema,
} as const;

export const workbenchSectionSchema = z.strictObject(sectionFields).superRefine(checkSectionState);

export const optionChainCellSchema = z
  .strictObject({
    contract_id: boundedIdSchema,
    side: z.enum(["call", "put"]),
    provider: z.enum(["alpaca", "kis", "ls"]),
    state: quoteStateSchema,
    bid: decimalSchema.nullable().default(null),
    ask: decimalSchema.nullable().default(null),
    last: decimalSchema.nullable().default(null),
    implied_volatility: decimalSchema.nullable().default(null),
    delta: decimalSchema.nullable().default(null),
    gamma: decimalSchema.nullable().default(null),
    theta: decimalSchema.nullable().default(null),
    vega: decimalSchema.nullable().default(null),
    volume: z.int().nonnegative().nullable().default(null),
    open_interest: z.int().nonnegative().nullable().default(null),
    observed_at: timestampSchema.nullable(),
    trace_id: boundedIdSchema,
    selectable: z.boolean(),
  })
  .superRefine((value, context) => {
    if (value.selectable && !["indicative", "delayed", "current"].includes(value.state)) {
      context.addIssue({ code: "custom", message: "selectable_quote_not_usable" });
    }
  });

export const optionChainRowSchema = z
  .strictObject({
    strike: nonnegativeDecimalSchema,
    call: optionChainCellSchema.nullable(),
    put: optionChainCellSchema.nullable(),
  })
  .superRefine((value, context) => {
    if (compareNonnegativeDecimals(value.strike, "0") <= 0) {
      context.addIssue({ code: "custom", message: "chain_row_strike_not_positive" });
    }
    if (value.call === null && value.put === null) {
      context.addIssue({ code: "custom", message: "empty_chain_row" });
    }
    if (value.call?.side !== undefined && value.call.side !== "call") {
      context.addIssue({ code: "custom", message: "call_cell_side_mismatch" });
    }
    if (value.put?.side !== undefined && value.put.side !== "put") {
      context.addIssue({ code: "custom", message: "put_cell_side_mismatch" });
    }
  });

export const optionChainViewSchema = z
  .strictObject({
    ...sectionFields,
    underlying: boundedIdSchema.nullable(),
    selected_expiration: dateSchema.nullable(),
    expirations: z.array(dateSchema).max(12),
    total_count: z.int().nonnegative(),
    projected_count: z.int().nonnegative(),
    truncated: z.boolean(),
    rows: z.array(optionChainRowSchema).max(41),
  })
  .superRefine((value, context) => {
    checkSectionState(value, context);
    if (value.projected_count !== value.rows.length) {
      context.addIssue({ code: "custom", message: "chain_projected_count_mismatch" });
    }
    if (value.total_count < value.projected_count) {
      context.addIssue({ code: "custom", message: "chain_total_count_below_projected" });
    }
    if (value.truncated !== value.total_count > value.projected_count) {
      context.addIssue({ code: "custom", message: "chain_truncation_mismatch" });
    }
    if (
      value.selected_expiration !== null &&
      !value.expirations.includes(value.selected_expiration)
    ) {
      context.addIssue({ code: "custom", message: "selected_expiration_not_available" });
    }
  });

export const strategyLegSchema = z
  .strictObject({
    contract_id: boundedIdSchema,
    action: z.enum(["long", "short"]),
    side: z.enum(["call", "put"]),
    strike: nonnegativeDecimalSchema,
    premium: nonnegativeDecimalSchema,
    quantity: z.int().positive().max(100_000),
    multiplier: z.int().positive().max(100_000),
    trace_id: boundedIdSchema,
  })
  .superRefine((value, context) => {
    if (compareNonnegativeDecimals(value.strike, "0") <= 0) {
      context.addIssue({ code: "custom", message: "strategy_leg_strike_not_positive" });
    }
  });

export const strategyScenarioSchema = z
  .strictObject({
    state: z.literal("research_only"),
    currency: z.string().regex(/^[A-Z]{3}$/),
    spot: nonnegativeDecimalSchema,
    legs: z.array(strategyLegSchema).min(1).max(8),
    scenario_spots: z.array(nonnegativeDecimalSchema).min(2).max(41),
    trace_id: boundedIdSchema,
  })
  .superRefine((value, context) => {
    if (
      value.scenario_spots.some((spot, index) => {
        if (index === 0) return false;
        const previous = value.scenario_spots.at(index - 1);
        return previous !== undefined && compareNonnegativeDecimals(previous, spot) >= 0;
      })
    ) {
      context.addIssue({ code: "custom", message: "scenario_spots_not_strictly_ascending" });
    }
  });

export const promotionSummarySchema = z
  .strictObject({
    promotion_id: boundedIdSchema,
    state: z.enum(["held", "approved", "rejected", "demoted"]),
    passed_gate_count: z.int().nonnegative(),
    total_gate_count: z.int().positive(),
    blockers: z.array(safeCodeSchema).max(20),
    trace_id: boundedIdSchema,
  })
  .superRefine((value, context) => {
    if (value.passed_gate_count > value.total_gate_count) {
      context.addIssue({ code: "custom", message: "promotion_passed_gate_count_exceeds_total" });
    }
    if (value.state === "approved" && value.passed_gate_count !== value.total_gate_count) {
      context.addIssue({ code: "custom", message: "promotion_approved_incomplete" });
    }
    if (value.state === "approved" && value.blockers.length > 0) {
      context.addIssue({ code: "custom", message: "promotion_approved_has_blockers" });
    }
    if (value.state !== "approved" && value.blockers.length === 0) {
      context.addIssue({ code: "custom", message: "promotion_blocker_required" });
    }
  });

export const optionsWorkbenchSchema = z.strictObject({
  schema_version: z.literal(1),
  selected_view: z.enum([
    "market_pulse",
    "option_chain",
    "strategy_agent",
    "experiment_lab",
    "promotion_operations",
  ]),
  market: workbenchSectionSchema,
  chain: optionChainViewSchema,
  scenario: strategyScenarioSchema.nullable(),
  agent: workbenchSectionSchema,
  experiment: workbenchSectionSchema,
  promotions: z.array(promotionSummarySchema).max(20),
});

export type OptionChainCellInput = z.input<typeof optionChainCellSchema>;
export type OptionsWorkbench = z.infer<typeof optionsWorkbenchSchema>;
export type OptionsWorkbenchInput = z.input<typeof optionsWorkbenchSchema>;

function checkSectionState(
  value: z.infer<typeof workbenchSectionSchema>,
  context: z.RefinementCtx,
): void {
  const blockerRequired = ["error", "blocked", "unavailable", "corrupt", "stale"].includes(
    value.state,
  );
  const observationRequired = ["populated", "stale"].includes(value.state);
  if (value.state === "loading" && (value.observed_at !== null || value.blocker_code !== null)) {
    context.addIssue({ code: "custom", message: "loading_section_metadata_forbidden" });
  }
  if (blockerRequired && value.blocker_code === null) {
    context.addIssue({ code: "custom", message: "section_blocker_required" });
  }
  if (!blockerRequired && value.blocker_code !== null) {
    context.addIssue({ code: "custom", message: "section_blocker_forbidden" });
  }
  if (observationRequired && value.observed_at === null) {
    context.addIssue({ code: "custom", message: "section_observed_at_required" });
  }
}

function compareNonnegativeDecimals(left: string, right: string): number {
  const [leftWhole = "0", leftFraction = ""] = left.split(".");
  const [rightWhole = "0", rightFraction = ""] = right.split(".");
  const wholeDifference = BigInt(leftWhole) - BigInt(rightWhole);
  if (wholeDifference !== 0n) return wholeDifference > 0n ? 1 : -1;
  const fractionDifference =
    BigInt(leftFraction.padEnd(8, "0")) - BigInt(rightFraction.padEnd(8, "0"));
  if (fractionDifference !== 0n) return fractionDifference > 0n ? 1 : -1;
  return 0;
}
