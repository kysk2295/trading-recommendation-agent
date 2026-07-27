import { describe, expect, test } from "bun:test";
import { dashboardSnapshotV2Schema } from "../src/schema_v2";
import {
  type ProviderEvidencePath,
  providerCoverageText,
  providerEvidencePresentation,
  providerQuoteNotice,
} from "../src/workspaces/data_sources";
import {
  krRealtimeCyclePresentation,
  type MarketEvidencePath,
  marketEvidencePresentation,
} from "../src/workspaces/markets";
import { marketsDataSourcesFixture } from "./e2e/markets_data_sources_fixture";

describe("Markets v2 safety", () => {
  test("Given an authoritative KR realtime cycle value, when it is rendered, then exact counts survive without quote inference", () => {
    expect(
      krRealtimeCyclePresentation("records=190;coverage=1/4;cycle=kr-m3-live-20260727-fbcb34d-060"),
    ).toEqual({
      records: 190,
      successfulSources: 1,
      totalSources: 4,
      cycleId: "kr-m3-live-20260727-fbcb34d-060",
    });
    expect(krRealtimeCyclePresentation("records=190;coverage=5/4;cycle=invalid")).toBeNull();
    expect(krRealtimeCyclePresentation("999.99")).toBeNull();
  });

  test("Given a non-session market row, when it lacks license proofs, then it never exposes a quote", () => {
    // Given: a canonical metric that is not an authoritative market-calendar session.
    const item = {
      item_id: "market.us.quote",
      label: "US current quote",
      state: "populated" as const,
      value: "999.99",
      observed_at: "2026-07-26T03:00:00Z",
      trace_id: "trace.markets.calendar.us",
    };

    // When: the Markets workspace derives display text.
    const value = marketEvidencePresentation(item, acceptedMarketPath()).value;

    // Then: the unproven number is withheld with an explicit reason.
    expect(value).toContain("사용 불가");
    expect(value).not.toContain("999.99");
  });

  test("Given a quote-shaped session value, when its calendar semantics are invalid, then it fails closed", () => {
    const display = marketEvidencePresentation(
      {
        item_id: "market.us.session",
        label: "US session",
        state: "populated",
        value: "999.99",
        observed_at: "2026-07-26T03:00:00Z",
        trace_id: "trace.markets.calendar.us",
      },
      acceptedMarketPath(),
    );

    expect(display.state).toBe("unavailable");
    expect(display.value).not.toContain("999.99");
  });

  test("Given every canonical state, when calendar evidence is rendered, then only populated and stale sessions retain their semantic values", () => {
    const states = [
      "loading",
      "empty",
      "error",
      "blocked",
      "unavailable",
      "corrupt",
      "stale",
      "populated",
    ] as const;
    const values = states.map((state) =>
      marketEvidencePresentation(
        {
          item_id: "market.us.session",
          label: "US session",
          state,
          value: "closed",
          observed_at: state === "unavailable" ? null : "2026-07-26T03:00:00Z",
          trace_id: "trace.markets.calendar.us",
        },
        acceptedMarketPath(),
      ),
    );

    expect(values.map((value) => value.state)).toEqual([
      "unavailable",
      "unavailable",
      "unavailable",
      "unavailable",
      "unavailable",
      "unavailable",
      "stale",
      "populated",
    ]);
  });
});

test("Given the Markets and Data Sources browser fixture, when it crosses the canonical boundary, then all eight providers are retained", () => {
  const snapshot = dashboardSnapshotV2Schema.parse(marketsDataSourcesFixture());

  expect(snapshot.workspaces.data_sources.capabilities).toHaveLength(8);
  expect(snapshot.workspaces.markets.items).toHaveLength(3);
  expect(
    new Set(snapshot.workspaces.data_sources.capabilities.map((capability) => capability.trace_id))
      .size,
  ).toBe(8);
});

describe("Data Sources v2 disclosure", () => {
  test("Given provider metadata without coverage or redistribution fields, when it is rendered, then the gaps stay explicit", () => {
    // Given: the normalized v2 provider capability contract.
    const coverage = providerCoverageText("FRED");
    const quoteNotice = providerQuoteNotice("realtime");

    // When: the capability details are derived.
    // Then: neither coverage nor quote redistribution is inferred.
    expect(coverage).toContain("미게시");
    expect(quoteNotice).toContain("redistribution");
  });

  test("Given a shared or missing capability trace, when provider evidence is derived, then it is blocked per provider", () => {
    const capability = {
      capability_id: "fred.authoritative",
      provider: "fred" as const,
      label: "FRED",
      state: "populated" as const,
      entitlement: "realtime" as const,
      observed_at: "2026-07-26T03:00:00Z",
      trace_id: "trace.shared",
    };
    const shared = providerEvidencePresentation(
      "fred",
      capability,
      acceptedProviderPath("trace.shared", "provider.fred"),
      ["trace.shared", "trace.shared"],
    );
    const missing = providerEvidencePresentation("fred", undefined, missingPath(), []);
    const corrupt = providerEvidencePresentation("fred", capability, corruptPath(), [
      "trace.shared",
    ]);

    expect(shared.state).toBe("corrupt");
    expect(missing.state).toBe("unavailable");
    expect(missing.traceId).toBeNull();
    expect(corrupt.state).toBe("corrupt");
  });

  test("Given every canonical state, when a provider trace is corrupt or unavailable, then the row remains fail closed", () => {
    const states = [
      "loading",
      "empty",
      "error",
      "blocked",
      "unavailable",
      "corrupt",
      "stale",
      "populated",
    ] as const;
    const rows = states.map((state) =>
      providerEvidencePresentation(
        "fred",
        {
          capability_id: "fred.authoritative",
          provider: "fred",
          label: "FRED",
          state,
          entitlement: "research_only",
          observed_at: state === "unavailable" ? null : "2026-07-26T03:00:00Z",
          trace_id: "trace.fred",
        },
        missingPath(),
        ["trace.fred"],
      ),
    );

    expect(rows.every((row) => row.state === "unavailable")).toBe(true);
  });
});

function acceptedMarketPath(): MarketEvidencePath {
  return {
    status: "resolved",
    startsAtSource: true,
    nodes: [
      {
        node_id: "trace.markets.calendar.us",
        kind: "source_receipt",
        state: "accepted",
        source_namespace: "market_calendar.markets",
      },
    ],
  };
}

function acceptedProviderPath(traceId: string, namespace: string): ProviderEvidencePath {
  const source = {
    node_id: traceId,
    kind: "source_receipt",
    label: "FRED provider review",
    state: "accepted",
    source_namespace: namespace,
  };
  return {
    status: "resolved",
    startsAtSource: true,
    nodes: [source],
    edges: [],
    terminal: source,
  };
}

function missingPath(): ProviderEvidencePath {
  return { status: "unavailable", startsAtSource: false, nodes: [], edges: [], terminal: null };
}

function corruptPath(): ProviderEvidencePath {
  return { status: "corrupt", startsAtSource: false, nodes: [], edges: [], terminal: null };
}
