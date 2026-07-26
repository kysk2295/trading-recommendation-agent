import type { DashboardSnapshotV2 } from "../schema_v2";

export const PROVIDERS = [
  "fred",
  "alfred",
  "treasury",
  "cftc",
  "opendart",
  "kis",
  "ls",
  "alpaca",
] as const;
export type Provider = (typeof PROVIDERS)[number];
export type Capability = DashboardSnapshotV2["workspaces"]["data_sources"]["capabilities"][number];
type SourceState = Capability["state"];

export type ProviderEvidencePath = {
  readonly status: "resolved" | "unavailable" | "corrupt";
  readonly startsAtSource: boolean;
  readonly nodes: readonly {
    readonly node_id: string;
    readonly kind: string;
    readonly state: string;
    readonly source_namespace: string;
    readonly label?: string;
  }[];
  readonly edges: readonly {
    readonly from_node_id: string;
    readonly to_node_id: string;
    readonly kind: string;
  }[];
  readonly terminal: {
    readonly node_id: string;
    readonly kind: string;
    readonly label: string;
    readonly state: string;
    readonly source_namespace: string;
  } | null;
};

export type ProviderEvidencePresentation = {
  readonly state: SourceState;
  readonly coverage: string;
  readonly receipt: string;
  readonly traceId: string | null;
};

export function providerCoverageText(label: string): string {
  return `${label}: coverage 미게시 · canonical v2 capability에 coverage field가 없습니다`;
}

export function providerQuoteNotice(entitlement: Capability["entitlement"]): string {
  return `현재 quote 미표시 · ${entitlement} entitlement이며 currentness/redistribution permit이 snapshot에 없습니다`;
}

export function providerEvidencePresentation(
  provider: Provider,
  capability: Capability | undefined,
  trace: ProviderEvidencePath,
  traceIds: readonly string[],
): ProviderEvidencePresentation {
  if (capability === undefined) {
    return {
      state: "unavailable",
      coverage: `${provider.toUpperCase()}: coverage 미게시 · capability 없음`,
      receipt: `Blocker · ${provider.toUpperCase()} capability가 canonical v2 snapshot에 없습니다`,
      traceId: null,
    };
  }
  const source = trace.nodes.find((node) => node.node_id === capability.trace_id);
  const needsBlocker = ["error", "blocked", "unavailable", "corrupt"].includes(capability.state);
  const terminal = trace.terminal;
  const sourceMatches =
    source?.kind === "source_receipt" &&
    source.source_namespace === `provider.${provider}` &&
    (source.state === "accepted" || source.state === "unavailable");
  const terminalMatches = needsBlocker
    ? terminal !== null &&
      terminal.node_id === `${capability.trace_id}.blocker` &&
      terminal.kind === "blocker_terminal" &&
      terminal.state === "blocked" &&
      terminal.source_namespace === `provider.${provider}` &&
      trace.nodes.some((node) => node.node_id === terminal.node_id) &&
      trace.edges.some(
        (edge) =>
          edge.from_node_id === capability.trace_id &&
          edge.to_node_id === terminal.node_id &&
          edge.kind === "blocked_by",
      )
    : terminal?.node_id === capability.trace_id &&
      trace.edges.every((edge) => edge.from_node_id !== capability.trace_id);
  const duplicateTrace = traceIds.filter((traceId) => traceId === capability.trace_id).length !== 1;
  const authoritative =
    !duplicateTrace &&
    trace.status === "resolved" &&
    trace.startsAtSource &&
    sourceMatches &&
    terminalMatches;
  if (!authoritative || terminal === null) {
    return {
      state: duplicateTrace || trace.status === "corrupt" ? "corrupt" : "unavailable",
      coverage: providerCoverageText(capability.label),
      receipt: `Blocker · ${capability.label} provider terminal authority 검증 실패`,
      traceId: capability.trace_id,
    };
  }
  return {
    state: capability.state,
    coverage: providerCoverageText(capability.label),
    receipt: `${needsBlocker ? "Blocker" : "Receipt"} · ${terminal.label}`,
    traceId: capability.trace_id,
  };
}
