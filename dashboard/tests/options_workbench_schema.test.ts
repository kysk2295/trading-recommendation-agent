import { describe, expect, test } from "bun:test";
import {
  type OptionsWorkbenchInput,
  optionsWorkbenchSchema,
} from "../src/options_workbench_schema";
import { dashboardSnapshotV2Schema } from "../src/schema_v2";
import {
  derivativesPaperAdverseFixture,
  derivativesPaperHappyFixture,
  populatedOptionsWorkbenchFixture,
} from "./e2e/derivatives_paper_fixture";
import { nearMaximumSnapshotV2, snapshotV2 } from "./snapshot_v2_fixture";

describe("options workbench schema", () => {
  test("accepts bounded calls-left strike-center puts-right research data", () => {
    // Given: a populated research-only workbench with paired option-chain rows.
    const workbench = populatedOptionsWorkbenchFixture();

    // When: the options contract is parsed at its boundary.
    const parsed = optionsWorkbenchSchema.safeParse(workbench);

    // Then: calls, strikes, and puts are preserved without a numeric authority claim.
    expect(parsed.success).toBe(true);
    expect(workbench.chain.rows).toHaveLength(3);
    expect(workbench.scenario?.state).toBe("research_only");
  });

  test("rejects a selectable stale cell produced by the fixture override", () => {
    // Given: an otherwise valid first call overridden to stale and selectable.
    const workbench = populatedOptionsWorkbenchFixture({ state: "stale", selectable: true });

    // When: the invalid option-chain cell crosses the schema boundary.
    const issues = issueMessages(workbench);

    // Then: stale quotes cannot become research legs.
    expect(issues).toContain("selectable_quote_not_usable");
  });

  test("rejects loading section metadata", () => {
    // Given: a loading market section with forbidden observation metadata.
    const workbench = populatedOptionsWorkbenchFixture();
    const invalid = {
      ...workbench,
      market: { ...workbench.market, state: "loading", observed_at: "2026-07-26T03:00:00Z" },
    };

    // When: the section is parsed.
    const issues = issueMessages(invalid);

    // Then: loading cannot serialize observation or blocker metadata.
    expect(issues).toContain("loading_section_metadata_forbidden");
  });

  test.each([
    [
      "42 rows",
      (value: OptionsWorkbenchInput) => ({
        ...value,
        chain: { ...value.chain, rows: Array.from({ length: 14 }, () => value.chain.rows).flat() },
      }),
    ],
    [
      "13 expirations",
      (value: OptionsWorkbenchInput) => ({
        ...value,
        chain: {
          ...value.chain,
          expirations: Array.from(
            { length: 13 },
            (_, index) => `2026-09-${String(index + 1).padStart(2, "0")}`,
          ),
        },
      }),
    ],
    [
      "count mismatch",
      (value: OptionsWorkbenchInput) => ({
        ...value,
        chain: { ...value.chain, projected_count: value.chain.projected_count + 1 },
      }),
    ],
    [
      "missing selected expiration",
      (value: OptionsWorkbenchInput) => ({
        ...value,
        chain: { ...value.chain, selected_expiration: "2026-12-19" },
      }),
    ],
  ])("rejects %s", (_label, invalidate) => {
    // Given: a valid bounded workbench and one broken option-chain invariant.
    const invalid = invalidate(populatedOptionsWorkbenchFixture());

    // When: the invalid workbench is parsed.
    const parsed = optionsWorkbenchSchema.safeParse(invalid);

    // Then: the bounded projection fails closed.
    expect(parsed.success).toBe(false);
  });

  test("rejects unsorted or duplicate scenario spots", () => {
    // Given: a valid workbench with a scenario and non-ascending prices.
    const scenario = requireScenario(populatedOptionsWorkbenchFixture());
    const invalid = {
      ...populatedOptionsWorkbenchFixture(),
      scenario: { ...scenario, scenario_spots: ["190", "190"] },
    };

    // When: scenario prices cross the schema boundary.
    const issues = issueMessages(invalid);

    // Then: deterministic payoff inputs require strictly ascending unique spots.
    expect(issues).toContain("scenario_spots_not_strictly_ascending");
  });

  test.each(["99999999999999999999999999999999", "0.123456789"])(
    "rejects unsafe operational decimal %s",
    (decimal) => {
      const workbench = populatedOptionsWorkbenchFixture();
      const first = workbench.chain.rows[0];
      if (first?.call === null || first?.call === undefined)
        throw new OptionsWorkbenchTestFixtureError();
      const invalid = {
        ...workbench,
        chain: {
          ...workbench.chain,
          rows: [
            { ...first, call: { ...first.call, bid: decimal } },
            ...workbench.chain.rows.slice(1),
          ],
        },
      };
      expect(optionsWorkbenchSchema.safeParse(invalid).success).toBe(false);
    },
  );

  test("accepts the eight-place operational fraction boundary", () => {
    const workbench = populatedOptionsWorkbenchFixture();
    const scenario = requireScenario(workbench);
    const parsed = optionsWorkbenchSchema.safeParse({
      ...workbench,
      scenario: { ...scenario, spot: "200.12345678" },
    });
    expect(parsed.success).toBe(true);
  });

  test("rejects an incomplete approved promotion", () => {
    // Given: a held promotion rewritten as approved without satisfying its gates.
    const workbench = populatedOptionsWorkbenchFixture();
    const invalid = {
      ...workbench,
      promotions: workbench.promotions.map((promotion) => ({ ...promotion, state: "approved" })),
    };

    // When: promotion evidence is parsed.
    const issues = issueMessages(invalid);

    // Then: approval cannot omit required gates or blockers.
    expect(issues).toContain("promotion_approved_incomplete");
  });

  test("rejects unknown workbench fields", () => {
    // Given: a valid workbench with an undeclared field.
    const invalid = { ...populatedOptionsWorkbenchFixture(), broker_authority: true };

    // When: it crosses the strict boundary.
    const parsed = optionsWorkbenchSchema.safeParse(invalid);

    // Then: schema drift fails closed.
    expect(parsed.success).toBe(false);
  });

  test("parses base near-maximum and complete dashboard fixtures", () => {
    // Given: base, bounded near-maximum, populated, and adverse dashboard projections.
    const fixtures = [
      snapshotV2,
      nearMaximumSnapshotV2,
      derivativesPaperHappyFixture,
      derivativesPaperAdverseFixture,
    ];

    // When: each crosses the full dashboard schema boundary.
    const parsed = fixtures.map((fixture) => dashboardSnapshotV2Schema.safeParse(fixture));

    // Then: every canonical derivatives workbench remains valid.
    expect(parsed.every((result) => result.success)).toBe(true);
  });
});

function issueMessages(value: unknown): readonly string[] {
  const parsed = optionsWorkbenchSchema.safeParse(value);
  if (parsed.success) return [];
  return parsed.error.issues.map((issue) => issue.message);
}

function requireScenario(
  workbench: OptionsWorkbenchInput,
): NonNullable<OptionsWorkbenchInput["scenario"]> {
  if (workbench.scenario === null) throw new OptionsWorkbenchTestFixtureError();
  return workbench.scenario;
}

class OptionsWorkbenchTestFixtureError extends Error {
  override readonly name = "OptionsWorkbenchTestFixtureError";

  constructor() {
    super("populated fixture must include a scenario");
  }
}
