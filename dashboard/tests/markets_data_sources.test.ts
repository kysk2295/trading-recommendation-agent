import { describe, expect, test } from "bun:test";
import { dashboardSnapshotV2Schema } from "../src/schema_v2";
import { providerCoverageText, providerQuoteNotice } from "../src/workspaces/data_sources";
import { marketFactValue } from "../src/workspaces/markets";
import { marketsDataSourcesFixture } from "./e2e/markets_data_sources_fixture";

describe("Markets v2 safety", () => {
  test("Given a non-session market row, when it lacks license proofs, then it never exposes a quote", () => {
    // Given: a canonical metric that is not an authoritative market-calendar session.
    const item = { item_id: "market.us.quote", value: "999.99" };

    // When: the Markets workspace derives display text.
    const value = marketFactValue(item);

    // Then: the unproven number is withheld with an explicit reason.
    expect(value).toContain("사용 불가");
    expect(value).not.toContain("999.99");
  });
});

test("Given the Markets and Data Sources browser fixture, when it crosses the canonical boundary, then all eight providers are retained", () => {
  const snapshot = dashboardSnapshotV2Schema.parse(marketsDataSourcesFixture());

  expect(snapshot.workspaces.data_sources.capabilities).toHaveLength(8);
  expect(snapshot.workspaces.markets.items).toHaveLength(3);
});

describe("Data Sources v2 disclosure", () => {
  test("Given provider metadata without coverage or redistribution fields, when it is rendered, then the gaps stay explicit", () => {
    // Given: the normalized v2 provider capability contract.
    const coverage = providerCoverageText();
    const quoteNotice = providerQuoteNotice("realtime");

    // When: the capability details are derived.
    // Then: neither coverage nor quote redistribution is inferred.
    expect(coverage).toContain("미게시");
    expect(quoteNotice).toContain("redistribution");
  });
});
