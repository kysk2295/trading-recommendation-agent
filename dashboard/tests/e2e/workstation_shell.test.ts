import { describe, expect, test } from "bun:test";
import { dashboardSnapshotV2Schema } from "../../src/schema_v2";
import { type PublishedState, workstationStateFixture } from "./workstation_shell_fixture";

describe("workstation browser state fixtures", () => {
  test("creates every publisher-valid canonical state with a complete trace", () => {
    // Given: every state the canonical publisher may emit.
    const states: readonly PublishedState[] = [
      "empty",
      "error",
      "blocked",
      "unavailable",
      "corrupt",
      "stale",
      "populated",
    ];

    // When: browser fixtures are parsed at the real snapshot boundary.
    const parsed = states.map((state, index) =>
      dashboardSnapshotV2Schema.safeParse(
        workstationStateFixture(state, new Date(Date.now() + index * 1_000).toISOString()),
      ),
    );

    // Then: every fixture has valid source-to-terminal evidence.
    expect(parsed.every((result) => result.success)).toBe(true);
  });
});
