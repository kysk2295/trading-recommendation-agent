import { websocket } from "hono/bun";
import { createApp } from "./app";
import type { SnapshotStore } from "./store";
import { MemorySnapshotStore, PostgresSnapshotStore } from "./store";

class ServerConfigurationError extends Error {
  override readonly name = "ServerConfigurationError";
}

const ingestToken = requiredEnvironment("DASHBOARD_INGEST_TOKEN");
const databaseUrl = process.env["DATABASE_URL"];
const store: SnapshotStore =
  databaseUrl === undefined ? new MemorySnapshotStore() : new PostgresSnapshotStore(databaseUrl);
const app = createApp(store, ingestToken);
const parsedPort = Number.parseInt(process.env["PORT"] ?? "3000", 10);

if (!Number.isInteger(parsedPort) || parsedPort < 1 || parsedPort > 65_535) {
  throw new ServerConfigurationError("PORT must be an integer from 1 to 65535");
}

function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (value === undefined || value.length === 0) {
    throw new ServerConfigurationError(`${name} is required`);
  }
  return value;
}

export default {
  port: parsedPort,
  fetch: app.fetch,
  websocket,
};
