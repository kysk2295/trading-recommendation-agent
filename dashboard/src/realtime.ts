import { z } from "zod";
import { PairingTickets } from "./operator_auth";
import type { Interaction } from "./schema";
import {
  autonomousTaskEventSchema,
  dashboardSnapshotV1Schema,
  directedJobEventSchema,
  interactionStateSchema,
} from "./schema";
import type { DashboardSnapshotV2 } from "./schema_v2";
import { parseAndNormalizeSnapshot } from "./snapshot_normalizer";
import type { SnapshotStore } from "./store";

const publisherMessageSchema = z.discriminatedUnion("type", [
  z.strictObject({
    type: z.literal("snapshot"),
    snapshot: z.unknown(),
  }),
  z.strictObject({
    type: z.literal("interaction_result"),
    interaction_id: z.uuid(),
    state: interactionStateSchema.exclude(["queued"]),
    response: z.string().max(8_000).nullable(),
  }),
  z.strictObject({
    type: z.literal("pairing_request"),
  }),
  autonomousTaskEventSchema,
  directedJobEventSchema,
]);

export interface RealtimePeer {
  readonly raw?: unknown;
  send(message: string): void;
  close(code: number, reason: string): void;
}

export class DashboardRealtimeHub {
  private readonly viewers = new Map<unknown, RealtimePeer>();
  private readonly operators = new Map<unknown, RealtimePeer>();
  private publisher: RealtimePeer | null = null;
  private publisherIdentity: unknown = null;

  constructor(
    private readonly store: SnapshotStore,
    private readonly pairingTickets = new PairingTickets(),
  ) {}

  async connectViewer(peer: RealtimePeer): Promise<void> {
    this.viewers.set(identity(peer), peer);
    const latest = await this.store.latest();
    if (latest !== null) {
      send(peer, { type: "snapshot", snapshot: latest });
    }
    for (const task of await this.store.listAgentTaskEvents()) {
      send(peer, { type: "agent_task_event", task });
    }
  }

  disconnectViewer(peer: RealtimePeer): void {
    this.viewers.delete(identity(peer));
  }

  async connectOperator(peer: RealtimePeer): Promise<void> {
    this.operators.set(identity(peer), peer);
    for (const interaction of await this.store.listInteractions()) {
      send(peer, { type: "interaction", interaction });
    }
    for (const task of await this.store.listAgentTaskEvents()) {
      send(peer, { type: "agent_task_event", task });
    }
    for (const event of await this.store.listDirectedJobEvents()) {
      send(peer, event);
    }
  }

  disconnectOperator(peer: RealtimePeer): void {
    this.operators.delete(identity(peer));
  }

  connectPublisher(peer: RealtimePeer): void {
    const nextIdentity = identity(peer);
    if (this.publisher !== null && this.publisherIdentity !== nextIdentity) {
      this.publisher.close(1008, "publisher_replaced");
      void this.failRunningInteractions();
    }
    this.publisher = peer;
    this.publisherIdentity = nextIdentity;
    void this.deliverPending(peer);
  }

  async disconnectPublisher(peer: RealtimePeer): Promise<void> {
    if (this.publisherIdentity === identity(peer)) {
      this.publisher = null;
      this.publisherIdentity = null;
      await this.failRunningInteractions();
    }
  }

  async handlePublisherMessage(peer: RealtimePeer, raw: string): Promise<void> {
    if (identity(peer) !== this.publisherIdentity) {
      peer.close(1008, "publisher_not_active");
      return;
    }
    const payload = parsePublisherMessage(raw);
    if (payload === null) {
      peer.close(1003, "invalid_message");
      return;
    }
    switch (payload.type) {
      case "snapshot":
        {
          const normalized = parseAndNormalizeSnapshot(payload.snapshot, dashboardSnapshotV1Schema);
          if (!normalized.ok) {
            peer.close(1003, "invalid_message");
            return;
          }
          const saved = await this.store.save(normalized.value);
          if (saved === "stale") {
            peer.close(1008, "stale_snapshot");
            return;
          }
          this.broadcast(this.viewers, {
            type: "snapshot",
            snapshot: normalized.value.canonical,
          });
        }
        return;
      case "interaction_result": {
        const updated = await this.store.updateInteraction(
          payload.interaction_id,
          payload.state,
          payload.response,
        );
        if (updated !== null) {
          this.broadcast(this.operators, { type: "interaction", interaction: updated });
        }
        return;
      }
      case "pairing_request": {
        const ticket = this.pairingTickets.issue();
        send(peer, { type: "pairing_ticket", path: `/operator/pair/${ticket}` });
        return;
      }
      case "agent_task_event": {
        const created = await this.store.appendAgentTaskEvent(payload.task);
        if (created) {
          this.broadcast(this.viewers, payload);
          this.broadcast(this.operators, payload);
        }
        return;
      }
      case "directed_job_event": {
        const created = await this.store.appendDirectedJobEvent(payload);
        if (created) {
          this.broadcast(this.operators, payload);
        }
        return;
      }
    }
  }

  broadcastSnapshot(snapshot: DashboardSnapshotV2): void {
    this.broadcast(this.viewers, { type: "snapshot", snapshot });
  }

  async queueInteraction(interaction: Interaction): Promise<void> {
    await this.store.createInteraction(interaction);
    this.broadcast(this.operators, { type: "interaction", interaction });
    if (this.publisher !== null) {
      send(this.publisher, { type: "interaction", interaction });
    }
  }

  private broadcast(peers: ReadonlyMap<unknown, RealtimePeer>, message: object): void {
    const payload = JSON.stringify(message);
    for (const peer of peers.values()) {
      peer.send(payload);
    }
  }

  private async deliverPending(peer: RealtimePeer): Promise<void> {
    for (const interaction of await this.store.pendingInteractions()) {
      if (this.publisherIdentity !== identity(peer)) {
        return;
      }
      if (interaction.state === "queued") {
        send(peer, { type: "interaction", interaction });
      }
    }
  }

  private async failRunningInteractions(): Promise<void> {
    for (const interaction of await this.store.pendingInteractions()) {
      if (interaction.state !== "running") {
        continue;
      }
      const updated = await this.store.updateInteraction(
        interaction.id,
        "uncertain",
        "publisher 연결이 끊겨 실행 결과를 확정할 수 없습니다.",
      );
      if (updated !== null) {
        this.broadcast(this.operators, { type: "interaction", interaction: updated });
      }
    }
  }
}

function parsePublisherMessage(raw: string): z.infer<typeof publisherMessageSchema> | null {
  try {
    const parsed = publisherMessageSchema.safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : null;
  } catch (error: unknown) {
    if (error instanceof SyntaxError) {
      return null;
    }
    throw error;
  }
}

function send(peer: RealtimePeer, message: object): void {
  peer.send(JSON.stringify(message));
}

function identity(peer: RealtimePeer): unknown {
  return peer.raw ?? peer;
}
