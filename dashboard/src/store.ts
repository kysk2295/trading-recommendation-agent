import postgres from "postgres";
import type { AgentTaskEventStore } from "./agent_task_event_store";
import { MemoryAgentTaskEventStore, PostgresAgentTaskEventStore } from "./agent_task_event_store";
import { initializeDashboardStore } from "./dashboard_store_schema";
import type { DirectedJobEventStore } from "./directed_job_event_store";
import {
  MemoryDirectedJobEventStore,
  PostgresDirectedJobEventStore,
} from "./directed_job_event_store";
import type {
  AutonomousTaskReceipt,
  DashboardSnapshotV1,
  DirectedJobEvent,
  Interaction,
  InteractionState,
} from "./schema";
import { dashboardSnapshotV1Schema, interactionSchema } from "./schema";
import type { DashboardSnapshotV2 } from "./schema_v2";
import { dashboardSnapshotV2Schema } from "./schema_v2";
import type { NormalizedSnapshot } from "./snapshot_normalizer";
import { parseAndNormalizeSnapshot } from "./snapshot_normalizer";
import { type SnapshotSaveResult, saveSnapshotPair } from "./snapshot_pair_store";

export interface SnapshotStore extends AgentTaskEventStore, DirectedJobEventStore {
  save(snapshot: NormalizedSnapshot): Promise<SnapshotSaveResult>;
  latest(): Promise<DashboardSnapshotV2 | null>;
  latestV1(): Promise<DashboardSnapshotV1 | null>;
  createInteraction(interaction: Interaction): Promise<void>;
  updateInteraction(
    id: string,
    state: InteractionState,
    response: string | null,
  ): Promise<Interaction | null>;
  listInteractions(): Promise<readonly Interaction[]>;
  pendingInteractions(): Promise<readonly Interaction[]>;
  appendAgentTaskEvent(event: AutonomousTaskReceipt): Promise<boolean>;
  listAgentTaskEvents(): Promise<readonly AutonomousTaskReceipt[]>;
}

export class MemorySnapshotStore implements SnapshotStore {
  private snapshot: NormalizedSnapshot | null = null;
  private readonly interactions: Interaction[] = [];
  private readonly agentTaskEvents = new MemoryAgentTaskEventStore();
  private readonly directedJobEvents = new MemoryDirectedJobEventStore();

  async save(snapshot: NormalizedSnapshot): Promise<SnapshotSaveResult> {
    let next = this.snapshot;
    const result = await saveSnapshotPair(
      {
        lock: async () => {},
        readCanonical: async () => this.snapshot?.canonical ?? null,
        writeCanonical: async () => {
          next = snapshot;
        },
        writeRollback: async () => {
          this.snapshot = next;
        },
      },
      snapshot,
    );
    return result;
  }

  async latest(): Promise<DashboardSnapshotV2 | null> {
    return this.snapshot?.canonical ?? null;
  }

  async latestV1(): Promise<DashboardSnapshotV1 | null> {
    return this.snapshot?.rollbackV1 ?? null;
  }

  async createInteraction(interaction: Interaction): Promise<void> {
    this.interactions.push(interaction);
  }

  async updateInteraction(
    id: string,
    state: InteractionState,
    response: string | null,
  ): Promise<Interaction | null> {
    const index = this.interactions.findIndex((interaction) => interaction.id === id);
    const current = this.interactions[index];
    if (current === undefined) {
      return null;
    }
    const updated = interactionSchema.parse({
      ...current,
      state,
      response,
      updated_at: new Date().toISOString(),
    });
    this.interactions[index] = updated;
    return updated;
  }

  async listInteractions(): Promise<readonly Interaction[]> {
    return [...this.interactions].sort((left, right) =>
      right.created_at.localeCompare(left.created_at),
    );
  }

  async pendingInteractions(): Promise<readonly Interaction[]> {
    return (await this.listInteractions()).filter(
      (interaction) => interaction.state === "queued" || interaction.state === "running",
    );
  }

  async appendAgentTaskEvent(event: AutonomousTaskReceipt): Promise<boolean> {
    return this.agentTaskEvents.appendAgentTaskEvent(event);
  }

  async listAgentTaskEvents(): Promise<readonly AutonomousTaskReceipt[]> {
    return this.agentTaskEvents.listAgentTaskEvents();
  }

  async appendDirectedJobEvent(event: DirectedJobEvent): Promise<boolean> {
    return this.directedJobEvents.appendDirectedJobEvent(event);
  }

  async listDirectedJobEvents(): Promise<readonly DirectedJobEvent[]> {
    return this.directedJobEvents.listDirectedJobEvents();
  }
}

type SnapshotRow = {
  readonly payload: unknown;
};

type InteractionRow = {
  readonly payload: unknown;
};

export class PostgresSnapshotStore implements SnapshotStore {
  private readonly sql: ReturnType<typeof postgres>;
  private readonly ready: Promise<void>;
  private readonly agentTaskEvents: PostgresAgentTaskEventStore;
  private readonly directedJobEvents: PostgresDirectedJobEventStore;

  constructor(databaseUrl: string) {
    this.sql = postgres(databaseUrl, {
      max: 4,
      idle_timeout: 20,
      connect_timeout: 10,
    });
    this.ready = this.initialize();
    this.agentTaskEvents = new PostgresAgentTaskEventStore(this.sql, this.ready);
    this.directedJobEvents = new PostgresDirectedJobEventStore(this.sql, this.ready);
  }

  async save(snapshot: NormalizedSnapshot): Promise<SnapshotSaveResult> {
    await this.ready;
    return this.sql.begin(async (transaction) => {
      return saveSnapshotPair(
        {
          lock: async () => {
            await transaction`SELECT pg_advisory_xact_lock(2026072602)`;
          },
          readCanonical: async () => {
            const rows = await transaction<SnapshotRow[]>`
            SELECT payload FROM dashboard_snapshots_v2 WHERE singleton_id = 2
          `;
            const row = rows[0];
            return row === undefined ? null : dashboardSnapshotV2Schema.parse(row.payload);
          },
          writeCanonical: async (canonical) => {
            await transaction`
            INSERT INTO dashboard_snapshots_v2 (singleton_id, generated_at, payload)
            VALUES (2, ${canonical.generated_at}, ${transaction.json(canonical)})
            ON CONFLICT (singleton_id) DO UPDATE SET
              generated_at = EXCLUDED.generated_at, payload = EXCLUDED.payload
          `;
          },
          writeRollback: async (rollback) => {
            await transaction`
            INSERT INTO dashboard_snapshots (singleton_id, generated_at, payload)
            VALUES (1, ${rollback.generated_at}, ${transaction.json(rollback)})
            ON CONFLICT (singleton_id) DO UPDATE SET
              generated_at = EXCLUDED.generated_at, payload = EXCLUDED.payload
          `;
          },
        },
        snapshot,
      );
    });
  }

  async latest(): Promise<DashboardSnapshotV2 | null> {
    await this.ready;
    const rows = await this.sql<SnapshotRow[]>`
      SELECT payload FROM dashboard_snapshots_v2 WHERE singleton_id = 2
    `;
    const row = rows[0];
    if (row !== undefined) {
      return dashboardSnapshotV2Schema.parse(row.payload);
    }
    const legacy = await this.latestV1();
    if (legacy === null) {
      return null;
    }
    const normalized = parseAndNormalizeSnapshot(legacy, dashboardSnapshotV1Schema);
    return normalized.ok ? normalized.value.canonical : null;
  }

  async latestV1(): Promise<DashboardSnapshotV1 | null> {
    await this.ready;
    const rows = await this.sql<SnapshotRow[]>`
      SELECT payload FROM dashboard_snapshots WHERE singleton_id = 1
    `;
    const row = rows[0];
    return row === undefined ? null : dashboardSnapshotV1Schema.parse(row.payload);
  }

  async createInteraction(interaction: Interaction): Promise<void> {
    await this.ready;
    await this.sql`
      INSERT INTO dashboard_interactions (id, state, created_at, payload)
      VALUES (
        ${interaction.id},
        ${interaction.state},
        ${interaction.created_at},
        ${this.sql.json(interaction)}
      )
    `;
  }

  async updateInteraction(
    id: string,
    state: InteractionState,
    response: string | null,
  ): Promise<Interaction | null> {
    const current = await this.interaction(id);
    if (current === null) {
      return null;
    }
    const updated = interactionSchema.parse({
      ...current,
      state,
      response,
      updated_at: new Date().toISOString(),
    });
    await this.sql`
      UPDATE dashboard_interactions
      SET state = ${updated.state}, payload = ${this.sql.json(updated)}
      WHERE id = ${id}
    `;
    return updated;
  }

  async listInteractions(): Promise<readonly Interaction[]> {
    await this.ready;
    const rows = await this.sql<InteractionRow[]>`
      SELECT payload
      FROM dashboard_interactions
      ORDER BY created_at DESC
      LIMIT 40
    `;
    return rows.map((row) => interactionSchema.parse(row.payload));
  }

  async pendingInteractions(): Promise<readonly Interaction[]> {
    await this.ready;
    const rows = await this.sql<InteractionRow[]>`
      SELECT payload
      FROM dashboard_interactions
      WHERE state IN ('queued', 'running')
      ORDER BY created_at ASC
      LIMIT 20
    `;
    return rows.map((row) => interactionSchema.parse(row.payload));
  }

  async appendAgentTaskEvent(event: AutonomousTaskReceipt): Promise<boolean> {
    return this.agentTaskEvents.appendAgentTaskEvent(event);
  }

  async listAgentTaskEvents(): Promise<readonly AutonomousTaskReceipt[]> {
    return this.agentTaskEvents.listAgentTaskEvents();
  }

  async appendDirectedJobEvent(event: DirectedJobEvent): Promise<boolean> {
    return this.directedJobEvents.appendDirectedJobEvent(event);
  }

  async listDirectedJobEvents(): Promise<readonly DirectedJobEvent[]> {
    return this.directedJobEvents.listDirectedJobEvents();
  }

  private async interaction(id: string): Promise<Interaction | null> {
    await this.ready;
    const rows = await this.sql<InteractionRow[]>`
      SELECT payload FROM dashboard_interactions WHERE id = ${id}
    `;
    const row = rows[0];
    return row === undefined ? null : interactionSchema.parse(row.payload);
  }

  private async initialize(): Promise<void> {
    await initializeDashboardStore(this.sql);
  }
}
