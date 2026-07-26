import { snapshotV2 } from "../snapshot_v2_fixture";

export type PublishedState =
  | "empty"
  | "error"
  | "blocked"
  | "unavailable"
  | "corrupt"
  | "stale"
  | "populated";

export function workstationStateFixture(state: PublishedState, generatedAt: string): unknown {
  const traceId = `trace-shell-${state}`;
  const terminalId = `${traceId}-terminal`;
  const blocked = ["error", "blocked", "unavailable", "corrupt"].includes(state);
  const item =
    state === "populated"
      ? [
          {
            item_id: "shell-cjk-long-id",
            kind: "system" as const,
            label: "한글과 English가 함께 있는 긴 권위 데이터 식별자",
            state,
            value: "c".repeat(160),
            observed_at: generatedAt,
            trace_id: traceId,
          },
        ]
      : [];
  return {
    ...snapshotV2,
    snapshot_id: crypto.randomUUID(),
    generated_at: generatedAt,
    workspaces: {
      ...snapshotV2.workspaces,
      command_center: {
        ...snapshotV2.workspaces.command_center,
        state,
        observed_at: state === "unavailable" ? null : generatedAt,
        freshness: {
          policy_id: "shell-state-qa",
          age_seconds: state === "unavailable" ? null : 0,
          as_of: generatedAt,
        },
        blocker_code: blocked ? `${state}_qa_authority` : null,
        summary: summaryForState(state),
        total_count: item.length,
        projected_count: item.length,
        items: item,
        trace_id: traceId,
      },
    },
    traces: {
      nodes: [
        ...snapshotV2.traces.nodes,
        {
          node_id: traceId,
          kind: "source_receipt",
          label: "shell state source receipt",
          observed_at: generatedAt,
          safe_ref: "c".repeat(64),
          state: blocked ? "blocked" : "accepted",
          source_namespace: "dashboard.shell.qa",
        },
        {
          node_id: terminalId,
          kind: blocked ? "blocker_terminal" : "process_receipt",
          label: blocked ? "explicit blocker terminal" : "accepted process terminal",
          observed_at: generatedAt,
          safe_ref: null,
          state: blocked ? "blocked" : "accepted",
          source_namespace: "dashboard.shell.qa",
        },
      ],
      edges: [
        ...snapshotV2.traces.edges,
        {
          from_node_id: traceId,
          to_node_id: terminalId,
          kind: blocked ? "blocked_by" : "executed_as",
        },
      ],
    },
    projection: {
      ...snapshotV2.projection,
      total_count: item.length,
      projected_count: item.length,
    },
  };
}

function summaryForState(state: PublishedState): string {
  switch (state) {
    case "empty":
      return "권위 있는 읽기 성공, 0 records";
    case "error":
      return "typed reader가 완료되지 않았습니다";
    case "blocked":
      return "안전 gate가 사용을 차단했습니다";
    case "unavailable":
      return "현재 권위 receipt가 없습니다";
    case "corrupt":
      return "schema 또는 hash 무결성 검증 실패";
    case "stale":
      return "마지막 관측이 신선도 기준을 초과했습니다";
    case "populated":
      return "권위 데이터가 검증되어 표시됩니다";
    default:
      return assertNever(state);
  }
}

function assertNever(value: never): never {
  throw new WorkstationFixtureError(`unknown fixture state: ${String(value)}`);
}

class WorkstationFixtureError extends Error {
  override readonly name = "WorkstationFixtureError";
}
