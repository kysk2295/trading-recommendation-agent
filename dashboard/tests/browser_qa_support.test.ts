import { describe, expect, test } from "bun:test";
import { aggregateAxeFindings } from "../scripts/browser_qa_support";

describe("browser QA axe aggregation", () => {
  test("Given disjoint incomplete findings, when positions combine, then none are discarded", () => {
    // Given: two materialized scroll positions with different incomplete findings.
    const scans = [
      {
        violations: [],
        incomplete: [{ id: "first", nodes: [{ target: ["#first"] }] }],
      },
      {
        violations: [],
        incomplete: [{ id: "second", nodes: [{ target: ["#second"] }] }],
      },
    ];

    // When: the position-local findings are combined.
    const combined = aggregateAxeFindings(scans);

    // Then: both unique incomplete findings remain release-blocking.
    expect(combined.incomplete).toBe(2);
  });
});
