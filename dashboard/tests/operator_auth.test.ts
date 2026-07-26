import { describe, expect, test } from "bun:test";
import { PairingTickets } from "../src/operator_auth";

describe("operator device pairing", () => {
  test("consumes an issued ticket exactly once", () => {
    const tickets = new PairingTickets(60_000);

    const ticket = tickets.issue();

    expect(tickets.consume(ticket)).toBe(true);
    expect(tickets.consume(ticket)).toBe(false);
  });

  test("rejects an expired ticket", () => {
    let now = 1_000;
    const tickets = new PairingTickets(50, () => now);
    const ticket = tickets.issue();

    now = 1_051;

    expect(tickets.consume(ticket)).toBe(false);
  });
});
