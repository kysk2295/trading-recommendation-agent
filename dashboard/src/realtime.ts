import { z } from "zod";
import type { DashboardSnapshot } from "./schema";
import { dashboardSnapshotSchema } from "./schema";
import type { SnapshotStore } from "./store";

const publisherMessageSchema = z.strictObject({
  type: z.literal("snapshot"),
  snapshot: dashboardSnapshotSchema,
});

export const viewerMessageSchema = z.strictObject({
  type: z.literal("snapshot"),
  snapshot: dashboardSnapshotSchema,
});

export interface RealtimePeer {
  readonly raw?: unknown;
  send(message: string): void;
  close(code: number, reason: string): void;
}

export class DashboardRealtimeHub {
  private readonly viewers = new Map<unknown, RealtimePeer>();
  private publisher: RealtimePeer | null = null;
  private publisherIdentity: unknown = null;

  constructor(private readonly store: SnapshotStore) {}

  async connectViewer(peer: RealtimePeer): Promise<void> {
    this.viewers.set(identity(peer), peer);
    const latest = await this.store.latest();
    if (latest !== null) {
      send(peer, { type: "snapshot", snapshot: latest });
    }
  }

  disconnectViewer(peer: RealtimePeer): void {
    this.viewers.delete(identity(peer));
  }

  connectPublisher(peer: RealtimePeer): void {
    const nextIdentity = identity(peer);
    if (this.publisher !== null && this.publisherIdentity !== nextIdentity) {
      this.publisher.close(1008, "publisher_replaced");
    }
    this.publisher = peer;
    this.publisherIdentity = nextIdentity;
  }

  disconnectPublisher(peer: RealtimePeer): void {
    if (this.publisherIdentity === identity(peer)) {
      this.publisher = null;
      this.publisherIdentity = null;
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
    await this.store.save(payload.snapshot);
    this.broadcast({ type: "snapshot", snapshot: payload.snapshot });
  }

  broadcastSnapshot(snapshot: DashboardSnapshot): void {
    this.broadcast({ type: "snapshot", snapshot });
  }

  private broadcast(message: object): void {
    const payload = JSON.stringify(message);
    for (const viewer of this.viewers.values()) {
      viewer.send(payload);
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
