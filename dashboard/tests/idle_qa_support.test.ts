import { describe, expect, test } from "bun:test";
import { observedIdleDelta } from "../scripts/idle_qa_support";

describe("idle QA observation", () => {
  test("Given baseline and end events, when idle delta is derived, then real events are counted", () => {
    // Given: setup events followed by one store query and two observed process launches.
    const baseline = {
      storeEvents: 3,
      observedProcessIds: [10, 20],
    };
    const end = {
      storeEvents: 4,
      observedProcessIds: [10, 20, 30, 40],
    };

    // When: the measured idle delta is calculated.
    const delta = observedIdleDelta(baseline, end);

    // Then: counts come from the event and PID differences.
    expect(delta.storeOperations).toBe(1);
    expect(delta.processLaunches).toBe(2);
  });
});
