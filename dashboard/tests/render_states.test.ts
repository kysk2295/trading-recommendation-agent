import { describe, expect, test } from "bun:test";
import { sourceStatePresentation } from "../src/render";

describe("source state renderer", () => {
  test("renders every canonical source state distinctly", () => {
    // Given: every canonical v2 state.
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

    // When: each state is mapped to its presentation.
    const presentations = states.map(sourceStatePresentation);

    // Then: labels are truthful, non-empty, and unique.
    expect(presentations.every((presentation) => presentation.label.length > 0)).toBe(true);
    expect(new Set(presentations.map((presentation) => presentation.label)).size).toBe(8);
    expect(presentations.map((presentation) => presentation.tone)).toEqual([
      "neutral",
      "neutral",
      "error",
      "error",
      "neutral",
      "error",
      "warning",
      "success",
    ]);
  });
});
