import { z } from "zod";
import { validateSnapshotGraph } from "./snapshot_v2_graph";

export const sourceStateNameSchema = z.enum([
  "loading",
  "empty",
  "error",
  "blocked",
  "unavailable",
  "corrupt",
  "stale",
  "populated",
]);

const boundedIdSchema = z.string().regex(/^[a-zA-Z0-9_.:-]{1,100}$/);
const timestampSchema = z.iso.datetime({ offset: true });
const blockerCodeSchema = z.string().regex(/^[a-z0-9_]{3,80}$/);

const countFields = {
  total_count: z.number().int().nonnegative().max(100_000),
  projected_count: z.number().int().nonnegative().max(1_000),
  truncated: z.boolean(),
} as const;

const freshnessSchema = z.strictObject({
  policy_id: boundedIdSchema,
  age_seconds: z.number().int().nonnegative().max(31_536_000).nullable(),
  as_of: timestampSchema,
});

const workspaceItemSchema = z
  .strictObject({
    item_id: boundedIdSchema,
    kind: z.enum(["metric", "research", "strategy", "derivative", "paper", "system"]),
    label: z.string().min(1).max(80),
    state: sourceStateNameSchema,
    value: z.string().max(160).nullable(),
    observed_at: timestampSchema.nullable(),
    trace_id: boundedIdSchema,
  })
  .superRefine(checkObservationState);

const publicAgentSchema = z.strictObject({
  agent_id: z.enum([
    "opportunity_manager",
    "day_trading",
    "swing_trading",
    "systematic_quant",
    "derivatives_research",
    "market_context",
  ]),
  label: z.string().min(1).max(40),
  role: z.string().min(1).max(80),
  capabilities: z.tuple([
    z.literal("conversation"),
    z.literal("directed_tool"),
    z.literal("autonomous_research"),
  ]),
  runtime_state: z.enum(["running", "armed", "idle", "failed", "unavailable"]),
  trace_id: boundedIdSchema,
});

const sourceCapabilitySchema = z
  .strictObject({
    capability_id: boundedIdSchema,
    provider: z.enum(["fred", "alfred", "treasury", "cftc", "opendart", "kis", "ls", "alpaca"]),
    label: z.string().min(1).max(80),
    state: sourceStateNameSchema,
    entitlement: z.enum(["realtime", "delayed", "research_only", "unavailable"]),
    observed_at: timestampSchema.nullable(),
    trace_id: boundedIdSchema,
  })
  .superRefine(checkObservationState);

const sourceStateFields = {
  state: sourceStateNameSchema,
  observed_at: timestampSchema.nullable(),
  freshness: freshnessSchema,
  blocker_code: blockerCodeSchema.nullable(),
  summary: z.string().min(1).max(160),
  ...countFields,
  trace_id: boundedIdSchema,
  items: z.array(workspaceItemSchema).max(24),
} as const;

const workspaceSchema = z.strictObject(sourceStateFields).superRefine(checkSourceState);
const commandCenterSchema = z
  .strictObject({ ...sourceStateFields, agents: z.array(publicAgentSchema).max(12) })
  .superRefine(checkSourceState);
const dataSourcesSchema = z
  .strictObject({ ...sourceStateFields, capabilities: z.array(sourceCapabilitySchema).max(30) })
  .superRefine(checkSourceState);

const traceNodeSchema = z.strictObject({
  node_id: boundedIdSchema,
  kind: z.enum([
    "source_receipt",
    "observation",
    "dataset",
    "code_revision",
    "hypothesis",
    "trial",
    "reviewer_decision",
    "lifecycle_decision",
    "paper_receipt",
    "process_receipt",
    "deployment_receipt",
    "blocker_terminal",
  ]),
  label: z.string().min(1).max(100),
  observed_at: timestampSchema,
  safe_ref: z
    .string()
    .regex(/^[a-f0-9]{64}$/)
    .nullable(),
  state: z.enum(["accepted", "blocked", "unavailable", "failed"]),
  source_namespace: boundedIdSchema,
});

const traceEdgeSchema = z.strictObject({
  from_node_id: boundedIdSchema,
  to_node_id: boundedIdSchema,
  kind: z.enum([
    "derived_from",
    "observed_by",
    "bound_to",
    "evaluated_in",
    "reviewed_by",
    "decided_by",
    "executed_as",
    "reconciled_by",
    "deployed_as",
    "blocked_by",
  ]),
});

export const dashboardSnapshotV2Schema = z
  .strictObject({
    schema_version: z.literal(2),
    snapshot_id: z.uuid(),
    generated_at: timestampSchema,
    source: z.literal("local-redacted-projector"),
    workspaces: z.strictObject({
      command_center: commandCenterSchema,
      overview: workspaceSchema,
      markets: workspaceSchema,
      data_sources: dataSourcesSchema,
      research: workspaceSchema,
      strategies: workspaceSchema,
      derivatives: workspaceSchema,
      paper: workspaceSchema,
      system: workspaceSchema,
    }),
    traces: z.strictObject({
      nodes: z.array(traceNodeSchema).min(1).max(512),
      edges: z.array(traceEdgeSchema).max(768),
    }),
    projection: z
      .strictObject({
        redaction_policy_version: z.literal("dashboard-redaction-v2"),
        reader_versions: z.array(boundedIdSchema).min(1).max(40),
        source_schema_version: z.union([z.literal(1), z.literal(2)]),
        ...countFields,
      })
      .superRefine(checkCounts),
  })
  .superRefine(validateSnapshotGraph);

export type DashboardSnapshotV2 = z.infer<typeof dashboardSnapshotV2Schema>;
export type DashboardSnapshotInputV2 = z.input<typeof dashboardSnapshotV2Schema>;

function checkCounts(
  value: { total_count: number; projected_count: number; truncated: boolean },
  context: z.RefinementCtx,
): void {
  const consistent =
    value.projected_count <= value.total_count &&
    value.truncated === value.total_count > value.projected_count;
  if (!consistent) {
    context.addIssue({ code: "custom", message: "inconsistent_count_metadata" });
  }
}

function checkSourceState(value: z.infer<typeof workspaceSchema>, context: z.RefinementCtx): void {
  checkCounts(value, context);
  checkObservationState(value, context);
  const requiresBlocker = ["error", "blocked", "unavailable", "corrupt"].includes(value.state);
  if (requiresBlocker !== (value.blocker_code !== null)) {
    context.addIssue({ code: "custom", message: "inconsistent_blocker_metadata" });
  }
  if (value.projected_count !== value.items.length) {
    context.addIssue({ code: "custom", message: "projected_count_mismatch" });
  }
}

function checkObservationState(
  value: { state: z.infer<typeof sourceStateNameSchema>; observed_at: string | null },
  context: z.RefinementCtx,
): void {
  if (value.state === "loading") {
    context.addIssue({ code: "custom", message: "publisher_loading_state" });
  }
  if (value.state !== "unavailable" && value.observed_at === null) {
    context.addIssue({ code: "custom", message: "observed_at_required" });
  }
}
