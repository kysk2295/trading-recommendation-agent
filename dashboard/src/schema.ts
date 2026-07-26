import { z } from "zod";

const marketSchema = z.strictObject({
  market_id: z.enum(["kr", "us"]),
  label: z.string().min(1).max(20),
  local_time: z.iso.datetime({ offset: true }),
  state: z.enum(["open", "closed", "pre", "after"]),
});

const forwardSchema = z.strictObject({
  session_date: z.iso.date().nullable(),
  eligible: z.boolean(),
  ranking_cycles: z.number().int().nonnegative(),
  watch_cycles: z.number().int().nonnegative(),
  failed_watch_cycles: z.number().int().nonnegative(),
  read_retries: z.number().int().nonnegative(),
  read_retry_failures: z.number().int().nonnegative(),
  candidate_input_cycles: z.number().int().nonnegative(),
  candidate_inputs: z.number().int().nonnegative(),
  recommendations: z.number().int().nonnegative(),
  blockers: z.array(z.string().max(160)).max(40),
  incidents: z.array(z.string().max(160)).max(80),
});

export const agentIdSchema = z.enum([
  "kr-theme",
  "us-intraday",
  "us-systematic",
  "us-swing",
  "research",
  "delivery",
]);

const agentSchema = z.strictObject({
  agent_id: agentIdSchema,
  label: z.string().min(1).max(30),
  state: z.enum(["running", "armed", "idle", "failed"]),
  scheduled_label: z.string().min(1).max(180),
});

const recommendationSchema = z.strictObject({
  symbol: z.string().regex(/^[A-Z0-9.-]{1,15}$/),
  strategy: z.string().min(1).max(100),
  created_at: z.iso.datetime({ offset: true }),
  entry: z.number().positive(),
  stop: z.number().positive(),
  target_1r: z.number().positive(),
  target_2r: z.number().positive(),
  state: z.string().min(1).max(40),
  rationale: z.string().max(240),
});

const signalSchema = z.strictObject({
  symbol: z.string().regex(/^[A-Z0-9.-]{1,15}$/),
  side: z.string().min(1).max(20),
  strategy: z.string().min(1).max(100),
  observed_at: z.iso.datetime({ offset: true }),
  valid_until: z.iso.datetime({ offset: true }),
  entry_price: z.string().min(1).max(40),
  stop_price: z.string().min(1).max(40),
  targets: z.array(z.string().max(40)).max(8),
  actionability: z.string().min(1).max(60),
  rationale: z.string().max(240),
  evidence_namespaces: z.array(z.string().max(100)).max(20),
});

const researchSchema = z.strictObject({
  status: z.enum(["ready", "blocked", "pending", "unavailable"]),
  session_date: z.iso.date().nullable(),
  summary: z.string().min(1).max(160),
});

const moneySchema = z.string().regex(/^-?(?:0|[1-9]\d*)(?:\.\d+)?$/);

const accountSchema = z.strictObject({
  status: z.enum(["verified", "incomplete", "unavailable"]),
  session_date: z.iso.date().nullable(),
  observed_at: z.iso.datetime({ offset: true }).nullable(),
  currency: z.literal("USD"),
  equity: moneySchema.nullable(),
  daily_pnl: moneySchema.nullable(),
  realized_pnl: moneySchema.nullable(),
  unrealized_pnl: moneySchema.nullable(),
  planned_open_risk: moneySchema.nullable(),
  open_positions: z.number().int().nonnegative(),
  open_orders: z.number().int().nonnegative(),
});

export const dashboardSnapshotSchema = z.strictObject({
  schema_version: z.literal(1),
  generated_at: z.iso.datetime({ offset: true }),
  source: z.literal("local-runtime"),
  markets: z.array(marketSchema).length(2),
  forward: forwardSchema,
  agents: z.array(agentSchema).max(12),
  recommendations: z.array(recommendationSchema).max(12),
  signals: z.array(signalSchema).max(12),
  research: researchSchema,
  account: accountSchema,
});

export type AgentId = z.infer<typeof agentIdSchema>;
export type DashboardSnapshot = z.infer<typeof dashboardSnapshotSchema>;
