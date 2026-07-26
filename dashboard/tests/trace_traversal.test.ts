import { describe, expect, test } from "bun:test";
import { resolveEvidenceTrace } from "../src/evidence_trace";
import { snapshotV2 } from "./snapshot_v2_fixture";

describe("Evidence Trace traversal", () => {
  test("resolves every workspace trace from source to an allowed terminal", () => {
    // Given: the canonical valid v2 fixture.
    const traces = Object.values(snapshotV2.workspaces).map((workspace) => workspace.trace_id);

    // When: every workspace trace is traversed.
    const resolved = traces.map((traceId) =>
      resolveEvidenceTrace(traceId, snapshotV2.traces.nodes, snapshotV2.traces.edges),
    );

    // Then: every path begins at a source and has a permitted terminal.
    expect(resolved.every((trace) => trace.startsAtSource)).toBe(true);
    expect(resolved.every((trace) => trace.terminal !== null)).toBe(true);
  });

  test("reports an unavailable trace without creating a synthetic node", () => {
    // Given: a trace reference absent from the graph.
    // When: the trace is resolved.
    const resolved = resolveEvidenceTrace(
      "trace-missing",
      snapshotV2.traces.nodes,
      snapshotV2.traces.edges,
    );

    // Then: the result is explicitly unavailable and contains no invented nodes.
    expect(resolved).toEqual({
      status: "unavailable",
      nodes: [],
      edges: [],
      startsAtSource: false,
      terminal: null,
    });
  });
});
