import { describe, expect, test } from "bun:test";
import { DEFAULT_WORKSPACE, resolveWorkspaceHash, WORKSPACES } from "../src/workspace_registry";

describe("workspace registry", () => {
  test("returns the exact nine routes when the shell is initialized", () => {
    // Given: the fixed v2 workspace registry.
    // When: its hashes are enumerated.
    const hashes = WORKSPACES.map((workspace) => workspace.hash);

    // Then: Command Center is first and every approved route appears once.
    expect(hashes).toEqual([
      "#command-center",
      "#overview",
      "#markets",
      "#data-sources",
      "#research",
      "#strategies",
      "#derivatives",
      "#paper",
      "#system",
    ]);
    expect(new Set(hashes).size).toBe(9);
  });

  test("falls back to Command Center when the hash is missing or invalid", () => {
    // Given: missing and unknown browser hashes.
    const hashes = ["", "#agents", "#not-a-workspace"];

    // When: each hash is resolved.
    const resolved = hashes.map(resolveWorkspaceHash);

    // Then: every invalid route resolves to the fixed default.
    expect(resolved).toEqual([DEFAULT_WORKSPACE, DEFAULT_WORKSPACE, DEFAULT_WORKSPACE]);
  });
});
