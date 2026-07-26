import { textElement } from "../dom";
import { renderWorkspace } from "../render";
import { workspaceById } from "../workspace_registry";
import type { WorkspaceRenderer } from "./types";

type SourceState =
  | "empty"
  | "error"
  | "blocked"
  | "unavailable"
  | "corrupt"
  | "stale"
  | "populated";

export type DerivativeQuoteAuthorityItem = {
  readonly state: SourceState;
  readonly value: string | null;
  readonly observed_at: string | null;
  readonly trace_id: string;
};

export type DerivativeQuoteEvidencePath = {
  readonly status: "resolved" | "unavailable" | "corrupt";
  readonly startsAtSource: boolean;
  readonly nodes: readonly {
    readonly node_id: string;
    readonly kind: string;
    readonly state: string;
    readonly source_namespace: string;
  }[];
};

export type DerivativeQuotePresentation = { readonly current: boolean; readonly value: string };

const CURRENT_AUTHORITY = [
  "entitlement:active_realtime",
  "redistribution:allowed",
  "capability:healthy_current",
  "quote:fresh",
] as const;

export function derivativeQuotePresentation(
  quote: DerivativeQuoteAuthorityItem,
  gates: readonly DerivativeQuoteAuthorityItem[],
  trace: DerivativeQuoteEvidencePath,
): DerivativeQuotePresentation {
  const source = trace.nodes.find((node) => node.node_id === quote.trace_id);
  const authorityValues = new Set(
    gates.flatMap((gate) =>
      gate.state === "populated" &&
      gate.observed_at !== null &&
      gate.trace_id === quote.trace_id &&
      gate.value !== null
        ? [gate.value]
        : [],
    ),
  );
  const current =
    quote.state === "populated" &&
    quote.value !== null &&
    quote.observed_at !== null &&
    CURRENT_AUTHORITY.every((value) => authorityValues.has(value)) &&
    trace.status === "resolved" &&
    trace.startsAtSource &&
    source?.kind === "source_receipt" &&
    source.state === "accepted" &&
    source.source_namespace === "derivatives.options.current";
  return current
    ? { current: true, value: quote.value ?? "Research-only · current quote unavailable" }
    : { current: false, value: "Research-only · current quote unavailable" };
}

export const renderDerivatives: WorkspaceRenderer = (snapshot, drawer) => {
  const fragment = document.createDocumentFragment();
  const guard = document.createElement("section");
  guard.className = "workspace-contract-strip derivatives-contract";
  guard.append(
    textElement("h2", "Derivatives research context"),
    textElement(
      "p",
      "Option chain, IV, skew, term structure, futures roll and CFTC context are read-only. Current quotes require active real-time entitlement, allowed redistribution, current healthy capability and fresh source authority together.",
    ),
  );
  fragment.append(guard, renderWorkspace(workspaceById("derivatives"), snapshot, drawer));
  return fragment;
};
