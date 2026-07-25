import { timingSafeEqual } from "node:crypto";
import { Hono } from "hono";
import { bodyLimit } from "hono/body-limit";
import { serveStatic } from "hono/bun";
import { secureHeaders } from "hono/secure-headers";
import { dashboardSnapshotSchema } from "./schema";
import type { SnapshotStore } from "./store";

const MAX_SNAPSHOT_BYTES = 256 * 1024;

export function createApp(store: SnapshotStore, ingestToken: string, viewToken: string): Hono {
  if (ingestToken === viewToken || ingestToken.length < 24 || viewToken.length < 24) {
    throw new ConfigurationError("dashboard tokens must be distinct and at least 24 characters");
  }
  const app = new Hono();
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
      const parsed = dashboardSnapshotSchema.safeParse(payload);
      if (!parsed.success) {
        return context.json({ error: "invalid_snapshot" }, 400);
      }
      await store.save(parsed.data);
      return context.json({ accepted: true }, 202);
    },
  );
  app.get("/api/snapshot", async (context) => {
    if (!authorized(context.req.header("authorization"), viewToken)) {
      return context.json({ error: "unauthorized" }, 401);
    }
    const snapshot = await store.latest();
    if (snapshot === null) {
      return context.json({ error: "snapshot_unavailable" }, 404);
    }
    context.header("cache-control", "no-store");
    return context.json(snapshot);
  });
  app.use("/assets/*", serveStatic({ root: "./public" }));
  app.get("/showcase", serveStatic({ path: "./public/showcase.html" }));
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

class ConfigurationError extends Error {
  override readonly name = "ConfigurationError";
}
