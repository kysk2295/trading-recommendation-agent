import { describe, expect, test } from "bun:test";
import { createApp } from "../src/app";
import { MemorySnapshotStore } from "../src/store";
import {
  type PaperEvidencePath,
  type PaperLedgerItem,
  paperLedgerPresentation,
} from "../src/workspaces/paper";

const paperTrace: PaperEvidencePath = {
  status: "resolved",
  nodes: [{ kind: "paper_receipt", state: "accepted" }],
};

describe("Paper finalized ledger presentation", () => {
  test("shows a finalized value only with a Paper terminal", () => {
    // Given
    const item: PaperLedgerItem = {
      item_id: "paper.daily_pnl",
      state: "populated",
      value: "104.75",
      observed_at: "2026-07-25T20:05:00Z",
    };

    // When
    const presentation = paperLedgerPresentation(item, paperTrace, "populated");

    // Then
    expect(presentation).toEqual({ verified: true, value: "104.75" });
  });

  test("never presents incomplete verification as live or verified", () => {
    // Given
    const item: PaperLedgerItem = {
      item_id: "paper.daily_pnl",
      state: "populated",
      value: "104.75",
      observed_at: "2026-07-25T20:05:00Z",
    };

    // When
    const presentation = paperLedgerPresentation(item, paperTrace, "blocked");

    // Then
    expect(presentation.verified).toBeFalse();
    expect(presentation.value).toBe("사용 불가 · finalized Paper verification 없음");
  });

  test("public Paper and provider mutation routes do not exist", async () => {
    // Given
    const store = new MemorySnapshotStore();
    const app = createApp(
      store,
      "ingest-token-with-adequate-length",
      "operator-token-with-adequate-length",
    );

    // When
    const responses = await Promise.all(
      ["/api/paper/orders", "/api/paper/cancel", "/api/provider/order"].map((path) =>
        app.request(path, { method: "POST", body: "{}" }),
      ),
    );

    // Then
    expect(responses.map((response) => response.status)).toEqual([404, 404, 404]);
    expect(await store.latest()).toBeNull();
  });
});
