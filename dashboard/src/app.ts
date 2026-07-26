import { randomUUID, timingSafeEqual } from "node:crypto";
import { Hono } from "hono";
import { bodyLimit } from "hono/body-limit";
import { serveStatic, upgradeWebSocket } from "hono/bun";
import { secureHeaders } from "hono/secure-headers";
import {
  clearOperatorCookie,
  operatorAuthorized,
  PairingTickets,
  setOperatorCookie,
} from "./operator_auth";
import { DashboardRealtimeHub } from "./realtime";
import {
  agentIdSchema,
  dashboardSnapshotV1Schema,
  interactionCreateSchema,
  interactionSchema,
} from "./schema";
import { parseAndNormalizeSnapshot } from "./snapshot_normalizer";
import type { SnapshotStore } from "./store";

const MAX_SNAPSHOT_BYTES = 256 * 1024;

export function createApp(
  store: SnapshotStore,
  ingestToken: string,
  operatorToken: string,
  pairingTickets = new PairingTickets(),
): Hono {
  requireToken(ingestToken, "ingest");
  requireToken(operatorToken, "operator");
  const app = new Hono();
  const realtime = new DashboardRealtimeHub(store, pairingTickets);
  app.get(
    "/api/realtime/view",
    upgradeWebSocket(() => ({
      onOpen: (_event, socket) => {
        void realtime.connectViewer(socket);
      },
      onClose: (_event, socket) => {
        realtime.disconnectViewer(socket);
      },
      onError: (_event, socket) => {
        realtime.disconnectViewer(socket);
      },
    })),
  );
  app.get(
    "/api/realtime/operator",
    async (context, next) => {
      if (!operatorAuthorized(context, operatorToken)) {
        return context.json({ error: "unauthorized" }, 401);
      }
      await next();
    },
    upgradeWebSocket(() => ({
      onOpen: (_event, socket) => {
        void realtime.connectOperator(socket);
      },
      onClose: (_event, socket) => {
        realtime.disconnectOperator(socket);
      },
      onError: (_event, socket) => {
        realtime.disconnectOperator(socket);
      },
    })),
  );
  app.get(
    "/api/realtime/publish",
    async (context, next) => {
      if (!authorized(context.req.header("authorization"), ingestToken)) {
        return context.json({ error: "unauthorized" }, 401);
      }
      await next();
    },
    upgradeWebSocket(() => ({
      onOpen: (_event, socket) => {
        realtime.connectPublisher(socket);
      },
      onMessage: (event, socket) => {
        if (typeof event.data !== "string") {
          socket.close(1003, "text_messages_only");
          return;
        }
        void realtime.handlePublisherMessage(socket, event.data);
      },
      onClose: (_event, socket) => {
        void realtime.disconnectPublisher(socket);
      },
      onError: (_event, socket) => {
        void realtime.disconnectPublisher(socket);
      },
    })),
  );
  app.use(
    "*",
    secureHeaders({
      contentSecurityPolicy: {
        defaultSrc: ["'self'"],
        connectSrc: ["'self'"],
        fontSrc: ["'self'"],
        frameAncestors: ["'none'"],
        imgSrc: ["'self'", "data:"],
        scriptSrc: ["'self'"],
        styleSrc: ["'self'"],
      },
      crossOriginResourcePolicy: "same-origin",
      referrerPolicy: "no-referrer",
    }),
  );
  app.get("/api/health", (context) => context.json({ ok: true }));
  app.get("/api/operator/session", (context) =>
    context.json({
      authenticated: operatorAuthorized(context, operatorToken),
    }),
  );
  app.post("/api/operator/session", (context) => {
    if (!authorized(context.req.header("authorization"), operatorToken)) {
      return context.json({ error: "unauthorized" }, 401);
    }
    setOperatorCookie(context, operatorToken);
    return context.body(null, 204);
  });
  app.delete("/api/operator/session", (context) => {
    clearOperatorCookie(context);
    return context.body(null, 204);
  });
  app.get("/operator/pair/:ticket", (context) => {
    if (!pairingTickets.consume(context.req.param("ticket"))) {
      return context.notFound();
    }
    setOperatorCookie(context, operatorToken);
    return context.redirect("/#command-center");
  });
  app.get("/api/operator/interactions", async (context) => {
    if (!operatorAuthorized(context, operatorToken)) {
      return context.json({ error: "unauthorized" }, 401);
    }
    return context.json({ interactions: await store.listInteractions() });
  });
  app.post("/api/agents/:agentId/interactions", async (context) => {
    if (!operatorAuthorized(context, operatorToken)) {
      return context.json({ error: "unauthorized" }, 401);
    }
    const agentId = agentIdSchema.safeParse(context.req.param("agentId"));
    if (!agentId.success) {
      return context.json({ error: "invalid_agent" }, 404);
    }
    let payload: unknown;
    try {
      payload = await context.req.json();
    } catch (error: unknown) {
      if (error instanceof SyntaxError || error instanceof TypeError) {
        return context.json({ error: "invalid_json" }, 400);
      }
      throw error;
    }
    const input = interactionCreateSchema.safeParse(payload);
    if (!input.success) {
      return context.json({ error: "invalid_command" }, 400);
    }
    const now = new Date().toISOString();
    const interaction = interactionSchema.parse({
      id: randomUUID(),
      agent_id: agentId.data,
      mode: input.data.mode,
      command: input.data.command,
      state: "queued",
      response: null,
      created_at: now,
      updated_at: now,
    });
    await realtime.queueInteraction(interaction);
    return context.json({ interaction }, 202);
  });
  app.post(
    "/api/ingest",
    bodyLimit({
      maxSize: MAX_SNAPSHOT_BYTES,
      onError: (context) => context.json({ error: "payload_too_large" }, 413),
    }),
    async (context) => {
      if (!authorized(context.req.header("authorization"), ingestToken)) {
        return context.json({ error: "unauthorized" }, 401);
      }
      let payload: unknown;
      try {
        payload = await context.req.json();
      } catch (error: unknown) {
        if (error instanceof SyntaxError || error instanceof TypeError) {
          return context.json({ error: "invalid_json" }, 400);
        }
        throw error;
      }
      const parsed = parseAndNormalizeSnapshot(payload, dashboardSnapshotV1Schema);
      if (!parsed.ok) {
        return context.json({ error: "invalid_snapshot" }, 400);
      }
      const saved = await store.save(parsed.value);
      if (saved === "stale") {
        return context.json({ error: "stale_snapshot" }, 409);
      }
      realtime.broadcastSnapshot(parsed.value.canonical);
      return context.json({ accepted: true }, 202);
    },
  );
  app.get("/api/snapshot", async (context) => {
    const snapshot = await store.latest();
    if (snapshot === null) {
      return context.json({ error: "snapshot_unavailable" }, 404);
    }
    context.header("cache-control", "no-store");
    return context.json(snapshot);
  });
  app.use("/assets/*", serveStatic({ root: "./public" }));
  app.get("/showcase", serveStatic({ path: "./public/showcase.html" }));
  app.all("/api/*", (context) => context.json({ error: "not_found" }, 404));
  app.get("*", serveStatic({ path: "./public/index.html" }));
  return app;
}

function authorized(header: string | undefined, expected: string): boolean {
  if (header === undefined || !header.startsWith("Bearer ")) {
    return false;
  }
  const presented = Buffer.from(header.slice(7));
  const target = Buffer.from(expected);
  return presented.length === target.length && timingSafeEqual(presented, target);
}

function requireToken(token: string, name: string): void {
  if (token.length < 24) {
    throw new ConfigurationError(`dashboard ${name} token must be at least 24 characters`);
  }
}

class ConfigurationError extends Error {
  override readonly name = "ConfigurationError";
}
