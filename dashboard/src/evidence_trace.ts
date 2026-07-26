import { requiredElement, textElement, timeElement } from "./dom";

type TraceNode = {
  readonly node_id: string;
  readonly kind: string;
  readonly label: string;
  readonly observed_at: string;
  readonly safe_ref: string | null;
  readonly state: string;
  readonly source_namespace: string;
};

type TraceEdge = {
  readonly from_node_id: string;
  readonly to_node_id: string;
  readonly kind: string;
};

type TraceStatus = "resolved" | "unavailable" | "corrupt";

export type ResolvedTrace = {
  readonly status: TraceStatus;
  readonly nodes: readonly TraceNode[];
  readonly edges: readonly TraceEdge[];
  readonly startsAtSource: boolean;
  readonly terminal: TraceNode | null;
};

const terminalKinds = new Set([
  "reviewer_decision",
  "lifecycle_decision",
  "paper_receipt",
  "process_receipt",
  "deployment_receipt",
  "blocker_terminal",
  "source_receipt",
]);

export function resolveEvidenceTrace(
  traceId: string,
  nodes: readonly TraceNode[],
  edges: readonly TraceEdge[],
): ResolvedTrace {
  const nodeMap = new Map(nodes.map((node) => [node.node_id, node]));
  if (!nodeMap.has(traceId)) return unavailableTrace();
  if (edges.some((edge) => !nodeMap.has(edge.from_node_id) || !nodeMap.has(edge.to_node_id))) {
    return corruptTrace();
  }
  const reached = new Set([traceId]);
  const orderedIds = [traceId];
  const reachedEdges: TraceEdge[] = [];
  for (const nodeId of orderedIds) {
    for (const edge of edges.filter((candidate) => candidate.from_node_id === nodeId)) {
      reachedEdges.push(edge);
      if (!reached.has(edge.to_node_id)) {
        reached.add(edge.to_node_id);
        orderedIds.push(edge.to_node_id);
      }
    }
  }
  const orderedNodes = orderedIds.flatMap((nodeId) => {
    const node = nodeMap.get(nodeId);
    return node === undefined ? [] : [node];
  });
  const terminal = orderedNodes.findLast((node) => terminalKinds.has(node.kind)) ?? null;
  return {
    status: terminal === null ? "corrupt" : "resolved",
    nodes: orderedNodes,
    edges: reachedEdges,
    startsAtSource: orderedNodes.some((node) => node.kind === "source_receipt"),
    terminal,
  };
}

export class EvidenceTraceDrawer {
  private readonly dialog = requiredElement("evidence-trace-dialog", HTMLDialogElement);
  private readonly heading = requiredElement("trace-heading", HTMLElement);
  private readonly summary = requiredElement("trace-summary", HTMLElement);
  private readonly list = requiredElement("trace-list", HTMLOListElement);
  private invoker: HTMLElement | null = null;

  constructor() {
    requiredElement("trace-close", HTMLButtonElement).addEventListener("click", () => this.close());
    this.dialog.addEventListener("keydown", (event) => this.handleKeydown(event));
    this.dialog.addEventListener("close", () => this.returnFocus());
  }

  open(label: string, trace: ResolvedTrace, invoker: HTMLElement): void {
    this.invoker = invoker;
    this.heading.textContent = label;
    this.summary.textContent = traceSummary(trace);
    this.list.replaceChildren(...trace.nodes.map(renderTraceNode));
    this.dialog.showModal();
    this.heading.focus();
  }

  close(): void {
    if (this.dialog.open) this.dialog.close();
  }

  private handleKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape") {
      event.preventDefault();
      this.close();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [...this.dialog.querySelectorAll<HTMLElement>("button, [tabindex='0']")];
    const first = focusable[0];
    const last = focusable.at(-1);
    if (first === undefined || last === undefined) return;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  private returnFocus(): void {
    if (this.invoker?.isConnected === true) {
      this.invoker.focus();
    } else {
      requiredElement("workspace-heading", HTMLElement).focus();
    }
    this.invoker = null;
  }
}

function renderTraceNode(node: TraceNode): HTMLLIElement {
  const item = document.createElement("li");
  item.tabIndex = 0;
  const heading = document.createElement("div");
  heading.append(
    textElement("span", humanize(node.kind), "state-badge state-neutral"),
    textElement("strong", node.label),
  );
  const metadata = document.createElement("div");
  metadata.className = "trace-node-meta";
  metadata.append(timeElement(node.observed_at), textElement("code", node.source_namespace));
  if (node.safe_ref !== null) metadata.append(textElement("code", middleTruncate(node.safe_ref)));
  item.append(heading, metadata);
  return item;
}

function traceSummary(trace: ResolvedTrace): string {
  switch (trace.status) {
    case "resolved":
      return `${trace.nodes.length}개 노드 · source에서 ${humanize(trace.terminal?.kind ?? "")}까지`;
    case "unavailable":
      return "연결된 trace가 없습니다. 값을 추정하거나 경로를 만들어내지 않았습니다.";
    case "corrupt":
      return "trace graph가 완결되지 않아 사용을 중단했습니다.";
    default:
      return assertNever(trace.status);
  }
}

function unavailableTrace(): ResolvedTrace {
  return {
    status: "unavailable",
    nodes: [],
    edges: [],
    startsAtSource: false,
    terminal: null,
  };
}

function corruptTrace(): ResolvedTrace {
  return {
    status: "corrupt",
    nodes: [],
    edges: [],
    startsAtSource: false,
    terminal: null,
  };
}

function humanize(value: string): string {
  return value.replaceAll("_", " ");
}

function middleTruncate(value: string): string {
  return value.length <= 24 ? value : `${value.slice(0, 12)}…${value.slice(-12)}`;
}

function assertNever(value: never): never {
  throw new EvidenceTraceError(`unknown trace status: ${String(value)}`);
}

class EvidenceTraceError extends Error {
  override readonly name = "EvidenceTraceError";
}
