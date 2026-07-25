import postgres from "postgres";
import type { DashboardSnapshot } from "./schema";
import { dashboardSnapshotSchema } from "./schema";

export interface SnapshotStore {
  save(snapshot: DashboardSnapshot): Promise<void>;
  latest(): Promise<DashboardSnapshot | null>;
}

export class MemorySnapshotStore implements SnapshotStore {
  private snapshot: DashboardSnapshot | null = null;

  async save(snapshot: DashboardSnapshot): Promise<void> {
    this.snapshot = snapshot;
  }

  async latest(): Promise<DashboardSnapshot | null> {
    return this.snapshot;
  }
}

type SnapshotRow = {
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

  private async initialize(): Promise<void> {
    await this.sql`
      CREATE TABLE IF NOT EXISTS dashboard_snapshots (
        singleton_id SMALLINT PRIMARY KEY CHECK (singleton_id = 1),
        generated_at TIMESTAMPTZ NOT NULL,
        payload JSONB NOT NULL
      )
    `;
  }
}
