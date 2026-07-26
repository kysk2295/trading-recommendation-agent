import { z } from "zod";
import { dashboardSnapshotV2Schema } from "./schema_v2";

export type { DashboardSnapshotV2 } from "./schema_v2";
export { dashboardSnapshotV2Schema } from "./schema_v2";

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

const legacyAgentIdSchema = z.enum([
  "kr-theme",
  "us-intraday",
  "us-systematic",
  "us-swing",
  "research",
  "delivery",
]);

const agentSchema = z.strictObject({
  agent_id: legacyAgentIdSchema,
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

export const researchFamilyIdSchema = z.enum([
  "opportunity_manager",
  "day_trading",
  "swing_trading",
  "systematic_quant",
  "derivatives_research",
  "market_context",
]);
export const agentIdSchema = researchFamilyIdSchema;
export type AgentId = z.infer<typeof agentIdSchema>;

export const interactionModeSchema = z.enum([
  "conversation",
  "research",
  "analysis",
  "hypothesis",
  "experiment",
  "allowed_code",
]);

export const interactionStateSchema = z.enum([
  "queued",
  "running",
  "completed",
  "failed",
  "uncertain",
]);

const privateTextPattern =
  /(?:\/Users\/|\/home\/|~\/|[A-Za-z]:\\|\b(?:api[_ -]?key|authorization|bearer|cookie|password|secret|token|account[_ -]?(?:id|fingerprint|number)|session[_ -]?id|worktree|raw[_ -]?(?:payload|header|response|log))\b)/i;

const operatorCommandSchema = z
  .string()
  .trim()
  .min(1)
  .max(2_000)
  .refine((value) => !privateTextPattern.test(value), "private operator text is forbidden");

export const interactionSchema = z.strictObject({
  id: z.uuid(),
  agent_id: agentIdSchema,
  mode: interactionModeSchema,
  command: operatorCommandSchema,
  state: interactionStateSchema,
  response: z.string().max(8_000).nullable(),
  created_at: z.iso.datetime({ offset: true }),
  updated_at: z.iso.datetime({ offset: true }),
});

export const interactionCreateSchema = z.strictObject({
  mode: interactionModeSchema,
  command: operatorCommandSchema,
});

export const interactionReceiptSchema = z.strictObject({
  interaction: interactionSchema,
});

export const directedJobEventSchema = z.strictObject({
  type: z.literal("directed_job_event"),
  interaction_id: z.uuid(),
  agent_family_id: researchFamilyIdSchema,
  job_kind: interactionModeSchema.exclude(["conversation"]),
  kind: z.enum(["progress", "evidence", "result"]),
  state: z.enum(["running", "completed", "failed", "uncertain", "blocked"]),
  sequence: z.number().int().nonnegative().max(32),
  step: z.string().max(40).nullable(),
  evidence_sha256: z
    .string()
    .regex(/^[a-f0-9]{64}$/)
    .nullable(),
  result_sha256: z
    .string()
    .regex(/^[a-f0-9]{64}$/)
    .nullable(),
  summary: z.string().max(240).nullable(),
});

export const autonomousTaskReceiptSchema = z.strictObject({
  schema_version: z.literal(1),
  public_task_id: z.string().regex(/^[a-f0-9]{32}$/),
  event_id: z.string().regex(/^[a-f0-9]{64}$/),
  agent_family_id: researchFamilyIdSchema,
  channel: z.literal("autonomous_research"),
  trigger_type: z.enum([
    "new_data",
    "market_event",
    "experiment_result",
    "reviewer_feedback",
    "approved_schedule",
  ]),
  policy_version: z.string().regex(/^[a-zA-Z0-9_.:-]{3,80}$/),
  code_version: z.string().regex(/^[a-f0-9]{40}(?:[a-f0-9]{24})?$/),
  sequence: z.number().int().nonnegative().max(10_000),
  kind: z.enum(["blocker", "claim", "progress", "evidence", "result", "cleanup"]),
  state: z.enum(["claimed", "running", "completed", "failed", "uncertain", "blocked", "duplicate"]),
  occurred_at: z.iso.datetime({ offset: true }),
  reason: z
    .string()
    .regex(/^[a-z0-9_]{3,80}$/)
    .nullable(),
  evidence_refs: z.array(z.string().regex(/^[a-f0-9]{64}$/)).max(32),
  result_sha256: z
    .string()
    .regex(/^[a-f0-9]{64}$/)
    .nullable(),
  summary: z.string().max(240).nullable(),
  consumed_tokens: z.number().int().nonnegative().max(1_000_000),
  consumed_cost_microusd: z.number().int().nonnegative().max(100_000_000),
  redaction_status: z.literal("passed"),
  reviewer_state: z.enum(["pending", "accepted", "rejected", "needs_evidence"]),
  lifecycle_state: z.literal("unchanged"),
});

export const autonomousTaskEventSchema = z.strictObject({
  type: z.literal("agent_task_event"),
  task: autonomousTaskReceiptSchema,
});

export type AutonomousTaskReceipt = z.infer<typeof autonomousTaskReceiptSchema>;

export const operatorSessionSchema = z.strictObject({
  authenticated: z.boolean(),
});

export const dashboardSnapshotV1Schema = z.strictObject({
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

export const dashboardSnapshotSchema = dashboardSnapshotV2Schema;

export const viewerMessageSchema = z.strictObject({
  type: z.literal("snapshot"),
  snapshot: dashboardSnapshotSchema,
});

export const operatorMessageSchema = z.discriminatedUnion("type", [
  z.strictObject({
    type: z.literal("interaction"),
    interaction: interactionSchema,
  }),
  directedJobEventSchema,
  autonomousTaskEventSchema,
]);

export type DashboardSnapshotV1 = z.infer<typeof dashboardSnapshotV1Schema>;
export type DashboardSnapshot = z.infer<typeof dashboardSnapshotSchema>;
export type AgentView = DashboardSnapshotV1["agents"][number];
export type Interaction = z.infer<typeof interactionSchema>;
export type InteractionState = z.infer<typeof interactionStateSchema>;
export type InteractionMode = z.infer<typeof interactionModeSchema>;
export type DirectedJobEvent = z.infer<typeof directedJobEventSchema>;
