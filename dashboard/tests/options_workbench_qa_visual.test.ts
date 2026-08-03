import { describe, expect, test } from "bun:test";
import { optionsWorkbenchScreenshotArtifactReference } from "../scripts/options_workbench_qa_visual";

describe("options workbench QA visual artifacts", () => {
  test("reports a bounded relative screenshot reference", () => {
    // Given: a captured Market Pulse view at a responsive width.
    // When: its report reference is derived.
    const reference = optionsWorkbenchScreenshotArtifactReference(375, "market_pulse");

    // Then: the report cannot disclose a filesystem location.
    expect(reference).toBe("screenshots/options-workbench-375-market_pulse.png");
    expect(reference.startsWith("/")).toBeFalse();
    expect(reference).not.toContain("/Users/");
    expect(reference).not.toContain("file://");
    expect(reference).not.toContain("..");
  });
});
