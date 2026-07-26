import type postgres from "postgres";
import type { DirectedJobEvent } from "./schema";
import { directedJobEventSchema } from "./schema";

export interface DirectedJobEventStore {
  appendDirectedJobEvent(event: DirectedJobEvent): Promise<boolean>;
  listDirectedJobEvents(): Promise<readonly DirectedJobEvent[]>;
}

export class MemoryDirectedJobEventStore implements DirectedJobEventStore {
  private readonly events: DirectedJobEvent[] = [];

  async appendDirectedJobEvent(event: DirectedJobEvent): Promise<boolean> {
    const exists = this.events.some(
      (candidate) =>
        candidate.interaction_id === event.interaction_id &&
        candidate.sequence === event.sequence &&
        candidate.kind === event.kind,
    );
    if (exists) return false;
    this.events.push(event);
    return true;
  }

  async listDirectedJobEvents(): Promise<readonly DirectedJobEvent[]> {
    return [...this.events].sort(
      (left, right) =>
        left.interaction_id.localeCompare(right.interaction_id) || left.sequence - right.sequence,
    );
  }
}

type DirectedJobEventRow = {
  readonly payload: unknown;
};

export class PostgresDirectedJobEventStore implements DirectedJobEventStore {
  constructor(
    private readonly sql: ReturnType<typeof postgres>,
    private readonly ready: Promise<void>,
  ) {}

  async appendDirectedJobEvent(event: DirectedJobEvent): Promise<boolean> {
    await this.ready;
    const rows = await this.sql`
      INSERT INTO dashboard_directed_job_events (interaction_id, sequence, kind, payload)
      VALUES (${event.interaction_id}, ${event.sequence}, ${event.kind}, ${this.sql.json(event)})
      ON CONFLICT (interaction_id, sequence, kind) DO NOTHING
      RETURNING interaction_id
    `;
    return rows.length === 1;
  }

  async listDirectedJobEvents(): Promise<readonly DirectedJobEvent[]> {
    await this.ready;
    const rows = await this.sql<DirectedJobEventRow[]>`
      SELECT payload
      FROM dashboard_directed_job_events
      ORDER BY created_at ASC, interaction_id ASC, sequence ASC
      LIMIT 200
    `;
    return rows.map((row) => directedJobEventSchema.parse(row.payload));
  }

  static async initialize(sql: ReturnType<typeof postgres>): Promise<void> {
    await sql`
      CREATE TABLE IF NOT EXISTS dashboard_directed_job_events (
        interaction_id UUID NOT NULL,
        sequence SMALLINT NOT NULL,
        kind TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        payload JSONB NOT NULL,
        PRIMARY KEY (interaction_id, sequence, kind)
      )
    `;
  }
}
