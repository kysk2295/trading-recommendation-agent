import { describe, expect, test } from "bun:test";
import { storeQaScreenshotArtifactReference } from "../scripts/options_workbench_store_qa_browser";

describe("options workbench store QA browser artifacts", () => {
  test("reports a bounded relative screenshot reference", () => {
    // Given: a captured actual Market Pulse screenshot.
    // When: its report reference is derived.
    const reference = storeQaScreenshotArtifactReference("actual", 375, "market_pulse");

    // Then: the artifact contract cannot disclose a filesystem location.
    expect(reference).toBe("options-workbench-store-screenshots/actual-375-market_pulse.png");
    expect(reference.startsWith("/")).toBeFalse();
    expect(reference).not.toContain("/Users/");
    expect(reference).not.toContain("file://");
    expect(reference).not.toContain("..");
  });
});
