import { expect, test } from "bun:test";
import {
  type ProviderEvidencePath,
  providerEvidencePresentation,
} from "../src/workspaces/data_sources";

test("FRED source evidence requires its exact provider terminal contract", () => {
  const capability = fredCapability();
  const healthy = providerPath(capability.trace_id, "provider.fred");
  const source = first(healthy.nodes);
  const healthyTerminal = terminal(healthy);
  const lsTerminal = {
    ...healthyTerminal,
    node_id: "trace.data_sources.ls.reviewer",
    label: "LS provider review",
    source_namespace: "provider.ls",
  };
  const unavailable = { ...capability, state: "unavailable" as const, observed_at: null };
  const blocked = providerPath(capability.trace_id, "provider.fred", true);

  const accepted = providerEvidencePresentation("fred", capability, healthy, [capability.trace_id]);
  expect(accepted).toMatchObject({ state: "populated", receipt: "Receipt · FRED source receipt" });

  for (const [item, path] of [
    [capability, { ...healthy, terminal: null }],
    [
      capability,
      {
        ...healthy,
        nodes: [source, lsTerminal],
        edges: [
          {
            from_node_id: capability.trace_id,
            to_node_id: lsTerminal.node_id,
            kind: "reviewed_by",
          },
        ],
        terminal: lsTerminal,
      },
    ],
    [unavailable, mutateBlocked(blocked, {}, [])],
    [unavailable, mutateBlocked(blocked, { kind: "reviewer_decision" })],
    [unavailable, mutateBlocked(blocked, { state: "accepted" })],
    [unavailable, mutateBlocked(blocked, { source_namespace: "provider.ls" })],
  ] as const) {
    const display = providerEvidencePresentation("fred", item, path, [capability.trace_id]);
    expect(display.state).toBe("unavailable");
    expect(display.receipt).not.toContain("resolved");
  }
});

function fredCapability() {
  return {
    capability_id: "fred.authoritative",
    provider: "fred" as const,
    label: "FRED",
    state: "populated" as const,
    entitlement: "realtime" as const,
    observed_at: "2026-07-26T03:00:00Z",
    trace_id: "trace.fred",
  };
}

function providerPath(traceId: string, namespace: string, blocked = false): ProviderEvidencePath {
  const source = {
    node_id: traceId,
    kind: "source_receipt",
    label: "FRED source receipt",
    state: blocked ? "unavailable" : "accepted",
    source_namespace: namespace,
  };
  if (!blocked)
    return {
      status: "resolved",
      startsAtSource: true,
      nodes: [source],
      edges: [],
      terminal: source,
    };
  const blocker = {
    ...source,
    node_id: `${traceId}.blocker`,
    kind: "blocker_terminal",
    label: "FRED entitlement unavailable",
    state: "blocked",
  };
  return {
    status: "resolved",
    startsAtSource: true,
    nodes: [source, blocker],
    edges: [{ from_node_id: traceId, to_node_id: blocker.node_id, kind: "blocked_by" }],
    terminal: blocker,
  };
}

function mutateBlocked(
  path: ProviderEvidencePath,
  patch: Partial<NonNullable<ProviderEvidencePath["terminal"]>>,
  edges = path.edges,
): ProviderEvidencePath {
  const blocker = { ...terminal(path), ...patch };
  return { ...path, nodes: [first(path.nodes), blocker], edges, terminal: blocker };
}

function terminal(path: ProviderEvidencePath) {
  if (path.terminal === null) throw new Error("test path requires a terminal");
  return path.terminal;
}

function first<T>(values: readonly T[]): T {
  const value = values.at(0);
  if (value === undefined) throw new Error("test path requires a source");
  return value;
}
