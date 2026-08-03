import { describe, expect, test } from "bun:test";
import { dashboardSnapshotV2Schema } from "../src/schema";
import { derivativesPaperHappyFixture } from "./e2e/derivatives_paper_fixture";
import { nearMaximumSnapshotV2, snapshotV2 } from "./snapshot_v2_fixture";

describe("snapshot v2 evidence semantics", () => {
  test("rejects reversed terminal-to-source lineage", () => {
    const reversed = {
      ...snapshotV2,
      traces: {
        ...snapshotV2.traces,
        edges: snapshotV2.traces.edges.map((edge) => ({
          ...edge,
          from_node_id: edge.to_node_id,
          to_node_id: edge.from_node_id,
        })),
      },
    };

    expect(dashboardSnapshotV2Schema.safeParse(reversed).success).toBe(false);
  });

  test("rejects deployment receipts as terminals outside the system domain", () => {
    const wrongDomain = {
      ...snapshotV2,
      traces: {
        ...snapshotV2.traces,
        nodes: snapshotV2.traces.nodes.map((node) =>
          node.node_id === "trace-research-terminal"
            ? { ...node, kind: "deployment_receipt" }
            : node,
        ),
      },
    };

    expect(dashboardSnapshotV2Schema.safeParse(wrongDomain).success).toBe(false);
  });

  test("rejects duplicate directed trace edges", () => {
    const duplicateEdge = {
      ...snapshotV2,
      traces: {
        ...snapshotV2.traces,
        edges: [...snapshotV2.traces.edges, snapshotV2.traces.edges[0]],
      },
    };

    expect(dashboardSnapshotV2Schema.safeParse(duplicateEdge).success).toBe(false);
  });

  test.each([
    ["trace node", nestedFuture(snapshotV2, "trace")],
    ["workspace observation", nestedFuture(snapshotV2, "workspace")],
    ["workspace freshness", nestedFuture(snapshotV2, "freshness")],
    ["workspace item", nestedFuture(nearMaximumSnapshotV2, "item")],
    ["source capability", nestedFuture(snapshotV2, "capability")],
  ])("rejects far-future %s timestamps", (_label, future) => {
    expect(dashboardSnapshotV2Schema.safeParse(future).success).toBe(false);
  });

  test.each(["populated", "empty", "stale", "blocked", "error", "corrupt"] as const)(
    "requires observed_at for %s source state",
    (state) => {
      const fixture = state === "populated" ? nearMaximumSnapshotV2 : snapshotV2;
      const missingObservation = {
        ...fixture,
        workspaces: {
          ...fixture.workspaces,
          overview: {
            ...fixture.workspaces.overview,
            state,
            observed_at: null,
            blocker_code: ["blocked", "error", "corrupt"].includes(state)
              ? "fixture_blocked"
              : null,
          },
        },
      };

      expect(dashboardSnapshotV2Schema.safeParse(missingObservation).success).toBe(false);
    },
  );

  test("allows null observed_at only for unavailable authority with a blocker terminal", () => {
    const unavailable = unavailableOverview(true);

    expect(dashboardSnapshotV2Schema.safeParse(unavailable).success).toBe(true);
  });

  test("rejects unavailable authority without its blocker terminal", () => {
    expect(dashboardSnapshotV2Schema.safeParse(unavailableOverview(false)).success).toBe(false);
  });

  test("rejects a missing derivatives workbench chain trace", () => {
    // Given: a populated derivatives workbench whose chain references an unknown trace.
    const missingChainTrace = {
      ...derivativesPaperHappyFixture,
      workspaces: {
        ...derivativesPaperHappyFixture.workspaces,
        derivatives: {
          ...derivativesPaperHappyFixture.workspaces.derivatives,
          workbench: {
            ...derivativesPaperHappyFixture.workspaces.derivatives.workbench,
            chain: {
              ...derivativesPaperHappyFixture.workspaces.derivatives.workbench.chain,
              trace_id: "missing-workbench-chain-trace",
            },
          },
        },
      },
    };

    // When: the full dashboard schema validates trace graph references.
    const parsed = dashboardSnapshotV2Schema.safeParse(missingChainTrace);

    // Then: every nested Workbench trace reference must resolve to the graph.
    expect(parsed.success).toBe(false);
  });
});

type FutureTarget = "trace" | "workspace" | "freshness" | "item" | "capability";

function nestedFuture(
  fixture: typeof snapshotV2 | typeof nearMaximumSnapshotV2,
  target: FutureTarget,
): unknown {
  const future = "2099-01-01T00:00:00Z";
  if (target === "trace") {
    return {
      ...fixture,
      traces: {
        ...fixture.traces,
        nodes: fixture.traces.nodes.map((node, index) =>
          index === 0 ? { ...node, observed_at: future } : node,
        ),
      },
    };
  }
  const system = fixture.workspaces.system;
  if (target === "workspace") {
    return {
      ...fixture,
      workspaces: { ...fixture.workspaces, system: { ...system, observed_at: future } },
    };
  }
  if (target === "freshness") {
    return {
      ...fixture,
      workspaces: {
        ...fixture.workspaces,
        system: { ...system, freshness: { ...system.freshness, as_of: future } },
      },
    };
  }
  if (target === "item") {
    return {
      ...fixture,
      workspaces: {
        ...fixture.workspaces,
        system: {
          ...system,
          items: system.items.map((item, index) =>
            index === 0 ? { ...item, observed_at: future } : item,
          ),
        },
      },
    };
  }
  return {
    ...fixture,
    workspaces: {
      ...fixture.workspaces,
      data_sources: {
        ...fixture.workspaces.data_sources,
        capabilities: [
          {
            capability_id: "future-capability",
            provider: "fred",
            label: "future",
            state: "empty",
            entitlement: "research_only",
            observed_at: future,
            trace_id: fixture.workspaces.data_sources.trace_id,
          },
        ],
      },
    },
  };
}

function unavailableOverview(withTerminal: boolean): unknown {
  const blockerId = "trace-overview-blocker";
  return {
    ...snapshotV2,
    workspaces: {
      ...snapshotV2.workspaces,
      overview: {
        ...snapshotV2.workspaces.overview,
        state: "unavailable",
        observed_at: null,
        blocker_code: "authority_absent",
      },
    },
    traces: {
      nodes: withTerminal
        ? [
            ...snapshotV2.traces.nodes,
            {
              node_id: blockerId,
              kind: "blocker_terminal",
              label: "authority absent",
              observed_at: snapshotV2.generated_at,
              safe_ref: null,
              state: "unavailable",
              source_namespace: "dashboard.fixture",
            },
          ]
        : snapshotV2.traces.nodes,
      edges: withTerminal
        ? [
            ...snapshotV2.traces.edges,
            {
              from_node_id: snapshotV2.workspaces.overview.trace_id,
              to_node_id: blockerId,
              kind: "blocked_by",
            },
          ]
        : snapshotV2.traces.edges,
    },
  };
}
