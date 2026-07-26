import { randomBytes, timingSafeEqual } from "node:crypto";
import type { Context } from "hono";
import { deleteCookie, getCookie, setCookie } from "hono/cookie";

const DEFAULT_TICKET_TTL_MS = 2 * 60 * 1_000;
const OPERATOR_COOKIE = "tra_operator";
const OPERATOR_SESSION_SECONDS = 60 * 60 * 24 * 30;

export class PairingTickets {
  private readonly expirations = new Map<string, number>();

  constructor(
    private readonly ttlMs = DEFAULT_TICKET_TTL_MS,
    private readonly now: () => number = Date.now,
  ) {}

  issue(): string {
    this.removeExpired();
    const ticket = randomBytes(32).toString("base64url");
    this.expirations.set(ticket, this.now() + this.ttlMs);
    return ticket;
  }

  consume(ticket: string): boolean {
    const expiresAt = this.expirations.get(ticket);
    this.expirations.delete(ticket);
    return expiresAt !== undefined && expiresAt >= this.now();
  }

  private removeExpired(): void {
    const now = this.now();
    for (const [ticket, expiresAt] of this.expirations.entries()) {
      if (expiresAt < now) {
        this.expirations.delete(ticket);
      }
    }
  }
}

export function operatorAuthorized(context: Context, expected: string): boolean {
  const value = getCookie(context, OPERATOR_COOKIE);
  if (value === undefined) {
    return false;
  }
  const presentedBytes = Buffer.from(value);
  const expectedBytes = Buffer.from(expected);
  return (
    presentedBytes.length === expectedBytes.length && timingSafeEqual(presentedBytes, expectedBytes)
  );
}

export function setOperatorCookie(context: Context, operatorToken: string): void {
  setCookie(context, OPERATOR_COOKIE, operatorToken, {
    httpOnly: true,
    maxAge: OPERATOR_SESSION_SECONDS,
    path: "/",
    sameSite: "Strict",
    secure: true,
  });
}

export function clearOperatorCookie(context: Context): void {
  deleteCookie(context, OPERATOR_COOKIE, { path: "/", secure: true });
}
