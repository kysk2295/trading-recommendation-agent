import { describe, expect, test } from "bun:test";
import type { DashboardSnapshotV1 } from "../src/schema";
import { dashboardSnapshotV1Schema } from "../src/schema";
import type { DashboardSnapshotV2 } from "../src/schema_v2";
import { parseAndNormalizeSnapshot } from "../src/snapshot_normalizer";
import { type SnapshotPairTransaction, saveSnapshotPair } from "../src/snapshot_pair_store";
import { snapshotV2 } from "./snapshot_v2_fixture";

class FakeTransaction implements SnapshotPairTransaction {
  readonly events: string[] = [];
  durableCanonical: DashboardSnapshotV2 | null = null;
  durableRollback: DashboardSnapshotV1 | null = null;
  stagedCanonical: DashboardSnapshotV2 | null = null;
  stagedRollback: DashboardSnapshotV1 | null = null;

  constructor(
    readonly current: DashboardSnapshotV2 | null,
    readonly failRollback = false,
  ) {}

  async lock(): Promise<void> {
    this.events.push("lock");
  }

  async readCanonical(): Promise<DashboardSnapshotV2 | null> {
    this.events.push("read");
    return this.current;
  }

  async writeCanonical(snapshot: DashboardSnapshotV2): Promise<void> {
    this.events.push("write_v2");
    this.stagedCanonical = snapshot;
  }

  async writeRollback(snapshot: DashboardSnapshotV1): Promise<void> {
    this.events.push("write_v1");
    if (this.failRollback) throw new FakeRollbackError();
    this.stagedRollback = snapshot;
  }

  commit(): void {
    if (this.stagedCanonical === null || this.stagedRollback === null) {
      throw new FakeCommitError();
    }
    this.durableCanonical = this.stagedCanonical;
    this.durableRollback = this.stagedRollback;
  }

  rollback(): void {
    this.stagedCanonical = null;
    this.stagedRollback = null;
  }
}

class FakeRollbackError extends Error {
  override readonly name = "FakeRollbackError";
}

class FakeCommitError extends Error {
  override readonly name = "FakeCommitError";
}

async function runTransaction(
  transaction: FakeTransaction,
  normalized: ReturnType<typeof parseAndNormalizeSnapshot> & { readonly ok: true },
) {
  try {
    const result = await saveSnapshotPair(transaction, normalized.value);
    transaction.commit();
    return result;
  } catch (error) {
    transaction.rollback();
    throw error;
  }
}

describe("snapshot pair transaction adapter", () => {
  test("locks before reading and stages both versions in one transaction", async () => {
    const normalized = parseAndNormalizeSnapshot(snapshotV2, dashboardSnapshotV1Schema);
    expect(normalized.ok).toBe(true);
    if (!normalized.ok) return;
    const transaction = new FakeTransaction(null);

    const result = await runTransaction(transaction, normalized);

    expect(result).toBe("saved");
    expect(transaction.events).toEqual(["lock", "read", "write_v2", "write_v1"]);
    expect(transaction.durableCanonical).toEqual(normalized.value.canonical);
    expect(transaction.durableRollback).toEqual(normalized.value.rollbackV1);
  });

  test("surfaces paired-write failure before a transaction can commit", async () => {
    const normalized = parseAndNormalizeSnapshot(snapshotV2, dashboardSnapshotV1Schema);
    expect(normalized.ok).toBe(true);
    if (!normalized.ok) return;
    const transaction = new FakeTransaction(null, true);

    const operation = runTransaction(transaction, normalized);

    await expect(operation).rejects.toBeInstanceOf(FakeRollbackError);
    expect(transaction.events).toEqual(["lock", "read", "write_v2", "write_v1"]);
    expect(transaction.stagedCanonical).toBeNull();
    expect(transaction.stagedRollback).toBeNull();
    expect(transaction.durableCanonical).toBeNull();
    expect(transaction.durableRollback).toBeNull();
  });
});
