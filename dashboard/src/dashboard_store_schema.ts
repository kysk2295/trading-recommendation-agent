import type postgres from "postgres";
import { PostgresAgentTaskEventStore } from "./agent_task_event_store";
import { PostgresDirectedJobEventStore } from "./directed_job_event_store";

export async function initializeDashboardStore(sql: ReturnType<typeof postgres>): Promise<void> {
  await sql`
    CREATE TABLE IF NOT EXISTS dashboard_snapshots (
      singleton_id SMALLINT PRIMARY KEY CHECK (singleton_id = 1),
      generated_at TIMESTAMPTZ NOT NULL,
      payload JSONB NOT NULL
    )
  `;
  await sql`
    CREATE TABLE IF NOT EXISTS dashboard_interactions (
      id UUID PRIMARY KEY,
      state TEXT NOT NULL,
      created_at TEXT NOT NULL,
      payload JSONB NOT NULL
    )
  `;
  await sql`
    CREATE TABLE IF NOT EXISTS dashboard_snapshots_v2 (
      singleton_id SMALLINT PRIMARY KEY CHECK (singleton_id = 2),
      generated_at TIMESTAMPTZ NOT NULL,
      payload JSONB NOT NULL
    )
  `;
  await PostgresAgentTaskEventStore.initialize(sql);
  await PostgresDirectedJobEventStore.initialize(sql);
}
