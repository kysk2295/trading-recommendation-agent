import postgres from "postgres";
import type { DashboardSnapshot, Interaction, InteractionState } from "./schema";
import { dashboardSnapshotSchema, interactionSchema } from "./schema";

export interface SnapshotStore {
  save(snapshot: DashboardSnapshot): Promise<void>;
  latest(): Promise<DashboardSnapshot | null>;
  createInteraction(interaction: Interaction): Promise<void>;
  updateInteraction(
    id: string,
    state: InteractionState,
    response: string | null,
  ): Promise<Interaction | null>;
  listInteractions(): Promise<readonly Interaction[]>;
  pendingInteractions(): Promise<readonly Interaction[]>;
}

export class MemorySnapshotStore implements SnapshotStore {
  private snapshot: DashboardSnapshot | null = null;
  private readonly interactions: Interaction[] = [];

  async save(snapshot: DashboardSnapshot): Promise<void> {
    this.snapshot = snapshot;
  }

  async latest(): Promise<DashboardSnapshot | null> {
    return this.snapshot;
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

  constructor(databaseUrl: string) {
    this.sql = postgres(databaseUrl, {
      max: 4,
      idle_timeout: 20,
      connect_timeout: 10,
    });
    this.ready = this.initialize();
  }

  async save(snapshot: DashboardSnapshot): Promise<void> {
    await this.ready;
    await this.sql`
      INSERT INTO dashboard_snapshots (singleton_id, generated_at, payload)
      VALUES (1, ${snapshot.generated_at}, ${this.sql.json(snapshot)})
      ON CONFLICT (singleton_id) DO UPDATE SET
        generated_at = EXCLUDED.generated_at,
        payload = EXCLUDED.payload
    `;
  }

  async latest(): Promise<DashboardSnapshot | null> {
    await this.ready;
    const rows = await this.sql<SnapshotRow[]>`
      SELECT payload FROM dashboard_snapshots WHERE singleton_id = 1
    `;
    const row = rows[0];
    if (row === undefined) {
      return null;
    }
    return dashboardSnapshotSchema.parse(row.payload);
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

  private async interaction(id: string): Promise<Interaction | null> {
    await this.ready;
    const rows = await this.sql<InteractionRow[]>`
      SELECT payload FROM dashboard_interactions WHERE id = ${id}
    `;
    const row = rows[0];
    return row === undefined ? null : interactionSchema.parse(row.payload);
  }

  private async initialize(): Promise<void> {
    await this.sql`
      CREATE TABLE IF NOT EXISTS dashboard_snapshots (
        singleton_id SMALLINT PRIMARY KEY CHECK (singleton_id = 1),
        generated_at TIMESTAMPTZ NOT NULL,
        payload JSONB NOT NULL
      )
    `;
    await this.sql`
      CREATE TABLE IF NOT EXISTS dashboard_interactions (
        id UUID PRIMARY KEY,
        state TEXT NOT NULL,
        created_at TEXT NOT NULL,
        payload JSONB NOT NULL
      )
    `;
  }
}
