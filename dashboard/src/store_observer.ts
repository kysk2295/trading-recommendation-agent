import { appendFile } from "node:fs/promises";
import { isAbsolute } from "node:path";
import type {
  AutonomousTaskReceipt,
  DashboardSnapshotV1,
  DirectedJobEvent,
  Interaction,
  InteractionState,
} from "./schema";
import type { DashboardSnapshotV2 } from "./schema_v2";
import type { NormalizedSnapshot } from "./snapshot_normalizer";
import type { SnapshotSaveResult } from "./snapshot_pair_store";
import type { SnapshotStore } from "./store";

type StoreOperation =
  | "save"
  | "latest"
  | "latestV1"
  | "createInteraction"
  | "updateInteraction"
  | "listInteractions"
  | "pendingInteractions"
  | "appendAgentTaskEvent"
  | "listAgentTaskEvents"
  | "appendDirectedJobEvent"
  | "listDirectedJobEvents";

export class ObservedSnapshotStore implements SnapshotStore {
  constructor(
    private readonly delegate: SnapshotStore,
    private readonly observationLog: string,
  ) {
    if (!isAbsolute(observationLog)) {
      throw new StoreObservationError("DASHBOARD_OBSERVATION_LOG must be absolute");
    }
  }

  save(snapshot: NormalizedSnapshot): Promise<SnapshotSaveResult> {
    return this.observe("save", () => this.delegate.save(snapshot));
  }

  latest(): Promise<DashboardSnapshotV2 | null> {
    return this.observe("latest", () => this.delegate.latest());
  }

  latestV1(): Promise<DashboardSnapshotV1 | null> {
    return this.observe("latestV1", () => this.delegate.latestV1());
  }

  createInteraction(interaction: Interaction): Promise<void> {
    return this.observe("createInteraction", () => this.delegate.createInteraction(interaction));
  }

  updateInteraction(
    id: string,
    state: InteractionState,
    response: string | null,
  ): Promise<Interaction | null> {
    return this.observe("updateInteraction", () =>
      this.delegate.updateInteraction(id, state, response),
    );
  }

  listInteractions(): Promise<readonly Interaction[]> {
    return this.observe("listInteractions", () => this.delegate.listInteractions());
  }

  pendingInteractions(): Promise<readonly Interaction[]> {
    return this.observe("pendingInteractions", () => this.delegate.pendingInteractions());
  }

  appendAgentTaskEvent(event: AutonomousTaskReceipt): Promise<boolean> {
    return this.observe("appendAgentTaskEvent", () => this.delegate.appendAgentTaskEvent(event));
  }

  listAgentTaskEvents(): Promise<readonly AutonomousTaskReceipt[]> {
    return this.observe("listAgentTaskEvents", () => this.delegate.listAgentTaskEvents());
  }

  appendDirectedJobEvent(event: DirectedJobEvent): Promise<boolean> {
    return this.observe("appendDirectedJobEvent", () =>
      this.delegate.appendDirectedJobEvent(event),
    );
  }

  listDirectedJobEvents(): Promise<readonly DirectedJobEvent[]> {
    return this.observe("listDirectedJobEvents", () => this.delegate.listDirectedJobEvents());
  }

  private async observe<T>(operation: StoreOperation, action: () => Promise<T>): Promise<T> {
    await appendFile(
      this.observationLog,
      `${JSON.stringify({
        type: "store_operation",
        operation,
        observed_at: new Date().toISOString(),
        pid: process.pid,
      })}\n`,
      { encoding: "utf8", mode: 0o600 },
    );
    return action();
  }
}

class StoreObservationError extends Error {
  override readonly name = "StoreObservationError";
}
