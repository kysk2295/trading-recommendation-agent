import { describe, expect, test } from "bun:test";
import { keyboardWorkspaceIndex } from "../src/workspace_tabs";

describe("workspace keyboard navigation", () => {
  test("moves with arrows and wraps at both ends", () => {
    // Given: nine workspace controls and an endpoint selection.
    // When: horizontal and vertical arrow keys are applied.
    const indices = [
      keyboardWorkspaceIndex("ArrowRight", 8, 9),
      keyboardWorkspaceIndex("ArrowDown", 8, 9),
      keyboardWorkspaceIndex("ArrowLeft", 0, 9),
      keyboardWorkspaceIndex("ArrowUp", 0, 9),
    ];

    // Then: navigation wraps in either shell orientation.
    expect(indices).toEqual([0, 0, 8, 8]);
  });

  test("moves to the first and last workspaces with Home and End", () => {
    // Given: a middle workspace selection.
    // When: Home and End are applied.
    const home = keyboardWorkspaceIndex("Home", 4, 9);
    const end = keyboardWorkspaceIndex("End", 4, 9);

    // Then: the roving target reaches both boundaries.
    expect([home, end]).toEqual([0, 8]);
  });
});
