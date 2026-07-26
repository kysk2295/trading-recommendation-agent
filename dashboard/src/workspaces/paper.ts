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

export type PaperLedgerItem = {
  readonly item_id: string;
  readonly state: SourceState;
  readonly value: string | null;
  readonly observed_at: string | null;
};

export type PaperEvidencePath = {
  readonly status: "resolved" | "unavailable" | "corrupt";
  readonly nodes: readonly { readonly kind: string; readonly state: string }[];
};

export type PaperLedgerPresentation = { readonly verified: boolean; readonly value: string };

export function paperLedgerPresentation(
  item: PaperLedgerItem,
  trace: PaperEvidencePath,
  workspaceState: SourceState,
): PaperLedgerPresentation {
  const finalized =
    item.item_id.startsWith("paper.") &&
    item.observed_at !== null &&
    item.value !== null &&
    (item.state === "populated" || item.state === "empty" || item.state === "stale") &&
    (workspaceState === "populated" || workspaceState === "stale") &&
    trace.status === "resolved" &&
    trace.nodes.some((node) => node.kind === "paper_receipt" && node.state === "accepted");
  return finalized
    ? { verified: true, value: item.value ?? "사용 불가 · finalized Paper verification 없음" }
    : { verified: false, value: "사용 불가 · finalized Paper verification 없음" };
}

export const renderPaper: WorkspaceRenderer = (snapshot, drawer) => {
  const fragment = document.createDocumentFragment();
  const guard = document.createElement("section");
  guard.className = "workspace-contract-strip paper-contract";
  guard.append(
    textElement("h2", "Finalized Paper ledger"),
    textElement(
      "p",
      "PnL, positions, orders and entry → protective OCO → reconcile → cutoff → EOD-flat lifecycle are read-only finalized evidence. This workspace has no submit, replace or cancel control.",
    ),
  );
  fragment.append(guard, renderWorkspace(workspaceById("paper"), snapshot, drawer));
  return fragment;
};
