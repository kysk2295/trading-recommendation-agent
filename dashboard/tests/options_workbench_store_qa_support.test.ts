import { describe, expect, test } from "bun:test";
import {
  OptionsWorkbenchStoreQaError,
  parseStoreQaOptions,
} from "../scripts/options_workbench_store_qa_support";
import { formatExpectedCliError } from "../scripts/run-options-workbench-store-qa";

const blockedArgs = [
  "--blocked",
  "missing=missing.json",
  "--blocked",
  "corrupt=corrupt.json",
  "--blocked",
  "stale=stale.json",
  "--blocked",
  "unlicensed_current=unlicensed.json",
] as const;

describe("options workbench store QA CLI", () => {
  test("parses the required actual and blocked inputs with default widths", () => {
    // Given: every required external snapshot path.
    const args = ["--actual", "actual.json", ...blockedArgs, "--output", "report.json"];

    // When: the CLI arguments cross the parser boundary.
    const parsed = parseStoreQaOptions(args);

    // Then: the run contract preserves all paths and default responsive widths.
    expect(parsed).toEqual({
      kind: "run",
      actualPath: "actual.json",
      blocked: [
        { label: "missing", path: "missing.json" },
        { label: "corrupt", path: "corrupt.json" },
        { label: "stale", path: "stale.json" },
        { label: "unlicensed_current", path: "unlicensed.json" },
      ],
      output: "report.json",
      widths: [375, 768, 1280],
    });
  });

  test("rejects an incomplete blocked-state matrix", () => {
    // Given: a caller omitted the stale snapshot required for browser safety coverage.
    const args = ["--actual", "actual.json", ...blockedArgs.slice(0, 6), "--output", "report.json"];

    // When: the CLI validates its mandatory state matrix.
    const parse = () => parseStoreQaOptions(args);

    // Then: it fails before a browser or listener is created.
    expect(parse).toThrow(OptionsWorkbenchStoreQaError);
  });

  test("rejects a malformed blocked label assignment", () => {
    // Given: an unknown blocked label supplied through the repeatable option.
    const args = [
      "--actual",
      "actual.json",
      "--blocked",
      "unknown=unknown.json",
      "--output",
      "report.json",
    ];

    // When: the parser reads the unsafe assignment.
    const parse = () => parseStoreQaOptions(args);

    // Then: no external file is opened for an unrecognized state.
    expect(parse).toThrow(OptionsWorkbenchStoreQaError);
  });

  test("formats expected CLI failures without absolute paths", () => {
    // Given: a known input error whose original file-read detail contains a local path.
    const error = new OptionsWorkbenchStoreQaError(
      "actual snapshot could not be read: ENOENT /Users/operator/worktree/actual.json",
    );

    // When: the top-level CLI boundary formats the expected error.
    const formatted = formatExpectedCliError(error);

    // Then: stderr receives one bounded message instead of a stack or local path.
    expect(formatted).toBe("ERROR: snapshot could not be read");
  });
});
