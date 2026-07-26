import type postgres from "postgres";
import type { AutonomousTaskReceipt } from "./schema";
import { autonomousTaskReceiptSchema } from "./schema";

export interface AgentTaskEventStore {
  appendAgentTaskEvent(event: AutonomousTaskReceipt): Promise<boolean>;
  listAgentTaskEvents(): Promise<readonly AutonomousTaskReceipt[]>;
}

export class MemoryAgentTaskEventStore implements AgentTaskEventStore {
  private readonly events: AutonomousTaskReceipt[] = [];

  async appendAgentTaskEvent(event: AutonomousTaskReceipt): Promise<boolean> {
    if (this.events.some((candidate) => candidate.event_id === event.event_id)) {
      return false;
    }
    this.events.push(event);
    return true;
  }

  async listAgentTaskEvents(): Promise<readonly AutonomousTaskReceipt[]> {
    return [...this.events].sort(
      (left, right) =>
        left.occurred_at.localeCompare(right.occurred_at) || left.sequence - right.sequence,
    );
  }
}

type AgentTaskEventRow = {
  readonly payload: unknown;
};

export class PostgresAgentTaskEventStore implements AgentTaskEventStore {
  private readonly sql: ReturnType<typeof postgres>;
  private readonly ready: Promise<void>;

  constructor(sql: ReturnType<typeof postgres>, ready: Promise<void>) {
    this.sql = sql;
    this.ready = ready;
  }

  async appendAgentTaskEvent(event: AutonomousTaskReceipt): Promise<boolean> {
    await this.ready;
    const rows = await this.sql`
      INSERT INTO dashboard_agent_task_events (event_id, occurred_at, payload)
      VALUES (${event.event_id}, ${event.occurred_at}, ${this.sql.json(event)})
      ON CONFLICT (event_id) DO NOTHING
      RETURNING event_id
    `;
    return rows.length === 1;
  }

  async listAgentTaskEvents(): Promise<readonly AutonomousTaskReceipt[]> {
    await this.ready;
    const rows = await this.sql<AgentTaskEventRow[]>`
      SELECT payload
      FROM dashboard_agent_task_events
      ORDER BY occurred_at ASC, event_id ASC
      LIMIT 200
    `;
    return rows.map((row) => autonomousTaskReceiptSchema.parse(row.payload));
  }

  static async initialize(sql: ReturnType<typeof postgres>): Promise<void> {
    await sql`
      CREATE TABLE IF NOT EXISTS dashboard_agent_task_events (
        event_id TEXT PRIMARY KEY,
        occurred_at TIMESTAMPTZ NOT NULL,
        payload JSONB NOT NULL
      )
    `;
  }
}
