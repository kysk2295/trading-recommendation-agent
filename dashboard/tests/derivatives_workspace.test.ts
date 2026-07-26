import { describe, expect, test } from "bun:test";
import { dashboardSnapshotV2Schema } from "../src/schema_v2";
import {
  type DerivativeQuoteAuthorityItem,
  type DerivativeQuoteEvidencePath,
  derivativeQuotePresentation,
} from "../src/workspaces/derivatives";
import {
  derivativesPaperAdverseFixture,
  derivativesPaperHappyFixture,
} from "./e2e/derivatives_paper_fixture";

const authority = (value: string): DerivativeQuoteAuthorityItem => ({
  state: "populated",
  value,
  observed_at: "2026-07-26T03:00:00Z",
  trace_id: "trace.derivatives.options.current",
});

const resolved: DerivativeQuoteEvidencePath = {
  status: "resolved",
  startsAtSource: true,
  nodes: [
    {
      node_id: "trace.derivatives.options.current",
      kind: "source_receipt",
      state: "accepted",
      source_namespace: "derivatives.options.current",
    },
  ],
};

describe("derivatives current quote authority", () => {
  test("browser fixtures satisfy the strict v2 boundary", () => {
    // Given
    const fixtures = [derivativesPaperHappyFixture, derivativesPaperAdverseFixture];

    // When
    const parsed = fixtures.map((fixture) => dashboardSnapshotV2Schema.safeParse(fixture));

    // Then
    expect(parsed.every((result) => result.success)).toBeTrue();
  });

  test("shows current only when every authority conjunct is present", () => {
    // Given
    const quote = authority("1.25 / 1.30");
    const gates = [
      authority("entitlement:active_realtime"),
      authority("redistribution:allowed"),
      authority("capability:healthy_current"),
      authority("quote:fresh"),
    ];

    // When
    const presentation = derivativeQuotePresentation(quote, gates, resolved);

    // Then
    expect(presentation).toEqual({ current: true, value: "1.25 / 1.30" });
  });

  for (const [gateIndex, unavailable] of [
    [0, "entitlement:expired"],
    [1, "redistribution:research_only"],
    [2, "capability:stale"],
    [3, "quote:derived"],
  ] as const) {
    test(`fails closed for ${unavailable}`, () => {
      // Given
      const quote = authority("1.25 / 1.30");
      const gates = [
        authority("entitlement:active_realtime"),
        authority("redistribution:allowed"),
        authority("capability:healthy_current"),
        authority("quote:fresh"),
      ].map((item, index) => (index === gateIndex ? authority(unavailable) : item));

      // When
      const presentation = derivativeQuotePresentation(quote, gates, resolved);

      // Then
      expect(presentation.current).toBeFalse();
      expect(presentation.value).toContain("Research-only");
    });
  }
});
