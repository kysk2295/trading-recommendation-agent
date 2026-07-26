import { describe, expect, test } from "bun:test";
import { dashboardSnapshotV2Schema } from "../../src/schema_v2";
import { WORKSPACES } from "../../src/workspace_registry";
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
    // Then: every fixture has valid evidence and applies the state to all nine workspaces.
    for (const [index, state] of states.entries()) {
      const result = dashboardSnapshotV2Schema.safeParse(
        workstationStateFixture(state, new Date(Date.now() + index * 1_000).toISOString()),
      );
      expect(result.success).toBe(true);
      if (!result.success) continue;
      for (const workspace of WORKSPACES) {
        expect(result.data.workspaces[workspace.key].state).toBe(state);
      }
    }
  });
});
