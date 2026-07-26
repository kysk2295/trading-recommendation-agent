import { nearMaximumSnapshotV2 } from "./snapshot_v2_fixture";

export const oversizedSnapshotV2 = {
  ...nearMaximumSnapshotV2,
  traces: {
    ...nearMaximumSnapshotV2.traces,
    nodes: [
      ...nearMaximumSnapshotV2.traces.nodes,
      ...Array.from({ length: 60 }, (_, index) => ({
        ...nearMaximumSnapshotV2.traces.nodes[0],
        node_id: `oversize-${index}-${"o".repeat(87)}`,
        label: "O".repeat(100),
        safe_ref: "c".repeat(64),
        source_namespace: `fixture.${"o".repeat(92)}`,
      })),
    ],
  },
} as const;
